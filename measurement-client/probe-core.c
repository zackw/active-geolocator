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

#include "config.h"

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
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

#if !defined HAVE_CLOCK_GETTIME
# if defined HAVE_MACH_ABSOLUTE_TIME
#  include <mach/mach_time.h>
# elif defined HAVE_GETTIMEOFDAY
#  include <sys/time.h>
# endif
#endif

#if defined __GNUC__ && __GNUC__ >= 4
# define NORETURN void __attribute__((noreturn))
# define PRINTFLIKE __attribute__((format(printf,1,2)))
#else
# define NORETURN void
# define PRINTFLIKE /*nothing*/
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

/* Time handling.  We prefer `clock_gettime(CLOCK_MONOTONIC)`, but
   we'll use `mach_get_absolute_time` or `gettimeofday` (which are not
   guaranteed to be monotonic) if that's all we can have.  All are
   converted to unsigned 64-bit nanosecond counts (relative to program
   start, to avoid overflow) for calculation.  */

#if defined HAVE_CLOCK_GETTIME
static time_t clock_zero_seconds;
static void
clock_init(void)
{
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  clock_zero_seconds = t.tv_sec;
}

static uint64_t
clock_monotonic(void)
{
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return ((uint64_t)(t.tv_sec - clock_zero_seconds)) * 1000000000 + t.tv_nsec;
}

#elif defined HAVE_MACH_ABSOLUTE_TIME
/* from https://stackoverflow.com/questions/23378063/ "pragmatic
   answer", converted to C */

static mach_timebase_info_data_t ratio;
static uint64_t bias;
static void
clock_init(void)
{
  mach_timebase_info_data_t tb;
  kern_return_t status = mach_timebase_info(&tb);
  if (status != KERN_SUCCESS)
    // There doesn't seem to be an equivalent of strerror() for
    // kern_return_t, but it doesn't matter because mach_timebase_info
    // can't actually fail (per code inspection).
    fatal_printf("mach_timebase_info: failed (code %d)", status);

  uint64_t now = mach_absolute_time();
  if (tb.denom > 1024) {
    double frac = (double)tb.numer/tb.denom;
    tb.denom = 1024;
    tb.numer = tb.denom * frac + 0.5;
    if (tb.numer <= 0)
      fatal_printf("scaling mach_timebase_info failed (frac=%f)", frac);
  }
  bias = now;
  ratio = tb;
}

static uint64_t
clock_monotonic(void)
{
  uint64_t now = mach_absolute_time();
  return (now - bias) * ratio.numer / ratio.denom;
}

#elif defined HAVE_GETTIMEOFDAY
static time_t clock_zero_seconds;
static void
clock_init(void)
{
  struct timeval t;
  gettimeofday(&t, 0);
  clock_zero_seconds = t.tv_sec;
}

static uint64_t
clock_monotonic(void)
{
  struct timeval t;
  gettimeofday(&t, 0);
  return ((((uint64_t)(t.tv_sec - clock_zero_seconds)) * 1000000 + t.tv_usec)
          * 1000);
}

#else
# error "Need a high-resolution monotonic clock"
#endif

static uint64_t
clock_parse_decimal_seconds(const char *str, const char *msgprefix)
{
  double n;
  char *endp;

  errno = 0;
  n = strtod(str, &endp);
  if (endp == str || *endp != '\0')
    fatal_printf("%s: '%s': invalid number", msgprefix, str);
  else if (errno)
    fatal_eprintf("%s: '%s'", msgprefix, str);
  else if (n <= 0)
    fatal_printf("%s: '%s': must be positive", msgprefix, str);

  return (uint64_t) lrint(n * 1e9);
}

static void
clock_print_decimal_seconds(FILE *fp, uint64_t nsec)
{
  double n = ((double)nsec) * 1e-9;
  fprintf(fp, "%f", n);
}

static void
clock_print_elapsed(FILE *fp, uint64_t nsec)
{
  unsigned int h, m;
  double s;
  double a = ((double)nsec) * 1e-9;
  h = (unsigned int)floor(a / 3600);
  m = (unsigned int)floor(fmod(a, 3600) / 60);
  s = fmod(a, 60);
  fprintf(fp, "%uh %02um %06.3fs", h, m, s);
}

