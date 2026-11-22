/*
 * rsrcsamp — wall-clock resource sampler for CipherOps benchmarks.
 *
 * Design (kernel accounting, not userspace guesses):
 *   CPU  — deltas of task jiffies from /proc/<pid>/stat (utime+stime), or
 *          cgroup v2 cpu.stat usage_usec when --cgroup is set / auto-detected.
 *          Percent = 100 * cpu_time_delta / wall_time_delta (CLOCK_MONOTONIC_RAW),
 *          so 100 is one core fully busy. cpu_pct_per_cpu divides by the CPUs the
 *          target may run on (its cpuset), not by every CPU of the host.
 *   RAM  — VmRSS / VmHWM from /proc/<pid>/status, and for a cgroup memory.current,
 *          memory.peak and the anon / file split from memory.stat. memory.current
 *          counts file cache too; anon and "current minus inactive_file" do not.
 *   Host — every row also records the machine (or WSL2 VM) from /proc/stat and
 *          /proc/meminfo: overall CPU busy %, MemTotal and MemAvailable. That is the
 *          capacity figure for a continuously hosted deployment; it is not file cache.
 *
 * Every row also carries the cumulative CPU time (cpu_total_usec), so a caller that
 * polls with --once can compute exact CPU use between two polls.
 *
 * libc only (open/read/select/clock_gettime). No psutil, no /usr/bin/top.
 *
 * Usage:
 *   rsrcsamp --once [--pid N] [--cgroup auto|/sys/fs/cgroup/...] [--label L]
 *   rsrcsamp --out FILE --interval-ms 1000 [--pid N] [--cgroup auto] [--label L]
 *            (runs until SIGTERM/SIGINT; stdin lines: LABEL name | QUIT)
 *
 * A LABEL line closes the running interval at once: the time up to it is written under
 * the old label, so each row belongs to exactly one phase and short phases are not lost.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <limits.h>
#include <sched.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

/* A slice shorter than this is not written on its own (too short for a meaningful rate);
 * it is counted in the next row instead. */
#define MIN_WINDOW_NS (50ull * 1000 * 1000)
#define ONCE_WINDOW_NS (100ull * 1000 * 1000)

static volatile sig_atomic_t g_stop = 0;
static char g_label[128] = "idle";

static void on_signal(int sig)
{
    (void)sig;
    g_stop = 1;
}

static uint64_t mono_ns(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0)
        clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void sleep_ns(uint64_t ns)
{
    struct timespec ts = {.tv_sec = (time_t)(ns / 1000000000ull), .tv_nsec = (long)(ns % 1000000000ull)};
    nanosleep(&ts, NULL); /* a signal ends the sleep early; the caller checks g_stop */
}

/* Whole file into buf (NUL-terminated). Kernel files may hand out less than asked per read. */
static int read_file(const char *path, char *buf, size_t cap)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return -1;
    size_t len = 0;
    while (len < cap - 1) {
        ssize_t n = read(fd, buf + len, cap - 1 - len);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            close(fd);
            return -1;
        }
        if (n == 0)
            break;
        len += (size_t)n;
    }
    close(fd);
    buf[len] = '\0';
    return (int)len;
}

/* Value of "key <number>" at the start of a line, or -1. */
static int64_t keyed_value(const char *buf, const char *key)
{
    size_t klen = strlen(key);
    for (const char *line = buf; line && *line; ) {
        if (strncmp(line, key, klen) == 0 && line[klen] == ' ')
            return (int64_t)strtoll(line + klen + 1, NULL, 10);
        const char *nl = strchr(line, '\n');
        line = nl ? nl + 1 : NULL;
    }
    return -1;
}

static long clk_tck(void)
{
    long t = sysconf(_SC_CLK_TCK);
    return t > 0 ? t : 100;
}

static int online_cpus(void)
{
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    return n > 0 ? (int)n : 1;
}

/* CPUs in a kernel list such as "0-3,7"; 0 if it cannot be read. */
static int count_cpu_list(const char *s)
{
    int n = 0;
    while (*s) {
        char *end;
        long a = strtol(s, &end, 10);
        if (end == s)
            break;
        long b = a;
        if (*end == '-') {
            s = end + 1;
            b = strtol(s, &end, 10);
            if (end == s)
                break;
        }
        if (b >= a)
            n += (int)(b - a + 1);
        s = (*end == ',') ? end + 1 : end;
        if (*end != ',')
            break;
    }
    return n;
}

