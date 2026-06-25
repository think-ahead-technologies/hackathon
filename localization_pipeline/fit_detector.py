"""
fit_detector.py -- OPTIMIZE the wheel detector for a run from hand labels.

Two jobs, both driven by labels (label_wheels.py -> *_truth.json):
  1. FIND THE ROI. The camera mount differs per run, so the dark wheel-hub lands
     in different pixels. We build a dark-DIFFERENCE map (mean dark over labeled
     wheel frames minus over non-wheel frames) and slide a small box to the most
     discriminative spot. Writes a roi json.
  2. TUNE THE THRESHOLDS. With that ROI, sweep the brightness-adaptive dark
     threshold (rel_k * frame-median), the dark gate D0 and the motion gate
     NETTHR; score detected runs vs the labeled wheel intervals (overlap match,
     FP/FN) and report the best.

Dark is measured BRIGHTNESS-RELATIVE (gray < rel_k * frame-median) so it is
robust to the camera's auto-exposure (see README).

Usage:
  python fit_detector.py --frames DIR --labels run_truth.json --out-roi roi_run.json
"""
import os, glob, re, json, argparse
import numpy as np
import cv2

ROI_W, ROI_H = 22, 13                       # detector ROI size (px)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--labels", required=True, help="*_truth.json from label_wheels.py")
    ap.add_argument("--out-roi", required=True)
    ap.add_argument("--rel-k", type=float, default=0.6, help="adaptive dark threshold factor")
    ap.add_argument("--pad", type=int, default=40, help="frames of margin around the labeled span")
    args = ap.parse_args()

    fs = sorted(glob.glob(os.path.join(args.frames, "*.jpg")))
    fts = np.array([int(re.search(r"_(\d+)\.jpg$", f).group(1)) for f in fs]) / 1e6
    dt = np.diff(fts, prepend=fts[0])
    T = json.load(open(args.labels))
    groups = [tuple(g) for g in T["groups"]]
    wheel = np.zeros(len(fs), bool)
    for a, b in groups:
        wheel[a:b + 1] = True
    lo = max(0, min(g[0] for g in groups) - args.pad)
    hi = min(len(fs) - 1, max(g[1] for g in groups) + args.pad)
    print(f"labeled span frames {lo}..{hi}  ({sum(1 for g in groups if lo<=g[0]<=hi)} wheels)")

    # ---- 1. ROI finding via brightness-adaptive dark-difference map ----
    H, W = cv2.imread(fs[lo], cv2.IMREAD_GRAYSCALE).shape
    wsum = np.zeros((H, W)); nsum = np.zeros((H, W)); wc = nc = 0
    for i in range(lo, hi + 1):
        g = cv2.imread(fs[i], cv2.IMREAD_GRAYSCALE).astype(np.float32)
        d = (g < args.rel_k * np.median(g)).astype(np.float32)
        if wheel[i]:
            wsum += d; wc += 1
        else:
            nsum += d; nc += 1
    diff = wsum / max(wc, 1) - nsum / max(nc, 1)
    best = None
    for y in range(0, H - ROI_H, 2):
        for x in range(0, W - ROI_W, 2):
            s = diff[y:y + ROI_H, x:x + ROI_W].mean()
            if best is None or s > best[0]:
                best = (s, x, y)
    _, bx, by = best
    roi = {"polygon": [[bx, by], [bx + ROI_W, by], [bx + ROI_W, by + ROI_H], [bx, by + ROI_H]],
           "thresh": 60, "mode": "rect", "frame_size": [W, H]}
    json.dump(roi, open(args.out_roi, "w"))
    print(f"ROI -> {args.out_roi}: x={bx}..{bx+ROI_W} y={by}..{by+ROI_H} (diff score {best[0]:.3f})")

    # ---- compute dark(adaptive) + net(motion) in that ROI over the span ----
    x0, y0 = bx, by; x1, y1 = bx + ROI_W, by + ROI_H
    dark = np.zeros(len(fs)); net = np.zeros(len(fs)); pr = pf = None
    for i in range(lo, hi + 1):
        img = cv2.imread(fs[i], cv2.IMREAD_GRAYSCALE)
        r = img[y0:y1 + 1, x0:x1 + 1].astype(np.float32)
        dark[i] = (r < args.rel_k * np.median(img)).mean()
        if pr is not None and dt[i] <= 1.0:
            net[i] = max(0.0, float(np.abs(r - pr).mean()) -
                         float(np.median(np.abs(img.astype(np.float32) - pf))))
        pr, pf = r, img.astype(np.float32)

    gts = [g for g in groups if lo <= g[0] <= hi]

    def score(D0, NET, PAD=1):
        g = np.zeros(len(fs)); b = dark > D0; i = lo
        while i <= hi:
            if b[i]:
                j = i
                while j <= hi and b[j]:
                    j += 1
                if net[i:j].max() >= NET:
                    g[max(lo, i - PAD):min(hi, j + PAD)] = 1
                i = j
            else:
                i += 1
        runs = []; i = lo
        while i <= hi:
            if g[i]:
                j = i
                while j <= hi and g[j]:
                    j += 1
                runs.append((i, j - 1)); i = j
            else:
                i += 1
        used = [False] * len(gts); fp = 0
        for r in runs:
            c = [gi for gi, gg in enumerate(gts)
                 if min(r[1] + 3, gg[1] + 3) - max(r[0] - 3, gg[0] - 3) > 0 and not used[gi]]
            if c:
                used[c[0]] = True
            else:
                fp += 1
        return len(runs), fp, used.count(False)

    print("\n2. threshold tuning (det, FP, FN  vs %d labeled):" % len(gts))
    best = None
    for D0 in (0.3, 0.4, 0.5):
        for NET in (0.2, 0.3, 0.5):
            d, fp, fn = score(D0, NET)
            if best is None or fp + fn < best[0]:
                best = (fp + fn, D0, NET, d, fp, fn)
    print(f"   BEST: D0={best[1]} NETTHR={best[2]}  -> det={best[3]} FP={best[4]} FN={best[5]}"
          f"  (recall {100*(len(gts)-best[5])/max(1,len(gts)):.0f}%)")
    print(f"\nrun the pipeline with:  --roi {args.out_roi} --rel-k {args.rel_k} "
          f"--d0 {best[1]} --netthr {best[2]}")


if __name__ == "__main__":
    main()
