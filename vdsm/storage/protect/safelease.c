/* Locker */
#define _GNU_SOURCE 1
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <sys/types.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>
#include <signal.h>

#define WARN(fmt, args...)      warn(__FUNCTION__, fmt, ## args)
#define PANIC(fmt, args...)     panic(__FUNCTION__, fmt, ## args)
#define DEBUG(fmt, args...)    do { if (debug) warn(__FUNCTION__, fmt, ## args); } while (0)

char *freetag = "------FREE------0000000000000000";
enum {
    idlen = 16,
    stamplen = 16,
    taglen = idlen + stamplen,
};

char *progname;
int debug;

char *id;
char *path;
char *request;

long lease_ms;
long op_max_ms;
char *iobuf;

inline unsigned long long tv2msec(struct timeval *tv);
int renew(int fd, off_t offset, char *id, long long *ts);

void
panic(const char const *fn, char *msg, ...)
{
    char buf[512];
    va_list va;
    int n;

    va_start(va, msg);
    n = vsprintf(buf, msg, va);
    va_end(va);
    buf[n] = 0;

    fprintf(stderr, "panic: [%d] %s: %s: (%m)\n", getpid(), fn, buf);

    exit(-1);
}

void
warn(const char const *fn, char *msg, ...)
{
    struct timeval tv;
    long long unsigned tscurr;
    char buf[512];
    va_list va;
    int n;

    va_start(va, msg);
    n = vsprintf(buf, msg, va);
    va_end(va);
    buf[n] = 0;

    gettimeofday(&tv, 0);
    tscurr = tv2msec(&tv);

    fprintf(stderr, "[%s:%d:%llu]: %s: %s\n", progname, getpid(), tscurr, fn, buf);
}

void
usage(void)
{
    fprintf(stderr, "Usage: %s [ -h ] <op>  [...]\n", progname);
    fprintf(stderr, "Ops:\n"
         "acquire [ -r <path> ] [ -b ] [ -o offset ] <path> <id> <lease_ms> <op_max_ms>\n"
         "renew   [ -r <path> ] [ -o offset ] [ -t laststamp ] <path> <id> <lease_ms> <op_max_ms>\n"
         "release [ -f ] [ -o offset ] <path> <id>\n"
         "query   [ -o offset ] <path>\n"
         "protect [ -r <path> -i <id>] [ -o offset ] <path> <lease_ms> <op_max_ms> <progname> [<param1> ...]\n"
        );
    fprintf(stderr, "\nNotes:\n"
        "-b - busy loop on lease until lease acquired\n"
        "-f - force release even if lease id is not equal to id\n"
        "-o - offset to lease in path (default is 0)\n"
        "-t - timestamp of last successful renewal\n"
        "Path is a path to a device or a file to use as a sync object.\n"
        "Id is an arbitrary unique string\n"
        "lease_ms is the maximum time in msec that the owner of the lease\n"
        "    may hold it without renewing it\n"
        "op_max_ms is the maximum time in msec that a single IO operation may take (must be <= lease_ms).\n"
        "if -r option is used, the path is a readable file/device.\n"
        " The program then validates that its 'id' is written at the given offset.\n"
        " If this is not the case, acquire and renew  will fail immediately.\n"
    );
    exit(1);
}

inline unsigned long long
tv2msec(struct timeval *tv)
{
    return tv->tv_sec * 1000ull + tv->tv_usec/1000;
}

int
withintimelimits(struct timeval *start, struct timeval *stop)
{
    unsigned long long delta;
    if (op_max_ms <= 0)
        return 1;
    delta = tv2msec(stop) - tv2msec(start);
    if (delta > op_max_ms) {
        DEBUG("Error - time limit breached: op_max_ms - %ld, time passed - %lld", op_max_ms, delta);
        errno = -ETIMEDOUT;
        return 0;
    }
    return 1;
}

int
sametag(const char *tag1, const char *tag2)
{
    return !memcmp(tag1, tag2, taglen);
}

int
isfree(const char *tag)
{
    return sametag(tag, freetag);
}

void
settag(char *tag, const char *src)
{
    memcpy(tag, src, taglen);
}

void
buildtag(char *tag, const char *id, long long ts)
{
    snprintf(tag, taglen+1, "%-*s%0*llx", idlen, id, stamplen, ts);
    DEBUG("'%s' ts %lld", tag, ts);
}

int
sameid(const char *tag, const char *id)
{
    char _id[idlen+1];

    snprintf(_id, idlen+1, "%-*s", idlen, id);
    return !memcmp(tag, _id, idlen);
}

void
querytag(const char *tag, char *id, long long *ts)
{
    char _stamp[stamplen+1] = "";

    memcpy(id, tag, idlen);
    id[idlen] = 0;
    memcpy(_stamp, tag+idlen, stamplen);
    *ts = strtoull(_stamp, 0, 16);
}

