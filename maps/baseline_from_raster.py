#! /usr/bin/python3

"""Construct a geographic "baseline" matrix from a GDAL-readable
raster file (assumed to be a map of the Earth), resampling as
necessary.  The value assigned to each matrix point is logarithmically
proportional to the value in each raster cell, and normalized to [0, 1].
Cells with missing data are assigned value 0.
"""

import argparse
import math
import os
import sys

import numpy as np
import tables

import rasterio
import rasterio.warp

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


        # We save all the (adjusted) parameters from the command line
        # so we can record them as metadata in the output file later.
        self.raster_name = args.raster.name
        self.resolution  = args.resolution
        self.fuzz        = args.fuzz
        self.fuzz_deg    = fuzz_degrees
        self.north       = north
        self.south       = south
        self.west        = west
        self.east        = east

        self.lon_spacing = lon_spacing
        self.lat_spacing = lat_spacing

        self.lon         = lon
        self.lat         = lat

        mtx = np.empty((n_lat, n_lon), dtype=np.float32)
        rasterio.warp.reproject(
            rasterio.band(args.raster, 1),
            mtx,
            src_transform = args.raster.affine,
            src_crs       = args.raster.crs,
            dst_crs       = rasterio.crs.CRS({
                'proj': 'longlat', 'ellps': 'WGS84', 'datum': 'WGS84',
                'no_defs': True}),
            dst_transform = rasterio.transform.from_bounds(
                west, south, east, north,
                n_lon, n_lat),
            dst_nodata    = 0,
            resampling    = rasterio.warp.Resampling.cubic)

        mtx[mtx < 0] = 0
        mtx  = np.log1p(mtx)
        mtx -= np.amin(mtx)
        mtx /= np.amax(mtx)

        # For no reason that I can find, 'mtx' will come out upside down.
        self.mtx = np.flipud(mtx)

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
            M.attrs.raster_name = self.raster_name

            # If you don't set a TITLE on M, the file is slightly out of
            # spec and R's hdf5load() will segfault(!)
            M.attrs.TITLE = "baseline"



def main():
    def rasterfile(fname):
        if not fname:
            raise ArgumentTypeError("shapefile name cannot be empty")
        try:
            ras = rasterio.open(fname)
        except Exception as e:
            raise ArgumentTypeError(e.message)
        if ras.count != 1:
            raise ArgumentTypeError("don't know what to do with a multi-raster file")
        return ras

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

    ap.add_argument('-o', '--output', default=None,
                    help='Name of output file.  The default is to use the '
                    'name of the input shapefile, with a ".hdf" suffix.')

    ap.add_argument('raster', type=rasterfile,
                    help='Raster file to process.')

    args = ap.parse_args()

    if not (-180 <= args.west < args.east <= 180):
        ap.error("improper values for --west/--east")

    if not (-90 <= args.south < args.north < 90):
        ap.error("improper values for --south/--north")

    if args.output is None:
        args.output = os.path.splitext(args.raster.name)[0] + '.hdf'

    # fake for downstream processing
    args.fuzz = args.resolution * 1.5

    matrix = GeographicMatrix(args)
    matrix.write_to(args.output)


main()
