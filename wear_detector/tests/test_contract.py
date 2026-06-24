# ABOUTME: Unit tests for the Contract B stage machine and payload shape.
# ABOUTME: Covers dwell escalation, hysteresis on release, trend, and §7 field schema.
from wear_detector.contract import (ADVANCED, ESTABLISHED, HEALTHY, WATCH,
                                    StageMachine, contract_b, fuse)

CONTRACT_FIELDS = {"ts", "container_id", "model_version", "anomaly_score",
                   "acoustic_score", "vibration_score", "severity_stage",
                   "track_position", "fault_locus", "trend"}


def test_payload_has_exact_contract_b_fields():
    rec = contract_b(ts=1.0, container_id="42", model_version="m", v=0.8, stage=2, trend="rising")
    assert set(rec) == CONTRACT_FIELDS
    assert rec["acoustic_score"] is None       # no acoustic sense yet
    assert rec["fault_locus"] == "unknown"     # no localization yet
    assert rec["track_position"] is None


def test_fuse_is_max_of_available_senses():
    assert fuse(0.7) == 0.7
    assert fuse(0.7, 0.9) == 0.9
    assert contract_b(ts=0, container_id="x", model_version="m", v=0.7, a=0.9)["anomaly_score"] == 0.9


def test_healthy_stream_stays_stage_zero():
    m = StageMachine(v_thr=0.95)
    stages = [m.update(0.3) for _ in range(50)]
    assert set(stages) == {HEALTHY}


def test_watch_is_immediate_then_dwell_escalates_to_established():
    m = StageMachine(v_thr=0.9, dwell=6)
    assert m.update(0.95) == WATCH          # first over-threshold window flags immediately
    last = WATCH
    for _ in range(10):
        last = m.update(0.95)
    assert last == ESTABLISHED              # sustained -> established (no rising trend on flat input)


def test_rising_sustained_reaches_advanced():
    m = StageMachine(v_thr=0.5, dwell=4, trend_eps=1e-4)
    stage = HEALTHY
    for v in [x / 100 for x in range(60, 100)]:  # steadily rising, all above thr
        stage = m.update(v)
    assert stage == ADVANCED
    assert m.trend() == "rising"


def test_hysteresis_holds_before_release():
    m = StageMachine(v_thr=0.9, dwell=4, release=6)
    for _ in range(10):
        m.update(0.99)
    assert m.stage == ESTABLISHED
    # a couple of low windows must NOT immediately drop it
    m.update(0.1); m.update(0.1)
    assert m.stage == ESTABLISHED
    for _ in range(6):
        m.update(0.1)
    assert m.stage == HEALTHY               # sustained drop releases