int
readtag(int fd, off_t offset, char *tag, int limit)
{
    struct timeval start, stop;
    int r;

    DEBUG("fd %d offset %ld", fd, offset);
    gettimeofday(&start, 0);
    r = pread(fd, iobuf, 512, offset);
    gettimeofday(&stop, 0);
    DEBUG("r %d %m", r);
    if (r <= 0 || (limit && !withintimelimits(&start, &stop)))
        return -1;
    memcpy(tag, iobuf, taglen);
    return r;
}

int
writetag(int fd, off_t offset, const char *tag, int limit)
{
    struct timeval start, stop;
    int r;

    DEBUG("Enter");
    memcpy(iobuf, tag, taglen);
    gettimeofday(&start, 0);
    r = pwrite(fd, iobuf, 512, offset) < taglen ? -1 : 0;
    gettimeofday(&stop, 0);
    DEBUG("Exit r=%ld", r);
    if (r < 0 || (limit && !withintimelimits(&start, &stop)))
        return -1;
    return r;
}

int
writetimestamp(int fd, off_t offset, const char *id, char *tag, long long *ts)
{
    struct timeval tv;
    long long t;
    int r;

    gettimeofday(&tv, 0);
    t = tv.tv_sec * 1000000ll + tv.tv_usec;
    buildtag(tag, id, t);
    r = writetag(fd, offset, tag, 1);
    if (r > 0)
        *ts = t;
    return r;
}

/*
 * Attempt to acquire the lease.
 * Return 1 if succedded, 0 if not , and < 0 on errors.
 */
int
acquire(int fd, off_t offset, char *id, int busyloop, long long *ts)
{
    char curr[taglen+1] = "", last[taglen+1] = "", tag[taglen+1] = "";
    long backoff_usec = (lease_ms + 6 * op_max_ms) * 1000;
    long contend_usec = (2 * op_max_ms) * 1000;
    char dummyid[idlen+1];

    if (readtag(fd, offset, curr, 1) < 0)
        return -errno;

    settag(last, freetag);

    do {
        DEBUG("restart: curr tag is '%s'", curr);
        if (!sametag(curr, last) && !isfree(curr)) do {
            DEBUG("backoff: curr tag is '%s'", curr);
            settag(last, curr);
            usleep(backoff_usec);
            if (readtag(fd, offset, curr, 1) < 0)
                return -errno;
        } while (busyloop && !sametag(curr, last) && !isfree(curr));
        if (!sametag(curr, last) && !isfree(curr)) {
            DEBUG("fail:    curr tag is '%s'", curr);
            return 0;
        }
        DEBUG("contend: curr tag is '%s'", curr);
        if (writetimestamp(fd, offset, id, tag, ts) < 0) {
            DEBUG("lost (writetimestamp failed)  : curr tag is %s", curr);
            return -errno;
        }
        usleep(contend_usec);
        if (readtag(fd, offset, curr, 1) < 0) {
            DEBUG("lost (readtag failed)  : curr tag is %s", curr);
            return -errno;
        }
    } while (busyloop && !sametag(curr, tag));

    if (busyloop || sametag(curr, tag)) {
        DEBUG("won    : curr tag is %s", curr);
        querytag(curr, dummyid, ts);
        return renew(fd, offset, id, ts);
    }
    DEBUG("lost   : curr tag is %s\n         our tag is  %s", curr, tag);
    return 0;
}

static void
handler(int sig)
{
    PANIC("IO op too long");
}

long long
timeleft_ms(long long tsprev)
{
    struct timeval tv;
    long long tscurr;

    tsprev /= 1000;
    gettimeofday(&tv, 0);
    tscurr = tv2msec(&tv);
    DEBUG("time elapsed: %lld/%lld", tscurr - tsprev, lease_ms);
    return lease_ms - (tscurr - tsprev);
}

/*
 * Attempt to renew the lease.
 * Return 1 if succeded, 0 if not , and < 0 on errors.
 */
int
renew(int fd, off_t offset, char *id, long long *ts)
{
    char curr[taglen+1] = "", tag[taglen+1] = "";
    char dummyid[idlen+1];
    struct sigaction sa;
    long long msleft;
    int rc = 0;

    sa.sa_flags = !SA_RESTART;
    sigemptyset(&sa.sa_mask);
    sa.sa_handler = handler;
    if (sigaction(SIGALRM, &sa, NULL) == -1)
        PANIC("sigaction: can't set alarm");

    if (readtag(fd, offset, curr, 0) < 0) {
        rc = -errno;
        goto out;
    }

    DEBUG("curr tag is '%s'", curr);
    if (!sameid(curr, id)) {
        *ts = 0;
        goto out;
    }

    querytag(curr, dummyid, ts);
    msleft = timeleft_ms(*ts);
    if (msleft <= 0) {
        rc = -ETIMEDOUT;
        goto out;
    }

    alarm(msleft / 1000);
    DEBUG("updating tag: msleft %lld", msleft);
    if (writetimestamp(fd, offset, id, tag, ts) < 0) {
        rc = -errno;
        goto out;
    }

    DEBUG("All good");
    /* disable the alarm because usleep might use the same signal */
    alarm(0);
    return 1;

out:
    alarm(0);
    return rc;
}