/* CPUs the measured target may use: the cgroup's cpuset, else the task's affinity, else all online. */
static int allowed_cpus(const char *cgroup, pid_t pid)
{
    if (cgroup && cgroup[0]) {
        char path[PATH_MAX], buf[1024];
        snprintf(path, sizeof path, "%s/cpuset.cpus.effective", cgroup);
        if (read_file(path, buf, sizeof buf) > 0) {
            int n = count_cpu_list(buf);
            if (n > 0)
                return n;
        }
        pid = 0; /* the sampler runs inside the same container */
    }
    cpu_set_t set;
    CPU_ZERO(&set);
    if (sched_getaffinity(pid, sizeof set, &set) == 0) {
        int n = CPU_COUNT(&set);
        if (n > 0)
            return n;
    }
    return online_cpus();
}

/* ---- process accounting (/proc/<pid>/stat + status) ---- */

typedef struct {
    bool ok;
    uint64_t utime;
    uint64_t stime;
    long rss_bytes;
    long hwm_bytes;
    long vsize_bytes;
    int threads;
} proc_sample;

static bool parse_proc_stat(pid_t pid, uint64_t *utime, uint64_t *stime)
{
    char path[64], buf[4096];
    snprintf(path, sizeof path, "/proc/%d/stat", (int)pid);
    if (read_file(path, buf, sizeof buf) < 0)
        return false;
    /* comm may contain spaces/parens — find the last ')' then scan fields after it. */
    char *r = strrchr(buf, ')');
    if (r == NULL || r[1] != ' ')
        return false;
    unsigned long ut = 0, st = 0;
    /* After ')': state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt utime stime */
    int n = sscanf(r + 2, "%*c %*d %*d %*d %*d %*d %*u %*u %*u %*u %*u %lu %lu", &ut, &st);
    if (n < 2)
        return false;
    *utime = ut;
    *stime = st;
    return true;
}

/* Number after "Key:" in /proc/<pid>/status text, or -1. */
static long status_value(const char *buf, const char *key)
{
    size_t klen = strlen(key);
    for (const char *line = buf; line && *line; ) {
        if (strncmp(line, key, klen) == 0 && line[klen] == ':') {
            long v = -1;
            return sscanf(line + klen + 1, " %ld", &v) == 1 ? v : -1;
        }
        const char *nl = strchr(line, '\n');
        line = nl ? nl + 1 : NULL;
    }
    return -1;
}

static proc_sample read_proc(pid_t pid)
{
    proc_sample s = {0};
    if (!parse_proc_stat(pid, &s.utime, &s.stime))
        return s;
    s.ok = true;
    s.rss_bytes = s.hwm_bytes = s.vsize_bytes = -1;
    s.threads = -1;
    char path[64], buf[8192];
    snprintf(path, sizeof path, "/proc/%d/status", (int)pid);
    if (read_file(path, buf, sizeof buf) < 0)
        return s;
    long rss_kb = status_value(buf, "VmRSS");
    long hwm_kb = status_value(buf, "VmHWM");
    long sz_kb = status_value(buf, "VmSize");
    s.rss_bytes = rss_kb >= 0 ? rss_kb * 1024L : -1;
    s.hwm_bytes = hwm_kb >= 0 ? hwm_kb * 1024L : -1;
    s.vsize_bytes = sz_kb >= 0 ? sz_kb * 1024L : -1;
    s.threads = (int)status_value(buf, "Threads");
    return s;
}

/* ---- cgroup v2 ---- */

typedef struct {
    bool ok;
    uint64_t usage_usec;
    int64_t memory_current;
    int64_t memory_peak;
    int64_t memory_anon;
    int64_t memory_file;
    int64_t memory_inactive_file;
} cg_sample;

static bool detect_cgroup_v2(char *out, size_t cap)
{
    char buf[4096];
    if (read_file("/proc/self/cgroup", buf, sizeof buf) < 0)
        return false;
    /* v2 unified: "0::/path" */
    for (char *line = buf; line && *line; ) {
        char *nl = strchr(line, '\n');
        if (nl)
            *nl = '\0';
        if (strncmp(line, "0::", 3) == 0) {
            const char *rel = line + 3;
            if (snprintf(out, cap, "/sys/fs/cgroup%s", rel[0] ? rel : "/") >= (int)cap)
                return false;
            return true;
        }
        line = nl ? nl + 1 : NULL;
    }
    return false;
}

static int64_t read_i64_file(const char *path)
{
    char buf[64];
    if (read_file(path, buf, sizeof buf) < 0)
        return -1;
    char *end = NULL;
    long long v = strtoll(buf, &end, 10);
    if (end == buf)
        return -1;
    return (int64_t)v;
}

