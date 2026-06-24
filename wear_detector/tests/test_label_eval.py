# ABOUTME: Tests for the label-eval harness — labels.csv parsing and the per-clip feature bank.
# ABOUTME: A synthetic WAV + CSV keep it hermetic; AUC itself is covered in test_imu_band_probe.
import wave

import numpy as np

from wear_detector import label_eval


def _wav(path, secs=30.0, fs=16000):
    n = int(secs * fs)
    x = (0.1 * np.random.default_rng(0).standard_normal(n) * 32767).astype("<i2")
    x = np.repeat(x[:, None], 2, axis=1).reshape(-1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(fs); w.writeframes(x.tobytes())
    return str(path)


def test_load_labels_skips_blank_and_parses_fields(tmp_path):
    p = tmp_path / "labels.csv"
    p.write_text("clip,t_start_s,t_end_s,fault,type,notes\n"
                 "1,0.0,5.0,0,-,bg\n"
                 ",,,,,\n"                       # blank row skipped
                 "2,5.0,10.0,1,squeal,loud\n")
    rows = label_eval.load_labels(str(p))
    assert len(rows) == 2
    assert rows[1] == ("2", 5.0, 10.0, 1, "squeal", "loud")


def test_clip_features_has_expected_keys(tmp_path):
    import numpy as np
    x, fs = np.zeros(16000 * 2), 16000
    x[:] = 0.1 * np.random.default_rng(1).standard_normal(len(x))
    f = label_eval.clip_features(x, fs, 0.0, 1.0)
    for k in ("rms", "crest", "kurtosis", "tonal", "hf_ratio", "centroid", "band_3000_4000"):
        assert k in f


def test_evaluate_returns_auc_per_feature(tmp_path):
    wav = _wav(tmp_path / "a.wav")
    csv = tmp_path / "labels.csv"
    # loud (fault) vs quiet by construction would need amplitude diffs; here just check plumbing
    csv.write_text("clip,t_start_s,t_end_s,fault,type,notes\n"
                   "1,0,5,0,-,\n2,5,10,1,rattle,\n3,10,15,0,-,\n4,15,20,1,squeal,\n")
    r = label_eval.evaluate(str(wav), str(csv)) if False else label_eval.evaluate(str(csv), wav)
    assert r["n_fault"] == 2 and r["n_ok"] == 2
    assert "rms" in r["aucs"] and 0.0 <= r["aucs"]["rms"] <= 1.0
