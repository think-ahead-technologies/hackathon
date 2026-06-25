// ABOUTME: Conservative IMU bearing/wear fault detector from analysis/fault_slice_error.py.
// ABOUTME: Computes one-window feature ratios against a per-unit healthy baseline.

#include "wear_fault.h"

#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define MIN_SAMPLES 16U
#define EPS 1e-12

#define ENV_RMS_THRESHOLD 2.8
#define ENV_P2P_THRESHOLD 2.8
#define JERK_THRESHOLD    2.4
#define ENERGY_THRESHOLD  2.1

typedef struct {
    double a_env_rms;
    double a_env_p2p;
    double a_jerk_mad;
    double a_rms;
    double a_std;
    double a_p2p;
    double a_rms_z;
} feature_values_t;

static wear_fault_result_t invalid_result(void) {
    wear_fault_result_t r;
    r.status = WEAR_FAULT_STATUS_INVALID_INPUT;
    r.fault_percent = 0.0f;
    r.anomaly_score = 0.0f;
    return r;
}

static int finite_nonnegative(float v) {
    return isfinite((double)v) && v >= 0.0f;
}

static int valid_baseline(const wear_fault_baseline_t *b) {
    return b != 0 &&
           finite_nonnegative(b->a_env_rms) && finite_nonnegative(b->a_env_p2p) &&
           finite_nonnegative(b->a_jerk_mad) && finite_nonnegative(b->a_rms) &&
           finite_nonnegative(b->a_std) && finite_nonnegative(b->a_p2p) &&
           finite_nonnegative(b->a_rms_z);
}

static double ratio(double value, float baseline) {
    return value / ((double)baseline + EPS);
}

static double min4(double a, double b, double c, double d) {
    double m = a < b ? a : b;
    m = c < m ? c : m;
    return d < m ? d : m;
}

static double max4(double a, double b, double c, double d) {
    double m = a > b ? a : b;
    m = c > m ? c : m;
    return d > m ? d : m;
}

static double clamp_percent(double v) {
    if (v < 0.0) return 0.0;
    if (v > 100.0) return 100.0;
    return v;
}

static void high_band_envelope_features(const double *sig, size_t n, double fs,
                                        double *env_rms, double *env_p2p) {
    static double dft_re[(WEAR_FAULT_MAX_SAMPLES / 2U) + 1U];
    static double dft_im[(WEAR_FAULT_MAX_SAMPLES / 2U) + 1U];
    static double env[WEAR_FAULT_MAX_SAMPLES];

    *env_rms = 0.0;
    *env_p2p = 0.0;
    if (n < MIN_SAMPLES || fs <= 0.0) {
        return;
    }

    const size_t half = n / 2U;
    const double nyq = fs * 0.5;
    const double lo = nyq * 0.5;
    const double hi = nyq * 0.95;
    int have_band = 0;

    for (size_t k = 0; k <= half; k++) {
        dft_re[k] = 0.0;
        dft_im[k] = 0.0;
    }

    for (size_t k = 1; k <= half; k++) {
        const double freq = fs * (double)k / (double)n;
        if (freq < lo || freq >= hi || freq >= nyq) {
            continue;
        }
        double re = 0.0;
        double im = 0.0;
        for (size_t i = 0; i < n; i++) {
            const double angle = 2.0 * M_PI * (double)k * (double)i / (double)n;
            re += sig[i] * cos(angle);
            im -= sig[i] * sin(angle);
        }
        dft_re[k] = re;
        dft_im[k] = im;
        have_band = 1;
    }

    if (!have_band) {
        return;
    }

    double env_sum = 0.0;
    double env_min = 0.0;
    double env_max = 0.0;
    const double scale = 2.0 / (double)n;
    for (size_t i = 0; i < n; i++) {
        double zr = 0.0;
        double zi = 0.0;
        for (size_t k = 1; k <= half; k++) {
            if (dft_re[k] == 0.0 && dft_im[k] == 0.0) {
                continue;
            }
            const double angle = 2.0 * M_PI * (double)k * (double)i / (double)n;
            const double c = cos(angle);
            const double s = sin(angle);
            zr += scale * (dft_re[k] * c - dft_im[k] * s);
            zi += scale * (dft_re[k] * s + dft_im[k] * c);
        }
        env[i] = sqrt(zr * zr + zi * zi);
        if (i == 0 || env[i] < env_min) env_min = env[i];
        if (i == 0 || env[i] > env_max) env_max = env[i];
        env_sum += env[i];
    }

    const double env_mean = env_sum / (double)n;
    double env_sumsq = 0.0;
    for (size_t i = 0; i < n; i++) {
        const double centered = env[i] - env_mean;
        env_sumsq += centered * centered;
    }

    *env_rms = sqrt(env_sumsq / (double)n);
    *env_p2p = env_max - env_min;
}

