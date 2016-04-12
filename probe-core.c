/* Network round-trip time measurement core for probe.py.
 *
 * This program is effectively a subroutine of probe.py, written in C
 * to eliminate interpreter overhead.  It is not intended to be run
 * directly.  It expects to receive three numeric command line
 * arguments: PARALLEL, SPACING, and TIMEOUT, in that order.  No more
 * than PARALLEL concurrent connections will occur at any one time,
 * and successive connections will be no closer to each other in time
 * than SPACING floating-point seconds.  Sockets that have neither
 * succeeded nor failed to connect after TIMEOUT floating-point
 * seconds will be treated as having failed.  No data is transmitted;
 * each socket is closed immediately after the connection resolves.
 *
 * On stdin should be a list of addresses to connect to:
 * ipv4_address <space> tcp_port <newline>
 * One connection is made to each address in the list, in order.
 * Results are written to stdout:
 * ipv4_address <space> tcp_port <space> errno <space> elapsed_time <newline>
 * with elapsed_time in floating-point seconds.  Note that no output is
 * produced until all connections have been resolved.
 *
 * This program requires the standard POSIX sockets API and a handful
 * of other features from POSIX.1-2001 and -2008 (notably 'getline'
 * and 'clock_gettime').  It uses 'poll' instead of 'select' because
 * this has less overhead when the number of monitored file
 * descriptors is small.
 */

#define _XOPEN_SOURCE 700
#define _FILE_OFFSET_BITS 64 /* might not be necessary, but safe */

#include <stddef.h>
#include <stdbool.h>
#include <sys/types.h>

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <time.h>
#include <unistd.h>

#if defined __GNUC__ && __GNUC__ >= 4
#define NORETURN void __attribute__((noreturn))
#define PRINTFLIKE __attribute__((format(printf,1,2)))
#else
#define NORETURN void
#define PRINTFLIKE /*nothing*/
#endif

/* Error reporting */

static const char *progname;

static NORETURN
fatal(const char *msg)
{
  fprintf(stderr, "%s: %s\n", progname, msg);
  exit(1);
}

static NORETURN
fatal_perror(const char *msg)
{
  fprintf(stderr, "%s: %s: %s\n", progname, msg, strerror(errno));
  exit(1);
}

static PRINTFLIKE NORETURN
fatal_printf(const char *msg, ...)
{
  va_list ap;
  fprintf(stderr, "%s: ", progname);
  va_start(ap, msg);
  vfprintf(stderr, msg, ap);
  va_end(ap);
  putc('\n', stderr);
  exit(1);
}

static PRINTFLIKE NORETURN
fatal_eprintf(const char *msg, ...)
{
  int err = errno;
  fprintf(stderr, "%s: ", progname);
  va_list ap;
  va_start(ap, msg);
  vfprintf(stderr, msg, ap);
  va_end(ap);
  fprintf(stderr, ": %s\n", strerror(err));
  exit(1);
}

static unsigned long
xstrtoul(const char *str, unsigned long minval, unsigned long maxval,
         const char *msgprefix)
{
  unsigned long rv;
  char *endp;
  errno = 0;
  rv = strtoul(str, &endp, 10);
  if (endp == str || *endp != '\0')
    fatal_printf("%s: '%s': invalid number", msgprefix, str);
  else if (errno)
    fatal_eprintf("%s: '%s'", msgprefix, str);
  else if (rv < minval)
    fatal_printf("%s: '%s': too small (minimum %lu)", msgprefix, str, minval);
  else if (rv > maxval)
    fatal_printf("%s: '%s': too large (maximum %lu)", msgprefix, str, maxval);

  return rv;
}

static void *
xreallocarray(void *optr, size_t nmemb, size_t size)
{
  /* s1*s2 <= SIZE_MAX if both s1 < K and s2 < K where K = sqrt(SIZE_MAX+1) */
  const size_t MUL_NO_OVERFLOW = ((size_t)1) << (sizeof(size_t) * 4);

  if ((nmemb >= MUL_NO_OVERFLOW || size >= MUL_NO_OVERFLOW) &&
      nmemb > 0 && SIZE_MAX / nmemb < size) {
    errno = ENOMEM;
    fatal_perror("malloc");
  }

  void *rv = realloc(optr, size * nmemb);
  if (!rv)
    fatal_perror("malloc");
  return rv;
}

