"""ageo.ranging - active geolocation library: calibration curves.

A calibration curve models the relationship between distance and
round-trip time.

"""

import collections
from functools import partial

import numpy as np
from scipy import optimize

class _Line(collections.namedtuple("__Line", ("m", "b"))):
    def __call__(self, x):
        return self.m * x + self.b

class Calibration:
    """Abstract base class."""

    def distance_range(rtts):
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
        max_dist = max(0, self._curve['max'](min_rtt))
        min_dist = max(0, self._curve['min'](min_rtt))
        return (min_dist, max_dist)

class PhysicalLimitsOnly(Calibration):
    """This calibration curve considers only physical limits, which can be
    (and are) hardwired in the code, so no data is required to create
    one.  In addition to being usable directly as a maximally
    conservative estimate, it can also be used to prune outliers from
    data to be used for calibration of a more sophisticated curve.

    Network cables (whether optical or electrical) all propagate
    signals at roughly the same speed, 2/3 c = 199,862 kilometers per
    second.  Surface microwave links are faster, but rarely cover
    significant distance, as they require a line of sight.  Satellite
    links have to go out to geosynchronous orbit (42,160 km, or 3.3
    times the diameter of Earth) and back, so they're very slow
    regardless of distance covered.  All told, 200,000 km/s is a
    reasonable choice for a _theoretical_ propagation speed limit.

    Considering also processing and queueing delays, several papers
    take 4/9 c = 133,241 km/s as the maximum "speed of internet"; RIPE
    data indicates that this can be too conservative, but not by much;
    a maximum speed of 153,000 km/s (0.5104 c) seems to be "safe".

    There is no _lower_ limit to propagation speed in principle---
    queueing and processing delays can be arbitrarily large.  (RFC
    1122 specifies a minimum timeout of 100 seconds for established
    connections.)  However, beyond some outer limit, time measurements
    are no longer providing meaningful information about the distance.
    Empirically, based on RIPE data again, treating the _slowest_
    reasonable propagation speed as 110,000 km/s (0.3669 c) plus
    allowing up to 55ms delay at distance 0 appears to do a good job
    of excluding outliers.

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
            'min': _Line(55, -55),
        },
        'physical':  {
            'max': _Line(100, 0),
            'min': _Line(0, 0)
        }
    }

    def __init__(self, mode='empirical'):
        self._curve = self._CURVES[mode]

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
        """OBS should be a 2-by-N matrix where the first column is distances
        and the second column is round-trip times."""

        # Eliminate redundant observations; for each distance d_i,
        # only the minimum round-trip time min {r_i} can contribute to
        # the solution.  Also, eliminate self-pings (d_i == 0) if any.
        dists = np.unique(obs[:,0])
        while dists[0] < 1:
            dists = dists[1:]

        minrtts = np.zeros_like(dists)
        for i, dist in enumerate(dists):
            thisdist = obs[:,0] == dist
            minrtts[i] = np.amin(obs[thisdist,1])

        # standard sum-of-squares criterion
        def objec(xs, ys, v):
            ds = ys - (v[0]*xs + v[1])
            return sum(ds*ds)

        # Jacobian of the objective function, I hope
        def J_objec(xs, ys, v):
            return np.array([
                2 * sum((v[0]*xs + v[1] - ys) * xs),
                2 * (sum(v[0]*xs - ys) + v[1])
            ])

        # constraint function template: each of these must be nonnegative
        def constr(x, y, v):
            return y - (v[0]*x + v[1])

        # Jacobian of the constraint (doesn't actually depend on y or v)
        def J_constr(x, y, v):
            return np.array([-x, 1])

        fit = optimize.minimize(
            partial(objec, dists, minrtts),
            np.array([1/100, 0]),
            method='SLSQP',
            jac=partial(J_objec, dists, minrtts),
            constraints=[
                { 'type': 'ineq',
                  'fun':  partial(constr, d, r),
                  'jac':  partial(J_constr, d, r) }
                for d, r in zip(dists, minrtts)
            ])
        if not fit.success:
            raise RuntimeError("CBG: failed to find bestline: \n" + str(fit))

        self._curve = {
            'max': _Line(fit.x[0], fit.x[1]),
            'min': _Line(0, 0)
        }
