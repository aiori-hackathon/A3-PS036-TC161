CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -std=gnu11
PREFIX ?= /usr/local
CPPFLAGS += -I$(PREFIX)/include -I$(PREFIX)/include/mtllib
LDFLAGS += -L$(PREFIX)/lib64 -Wl,-rpath,$(PREFIX)/lib64
LDLIBS = -lmtlslib -loqs -lcrypto -lm

# mtlbench needs the crypto stack; rsrcsamp is libc-only (accurate /proc + cgroup v2).
all: mtlbench rsrcsamp

mtlbench: mtlbench.c
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ $< $(LDFLAGS) $(LDLIBS)

rsrcsamp: rsrcsamp.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f mtlbench rsrcsamp

.PHONY: all clean
