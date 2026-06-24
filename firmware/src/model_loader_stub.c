// ABOUTME: No-TFLM stand-in for model_loader.cc, used by the connectivity-first
// ABOUTME: firmware-app build so the Wi-Fi/NATS publish path runs without the ML runtime.

// The real implementation (model_loader.cc) wires TFLM + the Ethos-U kernel over the
// flash-resident model slots. That's the "full image" scope. For the connectivity-first
// build we only need the device to come up, publish Contract B, and exercise the
// deploy/shadow control flow — so inference here is a deterministic placeholder.
//
// IMPORTANT: this fabricates no signal. infer() returns the mean absolute value of the
// feature window (0 while the BSP feature extractor is still a TODO), and reports no
// candidate. Swap this file out for model_loader.cc to get real on-device inference.

#include "model_loader.h"

#include <stdint.h>
#include <stddef.h>

bool model_loader_load_active(slot_id_t slot) {
    (void)slot;
    return true;
}

bool model_loader_load_candidate(slot_id_t slot) {
    (void)slot;
    return true;
}

float model_loader_infer(const int8_t *features, size_t len,
                         float *candidate_out, bool *have_candidate) {
    if (have_candidate) {
        *have_candidate = false;   // no candidate interpreter in the stub
    }
    if (candidate_out) {
        *candidate_out = 0.0f;
    }
    // Deterministic, non-fabricated: mean |feature|. 0 until the feature extractor lands.
    if (features == NULL || len == 0) {
        return 0.0f;
    }
    uint32_t acc = 0;
    for (size_t i = 0; i < len; i++) {
        int v = features[i];
        acc += (uint32_t)(v < 0 ? -v : v);
    }
    return (float)acc / (float)len;
}

void model_loader_clear_candidate(void) {
}
