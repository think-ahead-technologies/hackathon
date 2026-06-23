# ABOUTME: Unit tests for the bridge's pure logic — payload parsing and the alert decision.
# ABOUTME: No DB/NATS here; the IO layer is exercised end-to-end by the docker stack.

import json
from datetime import datetime

import pytest

from main import (
    deploy_subject,
    label_subject,
    next_model_version,
    parse_deploy,
    parse_inference,
    parse_label,
    parse_ts,
    should_open_alert,
    should_queue_retrain,
)


@pytest.mark.parametrize(
    "raw",
    ["2026-06-15T10:00:00+00:00", "2026-06-15T10:00:00Z", "2026-06-15T10:00:00"],
)
def test_parse_ts_returns_aware_datetime(raw):
    dt = parse_ts(raw)
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None  # asyncpg needs an aware datetime for timestamptz


def test_parse_inference_accepts_contract_b():
    payload = json.dumps(
        {"ts": "2026-06-15T10:00:00Z", "container_id": "cnc-7",
         "model_version": "v1", "anomaly_score": "0.71", "fault_class": None}
    ).encode()
    msg = parse_inference(payload)
    assert msg["container_id"] == "cnc-7"
    assert msg["anomaly_score"] == 0.71  # coerced to float


def test_parse_inference_rejects_missing_score():
    with pytest.raises(ValueError):
        parse_inference(json.dumps({"ts": "2026-06-15T10:00:00Z"}).encode())


def test_parse_label_accepts_contract_d():
    payload = json.dumps(
        {"ts": "2026-06-15T10:00:00Z", "container_id": "cnc-7",
         "feature_window_ref": "w-42", "label": "bearing wear"}
    ).encode()
    msg = parse_label(payload)
    assert msg["label"] == "bearing wear"


def test_parse_label_rejects_missing_container():
    with pytest.raises(ValueError):
        parse_label(json.dumps({"ts": "x", "label": "imbalance"}).encode())


@pytest.mark.parametrize(
    "score,threshold,has_open,expected",
    [
        (0.82, 0.60, False, True),    # over threshold, nothing open -> open
        (0.82, 0.60, True, False),    # over threshold but already open -> no dupe
        (0.55, 0.60, False, False),   # under threshold -> no alert
        (0.60, 0.60, False, True),    # exactly at threshold -> open (>=)
    ],
)
def test_should_open_alert(score, threshold, has_open, expected):
    assert should_open_alert(score, threshold, has_open) is expected


def test_label_subject_shape():
    assert label_subject("line1", "cnc-7") == "labels.line1.cnc-7"


def test_deploy_subject_shape():
    assert deploy_subject("line1") == "models.line1.deploy"


def test_parse_deploy_accepts_contract_c():
    msg = parse_deploy(json.dumps({"model_version": "v2", "detail": "x"}).encode())
    assert msg["model_version"] == "v2"


def test_parse_deploy_rejects_missing_version():
    with pytest.raises(ValueError):
        parse_deploy(json.dumps({"detail": "no version"}).encode())


@pytest.mark.parametrize(
    "new_labels,threshold,expected",
    [(1, 1, True), (0, 1, False), (3, 5, False), (5, 5, True)],
)
def test_should_queue_retrain(new_labels, threshold, expected):
    assert should_queue_retrain(new_labels, threshold) is expected


def test_next_model_version_increments():
    assert next_model_version(0) == "pdm-anomaly@retrained-1"
    assert next_model_version(2) == "pdm-anomaly@retrained-3"
