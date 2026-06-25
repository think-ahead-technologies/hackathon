# Conveyor box localization pipeline

Estimate where a transport box is on the conveyor loop, from the box's **camera**
(counts the big servo wheels it passes) + **IMU** (turntable rotations, stops).

## How it works (one paragraph)
The box moves forward around a fixed **clockwise loop**. Big wheels are exactly
**15 cm apart**, so counting them in a small fixed **ROI** gives distance. The 6
**turntables** rotate the box ~90° at the corners; each rotation is a **hard
anchor** (exact known position) detected from the gyro — and a turn lost in an
IMU data gap is **recovered from the magnetometer** (absolute heading steps even
when the gyro misses it). Between anchors the wheel count gives position; the box
**dwells** on turntables (conveyor keeps running → false wheels) so each leg is
**capped at its true segment length and held at the corner**. Camera
**auto-exposure** is handled with a **brightness-relative dark threshold**.

Validated accuracy (leave-one-wheel-out): **≤ ~6 cm**, non-accumulating.

## Track geometry (clockwise loop, centre-to-centre)
```
        TL --160-- TR          corners: TL TR BR BL (each a turntable)
        |           |          short side (top/bottom) = 160 cm  (120 straight + 40 turntable)
       460         460         long side (left/right)  = 460 cm  (420 straight + 40 turntable)
        |           |          lap = 1240 cm
        BL --160-- BR          1 big wheel = 15 cm ; belt cruise ~33 cm/s (0.45 s/wheel)
```
Clockwise order BR→BL→TL→TR. `track_map.json` is the machine-readable map.

## Files
| file | role |
|---|---|
| `label_wheels.py`        | **label** ground-truth wheels (interactive) |
| `fit_detector.py`        | **optimize**: find the ROI + tune thresholds from labels |
| `pipeline.py`            | **position**: wheels → anchors (+mag recovery) → position → LOO accuracy |
| `track.py`               | helpers: `detect_turntables`, `integrate_yaw`, `TrackMap` |
| `make_position_imu_csv.py` | join position onto every IMU sample → CSV |
| `render_map_video.py`    | verification video (camera + live position on the loop) |
| `track_map.json` / `build_track_map.py` / `viz_track_map.py` | the map + (re)build + draw it |
| `roi.json`, `roi_exp2.json` | ROI configs (per camera mount) |

## Workflow

**0. (once) inspect the map**
```
python viz_track_map.py            # -> track_map.png
```

**1. Label a run** (≥ 1 full lap; include a long side and a turntable dwell).
Run in your own terminal — it opens an interactive window.
```
python label_wheels.py --dir <frames_dir> --out <run>_truth.json
# r=record wheel, e=segment-end, s=save, q=quit
```

**2. Optimize the detector** for that run (camera mount sets the ROI; exposure
sets the thresholds):
```
python fit_detector.py --frames <frames_dir> --labels <run>_truth.json --out-roi roi_<run>.json
# prints the ROI and the best --rel-k / --d0 / --netthr to use
```

**3. Run positioning** (`--start-tt` = the FIRST turntable the box reaches;
clockwise BR/BL/TL/TR):
```
python pipeline.py --frames <frames_dir> --imu <imu.csv> \
    --roi roi_<run>.json --rel-k 0.6 --d0 0.4 --netthr 0.3 --start-tt BL \
    --out <run>
# -> <run>_track.csv, <run>_wheels.csv, <run>_anchors.csv ; prints LOO max error
```

**4. Position-vs-IMU CSV** (for correlation / feature work):
```
python make_position_imu_csv.py --imu <imu.csv> --frames <frames_dir> \
    --track <run>_track.csv --wheels <run>_wheels.csv --anchors <run>_anchors.csv \
    --out <run>_position_imu.csv
```

**5. Verification video**:
```
python render_map_video.py --frames <frames_dir> --track <run>_track.csv \
    --wheels <run>_wheels.csv --roi roi_<run>.json --start BL --out <run>_localization.mp4
```

## Key parameters
- `--rel-k 0.6` : dark threshold = `0.6 × frame-median brightness` (auto-exposure robust). `0` = fixed `roi.thresh`.
- `--d0 0.4` : ROI dark-fraction gate. `--netthr 0.3` : ROI motion gate (rejects the static bar / steady scenes).
- `--start-tt` : first turntable reached (needed for ABSOLUTE segment labels; without it you still get relative distance).

## Per-run notes (examples done)
- **experiment2**: camera = `roi_exp2.json`, start `TL`. LOO ~5–6 cm.
- **experiment3**: same camera `roi_exp2.json`, start `BL`. LOO ~5–7 cm (short, idle-heavy).
- New camera mount → re-run step 2 (the ROI moves). New exposure regime → re-check `--rel-k`.

## Gotchas
- The magnetometer is hard-iron distorted; `recover_missed_turns` calibrates it
  (circle fit) and only recovers a turn during a data gap if the heading step is
  large (>70° calibrated) AND the gyro is flat. Dedup vs gyro anchors within 4 s.
- `moving` flag can read 1 for the first ~0.2 s before the debounce settles.
- Positions are absolute only if `--start-tt` is correct and the run has no
  uncovered missed turns; otherwise the first ~lap is reliable and the sparse
  tail may be a corner off.
```
