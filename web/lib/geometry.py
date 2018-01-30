"""Geometric calculations on the surface of the Earth."""

# Important note: pyproj consistently takes coordinates in lon/lat
# order and distances in meters.  Therefore, this library also
# consistently uses lon/lat order and meters.

import functools
import random
import numpy as np
import pyproj
from shapely.geometry import Point, Polygon, box as Box
from shapely.ops import transform as sh_transform
from shapely.prepared import prep as sh_prep

wgs_proj  = pyproj.Proj("+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs")

def DiskOnGlobe(x, y, radius):
    """Return a shapely polygon which is a circle centered at longitude X,
       latitude Y, with radius RADIUS (meters), projected onto the
       surface of the Earth.
    """

    # Make sure the radius is at least 1km to prevent underflow.
    radius = max(radius, 1000)

    # If the radius is too close to half the circumference of the
    # Earth, the projection operation below will produce an invalid
    # polygon.  We don't get much use out of a region that includes
    # the whole planet but for a tiny disk (which will probably be
    # somewhere in the ocean anyway) so just give up and say that the
    # region is the entire planet.
    if radius > 19975000:
        return Box(-180, -90, 180, 90)

    # To find all points on the Earth within a certain distance of
    # a reference latitude and longitude, back-project onto the
    # Earth from an azimuthal-equidistant map with its zero point
    # at the reference latitude and longitude.
    aeqd = pyproj.Proj(proj='aeqd', ellps='WGS84', datum='WGS84', lat_0=y, lon_0=x)

    disk = sh_transform(
        functools.partial(pyproj.transform, aeqd, wgs_proj),
        Point(0, 0).buffer(radius, resolution=64))

    # Two special cases must be manually dealt with.  First, if any
    # side of the "disk" (really a many-sided polygon) crosses the
    # coordinate singularity at longitude ±180, we must replace that
    # side with a diversion to either the north or south pole
    # (whichever is closer) to ensure the diskstill encloses all of
    # the area it should.
    boundary = np.array(disk.boundary)
    i = 0
    while i < boundary.shape[0] - 1:
        if abs(boundary[i+1,0] - boundary[i,0]) > 180:
            pole = -90 if boundary[i,1] < 0 else 90
            west = -180 if boundary[i,0] < 0 else 180
            east = 180 if boundary[i,0] < 0 else -180

            boundary = np.insert(boundary, i+1, [
                [west, boundary[i,1]],
                [west, pole],
                [east, pole],
                [east, boundary[i+1,1]]
            ], axis=0)
            i += 5
        else:
            i += 1
    # If there were two sides that crossed the singularity and they
    # were both on the same side of the equator, the excursions will
    # coincide and shapely will be unhappy.  buffer(0) corrects this.
    disk = Polygon(boundary).buffer(0)

    # Second, if the radius is very large, the projected disk might
    # enclose the complement of the region that it ought to enclose.
    # If it doesn't contain the reference point, we must subtract it
    # from the entire map.
    origin = Point(x, y)
    if not disk.contains(origin):
        disk = Box(-180, -90, 180, 90).difference(disk)

    assert disk.is_valid
    assert disk.contains(origin)

    return disk

def intersect_disks_on_globe(xs, ys, rads):
    """Return the intersection of many DiskOnGlobe objects, constructed
       from three arrays of longitudes (xs), latitudes (ys), and radii (rads).
    """

    assert len(xs) == len(ys) == len(rads)

    result = Box(-180, -90, 180, 90)
    for x, y, rad in zip(xs, ys, rads):
        d = DiskOnGlobe(x, y, rad)
        result = result.intersection(d)

    return result

def sample_tuples_near_shape(shape, tuples, *, n=100, neighbor_dist=100):
    """Return a random subsample of at most N of the data tuples in
       TUPLES.  Each tuple must have 'lat' and 'lon' properties.  No
       two selected tuples will be closer to each other than
       NEIGHBOR_DIST kilometers. Tuples whose lat and lon lie within
       SHAPE will be selected first; if there are not enough, the
       shape will be progressively enlarged until either enough have
       been found, or we run out.
    """
    population = []
    covered = Polygon()
    neighbor_dist *= 1000
    for t in tuples:
        k = (t.lon, t.lat)
        if not covered.contains(Point(*k)):
            population.append(t)
            covered = covered.union(DiskOnGlobe(*k, neighbor_dist))

    if len(population) <= n:
        return population

    subsample = []
    while True:
        fshape = sh_prep(shape)
        hits = []
        rest = []
        for t in population:
            if fshape.contains(Point(t.lon, t.lat)):
                hits.append(t)
            else:
                rest.append(t)

        want = n - len(subsample)
        have = len(hits)
        if have == want:
            subsample.extend(hits)
            return subsample
        elif have > want:
            subsample.extend(random.sample(hits, want))
            return subsample
        else:
            subsample.extend(hits)
            if not rest:
                return subsample
            population = rest
            shape = shape.buffer(5)

WGS84geod = pyproj.Geod(ellps='WGS84')

def sample_more_tuples_into(sample_out, n, neighbor_dist, tuples):
    tuples = list(tuples)
    random.shuffle(tuples)
    for cand in tuples:
        if len(sample_out) >= n:
            break
        for other in sample_out:
            _, _, dist = WGS84geod.inv(other.lon, other.lat, cand.lon, cand.lat)
            if dist < neighbor_dist:
                break
        else:
            sample_out.append(cand)
