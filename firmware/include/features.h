// ABOUTME: On-device spectrogram feature extractor — 3-axis accel window -> int8 [49,40] model input.
// ABOUTME: Pure C port of wear_detector/export/spectro.py; deterministic + host-tested (cross-check).

#ifndef FEATURES_H
#define FEATURES_H

#include <stdint.h>
#include <stddef.h>

// Fixed by the model contract (model-meta.json / baseline.json feature_config @ fs=50 Hz).
#define FEAT_N_FRAMES 49
#define FEAT_N_BANDS  40
#define FEAT_N_FFT    16
#define FEAT_HOP      3
#define FEAT_N_FREQ   ((FEAT_N_FFT / 2) + 1)   // 9 rfft bins
#define FEAT_FS       50.0
#define FEAT_WINDOW_SAMPLES 200                // round(window_s * fs) = 4.0 * 50
#define FEAT_OUT_LEN  (FEAT_N_FRAMES * FEAT_N_BANDS)  // 1960

// Compute the int8 spectrogram the model consumes from a window of 3-axis accelerometer
// samples. `accel` is `n_samples` interleaved [x,y,z] triples (same units as training data).
// `out` receives FEAT_OUT_LEN int8 values in frame-major order (model input [1,49,40,1]).
// Returns 0 on success, -1 if n_samples is too short (< FEAT_N_FFT).
//
// Mirrors spectro.accel_to_spectrogram + baseline.embed_int8's quantization exactly:
//   dynamic magnitude (gravity removed) -> Hann -> rfft power -> linear-tri 40-band
//   filterbank -> log1p(power/1e-3) -> round-half-to-even(/scale + zp) clipped int8.
int features_from_accel(const float *accel, int n_samples, int8_t out[FEAT_OUT_LEN]);

#endif  // FEATURES_H
