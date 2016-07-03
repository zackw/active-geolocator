/* Network round-trip time measurement core for probe.py - shared routines.
 *
 * This program is effectively a subroutine of probe.py, written in C
 * to eliminate interpreter overhead.  It is not intended to be run
 * directly.  It communicates with probe.py via a shared memory segment.
 *
 * This file contains routines shared between probe-core-direct and
 * probe-core-socks.
 */

#include "probe-core.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <netdb.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#if !defined HAVE_CLOCK_GETTIME
# if defined HAVE_MACH_ABSOLUTE_TIME
#  include <mach/mach_time.h>
# elif defined HAVE_GETTIMEOFDAY
#  include <sys/time.h>
# endif
#endif

#if !defined HAVE_CLOSEFROM && !defined F_CLOSEM
#include <dirent.h>
#endif

/* Error reporting */
const char *progname;

NORETURN
fatal(const char *msg)
{
  fprintf(stderr, "%s: %s\n", progname, msg);
  exit(1);
}

NORETURN
fatal_perror(const char *msg)
{
  fprintf(stderr, "%s: %s: %s\n", progname, msg, strerror(errno));
  exit(1);
}

PRINTFLIKE NORETURN
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

PRINTFLIKE NORETURN
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

/* Perform operation or crash */
unsigned long
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

void *
xcalloc(size_t nmemb, size_t size, const char *msgprefix)
{
  void *rv = calloc(nmemb, size);
  if (!rv)
    fatal_perror(msgprefix);
  return rv;
}

/* Time handling.  We prefer `clock_gettime(CLOCK_MONOTONIC)`, but
   we'll use `mach_get_absolute_time` or `gettimeofday` (which are not
   guaranteed to be monotonic) if that's all we can have.  All are
   converted to unsigned 64-bit nanosecond counts (relative to program
   start, to avoid overflow) for calculation.  */

#if defined HAVE_CLOCK_GETTIME
static uint64_t clock_zero;

void
clock_init(void)
{
  clock_zero = clock_monotonic();
}

uint64_t
clock_monotonic(void)
{
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (((uint64_t) t.tv_sec) * 1000000000 + t.tv_nsec) - clock_zero;
}

#elif defined HAVE_MACH_ABSOLUTE_TIME
/* from https://stackoverflow.com/questions/23378063/ "pragmatic
   answer", converted to C */

static mach_timebase_info_data_t ratio;
static uint64_t bias;

void
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

uint64_t
clock_monotonic(void)
{
  uint64_t now = mach_absolute_time();
  return (now - bias) * ratio.numer / ratio.denom;
}

#elif defined HAVE_GETTIMEOFDAY
static uint64_t clock_zero;

void
clock_init(void)
{
  clock_zero = clock_monotonic();
}

uint64_t
clock_monotonic(void)
{
  struct timeval t;
  gettimeofday(&t, 0);
  return (((uint64_t) t.tv_sec) * 1000000 + t.tv_usec) * 1000 - clock_zero;
}

#else
# error "Need a high-resolution monotonic clock"
#endif

int
clock_poll(struct pollfd fds[], nfds_t nfds, uint64_t timeout)
{
#if defined HAVE_PPOLL
  struct timespec ts;
  ts.tv_sec  = timeout / 1000000000;
  ts.tv_nsec = timeout % 1000000000;
  return ppoll(fds, nfds, &ts, 0);

#elif defined HAVE_POLL
  /* plain poll() timeout is in milliseconds */
  return poll(fds, nfds, timeout / 1000000);

#else
# error "need a way to wait for multiple sockets with timeout"
#endif
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

static void
progress_report(uint64_t now, size_t n_conns, size_t n_proc, int n_pending)
{
  clock_print_elapsed(stderr, now);
  fprintf(stderr, ": %zu/%zu probes complete, %u in progress\n",
          n_proc - (unsigned)n_pending, n_conns, n_pending);
}

int
nonblocking_socket(const struct addrinfo *ai)
{
  int sock;

#ifdef SOCK_NONBLOCK
  static bool try_sock_nonblock = true;
  if (try_sock_nonblock) {
    sock = socket(ai->ai_family,
                  ai->ai_socktype | SOCK_NONBLOCK,
                  ai->ai_protocol);
    if (sock >= 0)
      return sock;

    /* If the failure was for some other reason than lack of support for
       SOCK_NONBLOCK, the socket() call below will also fail. */
    try_sock_nonblock = false;
  }
#endif

  sock = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
  if (sock < 0)
    fatal_perror("socket");
  if (fcntl(sock, F_SETFL, O_NONBLOCK))
    fatal_perror("fcntl");
  return sock;
}

/* Clean up in case parent is sloppy.  A portability nuisance.
 * Returns the maximum fd number allowed by rlimits.
 */
int
close_unnecessary_fds(void)
{
  struct rlimit rl;
  if (getrlimit(RLIMIT_NOFILE, &rl))
    fatal_perror("getrlimit");

#if defined F_CLOSEM
  /* Some but not all of the BSDs have this very sensible fcntl()
     operation.  */
  if (fcntl(3, F_CLOSEM, 0) != 0)
    fatal_perror("fcntl(F_CLOSEM)");

#elif defined HAVE_CLOSEFROM
  /* Some other BSDs instead offer a closefrom() system call, which,
     unlike the fcntl(), cannot fail. */
  closefrom(3);

#else
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
    /* Failing all the above, the least bad option is to iterate over
       all _possibly_ open file descriptor numbers and close them,
       ignoring errors. */
    for (int fd = 3; fd < (int)rl.rlim_cur; fd++)
      close(fd);
  }
#endif

  return rl.rlim_cur;
}

