#! /usr/bin/python3

"""Retrieve data on all of the publicly accessible RIPE probes and anchors
from their web service, classify them, and compute CBG calibration parameters
for them."""

import argparse
import collections
import datetime
from   functools import partial
from   itertools import chain, zip_longest
from   math      import inf as Inf
import multiprocessing
import os
import random
import sys
import time
import traceback
from   urllib.parse import urljoin, urlencode

from   ripe.atlas.sagan import PingResult
import numpy as np
from   scipy import optimize
import requests
import psycopg2
from   psycopg2.extras import execute_values

#
# Utility
#

start = None
def progress(msg):
    now = time.monotonic()
    global start
    if start is None:
        start = now
    m, s = divmod(now - start, 60)
    h, m = divmod(m, 60)
    sys.stderr.write("{}:{:>02}:{:>05.2f}: {}\n".format(int(h), int(m), s, msg))

def log_exception(msg, exc):
    progress(msg + ": " + str(exc))
    traceback.print_exc(file=sys.stderr)

def maybe_dt_from_timestamp(stamp):
    return None if stamp is None else datetime.datetime.utcfromtimestamp(stamp)

def beginning_of_day(date):
    return datetime.datetime.combine(date, datetime.time.min)

# Slightly modified from <https://stackoverflow.com/a/10791887/388520>.
# It is infuriating that this isn't already in itertools.
# Note: the sentinel must be an object used for no other purpose.
# It's a default argument only so it isn't recreated on every call;
# don't use that argument.
def chunked(iterable, *, n=100, __sentinel = object()):
    args = [iter(iterable)] * n
    for chunk in zip_longest(fillvalue=__sentinel, *args):
        if chunk[-1] is __sentinel:
            yield tuple(v for v in chunk if v is not __sentinel)
        else:
            yield chunk

class WorkerState:
    """HTTP session, RNG, and database connection objects must not be
       created before forking.  Each worker creates an instance of this
       object on startup."""
    def __init__(self, dbname):
        self.session = requests.Session()
        self.session.headers = {
            'User-Agent':'inter-anchor-rtt-retriever-1.0; zackw at cmu dot edu'
        }
        self.rng = random.Random()
        self.db  = psycopg2.connect(dbname=dbname)

_dbname = None
_wstate = None
def get_worker_state():
    global _wstate, _dbname
    if _wstate is None:
        assert _dbname is not None
        _wstate = WorkerState(_dbname)
    return _wstate

BASE_URL = 'https://atlas.ripe.net/api/v2/'

Landmark = collections.namedtuple('Landmark', (
    'pid', 'aid', 'address_v4',
    'latitude', 'longitude'
    ))

def retrieve_atlas(sess, endpoint, *,
                   constructor = lambda x: x,
                   filter      = lambda x: True,
                   params      = None):

    query_url = urljoin(BASE_URL, endpoint)
    if query_url[-1] != '/':
        query_url += '/'
    if params:
        query_url = urljoin(query_url, '?' + urlencode(params))

    retries = 0
    while True:
        try:
            resp = sess.get(query_url)
            resp.raise_for_status()
            blob = resp.json()
        except Exception as e:
            retries += 1
            if isinstance(e, requests.exceptions.ChunkedEncodingError):
                progress("retrieve_atlas: {}: protocol error{}"
                         .format(endpoint,
                                 ", retrying" if retries < 5 else ""))
            else:
                log_exception("retrieve_atlas [endpoint={} params={!r}]"
                              .format(endpoint, params), e)
            if retries >= 5:
                break
            time.sleep(5)
            continue

        if isinstance(blob, list):
            next_url = None
        else:
            next_url = blob.get("next")
            blob = blob["results"]
        for obj in blob:
            if filter(obj):
                yield constructor(obj)

        if next_url is None:
            break
        query_url = urljoin(query_url, next_url)

#
# Probe and anchor lists
#

def landmark_from_anchor_json(blob):
    assert blob["geometry"]["type"] == "Point"
    return Landmark(
        pid = blob["probe"]["id"],
        aid = blob["id"],
        address_v4 = blob["ip_v4"],
        latitude = blob["geometry"]["coordinates"][1],
        longitude = blob["geometry"]["coordinates"][0]
    )
def anchor_is_usable(blob):
    return (blob.get("ip_v4") is not None and
            blob["probe"]["status"]["name"] == "Connected" and
            -60 <= blob["geometry"]["coordinates"][1] <= 85);

