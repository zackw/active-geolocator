"""
Geolocation by IP address, based on the MaxMind downloadable database.
"""

import os
import time

import geoip2.database
from geoip2.errors import AddressNotFoundError


class GeoLite2City:
    def __init__(self, dbpath):
        self.dbpath = dbpath
        self.db = None
        self.last_stat_time = 0
        self.last_statprint = None
        self.maybe_reload_database()

    def get(self, addr):
        self.maybe_reload_database()
        try:
            rec = self.db.city(addr)
            return rec.location.longitude, rec.location.latitude
        except AddressNotFoundError:
            return 0, 0

    def maybe_reload_database(self):
        try:
            now = time.time()
            if now - self.last_stat_time < 3600:
                assert self.db is not None
                return

            self.last_stat_time = now
            st = os.stat(self.dbpath)
            stprint = (st.st_dev, st.st_ino, st.st_size,
                       st.st_mtime_ns, st.st_ctime_ns)
            if self.last_statprint == stprint:
                assert self.db is not None
                return

            self.last_statprint = stprint
            self.db = geoip2.database.Reader(self.dbpath)

        except Exception:
            self.db = None
            self.last_stat_time = 0
            self.last_statprint = None
            raise
