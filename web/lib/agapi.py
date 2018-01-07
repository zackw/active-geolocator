"""
active geolocator web API, used by both the command-line and web clients
"""

import csv
import json
import os
import socket
import subprocess
import tempfile

import flask

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

def landmark_list(request, config, log, locations):
    with open(config['ALL_LANDMARKS']) as fp:
        rd = csv.reader(fp)
        data = [
            (row[0], int(row[1]),
             float(row[2]), float(row[3]),
             float(row[4]), float(row[5]))
            for row in rd
        ]

    # In addition, we ask the client to ping 127.0.0.1, its apparent
    # external IP address, a guess at its gateway address (last
    # component of the IPv4 address forced to .1) and this server.  We
    # use TCP port 443 for these partially because that's consistent
    # with the main landmarks list and partially because the
    # JavaScript client isn't allowed to connect to port 7.
    data.append(("127.0.0.1", 443, 0, 0, 0, 0))

    client = request.remote_addr
    # In the unlikely event of an IPv6 address, don't bother; the client
    # can't handle them.
    if ':' not in client:
        gw_guess = '.'.join(client.split('.')[:-1]) + '.1'
        data.append((client, 443, 0, 0, 0, 0))
        data.append((gw_guess, 443, 0, 0, 0, 0))

    data.extend(
        (addr, 443, 0, 0, 0, 0)
        for addr in ipv4_addrs_for(request.host))

    if not locations:
        data = [(x[0], x[1]) for x in data]

    return flask.jsonify(sorted(set(data)))

def probe_results(request, config, log):
    """Record the results of a probe.  Expects a form POST containing one
       key, "blob", which is a JSON object; we validate this object and
       then save it to disk.
    """
    # The framework should already have confirmed that the request was
    # a POST, so we don't check that again.  Contra the documentation,
    # Werkzeug's MultiDict.keys returns an iterator in Py3k.

    def bad_request(what):
        log.debug("bad request: " + what)
        log.debug("  args: " + repr(request.args))
        log.debug("  form: " + repr(request.form))
        log.debug("  files: " + repr(request.files))
        flask.abort(400)

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
