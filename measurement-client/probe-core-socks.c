/* Network round-trip time measurement core for probe.py - SOCKS version.
 *
 * This program is effectively a subroutine of probe.py, written in C
 * to eliminate interpreter overhead.  It is not intended to be run
 * directly.  It expects to receive five command line arguments:
 * PARALLEL, SPACING, TIMEOUT, PROXY_ADDR, PROXY_PORT in that order.
 * PARALLEL must be a positive integer, SPACING and TIMEOUT must be
 * positive floating-point numbers, and PROXY_ADDR + PROXY_PORT must
 * be the address of a SOCKSv5 proxy (any pair of arguments acceptable
 * to getaddrinfo() will work).  The only difference between this
 * program and probe-core.c is that all connections are made via the
 * proxy.
 *
 * No more than PARALLEL concurrent connections will occur at any one
 * time, and successive connections will be no closer to each other in
 * time than SPACING floating-point seconds.  Sockets that have
 * neither succeeded nor failed to connect after TIMEOUT
 * floating-point seconds will be treated as having failed.  No data
 * is transmitted; each socket is closed immediately after the
 * connection resolves.
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
#include <netdb.h>
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

enum socks_state {
  NOT_YET_CONNECTED = 0,
  CONNECTING,
  SENT_AUTH,
  SENT_DESTINATION,
  FINISHED
};

struct conn_data
{
  struct sockaddr_in addr;
  uint64_t begin;
  uint64_t end;
  int errnm;
  enum socks_state sstate;
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

/* SOCKS state machine */

/* Map server-side SOCKSv5 errors to errno codes (as best we can; codes
   1 and 7 don't correspond to documented error codes for connect(2)).  */
static const int socks5_errors[] = {
  /* 00 */ 0,            /* Success */
  /* 01 */ EIO,          /* General failure */
  /* 02 */ EACCES,       /* Connection not allowed by ruleset */
  /* 03 */ ENETUNREACH,  /* Network unreachable */
  /* 04 */ EHOSTUNREACH, /* Host unreachable */
  /* 05 */ ECONNREFUSED, /* Connection refused by destination host */
  /* 06 */ ETIMEDOUT,    /* TTL expired */
  /* 07 */ ENOTSUP,      /* Command not supported / protocol error */
  /* 08 */ EAFNOSUPPORT, /* Address type not supported */
};
#define N_SOCKS5_ERRORS (sizeof(socks5_errors)/sizeof(int))

/* recv() exactly NBYTES of data from FD into BUF, blocking if
   necessary (even though the socket is in nonblocking mode).
   Returns 0 if successful, -1 if a hard error occurs (including EOF) */
#ifndef MSG_WAITALL
# define MSG_WAITALL 0
#endif
static int
recv_all(int fd, size_t nbytes, char *buf)
{
  size_t nread = 0;
  ssize_t more;
  struct pollfd pfd;

  while (nread < nbytes) {
    more = recv(fd, buf + nread, nbytes - nread, MSG_WAITALL);
    if (more > 0) {
      nread += more;
    } else {
      if (more == 0 || (errno != EAGAIN && errno != EWOULDBLOCK
                        && errno != EINTR))
        return -1;

      pfd.fd = fd;
      pfd.events = POLLIN;
      if (poll(&pfd, 1, -1) == -1)
        return -1;
    }
  }
  return 0;
}

/* send() exactly NBYTES of data from BUF to FD, blocking if
   necessary (even though the socket is in nonblocking mode).
   Returns 0 if successful, -1 if a hard error occurs (including EOF).  */
static int
send_all(int fd, size_t nbytes, const char *buf)
{
  size_t nwrote = 0;
  ssize_t more;
  struct pollfd pfd;

  while (nwrote < nbytes) {
    more = send(fd, buf + nwrote, nbytes - nwrote, 0);
    if (more > 0) {
      nwrote += more;
    } else {
      if (more == 0 || (errno != EAGAIN && errno != EWOULDBLOCK
                        && errno != EINTR))
        return -1;

      pfd.fd = fd;
      pfd.events = POLLOUT;
      if (poll(&pfd, 1, -1) == -1)
        return -1;
    }
  }
  return 0;
}

/* Take the next action appropriate for connection CN, which is
   associated with socket descriptor FD.  Returns 0 if processing of
   this connection is complete (in which case FD will be closed), or
   else some combination of POLL* flags (in which case they will be
   applied on the next call to poll() for this file descriptor);
   updates CN as appropriate.  PROXY should hold the address of
   the SOCKS proxy.  */
