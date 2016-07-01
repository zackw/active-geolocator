/* Network round-trip time measurement core for probe.py:
 * shared routine declarations.
 */

#ifndef PROBE_CORE_COMMON_H__
#define PROBE_CORE_COMMON_H__

#include "config.h"

#include <assert.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>

#include <poll.h>
#include <stdio.h>

#if defined __GNUC__ && __GNUC__ >= 4
# define NORETURN void __attribute__((noreturn))
# define PRINTFLIKE __attribute__((format(printf,1,2)))
# if __GNUC__ >= 5 || (__GNUC__ == 4 && __GNUC_MINOR__ >= 3)
#  define CALLOCLIKE __attribute__((malloc, alloc_size(1,2)))
# else
#  define CALLOCLIKE __attribute__((malloc))
# endif
#else
# define NORETURN void
# define PRINTFLIKE /*nothing*/
# define CALLOCLIKE /*nothing*/
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

/* Miscellaneous miscellaneous */

extern void progress_report(uint64_t now, size_t n_conns, size_t n_proc,
                            int n_pending);

#endif /* probe-core-common.h */