def retrieve_active_anchor_list():
    progress("retrieving active anchor list...")
    state = get_worker_state()
    return retrieve_atlas(state.session, 'anchors',
                          params = { 'include': 'probe' },
                          constructor = landmark_from_anchor_json,
                          filter = anchor_is_usable)

def landmark_from_probe_json(blob):
    assert blob["geometry"]["type"] == "Point"
    return Landmark(
        aid = None,
        pid = blob["id"],
        address_v4 = blob["address_v4"],
        latitude = blob["geometry"]["coordinates"][1],
        longitude = blob["geometry"]["coordinates"][0]
    )
def probe_is_usable(blob):
    return (blob.get("address_v4") is not None and
            blob["is_public"] and not blob["is_anchor"] and
            blob["status"]["name"] == "Connected" and
            -60 <= blob["geometry"]["coordinates"][1] <= 85)

def retrieve_active_probe_list():
    progress("retrieving active probe list...")
    state = get_worker_state()
    # Retrieve only probes that are not already covered by the anchor
    # list, are public, have been active with a stable IPv4 address
    # for at least 30 days, and are not behind NAT.
    return retrieve_atlas(state.session, 'probes',
                          params = {
                              'is_anchor': 'false',
                              'is_public': 'true',
                              'status_name': 'Connected',
                              'tags': 'system-ipv4-stable-30d,no-nat'
                          },
                          constructor = landmark_from_probe_json,
                          filter = probe_is_usable)

