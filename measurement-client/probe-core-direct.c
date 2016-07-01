/* Network round-trip time measurement core for probe.py.
 *
 * This program is a subroutine of probe.py, written in C to eliminate
 * interpreter overhead.  It is not intended to be run directly.  It
 * takes no command line arguments.  stdin is expected to be a handle
 * to a shared memory segment whose contents are a 'struct
 * conn_buffer' (see probe-core-common.h); this specifies the set of
 * connections to be made and will also receive the results of the
 * probes.  stdout is not used; error and progress messages will be
 * written to stderr.
 *
 * The conn_buffer contains a list of IPv4 addresses + TCP ports, and
 * two configuration parameters, SPACING and TIMEOUT.  One TCP
 * connection is made to each of the addresses in the conn_buffer, in
 * order; successive connections are no closer to each other in time
 * than SPACING nanoseconds; connections that have neither succeeded
 * nor failed to connect after TIMEOUT nanoseconds will be treated as
 * having failed.  No data is transmitted; each socket is closed
 * immediately after the connection resolves.  The number of in-flight
 * connection attempts is limited only by the 'number of open files'
 * rlimit.
 *
 * Written back to the conn_buffer, for each connection attempt, are the
 * errno code from connect() and the elapsed time in nanoseconds.
 */

#include "probe-core.h"

#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <string.h>
#include <unistd.h>

/* Internal per-connection data */
struct conn_internal
{
  uint64_t begin;
};

int
main(int argc, char **argv)
{
  progname = argv[0];
  if (argc != 1)
    fatal("takes no command line arguments");

  uint32_t maxfd = close_unnecessary_fds();
  uint32_t i;

  struct conn_buffer *cbuf = load_conn_buffer(0);
  if (cbuf->n_processed >= cbuf->n_conns)
    return 0; /* none left */

  uint64_t spacing = cbuf->spacing;
  uint64_t timeout = cbuf->timeout;

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

  uint32_t n_pending = 0;
  uint32_t n_conns = cbuf->n_conns;
  uint32_t nxt = 0;
  uint64_t now;
  uint64_t last_conn = 0;
  uint64_t last_progress_report = 0;

  struct addrinfo sspec;
  memset(&sspec, 0, sizeof(struct addrinfo));
  sspec.ai_family   = AF_INET;
  sspec.ai_socktype = SOCK_STREAM;
  sspec.ai_protocol = IPPROTO_TCP;

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
        int sock = nonblocking_socket(&sspec);
        if ((uint32_t)sock > maxfd)
          fatal_printf("socket fd %d out of expected range", sock);

        struct sockaddr_in sin;
        memset(&sin, 0, sizeof(struct sockaddr_in));
        sin.sin_family      = AF_INET;
        sin.sin_port        = cdat[i].tcp_port;
        sin.sin_addr.s_addr = cdat[i].ipv4_addr;

        errno = 0;
        now = last_conn = clock_monotonic();
        if (!connect(sock, (struct sockaddr *)&sin, sizeof(struct sockaddr_in))
            || errno == ECONNREFUSED
            || errno == EHOSTUNREACH
            || errno == ENETUNREACH
            || errno == ETIMEDOUT
            || errno == ECONNRESET) {
          /* The connection attempt resolved before connect() returned. */
          cdat[nxt].elapsed = clock_monotonic() - now;
          cdat[nxt].errnm = errno;
          cbuf->n_processed++;
          nxt++;

        } else if (errno == EINPROGRESS) {
          /* The connection attempt is pending. */
          cint[nxt].begin = now;

          pollvec[n_pending].fd = sock;
          pollvec[n_pending].events = POLLOUT;
          pollvec[n_pending].revents = 0;
          n_pending++;

          pending[sock] = nxt;
          nxt++;

        } else {
          /* Something dire has happened and we probably can't continue
             (for instance, there's no local network connection) */
          fatal_perror("connect");
        }
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
      socklen_t optlen;
      if (pollvec[i].revents) {
        cd->elapsed = now - ci->begin;
        optlen = sizeof(cd->errnm);
        getsockopt(pollvec[i].fd, SOL_SOCKET, SO_ERROR, &cd->errnm, &optlen);
        to_close = true;

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

  return 0;
}
