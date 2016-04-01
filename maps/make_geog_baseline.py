#! /usr/bin/python3

"""Construct a geographic "baseline" matrix from a collection of
shapefiles (assumed to be maps of the Earth).  Shapefiles can be
either "positive" or "negative".  The baseline matrix represents a
grid laid over the Earth; points inside the union of positive geometry
and not inside the union of negative geometry will have value 1,
points well within the complementary space will have value 0, and
points right on the edge (as determined by the "fuzz" argument) will
have intermediate values.  The grid will have points exactly on all
four edges, except when the westmost and eastmost meridians coincide,
in which case the eastmost meridian will not be included.
"""

import argparse
import functools
import math
import os
import sys

import fiona
import fiona.crs
import numpy as np
import pyproj
import shapely
import shapely.geometry
import shapely.ops
import shapely.prepared
import tables

from fiona.errors import FionaValueError
from argparse import ArgumentTypeError

class GeographicMatrix:
    def __init__(self, args):
        # WGS84 reference ellipsoid: see page 3-1 (physical page 34) of
        # http://earth-info.nga.mil/GandG/publications/tr8350.2/wgs84fin.pdf
        # A and F are exact, A is in meters.
        A = 6378137         # equatorial semi-axis
        F = 1/298.257223563 # flattening
        B = A * (1-F)       # polar semi-axis

        lon_spacing = (args.resolution * 180) / (A * math.pi)
        lat_spacing = (args.resolution * 180) / (B * math.pi)

        fuzz_degrees = (args.fuzz * 180) / ((A+B) * math.pi / 2)

        # To avoid rounding errors, precalculate the number of grid rows
        # and columns so we can use linspace() rather than arange().
        n_lon = int(math.floor((args.east - args.west) / lon_spacing))
        n_lat = int(math.floor((args.north - args.south) / lat_spacing))

        south = args.south
        north = south + n_lat * lat_spacing

        west = args.west
        east = west + n_lon * lon_spacing

        if (east - west) - 360.0 <= 1e-6:
            sys.stderr.write("East-west wraparound, shrinking grid.\n")
            n_lon -= 1
            east -= lon_spacing

        sys.stderr.write(
            "Matrix dimensions {}x{}\n"
            "Longitude spacing {:.9f};  eastmost grid error {:.9f}\n"
            " Latitude spacing {:.9f}; northmost grid error {:.9f}\n"
            .format(n_lon, n_lat,
                    lon_spacing, args.east - east,
                    lat_spacing, args.north - north))

        lon = np.linspace(west, east, n_lon)
        lat = np.linspace(south, north, n_lat)
        mtx = np.zeros((n_lat, n_lon), dtype=np.float32)

        # We save all the (adjusted) parameters from the command line
        # so we can record them as metadata in the output file later.
        self.resolution  = args.resolution
        self.fuzz        = args.fuzz
        self.north       = north
        self.south       = south
        self.west        = west
        self.east        = east

        self.lon_spacing = lon_spacing
        self.lat_spacing = lat_spacing

        # These are actually needed by process_geometry.
        self.lon         = lon
        self.lat         = lat
        self.mtx         = mtx
        self.fuzz_deg    = fuzz_degrees

        self.geoms       = []

    def write_to(self, fname):
        with tables.open_file(fname, 'w') as f:
            M = f.create_carray(f.root, 'baseline',
                                tables.Atom.from_dtype(self.mtx.dtype),
                                self.mtx.shape,
                                filters=tables.Filters(complevel=6,
                                                       complib='zlib'))
            M[:,:] = self.mtx[:,:]

            M.attrs.resolution  = self.resolution
            M.attrs.fuzz        = self.fuzz
            M.attrs.north       = self.north
            M.attrs.south       = self.south
            M.attrs.east        = self.east
            M.attrs.west        = self.west
            M.attrs.lon_spacing = self.lon_spacing
            M.attrs.lat_spacing = self.lat_spacing
            M.attrs.longitudes  = self.lon
            M.attrs.latitudes   = self.lat

            # If you don't manually encode the strings, or if you use
            # normal Python arrays, you get pickle barf in the file
            # instead of a proper HDF vector-of-strings.  I could
            # combine these attributes into a record array, but this
            # is simpler.
            M.attrs.geom_names  = np.array([ g[1].encode('utf-8')
                                             for g in self.geoms ])
            M.attrs.geom_senses = np.array([ g[0].encode('utf-8')
                                             for g in self.geoms ])

            # If you don't set a TITLE on M, the file is slightly out of
            # spec and R's hdf5load() will segfault(!)
            M.attrs.TITLE = "baseline"

    def process_geometry(self, sense, geom):
        assert sense == '+' or sense == '-'
        name = os.path.splitext(os.path.basename(geom.name))[0]
        self.geoms.append((sense, name))

        sys.stderr.write("Processing {} (crs={})...\n"
                         .format(name, fiona.crs.to_string(geom.crs)))

        # unary_union does not accept generators
        inner_boundary = shapely.ops.unary_union([
            shapely.geometry.shape(g['geometry'])
            for g in geom])

        # It is (marginally) more efficient to transform the inner
        # boundary to the desired "raw WGS84 lat/long" coordinate
        # system after combining it into one shape.
        inner_boundary = shapely.ops.transform(
            functools.partial(
                pyproj.transform,
                pyproj.Proj(geom.crs),
                pyproj.Proj(proj="latlong", datum="WGS84", ellps="WGS84")),
            inner_boundary)

        outer_boundary = inner_boundary.buffer(self.fuzz_deg)

        inner_boundary_p = shapely.prepared.prep(inner_boundary)
        outer_boundary_p = shapely.prepared.prep(outer_boundary)

        for i, x in enumerate(self.lon):
            for j, y in enumerate(self.lat):
                pt = shapely.geometry.Point(x, y)
                if inner_boundary_p.contains(pt):
                    val = 1
                elif not outer_boundary_p.contains(pt):
                    val = 0
                else:
                    # in between
                    val = 1 - min(1, max(0,
                        pt.distance(inner_boundary)/self.fuzz_deg))

                if sense == '+':
                    self.mtx[j,i] = min(1, self.mtx[j,i] + val)
                else:
                    self.mtx[j,i] = max(0, self.mtx[j,i] - val)