#if defined HAVE_PPOLL
typedef struct timespec poll_timeout;

static poll_timeout
clock_to_timeout(uint64_t nsec)
{
  poll_timeout rv;
  rv.tv_sec  = nsec / 1000000000;
  rv.tv_nsec = nsec % 1000000000;
  return rv;
}

static int
clock_poll(struct pollfd fds[], nfds_t nfds, poll_timeout timeout)
{
  return ppoll(fds, nfds, &timeout, 0);
}

#elif defined HAVE_POLL
typedef int poll_timeout;

static poll_timeout
clock_to_timeout(uint64_t nsec)
{
  /* plain poll() timeout is in milliseconds */
  return nsec / 1000000;
}

static int
clock_poll(struct pollfd fds[], nfds_t nfds, poll_timeout timeout)
{
  return poll(fds, nfds, timeout);
}

#else
# error "need a way to wait for multiple sockets with timeout"
#endif


/* Input and output. */

struct conn_data
{
  struct sockaddr_in addr;
  uint64_t begin;
  uint64_t end;
  int errnm;
};

struct conn_buffer {
  size_t n_conns;
  struct conn_data *conns;
};

#ifndef HAVE_GETLINE
/* getline replacement taken from gnulib */
static ssize_t
getline(char **lineptr, size_t *n, FILE *stream)
{
  enum { MIN_CHUNK = 64 };

  if (!lineptr || !n || !stream) {
    errno = EINVAL;
    return -1;
  }

  if (!*lineptr) {
    *n = MIN_CHUNK;
    *lineptr = malloc(*n);
    if (!*lineptr) {
      errno = ENOMEM;
      return -1;
    }
  }

  int nchars_avail = *n;
  char *read_pos = *lineptr;

  for (;;) {
    int save_errno;
    int c = getc(stream);

    save_errno = errno;

    /* We always want at least one char left in the buffer, since we
       always (unless we get an error while reading the first char)
       NUL-terminate the line buffer.  */
    if (nchars_avail < 2) {
      if (*n > MIN_CHUNK)
        *n = *n * 3 / 2;
      else
        *n += MIN_CHUNK;

      nchars_avail = *n + *lineptr - read_pos;
      *lineptr = realloc(*lineptr, *n);
      if (!*lineptr) {
        errno = ENOMEM;
        return -1;
      }
      read_pos = *n - nchars_avail + *lineptr;
    }

    if (ferror(stream)) {
      errno = save_errno;
      return -1;
    }

    if (c == EOF) {
      /* Return partial line, if any.  */
      if (read_pos == *lineptr)
        return -1;
      else
        break;
    }

    *read_pos++ = c;
    nchars_avail--;

    if (c == '\n')
      /* Return the line.  */
      break;
  }

  /* Done - NUL terminate and return the number of chars read.  */
  *read_pos = '\0';
  return read_pos - *lineptr;
}
#endif

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
    while (*p && !isspace((unsigned char)*p)) p++;
    if (!*p)
      fatal_printf("incomplete input line (looking for addr): '%s'", linebuf);

    *p = '\0';
    int addr_valid = inet_pton(AF_INET, linebuf, &cn->addr.sin_addr);
    if (addr_valid == 0)
      fatal_printf("invalid IPv4 address: '%s'", linebuf);
    if (addr_valid == -1)
      fatal_eprintf("invalid IPv4 address: '%s'", linebuf);

    *p = ' ';
    do p++; while (isspace((unsigned char)*p));
    char *q = p;
    while (*q && *q != '\n') q++;
    if (*q != '\n')
      fatal_printf("incomplete input line (looking for port): '%s'", linebuf);
    *q = '\0';

    cn->addr.sin_port =
      ntohs(xstrtoul(p, 1, 65535, "invalid TCP port number"));

    buf.n_conns += 1;
    if (buf.n_conns >= bufcap) {
      bufcap = bufcap * 3 / 2;
      buf.conns = xreallocarray(buf.conns, bufcap, sizeof(struct conn_data));
    }
  }

  if (!feof(stdin))
    fatal_perror("stdin");
  return buf;
}

