# ABOUTME: Unit tests for the anomaly-localizer's pure join logic — no NATS, no hardware.

import json

import main


ANOMALY = {
    "ts": "2026-06-15T10:00:00Z",
    "container_id": "cnc-7",
    "model_version": "pdm-anomaly@2026.06.15-a3f1",
    "anomaly_score": 0.83,
    "fault_class": None,
    "location": "spindle",
    "data_classification": "inference",
    "bytes": 47,
}

# Host µs of ANOMALY["ts"] (2026-06-15T10:00:00Z) so the join age is 0 in the happy path.
TS_US = 1_781_517_600_000_000

FIX = {
    "t_us": 123456789,
    "t_host_us": TS_US,
    "segment": "line1.left",
    "x": 0.0,
    "y": 1.83,
}


def test_parse_subject_handles_hyphenated_kind():
    assert main.parse_subject("edge.localized-anomaly.line1.cnc-7") == ("line1", "cnc-7")
    assert main.parse_subject("edge.anomaly.line1.cnc-7") == ("line1", "cnc-7")


def test_ts_to_unix_us():
    assert main.ts_to_unix_us("2026-06-15T10:00:00Z") == TS_US
    assert main.ts_to_unix_us("2026-06-15T10:00:00+00:00") == TS_US
    assert main.ts_to_unix_us(None) is None
    assert main.ts_to_unix_us("not-a-date") is None


def test_localize_stamps_position():
    out = main.localize(ANOMALY, FIX)
    # pass-through preserved
    assert out["anomaly_score"] == 0.83
    assert out["container_id"] == "cnc-7"
    assert out["location"] == "spindle"          # machine component, untouched
    # location added from the fix
    assert out["segment"] == "line1.left"        # floor zone
    assert out["x"] == 0.0 and out["y"] == 1.83
    assert out["pos_t_host_us"] == TS_US
    assert out["pos_age_ms"] == 0                # anomaly ts == fix host time here
    # gate field forced
    assert out["data_classification"] == "inference"


def test_localize_no_fix_yields_null_location_but_still_emits():
    out = main.localize(ANOMALY, None)
    assert out["segment"] is None
    assert out["x"] is None and out["y"] is None
    assert out["pos_t_host_us"] is None and out["pos_age_ms"] is None
    assert out["anomaly_score"] == 0.83          # anomaly never dropped
    assert out["data_classification"] == "inference"


def test_pos_age_ms_is_anomaly_minus_fix():
    older = dict(FIX, t_host_us=TS_US - 250_000)   # fix 250 ms before the anomaly
    out = main.localize(ANOMALY, older)
    assert out["pos_age_ms"] == 250


def test_bytes_equals_wire_size():
    out = main.localize(ANOMALY, FIX)
    assert out["bytes"] == len(json.dumps(out).encode())


def test_classification_forced_even_if_input_lies():
    sneaky = dict(ANOMALY, data_classification="raw")
    out = main.localize(sneaky, FIX)
    assert out["data_classification"] == "inference"
