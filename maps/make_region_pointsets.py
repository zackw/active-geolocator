#! /usr/bin/python3

"""Construct a set of "region" matrices from the baseline matrix (see
make_geog_baseline.py), plus a shapefile that divides it into regions,
plus an "override" list that can adjust the categorization.
"""

import argparse
import collections
import csv

import functools
import math
import os
import re
import sys
import time

import fiona
import fiona.crs
import pyproj
import scipy.sparse
import shapely
import shapely.geometry
import shapely.ops
import shapely.prepared
import tables

from fiona.errors import FionaValueError
from argparse import ArgumentTypeError

_time_0 = time.monotonic()
def progress(message, *args):
    global _time_0
    sys.stderr.write(
        ("{}: " + message + "\n").format(
            datetime.timedelta(seconds = time.monotonic() - _time_0),
            *args))

WGS84proj = pyproj.Proj(proj="latlong", datum="WGS84", ellps="WGS84")

def fuzz_to_degrees(fuzz_m):
    # WGS84 reference ellipsoid: see page 3-1 (physical page 34) of
    # http://earth-info.nga.mil/GandG/publications/tr8350.2/wgs84fin.pdf
    # A and F are exact, A is in meters.
    A = 6378137         # equatorial semi-axis
    F = 1/298.257223563 # flattening
    B = A * (1-F)       # polar semi-axis

    return (fuzz_m * 180) / ((A+B) * math.pi / 2)

_not_ok_in_filename_re = re.compile(r"[^a-z0-9_]")
_cleanup_filename_re = re.compile(r"^_*(.+?)_*$")
def to_h5filename(d, s):
    rv = _cleanup_filename_re.sub(
        r"\1.h5",
        _not_ok_in_filename_re.sub("_", s.casefold()))
    if rv == ".h5":
        rv = "noname.h5"
    return os.path.join(d, rv)

# The region-matrix format is the format used by ageo.Location, _not_ the
# format used by ageo.Map.
class RegionRowOnDisk(tables.IsDescription):
    """The row format of the pytables table used to save regions
       on disk.  See GeographicMatrix.carve_region; also see
       ageo.Location.save and ageo.Location.load."""
    grid_x    = tables.UInt32Col()
    grid_y    = tables.UInt32Col()
    longitude = tables.Float64Col()
    latitude  = tables.Float64Col()
    prob_mass = tables.Float32Col()

class GeographicMatrix:
    def __init__(self, basefile):
        M = basefile.root.baseline

        if M.shape[0] == len(M.attrs.longitudes):
            matrix = scipy.sparse.csr_matrix(M)
        elif M.shape[1] == len(M.attrs.longitudes):
            matrix = scipy.sparse.csr_matrix(M).T
        else:
            raise RuntimeError(
                "mapfile matrix shape {!r} is inconsistent with "
                "lon/lat vectors ({},{})"
                .format(M.shape,
                        len(M.attrs.longitudes),
                        len(M.attrs.latitudes)))

        self.points      = list(zip(*scipy.sparse.find(matrix)))
        self.resolution  = M.attrs.resolution
        self.fuzz        = M.attrs.fuzz
        self.fuzz_deg    = fuzz_to_degrees(self.fuzz)
        self.north       = M.attrs.north
        self.south       = M.attrs.south
        self.east        = M.attrs.east
        self.west        = M.attrs.west
        self.lon_spacing = M.attrs.lon_spacing
        self.lat_spacing = M.attrs.lat_spacing
        self.longitudes  = M.attrs.longitudes
        self.latitudes   = M.attrs.latitudes

    def write_pointset(self, odir, fname, rows):
        with tables.open_file(to_h5filename(odir, fname), "w") as f:
            t = f.create_table(f.root, "location",
                               RegionRowOnDisk, "location")
            t.attrs.resolution  = self.resolution
            t.attrs.fuzz        = self.fuzz
            t.attrs.north       = self.north
            t.attrs.south       = self.south
            t.attrs.east        = self.east
            t.attrs.west        = self.west
            t.attrs.lon_spacing = self.lon_spacing
            t.attrs.lat_spacing = self.lat_spacing
            t.attrs.lon_count   = len(self.longitudes)
            t.attrs.lat_count   = len(self.latitudes)

            cur = t.row
            for i, j, lon, lat, pmass in rows:
                cur['grid_x']    = i
                cur['grid_y']    = j
                cur['longitude'] = lon
                cur['latitude']  = lat
                cur['prob_mass'] = pmass
                cur.append()

            t.flush()

    def carve_region(self, projection, region_name, region_geometry, odir):

        boundary = shapely.prepared.prep(
            shapely.ops.unary_union([
                shapely.ops.transform(
                    functools.partial(
                        pyproj.transform, projection, WGS84proj),
                    shapely.geometry.shape(geom))
                for geom in region_geometry
            ]).buffer(self.fuzz_deg))

        within = []
        without = []
        for i, j, v in self.points:
            lon = self.longitudes[i]
            lat = self.latitudes[j]
            if boundary.contains(shapely.geometry.Point(lon, lat)):
                within.append((i, j, lon, lat, v))
            else:
                without.append((i, j, v))

        self.write_pointset(odir, region_name, within)
        self.points = without

    def write_remaining(self, odir):
        self.write_pointset(odir, 'other', (
            (i, j, self.longitudes[i], self.latitudes[j], v)
            for i, j, v in self.points
        ))

