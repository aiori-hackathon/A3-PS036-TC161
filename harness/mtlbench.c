/*
 * mtlbench - one benchmark trial over a fixed message set.
 *
 * Calls only the supplied Verisign MTL reference library, liboqs and OpenSSL.
 * No cryptographic primitive is implemented here.
 *
 * Modes:
 *   mtl        SLH-DSA-SHA2-128s in MTL mode via the reference library
 *   direct     the same SLH-DSA parameter set signing every message (liboqs)
 *   classical  ed25519 | ecdsa-p256 | rsa-2048 baseline (OpenSSL EVP)
 *
 * Message file: u32be count, then count x (u32be length, bytes).
 * Result: one JSON object on stdout. Signature artifacts go to --out-dir.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <getopt.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <openssl/bn.h>
#include <openssl/core_names.h>
#include <openssl/ecdsa.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <oqs/oqs.h>

#include "mtllib.h"

typedef struct
{
    size_t n;
    uint8_t **data;
    size_t *len;
} msgset;

static void die(const char *what)
{
    fprintf(stderr, "mtlbench: %s\n", what);
    exit(2);
}

static uint64_t now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static long rss_bytes(void)
{
    long pages_total = 0, pages_rss = 0;
    FILE *f = fopen("/proc/self/statm", "r");
    if (f == NULL)
        return -1;
    if (fscanf(f, "%ld %ld", &pages_total, &pages_rss) != 2)
        pages_rss = -1;
    fclose(f);
    return pages_rss < 0 ? -1 : pages_rss * sysconf(_SC_PAGESIZE);
}

static long maxrss_bytes(void)
{
    /* Not getrusage(): on Linux ru_maxrss survives execve and would report the Python parent's peak. */
    char line[256];
    long kb = -1;
    FILE *f = fopen("/proc/self/status", "r");
    if (f == NULL)
        return -1;
    while (fgets(line, sizeof line, f) != NULL)
        if (sscanf(line, "VmHWM: %ld kB", &kb) == 1)
            break;
    fclose(f);
    return kb < 0 ? -1 : kb * 1024L;
}

static uint32_t rd_u32(FILE *f)
{
    uint8_t b[4];
    if (fread(b, 1, 4, f) != 4)
        die("truncated message file");
    return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | b[3];
}

static void wr_u32(FILE *f, uint32_t v)
{
    uint8_t b[4] = {(uint8_t)(v >> 24), (uint8_t)(v >> 16), (uint8_t)(v >> 8), (uint8_t)v};
    fwrite(b, 1, 4, f);
}

static void load_messages(const char *path, size_t limit, msgset *ms)
{
    FILE *f = fopen(path, "rb");
    if (f == NULL)
        die("cannot open message file");
    size_t n = rd_u32(f);
    if (limit > 0 && limit < n)
        n = limit;
    ms->n = n;
    ms->data = calloc(n, sizeof(uint8_t *));
    ms->len = calloc(n, sizeof(size_t));
    for (size_t i = 0; i < n; i++)
    {
        ms->len[i] = rd_u32(f);
        ms->data[i] = malloc(ms->len[i] ? ms->len[i] : 1);
        if (fread(ms->data[i], 1, ms->len[i], f) != ms->len[i])
            die("truncated message body");
    }
    fclose(f);
}

static void write_set(const char *dir, const char *name, uint8_t **data, size_t *len, size_t n)
{
    char path[1024];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    FILE *f = fopen(path, "wb");
    if (f == NULL)
        die("cannot write artifact");
    wr_u32(f, (uint32_t)n);
    for (size_t i = 0; i < n; i++)
    {
        wr_u32(f, (uint32_t)len[i]);
        fwrite(data[i], 1, len[i], f);
    }
    fclose(f);
}

static void write_raw(const char *dir, const char *name, const uint8_t *data, size_t len)
{
    char path[1024];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    FILE *f = fopen(path, "wb");
    if (f == NULL)
        die("cannot write artifact");
    fwrite(data, 1, len, f);
    fclose(f);
}

