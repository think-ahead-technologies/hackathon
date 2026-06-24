# ABOUTME: Emits the §7 Contract B JSON payload from detector scores (severity stage + trend).
# ABOUTME: Single-sense (vibration) reality today; acoustic/localization fields emitted as null/unknown.
from collections import deque

# Stage names mirror spec §5. With only the vibration sense available, "established"
# is reached by persistence (dwell) rather than acoustic+vibration agreement; this is
# noted in the payload via acoustic_score=null so consumers know confidence is capped.
HEALTHY, WATCH, ESTABLISHED, ADVANCED = 0, 1, 2, 3


class StageMachine:
    """Hysteresis + trend severity stager over per-window scores (spec §5).

    Faithful to the §5 table when both acoustic (A) and vibration (V) are present.
    When A is None (current data), it degrades honestly: V over threshold = watch,
    V sustained over `dwell` windows = established, + rising trend = advanced.
    """

    def __init__(self, v_thr=0.95, a_thr=0.95, dwell=6, release=6,
                 trend_window=60, trend_eps=1e-3):
        self.v_thr = v_thr
        self.a_thr = a_thr
        self.dwell = dwell          # consecutive windows above thr before escalating
        self.release = release      # consecutive windows below thr before de-escalating
        self.trend_window = trend_window
        self.trend_eps = trend_eps
        self.stage = HEALTHY
        self._above = 0
        self._below = 0
        self._hist = deque(maxlen=trend_window)  # recent V values for slope

    def _slope(self):
        n = len(self._hist)
        if n < 3:
            return 0.0
        xs = range(n)
        mx = (n - 1) / 2.0
        my = sum(self._hist) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, self._hist))
        den = sum((x - mx) ** 2 for x in xs)
        return num / den if den else 0.0

    def trend(self):
        s = self._slope()
        if s > self.trend_eps:
            return "rising"
        if s < -self.trend_eps:
            return "falling"
        return "stable"

    def update(self, v, a=None):
        """Advance the machine one window; return the current severity stage 0..3."""
        self._hist.append(v)
        v_hi = v >= self.v_thr
        a_hi = a is not None and a >= self.a_thr

        if v_hi or a_hi:
            self._above += 1
            self._below = 0
        else:
            self._below += 1
            self._above = 0

        rising = self.trend() == "rising"

        if a is not None:
            # both senses available -> spec §5 table
            if not v_hi and not a_hi:
                target = HEALTHY
            elif v_hi and a_hi:
                target = ADVANCED if rising else ESTABLISHED
            else:  # single sense high (a-first watch, or v-alone investigate)
                target = WATCH
        else:
            # vibration-only: persistence stands in for cross-sense agreement
            if not v_hi:
                target = HEALTHY
            elif self._above < self.dwell:
                target = WATCH
            elif rising and self.stage >= ESTABLISHED:
                target = ADVANCED          # advance only after established (no 0->3 jumps)
            else:
                target = ESTABLISHED

        # hysteresis: WATCH is the immediate early flag; higher stages need dwell;
        # any de-escalation needs a sustained drop (release).
        if target > self.stage:
            if target == WATCH or self._above >= self.dwell:
                self.stage = min(target, self.stage + 1) if self.stage >= WATCH else target
            else:
                self.stage = max(self.stage, WATCH)  # hold at watch until dwell satisfied
        elif target < self.stage and self._below >= self.release:
            self.stage = target
        return self.stage


def fuse(v, a=None):
    """Fused headline anomaly score (spec §7): max of available senses."""
    return v if a is None else max(v, a)


def contract_b(ts, container_id, model_version, v, a=None, stage=0, trend="stable",
               track_position=None, fault_locus="unknown"):
    """Build one §7 Contract B record. Absent senses/localization are explicit nulls."""
    return {
        "ts": ts,
        "container_id": container_id,
        "model_version": model_version,
        "anomaly_score": round(fuse(v, a), 4),
        "acoustic_score": None if a is None else round(a, 4),
        "vibration_score": round(v, 4),
        "severity_stage": int(stage),
        "track_position": track_position,
        "fault_locus": fault_locus,
        "trend": trend,
    }
