#! /usr/bin/python3

# Import metadata for a set of OpenVPN servers operated by the same
# organization.
# Usage: import-openvpn-group dbname class directory
# where DIRECTORY contains openvpn config files

import collections
import json
import os
import re
import subprocess
import sys

import psycopg2

import GeoIP

def scan_config_files(dname):
    """Extract the IP address from each .ovpn file in the directory DNAME.
       Other files are ignored, as are .ovpn files for which the "remote"
       line is missing or specifies a hostname rather than an IP address.

       The return value is a list of tuples (partial_path, ip_addr) where
       partial_path is the path starting below dname and ending with the
       filename sans extension, e.g. for foovpn/AT/Wien.ovpn the entry
       will be ('AT/Wien', '192.0.2.2').
    """

    remote_re = re.compile(r"^\s*remote\s*((?:[0-9]+\.){3}[0-9]+)(?:\s|$)")

    result = []

    for dpath, dnames, fnames in os.walk(dname):
        dnames.sort()
        fnames.sort()
        for fn in fnames:
            if not fn.endswith(".ovpn"): continue
            pn = os.path.join(dpath, fn)

            with open(pn) as f:
                for line in f:
                    m = remote_re.match(line)
                    if m:
                        addr = m.group(1)
                        break
                else:
                    continue

            partial = os.path.relpath(pn, dname)
            result.append((
                os.path.splitext(partial)[0],
                addr
            ))

    return result

def lookup_locations(class_, addrs):
    """Look up the locations and ASNs of all the addresses ADDRS.  ADDRS
       is assumed to be the result of scan_config_files(), and the return
       value is an augmented list of tuples,
       (partial_path, addr, asn, country_code, locality, lat, lon, tag).
    """

    citydb = GeoIP.GeoIP("/usr/share/GeoIP/GeoLiteCity.dat",
                         GeoIP.GEOIP_STANDARD)
    asndb  = GeoIP.GeoIP("/usr/share/GeoIP/GeoIPASNum.dat",
                         GeoIP.GEOIP_STANDARD)

    crunch_asn = re.compile(r"^AS([0-9]+)(?: |$)")

    labeler = collections.Counter()

    result = []

    for ppath, addr in addrs:
        asn_long = asndb.name_by_addr(addr)
        if not asn_long:
            sys.stderr.write("{} {} => unknown AS {!r}, "
                             "skipped\n"
                             .format(ppath, addr, asn_long))
            continue

        asm = crunch_asn.match(asn_long)
        asn = asm.group(1)

        gr = citydb.record_by_addr(addr)
        country = gr['country_name']
        region = gr.get('region_name', '') or ''
        city = gr.get('city', '') or ''
        if region and region != city:
            locality = region + '.' + city
        else:
            locality = city

        cc = gr['country_code'].lower()
        labeler[cc] += 1
        label = "{}-{}-{}".format(class_, cc, labeler[cc])

        mlocale = (country + "." + locality).strip(".")
        result.append((
            ppath, addr, asn, cc, mlocale,
            round(gr['latitude'], 3),
            round(gr['longitude'], 3),
            label
        ))
        sys.stdout.write("{} => {}/{}, {}/{}\n".format(addr, ppath, mlocale,
                                                       gr['latitude'],
                                                       gr['longitude']))

    return result

def insert_into_db(dbname, class_, locs):
    db = psycopg2.connect(dbname=dbname)
    cur = db.cursor()
    with db:
        cur.executemany("""
        insert into geolocation.hosts
               (class, label, ipv4, asn, country, latitude, longitude, annot)
        values (%s,    %s,    %s,   %s,  %s,      %s,       %s,        %s)
        """, [
            (class_, label, ipv4, asn, cc, lat, lon,
             json.dumps({
                 "mmd_locale": mlocale,
                 "filename": ppath
             }))
            for ppath, ipv4, asn, cc, mlocale, lat, lon, label in locs
        ])

def main():
    dbname, class_, direc = sys.argv[1:]
    insert_into_db(dbname, class_,
                   lookup_locations(class_, scan_config_files(direc)))
main()