static int
next_action(struct conn_data *cn, int fd, const struct addrinfo *proxy)
{
  switch (cn->sstate) {
  case NOT_YET_CONNECTED:
    cn->begin = clock_monotonic();
    if (connect(sock, proxy->ai_addr, proxy->ai_addrlen)) {
      if (errno == EINPROGRESS) {
        /* Connection attempt is pending. */
        cn->sstate = CONNECTING;
        return POLLOUT;
      } else {
        /* Synchronous connection failure. */
        cn->end = clock_monotonic();
        cn->sstate = FINISHED;
        cn->errnm = errno;
        return 0;
      }
    } else
      goto connection_established;

  case CONNECTING:
    /* Check for async connection failure.  */
    optlen = sizeof(cn->errnm);
    getsockopt(pollvec[i].fd, SOL_SOCKET, SO_ERROR, &cn->errnm, &optlen);
    if (cn->errnm) {
      cn->end = clock_monotonic();
      cn->sstate = FINISHED;
      return 0;
    }

  connection_established:
    /* Send an unauthenticated SOCKSv5 client handshake. */
    if (!send_all(fd, 3, "\x05\x01\x00")) {
      cn->sstate = SENT_AUTH;
      return POLLIN;
    } else {
      /* Disconnect during handshake? */
      cn->end = clock_monotonic();
      cn->sstate = FINISHED;
      cn->errnm = errno;
      return 0;
    }

  case SENT_AUTH: {
    char rbuf[2];
    char dbuf[10];
    if (recv_all(fd, 2, rbuf)) {
      /* Disconnect during handshake? */
      cn->end = clock_monotonic();
      cn->sstate = FINISHED;
      cn->errnm = errno;
      return 0;
    }
    if (rbuf[0] != '\x05' || rbuf[1] != '\x00') {
      /* Protocol error. A reply of "\x05\xFF" indicates
         unauthenticated access is denied; other responses are
         invalid.  */
      cn->end = clock_monotonic();
      cn->sstate = FINISHED;
      if (rbuf[0] == '\x05' && rbuf[1] == '\xFF')
        cn->errnm = EACCES;
      else
        cn->errnm = EIO;
      return 0;
    }

    /* Send a request to connect to a specified IPv4 address.
       Reset the timer immediately after sending the message;
       everything up to this point was just overhead.  */
    memcpy(dbuf+0, "\x05\x01\x00\x01", 4);
    memcpy(dbuf+4, cn->addr.sin_addr, 4);
    memcpy(dbuf+8, cn->addr.sin_port, 2);
    if (!send_all(fd, 10, dbuf)) {
      cn->begin = clock_monotonic();
      cn->sstate = SENT_DESTINATION;
      return POLLIN;
    } else {
      /* Disconnect during handshake? */
      cn->end = clock_monotonic();
      cn->sstate = FINISHED;
      cn->errnm = errno;
      return 0;
    }
  }

  case SENT_DESTINATION: {
    /* When we reach this point we are done with the measurement; set
       cn->end immediately.  */
    cn->end = clock_monotonic();
    cn->sstate = FINISHED;

    char rbuf[2];
    if (recv_all(fd, 2, rbuf)) {
      /* Disconnect during handshake? */
      cn->errnm = errno;
      return 0;
    }
    if (rbuf[0] != '\x05') {
      /* Protocol error. */
      cn->errnm = EIO;
      return 0;
    }
    if ((unsigned)rbuf[1] < N_SOCKS5_ERRORS)
      cn->errnm = socks5_errors[(unsigned)rbuf[1]];
    else
      cn->errnm = EIO;

    /* There's more reply waiting, but we don't care. */
    return 0;
  }

  case FINISHED:
    /* Shouldn't ever actually get here. */
    return 0;

  default:
    abort();
  }
}


/* Core measurement loop */

static int
nonblocking_tcp_socket(const struct addrinfo *proxy)
{
  int sock;

#ifdef SOCK_NONBLOCK
  static bool try_sock_nonblock = true;
  if (try_sock_nonblock) {
    sock = socket(proxy->ai_family,
                  proxy->ai_socktype | SOCK_NONBLOCK,
                  proxy->ai_protocol);
    if (sock >= 0)
      return sock;

    /* If the failure was for some other reason than lack of support for
       SOCK_NONBLOCK, the socket() call below will also fail. */
    try_sock_nonblock = false;
  }
#endif

  sock = socket(proxy->ai_family, proxy->ai_socktype, proxy->ai_protocol);
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
               uint64_t timeout_ns,
               const struct addrinfo *proxy)
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
  int events;
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

      int sock = nonblocking_tcp_socket(proxy);
      if ((unsigned)sock > parallel + 3)
        fatal_printf("socket fd %d out of expected range", sock);

      n_pending++;
      pending[sock] = cn;
      cn++;
      events = next_action(cn, sock, proxy);
      if (events) {
        /* The connection attempt is pending. */
        pending[sock] = cn;
        cn++;

        pollvec[n_pending].fd = sock;
        pollvec[n_pending].events = events;
        pollvec[n_pending].revents = 0;
        n_pending++;

      } else {
        close(sock);
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

      if (pollvec[i].revents) {
        events = next_action(cp, pollvec[i].fd, proxy);
        if (events == 0)
          to_close = true;
        else {
          pollvec[i].events = events;
          pollvec[i].revents = 0;
        }

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
  if (argc != 6)
    fatal("five arguments required: "
          "parallel spacing timeout proxy_addr proxy_port");

  struct rlimit rl;
  if (getrlimit(RLIMIT_NOFILE, &rl))
    fatal_perror("getrlimit");

  unsigned int parallel = xstrtoul(argv[1], 1, rl.rlim_cur - 3,
                                   "parallel setting");
  uint64_t spacing =
    clock_parse_decimal_seconds(argv[2], "spacing setting");
  uint64_t timeout =
    clock_parse_decimal_seconds(argv[3], "timeout setting");

  struct addrinfo *proxy;
  struct addrinfo hints;
  memset(hints, 0, sizeof hints);
  hints->ai_family = AF_UNSPEC;
  hints->ai_socktype = SOCK_STREAM;
  int gaierr = getaddrinfo(argv[4], argv[5], &hints, &proxy);
  if (gaierr)
    fatal_printf("error parsing proxy address '%s:%s': %s\n",
                 argv[4], argv[5], gai_strerror(gaierr));

  struct conn_buffer buf = parse_input();

  clock_init();
  close_unnecessary_fds((int) rl.rlim_max);
  perform_probes(&buf, parallel, spacing, timeout, proxy);
  print_results(&buf);
  return 0;
}
