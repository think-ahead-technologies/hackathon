// ABOUTME: Camera change-detection — cheap downsampled-luma compare to skip near-identical frames.
// ABOUTME: Pure (no hardware); lets the CM55 encoder avoid JPEG-encoding+publishing static scenes.

#ifndef CAM_CHANGE_H
#define CAM_CHANGE_H

#include <stdbool.h>
#include <stdint.h>

// The frame signature is a coarse luma grid sampled from the full frame — small enough to compare
// in a few hundred byte reads, coarse enough to ignore sensor noise.
#define CAM_SIG_COLS  16
#define CAM_SIG_ROWS  12
#define CAM_SIG_LEN   (CAM_SIG_COLS * CAM_SIG_ROWS)   // 192 luma samples

typedef struct {
    uint8_t sig[CAM_SIG_LEN];   // downsampled luma of the last ACCEPTED (published) frame
    bool    have;               // false until the first frame seeds the signature
} cam_change_state_t;

// Decide whether a YUYV frame differs enough from the last accepted one to be worth publishing.
// Samples a CAM_SIG_COLS x CAM_SIG_ROWS luma grid and compares it (sum of absolute differences)
// against `threshold`. Returns true (publish) on the first frame or when SAD > threshold, and then
// updates `st` to the new signature; returns false (skip) otherwise, leaving the reference unchanged
// so a slow drift is measured against the last published frame, not the last seen one.
// `yuyv` is width*height*2 bytes (Y at even offsets); width/height must be > 0.
bool cam_frame_changed(const uint8_t *yuyv, int width, int height,
                       cam_change_state_t *st, uint32_t threshold);

#endif  // CAM_CHANGE_H
