"""Render track_map.json to a labelled diagram for visual confirmation."""
import json, os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

HERE = os.path.dirname(os.path.abspath(__file__))
T = json.load(open(os.path.join(HERE, "track_map.json")))

COL = {"turn": "#f4c542", "straight2": "#bcd4ff", "straight4": "#7fa8ff"}
fig, ax = plt.subplots(figsize=(7, 13))
for pid, p in T["pieces"].items():
    c, y = p["grid"]
    # height proportional to physical length (60cm = 1 unit); turntable = small
    slots = 2.0 if p["type"] == "straight4" else 1.0   # straight4 = 2 slots tall
    h = slots * 0.9
    ax.add_patch(FancyBboxPatch((c - 0.42, -y - h / 2), 0.84, h,
                 boxstyle="round,pad=0.02,rounding_size=0.1",
                 fc=COL[p["type"]], ec="black", lw=1.3))
    lab = pid + ("\n(switch)" if p.get("is_middle_switch") else "")
    sub = "Turn" if p["type"] == "turn" else f"{int(p['length_cm'])}cm/{p['wheels']}w"
    ax.text(c, -y + 0.12, lab, ha="center", va="center", fontsize=6.5, weight="bold")
    ax.text(c, -y - 0.22, sub, ha="center", va="center", fontsize=5.5, color="#333")

for cn in T["connections"]:
    a, b = T["pieces"][cn["a"]], T["pieces"][cn["b"]]
    ax.plot([a["grid"][0], b["grid"][0]], [-a["grid"][1], -b["grid"][1]],
            color="0.6", lw=1.0, zorder=0)

ax.set_xlim(-1, 4); ax.set_ylim(-10.4, 1.2); ax.set_aspect("equal"); ax.axis("off")
ax.set_title(f"Track map: {T['meta']['topology']}\n"
             f"{T['meta']['total_big_wheels']} big wheels, "
             f"upper={T['loops']['upper']['wheels']}w lower={T['loops']['lower']['wheels']}w "
             f"(bar shared)", fontsize=9)
plt.tight_layout()
out = os.path.join(HERE, "track_map.png")
plt.savefig(out, dpi=110); print("wrote", out)
