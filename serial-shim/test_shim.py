# ABOUTME: Unit tests for the serial→NATS shim's pure logic — line parsing and payload shaping.
# ABOUTME: No serial port or NATS here; the IO layer is exercised by attaching a real board.

import json

import pytest

from shim import edge_subject, parse_line, to_edge_payload


def _line(obj: dict) -> bytes:
    """A device 'println' of a Contract B object, as it arrives off the wire."""
    return (json.dumps(obj) + "\r\n").encode()


def test_parse_line_accepts_contract_b():
    msg = parse_line(_line({
        "ts": "2026-06-15T10:00:00Z", "container_id": "cnc-7",
        "model_version": "pdm-anomaly@2026.06.15-a3f1", "anomaly_score": 0.83,
    }))
    assert msg["container_id"] == "cnc-7"
    assert msg["anomaly_score"] == 0.83


def test_parse_line_coerces_string_score_to_float():
    msg = parse_line(_line({"container_id": "cnc-7", "anomaly_score": "0.71"}))
    assert msg["anomaly_score"] == 0.71


@pytest.mark.parametrize(
    "raw",
    [
        b"",                              # idle read / serial timeout
        b"   \r\n",                       # blank line
        b"[boot] sensors online\r\n",     # firmware boot banner (not JSON)
        b"\xff\x80\x01garbage",           # baud-rate mismatch → undecodable bytes
        b"[1, 2, 3]\r\n",                 # JSON, but not an object
        b'{"ts":"x","anomaly_score":0.5}\r\n',        # missing container_id
        b'{"ts":"x","container_id":"cnc-7"}\r\n',     # missing anomaly_score
        b'{"container_id":"cnc-7","anomaly_score":"NaNish"}\r\n',  # uncoercible score
    ],
)
def test_parse_line_skips_junk(raw):
    # The shim is the first line of defense against messy serial: skip, don't crash.
    assert parse_line(raw) is None


def test_edge_subject_routes_through_the_boundary_gateway():
    # edge.* is the device data plane Vector consumes — not inference.* directly.
    assert edge_subject("line1", "cnc-7") == "edge.line1.cnc-7"


def test_to_edge_payload_classifies_as_inference_and_records_wire_bytes():
    msg = {"container_id": "cnc-7", "anomaly_score": 0.83}
    out = to_edge_payload(msg, 47)
    assert out["data_classification"] == "inference"  # the only class Vector egresses
    assert out["bytes"] == 47                          # real wire size, for the audit metric
    assert out["container_id"] == "cnc-7"              # original fields preserved


def test_to_edge_payload_does_not_mutate_input():
    msg = {"container_id": "cnc-7", "anomaly_score": 0.83}
    to_edge_payload(msg, 47)
    assert "data_classification" not in msg
