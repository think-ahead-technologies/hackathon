// ABOUTME: Ethos-U55 NPU inference on CM55 via ml-middleware — embedded Vela model -> L2 score.
// ABOUTME: mtb_ml_model_init brings up the NPU (mtb_ml_ethosu_init) internally; no DeepCraft needed.

#include "npu_infer.h"

#include "mtb_ml.h"
#include "model_vela_data.h"
#include "score.h"

// Tensor arena for the model runtime. The Vela summary reports ~11 KB SRAM feature maps, so
// 128 KB is comfortably generous. NULL buffer -> ml-middleware allocates from the heap (CM55
// has a multi-MB SOCMEM heap). Sized via the model container's arena_size field.
#define NPU_ARENA_BYTES (128u * 1024u)

static mtb_ml_model_t *g_model = NULL;

// NVIC priority for the Ethos-U55 completion IRQ (lower = higher; mid-range is fine).
#define NPU_IRQ_PRIORITY 3u

bool npu_infer_init(void) {
    // Bring up the NPU: mtb_ml_init() wires the Ethos-U55 interrupt (mxu55_interrupt_npu_IRQn ->
    // ethosu_irq_handler) and enables the U55. WITHOUT this, mtb_ml_model_run dispatches to the
    // NPU but blocks forever in ethosu_semaphore_take — the completion IRQ never fires.
    if (mtb_ml_init(NPU_IRQ_PRIORITY) != CY_RSLT_SUCCESS) {
        return false;
    }
    // Fill the model container directly from the embedded Vela bytes — the ML Configurator /
    // DeepCraft codegen normally produces this, but the struct is plain (name + bytes + size +
    // arena), so we populate it by hand (designated initializer; model_size/arena_size are
    // const members) and stay fully headless.
    mtb_ml_model_bin_t bin = {
        .name       = "wear",
        .model_bin  = g_vela_model_data,
        .model_size = g_vela_model_len,
        .arena_size = NPU_ARENA_BYTES,
    };
    return mtb_ml_model_init(&bin, NULL, &g_model) == CY_RSLT_SUCCESS;
}

float npu_infer(const int8_t *features, int len) {
    (void)len;
    if (g_model == NULL) {
        return -1.0f;
    }
    // int8 model: MTB_ML_DATA_T is int8_t under COMPONENT_ML_INT8x8, so the int8 features
    // are passed straight through (no requantization).
    if (mtb_ml_model_run(g_model, (MTB_ML_DATA_T *)features) != CY_RSLT_SUCCESS) {
        return -1.0f;
    }
    MTB_ML_DATA_T *out = NULL;
    int out_size = 0;
    if (mtb_ml_model_get_output(g_model, &out, &out_size) != CY_RSLT_SUCCESS || out == NULL) {
        return -1.0f;
    }
    // Output is the [1,8] int8 embedding; score_distance dequantizes + L2-distances to centroid.
    int8_t emb[SCORE_EMBED_DIM];
    for (int k = 0; k < SCORE_EMBED_DIM; k++) {
        emb[k] = (k < out_size) ? (int8_t)out[k] : 0;
    }
    return score_distance(emb);
}