class RegionLabeler:
    def __init__(self, override):
        rd = csv.DictReader(override)
        if rd.fieldnames != ["cc2", "name", "region"]:
            raise ValueError("region override file categories not as expected")
        by_cc2 = {}
        by_name = {}
        for row in rd:
            by_cc2[row['cc2']] = row['region']
            by_name[row['name']] = row['region']

        self.by_cc2 = by_cc2
        self.by_name = by_name

    def __getitem__(self, key):
        props = key['properties']
        cc2   = props['iso_a2'].casefold()
        name  = props['name']
        rgn   = props['subregion']
        if cc2 in self.by_cc2:
            return self.by_cc2[cc2]
        if name in self.by_name:
            return self.by_name[name]
        return rgn

def main():
    def shapefile(fname):
        if not fname:
            raise ArgumentTypeError("shapefile name cannot be empty")
        try:
            return fiona.open(fname, 'r')
        except FionaValueError as e:
            raise ArgumentTypeError(
                "%s: not a shapefile (%s)" % (fname, str(e))) from e
        except OSError as e:
            raise ArgumentTypeError(
                "%s: cannot open (%s)" % (e.filename or fname,
                                          e.strerror)) from e

    def tablesfile(fname):
        if not fname:
            raise ArgumentTypeError("matrix file name cannot be empty")
        try:
            return tables.open_file(fname, 'r')
        except tables.exceptions.HDF5ExtError as e:
            raise ArgumentTypeError(
                "%s: not a pytables HDF (%s)" % (fname, str(e))) from e
        except OSError as e:
            raise ArgumentTypeError(
                "%s: cannot open (%s)" % (e.filename or fname,
                                          e.strerror)) from e

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('matrix', type=tablesfile)
    ap.add_argument('shapefile', type=shapefile)
    ap.add_argument('override', type=argparse.FileType('r'))
    ap.add_argument('odir')

    args = ap.parse_args()

    progress("preparation...")

    os.makedirs(args.odir, exist_ok=True)
    world = GeographicMatrix(args.matrix)
    region_labels = RegionLabeler(args.override)
    regions = collections.defaultdict(list)
    projection = pyproj.Proj(args.shapefile.crs)
    for rgn in args.shapefile:
        regions[region_labels[rgn]].append(rgn['geometry'])

    for label, geoms in regions.items():
        progress(label + "...")
        world.carve_region(projection, label, geoms, args.odir)

    progress("other...\n")
    world.write_remaining(args.odir)

    progress("done.")

main()
