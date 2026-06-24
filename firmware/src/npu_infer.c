// ABOUTME: Ethos-U55 NPU inference on CM55 via ml-middleware — active + candidate models -> L2 score.
// ABOUTME: Models load from QSPI flash slots over SMIF XIP; the baked Vela model is the boot fallback.

#include "npu_infer.h"

#include "mtb_ml.h"
#include "model_vela_data.h"
#include "score.h"

// Tensor arena per model. The Vela summary reports ~11 KB SRAM feature maps, so 128 KB is
// comfortably generous. NULL buffer -> ml-middleware allocates the arena from the heap; CM55 has a
// multi-MB SOCMEM heap, so two live models (active + candidate) fit. Sized via arena_size below.
#define NPU_ARENA_BYTES (128u * 1024u)

// NVIC priority for the Ethos-U55 completion IRQ (lower = higher; mid-range is fine).
#define NPU_IRQ_PRIORITY 3u

// SMIF memory-mapped (XIP) base — model flash offsets are read in place at this address + offset.
// VERIFY: the SMIF XIP region base on the E84 (CY_XIP_BASE on CAT1 parts). Must match the value
// platform_hal_pse84.c's hal_flash_xip_map() uses, since CM33 writes and CM55 reads the same flash.
#define SMIF_XIP_BASE 0x60000000u

// One model role: the interpreter handle plus the scoring params that go with that model.
typedef struct {
    mtb_ml_model_t *model;
    score_params_t  params;
} npu_slot_t;

static npu_slot_t g_slot[2];  // indexed by npu_role_t (NPU_ACTIVE, NPU_CANDIDATE)

// Build an ml-middleware model over `bytes`/`len` and place it in `role`, replacing any prior model
// there. `params` is copied in. Returns false on failure (the role is left empty either way).
static bool load_role(npu_role_t role, const uint8_t *bytes, uint32_t len,
                      const score_params_t *params) {
    if (g_slot[role].model != NULL) {
        mtb_ml_model_deinit(g_slot[role].model);
        g_slot[role].model = NULL;
    }
    mtb_ml_model_bin_t bin = {
        .name       = "wear",
        .model_bin  = bytes,
        .model_size = len,
        .arena_size = NPU_ARENA_BYTES,
    };
    if (mtb_ml_model_init(&bin, NULL, &g_slot[role].model) != CY_RSLT_SUCCESS) {
        g_slot[role].model = NULL;
        return false;
    }
    g_slot[role].params = *params;
    return true;
}

bool npu_infer_init(void) {
    // Bring up the NPU: mtb_ml_init() wires the Ethos-U55 interrupt (mxu55_interrupt_npu_IRQn ->
    // ethosu_irq_handler) and enables the U55. WITHOUT this, mtb_ml_model_run dispatches to the
    // NPU but blocks forever in ethosu_semaphore_take — the completion IRQ never fires.
    if (mtb_ml_init(NPU_IRQ_PRIORITY) != CY_RSLT_SUCCESS) {
        return false;
    }
    // Boot fallback: load the baked-in Vela model as ACTIVE with the compiled-in default scoring
    // params, so a board with empty flash still infers. CM33 later replaces this with the active
    // flash slot via npu_load_slot(NPU_ACTIVE, ...) once metadata is read.
    score_params_t def;
    score_default_params(&def);
    return load_role(NPU_ACTIVE, g_vela_model_data, g_vela_model_len, &def);
}

bool npu_load_slot(npu_role_t role, uint32_t offset, uint32_t len, const score_params_t *params) {
    const uint8_t *bytes = (const uint8_t *)(SMIF_XIP_BASE + offset);
    // CM33 wrote this slot over SMIF; invalidate CM55's D-cache for the range so the NPU reads the
    // freshly programmed bytes, not stale cache lines. (Alternative: mark the XIP region
    // non-cacheable in the MPU and drop this.)
    SCB_InvalidateDCache_by_Addr((void *)bytes, (int32_t)len);
    return load_role(role, bytes, len, params);
}

void npu_promote_candidate(void) {
    if (g_slot[NPU_CANDIDATE].model == NULL) {
        return;
    }
    if (g_slot[NPU_ACTIVE].model != NULL) {
        mtb_ml_model_deinit(g_slot[NPU_ACTIVE].model);
    }
    g_slot[NPU_ACTIVE] = g_slot[NPU_CANDIDATE];   // candidate (model + params) becomes active
    g_slot[NPU_CANDIDATE].model = NULL;
}

void npu_clear_candidate(void) {
    if (g_slot[NPU_CANDIDATE].model != NULL) {
        mtb_ml_model_deinit(g_slot[NPU_CANDIDATE].model);
        g_slot[NPU_CANDIDATE].model = NULL;
    }
}

bool npu_role_loaded(npu_role_t role) {
    return g_slot[role].model != NULL;
}

float npu_run(npu_role_t role, const int8_t *features, int len) {
    (void)len;
    mtb_ml_model_t *model = g_slot[role].model;
    if (model == NULL) {
        return -1.0f;
    }
    // int8 model: MTB_ML_DATA_T is int8_t under COMPONENT_ML_INT8x8, so the int8 features
    // are passed straight through (no requantization).
    if (mtb_ml_model_run(model, (MTB_ML_DATA_T *)features) != CY_RSLT_SUCCESS) {
        return -1.0f;
    }
    MTB_ML_DATA_T *out = NULL;
    int out_size = 0;
    if (mtb_ml_model_get_output(model, &out, &out_size) != CY_RSLT_SUCCESS || out == NULL) {
        return -1.0f;
    }
    // Output is the [1,8] int8 embedding; score_distance_with dequantizes (with this model's output
    // quant) + L2-distances to this model's centroid.
    int8_t emb[SCORE_EMBED_DIM];
    for (int k = 0; k < SCORE_EMBED_DIM; k++) {
        emb[k] = (k < out_size) ? (int8_t)out[k] : 0;
    }
    return score_distance_with(emb, &g_slot[role].params);
}

float npu_infer(const int8_t *features, int len) {
    return npu_run(NPU_ACTIVE, features, len);
}
