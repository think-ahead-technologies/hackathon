// ABOUTME: Cross-core camera mailbox — CM55 (USB capture + JPEG) -> CM33-NS (publish to edge.camera).
// ABOUTME: Fixed-address overlay in m33_m55_shared SOCMEM, PARTITIONED above the score mailboxes.

#ifndef CAM_SHM_H
#define CAM_SHM_H

#include <stdint.h>

#include "shared_score.h"   // SHARED_SOCMEM_BASE, SHARED_CTRL_OFFSET, shared_model_ctrl_t

#ifdef __cplusplus
extern "C" {
#endif

// Both the score mailbox (shared_score.h) and this camera mailbox live in the SAME 256 KB
// m33_m55_shared SOCMEM region at SHARED_SOCMEM_BASE. In the same-unit firmware they coexist in one
// binary, so the region is partitioned: the score + model-control mailboxes sit at the region base;
// the camera mailbox starts CAM_SHM_OFFSET bytes in. The _Static_asserts at the end of this header
// fail the build if the two ever overlap or the camera slots run past the region end.
#define CAM_SHM_REGION_SIZE     (0x40000u)        // 256 KB, both linkers reserve this
#define CAM_SHM_OFFSET          (0x4000u)         // 16 KB in — clear of the score/ctrl mailboxes
#define CAM_SHM_BASE            (SHARED_SOCMEM_BASE + CAM_SHM_OFFSET)

#define CAM_SHM_MAGIC           (0x314D4143u)      // "CAM1" (LE) — set by CM55 once initialized
// Three slots: one being read by CM33 (reader_slot), one holding the latest published frame
// (latest_slot), and one always free for the CM55 to encode into. Two would force the encoder to
// reuse the in-flight slot and tear frames mid-send.
#define CAM_SHM_NUM_SLOTS       (3u)
// Sentinel for reader_slot/latest_slot meaning "no slot": any out-of-range index works since
// consumers bound-check against CAM_SHM_NUM_SLOTS.
#define CAM_SLOT_NONE           (CAM_SHM_NUM_SLOTS)
#define CAM_SHM_SLOT_SIZE       (64u * 1024u)
#define CAM_JPEG_MAX            (CAM_SHM_SLOT_SIZE - 16u)

// camera_state values
#define CAM_STATE_NO_CAMERA     (0u)
#define CAM_STATE_CONNECTED     (1u)
#define CAM_STATE_STREAMING     (2u)
#define CAM_STATE_UNSUPPORTED   (3u)

typedef struct {
    volatile uint32_t magic;        // CAM_SHM_MAGIC once CM55 initialized
    volatile uint32_t camera_state; // CAM_STATE_...
    volatile uint16_t width;        // source frame dimensions
    volatile uint16_t height;
    volatile uint32_t frame_id;     // id of the latest published frame
    volatile uint32_t latest_slot;  // slot holding frame_id (CAM_SLOT_NONE = none)
    volatile uint32_t reader_slot;  // slot the CM33 is reading (CAM_SLOT_NONE = none)
    volatile uint32_t seq[CAM_SHM_NUM_SLOTS];   // per-slot seqlock (odd = writer active)
    volatile uint32_t size[CAM_SHM_NUM_SLOTS];  // JPEG byte count per slot
    volatile uint32_t frames_captured;          // raw frames seen (diag)
    volatile uint32_t frames_published;         // encoded frames (diag)
    volatile uint32_t encode_errors;            // diag
} cam_shm_hdr_t;

// Slots start at a fixed offset so the header can grow a little without moving the data.
#define CAM_SHM_SLOT_OFFSET     (256u)

#define CAM_SHM_HDR             ((cam_shm_hdr_t *)CAM_SHM_BASE)
#define CAM_SHM_SLOT(n)         ((uint8_t *)(CAM_SHM_BASE + CAM_SHM_SLOT_OFFSET + \
                                             (n) * CAM_SHM_SLOT_SIZE))

// --- compile-time layout guards (the same-unit binary holds BOTH mailboxes) ------------------
_Static_assert(CAM_SHM_OFFSET >= SHARED_CTRL_OFFSET + sizeof(shared_model_ctrl_t),
               "cam_shm base overlaps the score / model-control mailboxes");
_Static_assert(CAM_SHM_OFFSET + CAM_SHM_SLOT_OFFSET + CAM_SHM_NUM_SLOTS * CAM_SHM_SLOT_SIZE
                   <= CAM_SHM_REGION_SIZE,
               "cam_shm slots exceed the m33_m55_shared region");

#ifdef __cplusplus
}
#endif

#endif  // CAM_SHM_H
