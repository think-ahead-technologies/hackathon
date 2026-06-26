// ABOUTME: Camera change-detection — see cam_change.h. Downsampled-luma SAD compare, pure + tested.
// ABOUTME: Host-tested (test/test_cam_change.c); called by the CM55 encoder to skip static frames.

#include "cam_change.h"

#include <stddef.h>
#include <string.h>

// Sample the coarse luma grid from a YUYV frame into `out` (CAM_SIG_LEN bytes).
static void sample_signature(const uint8_t *yuyv, int width, int height, uint8_t *out) {
    for (int r = 0; r < CAM_SIG_ROWS; r++) {
        int y = r * height / CAM_SIG_ROWS;
        for (int c = 0; c < CAM_SIG_COLS; c++) {
            int x = c * width / CAM_SIG_COLS;
            // YUYV: 2 bytes per pixel, luma (Y) at the even byte of each pixel pair.
            out[r * CAM_SIG_COLS + c] = yuyv[((size_t)y * (size_t)width + (size_t)x) * 2u];
        }
    }
}

bool cam_frame_changed(const uint8_t *yuyv, int width, int height,
                       cam_change_state_t *st, uint32_t threshold) {
    uint8_t sig[CAM_SIG_LEN];
    sample_signature(yuyv, width, height, sig);

    if (!st->have) {
        memcpy(st->sig, sig, CAM_SIG_LEN);
        st->have = true;
        return true;                       // first frame always publishes
    }

    uint32_t sad = 0;
    for (int i = 0; i < CAM_SIG_LEN; i++) {
        int d = (int)sig[i] - (int)st->sig[i];
        sad += (uint32_t)(d < 0 ? -d : d);
    }

    if (sad > threshold) {
        memcpy(st->sig, sig, CAM_SIG_LEN);  // advance the reference to the just-published frame
        return true;
    }
    return false;                           // visually unchanged -> skip (reference unchanged)
}
