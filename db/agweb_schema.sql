-- Schema definition for the database of landmarks.
-- Note: this database and its applications require the PostGIS and
-- ORAFCE extensions.  (ORAFCE is only used for the 'median' aggregator.)

-- Each probe is assigned to a region.  This is mainly used by the
-- code that selects anchors for the coarse phase.  Note: the box
-- column is GEOMETRY, not GEOGRAPHY, because it will contain
-- parallels-and-meridians boxes, not great-circle boxes.  Points from
-- the landmarks table will be converted to GEOMETRY points before
-- comparison.
CREATE TABLE regions (
  id          SERIAL             NOT NULL PRIMARY KEY,
  name        TEXT               NOT NULL UNIQUE,
  box         GEOMETRY(POLYGON)  NOT NULL,
  lm_centroid GEOGRAPHY(POINT)
);
-- These boxes are chosen to roughly enclose all populated terrain and
-- draw useful dividing lines between groups of anchors, while staying
-- as simple as possible.  The poles are excluded to avoid problems with
-- projection singularities.
INSERT INTO regions (name, box) VALUES
    ('north.america',    ST_MakeEnvelope(-180,  20, -30, 85, 4326)),
    ('south.america',    ST_MakeEnvelope(-180, -60, -30, 20, 4326)),
    ('europe.n.africa',  ST_MakeEnvelope( -30,  20,  60, 85, 4326)),
    ('subsahara.africa', ST_MakeEnvelope( -30, -60,  60, 20, 4326)),
    ('north.east.asia',  ST_MakeEnvelope(  60,  30, 180, 85, 4326)),
    ('south.asia',       ST_MakeEnvelope(  60, -60, 130, 30, 4326)),
    ('oceania',          ST_MakeEnvelope( 130, -60, 180, 30, 4326));
CREATE INDEX regions__box ON regions USING GIST(box);

-- This is the main table, containing all known probes with their
-- locations and CBG parameters.
CREATE TABLE landmarks (
  probeid  INTEGER          NOT NULL PRIMARY KEY,
  anchorid INTEGER          UNIQUE,   -- NULL if not an anchor
  addr     INET             NOT NULL, -- there can be more than one probe per address
  usable   BOOLEAN          NOT NULL, -- can we ping it?
  location GEOGRAPHY(POINT) NOT NULL,
  region   INTEGER          REFERENCES regions(id),
  cbg_m    REAL,
  cbg_b    REAL
);
CREATE INDEX landmarks_addr_key ON landmarks(addr);
CREATE INDEX landmarks_location_key ON landmarks USING GIST(location);

-- Calibration data: minimum observed RTT for each (probe, anchor)
-- pair, at daily intervals.  The computation of cbg_m and cbg_b
-- uses the median value observed over the past two weeks, to exclude
-- outliers due to routing flaps.
CREATE TABLE calibration_rtts (
    s_id   INTEGER NOT NULL REFERENCES landmarks(probeid),
    d_id   INTEGER NOT NULL REFERENCES landmarks(anchorid),
    odate  DATE    NOT NULL,
    minrtt REAL,
    UNIQUE (s_id, d_id, odate)
);

-- Ancillary table which records the RIPE Atlas "measurements" whose
-- data has already been logged in calibration_rtts.
CREATE TABLE ripe_measurements (
   d_addr      INET      NOT NULL,
   meas_id     INTEGER   PRIMARY KEY,
   start_time  TIMESTAMP NOT NULL,
   stop_time   TIMESTAMP,          -- NULL = ongoing
   probes      INTEGER ARRAY       -- probe ids involved in this measurement
                                   -- may include probes not in the landmarks table
);
CREATE INDEX ripe_measurements__d_addr ON ripe_measurements(d_addr);

-- Local Variables:
-- sql-product: postgres
-- End:
