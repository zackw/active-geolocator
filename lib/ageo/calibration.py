"""ageo.ranging - active geolocation library: calibration curves.

A calibration curve models the relationship between distance and
round-trip time.

"""

import collections
from functools import partial

import numpy as np
from scipy import optimize, spatial

class _Line(collections.namedtuple("__Line", ("m", "b"))):
    def __call__(self, x):
        return self.m * x + self.b

def _interp_segments(segs, x):
    """Interpolate or extrapolate the polyline defined by SEGS at the
       x-coordinate X.  Returns the corresponding y-coordinate."""
    i = np.searchsorted(segs[:,0], x)
    if i == segs.shape[0]:
        assert x > segs[-1,0]
        x1, y1 = segs[-2,:]
        x2, y2 = segs[-1,:]
    elif x == segs[i,0]:
        return segs[i,1]
    elif i == 0:
        assert x < segs[-1,0]
        x1, y1 = segs[0,:]
        x2, y2 = segs[1,:]
    else:
        x1, y1 = segs[i-1,:]
        x2, y2 = segs[i,:]

    return y1 + (y2-y1)*(x-x1)/(x2-x1)

class _PolyLine:
    def __init__(self, points):
        self._points = points[points[:,0].argsort()]

    def __call__(self, x):
        return _interp_segments(self._points, x)

class Calibration:
    """Abstract base class."""

    def distance_range(self, rtts):
        """Given a vector of round-trip-time measurements RTTS, in
        milliseconds, compute and return a pair (min, max) which are
        the minimum and maximum plausible distance, in kilometers over
        the surface of the Earth, from the reference point to the target.
        """

        # Default implementation relies on the subclass constructor
        # setting a value for _curve.  We consider only the smallest
        # RTT, because that is the one least influenced by delays not
        # due to the true distance.
        min_rtt  = np.amin(rtts)
        min_dist = self._curve['min'](min_rtt)
        max_dist = self._curve['max'](min_rtt)
        return (max(min_dist, 0),
                max(max_dist, 0))

class PhysicalLimitsOnly(Calibration):
    """This calibration curve considers only physical limits, which can be
    (and are) hardwired in the code, so no data is required to create
    one.

    Network cables (whether optical or electrical) all propagate
    signals at roughly the same speed, 2/3 c = 199,862 kilometers per
    second.  Surface microwave links are faster, but rarely cover
    significant distance, as they require a line of sight.  All told,
    200,000 km/s is a reasonable choice for a _theoretical_
    propagation speed limit.

    Considering also processing and queueing delays, several papers
    take 4/9 c = 133,241 km/s as the maximum "speed of internet"; RIPE
    data indicates that this can be too conservative, but not by much;
    a maximum speed of 153,000 km/s (0.5104 c) seems to be "safe".

    There is no _lower_ limit to propagation speed in principle---
    queueing and processing delays can be arbitrarily large.  (RFC
    1122 specifies a minimum timeout of 100 seconds for established
    connections.)  However, beyond some outer limit, time measurements
    are no longer providing meaningful information about the distance.
    In particular, a satellite link will impose 238-284ms of delay
    (speed-of-light travel time to geostationary orbit and back)
    depending on the up and downlink points; the difference is only
    16%.

    Empirically, based on RIPE data again, treating the _slowest_
    reasonable propagation speed as 110,000 km/s (0.3669 c) plus
    allowing up to 55ms delay at distance 0 appears to do a good job
    of excluding outliers.  This translates to 237ms to cover half the
    circumference of the planet, which neatly cuts off satellite links.

    This calibration curve can be used in two modes: in 'physical'
    mode the fastest speed is 200,000 km/s and the slowest is zero;
    in 'empirical' mode the fastest speed is 153,000 km/s and the
    slowest is 110,000 km/s (plus 55ms delay at distance 0).

    """

    # The slopes of these lines are kilometers per millisecond, and
    # they're all half what you'd expect from the above discussion
    # because the /2 to convert RTT to OWTT has been baked in.
    _CURVES = {
        'empirical': {
            'max': _Line(76.5, 0),
            'min': _Line(55, -55*55),
        },
        'physical':  {
            'max': _Line(100, 0),
            'min': _Line(0, 0)
        }
    }

    def __init__(self, mode='empirical'):
        self._curve = self._CURVES[mode]

def discard_infeasible(obs):
    """Discard infeasible observations from OBS, which is expected to be a
       N-by-2 matrix where the first column is distances and the second
       column is round-trip times.  Returns a subset matrix.

       An observation is infeasible if it implies a propagation speed
       _faster_ than 200,000 km/s, or _slower_ than 110,000 km/s after
       subtracting a 55ms constant delay.  These limits are explained
       in the docstring for PhysicalLimitsOnly.
    """
    if len(obs.shape) != 2:
        raise ValueError("OBS should be a 2D matrix, not {}D"
                         .format(len(obs.shape)))

    if obs.shape[0] == 0 or obs.shape[1] != 2:
        raise ValueError("OBS should be an N-by-2 matrix, not {}-by-{}"
                         .format(*obs.shape))

    feasible = np.logical_and(
        obs[:,1] * 100     >= obs[:,0],
        (obs[:,1] - 55)*55 <= obs[:,0]
    )
    return obs[feasible, ...]

