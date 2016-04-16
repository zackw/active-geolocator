#! /usr/bin/env python

from __future__ import division

import os
import sys


import argparse
import csv
import errno
import random
import socket
import subprocess

def perform_probes(addresses, spacing, parallel, timeout, wr):
    """Make a connection to each of the ADDRESSES, in order, and measure
    the time for connect(2) to either succeed or fail -- we don't care
    which.  Each element of the iterable ADDRESSES should be a 2-tuple
    (addr, port) as returned by socket.getaddrinfo().  Successive
    connections will be no closer to each other in time than SPACING
    floating-point seconds.  No more than PARALLEL concurrent
    connections will occur at any one time.  Sockets that have neither
    succeeded nor failed to connect after TIMEOUT floating-point
    seconds will be treated as having failed.  No data is transmitted;
    each socket is closed immediately after the connection resolves.

    The results are written to the csv.writer object WR; each row of the
    file will be <host address>,<elapsed time> (where <host address> is
    addr[4][0] for some element ADDR of ADDRESSES).

    """

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if spacing <= 0:
        raise ValueError("spacing must be positive")
    if parallel < 1:
        raise ValueError("parallel must be at least 1")

    thisdir = os.path.dirname(os.path.abspath(__file__))
    probecore = os.path.join(thisdir, "probe-core")

    cmd = [probecore, str(parallel), str(spacing), str(timeout)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    for host, port in addresses:
        proc.stdin.write("{} {}\n".format(host, port))
    proc.stdin.close()

    for meas in proc.stdout:
        host, port, err, elapsed = meas.split()
        wr.writerow((host, port, errno.errorcode.get(err, err), elapsed))

    rc = proc.wait()
    if rc:
        raise CalledProcessError(rc, cmd)

def choose_probe_order(addresses):
    """Choose a randomized probe order for the addresses ADDRS.

    Unlike perform_probes, ADDRESSES is expected to be a list of
    3-tuples (count, host, port) where COUNT is the number of times to
    probe HOST at PORT.

    The return value is a list acceptable as the ADDRESSES argument to
    perform_probes."""

    remaining = {}
    last_appearance = {}
    for count, host, port in addresses:
        raddr = socket.getaddrinfo(
            host, port, 0, socket.SOCK_STREAM, 0, 0)[0][4]
        remaining[raddr] = count
        last_appearance[raddr] = -1

    rv = []
    deadcycles = 0
    while remaining:
        ks = list(remaining.keys())
        x = random.choice(ks)
        last = last_appearance[x]
        if last == -1 or (len(rv) - last) >= (len(ks) // 4):
            last_appearance[x] = len(rv)
            rv.append(x)
            remaining[x] -= 1
            if not remaining[x]:
                del remaining[x]
            deadcycles = 0
        else:
            deadcycles += 1
            if deadcycles == 10:
                raise RuntimeError("choose_probe_order: 10 dead cycles\n"
                                   "remaining: {!r}\n"
                                   "last_appearance: {!r}\n"
                                   .format(remaining, last_appearance))
    return rv

def load_addresses(ifname):
    """Read addresses to probe from IFNAME.  This is expected to be a
    CSV file with columns named "host", "port", and "nprobes"; any
    other columns are ignored.
    """
    with open(ifname, "rt") as f:
        rd = csv.DictReader(f)
        return [
            (int(r["nprobes"]), r["host"], int(r["port"]))
            for r in rd
        ]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="Input file name (see load_addresses)")
    ap.add_argument("output", help="Output file name (see perform_probes)")
    ap.add_argument("-s", "--spacing", type=float, default=0.1,
                    help="Time between successive probes (default: 0.1s)")
    ap.add_argument("-p", "--parallel", type=int, default=1,
                    help="Number of concurrent probes to perform (default: 1)")
    ap.add_argument("-t", "--timeout", type=float, default=10,
                    help="Connection timeout (default: 10s)")
    args = ap.parse_args()

    addrs = choose_probe_order(load_addresses(args.input))
    with open(args.output, "wt") as f:
        wr = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
        wr.writerow(("host","port","status","elapsed"))
        perform_probes(addrs, args.spacing, args.parallel, args.timeout, wr)

main()