/*
 * Attempt to release the lease.
 * Return 1 if succedded, 0 if not , and < 0 on errors.
 */
int
release(int fd, off_t offset, char *id, int force)
{
    char curr[taglen+1] = "";

    if (!force) {
        if (readtag(fd, offset, curr, 0) < 0)
            return -errno;

        if (!sameid(curr, id))
            return 0;
    }

    return writetag(fd, offset, freetag, 0) < 0 ? -1 : 1;
}

/*
 * Qeury the lease.
 * Return 1 if succedded, 0 if not , and < 0 on errors.
 */
int
query(int fd, off_t offset)
{
    char curr[taglen+1] = "";
    char id[idlen+1] = "";
    long long ts;
    time_t tsec;
    int tusec;
    char *t;

    if (readtag(fd, offset, curr, 0) < 0)
        return -errno;

    querytag(curr, id, &ts);
    tsec = ts / 1000000;
    tusec = ts % 1000000;

    t = ctime(&tsec);
    t[strlen(t)-1] = 0;

    printf("%s: ID %-*s TS %0*llx (%s, %d usec)\n", 
        sameid(curr, freetag) ? "FREE" : "LOCKED",
        idlen, id, stamplen, ts, t, tusec);

    return 1;
}

void
validate_path(const char *path)
{
    if (access(path, R_OK | W_OK) < 0)
        PANIC("can't access '%s'", path);
}

void
validate_id(const char *id)
{
    if (strlen(id) > idlen)
        PANIC("id must be <= 8 characters");
    if (!strncmp(id, freetag, idlen))
        PANIC("can't lease free stamp");
}

void
validate_lease_params(int lease_ms, int op_max_ms)
{
    if (lease_ms <= 0 || op_max_ms <= 0 || lease_ms < op_max_ms ||
        op_max_ms < 1000 || op_max_ms % 1000 != 0)
        PANIC("bad lease/op max timeouts");
}

/*
 * Initialize the timeout to one op_max_ms.
 */
long long
renew_timeout(void)
{
    struct timeval tv;

    gettimeofday(&tv, 0);
    return tv.tv_sec * 1000000ull + tv.tv_usec - (lease_ms - op_max_ms) * 1000;
}

int
cmd_acquire(int argc, char **argv)
{
    int opt, fd, r, b = 0;
    off_t offset = 0;
    long long ts;

    optind = 0;
    while ((opt = getopt(argc, argv, "+hdr:bo:")) != -1) {
        switch (opt) {
        case 'h':
            usage();
            break;
        case 'd':
            debug++;
            break;
        case 'r':
            request = optarg;
            break;
        case 'b':
            b = 1;
            break;
        case 'o':
            offset = strtoul(optarg, 0, 0);
            break;
        }
    }
    if (argc - optind < 4)
        usage();

    path = argv[optind++];
    validate_path(path);
    id = argv[optind++];
    validate_id(id);
    lease_ms = strtoul(argv[optind++], 0, 0);
    op_max_ms = strtoul(argv[optind++], 0, 0);
    validate_lease_params(lease_ms, op_max_ms);

    DEBUG("path '%s' offset %ld id '%s' lease_ms %ld op_max_ms %ld",
        path, offset, id, lease_ms, op_max_ms);

    if ((fd = open(path, O_RDWR | O_DIRECT)) < 0)
        panic("can't open '%s'", path);

    r = acquire(fd, offset, id, b, &ts);

    close(fd);

    if (r == 1) {
        /* print last successful timestamp == aquire time */
        printf("%lld", ts);
        DEBUG("Succeeded");
        return 0;
    } else
        DEBUG("%s (%s)", "Failed", strerror(r));

    return 1;
}