/* Timespec utilities */

static struct timespec
timespec_minus(struct timespec end, struct timespec start)
{
  struct timespec temp;
  if (end.tv_nsec - start.tv_nsec < 0) {
    temp.tv_sec = end.tv_sec - start.tv_sec - 1;
    temp.tv_nsec = 1000000000 + end.tv_nsec - start.tv_nsec;
  } else {
    temp.tv_sec = end.tv_sec - start.tv_sec;
    temp.tv_nsec = end.tv_nsec - start.tv_nsec;
  }
  return temp;
}

static bool
timespec_isless(struct timespec a, struct timespec b)
{
  if (a.tv_sec < b.tv_sec)
    return true;
  if (a.tv_sec > b.tv_sec)
    return false;
  return a.tv_nsec < b.tv_nsec;
}

static int
timespec_to_millis(struct timespec t)
{
  double val = (t.tv_sec + ((double)t.tv_nsec) * 1e-9) * 1e3;
  if (val <= 0 || val > INT_MAX)
    fatal_eprintf("cannot convert '%.6f' milliseconds to an int", val);
  return lrint(val);
}

static struct timespec
timespec_parse_decimal_seconds(const char *secs, const char *msgprefix)
{
  double n;
  char *endp;
  struct timespec rv;

  errno = 0;
  n = strtod(secs, &endp);
  if (endp == secs || *endp != '\0')
    fatal_printf("%s: '%s': invalid number", msgprefix, secs);
  else if (errno)
    fatal_eprintf("%s: '%s'", msgprefix, secs);
  else if (n <= 0)
    fatal_printf("%s: '%s': must be positive", msgprefix, secs);

  rv.tv_sec  = (time_t) floor(n);
  rv.tv_nsec = lrint((n - rv.tv_sec) * 1e9);
  if (rv.tv_nsec < 0) {
    rv.tv_sec -= 1;
    rv.tv_nsec += 1000000000;
  }
  return rv;
}

static void
timespec_print_decimal_seconds(FILE *fp, const struct timespec *ts)
{
  fprintf(fp, "%ld.%09ld", ts->tv_sec, ts->tv_nsec);
}

/* Input and output. */

struct conn_data
{
  struct sockaddr_in addr;
  struct timespec begin;
  struct timespec end;
  int errnm;
};

struct conn_buffer {
  size_t n_conns;
  struct conn_data *conns;
};

static struct conn_buffer
parse_input(void)
{
  char *linebuf = 0;
  size_t linecap = 0;

  struct conn_buffer buf;
  size_t bufcap = 10;
  buf.n_conns = 0;
  buf.conns = xreallocarray(0, bufcap, sizeof(struct conn_data));

  while (getline(&linebuf, &linecap, stdin) > 0) {

    struct conn_data *cn = &buf.conns[buf.n_conns];

    memset(cn, 0, sizeof(struct conn_data));
    cn->addr.sin_family = AF_INET;

    char *p = linebuf;
    while (*p && !isspace(*p)) p++;
    if (!*p)
      fatal_printf("incomplete input line (looking for addr): '%s'", linebuf);

    *p = '\0';
    int addr_valid = inet_pton(AF_INET, linebuf, &cn->addr.sin_addr);
    if (addr_valid == 0)
      fatal_printf("invalid IPv4 address: '%s'", linebuf);
    if (addr_valid == -1)
      fatal_eprintf("invalid IPv4 address: '%s'", linebuf);

    *p = ' ';
    do p++; while (isspace(*p));
    char *q = p;
    while (*q && *q != '\n') q++;
    if (*q != '\n')
      fatal_printf("incomplete input line (looking for port): '%s'", linebuf);
    *q = '\0';

    cn->addr.sin_port =
      ntohs(xstrtoul(p, 1, 65535, "invalid TCP port number"));

    buf.n_conns += 1;
    if (buf.n_conns >= bufcap) {
      bufcap *= 2;
      buf.conns = xreallocarray(buf.conns, bufcap, sizeof(struct conn_data));
    }
  }

  if (ferror(stdin))
    fatal_perror("getline(stdin)");
  return buf;
}