static cg_sample read_cgroup(const char *cg)
{
    cg_sample s = {0};
    char path[PATH_MAX], buf[16384];
    snprintf(path, sizeof path, "%s/cpu.stat", cg);
    if (read_file(path, buf, sizeof buf) < 0)
        return s;
    int64_t usage = keyed_value(buf, "usage_usec");
    if (usage < 0)
        return s;
    s.usage_usec = (uint64_t)usage;
    snprintf(path, sizeof path, "%s/memory.current", cg);
    s.memory_current = read_i64_file(path);
    snprintf(path, sizeof path, "%s/memory.peak", cg);
    s.memory_peak = read_i64_file(path);
    s.memory_anon = s.memory_file = s.memory_inactive_file = -1;
    snprintf(path, sizeof path, "%s/memory.stat", cg);
    if (read_file(path, buf, sizeof buf) > 0) {
        s.memory_anon = keyed_value(buf, "anon");
        s.memory_file = keyed_value(buf, "file");
        s.memory_inactive_file = keyed_value(buf, "inactive_file");
    }
    s.ok = true;
    return s;
}

/* ---- host (/proc/stat + /proc/meminfo): the machine the containers share ---- */

typedef struct {
    bool ok;
    uint64_t busy;   /* user+nice+system+irq+softirq+steal */
    uint64_t total;  /* busy + idle + iowait */
} host_cpu;

typedef struct {
    bool ok;
    int64_t mem_total_bytes;
    int64_t mem_available_bytes;
} host_mem;

static host_cpu read_host_cpu(void)
{
    host_cpu s = {0};
    char buf[4096];
    if (read_file("/proc/stat", buf, sizeof buf) < 0)
        return s;
    /* First line: cpu  user nice system idle iowait irq softirq steal ... */
    unsigned long long user = 0, nice = 0, system = 0, idle = 0, iowait = 0, irq = 0, softirq = 0, steal = 0;
    if (sscanf(buf, "cpu %llu %llu %llu %llu %llu %llu %llu %llu",
               &user, &nice, &system, &idle, &iowait, &irq, &softirq, &steal) < 4)
        return s;
    s.busy = user + nice + system + irq + softirq + steal;
    s.total = s.busy + idle + iowait;
    s.ok = s.total > 0;
    return s;
}

static host_mem read_host_mem(void)
{
    host_mem s = {0};
    char buf[8192];
    if (read_file("/proc/meminfo", buf, sizeof buf) < 0)
        return s;
    long total_kb = status_value(buf, "MemTotal");
    long avail_kb = status_value(buf, "MemAvailable");
    if (total_kb < 0)
        return s;
    s.mem_total_bytes = total_kb * 1024L;
    s.mem_available_bytes = avail_kb >= 0 ? avail_kb * 1024L : -1;
    s.ok = true;
    return s;
}

/* ---- sampling ---- */

typedef struct {
    pid_t pid;
    const char *cgroup; /* "" when not sampling a cgroup */
    int cpus;
    uint64_t t0;        /* start of the running interval */
    proc_sample p0;
    cg_sample c0;
    host_cpu h0;
} sampler;

static void json_escape_label(const char *in, char *out, size_t cap)
{
    size_t j = 0;
    for (size_t i = 0; in[i] && j + 2 < cap; i++) {
        char c = in[i];
        if (c == '"' || c == '\\') {
            out[j++] = '\\';
            out[j++] = c;
        } else if ((unsigned char)c < 0x20) {
            out[j++] = '_';
        } else {
            out[j++] = c;
        }
    }
    out[j] = '\0';
}

static void prime(sampler *s)
{
    s->t0 = mono_ns();
    s->p0 = read_proc(s->pid);
    if (s->cgroup[0])
        s->c0 = read_cgroup(s->cgroup);
    s->h0 = read_host_cpu();
}

