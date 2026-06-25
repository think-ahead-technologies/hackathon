# ABOUTME: Generates silver fault labels for unlabeled fault sessions via the analysis RF labeler.
# ABOUTME: Writes silver_labels/<session>.csv (start,end spans) that dataset.py folds into training.
import csv
import os

import numpy as np

import dataset
import rf_labeler
import imloader as L


def main():
    os.makedirs(dataset.SILVER_DIR, exist_ok=True)
    # Precision operating point + persistence: clean fault EVENTS, not the noisy per-window screen.
    clf, thr, auc = rf_labeler.train_labeler(op="low_fpr")
    print(f"reference RF LOSO AUC {auc:.3f} | low-FPR threshold {thr:.3f} "
          f"| persistence >={rf_labeler.SILVER_MIN_RUN} windows")

    for sess in dataset.SILVER_SESSIONS:
        imu = L.load_imu(os.path.join(L.DATA_ROOT, sess))
        if imu is None:
            print(f"skip (no IMU): {sess}")
            continue
        fs = L.imu_fs(imu)
        accel = imu[["ax", "ay", "az"]].values
        gyro = imu[["gx", "gy", "gz"]].values
        spans = rf_labeler.label_imu(accel, gyro, imu["t"].values, fs, clf, thr)
        out = os.path.join(dataset.SILVER_DIR, sess + ".csv")
        with open(out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["start", "end"])
            w.writeheader()
            for a, b in spans:
                w.writerow({"start": f"{a:.3f}", "end": f"{b:.3f}"})
        dur = float(np.sum([b - a for a, b in spans]))
        print(f"{sess}: {len(spans)} fault spans, {dur:.1f}s flagged -> {out}")


if __name__ == "__main__":
    main()