static void
print_results(const struct conn_buffer *buf)
{
  const struct conn_data *cn, *limit;
  char pton_buf[INET_ADDRSTRLEN];
  struct timespec delta;

  for (cn = buf->conns, limit = cn + buf->n_conns; cn < limit; cn++) {
    if (inet_ntop(AF_INET, &cn->addr.sin_addr, pton_buf, INET_ADDRSTRLEN)
        != pton_buf)
      fatal_perror("inet_ntop");

    printf("%s %u %d ", pton_buf, ntohs(cn->addr.sin_port), cn->errnm);

    delta = timespec_minus(cn->end, cn->begin);
    timespec_print_decimal_seconds(stdout, &delta);
    putchar('\n');
  }
}

/* Core measurement loop */

static int
nonblocking_tcp_socket(void)
{
  int sock;

#ifdef SOCK_NONBLOCK
  static bool try_sock_nonblock = true;
  if (try_sock_nonblock) {
    sock = socket(AF_INET, SOCK_STREAM|SOCK_NONBLOCK, IPPROTO_TCP);
    if (sock >= 0)
      return sock;

    /* If the failure was for some other reason than lack of support for
       SOCK_NONBLOCK, the socket() call below will also fail. */
    try_sock_nonblock = false;
  }
#endif

  sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (sock < 0)
    fatal_perror("socket");
  if (fcntl(sock, F_SETFL, O_NONBLOCK))
    fatal_perror("fcntl");
  return sock;
}

static void
perform_probes(struct conn_buffer *buf,
               unsigned int parallel,
               struct timespec spacing,
               struct timespec timeout)
{
  /* The 'pollvec' array has one more entry than necessary so that
     memmove()s below work as expected when the array is full. */
  struct pollfd *pollvec =
    xreallocarray(0, parallel + 1, sizeof(struct pollfd));
  memset(pollvec, 0, (parallel + 1) * sizeof(struct pollfd));

  /* The 'pending' array is indexed by file descriptor number.
     Setup in main() ensures that socket fds will be in the
     range [3, 3 + parallel). */
  struct conn_data **pending =
    xreallocarray(0, parallel + 3, sizeof(struct conn_data *));
  memset(pending, 0, (parallel + 3) * sizeof(struct conn_data *));

  int n_pending = 0;
  struct conn_data *cn = buf->conns;
  struct conn_data *limit = cn + buf->n_conns;
  struct timespec now;
  struct timespec last_conn = { 0, 0 };
  int spacing_m = timespec_to_millis(spacing);

