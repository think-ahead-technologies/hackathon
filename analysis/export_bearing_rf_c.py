"""Train the bearing RandomForest reference model and export C inference tables.

The analysis notebooks/scripts train RandomForest models in-process for metrics. This
script freezes the bearing detector into deterministic C arrays so firmware can run
the same tree ensemble without sklearn.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA_ROOT = REPO / "data" / "provided" / "thingkathon_kickstart" / "thinkathon_kickstart" / "data"

sys.path.insert(0, str(HERE))
import imloader as L  # noqa: E402
import features as F  # noqa: E402

N_ESTIMATORS = 150
MAX_DEPTH = 10
RANDOM_STATE = 0


def build_dataset():
    L.DATA_ROOT = str(DATA_ROOT)
    xs, ys, groups = [], [], []
    sessions = F.FAULT_SESSIONS + F.NORMAL_SESSIONS
    for session_index, name in enumerate(sessions):
        session_dir = os.path.join(L.DATA_ROOT, name)
        imu = L.load_imu(session_dir)
        if imu is None:
            raise FileNotFoundError(f"missing IMU data for {name}")
        fs = L.imu_fs(imu)
        feats, centers = F.extract_windows(imu, fs)
        labels = L.load_labels(session_dir)
        y = F.window_labels(centers, labels)
        xs.append(feats)
        ys.append(y)
        groups.append(np.full(len(feats), session_index, dtype=np.int32))
    return np.vstack(xs), np.concatenate(ys), np.concatenate(groups), sessions


def new_classifier():
    return RandomForestClassifier(
        n_estimators=N_ESTIMATORS,
        max_depth=MAX_DEPTH,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )


def train_final_model(x, y):
    return new_classifier().fit(x, y)


def rates(y, score, threshold):
    pred = score >= threshold
    pos = y == 1
    neg = y == 0
    fpr = np.sum(pred[neg]) / max(int(np.sum(neg)), 1)
    fnr = np.sum(~pred[pos]) / max(int(np.sum(pos)), 1)
    return float(fpr), float(fnr)


def pick_high_recall_threshold(x, y, groups):
    fault_sessions = sorted(set(groups[y == 1]))
    oof = np.full(len(y), np.nan)
    for held in fault_sessions:
        train = groups != held
        test = groups == held
        clf = new_classifier().fit(x[train], y[train])
        oof[test] = clf.predict_proba(x[test])[:, 1]

    keep = ~np.isnan(oof)
    yy = y[keep]
    score = oof[keep]
    thresholds = np.unique(score)
    if len(thresholds) > 600:
        thresholds = np.quantile(score, np.linspace(0, 1, 600))
    fprs = np.array([rates(yy, score, t)[0] for t in thresholds])
    fnrs = np.array([rates(yy, score, t)[1] for t in thresholds])
    ok = np.where(fnrs <= 0.10)[0]
    if len(ok):
        index = ok[np.argmin(fprs[ok])]
    else:
        index = np.argmax((1.0 - fnrs) - fprs)
    return float(thresholds[index]), float(fprs[index]), float(fnrs[index])


def rf_arrays(clf):
    node_feature = []
    node_left = []
    node_right = []
    node_threshold = []
    node_probability = []
    tree_roots = []

    for estimator in clf.estimators_:
        tree = estimator.tree_
        offset = len(node_feature)
        tree_roots.append(offset)
        for node in range(tree.node_count):
            left = int(tree.children_left[node])
            right = int(tree.children_right[node])
            feature = int(tree.feature[node])
            if left >= 0:
                left += offset
            if right >= 0:
                right += offset
            values = tree.value[node, 0]
            total = float(values.sum())
            probability = float(values[1] / total) if total > 0.0 else 0.0
            node_feature.append(feature)
            node_left.append(left)
            node_right.append(right)
            node_threshold.append(float(tree.threshold[node]))
            node_probability.append(probability)

    return {
        "tree_roots": tree_roots,
        "node_feature": node_feature,
        "node_left": node_left,
        "node_right": node_right,
        "node_threshold": node_threshold,
        "node_probability": node_probability,
    }


def fmt_int_array(name, c_type, values, per_line=12):
    lines = [f"static const {c_type} {name}[] = {{"]
    for start in range(0, len(values), per_line):
        chunk = values[start : start + per_line]
        lines.append("    " + ", ".join(str(int(v)) for v in chunk) + ",")
    lines.append("};")
    return "\n".join(lines)


def fmt_float_array(name, values, per_line=6):
    lines = [f"static const float {name}[] = {{"]
    for start in range(0, len(values), per_line):
        chunk = values[start : start + per_line]
        literals = []
        for value in chunk:
            literal = f"{float(value):.9g}"
            if "." not in literal and "e" not in literal and "E" not in literal:
                literal += ".0"
            literals.append(literal + "f")
        lines.append("    " + ", ".join(literals) + ",")
    lines.append("};")
    return "\n".join(lines)


def write_header(path: Path, node_count: int, threshold: float):
    names = ",\n".join(f'    "{name}"' for name in F.FEATURE_NAMES)
    path.write_text(
        f"""// ABOUTME: Bearing RandomForest detector API exported from analysis/export_bearing_rf_c.py.
// ABOUTME: Scores the 10-window feature vector used by analysis/features.py.