def process(args):
    matrix = GeographicMatrix(args)

    for sense, geom in args.shapefile:
        matrix.process_geometry(sense, geom)

    matrix.write_to(args.output)

def main():

    def shapefile(fname):
        if not fname:
            raise ArgumentTypeError("shapefile name cannot be empty")
        if fname[0] == '+':
            tag = '+'
            fname = fname[1:]
        else:
            tag = '?'
        try:
            return (tag, fiona.open(fname, 'r'))
        except FionaValueError as e:
            raise ArgumentTypeError(
                "%s: not a shapefile (%s)" % (fname, str(e)))
        except OSError as e:
            raise ArgumentTypeError(
                "%s: cannot open (%s)" % (fname, e.strerror))

    def fixup_shapefile_arg(n, arg):
        if arg[0] != '?': return arg
        if n == 0: return ('+', arg[1])
        return ('-', arg[1])

    ap = argparse.ArgumentParser(description=__doc__)

    ap.add_argument('-s', '--south', type=float, default=-60,
                    help='Southmost latitude for the output matrix. '
                    'The default is -60, which is south of all major '
                    'landmasses except Antarctica.')
    ap.add_argument('-n', '--north', type=float, default=84,
                    help='Northmost latitude for the output matrix. '
                    'The default is 84, which is north of all major '
                    'landmasses.')
    ap.add_argument('-w', '--west', type=float, default=-180,
                    help='Westmost longitude for the output matrix. '
                    'The default is -180.')
    ap.add_argument('-e', '--east', type=float, default=180,
                    help='Eastmost longitude for the output matrix. '
                    'The default is 180.')

    ap.add_argument('-r', '--resolution', type=float, default=5000,
                    help='Grid resolution of the matrix, in meters at '
                    'the equator.  The matrix is NOT projected, so its '
                    'east-west resolution closer to the poles will be finer.'
                    'The default is 5km.')
    ap.add_argument('-f', '--fuzz', type=float, default=None,
                    help='Fuzz radius.  Points outside the positive geometry '
                    'by less than this distance will have values between 0 and '
                    '1. The default is 1.5 times the resolution.')

    ap.add_argument('-o', '--output', default=None,
                    help='Name of output file.  The default is to use the '
                    'name of the first input shapefile, with a ".hdf" suffix.')

    ap.add_argument('shapefile', type=shapefile, nargs='+',
                    help='Shapefiles to process.  The first shapefile in the '
                    'list is always considered positive geometry; subsequent '
                    'shapefiles are negative geometry unless specified with a '
                    'leading "+" on the filename.')

    args = ap.parse_args()
    if not args.shapefile:
        ap.error("at least one shapefile must be specified")

    if not (-180 <= args.west < args.east <= 180):
        ap.error("improper values for --west/--east")

    if not (-90 <= args.south < args.north < 90):
        ap.error("improper values for --south/--north")

    args.shapefile = [fixup_shapefile_arg(n, arg)
                      for n, arg in enumerate(args.shapefile)]

    if args.output is None:
        args.output = os.path.splitext(args.shapefile[0][1].name)[0] + '.hdf'
    if args.fuzz is None:
        args.fuzz = args.resolution * 1.5

    process(args)

main()
