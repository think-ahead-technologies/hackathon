/*******************************************************************************
 * File Name        : camera_fwd.c
 *
 * Description      : Forwards JPEG camera frames published by the CM55 (see
 *                    shared/cam_shm.h and proj_cm55/camera/) to the Wi-Fi TCP
 *                    client as type-0x30 frames. Camera frames flow whenever
 *                    a TCP client is connected — a live preview independent
 *                    of the IMU start/stop commands; the host bridge records
 *                    them to the log only while a log is open.
 *
 *  A frame torn by the CM55 overwriting a slot mid-send fails the wire CRC
 *  and is dropped by the receiver; the seqlock makes that rare (the writer
 *  ping-pongs between two slots).
 *******************************************************************************/

#include "camera_fwd.h"
#include "tcp_stream.h"
#include "uart_stream.h"
#include "cam_shm.h"

#include "FreeRTOS.h"
#include "task.h"

#define CAMERA_FWD_TASK_NAME       ("Camera Fwd Task")
#define CAMERA_FWD_TASK_STACK_SIZE (configMINIMAL_STACK_SIZE * 4)
#define CAMERA_FWD_TASK_PRIORITY   (configMAX_PRIORITIES - 4)
#define CAMERA_FWD_POLL_MS         (20u)

static void camera_fwd_task(void *arg)
{
    (void)arg;
    cam_shm_hdr_t *shm = CAM_SHM_HDR;
    uint32_t last_sent_id = 0u;
    uint32_t last_state   = 0xFFFFFFFFu;

    for (;;)
    {
        vTaskDelay(pdMS_TO_TICKS(CAMERA_FWD_POLL_MS));

        if (shm->magic != CAM_SHM_MAGIC)       /* CM55 not up yet */
        {
            continue;
        }

        /* Surface CM55 camera state changes on the console (the CM55 has no
         * UART of its own). */
        uint32_t state = shm->camera_state;
        if (state != last_state)
        {
            last_state = state;
            switch (state)
            {
                case CAM_STATE_NO_CAMERA:
                    uart_stream_print("[cam] no camera connected\r\n");
                    break;
                case CAM_STATE_CONNECTED:
                    uart_stream_print("[cam] camera detected, configuring\r\n");
                    break;
                case CAM_STATE_STREAMING:
                    uart_stream_print("[cam] camera streaming\r\n");
                    break;
                case CAM_STATE_UNSUPPORTED:
                    uart_stream_print("[cam] camera not usable "
                                      "(needs uncompressed 320x240)\r\n");
                    break;
                default:
                    break;
            }
        }
        if (!tcp_stream_connected())
        {
            last_sent_id = shm->frame_id;      /* don't burst a backlog */
            continue;
        }

        uint32_t frame_id = shm->frame_id;
        if (frame_id == last_sent_id)
        {
            continue;
        }

        uint32_t slot = shm->latest_slot;
        if (slot >= CAM_SHM_NUM_SLOTS)
        {
            continue;
        }

        /* Reserve the slot before reading it so the CM55 encoder won't recycle
         * it out from under us while we transmit it straight out of SOCMEM. */
        shm->reader_slot = slot;
        __DMB();

        /* Validate the seqlock *after* publishing the reservation: if the
         * writer was already mid-update (odd) or has since moved on, release
         * and retry next poll. */
        uint32_t seq_before = shm->seq[slot];
        if ((seq_before & 1u) ||
            (shm->latest_slot != slot) ||
            (shm->frame_id != frame_id))
        {
            shm->reader_slot = CAM_SLOT_NONE;
            __DMB();
            continue;
        }
        __DMB();
        uint32_t size = shm->size[slot];
        if ((size == 0u) || (size > CAM_JPEG_MAX))
        {
            shm->reader_slot = CAM_SLOT_NONE;
            __DMB();
            continue;
        }

        bool sent = tcp_stream_send_camera(frame_id,
                                           shm->width, shm->height,
                                           CAM_SHM_SLOT(slot), size);

        /* Confirm the slot stayed stable across the (slow) Wi-Fi send. With the
         * reservation honoured this always holds; if a rare race let the writer
         * touch it anyway, treat the frame as torn and don't advance, so the
         * next frame is sent fresh rather than a corrupt one counted as sent. */
        __DMB();
        bool stable = (shm->seq[slot] == seq_before);
        shm->reader_slot = CAM_SLOT_NONE;
        __DMB();

        if (sent && stable)
        {
            last_sent_id = frame_id;
        }
    }
}

bool camera_fwd_create_task(void)
{
    return (pdPASS == xTaskCreate(camera_fwd_task, CAMERA_FWD_TASK_NAME,
                                  CAMERA_FWD_TASK_STACK_SIZE, NULL,
                                  CAMERA_FWD_TASK_PRIORITY, NULL));
}