static void json_u64_array(const char *key, const uint64_t *v, size_t n, bool comma)
{
    printf("\"%s\":[", key);
    for (size_t i = 0; i < n; i++)
        printf(i ? ",%llu" : "%llu", (unsigned long long)v[i]);
    printf("]%s", comma ? "," : "");
}

static void json_size_array(const char *key, const size_t *v, size_t n, bool comma)
{
    printf("\"%s\":[", key);
    for (size_t i = 0; i < n; i++)
        printf(i ? ",%zu" : "%zu", v[i]);
    printf("]%s", comma ? "," : "");
}

static size_t total_bytes(const msgset *ms)
{
    size_t t = 0;
    for (size_t i = 0; i < ms->n; i++)
        t += ms->len[i];
    return t;
}

/* ------------------------------------------------------------------ */
/* MTL mode (reference library)                                        */
/* ------------------------------------------------------------------ */

static bool mtl_ok(MTLLIB_STATUS s)
{
    return s == MTLLIB_OK || s == MTLLIB_OK_VALIDATED_LADDER;
}

static MTLLIB_STATUS mtl_verify_bytes(MTLLIB_CTX *v, const uint8_t *m, size_t mlen,
                                      const uint8_t *s, size_t slen, MTLLIB_BUFFER *ladder)
{
    MTLLIB_BUFFER *mb = NULL, *sb = NULL;
    mtllib_buffer_initialize(&mb, mlen, (uint8_t *)m);
    mtllib_buffer_initialize(&sb, slen, (uint8_t *)s);
    MTLLIB_STATUS st = mtllib_verify(v, mb, sb, ladder, NULL);
    mtllib_buffer_free(mb);
    mtllib_buffer_free(sb);
    return st;
}

