"""
active geolocator web API, used by both the command-line and web clients
"""

import collections
import csv
import functools
import json
import os
import socket
import subprocess
import tempfile

import flask

from database import get_db_cursor
import geometry

# Utility functions

def bad_request_(request, log, what):
    log.debug("bad request: " + what)
    log.debug("  args: " + repr(request.args))
    log.debug("  form: " + repr(request.form))
    log.debug("  files: " + repr(request.files))
    flask.abort(400)

def ipv4_addrs_for(hostname):
    if hostname[0] == '[': # IPv6 address literal
        return []
    hostname = hostname.partition(':')[0]

    return sorted(set(
        addr[4][0]
        for addr in socket.getaddrinfo(hostname, None,
                                       family=socket.AF_INET,
                                       proto=socket.IPPROTO_TCP)
        if addr[0] == socket.AF_INET
        and not addr[4][0].startswith('127.')
    ))

_LandmarkWithLocation = collections.namedtuple(
    "_LandmarkWithLocation",
    ("addr", "port", "lat", "lon", "m", "b"))
class LandmarkWithLocation(_LandmarkWithLocation):
    __slots__ = ()
    def __new__(cls, *, addr, port, lat, lon, m, b, **ignored):
        return super().__new__(cls, addr, int(port),
                               float(lat), float(lon),
                               float(m), float(b))

_LandmarkBare = collections.namedtuple(
    "_LandmarkBare",
    ("addr", "port"))
class LandmarkBare(_LandmarkBare):
    __slots__ = ()
    def __new__(cls, *, addr, port, **ignored):
        return super().__new__(addr, int(port))

def lm_entry_with_location(row):
    return LandmarkWithLocation(**row)

def lm_entry_no_location(row):
    return LandmarkBare(**row)

# Additional probes - not sent to a target on the landmarks list
# always use port 80, partially because that's consistent with the
# usual settings in the landmarks list, and partially because the
# JavaScript client isn't allowed to connect to port 7.
def x_entry_with_location(addr):
    return LandmarkWithLocation(addr=addr, port=80, lat=0, lon=0, m=0, b=0)

def x_entry_no_location(addr):
    return LandmarkBare(addr=addr, port=80)

def landmark_list(request, config, log, db, locations):
    """(Previous API generation) Return the complete list of
       available landmarks, optionally with locations and CBG
       calibration parameters.
    """

    if locations:
        lm_entry = lm_entry_with_location
        x_entry = x_entry_with_location
    else:
        lm_entry = lm_entry_no_location
        x_entry = x_entry_no_location

    with get_db_cursor(db) as cur:
        # For the legacy API, return the complete set of usable
        # RIPE anchors.  If an anchor does not have a CBG calibration,
        # use the CBG baseline (2/3c, no fixed delay).
        cur.execute("""
            SELECT addr, 80 AS port,
                   ST_Y(location::GEOMETRY) as lat,
                   ST_X(location::GEOMETRY) as lon,
                   COALESCE(cbg_m, 100000) AS m, COALESCE(cbg_b, 0) AS b
              FROM landmarks
             WHERE usable AND anchorid IS NOT NULL
        """)
        data = [lm_entry(row) for row in cur]

    # In addition, we ask the client to ping 127.0.0.1, its apparent
    # external IP address, a guess at its gateway address (last
    # component of the IPv4 address forced to .1) and this server.
    data.append(x_entry("127.0.0.1"))

    client = request.remote_addr
    # In the unlikely event of an IPv6 address, don't bother; the client
    # can't handle them.
    if ':' not in client:
        gw_guess = '.'.join(client.split('.')[:-1]) + '.1'
        data.append(x_entry(client))
        data.append(x_entry(gw_guess))

    data.extend(x_entry(addr) for addr in ipv4_addrs_for(request.host))

    return flask.jsonify(sorted(tuple(x) for x in set(data)))

def continent_marks(request, config, log, db):
    """Return the subset of landmarks that is to be used for
       first-stage localization.  This is supposed to be a
       short but broadly geographically distributed list.
    """
    with get_db_cursor(db) as cur:
        # This query selects the three anchors within each region (as
        # defined by the "regions" table) that are closest to the
        # centroid of the point-set of all the usable probes (not just
        # anchors) within that region.  (The location of that centroid
        # was previously determined and written to the database by the
        # update script.)  Anchors closer than 100km to the previously
        # selected anchor are excluded.  As above, if an anchor does
        # not have a CBG calibration, use the CBG baseline (2/3c, no
        # fixed delay).
        cur.execute("""
            SELECT addr, 80 AS port,
                   ST_Y(location::GEOMETRY) AS lat,
                   ST_X(location::GEOMETRY) AS lon,
                   COALESCE(cbg_m, 100000) AS m, COALESCE(cbg_b, 0) AS b
              FROM (SELECT *, RANK() OVER (PARTITION BY rgn_id
                                           ORDER BY c_distance)
              FROM (SELECT *, ST_Distance(location, LAG(location)
                                OVER (PARTITION BY rgn_id
                                      ORDER BY c_distance))
                                AS prev_distance
              FROM (SELECT l.anchorid, l.addr, l.location, l.cbg_m, l.cbg_b,
                           r.id as rgn_id,
                           ST_Distance(l.location, r.lm_centroid)
                               AS c_distance
              FROM landmarks l, regions r
             WHERE l.usable AND l.anchorid IS NOT NULL AND l.region = r.id)
          _1)
          _2 WHERE (prev_distance IS NULL OR prev_distance > 100000))
          _3 WHERE rank <= 3;
        """)
        data = [lm_entry_with_location(row) for row in cur]

    # Also ask the client to ping its apparent external IP address
    # and 127.0.0.1.  It uses these pingtimes to estimate connection
    # overhead.
    data.append(x_entry_with_location(request.remote_addr))
    data.append(x_entry_with_location("127.0.0.1"))

    return flask.jsonify(sorted(tuple(x) for x in set(data)))

