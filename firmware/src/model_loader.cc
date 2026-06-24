// ABOUTME: TFLite Micro load/reload over flash-resident slots — the real TFLM API + HAL placeholders.
// ABOUTME: BUILT ON-TARGET ONLY (needs TFLM + the Ethos-U kernel). Not part of the host test build.

// NOTE: this file is the integration glue, not host-testable logic. The decision logic it
// relies on (slot selection, contract check, shadow verdict) is the pure, tested C in src/.

#include "model_loader.h"

#include <string.h>

#include "platform_hal.h"

// --- TFLM headers (present in the on-target ModusToolbox/CMSIS build) --------
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/micro/kernels/ethos_u/ethos_u.h"   // AddEthosU
#include "tensorflow/lite/schema/schema_generated.h"

namespace {

// One fixed arena per interpreter. Sized to the worst-case model; cannot grow at runtime.
alignas(16) uint8_t g_active_arena[TENSOR_ARENA_BYTES];
alignas(16) uint8_t g_candidate_arena[TENSOR_ARENA_BYTES];

tflite::MicroInterpreter *g_active = nullptr;
tflite::MicroInterpreter *g_candidate = nullptr;

// Resolver: register the Ethos-U custom op so NPU subgraphs run on the U55, plus any
// CPU-fallback ops the model uses. Keep this list in sync with the model's operator set.
tflite::MicroMutableOpResolver<8> &resolver() {
    static tflite::MicroMutableOpResolver<8> r;
    static bool init = false;
    if (!init) {
        r.AddEthosU();
        // r.AddConv2D(); r.AddFullyConnected(); ... for any ops that fall back to the M55.
        init = true;
    }
    return r;
}

// Map a verified slot's flatbuffer and build an interpreter over the given arena.
tflite::MicroInterpreter *load_slot(slot_id_t slot, uint8_t *arena) {
    model_meta_t meta;
    if (!hal_meta_read(&meta)) return nullptr;
    const slot_meta_t *s = &meta.slot[slot];

    // Map QSPI -> address space for read-in-place (or copy into HYPERRAM for speed).
    const uint8_t *p = hal_flash_xip_map(s->flash_offset);
    if (p == nullptr) return nullptr;

    // Integrity check at load: the signature (over the manifest) was verified at deploy time;
    // here we re-confirm the flatbuffer still matches the digest that signed manifest bound — this
    // catches flash bit-rot / partial writes. (For secure-boot-grade re-verification on every boot,
    // persist the signed manifest and re-run hal_verify_signature over it here instead.)
    uint8_t got[32];
    if (!hal_sha256(p, s->len, got) || memcmp(got, s->sha256, 32) != 0) return nullptr;

    const tflite::Model *m = tflite::GetModel(p);
    if (m->version() != TFLITE_SCHEMA_VERSION) return nullptr;

    auto *interp = new tflite::MicroInterpreter(m, resolver(), arena, TENSOR_ARENA_BYTES);
    if (interp->AllocateTensors() != kTfLiteOk) {
        delete interp;
        return nullptr;
    }
    return interp;
}

// Single-output anomaly score, dequantized from the int8 output tensor.
float read_score(tflite::MicroInterpreter *interp) {
    TfLiteTensor *out = interp->output(0);
    int8_t q = out->data.int8[0];
    return (q - out->params.zero_point) * out->params.scale;
}

void write_input(tflite::MicroInterpreter *interp, const int8_t *features, size_t len) {
    TfLiteTensor *in = interp->input(0);
    for (size_t i = 0; i < len; i++) {
        in->data.int8[i] = features[i];
    }
}

}  // namespace

extern "C" {

bool model_loader_load_active(slot_id_t slot) {
    tflite::MicroInterpreter *next = load_slot(slot, g_active_arena);
    if (next == nullptr) return false;
    delete g_active;     // drop the previous interpreter
    g_active = next;
    return true;
}

bool model_loader_load_candidate(slot_id_t slot) {
    tflite::MicroInterpreter *next = load_slot(slot, g_candidate_arena);
    if (next == nullptr) return false;
    delete g_candidate;
    g_candidate = next;
    return true;
}

float model_loader_infer(const int8_t *features, size_t len,
                         float *candidate_out, bool *have_candidate) {
    write_input(g_active, features, len);
    g_active->Invoke();
    float score = read_score(g_active);

    *have_candidate = (g_candidate != nullptr);
    if (g_candidate != nullptr) {
        write_input(g_candidate, features, len);
        g_candidate->Invoke();
        *candidate_out = read_score(g_candidate);
    }
    return score;
}

void model_loader_clear_candidate(void) {
    delete g_candidate;
    g_candidate = nullptr;
}

}  // extern "C"
