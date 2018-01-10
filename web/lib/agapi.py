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

def landmark_list(request, config, log, locations):
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

    with open(config['ALL_LANDMARKS']) as fp:
        rd = csv.DictReader(fp)
        data = [lm_entry(row) for row in rd]

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

def continent_marks(request, config, log):
    """Return the subset of landmarks that is to be used for
       first-stage localization.  This is supposed to be a
       short but broadly geographically distributed list.
    """
    with open(config['CONTINENT_MARKS']) as fp:
        rd = csv.DictReader(fp)
        data = [lm_entry_with_location(row) for row in rd]

    # Also ask the client to ping its apparent external IP address.
    data.append(x_entry_with_location(request.remote_addr))

    return flask.jsonify(sorted(tuple(x) for x in set(data)))

def local_marks(request, config, log):
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
            raise ValueError("radius out of range")
        return v * 1000

    try:
        lat = request.args.getlist('lat', type=check_latitude)
        lon = request.args.getlist('lon', type=check_longitude)
        rad = request.args.getlist('rad', type=check_kilometers)
    except KeyError:
        bad_request("missing key")
    except ValueError:
        bad_request("malformed query value")

    if len(lat) != len(lon) or len(lon) != len(rad):
        bad_request("wrong number of values for a query key")

    n = int(request.args.get('n', 100))
    neighbor_dist = float(request.args.get('neighbor_dist', 100))

    shape = geometry.intersect_disks_on_globe(lon, lat, rad)

    with open(config['ALL_LANDMARKS']) as fp:
        rd = csv.DictReader(fp)
        lmsample = geometry.sample_tuples_near_shape(
            shape, (lm_entry_with_location(row) for row in rd),
            n=n, neighbor_dist=neighbor_dist)

    return flask.jsonify(sorted(tuple(x) for x in set(lmsample)))

def probe_results(request, config, log):
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

    blob['client_ip'] = request.remote_addr
    blob = json.dumps(blob, separators=(',',':'), sort_keys=True).encode('utf-8')

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
