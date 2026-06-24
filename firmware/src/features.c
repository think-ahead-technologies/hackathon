// ABOUTME: Spectrogram feature extractor — 3-axis accel window -> int8 [49,40] model input.
// ABOUTME: Pure C port of wear_detector/export/spectro.py (+ baseline.embed_int8 quantization).

#include "features.h"

#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// Constants from the model contract (baseline.json @ fs=50 Hz). Computed in double to
// match numpy's float64 STFT path; the model is robust to the residual <=1-LSB rounding.
#define FEAT_NYQ        (FEAT_FS / 2.0)                 // 25.0
#define FEAT_SCALE_EPS  1e-3
#define FEAT_IN_SCALE   0.025814762338995934
#define FEAT_IN_ZP      (-128)

int features_from_accel(const float *accel, int n_samples, int8_t out[FEAT_OUT_LEN]) {
    if (n_samples < FEAT_N_FFT) {
        return -1;
    }
    if (n_samples > FEAT_WINDOW_SAMPLES) {
        n_samples = FEAT_WINDOW_SAMPLES;  // contract is exactly one 4 s window
    }

    // (1) dynamic magnitude: vector magnitude with the per-window mean removed (drops gravity).
    static double sig[FEAT_WINDOW_SAMPLES];  // single-threaded device loop; keeps stack small
    double mean = 0.0;
    for (int i = 0; i < n_samples; i++) {
        double ax = accel[3 * i + 0], ay = accel[3 * i + 1], az = accel[3 * i + 2];
        sig[i] = sqrt(ax * ax + ay * ay + az * az);
        mean += sig[i];
    }
    mean /= (double)n_samples;
    for (int i = 0; i < n_samples; i++) {
        sig[i] -= mean;
    }

    // (2) Hann window (numpy np.hanning: 0.5 - 0.5*cos(2*pi*k/(N-1))).
    double win[FEAT_N_FFT];
    for (int k = 0; k < FEAT_N_FFT; k++) {
        win[k] = 0.5 - 0.5 * cos(2.0 * M_PI * k / (FEAT_N_FFT - 1));
    }

    // (3) triangular filterbank, linearly spaced over [0, nyq] (matches _tri_filterbank).
    double freqs[FEAT_N_FREQ];
    for (int f = 0; f < FEAT_N_FREQ; f++) {
        freqs[f] = FEAT_NYQ * (double)f / (double)(FEAT_N_FREQ - 1);
    }
    static double fb[FEAT_N_BANDS][FEAT_N_FREQ];
    for (int b = 0; b < FEAT_N_BANDS; b++) {
        double lo  = FEAT_NYQ * (double)b       / (double)(FEAT_N_BANDS + 1);
        double ctr = FEAT_NYQ * (double)(b + 1) / (double)(FEAT_N_BANDS + 1);
        double hi  = FEAT_NYQ * (double)(b + 2) / (double)(FEAT_N_BANDS + 1);
        for (int f = 0; f < FEAT_N_FREQ; f++) {
            double left  = (freqs[f] - lo) / (ctr - lo + 1e-12);
            double right = (hi - freqs[f]) / (hi - ctr + 1e-12);
            double v = left < right ? left : right;
            fb[b][f] = v > 0.0 ? v : 0.0;
        }
    }

    // (4) STFT -> power -> filterbank -> log1p -> quantize, one frame per hop.
    int frame = 0;
    for (int start = 0; start + FEAT_N_FFT <= n_samples && frame < FEAT_N_FRAMES;
         start += FEAT_HOP, frame++) {
        double power[FEAT_N_FREQ];
        for (int f = 0; f < FEAT_N_FREQ; f++) {
            double re = 0.0, im = 0.0;
            for (int k = 0; k < FEAT_N_FFT; k++) {
                double s = sig[start + k] * win[k];
                double ang = -2.0 * M_PI * (double)f * (double)k / (double)FEAT_N_FFT;
                re += s * cos(ang);
                im += s * sin(ang);
            }
            power[f] = re * re + im * im;
        }
        for (int b = 0; b < FEAT_N_BANDS; b++) {
            double bandp = 0.0;
            for (int f = 0; f < FEAT_N_FREQ; f++) {
                bandp += fb[b][f] * power[f];
            }
            double logmel = log1p(bandp / FEAT_SCALE_EPS);
            // baseline.embed_int8: round-half-to-even(x/scale + zp), clip to int8.
            double q = nearbyint(logmel / FEAT_IN_SCALE + FEAT_IN_ZP);
            if (q < -128.0) q = -128.0;
            else if (q > 127.0) q = 127.0;
            out[frame * FEAT_N_BANDS + b] = (int8_t)q;
        }
    }

    // Pad to exactly N_FRAMES by repeating the last frame (spectro.py pads pre-log; repeating
    // the already-quantized row is equivalent since log1p/quantize are per-element monotone).
    for (; frame < FEAT_N_FRAMES; frame++) {
        for (int b = 0; b < FEAT_N_BANDS; b++) {
            out[frame * FEAT_N_BANDS + b] = out[(frame - 1) * FEAT_N_BANDS + b];
        }
    }
    return 0;
}
