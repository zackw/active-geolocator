"""ageo.ranging - active geolocation library: ranging functions.

A ranging function computes probability as a function of geographic
location, given a reference location, calibration data (see
ageo.calibration), and a set of timing observations.  This module
provides several different algorithms for this calculation.
"""

import numpy as np

class RangingFunction:
    """Abstract base class."""

    def __init__(self, ref_lon, ref_lat, calibration, timing):
        self.ref_lon = ref_lon
        self.ref_lat = ref_lat
        self.calibration = calibration
        self.timing = timing

    def __call__(self, lon, lat):
        raise NotImplementedError