/* Write one row for the interval since the last row, under the current label, and start a new interval. */
static void take_sample(sampler *s, FILE *out)
{
    uint64_t t1 = mono_ns();
    double wall_s = (t1 - s->t0) / 1e9;
    if (wall_s <= 0)
        wall_s = 1e-9;

    double cpu_pct = 0;
    long long cpu_total_usec = -1;
    long long mem_cur = -1, mem_peak = -1, mem_anon = -1, mem_file = -1, mem_inactive = -1;
    const char *source = "proc";

    if (s->cgroup[0] && s->c0.ok) {
        cg_sample c1 = read_cgroup(s->cgroup);
        if (c1.ok) {
            uint64_t used = c1.usage_usec >= s->c0.usage_usec ? c1.usage_usec - s->c0.usage_usec : 0;
            cpu_pct = 100.0 * (used / 1e6) / wall_s;
            cpu_total_usec = (long long)c1.usage_usec;
            mem_cur = c1.memory_current;
            mem_peak = c1.memory_peak;
            mem_anon = c1.memory_anon;
            mem_file = c1.memory_file;
            mem_inactive = c1.memory_inactive_file;
            source = "cgroup_v2";
            s->c0 = c1;
        }
    }

    long rss = -1, hwm = -1, vsize = -1;
    int threads = -1;
    proc_sample p1 = read_proc(s->pid);
    if (p1.ok) {
        if (strcmp(source, "cgroup_v2") != 0) {
            uint64_t ticks1 = p1.utime + p1.stime;
            if (s->p0.ok) {
                uint64_t ticks0 = s->p0.utime + s->p0.stime;
                uint64_t used = ticks1 >= ticks0 ? ticks1 - ticks0 : 0;
                cpu_pct = 100.0 * ((double)used / (double)clk_tck()) / wall_s;
            }
            cpu_total_usec = (long long)((double)ticks1 * 1e6 / (double)clk_tck());
        }
        rss = p1.rss_bytes;
        hwm = p1.hwm_bytes;
        vsize = p1.vsize_bytes;
        threads = p1.threads;
        s->p0 = p1;
    }

    double host_cpu_pct = -1;
    host_cpu h1 = read_host_cpu();
    if (h1.ok && s->h0.ok && h1.total > s->h0.total) {
        uint64_t d_total = h1.total - s->h0.total;
        uint64_t d_busy = h1.busy >= s->h0.busy ? h1.busy - s->h0.busy : 0;
        host_cpu_pct = 100.0 * (double)d_busy / (double)d_total;
        s->h0 = h1;
    } else if (h1.ok) {
        s->h0 = h1;
    }
    host_mem hm = read_host_mem();

    char lab[2 * sizeof g_label + 2];
    json_escape_label(g_label, lab, sizeof lab);
    fprintf(out,
            "{\"ts_mono_ns\":%llu,\"wall_ms\":%.3f,\"label\":\"%s\",\"pid\":%d,"
            "\"cpu_pct\":%.4f,\"cpu_pct_per_cpu\":%.4f,\"cpu_total_usec\":%lld,"
            "\"rss_bytes\":%ld,\"hwm_bytes\":%ld,\"vsize_bytes\":%ld,\"threads\":%d,"
            "\"cgroup_memory_current\":%lld,\"cgroup_memory_peak\":%lld,\"cgroup_memory_anon\":%lld,"
            "\"cgroup_memory_file\":%lld,\"cgroup_memory_inactive_file\":%lld,"
            "\"host_cpu_pct\":%.4f,\"host_mem_total_bytes\":%lld,\"host_mem_available_bytes\":%lld,"
            "\"source\":\"%s\",\"cpus_allowed\":%d,\"cpus_online\":%d",
            (unsigned long long)t1, wall_s * 1000.0, lab, (int)s->pid,
            cpu_pct, cpu_pct / s->cpus, cpu_total_usec,
            rss, hwm, vsize, threads,
            mem_cur, mem_peak, mem_anon, mem_file, mem_inactive,
            host_cpu_pct,
            hm.ok ? (long long)hm.mem_total_bytes : -1LL,
            hm.ok ? (long long)hm.mem_available_bytes : -1LL,
            source, s->cpus, online_cpus());
    if (s->cgroup[0])
        fprintf(out, ",\"cgroup\":\"%s\"", s->cgroup);
    fputs("}\n", out);
    fflush(out);
    s->t0 = t1;
}

/* ---- commands on stdin ---- */

/* Read what is waiting on stdin and act on every complete line. Returns false once stdin is closed.
 * Raw read(), not stdio: a buffered reader would keep a second line where select() cannot see it. */
static bool handle_stdin(sampler *s, FILE *out)
{
    static char buf[512];
    static size_t len = 0;
    ssize_t n = read(STDIN_FILENO, buf + len, sizeof buf - len);
    if (n < 0)
        return errno == EINTR || errno == EAGAIN;
    if (n == 0)
        return false;
    len += (size_t)n;

    size_t start = 0;
    for (;;) {
        char *nl = memchr(buf + start, '\n', len - start);
        if (nl == NULL)
            break;
        char *line = buf + start;
        *nl = '\0';
        if (nl > line && nl[-1] == '\r')
            nl[-1] = '\0';
        start = (size_t)(nl - buf) + 1;
        if (strncmp(line, "QUIT", 4) == 0) {
            g_stop = 1;
            break;
        }
        if (strncmp(line, "LABEL ", 6) == 0 && line[6] != '\0') {
            if (mono_ns() - s->t0 >= MIN_WINDOW_NS)
                take_sample(s, out); /* close the interval under the label that was in effect */
            snprintf(g_label, sizeof g_label, "%s", line + 6);
        }
    }
    len -= start;
    memmove(buf, buf + start, len);
    if (len == sizeof buf) /* one over-long line with no newline: drop it */
        len = 0;
    return true;
}

