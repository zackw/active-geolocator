"""ageo.ranging - active geolocation library: calibration curves.

A calibration curve models the relationship between distance and
round-trip time.

"""

import collections
from functools import partial
import math
import warnings

import numpy as np
from scipy import optimize, spatial

import sys

# half of the equatorial circumference of the Earth, in meters
# it is impossible for the target to be farther away than this
DISTANCE_LIMIT = 20037508

class _Line(collections.namedtuple("__Line", ("m", "b"))):
    def __call__(self, x):
        return self.m * x + self.b

class _Cubic(collections.namedtuple("__Cubic", ("a", "b", "c", "d"))):
    def __call__(self, x):
        a, b, c, d = self
        return ((a*x + b)*x + c)*x + d

class _ScaledCubic(collections.namedtuple(
        "__ScaledCubic",
        ("a", "b", "c", "d", "xm", "ym", "rxr", "yr"))):
    def __call__(self, x):
        a, b, c, d, xm, ym, rxr, yr = self
        x = (x - xm)*rxr
        return (((a*x + b)*x + c)*x + d)*yr + ym

def _interp_segments(segs, x):
    """Interpolate or extrapolate the polyline defined by SEGS at the
       x-coordinate X.  Returns the corresponding y-coordinate."""
    i = np.searchsorted(segs[:,0], x)
    if i == segs.shape[0]:
        assert x > segs[-1,0]
        x1, y1 = segs[-2,:]
        x2, y2 = segs[-1,:]
        case = '>'
    elif x == segs[i,0]:
        return segs[i,1]
    elif i == 0:
        assert x < segs[-1,0]
        x1, y1 = segs[0,:]
        x2, y2 = segs[1,:]
        case = '<'
    else:
        x1, y1 = segs[i-1,:]
        x2, y2 = segs[i,:]
        case = '_'

    if x2 == x1:
        sys.stderr.write("DIV0: case={} i={}/{} x={} x1={} x2={} y1={} y2={}\n"
                         .format(case, i, segs.shape, x, x1, x2, y1, y2))
        x2 += 0.000001

    return y1 + (y2-y1)*(x-x1)/(x2-x1)

class _PolyLine:
    def __init__(self, points):
        self._points = points[points[:,0].argsort()]

    def __call__(self, x):
        return _interp_segments(self._points, x)

class MinimizationFailedWarning(UserWarning):
    def __init__(self, label, optresult):
        UserWarning.__init__(
            self,
            label + ": minimization failed: " + optresult.message)
        self.details = optresult

def warn_if_minimization_failed(label, optresult):
    if not optresult.success:
        warnings.warn(
            MinimizationFailedWarning(label, optresult),
            stacklevel=2)

