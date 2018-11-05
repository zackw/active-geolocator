#! /usr/bin/python3

import collections
import errno
import glob
import json
import os
import psycopg2
import subprocess
import sys

# Usage: import-reports <directory> <database>

def errcode(s):
    if isinstance(s, int): return s
    if s == "0" or s == "success": return 0
    v = getattr(errno, s, None)
    if v: return v
    return int(s)

def read_one_report(fname, bname):
    with subprocess.Popen(
            ["gpg2", "--decrypt", "--quiet", "--batch", "--no-tty", fname],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL) as proc:
        data, msgs = proc.communicate()
        rc = proc.wait()
        if rc:
            raise RuntimeError("{}: gpg: exit {}\n{}".format(fname, rc, msgs))

    blob = json.loads(data.decode("utf-8"))
    blob['blob'] = bname

    results = [
        (r[0], errcode(r[2]), float(r[3]))
        for r in blob['results']
    ]
    del blob['results']

    meta                = {}
    meta['date']        = blob['timestamp']
    meta['proxied']     = ('proxied_connection' in blob and
                           blob['proxied_connection'])
    meta['client_addr'] = blob.get('client_addr', '0.0.0.0')
    meta['proxy_addr']  = blob.get('proxy_addr', '0.0.0.0')

    if meta['proxied']:
        if 'socks5' in blob:
            blob['proxy_type'] = 'socks5'
        else:
            blob['proxy_type'] = 'ovpn'

    if 'location_unknown' in meta:
        meta['client_lat']  = 0
        meta['client_lon']  = 0
    else:
        meta['client_lat']  = blob['latitude']
        meta['client_lon']  = blob['longitude']

    if not meta['proxied'] or 'proxy_location_unknown' in blob:
        meta['proxy_lat']   = 0
        meta['proxy_lon']   = 0
    else:
        meta['proxy_lat']   = blob['proxy_latitude']
        meta['proxy_lon']   = blob['proxy_longitude']

    if 'core' in blob:                   del blob['core']
    if 'timestamp' in blob:              del blob['timestamp']
    if 'proxied_connection' in blob:     del blob['proxied_connection']
    if 'client_addr' in blob:            del blob['client_addr']
    if 'proxy_addr' in blob:             del blob['proxy_addr']
    if 'latitude' in blob:               del blob['latitude']
    if 'longitude' in blob:              del blob['longitude']
    if 'location_unknown' in blob:       del blob['location_unknown']
    if 'proxy_latitude' in blob:         del blob['proxy_latitude']
    if 'proxy_longitude' in blob:        del blob['proxy_longitude']
    if 'proxy_location_unknown' in blob: del blob['proxy_location_unknown']
    if 'socks5' in blob:                 del blob['socks5']

    meta['annot'] = json.dumps(blob)
    meta['cid'] = blob['blob']

    return meta, results

def get_already(db):
    with db.cursor() as cur:
        cur.execute("select distinct annot->>'blob' from batches")
        return frozenset(r[0] for r in cur)

def record_one_batch(db, meta, results):
    with db, db.cursor() as cur:
        cur.execute("""
        insert into batches
            (date, proxied,
             client_lat, client_lon, client_addr,
             proxy_lat, proxy_lon, proxy_addr,
             annot)
        values
            (%(date)s, %(proxied)s,
             %(client_lat)s, %(client_lon)s, %(client_addr)s,
             %(proxy_lat)s, %(proxy_lon)s, %(proxy_addr)s,
             %(annot)s::jsonb)
        returning id
        """, meta)
        batchid = cur.fetchone()

        if meta['proxied']:
            cip  = meta['proxy_addr']
            clat = meta['proxy_lat']
            clon = meta['proxy_lon']
        else:
            cip  = meta['client_addr']
            clat = meta['client_lat']
            clon = meta['client_lon']

        cur.execute("""select label from hosts where ipv4 = %s""", (cip,))
        if len(cur.fetchall()) == 0:
            cur.execute("""
            insert into hosts
            values ('client', %s, %s, -1, default, %s, %s, default)
            """,
                        (meta['cid'], cip, clat, clon))

        meas = b",".join(
            cur.mogrify("(%s,%s,%s,%s,%s)",
                        (batchid, cip, dest_ip, rtt, status))
            for dest_ip, status, rtt in results
        )
        cur.execute(b"insert into measurements values " + meas)

def main():
    reportdir = sys.argv[1]
    db = psycopg2.connect(dbname=sys.argv[2])
    already = get_already(db)
    for fn in glob.glob(os.path.join(reportdir, "blob*")):
        bn = os.path.basename(fn)
        if bn in already: continue
        sys.stderr.write(bn + "\n")
        meta, results = read_one_report(fn, bn)
        record_one_batch(db, meta, results)

main()
