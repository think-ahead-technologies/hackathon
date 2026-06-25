"""
build_track_map.py  --  generate track_map.json for position encoding.

Track layout (from the user's diagram): a FIGURE-8 / theta -- two stacked
rectangular loops (UPPER + LOWER) that share a middle bar. The two middle
turntables (TT_ML, TT_MR) are the crossover/junction nodes (degree 3).

Piece types
-----------
  straight2 : short segment, 60 cm,  4 big wheels
  straight4 : long  segment, 120 cm, 8 big wheels
  turn      : a TURNTABLE -- the box rotates in place (~90 deg). It is its own
              segment but contributes little translation; its signature is a
              YAW rotation in the IMU (gyr_z), which we use as a hard anchor.

Coordinates: 0-100 PROGRESS along the current piece, plus the id of the piece
behind and ahead ("between two named pieces"). A global per-loop cumulative
distance (cm) and cumulative big-wheel count are also emitted so the camera
wheel-counter and the IMU can both be mapped onto the same scale.

Grid (col,row): col 0=left, 1/2=centre straights, 3=right; row increases
downward. Used only for visualization.

EDIT ME: TURN_CM (turntable travel length) and the crossover routing are the
two things the diagram doesn't pin down -- see CROSSOVER_NOTE.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- physical constants (edit if measured) ----
LEN = {"straight2": 60.0, "straight4": 120.0, "turn": 0.0}  # cm of travel
WHEELS = {"straight2": 4, "straight4": 8, "turn": 0}
TURN_CM = 0.0          # the box does NOT translate while on a turntable
TURN_DEG = 90.0        # yaw change when a turntable rotates the box

# grid y is a stacking SLOT coordinate (downward), not physical distance:
# turntable & straight2 = 1 slot, straight4 = 2 slots. Pieces stack end-to-end
# so nothing overlaps and 4-segments are drawn twice as tall. Both side columns
# total 10 slots so the top/middle/bottom turntables line up across the track.
CROSSOVER_NOTE = (
    "TT_ML and TT_MR are the middle turntables. Each is externally controlled "
    "(we have NO access): it either stays put -> the box passes STRAIGHT "
    "(vertical, upper-side <-> lower-side, 0 deg) or it rotates -> the box is "
    "SWITCHED onto the shared middle bar (90 deg). Which one happened is "
    "OBSERVED from the IMU yaw at that node (rotation present = switch). The "
    "corner turntables always rotate 90 deg. The box does not travel during any "
    "turntable (length_cm = 0)."
)

# ---- pieces: id -> (type, grid[col, y_center]) ----  y = stacking-slot center
# Left & right columns each total 10 slots; turntables align at y=0.5/2.5/9.5.
# turntables (6)
TURNS = {
    "TT_TL": (0, 0.5), "TT_TR": (3, 0.5),     # upper corners
    "TT_ML": (0, 2.5), "TT_MR": (3, 2.5),     # middle (switch) turntables
    "TT_BL": (0, 9.5), "TT_BR": (3, 9.5),     # lower corners
}
# straights -- (col, y_center); straight4 spans 2 slots (center on the .0)
STRAIGHTS = {
    # upper loop top edge + sides
    "U_TOP1": ("straight2", (1, 0.5)), "U_TOP2": ("straight2", (2, 0.5)),
    "U_LFT":  ("straight2", (0, 1.5)), "U_RGT":  ("straight2", (3, 1.5)),
    # shared middle bar (between TT_ML and TT_MR)
    "M_BAR1": ("straight2", (1, 2.5)), "M_BAR2": ("straight2", (2, 2.5)),
    # lower loop -- right side (TT_MR -> TT_BR): 2,2,4,2,2  (60+60+120+60+60=360)
    "L_R1": ("straight2", (3, 3.5)), "L_R2": ("straight2", (3, 4.5)),
    "L_R3": ("straight4", (3, 6.0)), "L_R4": ("straight2", (3, 7.5)),
    "L_R5": ("straight2", (3, 8.5)),
    # lower loop -- bottom (TT_BR -> TT_BL): 2,2
    "L_BOT1": ("straight2", (1, 9.5)), "L_BOT2": ("straight2", (2, 9.5)),
    # lower loop -- left side (TT_BL -> TT_ML, going up): 4,4,2,2 (120+120+60+60=360)
    "L_L1": ("straight2", (0, 3.5)), "L_L2": ("straight2", (0, 4.5)),
    "L_L3": ("straight4", (0, 6.0)), "L_L4": ("straight4", (0, 8.0)),
}

# ---- turntable transition tables (which neighbors connect, and the rotation) ----
# corners: single 90deg turn between their two neighbors.
# middle: 'straight' = vertical pass-through (0deg), 'switch' = onto the bar (90deg).
TRANSITIONS = {
    "TT_TL": [{"mode": "turn", "between": ["U_TOP1", "U_LFT"], "rotation_deg": 90}],
    "TT_TR": [{"mode": "turn", "between": ["U_TOP2", "U_RGT"], "rotation_deg": 90}],
    "TT_BL": [{"mode": "turn", "between": ["L_BOT1", "L_L4"], "rotation_deg": 90}],
    "TT_BR": [{"mode": "turn", "between": ["L_BOT2", "L_R5"], "rotation_deg": 90}],
    "TT_ML": [
        {"mode": "straight", "between": ["U_LFT", "L_L1"], "rotation_deg": 0},
        {"mode": "switch",   "between": ["M_BAR1", "U_LFT"], "rotation_deg": 90},
        {"mode": "switch",   "between": ["M_BAR1", "L_L1"], "rotation_deg": 90},
    ],
    "TT_MR": [
        {"mode": "straight", "between": ["U_RGT", "L_R1"], "rotation_deg": 0},
        {"mode": "switch",   "between": ["M_BAR2", "U_RGT"], "rotation_deg": 90},
        {"mode": "switch",   "between": ["M_BAR2", "L_R1"], "rotation_deg": 90},
    ],
}

# ---- ordered rings (each ring is a closed list of piece ids) ----
# UPPER loop, clockwise from top-left turntable. Bottom edge = shared bar.
UPPER = ["TT_TL", "U_TOP1", "U_TOP2", "TT_TR", "U_RGT",
         "TT_MR", "M_BAR2", "M_BAR1", "TT_ML", "U_LFT"]
# LOWER loop, clockwise from the mid-right turntable. Top edge = shared bar.
LOWER = ["TT_MR", "L_R1", "L_R2", "L_R3", "L_R4", "L_R5",
         "TT_BR", "L_BOT2", "L_BOT1", "TT_BL",
         "L_L4", "L_L3", "L_L2", "L_L1", "TT_ML",
         "M_BAR1", "M_BAR2"]   # ...back across the shared bar to TT_MR

SHARED = ["TT_ML", "TT_MR", "M_BAR1", "M_BAR2"]


def piece_type(pid):
    if pid in TURNS:
        return "turn"
    return STRAIGHTS[pid][0]


def piece_grid(pid):
    return list(TURNS[pid]) if pid in TURNS else list(STRAIGHTS[pid][1])


def build_pieces():
    pieces = {}
    for pid in list(TURNS) + list(STRAIGHTS):
        t = piece_type(pid)
        loops = [n for n, ring in (("upper", UPPER), ("lower", LOWER)) if pid in ring]
        p = {
            "id": pid, "type": t,
            "length_cm": TURN_CM if t == "turn" else LEN[t],
            "wheels": WHEELS[t],
            "grid": piece_grid(pid),
            "loops": loops,
            "is_turntable": t == "turn",
        }
        if t == "turn":
            p["is_middle_switch"] = pid in ("TT_ML", "TT_MR")
            p["transitions"] = TRANSITIONS[pid]
            # corners always 90deg; middle is 0 (straight) or 90 (switch)
            p["rotation_deg"] = (TURN_DEG if not p["is_middle_switch"]
                                 else "0 (straight) or 90 (switch)")
        pieces[pid] = p
    return pieces


def ring_with_cumulative(ring, pieces):
    """Return ordered nodes with cumulative travel-cm and wheel-count at the
    START of each piece, plus the junction 'point' ids between consecutive
    pieces. cum is taken around the ring starting at index 0."""
    out, cum_cm, cum_w = [], 0.0, 0
    n = len(ring)
    for i, pid in enumerate(ring):
        nxt = ring[(i + 1) % n]
        out.append({
            "piece": pid,
            "point_in": f"J:{ring[(i - 1) % n]}->{pid}",
            "point_out": f"J:{pid}->{nxt}",
            "cum_cm_start": round(cum_cm, 2),
            "cum_wheels_start": cum_w,
        })
        cum_cm += pieces[pid]["length_cm"]
        cum_w += pieces[pid]["wheels"]
    return out, round(cum_cm, 2), cum_w


def build_connections():
    conns = set()
    for ring in (UPPER, LOWER):
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            conns.add(tuple(sorted((a, b))))
    return [{"a": a, "b": b} for a, b in sorted(conns)]


def build_points(conns):
    """A 'point' = a junction between two named pieces. Directed ids are used
    in the rings; here we list the undirected junctions with a stable id."""
    pts = []
    for i, c in enumerate(sorted({tuple(sorted((x["a"], x["b"]))) for x in conns})):
        pts.append({"id": f"P{i:02d}", "between": list(c)})
    return pts


def main():
    pieces = build_pieces()
    upper, upper_cm, upper_w = ring_with_cumulative(UPPER, pieces)
    lower, lower_cm, lower_w = ring_with_cumulative(LOWER, pieces)
    conns = build_connections()
    points = build_points(conns)

    total_wheels = sum(p["wheels"] for p in pieces.values())
    total_straight_cm = sum(p["length_cm"] for p in pieces.values()
                            if p["type"] != "turn")

    track = {
        "meta": {
            "units": "cm",
            "topology": "figure8_theta (two stacked loops sharing a middle bar)",
            "segment_types": {
                "straight2": {"length_cm": LEN["straight2"], "wheels": WHEELS["straight2"]},
                "straight4": {"length_cm": LEN["straight4"], "wheels": WHEELS["straight4"]},
                "turn": {"length_cm": TURN_CM, "wheels": 0,
                         "is_turntable": True, "travel_during_turn": False,
                         "rotation_deg": TURN_DEG},
            },
            "wheel_period_s": 0.455,
            "n_pieces": len(pieces),
            "n_turntables": len(TURNS),
            "total_big_wheels": total_wheels,
            "total_straight_length_cm": total_straight_cm,
            "middle_switch_turntables": ["TT_ML", "TT_MR"],
            "crossover_note": CROSSOVER_NOTE,
            "grid_units": "y in 60cm-units, downward; straight4 spans 2 units",
            "imu": {
                "source": "../merged_20260623_17xx.csv",
                "yaw_channel": "gyr_z_dps",
                "time_channel_s": "t_rel_s",
                "note": "IMU ODR changes mid-recording -> integrate with real "
                        "per-sample dt from t_rel_s/t_host_us, never a fixed rate.",
            },
        },
        "pieces": pieces,
        "connections": conns,
        "points": points,
        "shared_pieces": SHARED,
        "loops": {
            "upper": {"order": UPPER, "length_cm": upper_cm,
                      "wheels": upper_w, "ring": upper},
            "lower": {"order": LOWER, "length_cm": lower_cm,
                      "wheels": lower_w, "ring": lower},
        },
        "position_encoding": {
            "scheme": "between_pieces",
            "description": "A box position = {loop, piece, pct} where pct in "
                           "[0,100] is travel progress along 'piece'; the box is "
                           "between piece (point_in side) and the next piece "
                           "(point_out side). Global per-loop coordinates "
                           "cum_cm_start / cum_wheels_start let the camera "
                           "wheel-count and IMU distance map onto one scale.",
            "example": {"loop": "lower", "piece": "L_R3", "pct": 42.0},
        },
    }

    out = os.path.join(HERE, "track_map.json")
    with open(out, "w") as f:
        json.dump(track, f, indent=2)

    print(f"wrote {out}")
    print(f"  pieces={len(pieces)}  turntables={len(TURNS)}  "
          f"connections={len(conns)}  points={len(points)}")
    print(f"  UPPER loop: {len([p for p in UPPER if p not in TURNS])} straights, "
          f"{upper_cm:.0f} cm, {upper_w} wheels")
    print(f"  LOWER loop: {len([p for p in LOWER if p not in TURNS])} straights "
          f"(bar shared), {lower_cm:.0f} cm, {lower_w} wheels")
    print(f"  total distinct big wheels on track = {total_wheels}")


if __name__ == "__main__":
    main()
