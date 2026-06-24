-- ABOUTME: Seed data so the dashboard never opens empty on a cold demo start.
-- ABOUTME: A flat-then-rising anomaly curve for two containers over the last 15 minutes.

-- cnc-7: flat healthy baseline that ramps over threshold near "now".
INSERT INTO scores (ts, container_id, model_version, anomaly_score, fault_class, location)
SELECT
  now() - (g || ' seconds')::interval,
  'cnc-7',
  'pdm-anomaly@2026.06.15-a3f1',
  CASE
    WHEN g > 180 THEN 0.18 + 0.015 * g / 60.0                 -- flat early
    ELSE GREATEST(0.05, 0.95 - 0.0035 * g)                    -- rising toward now
  END,
  NULL,
  'spindle'
FROM generate_series(0, 900, 5) AS g;

-- press-3: stays healthy the whole window (the teal control container).
INSERT INTO scores (ts, container_id, model_version, anomaly_score, fault_class, location)
SELECT
  now() - (g || ' seconds')::interval,
  'press-3',
  'pdm-anomaly@2026.06.15-a3f1',
  0.20 + 0.05 * sin(g / 30.0),
  NULL,
  'ram'
FROM generate_series(0, 900, 5) AS g;

-- One open alert so the feed + stat tiles have something to show immediately.
INSERT INTO alerts (ts, container_id, anomaly_score, state)
VALUES (now() - interval '20 seconds', 'cnc-7', 0.82, 'unlabeled');

-- Conveyor track: one healthy-ish score per segment so the track view's heatmap
-- isn't blank on a cold start. seg-4 rides over threshold with a model-flagged fault.
INSERT INTO scores (ts, container_id, model_version, anomaly_score, fault_class, location)
SELECT
  now() - interval '5 seconds',
  'seg-' || g,
  'pdm-anomaly@2026.06.15-a3f1',
  CASE WHEN g = 4 THEN 0.79 ELSE 0.18 + 0.10 * abs(sin(g::float)) END,
  CASE WHEN g = 4 THEN 'bearing wear' ELSE NULL END,
  'seg-' || g
FROM generate_series(1, 17) AS g;

-- An open alert on the flagged segment so the track shows the live alert ring.
INSERT INTO alerts (ts, container_id, anomaly_score, state)
VALUES (now() - interval '5 seconds', 'seg-4', 0.79, 'unlabeled');