static int run_mtl(const char *algo, msgset *ms, const char *outdir, size_t full_n)
{
    size_t n = ms->n;
    MTLLIB_CTX *ctx = NULL, *vctx = NULL;
    uint64_t t0;

    long rss_start = rss_bytes();
    t0 = now_ns();
    if (mtllib_key_new((char *)algo, &ctx) != MTLLIB_OK)
        die("mtllib_key_new failed");
    uint64_t keygen_ns = now_ns() - t0;
    long rss_after_keygen = rss_bytes();

    uint64_t *append_ns = calloc(n, sizeof(uint64_t));
    uint64_t *cond_ns = calloc(n, sizeof(uint64_t));
    uint64_t *vcond_ns = calloc(n, sizeof(uint64_t));
    MTL_INDEX *leaf = calloc(n, sizeof(MTL_INDEX));
    uint8_t **cond = calloc(n, sizeof(uint8_t *));
    size_t *cond_len = calloc(n, sizeof(size_t));

    for (size_t i = 0; i < n; i++)
    {
        MTLLIB_BUFFER *mb = NULL;
        MTL_HANDLE *h = NULL;
        mtllib_buffer_initialize(&mb, ms->len[i], ms->data[i]);
        t0 = now_ns();
        if (mtllib_sign_append(ctx, mb, &h) != MTLLIB_OK)
            die("mtllib_sign_append failed");
        append_ns[i] = now_ns() - t0;
        leaf[i] = h->leaf_index;
        mtllib_sign_free_handle(&h);
        mtllib_buffer_free(mb);
    }
    long rss_after_append = rss_bytes();

    MTLLIB_BUFFER *ladder = NULL;
    mtllib_buffer_initialize(&ladder, mtllib_sign_get_signed_ladder_length(ctx), NULL);
    t0 = now_ns();
    if (mtllib_sign_get_signed_ladder(ctx, ladder) != MTLLIB_OK)
        die("mtllib_sign_get_signed_ladder failed");
    uint64_t ladder_sign_ns = now_ns() - t0;
    size_t ladder_len = mtllib_buffer_in_use(ladder);

    MTL_HANDLE handle;
    memset(&handle, 0, sizeof(handle));
    for (size_t i = 0; i < n; i++)
    {
        MTLLIB_BUFFER *cb = NULL;
        handle.leaf_index = leaf[i];
        mtllib_buffer_initialize(&cb, mtllib_sign_get_condensed_sig_length(ctx, &handle), NULL);
        t0 = now_ns();
        if (mtllib_sign_get_condensed_sig(ctx, &handle, cb) != MTLLIB_OK)
            die("mtllib_sign_get_condensed_sig failed");
        cond_ns[i] = now_ns() - t0;
        cond_len[i] = mtllib_buffer_in_use(cb);
        cond[i] = malloc(cond_len[i]);
        memcpy(cond[i], mtllib_buffer_data_ptr(cb), cond_len[i]);
        mtllib_buffer_free(cb);
    }

    /* Verifier side: public key + series id only, as a resolver would hold. */
    size_t pk_len = mtllib_pubkey_to_buffer_length(ctx);
    MTLLIB_BUFFER *pk = NULL;
    mtllib_buffer_initialize(&pk, pk_len, NULL);
    if (mtllib_pubkey_to_buffer(ctx, pk) != MTLLIB_OK)
        die("mtllib_pubkey_to_buffer failed");
    t0 = now_ns();
    if (mtllib_pubkey_from_buffer((char *)algo, &vctx, pk, cond[0] + 2) != MTLLIB_OK)
        die("mtllib_pubkey_from_buffer failed");
    uint64_t verifier_setup_ns = now_ns() - t0;

    t0 = now_ns();
    MTLLIB_STATUS lst = mtllib_verify_signed_ladder(vctx, ladder);
    uint64_t ladder_verify_ns = now_ns() - t0;
    size_t fail_ladder = (lst == MTLLIB_OK) ? 0 : 1;

    size_t fail_cond = 0;
    for (size_t i = 0; i < n; i++)
    {
        MTLLIB_BUFFER *mb = NULL, *sb = NULL;
        mtllib_buffer_initialize(&mb, ms->len[i], ms->data[i]);
        mtllib_buffer_initialize(&sb, cond_len[i], cond[i]);
        t0 = now_ns();
        MTLLIB_STATUS st = mtllib_verify(vctx, mb, sb, ladder, NULL);
        vcond_ns[i] = now_ns() - t0;
        if (st != MTLLIB_OK)
            fail_cond++;
        mtllib_buffer_free(mb);
        mtllib_buffer_free(sb);
    }

    /* Full signature = condensed || signed ladder (reference "-r" reconstruction). */
    if (full_n > n)
        full_n = n;
    uint64_t *vfull_ns = calloc(full_n ? full_n : 1, sizeof(uint64_t));
    size_t fail_full = 0;
    for (size_t i = 0; i < full_n; i++)
    {
        size_t flen = cond_len[i] + ladder_len;
        uint8_t *full = malloc(flen);
        memcpy(full, cond[i], cond_len[i]);
        memcpy(full + cond_len[i], mtllib_buffer_data_ptr(ladder), ladder_len);
        t0 = now_ns();
        MTLLIB_STATUS st = mtl_verify_bytes(vctx, ms->data[i], ms->len[i], full, flen, NULL);
        vfull_ns[i] = now_ns() - t0;
        if (!mtl_ok(st))
            fail_full++;
        free(full);
    }

    /* Tamper checks: each must be rejected. */
    uint8_t *tm = malloc(ms->len[0]);
    memcpy(tm, ms->data[0], ms->len[0]);
    tm[ms->len[0] / 2] ^= 0x01;
    bool msg_rejected = !mtl_ok(mtl_verify_bytes(vctx, tm, ms->len[0], cond[0], cond_len[0], ladder));
    free(tm);

    uint8_t *ts = malloc(cond_len[0]);
    memcpy(ts, cond[0], cond_len[0]);
    ts[cond_len[0] - 1] ^= 0x01;
    bool sig_rejected = !mtl_ok(mtl_verify_bytes(vctx, ms->data[0], ms->len[0], ts, cond_len[0], ladder));
    free(ts);

    uint8_t *tl = malloc(ladder_len);
    memcpy(tl, mtllib_buffer_data_ptr(ladder), ladder_len);
    tl[ladder_len - 1] ^= 0x01;
    MTLLIB_BUFFER *tlb = NULL;
    mtllib_buffer_initialize(&tlb, ladder_len, tl);
    bool ladder_rejected = mtllib_verify_signed_ladder(vctx, tlb) != MTLLIB_OK;
    mtllib_buffer_free(tlb);
    free(tl);

    if (outdir != NULL)
    {
        write_set(outdir, "condensed.bin", cond, cond_len, n);
        write_raw(outdir, "signed_ladder.bin", mtllib_buffer_data_ptr(ladder), ladder_len);
        write_raw(outdir, "public_key.bin", mtllib_buffer_data_ptr(pk), pk_len);
    }

    printf("{\"mode\":\"mtl\",\"algorithm\":\"%s\",\"n_messages\":%zu,\"message_bytes\":%zu,", algo, n, total_bytes(ms));
    printf("\"keygen_ns\":%llu,\"ladder_sign_ns\":%llu,\"verifier_setup_ns\":%llu,\"ladder_verify_ns\":%llu,",
           (unsigned long long)keygen_ns, (unsigned long long)ladder_sign_ns,
           (unsigned long long)verifier_setup_ns, (unsigned long long)ladder_verify_ns);
    json_u64_array("append_ns", append_ns, n, true);
    json_u64_array("condensed_ns", cond_ns, n, true);
    json_u64_array("verify_condensed_ns", vcond_ns, n, true);
    json_u64_array("verify_full_ns", vfull_ns, full_n, true);
    printf("\"public_key_bytes\":%zu,\"signed_ladder_bytes\":%zu,", pk_len, ladder_len);
    json_size_array("condensed_bytes", cond_len, n, true);
    printf("\"failures\":{\"ladder\":%zu,\"verify_condensed\":%zu,\"verify_full\":%zu},", fail_ladder, fail_cond, fail_full);
    printf("\"tamper\":{\"message_rejected\":%s,\"signature_rejected\":%s,\"ladder_rejected\":%s},",
           msg_rejected ? "true" : "false", sig_rejected ? "true" : "false", ladder_rejected ? "true" : "false");
    printf("\"memory\":{\"rss_start\":%ld,\"rss_after_keygen\":%ld,\"rss_after_append\":%ld,\"maxrss\":%ld}}\n",
           rss_start, rss_after_keygen, rss_after_append, maxrss_bytes());

    for (size_t i = 0; i < n; i++)
        free(cond[i]);
    free(cond); free(cond_len); free(leaf);
    free(append_ns); free(cond_ns); free(vcond_ns); free(vfull_ns);
    mtllib_buffer_free(ladder);
    mtllib_buffer_free(pk);
    mtllib_key_free(vctx);
    mtllib_key_free(ctx);
    return (fail_ladder || fail_cond || fail_full || !msg_rejected || !sig_rejected || !ladder_rejected) ? 1 : 0;
}

/* ------------------------------------------------------------------ */
/* Direct (non-MTL) SLH-DSA via liboqs                                 */
/* ------------------------------------------------------------------ */

static int run_direct(const char *oqs_alg, msgset *ms, const char *outdir)
{
    size_t n = ms->n;
    uint64_t t0;
    long rss_start = rss_bytes();
    OQS_SIG *sig = OQS_SIG_new(oqs_alg);
    if (sig == NULL)
        die("OQS_SIG_new failed (algorithm not enabled?)");
    uint8_t *pk = malloc(sig->length_public_key);
    uint8_t *sk = malloc(sig->length_secret_key);

    t0 = now_ns();
    if (OQS_SIG_keypair(sig, pk, sk) != OQS_SUCCESS)
        die("OQS_SIG_keypair failed");
    uint64_t keygen_ns = now_ns() - t0;

    uint64_t *sign_ns = calloc(n, sizeof(uint64_t));
    uint64_t *verify_ns = calloc(n, sizeof(uint64_t));
    uint8_t **sigs = calloc(n, sizeof(uint8_t *));
    size_t *sig_len = calloc(n, sizeof(size_t));

    for (size_t i = 0; i < n; i++)
    {
        sigs[i] = malloc(sig->length_signature);
        t0 = now_ns();
        if (OQS_SIG_sign(sig, sigs[i], &sig_len[i], ms->data[i], ms->len[i], sk) != OQS_SUCCESS)
            die("OQS_SIG_sign failed");
        sign_ns[i] = now_ns() - t0;
    }

    size_t fail = 0;
    for (size_t i = 0; i < n; i++)
    {
        t0 = now_ns();
        OQS_STATUS st = OQS_SIG_verify(sig, ms->data[i], ms->len[i], sigs[i], sig_len[i], pk);
        verify_ns[i] = now_ns() - t0;
        if (st != OQS_SUCCESS)
            fail++;
    }

    uint8_t *tm = malloc(ms->len[0]);
    memcpy(tm, ms->data[0], ms->len[0]);
    tm[ms->len[0] / 2] ^= 0x01;
    bool msg_rejected = OQS_SIG_verify(sig, tm, ms->len[0], sigs[0], sig_len[0], pk) != OQS_SUCCESS;
    free(tm);
    uint8_t *ts = malloc(sig_len[0]);
    memcpy(ts, sigs[0], sig_len[0]);
    ts[sig_len[0] - 1] ^= 0x01;
    bool sig_rejected = OQS_SIG_verify(sig, ms->data[0], ms->len[0], ts, sig_len[0], pk) != OQS_SUCCESS;
    free(ts);

    if (outdir != NULL)
    {
        write_set(outdir, "signatures.bin", sigs, sig_len, n);
        write_raw(outdir, "public_key.bin", pk, sig->length_public_key);
    }

    printf("{\"mode\":\"direct\",\"algorithm\":\"%s\",\"n_messages\":%zu,\"message_bytes\":%zu,", oqs_alg, n, total_bytes(ms));
    printf("\"keygen_ns\":%llu,", (unsigned long long)keygen_ns);
    json_u64_array("sign_ns", sign_ns, n, true);
    json_u64_array("verify_ns", verify_ns, n, true);
    printf("\"public_key_bytes\":%zu,", sig->length_public_key);
    json_size_array("signature_bytes", sig_len, n, true);
    printf("\"failures\":{\"verify\":%zu},", fail);
    printf("\"tamper\":{\"message_rejected\":%s,\"signature_rejected\":%s},",
           msg_rejected ? "true" : "false", sig_rejected ? "true" : "false");
    printf("\"memory\":{\"rss_start\":%ld,\"maxrss\":%ld}}\n", rss_start, maxrss_bytes());

    for (size_t i = 0; i < n; i++)
        free(sigs[i]);
    free(sigs); free(sig_len); free(sign_ns); free(verify_ns);
    OQS_MEM_secure_free(sk, sig->length_secret_key);
    free(pk);
    OQS_SIG_free(sig);
    return (fail || !msg_rejected || !sig_rejected) ? 1 : 0;
}

