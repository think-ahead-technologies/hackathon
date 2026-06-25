// ABOUTME: Embedded numeric feature extraction for the bearing RandomForest detector.
// ABOUTME: Mirrors analysis/features.py closely without retaining anything beyond the 1 s window.

#include "bearing_features.h"

#include <math.h>
#include <string.h>

#ifndef BEARING_PI
#define BEARING_PI 3.14159265358979323846f
#endif

#define EPS_RMS 1.0e-9f
#define EPS_SPEC 1.0e-12f

static const float HP_B[5] = {
    0.43284664f, -1.73138658f, 2.59707987f, -1.73138658f, 0.43284664f,
};
static const float HP_A[5] = {
    1.0f, -2.36951301f, 2.31398841f, -1.05466541f, 0.18737949f,
};
static const float LF_B[3] = {
    0.01335920f, 0.02671840f, 0.01335920f,
};
static const float LF_A[3] = {
    1.0f, -1.64745998f, 0.70089678f,
};

void bearing_window_init(bearing_sensor_window_t *window)
{
    if (window != NULL) {
        memset(window, 0, sizeof(*window));
    }
}

bool bearing_window_push(bearing_sensor_window_t *window,
                         const bearing_sensor_sample_t *sample,
                         bearing_sensor_sample_t *overwritten)
{
    if (window == NULL || sample == NULL) {
        return false;
    }

    bool did_overwrite = window->count == BEARING_SENSOR_WINDOW_SAMPLES;
    if (did_overwrite && overwritten != NULL) {
        *overwritten = window->samples[window->write_index];
    }

    window->samples[window->write_index] = *sample;
    window->write_index = (window->write_index + 1u) % BEARING_SENSOR_WINDOW_SAMPLES;
    if (window->count < BEARING_SENSOR_WINDOW_SAMPLES) {
        window->count++;
    }
    window->total_pushed++;
    return did_overwrite;
}

bool bearing_window_ready(const bearing_sensor_window_t *window)
{
    return window != NULL && window->count == BEARING_SENSOR_WINDOW_SAMPLES;
}

uint32_t bearing_window_count(const bearing_sensor_window_t *window)
{
    return window != NULL ? window->count : 0u;
}

uint32_t bearing_window_end_ms(const bearing_sensor_window_t *window)
{
    if (window == NULL || window->count == 0u) {
        return 0u;
    }
    uint32_t newest = (window->write_index + BEARING_SENSOR_WINDOW_SAMPLES - 1u) %
                      BEARING_SENSOR_WINDOW_SAMPLES;
    return window->samples[newest].t_ms;
}

static const bearing_sensor_sample_t *window_at(const bearing_sensor_window_t *window,
                                                uint32_t chronological_index)
{
    uint32_t start = (window->count == BEARING_SENSOR_WINDOW_SAMPLES) ? window->write_index : 0u;
    uint32_t index = (start + chronological_index) % BEARING_SENSOR_WINDOW_SAMPLES;
    return &window->samples[index];
}

static void iir_forward(const float *b, const float *a, int taps,
                        const float *x, float *y, int n)
{
    for (int i = 0; i < n; i++) {
        float acc = 0.0f;
        for (int j = 0; j < taps; j++) {
            if (i >= j) {
                acc += b[j] * x[i - j];
            }
        }
        for (int j = 1; j < taps; j++) {
            if (i >= j) {
                acc -= a[j] * y[i - j];
            }
        }
        y[i] = acc;
    }
}

static void filtfilt_window(const float *b, const float *a, int taps,
                            const float *x, float *y, int n)
{
    float tmp[BEARING_SENSOR_WINDOW_SAMPLES];
    float rev[BEARING_SENSOR_WINDOW_SAMPLES];
    float rev_out[BEARING_SENSOR_WINDOW_SAMPLES];

    iir_forward(b, a, taps, x, tmp, n);
    for (int i = 0; i < n; i++) {
        rev[i] = tmp[n - 1 - i];
    }
    iir_forward(b, a, taps, rev, rev_out, n);
    for (int i = 0; i < n; i++) {
        y[i] = rev_out[n - 1 - i];
    }
}

static float band_energy_fraction(const float *spec, const float *freqs,
                                  int n_freq, float total, float lo, float hi)
{
    float sum = 0.0f;
    for (int i = 0; i < n_freq; i++) {
        if (freqs[i] >= lo && freqs[i] < hi) {
            sum += spec[i];
        }
    }
    return sum / total;
}

