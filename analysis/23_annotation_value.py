"""Pitch artifact: the value of the manual AUDIO annotation (Waldemar).

Honest story:
- Before the labels: the audio channel was an untested hypothesis. We could show the
  microphone has ~160-320x the inertial-sensor bandwidth, but ZERO labelled faulty-audio
  recordings existed (all prior fault data was inertial+magnetometer only) -> no way to
  validate an audio detector at all.
- The labels (78 clips / 13 fault windows across 2 recordings) enabled validation AND
  caught a feature-design error in the first analysis pass (AUC 0.57 -> 0.89), then
  proved the detector generalises across recordings (pooled AUC 0.87).

AUC = area under the ROC curve (0.5 = chance, 1.0 = perfect).
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FIG = os.path.join(os.path.dirname(__file__), "figures")

stages = ["First pass\n(naive features)", "Corrected indicator\n(test2, 24 clips)",
          "Validated across both\n(test1+test2, 78 clips)"]
auc = [0.57, 0.89, 0.87]
colors = ["#bbbbbb", "C0", "C2"]

fig, ax = plt.subplots(figsize=(11, 6))
bars = ax.bar(stages, auc, color=colors, width=0.6)
ax.axhline(0.5, color="grey", ls="--", lw=1)
ax.text(2.45, 0.505, "chance (0.5)", ha="right", va="bottom", color="grey", fontsize=9)
ax.set_ylim(0.5, 1.0); ax.set_ylabel("audio fault-detector accuracy (area under ROC curve)")
for b, v in zip(bars, auc):
    ax.text(b.get_x()+b.get_width()/2, v+0.008, f"{v:.2f}", ha="center", fontweight="bold")
ax.set_title("Manual audio annotation turned a hypothesis into a validated detector",
             fontsize=13)
# annotations telling the contribution story
ax.annotate("labels CAUGHT our\nfeature-design error", xy=(0, 0.575), xytext=(0.18, 0.66),
            arrowprops=dict(arrowstyle="->", color="C3"), fontsize=10, color="C3", ha="center")
ax.annotate("2nd recording labelled\n→ proven to generalise", xy=(2, 0.872), xytext=(1.5, 0.95),
            arrowprops=dict(arrowstyle="->", color="C2"), fontsize=10, color="C2", ha="center")
fig.text(0.5, 0.045,
         "Before these labels: audio was an untested hypothesis — 0 labelled faulty-audio recordings existed "
         "(all prior fault data was inertial-only).\nAfter: 78 hand-labelled 5 s clips (13 fault) across 2 "
         "recordings → validated detector, equal-error false-positive 0.17 / false-negative 0.15.",
         ha="center", va="bottom", fontsize=9.5, style="italic", color="#333333")
for s in ["top", "right"]:
    ax.spines[s].set_visible(False)
plt.tight_layout(rect=[0, 0.12, 1, 1])
out = os.path.join(FIG, "audio-annotation-impact.png")
plt.savefig(out, dpi=120); print("wrote", out)