class Calibration:
    """Abstract base class."""

    def distance_range(self, rtts):
        """Given a vector of round-trip-time measurements RTTS, in
        milliseconds, compute and return a pair (min, max) which are
        the minimum and maximum plausible distance, in meters over the
        surface of the Earth, from the reference point to the target.
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

    # The slopes of these lines are meters per millisecond, and
    # they're all half what you'd expect from the above discussion
    # because the /2 to convert RTT to OWTT has been baked in.
    _CURVES = {
        'empirical': {
            'max': _Line(76.5 * 1000, 0),
            'min': _Line(55   * 1000, -55 * 55 * 1000),
        },
        'physical':  {
            'max': _Line(100 * 1000, 0),
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
        obs[:,1] * (100 * 1000)       >= obs[:,0],
        (obs[:,1] - 55) * (55 * 1000) <= obs[:,0]
    )
    fobs = obs[feasible,:]
    return fobs[np.lexsort((fobs[:,1], fobs[:,0])),:]

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

        # Also discard all observations at distance 0, CBG can't make
        # constructive use of them.
        obs = obs[obs[:,0] > 0, :]

        # Split the feasible observations into bins, and take the
        # minimum round-trip time in each bin; only this time can
        # contribute to the solution.  This number of edges carves the
        # planet's half-circumference into roughly 25km intervals.
        # It's 804, not 800, for exact consistency with Spotter (see
        # below).
        xs = obs[:,1] # rtts
        ys = obs[:,0] # distances
        edges = np.linspace(ys[0], ys[-1], 804)
        binds = np.digitize(ys, edges)
        nbins = binds.max()-1

        dists   = np.zeros(nbins)
        minrtts = np.zeros(nbins)
        for i in reversed(range(nbins)):
            dists[i]   = (edges[i] + edges[i+1])/2
            sel = binds == i+1
            if any(sel):
                minrtts[i] = np.amin(xs[sel])
            elif i < nbins-1:
                # optimize.linprog cannot cope with NaN; substitute
                # the next higher observation, which will DTRT
                minrtts[i] = minrtts[i+1]
            else:
                # there _is_ no next higher observation; substitute an
                # artificial value (see below)
                minrtts[i] = 237.16
            assert minrtts[i] > 0

        # The goal is to find m, b that minimize \sum_i (y_i - mx_i - b)
        # while still satisfying y_i \ge mx_i + b for all i.
        # As a linear programming problem, this corresponds to minimizing
        # 1Y - mX - bI where Y = \sum_i y_i, X = \sum_i x_i, I = \sum_i 1.
        # The data constraints take the form 0·1 + x_i·m + 1·b \le y_i.
        #
        # We also impose physical constraints:
        #   m >= 1/100000      200,000 km/s physical speed limit
        #   b >= 0             negative fixed delays don't make sense
        #   b <= min(minrtts)  otherwise the fit will not work
        #
        # Finally, we add an artificial data constraint:
        #   x_limit = 20037508  half of Earth's equatorial circumference
        #   y_limit = 237.16    empirical "slowest plausible" time to
        #                       traverse that distance (see above)
        #
        # This last ensures that the fit will not select a data point from
        # a satellite link as a defining point for the line.

        coef = np.array([np.sum(minrtts), -np.sum(dists), -len(dists)])
        cx = np.append(dists, DISTANCE_LIMIT)
        cy = np.append(minrtts, 237.16)
        constr_A = np.column_stack((
            np.zeros_like(cx), cx, np.ones_like(cx)
        ))
        constr_B = np.column_stack((cy,))
        bounds = [(1,1), (1/100000, None), (0, np.amin(cy))]

        self.cx = cx
        self.cy = cy
        self.coef = coef

        fit = optimize.linprog(coef,
                               A_ub=constr_A,
                               b_ub=constr_B,
                               bounds=bounds)
        warn_if_minimization_failed("CBG", fit)
        if fit.success:
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
        else:
            self.fit = fit

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

        if len(obs.shape) != 2 or obs.shape[1] != 2:
            raise ValueError(
                "improperly shaped observations - must be (:,2), not {!r}"
                .format(obs.shape))

        obs = discard_infeasible(obs)
        if obs.shape[0] == 0:
            raise ValueError("not enough feasible observations")

        # swap columns, since we want to end up with time predicting distance
        obs[:,(0,1)] = obs[:,(1,0)]

        # QbB: do the actual hull computation after scaling to the unit cube
        # this reduces the odds of precision errors
        hull = spatial.ConvexHull(obs, qhull_options="QbB")

        v = obs[hull.vertices,:]
        nroll = v.shape[0]
        while v[0,0] == v[1,0]:
            v = np.roll(v, -1, axis=0)
            nroll -= 1
            if nroll == 0:
                raise ValueError("all hull points have same x-coord? v={!r}"
                                 .format(v))

        if v[0,0] > v[1,0]:
            for i in range(v.shape[0]):
                if i > 1 and v[i-1,0] < v[i,0]:
                    break
        else:
            for i in range(v.shape[0]):
                if i > 1 and v[i-1,0] > v[i,0]:
                    break

        upper = v[:i,:]
        lower = v[i-1:,:]
        if upper.shape[0] <= 1 or lower.shape[0] <= 1:
            raise ValueError("hull split inappropriately - i={!r} v={!r}"
                             .format(i, v))

        _, uind = np.unique(upper[:,0], return_index=True)
        _, lind = np.unique(lower[:,0], return_index=True)
        upper = upper[uind,:]
        lower = lower[lind,:]

        if upper.shape[0] <= 1 or lower.shape[0] <= 1:
            raise ValueError("bad spline after dropping redundant x-coords - "
                             "i={!r} v={!r} lower={!r} upper={!r}"
                             .format(i, v, lower, upper))

        upper_cut = np.percentile(obs, 50, axis=0)
        upper_cut[1] = _interp_segments(upper, upper_cut[0])

        lower_cut = np.percentile(obs, 75, axis=0)
        lower_cut[1] = _interp_segments(lower, lower_cut[0])

        def extrapolate(point, slope, intercept):
            return np.array([intercept, point[1] + slope*(intercept-point[0])])

        upper_adjusted = np.vstack([
            upper[upper[:,0] < upper_cut[0]],
            upper_cut,
            extrapolate(upper_cut, 55*1000, 1000)
        ])
        lower_adjusted = np.vstack([
            lower[lower[:,0] < lower_cut[0]],
            lower_cut,
            extrapolate(lower_cut, 100*1000, 1000)
        ])

        self._curve = {
            'max': _PolyLine(upper),
            'min': _PolyLine(lower)
        }

class Spotter(Calibration):
    """An algorithm derived from "Spotter: A Model-Based Active Geolocation
    Service", INFOCOM 2011.

    Spotter computes the mean and standard deviation of distance as a
    function of delay, and fits "a polynomial" to each curve.  Then,
    given a single delay D, it predicts that the distance to the
    target will be distributed as a Gaussian with mean and standard
    deviation given by the fitted curves.  The paper does not specify
    the degree of the polynomial, the exact curve-fitting procedure,
    nor what to do with a _group_ of observed delays, so we have
    filled in these gaps as follows.  We use cubic polynomials, fit by
    least squares, with the additional constraints that each curve
    must be increasing everywhere and must go through the minimum
    observation.  Given a group of observed delays, we use the first
    quartile as the representative delay; this is because the delay
    distribution is unbounded upward.

    Finally, to satisfy the interface of Calibration, distance_range()
    uses 5*sigma as the outer limits of plausibility in both
    directions.
    """

    def __init__(self, obs):
        """OBS should be an N-by-2 matrix where the first column is distances
           and the second column is round-trip times."""

        # The default number of edges carves the planet's
        # half-circumference into (roughly) 25km bins.
        def windowed_moments(xs, ys, nknots=800):
            edges = np.linspace(xs[0], xs[-1], nknots + 4)
            knots = edges[2:-2]
            mu    = np.zeros_like(knots)
            sigma = np.zeros_like(knots)
            for i, (lo, hi) in enumerate(zip(edges[:-4], edges[4:])):
                ind = (xs >= lo) & (xs <= hi)
                blk = ys[ind]
                if len(blk) > 0:
                    mu[i] = np.mean(blk)
                    sigma[i] = np.std(blk)
                else:
                    mu[i] = math.nan
                    sigma[i] = math.nan

            return (knots, mu, sigma)

        def fit_cubic_constrained(xs, ys):

            # The function to be minimized.  This is the least-squares
            # error of a cubic polynomial with coefficients COEF,
            # applied to data points (xs, ys).
            def lse_cubic(coef, xs, ys):
                zs = _Cubic(*coef)(xs)
                resid = zs - ys
                return resid.dot(resid)

            # Any smooth function is increasing everywhere if and only if
            # its derivative is positive everywhere.  The derivative of a
            # cubic ax^3 + bx^2 + cx + d is a quadratic (3a)x^2 + (2b)x + c,
            # and a quadratic Ax^2 + Bx + C is positive everywhere when
            # A > 0 and B^2 - 4AC < 0 (that is, the parabola is concave
            # upward and does not touch the x-axis).  minimize() wants
            # inequality constraints of the form g(coef) >= 0.
            def constr_concave_up(coef):
                return coef[0]*3
            def constr_det_negative(coef):
                A = coef[0]*3
                B = coef[1]*2
                C = coef[2]
                return -(B*B - 4*A*C)

            # Scale the data to the unit square in both directions, to
            # avoid "loss of precision" errors.
            ymin   = ys.min()
            ymax   = ys.max()
            yrang  = ymax - ymin
            if yrang == 0 or math.isnan(yrang):
                raise RuntimeError("wtf")
            ryrang = 1/yrang

            xmin   = xs.min()
            xmax   = xs.max()
            xrang  = xmax - xmin
            if yrang == 0 or math.isnan(yrang):
                raise RuntimeError("wtf #2")
            rxrang = 1/xrang

            xss = (xs-xmin) * rxrang
            yss = (ys-ymin) * ryrang

            result = optimize.minimize(
                fun    = lse_cubic,
                args   = (xss, yss),
                # initial linear approximation
                x0     = np.array([0, 0, 1, 0]),
                # require cubic to be increasing everywhere (see above)
                constraints = [
                    { 'type': 'ineq', 'fun': constr_concave_up },
                    { 'type': 'ineq', 'fun': constr_det_negative }
                ],
                # require y-intercept to be nonnegative
                bounds = [
                    (None, None), (None, None), (None, None), (0, None)
                ],
                # scipy 0.17.1's default method for constrained
                # optimization; pinned for reproducibility
                method = 'SLSQP',
                # because of the constraints, the solver may need
                # extra iterations
                options = { 'maxiter': 10000 }
            )
            warn_if_minimization_failed("Spotter", result)
            return _ScaledCubic(*result.x, xmin, ymin, rxrang, yrang)

        obs = discard_infeasible(obs)
        if obs.shape[0] == 0:
            raise ValueError("not enough feasible observations")

        X, M, S = windowed_moments(obs[:,1], obs[:,0])
        self._mu = fit_cubic_constrained(X, M)
        self._sigma = fit_cubic_constrained(X, S)

    def distance_range(self, rtts):
        med_rtt = np.percentile(rtts, .25)
        mu = self._mu(med_rtt)
        s5 = self._sigma(med_rtt) * 5
        return (max(mu - s5, 0), max(mu + s5, 0))