def local_marks(request, config, log, db):
    """Return the subset of all available landmarks which are within a
       useful striking distance of a particular location, expressed as
       a set of (longitude, latitude, radius) triples; the location is
       where all the disks intersect.
    """
    bad_request = functools.partial(bad_request_, request, log)

    if request.form or request.files:
        bad_request("should be no form or files")

    def check_latitude(v):
        v = float(v)
        if not (-90 <= v <= 90):
            raise ValueError("latitude out of range")
        return v
    def check_longitude(v):
        v = float(v)
        if not (-180 <= v <= 180):
            raise ValueError("latitude out of range")
        return v
    def check_kilometers(v):
        v = float(v)
        if not (0 < v < 20037.5):
            raise ValueError("km out of range")
        return v * 1000

    try:
        lats = request.args.getlist('lat', type=check_latitude)
        lons = request.args.getlist('lon', type=check_longitude)
        rads = request.args.getlist('rad', type=check_kilometers)

        neighbor_dist = check_kilometers(
            request.args.get('neighbor_dist', '10'))

        n = int(request.args.get('n', '50'))
        if not (0 < n <= 200):
            raise ValueError("n out of range")

    except KeyError:
        bad_request("missing key")
    except ValueError:
        bad_request("malformed query value")

    if len(lats) != len(lons) or len(lons) != len(rads):
        bad_request("wrong number of values for a query key")

    # Process the disks from smallest to largest.
    disks = sorted(zip(lons, lats, rads), key = lambda d: d[2])

    sample = []
    scale = -1000 * 1000
    scaledelta = max(1000 * 1000, 4 * neighbor_dist)
    with get_db_cursor(db) as cur:
        while len(sample) < n and scale < 10018750:
            # Construct a SQL query which will retrieve all of the
            # probes that are within the intersection of all the
            # requested disks.  Rather than attempting to _compute_
            # that intersection, we simply ask the database for
            # "within X meters of point (A,B) AND within Y meters of
            # point (C,D) AND ..." -- this is probably just as fast
            # for short lists of disks, and is more likely to do the
            # Right Thing when some of the circles are very large
            # and/or cross the poles or the antimeridian.
            #
            # Note that unlike the above two queries, this one is not
            # limited to anchors.  Logic above has ensured that
            # 'lats', 'lons', and 'rads' contain only floats, so the
            # SQL string bashing below is injection-safe.
            query = """
                SELECT addr, 80 AS port,
                       ST_Y(location::GEOMETRY) as lat,
                       ST_X(location::GEOMETRY) as lon,
                       COALESCE(cbg_m, 100000) AS m, COALESCE(cbg_b, 0) AS b
                  FROM landmarks
                 WHERE usable
            """
            query += "\n".join(
            "AND ST_DWithin(location, 'SRID=4326;POINT({} {})'::GEOGRAPHY, {})"
                .format(lon, lat, rad + scale)
                for lon, lat, rad in disks)

            cur.execute(query)

            geometry.sample_more_tuples_into(
                sample, n, neighbor_dist,
                (lm_entry_with_location(row) for row in cur))

            scale += scaledelta

    if not sample:
        # Treating this as a bad request simplifies the client.
        bad_request("no landmarks available - empty intersection?")

    return flask.jsonify(sorted(tuple(x) for x in set(sample)))

def probe_results(request, config, log, lmdb):
    """Record the results of a probe.  Expects a form POST containing one
       key, "blob", which is a JSON object; we validate this object and
       then save it to disk.
    """
    bad_request = functools.partial(bad_request_, request, log)

    # The framework should already have confirmed that the request was
    # a POST, so we don't check that again.  Contra the documentation,
    # Werkzeug's MultiDict.keys returns an iterator in Py3k.
    if request.args or request.files:
        bad_request("should be no args or files")

    keys = list(request.form.keys())
    if len(keys) != 1:
        bad_request("wrong number of form keys")
    if keys[0] != "blob":
        bad_request("unexpected form key '{!r}'".format(keys[0]))

    blobs = request.form.getlist('blob')
    if len(blobs) != 1:
        bad_request("wrong number of blob values")

    try:
        blob = json.loads(blobs[0])
    except Exception as e:
        bad_request("blob has json parse error: " + e)

    if not isinstance(blob, dict):
        bad_request("blob is not a dictionary")
    if not blob:
        bad_request("blob is empty")

    if blob.get('proxied_connection', False):
        blob['proxy_addr'] = request.remote_addr
    else:
        if 'client_addr' not in blob:
            blob['client_addr'] = request.remote_addr

    blob = json.dumps(blob, separators=(',',':'),
                      sort_keys=True).encode('utf-8')

    os.makedirs(config['REPORT_DIR'], exist_ok=True)
    with tempfile.NamedTemporaryFile(
            prefix="blob", dir=config['REPORT_DIR'], delete=False) as ofp:

        if config['GPG_HOME'] and config['ENCRYPT_TO']:
            proc = subprocess.Popen([
                "gpg2", "--homedir", config['GPG_HOME'],
                "--no-permission-warning", "--encrypt", "--sign",
                "--recipient", config['ENCRYPT_TO']
            ], stdin=subprocess.PIPE, stdout=ofp)

            proc.stdin.write(blob)
            proc.stdin.close()
            if proc.wait() != 0:
                flask.abort(500)

        else:
            ofp.write(blob)

        os.chmod(ofp.name, 0o640)

        return flask.jsonify({ 'ccode': os.path.basename(ofp.name)[4:] })
