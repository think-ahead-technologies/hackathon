/*******************************************************************************
 * File Name        : cam_shm.h
 *
 * Description      : Cross-core camera frame hand-off. The CM55 captures USB
 *                    webcam frames, JPEG-encodes them, and publishes them into
 *                    the m33_m55_shared SOCMEM region (0x262FC000, 256 KB,
 *                    visible to both cores at the same address). The CM33
 *                    polls for new frames and forwards them over the Wi-Fi
 *                    TCP stream as type-0x30 frames.
 *
 *  Concurrency: single writer (CM55 encoder task), single reader (CM33
 *  forwarder task). Per-slot sequence counters form a seqlock: the writer
 *  increments seq to odd before writing and to even after; the reader
 *  validates seq is even and unchanged around its use of the data.
 *
 *  Because the CM33 transmits a frame straight out of its slot over Wi-Fi —
 *  which takes far longer than one capture interval — the reader publishes
 *  the slot it is busy with in `reader_slot`, and the writer avoids both that
 *  slot and the currently published `latest_slot` when choosing where to
 *  encode next. With CAM_SHM_NUM_SLOTS >= 3 a free slot always exists, so the
 *  two cores never touch the same slot and frames are not torn mid-send. The
 *  seqlock remains as a backstop for the brief reservation-visibility window.
 *
 *  This header is shared between proj_cm55 and proj_cm33_ns (INCLUDES+=../shared).
 *******************************************************************************/

#ifndef CAM_SHM_H
#define CAM_SHM_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* m33_m55_shared region (see bsps/.../cymem_*.sct): same VA on both cores. */
#define CAM_SHM_BASE            (0x262FC000u)
#define CAM_SHM_REGION_SIZE     (0x40000u)          /* 256 KB */

#define CAM_SHM_MAGIC           (0x314D4143u)       /* "CAM1" (LE) */
/* Three slots: one being transmitted by the CM33 (reader_slot), one holding
 * the latest published frame (latest_slot), and one always free for the CM55
 * to encode into. Two slots would force the encoder to reuse the in-flight
 * slot and tear frames mid-send. */
#define CAM_SHM_NUM_SLOTS       (3u)
/* Sentinel for reader_slot/latest_slot meaning "no slot": any out-of-range
 * index works since consumers bound-check against CAM_SHM_NUM_SLOTS. */
#define CAM_SLOT_NONE           (CAM_SHM_NUM_SLOTS)
/* Slot cap also bounded by the wire protocol: frame payload len is u16, so
 * 8-byte camera header + JPEG must stay below 65536. */
#define CAM_SHM_SLOT_SIZE       (64u * 1024u)
#define CAM_JPEG_MAX            (CAM_SHM_SLOT_SIZE - 16u)

/* camera_state values */
#define CAM_STATE_NO_CAMERA     (0u)
#define CAM_STATE_CONNECTED     (1u)
#define CAM_STATE_STREAMING     (2u)
#define CAM_STATE_UNSUPPORTED   (3u)

typedef struct
{
    volatile uint32_t magic;        /* CAM_SHM_MAGIC once CM55 initialized */
    volatile uint32_t camera_state; /* CAM_STATE_... */
    volatile uint16_t width;        /* source frame dimensions */
    volatile uint16_t height;
    volatile uint32_t frame_id;     /* id of the latest published frame */
    volatile uint32_t latest_slot;  /* slot holding frame_id (CAM_SLOT_NONE = none) */
    volatile uint32_t reader_slot;  /* slot the CM33 is transmitting (CAM_SLOT_NONE = none) */
    volatile uint32_t seq[CAM_SHM_NUM_SLOTS];   /* seqlock per slot */
    volatile uint32_t size[CAM_SHM_NUM_SLOTS];  /* JPEG byte count per slot */
    volatile uint32_t frames_captured;          /* raw frames seen (diag) */
    volatile uint32_t frames_published;         /* encoded frames (diag) */
    volatile uint32_t encode_errors;            /* diag */
} cam_shm_hdr_t;

/* Slots start at a fixed offset so the header can grow a little without
 * moving the data. */
#define CAM_SHM_SLOT_OFFSET     (256u)

#define CAM_SHM_HDR             ((cam_shm_hdr_t *)CAM_SHM_BASE)
#define CAM_SHM_SLOT(n)         ((uint8_t *)(CAM_SHM_BASE + CAM_SHM_SLOT_OFFSET + \
                                             (n) * CAM_SHM_SLOT_SIZE))

/* Wire protocol: camera frame type (alongside 0x10 IMU / 0x20 STATUS).
 * Payload layout (little-endian):
 *   uint32  frame_id
 *   uint16  width
 *   uint16  height
 *   uint8[] jpeg bitstream
 */
#define CAM_FRAME_TYPE          (0x30u)
#define CAM_FRAME_HDR_LEN       (8u)

#ifdef __cplusplus
}
#endif

#endif /* CAM_SHM_H */