  while (cn < limit || n_pending) {
    clock_gettime(CLOCK_MONOTONIC, &now);
    if ((unsigned)n_pending < parallel && cn < limit &&
        timespec_isless(spacing, timespec_minus(now, last_conn))) {

      int sock = nonblocking_tcp_socket();
      if ((unsigned)sock > parallel + 3)
        fatal_printf("socket fd %d out of expected range", sock);

      n_pending++;
      pending[sock] = cn;
      cn++;
      clock_gettime(CLOCK_MONOTONIC, &pending[sock]->begin);
      errno = 0;
      if (!connect(sock, (struct sockaddr *)&pending[sock]->addr,
                   sizeof(struct sockaddr_in))
          || errno == ECONNREFUSED
          || errno == EHOSTUNREACH
          || errno == ENETUNREACH
          || errno == ETIMEDOUT
          || errno == ECONNRESET) {
        /* The connection attempt resolved before connect() returned. */
        clock_gettime(CLOCK_MONOTONIC, &pending[sock]->end);
        pending[sock]->errnm = errno;
        pending[sock] = 0;
        n_pending--;

      } else if (errno == EINPROGRESS) {
        /* The connection attempt is pending. */
        pollvec[n_pending-1].fd = sock;
        pollvec[n_pending-1].events = POLLOUT;
        pollvec[n_pending-1].revents = 0;

      } else {
        /* Something dire has happened and we probably can't continue
           (for instance, there's no local network connection) */
        fatal_perror("connect");
      }
    }

    int nready = poll(pollvec, n_pending, spacing_m);
    if (nready < 0)
      fatal_perror("poll");
    clock_gettime(CLOCK_MONOTONIC, &now);

    /* Inspect all of the pending sockets for both readiness and timeout. */
    for (int i = 0; i < n_pending; i++) {
      bool to_close = false;
      struct conn_data *cp = pending[pollvec[i].fd];
      socklen_t optlen;
      if (pollvec[i].revents) {
        cp->end = now;
        optlen = sizeof(cp->errnm);
        getsockopt(pollvec[i].fd, SOL_SOCKET, SO_ERROR, &cp->errnm, &optlen);
        to_close = true;

      } else if (timespec_isless(timeout, timespec_minus(now, cp->begin))) {
        cp->end = now;
        cp->errnm = ETIMEDOUT;
        to_close = true;
      }

      if (to_close) {
        pending[pollvec[i].fd] = 0;
        close(pollvec[i].fd);
        memmove(&pollvec[i], &pollvec[i+1],
                (n_pending - i)*sizeof(struct pollfd));
        n_pending--;
        i--;
      }
    }
  }
}

/* clean up in case parent is sloppy */
/* only a few Unixes have a sane way to do this */
static void
close_unnecessary_fds(const struct rlimit *rl)
{
  /* Some but not all of the BSDs have this very sensible fcntl()
     operation.  Some BSDs also or instead offer a closefrom() system
     call but there is no good way to know whether it exists. */
#ifdef F_CLOSEM
  if (fcntl(3, F_CLOSEM, 0) == 0)
    return;
#endif

  /* Linux does not have F_CLOSEM as of this writing, but it does let
     you enumerate all open file descriptors via /proc. */
  DIR *fdir = opendir("/proc/self/fd");
  if (fdir) {
    int dfd = dirfd(fdir);
    struct dirent dent, *dent_out;
    int fd;

    for (;;) {
      if ((errno = readdir_r(fdir, &dent, &dent_out)) != 0)
        fatal_perror("readdir: /proc/self/fd");
      if (!dent_out)
        break;
      if (!strcmp(dent.d_name, ".") || !strcmp(dent.d_name, ".."))
        continue;

      errno = 0;
      fd = (int)xstrtoul(dent.d_name, 0, INT_MAX,
                         "invalid /proc/self/fd entry");

      if (fd >= 3 && fd != dfd)
        close((int)fd);
    }
    closedir(fdir);

  } else {
    /* Failing all the above, the least bad option is to iterate over all
       _possibly_ open file descriptor numbers and close them blindly. */
    for (int fd = 3; fd < (int)rl->rlim_max; fd++)
      close(fd);
  }
}

int
main(int argc, char **argv)
{
  progname = argv[0];
  if (argc != 4)
    fatal("three arguments required: parallel spacing timeout");

  struct rlimit rl;
  if (getrlimit(RLIMIT_NOFILE, &rl))
    fatal_perror("getrlimit");

  close_unnecessary_fds(&rl);
  unsigned int parallel = xstrtoul(argv[1], 1, rl.rlim_cur - 3,
                                   "parallel setting");

  struct timespec spacing =
    timespec_parse_decimal_seconds(argv[2], "spacing setting");
  struct timespec timeout =
    timespec_parse_decimal_seconds(argv[3], "timeout setting");

  struct conn_buffer buf = parse_input();
  perform_probes(&buf, parallel, spacing, timeout);
  print_results(&buf);
  return 0;
}