class CBG(Calibration):
    """The CBG algorithm (from "Constraint-based Geolocation of Internet
    Hosts", IMC 2004) takes a set of calibration observations---RTTs
    to hosts at known distances---and computes their "bestline", which
    is the line that is closest to, but below, all calibration data
    points.  It is also required to have a non-negative intercept
    (when distance is plotted on the x-axis) since "it makes no sense
    to consider negative delays".  This line is taken to indicate the
    fastest possible propagation speed.  The slowest possible
    propagation speed is always taken to be zero.
    """

    def __init__(self, obs):
        """OBS should be an N-by-2 matrix where the first column is distances
        and the second column is round-trip times."""

        obs = discard_infeasible(obs)
        if obs.shape[0] == 0:
            raise ValueError("not enough feasible observations")

        # Eliminate redundant observations; for each distance d_i,
        # only the minimum round-trip time min {r_i} can contribute to
        # the solution.  Also, eliminate self-pings (d_i == 0) if any.
        dists = np.unique(obs[:,0])
        while len(dists) > 0 and dists[0] < 1:
            dists = dists[1:]

        if len(dists) == 0:
            raise ValueError("not enough unique non-self observations")

        minrtts = np.zeros_like(dists)
        for i, dist in enumerate(dists):
            thisdist = obs[:,0] == dist
            minrtts[i] = np.amin(obs[thisdist,1])

        # The goal is to find m, b that minimize \sum_i (y_i - mx_i - b)
        # while still satisfying y_i \ge mx_i + b for all i.
        # As a linear programming problem, this corresponds to minimizing
        # 1Y - mX - bI where Y = \sum_i y_i, X = \sum_i x_i, I = \sum_i 1.
        # The data constraints take the form 0·1 + x_i·m + 1·b \le y_i.
        #
        # We also impose physical constraints:
        #   m >= 1/100         200,000 km/s physical speed limit
        #   b >= 0             negative fixed delays don't make sense
        #   b <= min(minrtts)  otherwise the fit will not work
        #
        # Finally, we add an artificial data constraint:
        #   x_limit = 20037.5  half of Earth's equatorial circumference
        #   y_limit = 237.16   empirical "slowest plausible" time to
        #                      traverse that distance (see above)
        #
        # This last ensures that the fit will not select a data point from
        # a satellite link as a defining point for the line.

        coef = np.array([np.sum(minrtts), -np.sum(dists), -len(dists)])
        cx = np.append(dists, 20037.5)
        cy = np.append(minrtts, 237.16)
        constr_A = np.column_stack((
            np.zeros_like(cx), cx, np.ones_like(cx)
        ))
        constr_B = np.column_stack((cy,))
        bounds = [(1,1), (1/100, None), (0, np.amin(cy))]

        fit = optimize.linprog(coef,
                               A_ub=constr_A,
                               b_ub=constr_B,
                               bounds=bounds)

        if not fit.success:
            raise RuntimeError("CBG: failed to find bestline: \n" + str(fit))

        # The linear program found a "bestline", mapping distance to
        # latency.  The "max curve" is the inverse function of this
        # bestline, mapping latency to distance.  Coefficient 0 of the
        # fit is a dummy.
        m = 1/fit.x[1]
        b = -m * fit.x[2]
        self._curve = {
            'max': _Line(m, b),
            'min': _Line(0, 0)
        }

class QuasiOctant(Calibration):
    """An algorithm derived from "Octant: A Comprehensive Framework for
       the Geolocalization of Internet Hosts," NSDI 2007.

       Quasi-Octant takes a set of (distance, RTT) observations and
       computes their convex hull.  The left edge of the hull (taking
       the x-axis to be RTT) gives QO's estimate of the fastest
       possible travel time = greatest possible distance to the
       target, and the right edge gives its estimate of the _slowest_
       possible travel time = shortest possible distance to the
       target.  The left edge is cut at the 50th percentile of all
       observations, and the right edge at the 75th percentile; beyond
       these cuts, the slopes of the empirical curves of
       PhysicalLimitsOnly are applied.

       Quasi-Octant differs from Octant in two respects: (1) only RTT
       information is considered, not traceroute information; (2) the
       "height" correction for last-hop delays is not included.
    """

    def __init__(self, obs):
        """OBS should be an N-by-2 matrix where the first column is distances
           and the second column is round-trip times."""

        obs = discard_infeasible(obs)
        if obs.shape[0] == 0:
            raise ValueError("not enough feasible observations")

        hull = spatial.ConvexHull(obs)

        v = obs[hull.vertices,:]

        for i in range(v.shape[0]):
            if i > 0 and v[i-1,0] < v[i,0]:
                break

        upper = v[:i,:]
        lower = v[i-1:,:]
        upper = upper[upper[:,0].argsort()]
        lower = lower[lower[:,0].argsort()]

        upper_cut = np.percentile(obs, 50, axis=0)
        upper_cut[1] = _interp_segments(upper, upper_cut[0])

        lower_cut = np.percentile(obs, 75, axis=0)
        lower_cut[1] = _interp_segments(lower, lower_cut[0])

        def extrapolate(point, slope, intercept):
            return np.array([intercept, point[1] + slope*(intercept-point[0])])

        upper_adjusted = np.vstack([
            upper[upper[:,0] < upper_cut[0]],
            upper_cut,
            extrapolate(upper_cut, 1/55, 30000)
        ])
        lower_adjusted = np.vstack([
            lower[lower[:,0] < lower_cut[0]],
            lower_cut,
            extrapolate(lower_cut, 1/100, 30000)
        ])

        self._curve = {
            'max': _PolyLine(lower),
            'min': _PolyLine(upper)
        }
