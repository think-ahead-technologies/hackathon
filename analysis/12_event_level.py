"""Event-level FPR/FNR for the bearing detector — the deployment-relevant rates.

A bearing fault is a multi-second event that recurs every lap, so per-window rates
overstate false alarms. Here we require k consecutive flagged windows to declare a
detection, then measure:
  event FNR  = labeled fault events with NO detection / all labeled fault events
  false-alarm rate = spurious detections per hour on fault-free time
(For events there is no clean 'true negative' count, so the false-positive side is
reported as a RATE per hour, which is what an operator actually experiences.)"""
import os
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import imloader as L
import features as F

OUT = os.path.join(os.path.dirname(__file__), "out")

# Build per-session windows WITH timestamps, plus a LOSO model per held-out session.
sessions = F.FAULT_SESSIONS + F.NORMAL_SESSIONS
per = {}
for name in sessions:
    sd = os.path.join(L.DATA_ROOT, name)
    imu = L.load_imu(sd)
    if imu is None:
        continue
    fs = L.imu_fs(imu)
    feats, centers = F.extract_windows(imu, fs)
    labels = L.load_labels(sd)
    y = F.window_labels(centers, labels)
    faults = labels[labels["label"] == "fault"]
    per[name] = dict(X=feats, centers=centers, y=y, faults=faults,
                     dur=centers[-1] - centers[0] if len(centers) else 0)

# global training matrix
allX = np.vstack([per[n]["X"] for n in per])
allY = np.concatenate([per[n]["y"] for n in per])
allG = np.concatenate([[i] * len(per[n]["X"]) for i, n in enumerate(per)])
names = list(per)

HOP = F.HOP_S
K = 2                 # consecutive windows required => ~1.0 s persistence
TOL = 1.5             # s tolerance matching detection to labeled event


def detections(score, centers, thr, k):
    flag = score >= thr
    runs, i = [], 0
    while i < len(flag):
        if flag[i]:
            j = i
            while j < len(flag) and flag[j]:
                j += 1
            if j - i >= k:
                runs.append((centers[i], centers[j - 1]))
            i = j
        else:
            i += 1
    return runs


# cache LOSO scores once (model fit is the expensive part)
_score_cache = {}
def session_scores(i, name):
    if name not in _score_cache:
        tr = allG != i
        clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                     random_state=0, n_jobs=-1).fit(allX[tr], allY[tr])
        _score_cache[name] = clf.predict_proba(per[name]["X"])[:, 1]
    return _score_cache[name]


def eval_threshold(thr, k):
    n_events = n_missed = 0
    fa = 0; fault_free_hours = 0.0
    for i, name in enumerate(names):
        d = per[name]
        s = session_scores(i, name)
        runs = detections(s, d["centers"], thr, k)
        fl = d["faults"]
        # event FNR
        for _, r in fl.iterrows():
            n_events += 1
            hit = any(not (re < r["start"] - TOL or rs > r["end"] + TOL) for rs, re in runs)
            if not hit:
                n_missed += 1
        # false alarms = detections not overlapping any labeled fault
        for rs, re in runs:
            overlap = any(not (re < r["start"] - TOL or rs > r["end"] + TOL)
                          for _, r in fl.iterrows())
            if not overlap:
                fa += 1
        # fault-free duration (approx: total minus labeled fault span)
        fault_span = (fl["end"] - fl["start"]).sum() if len(fl) else 0
        fault_free_hours += max(d["dur"] - fault_span, 0) / 3600.0
    fnr = n_missed / max(n_events, 1)
    fa_per_hr = fa / max(fault_free_hours, 1e-9)
    return n_events, n_missed, fnr, fa, fault_free_hours, fa_per_hr


print("Sweep persistence k (consecutive 1s windows) x threshold; match tol +-1.5s")
print("(faults last 2-4 s ~ 4-8 windows, so higher k filters short spurious flags)\n")
print(f"{'thr':>6s} {'k':>3s} {'events':>7s} {'missed':>7s} {'event-FNR':>10s} "
      f"{'FA/hour':>9s}")
for thr in [0.053, 0.10, 0.20]:
    for k in [2, 4, 6, 8]:
        ne, nm, fnr, fa, ffh, fph = eval_threshold(thr, k)
        print(f"{thr:6.3f} {k:3d} {ne:7d} {nm:7d} {fnr:10.2f} {fph:9.2f}")
    print()

print("CAVEAT: many 'false alarms' are likely REAL but UNLABELED fault passes — the "
      "labelers marked some lap passes, not all (figure-8 has ~2 loci/lap), and turns "
      "can still leak. So measured FA/hour is an UPPER BOUND on the true rate. The "
      "clean fix is lap-phase gating (a true fault recurs at the same lap position) "
      "-- but that's the localisation workstream.")