#ifndef BEARING_RF_H
#define BEARING_RF_H

#ifdef __cplusplus
extern "C" {{
#endif

#define BEARING_RF_FEATURE_COUNT {len(F.FEATURE_NAMES)}
#define BEARING_RF_TREE_COUNT {N_ESTIMATORS}
#define BEARING_RF_NODE_COUNT {node_count}
#define BEARING_RF_THRESHOLD {threshold:.9g}f

typedef enum {{
    BEARING_RF_STATUS_OK = 0,
    BEARING_RF_STATUS_FAULT = 1,
    BEARING_RF_STATUS_INVALID_INPUT = 2,
}} bearing_rf_status_t;

typedef struct {{
    bearing_rf_status_t status;
    float fault_percent;
    float score;
}} bearing_rf_result_t;

extern const char *const BEARING_RF_FEATURE_NAMES[BEARING_RF_FEATURE_COUNT];

float bearing_rf_score(const float features[BEARING_RF_FEATURE_COUNT]);
bearing_rf_result_t bearing_rf_detect_features(const float features[BEARING_RF_FEATURE_COUNT]);

#ifdef __cplusplus
}}
#endif

#endif  // BEARING_RF_H
""",
        encoding="utf-8",
    )
    return names


def write_source(path: Path, arrays, threshold: float):
    feature_names = ",\n".join(f'    "{name}"' for name in F.FEATURE_NAMES)
    source = f"""// ABOUTME: Generated compact bearing RandomForest model and C inference.
// ABOUTME: Regenerate with: python analysis/export_bearing_rf_c.py

#include "bearing_rf.h"

#include <math.h>
#include <stdint.h>

const char *const BEARING_RF_FEATURE_NAMES[BEARING_RF_FEATURE_COUNT] = {{
{feature_names}
}};

{fmt_int_array("TREE_ROOTS", "uint32_t", arrays["tree_roots"])}

{fmt_int_array("NODE_FEATURE", "int16_t", arrays["node_feature"])}

{fmt_int_array("NODE_LEFT", "int32_t", arrays["node_left"])}

{fmt_int_array("NODE_RIGHT", "int32_t", arrays["node_right"])}

{fmt_float_array("NODE_THRESHOLD", arrays["node_threshold"])}

{fmt_float_array("NODE_PROBABILITY", arrays["node_probability"])}

static int valid_features(const float features[BEARING_RF_FEATURE_COUNT]) {{
    if (!features) return 0;
    for (int i = 0; i < BEARING_RF_FEATURE_COUNT; i++) {{
        if (!isfinite((double)features[i])) return 0;
    }}
    return 1;
}}

float bearing_rf_score(const float features[BEARING_RF_FEATURE_COUNT]) {{
    if (!valid_features(features)) return -1.0f;
    double sum = 0.0;
    for (int tree = 0; tree < BEARING_RF_TREE_COUNT; tree++) {{
        uint32_t node = TREE_ROOTS[tree];
        while (NODE_LEFT[node] >= 0) {{
            const int feature = NODE_FEATURE[node];
            node = ((double)features[feature] <= (double)NODE_THRESHOLD[node])
                ? (uint32_t)NODE_LEFT[node]
                : (uint32_t)NODE_RIGHT[node];
        }}
        sum += NODE_PROBABILITY[node];
    }}
    return (float)(sum / (double)BEARING_RF_TREE_COUNT);
}}

bearing_rf_result_t bearing_rf_detect_features(const float features[BEARING_RF_FEATURE_COUNT]) {{
    bearing_rf_result_t result;
    const float score = bearing_rf_score(features);
    if (score < 0.0f) {{
        result.status = BEARING_RF_STATUS_INVALID_INPUT;
        result.fault_percent = 0.0f;
        result.score = 0.0f;
        return result;
    }}
    result.status = score >= {threshold:.9g}f ? BEARING_RF_STATUS_FAULT : BEARING_RF_STATUS_OK;
    result.fault_percent = score >= 1.0f ? 100.0f : score * 100.0f;
    result.score = score;
    return result;
}}
"""
    path.write_text(source, encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--header", type=Path, default=REPO / "firmware" / "include" / "bearing_rf.h")
    parser.add_argument("--source", type=Path, default=REPO / "firmware" / "src" / "bearing_rf.c")
    return parser.parse_args()


def main():
    args = parse_args()
    x, y, groups, sessions = build_dataset()
    threshold, fpr, fnr = pick_high_recall_threshold(x, y, groups)
    clf = train_final_model(x, y)
    arrays = rf_arrays(clf)
    args.header.parent.mkdir(parents=True, exist_ok=True)
    args.source.parent.mkdir(parents=True, exist_ok=True)
    write_header(args.header, len(arrays["node_feature"]), threshold)
    write_source(args.source, arrays, threshold)
    print(f"training_windows={len(x)} faults={int(y.sum())} sessions={len(sessions)}")
    print(f"rf_trees={N_ESTIMATORS} max_depth={MAX_DEPTH} nodes={len(arrays['node_feature'])}")
    print(f"threshold={threshold:.9g} loso_fpr={fpr:.4f} loso_fnr={fnr:.4f}")
    print(f"wrote {args.header}")
    print(f"wrote {args.source}")


if __name__ == "__main__":
    main()