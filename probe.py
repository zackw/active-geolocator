#! /usr/bin/env python

from __future__ import division

import os
import sys
sys.path.insert(0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))

import argparse
import csv
import errno
import random
import socket

try:
    from time import monotonic as tick
except ImportError:
    from time import time as tick

try:
    import selectors
except ImportError:
    import selectors34 as selectors

start = tick()
def progress(now, total, pending, complete):
    global start
    h, ms = divmod(now - start, 3600)
    m, s  = divmod(ms, 60)
    sys.stderr.write("{:d}:{:02d}:{:06.3f}: {:>6}/{:>6} complete, {} pending\n"
                     .format(int(h), int(m), s, complete, total, pending))

def perform_probes(addresses, spacing, parallel, timeout, wr):
    """Make a connection to each of the ADDRESSES, in order, and measure
    the time for connect(2) to either succeed or fail -- we don't care
    which.  Each element of the iterable ADDRESSES should be a 5-tuple
    as returned by socket.getaddrinfo().  Successive connections will
    be no closer to each other in time than SPACING floating-point
    seconds.  No more than PARALLEL concurrent connections will occur
    at any one time.  Sockets that have neither succeeded nor failed
    to connect after TIMEOUT floating-point seconds will be treated as
    having failed.  No data is transmitted; each socket is closed
    immediately after the connection resolves.

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

    sel = selectors.DefaultSelector()
    EVENT_RW = selectors.EVENT_READ|selectors.EVENT_WRITE

    pending = set()
    addresses.reverse()
    last_connection = 0
    total = len(addresses)
    complete = 0

    try:
        while pending or addresses:
            now = tick()
            progress(now, total, len(pending), complete)

            if (len(pending) < parallel and addresses
                and now - last_connection >= spacing):

                addr = addresses.pop()
                sock = socket.socket(addr[0], addr[1], addr[2])
                sock.settimeout(timeout)

                last_connection = tick()
                err = sock.connect_ex(addr[4])
                if err == errno.EINPROGRESS:
                    # This is the expected case: the connection attempt is
                    # in progress and we must wait for results.
                    pending.add(sel.register(sock, EVENT_RW,
                                             (addr[4][0], last_connection)))

                elif err in (0,
                           errno.ECONNREFUSED,
                           errno.EHOSTUNREACH,
                           errno.ENETUNREACH,
                           errno.ETIMEDOUT,
                           errno.ECONNRESET):
                    # The connection attempt resolved before connect()
                    # returned.
                    after = tick()
                    wr.writerow((addr[4][0], after - now))
                    complete += 1

                else:
                    # Something dire has happened and we probably
                    # can't continue (for instance, there's no local
                    # network connection).
                    exc = socket.error(err, os.strerror(err))
                    exc.filename = addr[4][0]
                    raise exc

            events = sel.select(spacing)
            after = tick()
            # We don't care whether each connection succeeded or failed.
            for key, _ in events:
                addr, before = key.data
                sock = key.fileobj

                sel.unregister(sock)
                sock.close()
                pending.remove(key)
                wr.writerow((addr, after - before))
                complete += 1

        #end while
        return rv

    finally:
        for key in pending:
            sel.unregister(key.fileobj)
            key.fileobj.close()
        sel.close()

def choose_probe_order(addresses):
    """Choose a randomized probe order for the addresses ADDRS.

    Unlike perform_probes, ADDRESSES is expected to be a list of
    3-tuples (count, host, port) where COUNT is the number of times to
    probe HOST at PORT, and HOST+PORT are acceptable as the first two
    arguments to socket.getaddrinfo() in AI_NUMERICHOST|AI_NUMERICSERV mode.

    The return value is a list acceptable as the ADDRESSES argument to
    perform_probes."""

    remaining = {}
    last_appearance = {}
    resolved_address = {}
    for count, host, port in addresses:
        rfaddr = socket.getaddrinfo(
            host, port, 0, socket.SOCK_STREAM, 0,
            socket.AI_NUMERICHOST|socket.AI_NUMERICSERV)[0]
        raddr = rfaddr[4]
        remaining[raddr] = count
        last_appearance[raddr] = -1
        resolved_address[raddr] = rfaddr

    rv = []
    deadcycles = 0
    while remaining:
        ks = remaining.keys()
        x = random.choice(ks)
        last = last_appearance[x]
        if last == -1 or (len(rv) - last) >= (len(ks) // 4):
            last_appearance[x] = len(rv)
            rv.append(resolved_address[x])
            remaining[x] -= 1
            if not remaining[x]:
                del remaining[x]
            deadcycles = 0
        else:
            deadcycles += 1
            if deadcycles == 10:
                raise RuntimeError("choose_probe_order: 10 dead cycles\n"
                                   "remaining: %r\n"
                                   "last_appearance: %r\n"
                                   % (remaining, last_appearance))
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
        wr.writerow(("addr","elapsed"))
        perform_probes(addrs, args.spacing, args.parallel, args.timeout, wr)

main()
