// ABOUTME: Camera HAL (CM33 side) — read JPEG frames the CM55 publishes into the cam_shm SOCMEM
// ABOUTME: slots, with the slot-reservation + seqlock handshake, for camera_publish -> edge.camera.

// The CM55 owns the producer side (USB capture + JPEG into cam_shm; ported in a later phase). This
// is the consumer: it hands camera_publish a pointer straight into the reserved SOCMEM slot (no
// copy) and holds the reservation across the publish so the encoder cannot recycle the slot
// mid-send. Logic mirrors data-pipeline-firmware's camera_fwd.c, reshaped to the hal_camera_* seam.
// Returns no frame until the CM55 has initialized the region (magic gate), so it is safe to run
// before — or without — the producer.

#include <stddef.h>
#include <stdint.h>

#include "platform_hal.h"
#include "cam_shm.h"

// Cross-core memory ordering against the CM55 producer. __DMB() on-target; a no-op where the
// intrinsic is unavailable (this file is built on-target only, alongside the CM33-NS app).
#if defined(__has_include)
#  if __has_include("cmsis_compiler.h")
#    include "cmsis_compiler.h"
#    define CAM_HAL_DMB() __DMB()
#  endif
#endif
#ifndef CAM_HAL_DMB
#  define CAM_HAL_DMB() ((void)0)
#endif

// Single reader (this core). The slot/seq/frame held between frame_get and frame_release, and the
// last frame_id we committed as published (advanced only on a stable release, so a torn frame is
// retried rather than counted as sent).
static uint32_t g_last_frame_id;
static uint32_t g_held_slot = CAM_SLOT_NONE;
static uint32_t g_held_seq;
static uint32_t g_held_frame_id;

bool hal_camera_init(void) {
    // The CM55 owns the producer; nothing to bring up here. frame_get gates on the cam_shm magic,
    // so it safely yields no frame until the CM55 has initialized the region.
    g_held_slot = CAM_SLOT_NONE;
    return true;
}

bool hal_camera_frame_get(const uint8_t **jpeg, uint32_t *len, hal_cam_meta_t *meta) {
    cam_shm_hdr_t *shm = CAM_SHM_HDR;
    if (shm->magic != CAM_SHM_MAGIC) return false;          // CM55 not up yet

    uint32_t frame_id = shm->frame_id;
    if (frame_id == g_last_frame_id) return false;          // nothing new since last publish

    uint32_t slot = shm->latest_slot;
    if (slot >= CAM_SHM_NUM_SLOTS) return false;

    // Reserve the slot before reading so the CM55 encoder won't recycle it out from under us.
    shm->reader_slot = slot;
    CAM_HAL_DMB();

    // Validate the seqlock after publishing the reservation: if the writer was mid-update (odd seq)
    // or has since moved on, release and report no frame; the next poll retries.
    uint32_t seq_before = shm->seq[slot];
    if ((seq_before & 1u) || (shm->latest_slot != slot) || (shm->frame_id != frame_id)) {
        shm->reader_slot = CAM_SLOT_NONE;
        CAM_HAL_DMB();
        return false;
    }
    CAM_HAL_DMB();

    uint32_t size = shm->size[slot];
    if (size == 0u || size > CAM_JPEG_MAX) {
        shm->reader_slot = CAM_SLOT_NONE;
        CAM_HAL_DMB();
        return false;
    }

    g_held_slot     = slot;
    g_held_seq      = seq_before;
    g_held_frame_id = frame_id;

    *jpeg          = CAM_SHM_SLOT(slot);   // straight into SOCMEM — no copy
    *len           = size;
    meta->frame_id = frame_id;
    meta->width    = shm->width;
    meta->height   = shm->height;
    meta->t_us     = shm->t_us[slot];   // device capture time the CM55 stamped for this slot
    return true;
}

void hal_camera_frame_release(void) {
    if (g_held_slot >= CAM_SHM_NUM_SLOTS) return;           // nothing held
    cam_shm_hdr_t *shm = CAM_SHM_HDR;

    // Confirm the slot stayed stable across the (slow) publish. If a rare race let the writer touch
    // it, do NOT commit last_frame_id, so the next frame is sent fresh rather than a torn one counted
    // as published. The reservation made this near-impossible; the seqlock is the backstop.
    CAM_HAL_DMB();
    bool stable = (shm->seq[g_held_slot] == g_held_seq);
    shm->reader_slot = CAM_SLOT_NONE;
    CAM_HAL_DMB();

    if (stable) g_last_frame_id = g_held_frame_id;
    g_held_slot = CAM_SLOT_NONE;
}
