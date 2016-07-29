"""ageo.ranging - active geolocation library: ranging functions.

A ranging function computes (non-normalized) probability as a function
of geographic location, given a reference location, calibration data
(see ageo.calibration), and a set of timing observations.  This module
provides several different algorithms for this calculation.
"""

import numpy as np
import pyproj
from shapely.geometry import Point, MultiPoint
from shapely.ops import transform as sh_transform
from functools import partial
from sys import stderr

WGS84_globe = pyproj.Proj(proj='latlong', ellps='WGS84')

def Disk(x, y, radius):
    return Point(x, y).buffer(radius)


# Convenience wrappers for forward and inverse geodetic computations
# on the WGS84 ellipsoid, smoothing over some warts in pyproj.Geod.
# inv() and fwd() take coordinates in lon/lat order and distances in
# meters, whereas the rest of this program uses lat/lon order and
# distances in kilometers.  They are vectorized internally, but do not
# support numpy-style broadcasting.  The prebound _Fwd, _Inv, and
# _Bcast are strictly performance hacks.
_WGS84geod = pyproj.Geod(ellps='WGS84')
def WGS84dist(lat1, lon1, lat2, lon2, *,
              _Inv = _WGS84geod.inv, _Bcast = np.broadcast_arrays):
    _, _, dist = _Inv(*_Bcast(lon1, lat1, lon2, lat2))
    return dist/1000
def WGS84loc(lat, lon, az, dist, *,
             _Fwd = _WGS84geod.fwd, _Bcast = np.broadcast_arrays):
    tlon, tlat, _ = _Fwd(*_Bcast(lon, lat, az, dist*1000))
    return tlat, tlon

# half of the equatorial circumference of the Earth, in meters
# it is impossible for the target to be farther away than this
DISTANCE_LIMIT = 20037508

class RangingFunction:
    """Abstract base class."""

    def __init__(self, calibration, rtts, fuzz):
        self.calibration = calibration
        self.rtts = rtts
        self.fuzz = fuzz

    def unnormalized_pvals(self, distances):
        raise NotImplementedError

    def distance_bound(self):
        raise NotImplementedError

class MinMax(RangingFunction):
    """A min-max ranging function is a flat nonzero value for any distance
       in between the minimum and maximum distances considered
       feasible by the calibration, and 0 otherwise.
    """

    def __init__(self, *args, **kwargs):
        RangingFunction.__init__(self, *args, **kwargs)
        min_dist, max_dist = \
            self.calibration.distance_range(self.rtts)
        if min_dist < 0 or max_dist < 0 or max_dist < min_dist:
            stderr.write("Inconsistent distance range [{}, {}], clamping\n"
                         .format(min_dist, max_dist))
            min_dist = max(min_dist, 0)
            max_dist = max(max_dist, min_dist)

        self.min_dist = min(min_dist, DISTANCE_LIMIT)
        self.min_fuzz = max(0, self.min_dist - self.fuzz)
        self.max_dist = min(max_dist, DISTANCE_LIMIT)
        self.max_fuzz = min(self.max_dist + self.fuzz, DISTANCE_LIMIT)

    def distance_bound(self):
        return self.max_fuzz

    def unnormalized_pvals(self, dist):
        # The unnormalized probability is 1 at each point within
        # [min_dist, max_dist], and falls off linearly with the
        # distance beyond, to 0 outside ]min_fuzz, max_fuzz[.

        rv = np.zeros_like(dist)
        rv[self.min_dist <= dist <= self.max_dist] = 1

        fb = self.min_fuzz < dist < self.min_dist
        fa = self.max_dist < dist < self.max_fuzz

        rv[fb] = (self.min_dist - dist[fb]) / self.fuzz
        rv[fa] = (dist[fa] - self.max_dist) / self.fuzz

        return rv