def update_landmarks_in_db(current_landmarks):
    progress("recording {} active landmarks in database".format(
        len(current_landmarks)))
    state = get_worker_state()
    with state.db, state.db.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE current_landmarks (
                probeid   INTEGER  NOT NULL PRIMARY KEY,
                anchorid  INTEGER,
                addr      INET     NOT NULL,
                latitude  REAL     NOT NULL,
                longitude REAL     NOT NULL);
        """)
        execute_values(cur, """
            INSERT INTO current_landmarks
                (probeid, anchorid, addr, latitude, longitude)
            VALUES %s;
        """, current_landmarks, page_size=400)
        cur.execute("""
            ANALYZE current_landmarks;
        """)
        cur.execute("""
            DELETE FROM current_landmarks
             WHERE latitude < -60 OR latitude > 85;
        """)
        cur.execute("""
            UPDATE landmarks
               SET usable = false
             WHERE usable = true
               AND (addr NOT IN (SELECT addr FROM current_landmarks)
                    OR probeid NOT IN (SELECT probeid FROM current_landmarks));
        """)
        cur.execute("""
            INSERT INTO landmarks (probeid, anchorid, addr, usable, location)
            SELECT probeid, anchorid, addr, true AS usable,
                   ST_SetSRID(ST_Point(longitude, latitude), 4326) AS location
              FROM current_landmarks
                ON CONFLICT (probeid)
                DO UPDATE SET anchorid = excluded.anchorid,
                              addr = excluded.addr,
                              usable = true,
                              location = excluded.location;
        """)
        cur.execute("""
            DROP TABLE current_landmarks;
        """)
        cur.execute("""
            UPDATE landmarks AS l SET region = r.id
            FROM regions r
            WHERE l.region IS NULL
            AND ST_Covers(r.box, l.location::geometry);
        """)
        cur.execute("""
            ANALYZE landmarks;
        """)
        # Should really cast back to GEOGRAPHY before calling ST_Centroid,
        # but postgis 2.3.1 (the version in debian 9) doesn't support that.
        # The error is less than 500km in all cases so we can live with it
        # for now.
        cur.execute("""
            UPDATE regions AS r SET lm_centroid = s.centroid
              FROM (SELECT r.id,
                       ST_Centroid(ST_Union(l.location::GEOMETRY))
                         AS centroid
                      FROM regions r, landmarks l
                     WHERE l.usable AND l.region = r.id
                     GROUP BY r.id) s
             WHERE s.id = r.id;
        """)
        cur.execute("""
            SELECT cl, count(*) FROM (
                SELECT CASE WHEN NOT usable THEN 'unusable'
                            WHEN anchorid IS NOT NULL THEN 'anchor'
                            ELSE 'probe' END AS cl
                FROM landmarks
            ) _ GROUP BY cl;
        """)
        counts = { 'unusable': 0, 'anchor': 0, 'probe': 0 }
        counts.update(cur.fetchall())
        progress("recorded {probe} probes, {anchor} anchors, and "
                 "{unusable} no longer usable.".format(**counts))

        # The next stages need this information, and we have a
        # database handle right now, so we may as well retrieve it.
        cur.execute("""
            SELECT l.addr, l.anchorid, MAX(m.stop_time)
              FROM landmarks l
         LEFT JOIN ripe_measurements m ON l.addr = m.d_addr
             WHERE l.usable AND l.anchorid IS NOT NULL
          GROUP BY l.addr, l.anchorid
        """)
        return list(cur)

def retrieve_active_probes_and_anchors():
    """Worker function: retrieve the list of available probes,
       classify them, and record them in the database.  Returns
       lists of probe IDs and anchor addresses, which are needed by
       the next stage.
    """
    return update_landmarks_in_db(set(chain(
        retrieve_active_anchor_list(),
        retrieve_active_probe_list())))

#
# Retrieving round-trip times
#
def retrieve_anchor_ping_measurements(ainfo):
    """Worker function: retrieve all ping measurements targeting the
       anchor with IPv4 address ADDR, and record their metadata in the
       database.
    """
    addr, _, max_stop_time = ainfo
    state = get_worker_state()
    params = {
        "target_ip": addr,
        "status": "2,4,8",
        "type": "ping",
        "optional_fields": "probes"
    }
    if max_stop_time is not None:
        params["stop_time__gt"] = max_stop_time.timestamp()

    updates = list(retrieve_atlas(
        state.session, 'measurements', params=params,
        constructor = lambda m: (
            addr, m["id"],
            maybe_dt_from_timestamp(m["start_time"]),
            maybe_dt_from_timestamp(m["stop_time"]),
            [p["id"] for p in m["probes"]]
        )))
    if updates:
        with state.db, state.db.cursor() as cur:
            execute_values(cur, """
                    INSERT INTO ripe_measurements
                        (d_addr, meas_id, start_time, stop_time, probes)
                    VALUES %s
                    ON CONFLICT (meas_id)
                    DO UPDATE SET d_addr     = excluded.d_addr,
                                  start_time = excluded.start_time,
                                  stop_time  = excluded.stop_time,
                                  probes     = excluded.probes
                """, updates)

            progress("{}: updated {} measurement{}"
                     .format(addr, len(updates),
                             "" if len(updates) == 1 else "s"))

def retrieve_anchor_ping_results(ainfo):
    d_addr, anchorid, max_stop_time = ainfo
    state = get_worker_state()
    progress("retrieving ping times toward {}".format(d_addr))
    with state.db, state.db.cursor() as cur:
        cur.execute("""
            SELECT l.probeid, min(r.odate), max(r.odate)
              FROM (SELECT * FROM landmarks WHERE usable) l
         LEFT JOIN (SELECT * FROM calibration_rtts WHERE d_id = %s) r
                ON l.probeid = r.s_id
          GROUP BY l.probeid;
        """, (anchorid,))
        already_have = { row[0]: (row[1], row[2]) for row in cur }

        # Construct an index of available measurements sorted in
        # reverse order of when they ended (that is, most recent first,
        # still running first of all).
        cur.execute("""
            SELECT meas_id, start_time, stop_time, probes
              FROM ripe_measurements
             WHERE d_addr = %s
          ORDER BY stop_time DESC NULLS FIRST;
        """, (d_addr,))
        measurements = cur.fetchall()
    # release database locks while talking to RIPE

    # So that we never have to UPDATE a row in calibration_rtts, and
    # also to make the logic below a little bit simpler, we never
    # retrieve any information about the current day, only days that
    # are completely in the past.
    begin_today = beginning_of_day(datetime.date.today())

    # We want to record a number for each probe for each day, which is
    # most easily done by querying the measurements API for one-day
    # intervals.
    one_day = datetime.timedelta(days=1)

    # We record a blank measurement for yesterday for all probes that
    # don't have any data for yesterday; this ensures future runs of
    # this script will not repeat work that has already been done.
    yesterday = (begin_today - one_day).date()
    no_data_for_yesterday = set()

    # For each probe, work out how far back in time we need to go for
    # it.  Some probes do not ping all of the anchors continuously, so
    # we need to do this in two passes.  The first, optimistic pass
    # retrieves only the data we would need if all of the probes did
    # ping all of the anchors at least once each day -- going back at
    # most 14 days.
    optimistic_earliest_time = begin_today - datetime.timedelta(days=14)
    earliest_time_for_probe = {}
    earliest_time_overall = begin_today
    for probe, (_, maxdate) in already_have.items():
        if maxdate is None:
            maxdatestamp = optimistic_earliest_time
        else:
            maxdatestamp = beginning_of_day(maxdate)

        # If the maxdatestamp is at or after the beginning of today,
        # we don't need to retrieve anything for this probe at all.
        if maxdatestamp < begin_today:
            earliest_time_for_probe[probe] = max(maxdatestamp,
                                                 optimistic_earliest_time)
            earliest_time_overall = min(earliest_time_overall,
                                        earliest_time_for_probe[probe])
            no_data_for_yesterday.add(probe)

    if earliest_time_overall == begin_today:
        progress("no need to retrieve pings for {}".format(d_addr))
        return
    else:
        progress("{}: beginning of today is {}"
                 .format(d_addr, begin_today))
        progress("{}: optimistic earliest date is {}"
                 .format(d_addr, earliest_time_overall))
        progress("{}: need data for {} probes".format(d_addr, len(earliest_time_for_probe)))
        for pid, etime in earliest_time_for_probe.items():
            if etime < (begin_today - one_day):
                progress("{}: for {} need since {}"
                         .format(d_addr, pid, etime))

    new_data = []
    w_stop_time = begin_today
    while earliest_time_for_probe and w_stop_time > earliest_time_overall:
        w_start_time = w_stop_time - one_day
        assert w_start_time >= earliest_time_overall

        datestamp = w_start_time.date()
        data_this_day = {}
        for m_id, m_start_time, m_stop_time, m_probes in measurements:
            if m_stop_time is None:
                m_stop_time = begin_today
            if m_stop_time < w_start_time or m_start_time > w_stop_time:
                continue

            wanted_probes = set(earliest_time_for_probe.keys())
            wanted_probes.intersection_update(m_probes)
            if not wanted_probes:
                continue

            progress("retrieving measurement {} for {}"
                     .format(m_id, datestamp))

            x_start_time = int(max(m_start_time, w_start_time).timestamp())
            x_stop_time = int(min(m_stop_time, w_stop_time).timestamp())
            m_results = 'measurements/{}/results'.format(m_id)

            for probeids in chunked(wanted_probes, n=100):
                for result in retrieve_atlas(
                        state.session, m_results,
                        params = {
                            "start": x_start_time,
                            "stop": x_stop_time,
                            "probe_ids": ",".join(str(pid)
                                                  for pid in probeids)
                        },
                        constructor = PingResult):
                    if (result.is_error or result.is_malformed or
                        result.rtt_min is None):
                        continue
                    if result.probe_id in data_this_day:
                        data_this_day[result.probe_id] = min(
                            data_this_day[result.probe_id],
                            result.rtt_min)
                    else:
                        data_this_day[result.probe_id] = result.rtt_min

                    if datestamp == yesterday:
                        no_data_for_yesterday.discard(result.probe_id)

        progress("retrieved pings from {} probes to {} on {}"
                 .format(len(data_this_day), d_addr, datestamp))
        for probeid, minrtt in data_this_day.items():
            new_data.append((probeid, datestamp, minrtt))
            if (probeid in earliest_time_for_probe and
                earliest_time_for_probe[probeid] >= w_start_time):
                del earliest_time_for_probe[probeid]

        w_stop_time = w_start_time

    # Do not load historical data for probes for which we have
    # received no new data but earlier data was available.
    for pid, etime in list(earliest_time_for_probe.items()):
        if etime > optimistic_earliest_time:
            del earliest_time_for_probe[pid]

    if earliest_time_for_probe:
        progress("{}: need historical data for {} probes"
                 .format(d_addr, len(earliest_time_for_probe)))
        for pid, etime in earliest_time_for_probe.items():
            progress("{}: for {} need since {}"
                     .format(d_addr, pid, etime))

        historical_data = collections.defaultdict(dict)

        for m_id, m_start_time, m_stop_time, m_probes in measurements:
            if m_stop_time is None or m_stop_time >= earliest_time_overall:
                continue # we already got this one

            wanted_probes = set(earliest_time_for_probe.keys())
            wanted_probes.intersection_update(m_probes)
            if not wanted_probes:
                continue # this measurement has no probes we care about

            # Round the stop time up to a day boundary.
            if m_stop_time == beginning_of_day(m_stop_time.date()):
                w_stop_time = m_stop_time
            else:
                w_stop_time = beginning_of_day((m_stop_time + one_day).date())

            days_considered = 0
            while w_stop_time > m_start_time and days_considered < 14:
                w_start_time = w_stop_time - one_day
                datestamp = w_start_time.date()

                x_start_time = int(max(m_start_time, w_start_time).timestamp())
                x_stop_time = int(min(m_stop_time, w_stop_time).timestamp())
                m_results = 'measurements/{}/results'.format(m_id)
                data_this_day = historical_data[datestamp]

                progress("retrieving measurement {} for {} (historical)"
                         .format(m_id, datestamp))
                old_pings = len(data_this_day)
                for probeids in chunked(wanted_probes, n=100):
                    for result in retrieve_atlas(
                            state.session, m_results,
                            params = {
                                "start": x_start_time,
                                "stop": x_stop_time,
                                "probe_ids": ",".join(str(pid)
                                                      for pid in probeids)
                            },
                            constructor = PingResult):
                        if (result.is_error or result.is_malformed or
                            result.rtt_min is None):
                            continue
                        if result.probe_id in data_this_day:
                            data_this_day[result.probe_id] = min(
                                data_this_day[result.probe_id],
                                result.rtt_min)
                        else:
                            data_this_day[result.probe_id] = result.rtt_min
                progress(
                    "retrieved pings from {} probes to {} on {} (historical)"
                    .format(len(data_this_day) - old_pings, d_addr, datestamp))
                w_stop_time = w_start_time
                days_considered += 1

        for datestamp, data_this_day in historical_data.items():
            for probeid, minrtt in data_this_day.items():
                new_data.append((probeid, datestamp, minrtt))
                if probeid in earliest_time_for_probe:
                    del earliest_time_for_probe[probeid]

    if earliest_time_for_probe:
        progress("{} probes have no data at all for {}"
                 .format(len(earliest_time_for_probe), d_addr))

    if no_data_for_yesterday:
        new_data.extend((pid, yesterday, None)
                        for pid in no_data_for_yesterday)

    if new_data:
        progress("recording {} new observations for {}"
                 .format(len(new_data), d_addr))
        with state.db, state.db.cursor() as cur:
            execute_values(
                cur, """
                    INSERT INTO calibration_rtts (s_id, d_id, odate, minrtt)
                    VALUES %s
                    ON CONFLICT DO NOTHING;
                """, new_data,
                template="(%s, {}, %s, %s)".format(anchorid))

#
# Computing CBG calibrations from round-trip times.
# This step only talks to the database.
#

# half of the equatorial circumference of the Earth, in meters
# it is impossible for the target to be farther away than this
DISTANCE_LIMIT = 20037508

# maximum meaningful RTT to that distance (ms)
# this is twice the amount of time it takes a radio transmission
# to reach geostationary orbit and come back; any RTT longer than
# this could have gone from anywhere to anywhere
TIME_LIMIT = 477.48

def discard_infeasible(obs):
    """Discard infeasible observations from OBS, which is expected to be a
       N-by-2 matrix where the first column is distances and the second
       column is round-trip times.  Returns a subset matrix.

       An observation is infeasible if it implies a propagation speed
       faster than 200,000 km/s.  We also discard all observations at
       distance < 1000m, because these tend to make 'linprog' barf.
    """
    if len(obs.shape) != 2:
        raise ValueError("OBS should be a 2D matrix, not {}D"
                         .format(len(obs.shape)))

    if obs.shape[0] == 0 or obs.shape[1] != 2:
        raise ValueError("OBS should be an N-by-2 matrix, not {}-by-{}"
                         .format(*obs.shape))

    feasible = np.logical_and(
        obs[:,0] >= 1000,
        obs[:,1] * (100 * 1000) >= obs[:,0]
    )
    fobs = obs[feasible,:]
    return fobs[np.lexsort((fobs[:,1], fobs[:,0])),:]

def calibrate_cbg_for_block(sid, block):
    obs = discard_infeasible(np.array(block))
    if obs.shape[0] == 0:
        return [] # not enough feasible observations, can't calibrate

    dists = obs[:,0]
    rtts  = obs[:,1]

    # The goal is to find m, b that minimize \sum_i (y_i - mx_i - b)
    # while still satisfying y_i \ge mx_i + b for all i.
    # As a linear programming problem, this corresponds to minimizing
    # 1Y - mX - bI where Y = \sum_i y_i, X = \sum_i x_i, I = \sum_i 1.
    # The data constraints take the form 0·1 + x_i·m + 1·b \le y_i.
    #
    # We also impose physical constraints:
    #   m >= 1/100000      200,000 km/s physical speed limit
    #   b >= 0             negative fixed delays don't make sense
    #   b <= min(rtts)     otherwise the fit will not work
    #
    # Finally, we add an artificial data constraint:
    #   x_limit = 20037508  half of Earth's equatorial circumference
    #   y_limit = 477.48    empirical "slowest plausible" time to
    #                       traverse that distance (see above)
    #
    # This last ensures that the fit will not select a data point from
    # a satellite link as a defining point for the line.
    cx = np.append(dists, DISTANCE_LIMIT)
    cy = np.append(rtts, TIME_LIMIT)

    coef = np.array([np.sum(cy), -np.sum(cx), -len(cx)])
    constr_A = np.column_stack((
        np.zeros_like(cx), cx, np.ones_like(cx)
    ))
    constr_B = np.column_stack((cy,))
    bounds = [(1,1), (1/(100*1000), None), (0, np.amin(cy))]

    fit = optimize.linprog(coef,
                           A_ub=constr_A,
                           b_ub=constr_B,
                           bounds=bounds)
    if not fit.success:
        return []

    # If the latency at distance 0 is larger than 50 ms, we don't
    # believe the fit.  (This probably means there were no observations
    # at a relatively short distance.)
    if fit.x[2] > 50:
        return []

    # The linear program found a "bestline", mapping distance to
    # latency.  The "max curve" is the inverse function of this
    # bestline, mapping latency to distance.  Coefficient 0 of the
    # fit is a dummy.
    m = 1/fit.x[1]
    b = -m * fit.x[2]
    return [(sid, m, b)]

def calibrate_cbg():
    """Worker function: calculate CBG calibration "bestlines" for each
       probe."""
    state = get_worker_state()
    with state.db:
        progress("computing bestlines")
        with state.db.cursor(name='cbgcal_read_{:016x}'.format(
                random.randint(0, sys.maxsize))) as cur:
            cur.itersize = 1024
            cur.execute("""
                SELECT l.probeid,
                       ST_Distance(l.location, m.location) AS distance,
                       n.median AS rtt
                  FROM landmarks l, landmarks m,
                       (SELECT s_id, d_id, median(minrtt)
                          FROM (SELECT *
                                  FROM (SELECT s_id, d_id, odate, minrtt,
                                               rank() OVER (
                                                   PARTITION BY s_id, d_id
                                                   ORDER BY odate DESC)
                                          FROM calibration_rtts
                                         WHERE minrtt IS NOT NULL) _a
                                 WHERE rank <= 14) _b
                      GROUP BY s_id, d_id) n
                WHERE l.probeid = n.s_id and m.anchorid = n.d_id
            """)

            results = []
            block = None
            prev_sid = None
            for row in cur:
                if row[0] != prev_sid:
                    if prev_sid is not None:
                        results.extend(calibrate_cbg_for_block(prev_sid, block))
                    prev_sid = row[0]
                    block = []
                block.append((row[1], row[2]))

            if prev_sid is not None:
                results.extend(calibrate_cbg_for_block(prev_sid, block))

        progress("recording bestlines for {} probes".format(len(results)))
        with state.db.cursor() as cur:
            execute_values(
                cur,
                """
                    UPDATE landmarks AS l SET cbg_m = d.m, cbg_b = d.b
                      FROM (VALUES %s) AS d (id, m, b)
                     WHERE l.probeid = d.id
                """,
                results)

#
# Master control
#

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dbname",
                    help="Name of the database to record all information in.")
    args = ap.parse_args()

    global _dbname
    _dbname = args.dbname

    progress("starting up...")
    pool = multiprocessing.Pool()

    # The initial retrieval of the probe list has to be done serially.
    anchor_data = pool.apply(retrieve_active_probes_and_anchors)

    # For each anchor, update the list of available measurements
    # targeting it.
    progress("retrieving measurements targeting all anchors...")
    pool.map(retrieve_anchor_ping_measurements, anchor_data, chunksize=40)

    # For each anchor, retrieve ping times targeting it.
    pool.map(retrieve_anchor_ping_results, anchor_data, chunksize=40)

    calibrate_cbg()
    progress("done")

if __name__ == '__main__':
    main()
