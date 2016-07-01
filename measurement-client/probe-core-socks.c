/* Network round-trip time measurement core for probe.py - SOCKS version.
 *
 * This program is a subroutine of probe.py, written in C to eliminate
 * interpreter overhead.  It is not intended to be run directly.  It
 * takes two command line arguments, the IP address and TCP port
 * respectively of a SOCKSv5 proxy, via which all connections will be
 * made.  (These can be in any form acceptable to getaddrinfo(3).)
 * stdin is expected to be a handle to a shared memory segment whose
 * contents are a 'struct conn_buffer' (see probe-core-common.h); this
 * specifies the set of connections to be made and will also receive
 * the results of the probes.  stdout is not used; error and progress
 * messages will be written to stderr.
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
 *
 * Use of the SOCKS proxy is the only difference between this program
 * and probe-core-direct.
 */

#include "probe-core.h"

#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

/* Internal per-connection data */
enum socks_state
{
  NOT_YET_CONNECTED = 0,
  CONNECTING,
  SENT_AUTH,
  SENT_DESTINATION,
  FINISHED
};

struct conn_internal
{
  uint64_t begin;
  enum socks_state sstate;
};

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

/* Take the next action appropriate for connection CD+CI, which is
   associated with socket descriptor FD.  Returns 0 if processing of
   this connection is complete (in which case FD will be closed), or
   else some combination of POLL* flags (in which case they will be
   applied on the next call to poll() for this file descriptor);
   updates CN as appropriate.  PROXY should hold the address of
   the SOCKS proxy, and NOW is the current time.  */
static int
next_action(struct conn_data *cd, struct conn_internal *ci, int fd,
            const struct addrinfo *proxy, uint64_t now)
{
  switch (ci->sstate) {
  case NOT_YET_CONNECTED:
    ci->begin = now;
    if (connect(fd, proxy->ai_addr, proxy->ai_addrlen)) {
      if (errno == EINPROGRESS) {
        /* Connection attempt is pending. */
        ci->sstate = CONNECTING;
        return POLLOUT;
      } else
        /* Synchronous connection failure. */
        goto finished;
    } else
      goto connection_established;

  case CONNECTING: {
    /* Check for async connection failure.  */
    socklen_t optlen = sizeof(cd->errnm);
    getsockopt(fd, SOL_SOCKET, SO_ERROR, &cd->errnm, &optlen);
    if (cd->errnm)
      goto finished_err_already_set;
  }

  connection_established:
    /* Send an unauthenticated SOCKSv5 client handshake. */
    if (!send_all(fd, 3, "\x05\x01\x00")) {
      ci->sstate = SENT_AUTH;
      return POLLIN;
    } else
      /* Disconnect during handshake? */
      goto finished;

  case SENT_AUTH: {
    char rbuf[2];
    char dbuf[10];
    if (recv_all(fd, 2, rbuf))
      /* Disconnect during handshake? */
      goto finished;

    if (rbuf[0] != '\x05' || rbuf[1] != '\x00') {
      /* Protocol error. A reply of "\x05\xFF" indicates
         unauthenticated access is denied; other responses are
         invalid.  */
      if (rbuf[0] == '\x05' && rbuf[1] == '\xFF')
        cd->errnm = EACCES;
      else
        cd->errnm = EIO;
      goto finished_err_already_set;
    }

    /* Send a request to connect to a specified IPv4 address.
       Reset the timer immediately after sending the message;
       everything up to this point was just overhead.  */
    memcpy(dbuf+0, "\x05\x01\x00\x01", 4);
    memcpy(dbuf+4, &cd->ipv4_addr, 4);
    memcpy(dbuf+8, &cd->tcp_port, 2);
    if (!send_all(fd, 10, dbuf)) {
      ci->begin = clock_monotonic();
      ci->sstate = SENT_DESTINATION;
      return POLLIN;
    } else
      /* Disconnect during handshake? */
      goto finished;
  }

  case SENT_DESTINATION: {
    /* When we reach this point we are done with the measurement; set
       cd->elapsed now (before reading any more data).  */
    cd->elapsed = now - ci->begin;
    ci->sstate = FINISHED;

    char rbuf[2];
    if (recv_all(fd, 2, rbuf)) {
      /* Disconnect during handshake? */
      cd->errnm = errno;
      return 0;
    }
    if (rbuf[0] != '\x05') {
      /* Protocol error. */
      cd->errnm = EIO;
      return 0;
    }
    if ((unsigned)rbuf[1] < N_SOCKS5_ERRORS)
      cd->errnm = socks5_errors[(unsigned)rbuf[1]];
    else
      cd->errnm = EIO;

    /* There's more reply waiting, but we don't care. */
    return 0;
  }

  finished:
    cd->errnm = errno;
  finished_err_already_set:
    cd->elapsed = clock_monotonic() - ci->begin;
    ci->sstate = FINISHED;

  case FINISHED:
    /* Shouldn't ever actually branch to the case label, but we can
       fall through from above. */
    return 0;

  default:
    abort();
  }
}

int
main(int argc, char **argv)
{
  progname = argv[0];
  if (argc != 3)
    fatal("two arguments required: proxy_addr proxy_port");

  struct addrinfo *proxy;
  struct addrinfo hints;
  memset(&hints, 0, sizeof hints);
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  int gaierr = getaddrinfo(argv[4], argv[5], &hints, &proxy);
  if (gaierr)
    fatal_printf("error parsing proxy address '%s:%s': %s\n",
                 argv[4], argv[5], gai_strerror(gaierr));

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
  int events;

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

  return 0;
}