/* ------------------------------------------------------------------ */
/* Classical baseline via OpenSSL EVP                                  */
/* ------------------------------------------------------------------ */

static EVP_PKEY *classical_keygen(const char *alg)
{
    if (strcmp(alg, "ed25519") == 0)
        return EVP_PKEY_Q_keygen(NULL, NULL, "ED25519");
    if (strcmp(alg, "ecdsa-p256") == 0)
        return EVP_PKEY_Q_keygen(NULL, NULL, "EC", "P-256");
    if (strcmp(alg, "rsa-2048") == 0)
        return EVP_PKEY_Q_keygen(NULL, NULL, "RSA", (size_t)2048);
    die("unknown classical algorithm");
    return NULL;
}

static const EVP_MD *classical_md(const char *alg)
{
    return strcmp(alg, "ed25519") == 0 ? NULL : EVP_sha256();
}

/* DNSSEC carries ECDSA as fixed-width r||s (RFC 6605), not DER. */
static size_t ecdsa_der_to_raw(const uint8_t *der, size_t der_len, uint8_t *raw64)
{
    const uint8_t *p = der;
    ECDSA_SIG *es = d2i_ECDSA_SIG(NULL, &p, (long)der_len);
    if (es == NULL)
        die("d2i_ECDSA_SIG failed");
    const BIGNUM *r = NULL, *s = NULL;
    ECDSA_SIG_get0(es, &r, &s);
    BN_bn2binpad(r, raw64, 32);
    BN_bn2binpad(s, raw64 + 32, 32);
    ECDSA_SIG_free(es);
    return 64;
}

