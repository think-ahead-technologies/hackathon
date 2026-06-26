// ABOUTME: Tests for camera change-detection — skip publishing frames visually identical to the last.
// ABOUTME: Pure logic over a downsampled luma signature; drives the CM55 encoder's frame-skip.

#include <string.h>

#include "cam_change.h"
#include "test_util.h"

// Fill a WxH YUYV buffer with uniform luma `y` (Y at even bytes) and neutral chroma.
static void fill_yuyv(uint8_t *buf, int w, int h, uint8_t y) {
    for (int i = 0; i < w * h; i++) {
        buf[i * 2]     = y;
        buf[i * 2 + 1] = 128;
    }
}

void run_cam_change_tests(void) {
    enum { W = 64, H = 48 };
    static uint8_t frame[W * H * 2];
    cam_change_state_t st;
    memset(&st, 0, sizeof(st));

    // First frame is always "changed" (no reference yet) -> publish, and it seeds the reference.
    fill_yuyv(frame, W, H, 100);
    CHECK(cam_frame_changed(frame, W, H, &st, 1000u) == true);

    // Identical frame -> not changed -> skip.
    CHECK(cam_frame_changed(frame, W, H, &st, 1000u) == false);

    // Large uniform luma shift (SAD = 192*100) -> changed; new level becomes the reference.
    fill_yuyv(frame, W, H, 200);
    CHECK(cam_frame_changed(frame, W, H, &st, 1000u) == true);
    CHECK(cam_frame_changed(frame, W, H, &st, 1000u) == false);

    // Tiny change below threshold (SAD = 192*1 = 192 < 1000) -> skip, reference stays at 200.
    fill_yuyv(frame, W, H, 201);
    CHECK(cam_frame_changed(frame, W, H, &st, 1000u) == false);

    // Reference did NOT drift to 201: 202 vs 200 with threshold 0 -> any difference is "changed".
    fill_yuyv(frame, W, H, 202);
    CHECK(cam_frame_changed(frame, W, H, &st, 0u) == true);
}
