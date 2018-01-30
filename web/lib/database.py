"""
persistent database connection handling
"""

import contextlib
import threading

from psycopg2.pool import AbstractConnectionPool
from psycopg2.extras import DictCursor

class ThreadedExpiringConnectionPool(AbstractConnectionPool):
    """A thread-safe connection pool, that additionally closes down
       connections that have been idle for a configurable interval
       (default 600 seconds = 10 minutes).

       The expiration timer logic is not as efficient as it could be:
       a Timer object (which involves an entire thread) is spawned for
       every call to putconn(), and then often destroyed again before
       it expires.  However, it is much easier to be sure that it is
       correct than any plausible alternative (using heapq and/or
       sched?)
    """
    def __init__(self, minconn, maxconn, *args, max_idle_time=600, **kwargs):
        AbstractConnectionPool.__init__(
            self, minconn, maxconn, *args, **kwargs)
        self._lock = threading.Lock()
        self._max_idle_time = max_idle_time
        self._expire_timers = {} # conn id -> expiration timer

    def getconn(self, key=None):
        """Get a free connection and assign it to 'key' if not None."""
        self._lock.acquire()
        try:
            conn = self._getconn(key)
            cid = id(conn)
            timer = self._expire_timers.pop(cid, None)
            if timer is not None:
                timer.cancel()
            return conn
        finally:
            self._lock.release()

    def putconn(self, conn, key=None, close=False):
        """Put away a connection, and if it hasn't already been closed,
           schedule it to be closed after max_idle_time if it doesn't
           get reused before then."""
        self._lock.acquire()
        try:
            self._putconn(conn, key, close)
            if not conn.closed:
                self._expire_timers[id(conn)] = threading.Timer(
                    self._max_idle_time, self._expire_conn, args=(conn,))
        finally:
            self._lock.release()

    def closeall(self):
        """Forcibly close all connections (even those currently in use)."""
        self._lock.acquire()
        try:
            self._closeall()
            for timer in self._expire_timers.values():
                timer.cancel()
            self._expire_timers.clear()
        finally:
            self._lock.release()

    def _expire_conn(self, conn):
        """Internal use only: close the connection CONN and remove it
           from the pool."""
        self._lock.acquire()
        try:
            # Must recheck whether the connection has been recycled
            # after acquiring the lock.
            timer = self._expire_timers.pop(id(conn), None)
            if timer is None:
                return

            self._putconn(conn, close=True)

        finally:
            self._lock.release()

@contextlib.contextmanager
def get_db_cursor(pool, commit=False, factory=None):
    conn = pool.getconn()
    try:
        with conn:
            cur = conn.cursor(cursor_factory=DictCursor)
            yield cur
            cur.close()
            if commit:
                conn.commit()
    finally:
        pool.putconn(conn)