/* Length of the DNSKEY public key field: raw key (RFC 8080), x||y (RFC 6605),
 * or exponent-length || exponent || modulus (RFC 3110). */
static size_t dnskey_pubkey_len(EVP_PKEY *key, const char *alg)
{
    if (strcmp(alg, "ed25519") == 0)
    {
        size_t len = 0;
        EVP_PKEY_get_raw_public_key(key, NULL, &len);
        return len;
    }
    if (strcmp(alg, "ecdsa-p256") == 0)
        return (size_t)i2d_PublicKey(key, NULL) - 1;
    BIGNUM *n = NULL, *e = NULL;
    if (EVP_PKEY_get_bn_param(key, OSSL_PKEY_PARAM_RSA_N, &n) != 1 ||
        EVP_PKEY_get_bn_param(key, OSSL_PKEY_PARAM_RSA_E, &e) != 1)
        die("RSA key parameters unavailable");
    size_t e_len = (size_t)BN_num_bytes(e);
    size_t len = (e_len <= 255 ? 1 : 3) + e_len + (size_t)BN_num_bytes(n);
    BN_free(n);
    BN_free(e);
    return len;
}

static int run_classical(const char *alg, msgset *ms, const char *outdir)
{
    size_t n = ms->n;
    uint64_t t0;
    t0 = now_ns();
    EVP_PKEY *key = classical_keygen(alg);
    uint64_t keygen_ns = now_ns() - t0;
    if (key == NULL)
        die("classical keygen failed");
    const EVP_MD *md = classical_md(alg);
    bool is_ecdsa = strcmp(alg, "ecdsa-p256") == 0;

    uint64_t *sign_ns = calloc(n, sizeof(uint64_t));
    uint64_t *verify_ns = calloc(n, sizeof(uint64_t));
    uint8_t **sigs = calloc(n, sizeof(uint8_t *));
    size_t *sig_len = calloc(n, sizeof(size_t));
    uint8_t **wire = calloc(n, sizeof(uint8_t *));
    size_t *wire_len = calloc(n, sizeof(size_t));

    for (size_t i = 0; i < n; i++)
    {
        size_t slen = (size_t)EVP_PKEY_get_size(key);
        sigs[i] = malloc(slen);
        t0 = now_ns();
        EVP_MD_CTX *mctx = EVP_MD_CTX_new();
        if (EVP_DigestSignInit(mctx, NULL, md, NULL, key) != 1 ||
            EVP_DigestSign(mctx, sigs[i], &slen, ms->data[i], ms->len[i]) != 1)
            die("EVP_DigestSign failed");
        EVP_MD_CTX_free(mctx);
        sign_ns[i] = now_ns() - t0;
        sig_len[i] = slen;
        if (is_ecdsa)
        {
            wire[i] = malloc(64);
            wire_len[i] = ecdsa_der_to_raw(sigs[i], slen, wire[i]);
        }
        else
        {
            wire[i] = sigs[i];
            wire_len[i] = slen;
        }
    }

    size_t fail = 0;
    for (size_t i = 0; i < n; i++)
    {
        t0 = now_ns();
        EVP_MD_CTX *mctx = EVP_MD_CTX_new();
        int ok = EVP_DigestVerifyInit(mctx, NULL, md, NULL, key) == 1 &&
                 EVP_DigestVerify(mctx, sigs[i], sig_len[i], ms->data[i], ms->len[i]) == 1;
        EVP_MD_CTX_free(mctx);
        verify_ns[i] = now_ns() - t0;
        if (!ok)
            fail++;
    }

    uint8_t *tm = malloc(ms->len[0]);
    memcpy(tm, ms->data[0], ms->len[0]);
    tm[ms->len[0] / 2] ^= 0x01;
    EVP_MD_CTX *mctx = EVP_MD_CTX_new();
    bool msg_rejected = !(EVP_DigestVerifyInit(mctx, NULL, md, NULL, key) == 1 &&
                          EVP_DigestVerify(mctx, sigs[0], sig_len[0], tm, ms->len[0]) == 1);
    EVP_MD_CTX_free(mctx);
    free(tm);
    ERR_clear_error();

    size_t pk_len = dnskey_pubkey_len(key, alg);

    if (outdir != NULL)
        write_set(outdir, "signatures.bin", wire, wire_len, n);

    printf("{\"mode\":\"classical\",\"algorithm\":\"%s\",\"n_messages\":%zu,\"message_bytes\":%zu,", alg, n, total_bytes(ms));
    printf("\"keygen_ns\":%llu,", (unsigned long long)keygen_ns);
    json_u64_array("sign_ns", sign_ns, n, true);
    json_u64_array("verify_ns", verify_ns, n, true);
    printf("\"public_key_bytes\":%zu,", pk_len);
    json_size_array("signature_bytes", wire_len, n, true);
    printf("\"failures\":{\"verify\":%zu},", fail);
    printf("\"tamper\":{\"message_rejected\":%s},", msg_rejected ? "true" : "false");
    printf("\"memory\":{\"maxrss\":%ld}}\n", maxrss_bytes());

    for (size_t i = 0; i < n; i++)
    {
        if (wire[i] != sigs[i])
            free(wire[i]);
        free(sigs[i]);
    }
    free(sigs); free(sig_len); free(wire); free(wire_len); free(sign_ns); free(verify_ns);
    EVP_PKEY_free(key);
    return (fail || !msg_rejected) ? 1 : 0;
}

