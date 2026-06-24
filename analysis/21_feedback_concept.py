"""Conceptual 'shape of what you'd see' for the technician feedback loop.
Deliberately illustrative (not real data) — the real simulation that backs the shape
is feedback-loop-potential.png. Clean schematic for the presentation."""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FIG = os.path.join(os.path.dirname(__file__), "figures")
x = np.linspace(0, 1, 200)
miss = 0.32*np.exp(-3.2*x) + 0.05          # misses fall and level off
false_alarm = 0.55*np.exp(-2.6*x) + 0.10   # false alarms fall and level off

fig, ax = plt.subplots(figsize=(11, 6))
ax.plot(x, false_alarm, color="C3", lw=4, label="false alarms")
ax.plot(x, miss, color="C0", lw=4, label="missed faults")

# qualitative axes only (clearly conceptual)
ax.set_xticks([0.02, 0.5, 0.95])
ax.set_xticklabels(["deploy\n(generic model)", "in service\n(technician labels alerts)", "matured\n(tuned to this site)"])
ax.set_yticks([]); ax.set_ylabel("error rate  (lower = better)")
ax.set_xlabel("system in use  →")
ax.set_ylim(0, 0.75); ax.set_xlim(0, 1)
ax.legend(loc="upper right", fontsize=12)
ax.annotate("each technician correction\n(false alarm / missed fault)\nretrains & recalibrates the model",
            xy=(0.42, false_alarm[84]), xytext=(0.45, 0.55),
            arrowprops=dict(arrowstyle="->", color="grey"), fontsize=11, color="grey")
ax.set_title("Feedback loop: the system gets better the more it's used  (illustrative shape)",
             fontsize=13)
ax.text(0.99, -0.16, "Illustrative concept — quantitative backing: figures/feedback-loop-potential.png",
        transform=ax.transAxes, ha="right", fontsize=9, style="italic", color="grey")
for s in ["top", "right"]:
    ax.spines[s].set_visible(False)
plt.tight_layout()
out = os.path.join(FIG, "feedback-loop-concept.png")
plt.savefig(out, dpi=120, bbox_inches="tight"); print("wrote", out)
