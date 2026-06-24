-- ABOUTME: TimescaleDB schema for the edge ops dashboard — scores, alerts, labels.
-- ABOUTME: Provisioned as code (Contract B telemetry + Contract D labels), not clicked together.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Contract B telemetry lands here, one row per inference message.
CREATE TABLE IF NOT EXISTS scores (
  ts            timestamptz       NOT NULL,
  container_id  text,
  model_version text,
  anomaly_score double precision,
  fault_class   text,
  location      text
);
SELECT create_hypertable('scores', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS scores_container_ts ON scores (container_id, ts DESC);

-- 48h retention echoes the on-device ring-buffer story; plenty for the demo.
SELECT add_retention_policy('scores', INTERVAL '48 hours', if_not_exists => TRUE);

-- One open alert per container while it stays over threshold; flips to labeled
-- when an operator labels it (Contract D), or dismissed from the label UI.
CREATE TABLE IF NOT EXISTS alerts (
  id            bigserial PRIMARY KEY,
  ts            timestamptz,
  container_id  text,
  anomaly_score double precision,
  state         text DEFAULT 'unlabeled',   -- unlabeled | labeled | dismissed
  label         text,
  labeled_at    timestamptz
);
CREATE INDEX IF NOT EXISTS alerts_open ON alerts (container_id) WHERE state = 'unlabeled';

-- The labeled-data store ML retrains from (Contract D).
CREATE TABLE IF NOT EXISTS labels (
  ts                 timestamptz,
  container_id       text,
  feature_window_ref text,
  label              text
);

-- Directed-gather training data (Contract E): windows the device captured on command,
-- binned by label ("healthy" baseline vs. a fault class) and segment for retraining.
CREATE TABLE IF NOT EXISTS captures (
  ts           timestamptz NOT NULL DEFAULT now(),
  request_id   text,
  container_id text,
  label        text,
  segment      text,
  seq          int,
  features_b64 text
);
CREATE INDEX IF NOT EXISTS captures_req ON captures (request_id, seq);

-- Model lifecycle events that close the loop: a retrain queued from accumulated
-- labels, and a deploy delivered over Contract C (models.<line>.deploy).
CREATE TABLE IF NOT EXISTS model_events (
  ts            timestamptz NOT NULL DEFAULT now(),
  line          text,
  event_type    text,        -- retrain_queued | deployed
  model_version text,
  detail        text
);
CREATE INDEX IF NOT EXISTS model_events_ts ON model_events (ts DESC);
