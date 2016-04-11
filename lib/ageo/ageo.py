"""ageo - active geolocation library: core.
"""

import numpy as np
import scipy.sparse
import tables

__all__ = ('Location', 'Map', 'Observation')

class LocationRowOnDisk(tables.IsDescription):
    """The row format of the pytables table used to save Location objects
       on disk.  See Location.save and Location.load."""
    grid_x    = tables.UInt32Col()
    grid_y    = tables.UInt32Col()
    longitude = tables.Float64Col()
    latitude  = tables.Float64Col()
    prob_mass = tables.Float32Col()

class Location:
    """An estimated location for a host.  This is represented by a
    probability mass function over the surface of the Earth, quantized
    to a cell grid, and stored as a sparse matrix.

    Properties:
      resolution  - Grid resolution, in meters at the equator
      lon_spacing - East-west (longitude) grid resolution, in decimal degrees
      lat_spacing - North-south (latitude) grid resolution, in decimal degrees
      fuzz        - Coastline uncertainty factor, in meters at the equator
      north       - Northernmost latitude covered by the grid
      south       - Southernmost latitude ditto
      east        - Easternmost longitude ditto
      west        - Westernmost longitude ditto
      latitudes   - Vector of latitude values corresponding to grid points
      longitudes  - Vector of longitude values ditto
      probability - Probability mass matrix

    You will normally not construct bare Location objects directly, only
    Map and Observation objects (these are subclasses).  However, any two
    Locations can be _intersected_ to produce a new one.
    """
    def __init__(self, *,
                 resolution, fuzz, lon_spacing, lat_spacing,
                 north, south, east, west,
                 longitudes, latitudes,
                 probability):
        self.resolution  = resolution
        self.fuzz        = fuzz
        self.north       = north
        self.south       = south
        self.east        = east
        self.west        = west
        self.lon_spacing = lon_spacing
        self.lat_spacing = lat_spacing
        self.longitudes  = longitudes
        self.latitudes   = latitudes
        self.probability = probability

    def intersection(self, other):
        if (self.resolution  != other.resolution or
            self.fuzz        != other.fuzz or
            self.north       != other.north or
            self.south       != other.south or
            self.east        != other.east or
            self.west        != other.west or
            self.lon_spacing != other.lon_spacing or
            self.lat_spacing != other.lat_spacing):
            raise ValueError("can't intersect locations with inconsistent grids")

        # Compute P(self AND other).
        M = self.probability * other.probability
        s = M.sum()
        if s:
            M /= s

        return Location(
            resolution  = self.resolution,
            fuzz        = self.fuzz,
            north       = self.north,
            south       = self.south,
            east        = self.east,
            west        = self.west,
            lon_spacing = self.lon_spacing,
            lat_spacing = self.lat_spacing,
            longitudes  = self.longitudes,
            latitudes   = self.latitudes,
            probability = M
        )

    def save(self, fname):
        """Write out this location to an HDF file.
           For compactness, we write only the nonzero entries in a
           pytables record form, and we _don't_ write out the full
           longitude/latitude grid (it can be reconstructed from
           the other metadata).
        """
        with tables.open_file(fname, mode="w", title="location") as f:
            t = f.create_table(f.root, "location", LocationRowOnDisk, "location")
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
            for i, lat in enumerate(self.latitudes):
                for j, lon in enumerate(self.longitudes):
                    pmass = self.probability[i,j]
                    if pmass:
                        cur['grid_x']    = j
                        cur['grid_y']    = i
                        cur['longitude'] = lon
                        cur['latitude']  = lat
                        cur['prob_mass'] = pmass
                        cur.append()

            t.flush()

    @classmethod
    def load(cls, fname):
        """Read an HDF file containing a location (the result of save()) and
           instantiate a Location object from it."""

        with tables.open_file(fname, "r"):
            t = f.location
            M = scipy.sparse.dok_matrix((t.attrs.lat_count,
                                         t.attrs.lon_count),
                                        dtype=np.float32)
            for row in t.iterrows():
                M[cur['grid_y'], cur['grid_x']] = cur['prob_mass']

            M = M.tocsr()

            longs = np.linspace(t.attrs.west, t.attrs.east,
                                t.attrs.lon_count)
            lats = np.linspace(t.attrs.south, t.attrs.north,
                               t.attrs.lat_count)

            return cls(
                resolution  = t.attrs.resolution,
                fuzz        = t.attrs.fuzz,
                north       = t.attrs.north,
                south       = t.attrs.south,
                east        = t.attrs.east,
                west        = t.attrs.west,
                lon_spacing = t.attrs.lon_spacing,
                lat_spacing = t.attrs.lat_spacing,
                longitudes  = longs,
                latitudes   = lats,
                probability = M
            )

class Map(Location):
    """The map on which to locate a host.

       Maps are defined by HDF5 files (see maps/ for the program that
       generates these from shapefiles) that define a grid over the
       surface of the Earth and a "baseline matrix" which specifies
       the Bayesian prior probability of locating a host at any point
       on that grid.  (For instance, nobody puts servers in the middle
       of the ocean.)
    """

    def __init__(self, mapfile):
        with tables.open(mapfile, 'r') as f:
            M = f.root.baseline
            baseline = scipy.sparse.csr_matrix(M)
            # The probabilities stored in the file are not normalized.
            baseline /= baseline.sum()

            Location.__init__(
                resolution  = M.attrs.resolution,
                fuzz        = M.attrs.fuzz,
                north       = M.attrs.north,
                south       = M.attrs.south,
                east        = M.attrs.east,
                west        = M.attrs.west,
                lon_spacing = M.attrs.lon_spacing,
                lat_spacing = M.attrs.lat_spacing,
                longitudes  = M.attrs.longitudes,
                latitudes   = M.attrs.latitudes,
                probability = baseline
            )

class Observation(Location):
    """A single observation of the distance to a host.

    An observation is defined by a map (used only for its grid spec -
    if you want to intersect the observation with the map, do that
    explicitly), and a _ranging function_ that computes probability as
    a function of location."""

    def __init__(self, map, dfunc):
        M = scipy.sparse.dok_matrix((len(map.latitudes),
                                     len(map.longitudes)),
                                    dtype=map.dtype)

        for i, lat in enumerate(map.latitudes):
            for j, lon in enumerate(map.longitudes):
                n = dfunc(lon, lat)
                if n:
                    M[i,j] = n

        Location.__init__(
            resolution  = map.resolution,
            fuzz        = map.fuzz,
            north       = map.north,
            south       = map.south,
            east        = map.east,
            west        = map.west,
            lon_spacing = map.lon_spacing,
            lat_spacing = map.lat_spacing,
            longitudes  = map.longitudes,
            latitudes   = map.latitudes,
            probability = M.tocsr()
        )