struct conn_buffer *
load_conn_buffer(int fd)
{
  struct stat st;
  struct conn_buffer *buf;

  if (fstat(fd, &st))
    fatal_perror("fstat");
  if (st.st_size > (off_t)SIZE_MAX)
    fatal("connection buffer is too big to map into memory");

  buf = mmap(0, (size_t)st.st_size, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0);
  if (!buf)
    fatal_perror("mmap");

  /* sanity check */
  if ((size_t)st.st_size !=
      ((size_t)buf->n_conns) * sizeof(struct conn_data)
      + sizeof(struct conn_buffer))
    fatal_printf("connection buffer is the wrong size: %zu "
                 "(expected %zu=%u*%zu+%zu)",
                 (size_t)st.st_size,
                 ((size_t)buf->n_conns) * sizeof(struct conn_data)
                 + sizeof(struct conn_buffer),
                 buf->n_conns,
                 sizeof(struct conn_data),
                 sizeof(struct conn_buffer));

  return buf;
}

/* Main loop, called by main() in each specialization, calls back to
   next_action() in each specialization */

void
perform_probes(struct conn_buffer *cbuf,
               const struct addrinfo *proxy,
               uint32_t maxfd)
{
  uint64_t spacing = cbuf->spacing;
  uint64_t timeout = cbuf->timeout;
  uint32_t n_conns = cbuf->n_conns;
  uint32_t n_pending = 0;
  uint32_t nxt = 0;
  uint32_t i;
  uint64_t now;
  uint64_t last_conn = 0;
  uint64_t last_progress_report = 0;
  int events;

  if (cbuf->n_processed >= cbuf->n_conns)
    return; /* none left */

  struct conn_data *cdat = &cbuf->conns[0];

  struct conn_internal *cint =
    xcalloc(cbuf->n_conns, sizeof(struct conn_internal), "conn_internal");

  /* The 'pollvec' array has one more entry than necessary so that
     memmove()s below work as expected when the array is full. */
  struct pollfd *pollvec =
    xcalloc(maxfd + 1, sizeof(struct pollfd), "pollvec");

  /* The 'pending' array is indexed by file descriptor number and
     holds the index of the corresponding entries in cdat and cint.  */
  uint32_t *pending = xcalloc(maxfd, sizeof(uint32_t), "pending");
  for (i = 0; i < maxfd; i++) pending[i] = -1;

  clock_init();

  while (nxt < n_conns || n_pending) {
    now = clock_monotonic();
    /* Issue a progress report once a minute.  */
    if (last_progress_report == 0 ||
        now - last_progress_report > 60 * 1000000000ull) {
      progress_report(now, n_conns, cbuf->n_processed, n_pending);
      last_progress_report = now;
    }

    if (n_pending < maxfd - 3 && nxt < n_conns &&
        now - last_conn >= spacing) {

      while (nxt < n_conns && cdat[nxt].elapsed != 0)
        nxt++;

      if (nxt < n_conns) {
        int sock = nonblocking_socket(proxy);
        if ((uint32_t)sock > maxfd)
          fatal_printf("socket fd %d out of expected range", sock);

        now = last_conn = clock_monotonic();
        events = next_action(&cdat[nxt], &cint[nxt], sock, proxy, now);

        if (events) {
          /* The connection attempt is pending. */
          pending[sock] = nxt;
          pollvec[n_pending].fd = sock;
          pollvec[n_pending].events = events;
          pollvec[n_pending].revents = 0;
          n_pending++;
        } else
          close(sock);

        nxt++;
      }
    }

    int nready = clock_poll(pollvec, n_pending, timeout);
    if (nready < 0)
      fatal_perror("poll");
    now = clock_monotonic();

    /* Inspect all of the pending sockets for both readiness and timeout. */
    for (i = 0; i < n_pending; i++) {
      bool to_close = false;
      struct conn_data *cd     = &cdat[pending[pollvec[i].fd]];
      struct conn_internal *ci = &cint[pending[pollvec[i].fd]];
      if (pollvec[i].revents) {
        events = next_action(cd, ci, pollvec[i].fd, proxy, now);
        if (events == 0)
          to_close = true;
        else {
          pollvec[i].events = events;
          pollvec[i].revents = 0;
        }

      } else if (now - ci->begin >= timeout) {
        cd->elapsed = now - ci->begin;
        cd->errnm = ETIMEDOUT;
        to_close = true;
      }

      if (to_close) {
        pending[pollvec[i].fd] = -1;
        close(pollvec[i].fd);
        memmove(&pollvec[i], &pollvec[i+1],
                (n_pending - i)*sizeof(struct pollfd));
        n_pending--;
        i--;
        cbuf->n_processed++;
      }
    }
  }
}