static int extract_features(const wear_fault_sample_t *samples, size_t n, double fs,
                            feature_values_t *out) {
    static double sig[WEAR_FAULT_MAX_SAMPLES];

    double mag_sum = 0.0;
    double z_sum = 0.0;
    for (size_t i = 0; i < n; i++) {
        const double ax = (double)samples[i].ax;
        const double ay = (double)samples[i].ay;
        const double az = (double)samples[i].az;
        if (!isfinite(ax) || !isfinite(ay) || !isfinite(az)) {
            return 0;
        }
        sig[i] = sqrt(ax * ax + ay * ay + az * az);
        mag_sum += sig[i];
        z_sum += az;
    }

    const double mag_mean = mag_sum / (double)n;
    const double z_mean = z_sum / (double)n;
    double sumsq = 0.0;
    double z_sumsq = 0.0;
    double jerk_sum = 0.0;
    double minv = 0.0;
    double maxv = 0.0;

    for (size_t i = 0; i < n; i++) {
        sig[i] -= mag_mean;
        if (i == 0 || sig[i] < minv) minv = sig[i];
        if (i == 0 || sig[i] > maxv) maxv = sig[i];
        sumsq += sig[i] * sig[i];
        if (i > 0) {
            jerk_sum += fabs(sig[i] - sig[i - 1U]);
        }
        const double z = (double)samples[i].az - z_mean;
        z_sumsq += z * z;
    }

    out->a_rms = sqrt(sumsq / (double)n);
    out->a_std = out->a_rms;
    out->a_p2p = maxv - minv;
    out->a_jerk_mad = jerk_sum / (double)(n - 1U);
    out->a_rms_z = sqrt(z_sumsq / (double)n);
    high_band_envelope_features(sig, n, fs, &out->a_env_rms, &out->a_env_p2p);
    return 1;
}

wear_fault_result_t wear_fault_detect_window(const wear_fault_sample_t *samples,
                                             size_t sample_count,
                                             float sample_rate_hz,
                                             const wear_fault_baseline_t *baseline) {
    if (samples == 0 || sample_count < MIN_SAMPLES ||
        sample_count > WEAR_FAULT_MAX_SAMPLES ||
        !isfinite((double)sample_rate_hz) || sample_rate_hz <= 0.0f ||
        !valid_baseline(baseline)) {
        return invalid_result();
    }

    feature_values_t f;
    if (!extract_features(samples, sample_count, (double)sample_rate_hz, &f)) {
        return invalid_result();
    }

    const double env_rms_ratio = ratio(f.a_env_rms, baseline->a_env_rms);
    const double env_p2p_ratio = ratio(f.a_env_p2p, baseline->a_env_p2p);
    const double jerk_ratio = ratio(f.a_jerk_mad, baseline->a_jerk_mad);
    const double rms_ratio = ratio(f.a_rms, baseline->a_rms);
    const double std_ratio = ratio(f.a_std, baseline->a_std);
    const double p2p_ratio = ratio(f.a_p2p, baseline->a_p2p);
    const double rms_z_ratio = ratio(f.a_rms_z, baseline->a_rms_z);
    const double energy_ratio = max4(rms_ratio, std_ratio, p2p_ratio, rms_z_ratio);

    const int is_fault = env_rms_ratio >= ENV_RMS_THRESHOLD &&
                         env_p2p_ratio >= ENV_P2P_THRESHOLD &&
                         jerk_ratio >= JERK_THRESHOLD &&
                         energy_ratio >= ENERGY_THRESHOLD;

    const double progress = min4(env_rms_ratio / ENV_RMS_THRESHOLD,
                                 env_p2p_ratio / ENV_P2P_THRESHOLD,
                                 jerk_ratio / JERK_THRESHOLD,
                                 energy_ratio / ENERGY_THRESHOLD);

    wear_fault_result_t result;
    result.status = is_fault ? WEAR_FAULT_STATUS_FAULT : WEAR_FAULT_STATUS_OK;
    result.fault_percent = (float)clamp_percent(progress * 100.0);
    result.anomaly_score = (float)progress;
    return result;
}