static void usage(void)
{
    fprintf(stderr,
            "usage: rsrcsamp [--once] [--out FILE] [--interval-ms N] [--pid PID]\n"
            "                [--cgroup auto|PATH] [--label NAME]\n");
}

int main(int argc, char **argv)
{
    const char *out_path = NULL;
    int interval_ms = 1000;
    bool once = false;
    pid_t pid = getpid();
    bool pid_set = false;
    char cgroup[PATH_MAX] = "";
    bool use_cgroup = false;
    bool cgroup_auto = false;

    static struct option opts[] = {
        {"once", no_argument, 0, '1'},
        {"out", required_argument, 0, 'o'},
        {"interval-ms", required_argument, 0, 'i'},
        {"pid", required_argument, 0, 'p'},
        {"cgroup", required_argument, 0, 'c'},
        {"label", required_argument, 0, 'l'},
        {"help", no_argument, 0, 'h'},
        {0, 0, 0, 0},
    };
    int c;
    while ((c = getopt_long(argc, argv, "1o:i:p:c:l:h", opts, NULL)) != -1) {
        switch (c) {
        case '1':
            once = true;
            break;
        case 'o':
            out_path = optarg;
            break;
        case 'i':
            interval_ms = atoi(optarg);
            if (interval_ms < 50)
                interval_ms = 50;
            break;
        case 'p':
            pid = (pid_t)atoi(optarg);
            pid_set = true;
            break;
        case 'c':
            use_cgroup = true;
            if (strcmp(optarg, "auto") == 0)
                cgroup_auto = true;
            else {
                snprintf(cgroup, sizeof cgroup, "%s", optarg);
                cgroup_auto = false;
            }
            break;
        case 'l':
            snprintf(g_label, sizeof g_label, "%s", optarg);
            break;
        case 'h':
        default:
            usage();
            return c == 'h' ? 0 : 2;
        }
    }

    if (!pid_set && !use_cgroup) {
        /* Default: sample this process's parent (the orchestrator) when run as helper,
         * else self. Parent is more useful when Python spawns us. */
        pid_t pp = getppid();
        pid = (pp > 1) ? pp : getpid();
    }

    if (cgroup_auto && !detect_cgroup_v2(cgroup, sizeof cgroup)) {
        fprintf(stderr, "rsrcsamp: cgroup v2 path not found\n");
        cgroup[0] = '\0';
    }
    if (!use_cgroup)
        cgroup[0] = '\0';

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    FILE *out = stdout;
    if (out_path) {
        out = fopen(out_path, "a");
        if (!out) {
            perror(out_path);
            return 1;
        }
        setvbuf(out, NULL, _IOLBF, 0);
    }

    sampler s = {.pid = pid, .cgroup = cgroup, .cpus = allowed_cpus(cgroup, pid)};
    prime(&s);

    if (once) {
        sleep_ns(ONCE_WINDOW_NS); /* a single row still needs a window to measure a rate over */
        take_sample(&s, out);
    } else {
        const uint64_t interval_ns = (uint64_t)interval_ms * 1000000ull;
        bool stdin_open = true;
        while (!g_stop) {
            uint64_t now = mono_ns(), deadline = s.t0 + interval_ns;
            if (now >= deadline) {
                take_sample(&s, out);
                continue;
            }
            uint64_t left = deadline - now;
            if (!stdin_open) {
                sleep_ns(left);
                continue;
            }
            fd_set fds;
            FD_ZERO(&fds);
            FD_SET(STDIN_FILENO, &fds);
            struct timeval tv = {.tv_sec = (time_t)(left / 1000000000ull),
                                 .tv_usec = (suseconds_t)((left % 1000000000ull) / 1000)};
            int r = select(STDIN_FILENO + 1, &fds, NULL, NULL, &tv);
            if (r > 0)
                stdin_open = handle_stdin(&s, out); /* closed stdin: keep sampling until a signal */
            else if (r < 0 && errno != EINTR)
                stdin_open = false;
        }
        if (mono_ns() - s.t0 >= MIN_WINDOW_NS)
            take_sample(&s, out); /* the last partial interval */
    }

    if (out != stdout)
        fclose(out);
    return 0;
}
