/* Network round-trip time measurement core for probe.py:
 * shared routine declarations.
 */

#ifndef PROBE_CORE_COMMON_H__
#define PROBE_CORE_COMMON_H__

#include "config.h"

#include <poll.h>
#include <stddef.h>
#include <stdint.h>

#if defined __GNUC__ && __GNUC__ >= 4
# define NORETURN void __attribute__((noreturn))
# define PRINTFLIKE __attribute__((format(printf,1,2)))
# if __GNUC__ >= 5 || (__GNUC__ == 4 && __GNUC_MINOR__ >= 3)
#  define CALLOCLIKE __attribute__((malloc, alloc_size(1,2)))
# else
#  define CALLOCLIKE __attribute__((malloc))
# endif
# define UNUSED_ARG(arg) arg __attribute__((unused))
#else
# define NORETURN void
# define PRINTFLIKE /*nothing*/
# define CALLOCLIKE /*nothing*/
# define UNUSED_ARG(arg) arg
#endif

#ifdef HAVE__STATIC_ASSERT
#define static_assert(expr, msg) _Static_assert(expr, msg)
#else
# ifdef __COUNTER__
#  define STATIC_ASSERT_UNIQUE() STATIC_ASSERT_PASTE(static_assert_,__COUNTER__)
# else
#  define STATIC_ASSERT_UNIQUE() STATIC_ASSERT_PASTE(static_assert_,__LINE__)
# endif
# define STATIC_ASSERT_PASTE(x,y) STATIC_ASSERT_PASTE2(x,y)
# define STATIC_ASSERT_PASTE2(x,y) x##y
# define static_assert(expr, msg) \
  struct STATIC_ASSERT_UNIQUE() { int assertion_failed : !!(expr); }
#endif

/* The memory segment shared with the parent process.  On startup, this is
   accessible as file descriptor 0.  Note that the parent process is Python
   and is using struct.pack/unpack to access the segment, so it is critical
   for these structures to contain no invisible padding.  */

struct conn_data
{
  uint32_t ipv4_addr; /* read - network byte order - target IPv4 address */
  uint16_t tcp_port;  /* read - network byte order - target TCP port */
  uint16_t errnm;     /* write - native byte order - errno code */
  uint64_t elapsed;   /* write - native byte order - elapsed time in ns */
};
static_assert(sizeof(struct conn_data) == 16, "conn_data is wrong size");

struct conn_buffer
{
  uint32_t n_conns;     /* read - native byte order - total # connections */
  uint32_t n_processed; /* read/write - native byte order - # complete */
  uint32_t spacing;     /* read - native byte order - connection spacing, ns */
  uint32_t timeout;     /* read - native byte order - timeout, ns */
  struct conn_data conns[];
};
static_assert(sizeof(struct conn_buffer) == 16, "conn_buffer is wrong size");

extern struct conn_buffer *load_conn_buffer(int fd);

/* Error reporting */
extern const char *progname; /* main must set */
extern NORETURN fatal(const char *msg);
extern NORETURN fatal_perror(const char *msg);
extern PRINTFLIKE NORETURN fatal_printf(const char *msg, ...);
extern PRINTFLIKE NORETURN fatal_eprintf(const char *msg, ...);

/* Perform operation or crash */
unsigned long xstrtoul(const char *str, unsigned long minval,
                       unsigned long maxval, const char *msgprefix);
void *CALLOCLIKE xcalloc(size_t nmemb, size_t size, const char *msgprefix);

/* Time handling */
extern void clock_init(void);
extern uint64_t clock_monotonic(void); /* returns nanosecs since clock_init */
extern int clock_poll(struct pollfd fds[], nfds_t nfds, uint64_t timeout);

/* Miscellaneous portability shims */
struct addrinfo;
extern int nonblocking_socket(const struct addrinfo *ai);
extern int close_unnecessary_fds(void);

/* Core probe loop and its callback */

struct conn_internal
{
  uint64_t begin;  /* Time at which this probe began */
  uint32_t state;  /* Initially 0, next_action may use as it sees fit */
  uint32_t state2; /* Ditto - not currently used */
};

extern void perform_probes(struct conn_buffer *cbuf,
                           const struct addrinfo *proxy,
                           uint32_t maxfd);

/* Take the next action appropriate for connection CD+CI, which is
   associated with socket descriptor FD.  Returns 0 if processing of
   this connection is complete (in which case FD will be closed), or
   else some combination of POLL* flags (in which case they will be
   applied on the next call to poll() for this file descriptor);
   updates CD and CI as appropriate.  PROXY is the address of the
   proxy, if any, and NOW is the current time.  */

extern int next_action(struct conn_data *cd,
                       struct conn_internal *ci,
                       int fd,
                       const struct addrinfo *proxy,
                       uint64_t now);

#endif /* probe-core-common.h */