static void
print_results(const struct conn_buffer *buf)
{
  const struct conn_data *cn, *limit;
  char pton_buf[INET_ADDRSTRLEN];

  for (cn = buf->conns, limit = cn + buf->n_conns; cn < limit; cn++) {
    if (inet_ntop(AF_INET, &cn->addr.sin_addr, pton_buf, INET_ADDRSTRLEN)
        != pton_buf)
      fatal_perror("inet_ntop");

    printf("%s %u %d ", pton_buf, ntohs(cn->addr.sin_port), cn->errnm);
    clock_print_decimal_seconds(stdout, cn->end - cn->begin);
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
progress_report(uint64_t now, size_t n_conns, size_t n_proc, int n_pending)
{
  clock_print_elapsed(stderr, now);
  fprintf(stderr, ": %zu/%zu probes complete, %u in progress\n",
          n_proc - (unsigned)n_pending, n_conns, n_pending);
}

static void
perform_probes(struct conn_buffer *buf,
               unsigned int parallel,
               uint64_t spacing,
               uint64_t timeout_ns)
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
  uint64_t now;
  uint64_t last_conn = 0;
  uint64_t last_progress_report = 0;
  poll_timeout timeout = clock_to_timeout(timeout_ns);

  while (cn < limit || n_pending) {
    now = clock_monotonic();
    /* Issue a progress report once a minute.  */
    if (last_progress_report == 0 ||
        now - last_progress_report > 60 * 1000000000ull) {
      progress_report(now, buf->n_conns, cn - buf->conns, n_pending);
      last_progress_report = now;
    }

    if ((unsigned)n_pending < parallel && cn < limit &&
        now - last_conn >= spacing) {

      int sock = nonblocking_tcp_socket();
      if ((unsigned)sock > parallel + 3)
        fatal_printf("socket fd %d out of expected range", sock);

      n_pending++;
      pending[sock] = cn;
      cn++;
      pending[sock]->begin = clock_monotonic();
      errno = 0;
      if (!connect(sock, (struct sockaddr *)&pending[sock]->addr,
                   sizeof(struct sockaddr_in))
          || errno == ECONNREFUSED
          || errno == EHOSTUNREACH
          || errno == ENETUNREACH
          || errno == ETIMEDOUT
          || errno == ECONNRESET) {
        /* The connection attempt resolved before connect() returned. */
        pending[sock]->end = clock_monotonic();
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

    int nready = clock_poll(pollvec, n_pending, timeout);
    if (nready < 0)
      fatal_perror("poll");
    now = clock_monotonic();

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

      } else if (now - cp->begin >= timeout_ns) {
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

/* Clean up in case parent is sloppy.  A portability nuisance. */
static void
close_unnecessary_fds(int maxfd)
{
  /* Some but not all of the BSDs have this very sensible fcntl()
     operation.  Some other BSDs instead offer a closefrom() system
     call, which, unlike the fcntl(), cannot fail. */
#if defined F_CLOSEM
  if (fcntl(3, F_CLOSEM, 0) == 0)
    return;
#elif defined HAVE_CLOSEFROM
  closefrom(3);
  return;
#endif

  /* Linux does not have F_CLOSEM or closefrom as of this writing, but
     it does let you enumerate all open file descriptors via /proc. */
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
    for (int fd = 3; fd < maxfd; fd++)
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

  unsigned int parallel = xstrtoul(argv[1], 1, rl.rlim_cur - 3,
                                   "parallel setting");
  uint64_t spacing =
    clock_parse_decimal_seconds(argv[2], "spacing setting");
  uint64_t timeout =
    clock_parse_decimal_seconds(argv[3], "timeout setting");

  struct conn_buffer buf = parse_input();

  clock_init();
  close_unnecessary_fds((int) rl.rlim_max);
  perform_probes(&buf, parallel, spacing, timeout);
  print_results(&buf);
  return 0;
}
