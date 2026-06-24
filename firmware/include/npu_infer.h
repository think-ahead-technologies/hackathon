// ABOUTME: Ethos-U55 NPU inference (CM55) — runs the embedded Vela wear model via ml-middleware.
// ABOUTME: Returns the L2-to-centroid anomaly score; on-target only (needs ml-middleware + U55).

#ifndef NPU_INFER_H
#define NPU_INFER_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Initialize the NPU model runtime (loads the Vela flatbuffer, brings up the Ethos-U55 via
// ml-middleware's mtb_ml_ethosu_init). Returns false on failure. Call once at startup.
bool npu_infer_init(void);

// Run one inference: `features` is the int8 [49*40] spectrogram (FEAT_OUT_LEN). Returns the
// anomaly score (L2 distance of the dequantized [1,8] embedding to the healthy centroid),
// or a negative value on error.
float npu_infer(const int8_t *features, int len);

#ifdef __cplusplus
}
#endif

#endif  // NPU_INFER_H
