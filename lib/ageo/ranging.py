"""ageo.ranging - active geolocation library: ranging functions.

A ranging function computes (non-normalized) probability as a function
of geographic location, given a reference location, calibration data
(see ageo.calibration), and a set of timing observations.  This module
provides several different algorithms for this calculation.
"""

import numpy as np
from pyproj import Geod

_WGS84dist = Geod(ellps='WGS84').inv
def WGS84dist(lat1, lon1, lat2, lon2):
    _, _, dist = _WGS84dist(lon1, lat1, lon2, lat2)
    return dist/1000

class RangingFunction:
    """Abstract base class."""

    def __init__(self, ref_lat, ref_lon, calibration, timing):
        self.ref_lat = ref_lat
        self.ref_lon = ref_lon
        self.calibration = calibration
        self.timing = timing

    def __call__(self, lon, lat):
        raise NotImplementedError

class MinMax(RangingFunction):
    """A min-max ranging function is 1 for any distance in between
       the minimum and maximum distances considered feasible by the
       calibration, and 0 otherwise.  (These are not Booleans, they
       are unnormalized probabilities.)
    """

    def __init__(self, ref_lat, ref_lon, calibration, timing):
        RangingFunction.__init__(self, ref_lat, ref_lon,
                                 calibration, timing)

        self.min_dist, self.max_dist = \
            self.calibration.distance_range(self.timing)

    def __call__(self, lat, lon):
        dist = WGS84dist(self.ref_lat, self.ref_lon, lat, lon)
        if self.min_dist <= dist <= self.max_dist:
            return 1
        return 0