static void usage(void)
{
    fprintf(stderr,
            "usage: mtlbench MODE --messages FILE [--algo NAME] [--out-dir DIR] [--limit N] [--full-verify N]\n"
            "  MODE mtl        --algo SLH-DSA-SHA2-128s-MTL-SHA2-128\n"
            "  MODE direct     --algo SLH-DSA-PURE-SHA2-128s (liboqs name)\n"
            "  MODE classical  --algo ed25519|ecdsa-p256|rsa-2048\n");
    exit(2);
}

int main(int argc, char **argv)
{
    if (argc < 2)
        usage();
    const char *mode = argv[1];
    const char *messages = NULL, *algo = NULL, *outdir = NULL;
    size_t limit = 0, full_n = 64;

    static struct option opts[] = {
        {"messages", required_argument, 0, 'm'},
        {"algo", required_argument, 0, 'a'},
        {"out-dir", required_argument, 0, 'o'},
        {"limit", required_argument, 0, 'l'},
        {"full-verify", required_argument, 0, 'f'},
        {0, 0, 0, 0}};
    optind = 2;
    int c;
    while ((c = getopt_long(argc, argv, "m:a:o:l:f:", opts, NULL)) != -1)
    {
        switch (c)
        {
        case 'm': messages = optarg; break;
        case 'a': algo = optarg; break;
        case 'o': outdir = optarg; break;
        case 'l': limit = strtoull(optarg, NULL, 10); break;
        case 'f': full_n = strtoull(optarg, NULL, 10); break;
        default: usage();
        }
    }
    if (messages == NULL || algo == NULL)
        usage();

    msgset ms;
    load_messages(messages, limit, &ms);
    if (ms.n == 0)
        die("empty message set");

    if (strcmp(mode, "mtl") == 0)
        return run_mtl(algo, &ms, outdir, full_n);
    if (strcmp(mode, "direct") == 0)
        return run_direct(algo, &ms, outdir);
    if (strcmp(mode, "classical") == 0)
        return run_classical(algo, &ms, outdir);
    usage();
    return 2;
}