static float fisher_kurtosis(const float *x, int n)
{
    float mean = 0.0f;
    for (int i = 0; i < n; i++) {
        mean += x[i];
    }
    mean /= (float)n;

    float m2 = 0.0f;
    float m4 = 0.0f;
    for (int i = 0; i < n; i++) {
        float d = x[i] - mean;
        float d2 = d * d;
        m2 += d2;
        m4 += d2 * d2;
    }
    m2 /= (float)n;
    m4 /= (float)n;
    if (m2 <= 1.0e-18f) {
        return 0.0f;
    }
    return (m4 / (m2 * m2)) - 3.0f;
}

bool bearing_extract_features(const bearing_sensor_window_t *window,
                              float features[BEARING_RF_FEATURE_COUNT])
{
    if (!bearing_window_ready(window) || features == NULL) {
        return false;
    }

    enum { N = (int)BEARING_SENSOR_WINDOW_SAMPLES, N_FREQ = (N / 2) + 1 };
    float acc_mag[BEARING_SENSOR_WINDOW_SAMPLES];
    float gyro_mag[BEARING_SENSOR_WINDOW_SAMPLES];
    float acc_hp[BEARING_SENSOR_WINDOW_SAMPLES];
    float acc_lf[BEARING_SENSOR_WINDOW_SAMPLES];
    float spec[N_FREQ];
    float freqs[N_FREQ];

    for (int i = 0; i < N; i++) {
        const bearing_sensor_sample_t *s = window_at(window, (uint32_t)i);
        acc_mag[i] = sqrtf((s->ax_ms2 * s->ax_ms2) + (s->ay_ms2 * s->ay_ms2) +
                           (s->az_ms2 * s->az_ms2));
        gyro_mag[i] = sqrtf((s->gx_dps * s->gx_dps) + (s->gy_dps * s->gy_dps) +
                            (s->gz_dps * s->gz_dps));
    }

    filtfilt_window(HP_B, HP_A, 5, acc_mag, acc_hp, N);
    filtfilt_window(LF_B, LF_A, 3, acc_mag, acc_lf, N);

    float hp_energy = 0.0f;
    float peak = 0.0f;
    for (int i = 0; i < N; i++) {
        float a = fabsf(acc_hp[i]);
        if (a > peak) {
            peak = a;
        }
        hp_energy += acc_hp[i] * acc_hp[i];
    }
    float rms = sqrtf(hp_energy / (float)N) + EPS_RMS;

    for (int k = 0; k < N_FREQ; k++) {
        float real = 0.0f;
        float imag = 0.0f;
        for (int n = 0; n < N; n++) {
            float hann = 0.5f - 0.5f * cosf((2.0f * BEARING_PI * (float)n) / (float)(N - 1));
            float x = acc_hp[n] * hann;
            float angle = (2.0f * BEARING_PI * (float)k * (float)n) / (float)N;
            real += x * cosf(angle);
            imag -= x * sinf(angle);
        }
        spec[k] = (real * real) + (imag * imag);
        freqs[k] = ((float)k * (float)BEARING_SENSOR_HZ) / (float)N;
    }

    float spec_total = EPS_SPEC;
    float centroid_num = 0.0f;
    for (int k = 0; k < N_FREQ; k++) {
        spec_total += spec[k];
        centroid_num += freqs[k] * spec[k];
    }

    float gyro_energy = 0.0f;
    for (int i = 0; i < N; i++) {
        gyro_energy += gyro_mag[i] * gyro_mag[i];
    }

    float lf_mean = 0.0f;
    for (int i = 0; i < N; i++) {
        lf_mean += acc_lf[i];
    }
    lf_mean /= (float)N;
    float lf_var = 0.0f;
    for (int i = 0; i < N; i++) {
        float d = acc_lf[i] - lf_mean;
        lf_var += d * d;
    }
    lf_var /= (float)N;

    features[0] = rms;
    features[1] = peak * 2.0f;
    features[2] = peak / rms;
    features[3] = fisher_kurtosis(acc_hp, N);
    features[4] = band_energy_fraction(spec, freqs, N_FREQ, spec_total, 5.0f, 10.0f);
    features[5] = band_energy_fraction(spec, freqs, N_FREQ, spec_total, 10.0f, 15.0f);
    features[6] = band_energy_fraction(spec, freqs, N_FREQ, spec_total, 15.0f, 25.0f);
    features[7] = centroid_num / spec_total;
    features[8] = sqrtf(gyro_energy / (float)N);
    features[9] = sqrtf(lf_var);
    return true;
}