int
cmd_renew(int argc, char **argv)
{
    long long ts = renew_timeout();
    off_t offset = 0;
    int opt, fd, r;

    optind = 0;
    while ((opt = getopt(argc, argv, "+hdr:o:t:")) != -1) {
        switch (opt) {
        case 'h':
            usage();
            break;
        case 'd':
            debug++;
            break;
        case 'r':
            request = optarg;
            break;
        case 'o':
            offset = strtoul(optarg, 0, 0);
            break;
        case 't':
            ts = strtoll(optarg, 0, 0);
            break;
        }
    }
    if (argc - optind < 4)
        usage();

    path = argv[optind++];
    validate_path(path);
    id = argv[optind++];
    validate_id(id);
    lease_ms = strtoul(argv[optind++], 0, 0);
    op_max_ms = strtoul(argv[optind++], 0, 0);
    validate_lease_params(lease_ms, op_max_ms);

    DEBUG("path '%s' offset %ld id '%s' lease_ms %ld op_max_ms %ld",
        path, offset, id, lease_ms, op_max_ms);

    if ((fd = open(path, O_RDWR | O_DIRECT)) < 0)
        panic("can't open '%s'", path);

    r = renew(fd, offset, id, &ts);

    close(fd);

    /* print out the last successful renewal timestamp, or zero for don't renew */
    printf("%lld\n", ts);

    if (r == 1) {
        DEBUG("Succeeded");
        return 0;
    }

    DEBUG("%s (%s)", "Failed", strerror(r));
    return 1;
}

int
cmd_release(int argc, char **argv)
{
    int opt, fd, r;
    int force = 0;
    off_t offset = 0;

    optind = 0;
    while ((opt = getopt(argc, argv, "+hdfo:")) != -1) {
        switch (opt) {
        case 'h':
            usage();
            break;
        case 'd':
            debug++;
            break;
        case 'f':
            force++;
            break;
        case 'o':
            offset = strtoul(optarg, 0, 0);
            break;
        }
    }
    if (argc - optind < 2)
        usage();

    path = argv[optind++];
    validate_path(path);
    id = argv[optind++];
    validate_id(id);

    DEBUG("path '%s' offset %ld id '%s' force %d", path, offset, id, force);

    if ((fd = open(path, O_RDWR | O_DIRECT)) < 0)
        panic("can't open '%s'", path);

    r = release(fd, offset, id, force);

    close(fd);

    if (r == 1) {
        DEBUG("Succeeded");
        return 0;
    } else
        DEBUG("%s (%s)", "Failed", strerror(r));

    return 1;
}

int
cmd_query(int argc, char **argv)
{
    int opt, fd, r;
    off_t offset = 0;

    optind = 0;
    while ((opt = getopt(argc, argv, "+hdr:o:")) != -1) {
        switch (opt) {
        case 'h':
            usage();
            break;
        case 'd':
            debug++;
            break;
        case 'r':
            request = optarg;
            break;
        case 'o':
            offset = strtoul(optarg, 0, 0);
            break;
        }
    }
    if (argc - optind < 4)
        usage();

    path = argv[optind++];
    validate_path(path);

    DEBUG("path '%s' offset %ld id '%s'", path, offset, id);

    if ((fd = open(path, O_RDWR | O_DIRECT)) < 0)
        panic("can't open '%s'", path);

    r = query(fd, offset);

    close(fd);

    if (r == 1) {
        DEBUG("Succeeded");
        return 0;
    } else
        DEBUG("%s (%s)", "Failed", strerror(r));

    return 1;
}

int
cmd_protect(int argc, char **argv)
{
    return 0;
}

void
sig_handler(int sig)
{
    fprintf(stderr, "%s: Exiting due to signal %d\n", progname, sig);
    exit(0);
}

int
main(int argc, char **argv)
{
    int opt;
    void *v = 0;

    signal(SIGTERM, sig_handler);
    signal(SIGINT, sig_handler);
    signal(SIGTRAP, sig_handler);

    if (posix_memalign(&v, 4096, 512) != 0)
    {
        fprintf(stderr, "fatal memory allocation error\n");
        return 1;
    }

    iobuf = v;
    memset(iobuf, 0, 512);

    progname = strrchr(argv[0], '/');
    if (!progname)
        progname = argv[0];
    else
        progname++;

    while ((opt = getopt(argc, argv, "+hd")) != -1) {
        switch (opt) {
        case 'h':
            usage();
            break;
        case 'd':
            debug++;
            break;
        }
    }
    if (optind >= argc)
        usage();

    if (!strcmp(argv[optind], "acquire"))
        return cmd_acquire(argc - optind, argv + optind);
    if (!strcmp(argv[optind], "renew"))
        return cmd_renew(argc - optind, argv + optind);
    if (!strcmp(argv[optind], "release"))
        return cmd_release(argc - optind, argv + optind);
    if (!strcmp(argv[optind], "query"))
        return cmd_query(argc - optind, argv + optind);
    if (!strcmp(argv[optind], "protect"))
        return cmd_protect(argc - optind, argv + optind);

    fprintf(stderr, "unknonwn op <%s>\n", argv[optind]);
    usage();

    return 1;
}

