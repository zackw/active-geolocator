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
#include <sys/socket.h>

/* Values for conn_internal.state */
#define NOT_YET_CONNECTED 0
#define CONNECTING        1
#define FINISHED          2

int
next_action(struct conn_data *cd, struct conn_internal *ci, int fd,
            const struct addrinfo *UNUSED_ARG(ai), uint64_t now)
{
  switch (ci->state) {
  case NOT_YET_CONNECTED:
    ci->begin = now;

    struct sockaddr_in sin;
    memset(&sin, 0, sizeof(struct sockaddr_in));
    sin.sin_family      = AF_INET;
    sin.sin_port        = cd->tcp_port;
    sin.sin_addr.s_addr = cd->ipv4_addr;

    if (connect(fd, (struct sockaddr *)&sin, sizeof(struct sockaddr_in))) {
      if (errno == EINPROGRESS) {
        /* Connection attempt is pending. */
        ci->state = CONNECTING;
        return POLLOUT;
      } else {
        /* Synchronous connection failure.  Must read errno before
           checking the time, unfortunately. */
        cd->errnm = errno;
        now = clock_monotonic();
        goto finished;
      }
    }
    else {
      /* Synchronous connection success. */
      cd->errnm = 0;
      now = clock_monotonic();
      goto finished;
    }

  case CONNECTING: {
    /* Check for async connection failure. */
    socklen_t optlen = sizeof(cd->errnm);
    getsockopt(fd, SOL_SOCKET, SO_ERROR, &cd->errnm, &optlen);
  }
  finished:
    cd->elapsed = now - ci->begin;
    ci->state = FINISHED;

  case FINISHED:
    /* Shouldn't ever actually branch to the case label, but we can
       fall through from above. */
    return 0;

  default:
    fatal_printf("next_action called with invalid ci->state == %d\n",
                 ci->state);
  }
}

int
main(int argc, char **argv)
{
  set_progname(argv[0]);
  if (argc != 1)
    fatal("takes no command line arguments");

  uint32_t maxfd = close_unnecessary_fds();

  struct addrinfo sspec;
  memset(&sspec, 0, sizeof(struct addrinfo));
  sspec.ai_family   = AF_INET;
  sspec.ai_socktype = SOCK_STREAM;
  sspec.ai_protocol = IPPROTO_TCP;

  struct conn_buffer *cbuf = load_conn_buffer(0);
  perform_probes(cbuf, &sspec, maxfd);
  return 0;
}
