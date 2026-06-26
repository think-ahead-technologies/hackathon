/*******************************************************************************
 * File Name        : camera_stream.c
 *
 * Description      : See camera_stream.h. The UVC capture flow (device
 *                    notification, enumeration, stream open, data callback)
 *                    is ported from the Face ID demo's usb_camera_task.c with
 *                    the display/ML consumers replaced by a JPEG encoder task
 *                    that publishes into shared SOCMEM for the CM33.
 *******************************************************************************/

#include "cybsp.h"
#include "cy_pdl.h"

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"

#include "USBH.h"
#include "USBH_Util.h"
#include "USBH_VIDEO.h"

#include "camera_stream.h"
#include "jpeg_encoder_yuyv.h"
#include "cam_shm.h"
#include "cam_change.h"

#include <string.h>

/*******************************************************************************
 * Configuration
 ******************************************************************************/
#ifndef CAMERA_WIDTH
#define CAMERA_WIDTH                (320)
#endif
#ifndef CAMERA_HEIGHT
#define CAMERA_HEIGHT               (240)
#endif
#define CAMERA_BUFFER_SIZE          ((CAMERA_WIDTH) * (CAMERA_HEIGHT) * 2)
#define NUM_IMAGE_BUFFERS           (2)

/* Pick the advertised frame interval closest to this (100 ns units).
 * 1000000 = 10 FPS: roughly what the Wi-Fi link comfortably carries as JPEG
 * and what the CM55 comfortably encodes. Override at build time to trade rate for bandwidth. */
#ifndef TARGET_FRAME_INTERVAL
#define TARGET_FRAME_INTERVAL       (1000000u)
#endif

#ifndef JPEG_QUALITY
#define JPEG_QUALITY                (JPEG_QUALITY_HIGH)
#endif

/* Frame-skip: publish only when a frame differs enough from the last published one. The metric is
 * the sum of absolute luma differences over a CAM_SIG_LEN (192) sample grid, so the threshold is
 * roughly (avg per-sample luma delta) * 192. 0 publishes every frame. This is the dominant
 * bandwidth lever for a mostly-static scene. */
#ifndef CAM_CHANGE_THRESHOLD
#define CAM_CHANGE_THRESHOLD        (192u * 6u)   // ~6 luma levels of average change
#endif

#define USB_WEBCAM_TASK_PRIORITY    (configMAX_PRIORITIES - 4)
#define TASK_PRIO_USBH_MAIN         (configMAX_PRIORITIES - 2)
#define TASK_PRIO_USBH_ISR          (configMAX_PRIORITIES - 1)
#define ENCODER_TASK_PRIORITY       (configMAX_PRIORITIES - 5)

#define USB_STREAM_ERROR_THRESHOLD  (10)
#define DEVICE_EVT_DISCONNECTED     (0xFFu)

/*******************************************************************************
 * Capture state (pattern from the Face ID demo)
 ******************************************************************************/
typedef struct
{
    uint32_t NumBytes;
    uint8_t  BufReady;
} VideoBuffer_t;

/* Raw YUYV capture buffers live in CM55-private SOCMEM (~300 KB total). */
CY_SECTION(".cy_socmem_data") static uint8_t s_yuv_frames[NUM_IMAGE_BUFFERS][CAMERA_BUFFER_SIZE];

static VideoBuffer_t            s_image_buf[NUM_IMAGE_BUFFERS];
static volatile uint8_t         s_last_buffer;
static volatile uint32_t        s_capture_count;
static volatile uint8_t         s_device_connected;

static QueueHandle_t            s_video_mailbox;
static QueueHandle_t            s_devstate_mailbox;
static USBH_NOTIFICATION_HOOK   s_notification_hook;
static int                      s_stream_err_cnt;

static TaskHandle_t             s_usbh_main_handle;
static TaskHandle_t             s_usbh_isr_handle;

/*******************************************************************************
 * Newlib stub: nothing on this core owns a console (the CM33 owns the debug
 * UART), and the platform default _write is CY_ASSERT(false). Swallow output.
 ******************************************************************************/
int _write(int file, char *ptr, int len)
{
    (void)file;
    (void)ptr;
    return len;
}

/*******************************************************************************
 * Device add/remove callback (USBH task context)
 ******************************************************************************/
static void _cbOnAddRemoveDevice(void *pContext, U8 DevIndex,
                                 USBH_DEVICE_EVENT Event)
{
    (void)pContext;

    switch (Event)
    {
        case USBH_DEVICE_EVENT_ADD:
            s_device_connected = 1u;
            for (int i = 0; i < NUM_IMAGE_BUFFERS; i++)
            {
                s_image_buf[i].BufReady = 0u;
                s_image_buf[i].NumBytes = 0u;
            }
            s_last_buffer = 0u;
            __DMB();
            CAM_SHM_HDR->camera_state = CAM_STATE_CONNECTED;
            (void)xQueueSend(s_video_mailbox, &DevIndex, 0);
            break;

        case USBH_DEVICE_EVENT_REMOVE:
        {
            s_device_connected = 0u;
            for (int i = 0; i < NUM_IMAGE_BUFFERS; i++)
            {
                s_image_buf[i].BufReady = 0u;
                s_image_buf[i].NumBytes = 0u;
            }
            __DMB();
            CAM_SHM_HDR->camera_state = CAM_STATE_NO_CAMERA;
            U8 evt = DEVICE_EVT_DISCONNECTED;
            (void)xQueueSend(s_devstate_mailbox, &evt, 0);
            s_stream_err_cnt = 0;
            break;
        }

        default:
            break;
    }
}

/*******************************************************************************
 * Per-packet data callback (ported from the demo's _cbOnData; ISR-time hot
 * path, hence ITCM placement). Assembles packets into the current YUYV
 * buffer and flips buffers on a complete frame.
 ******************************************************************************/
CY_SECTION_ITCM_BEGIN
static void _cbOnData(USBH_VIDEO_DEVICE_HANDLE hDevice,
                      USBH_VIDEO_STREAM_HANDLE hStream,
                      USBH_STATUS Status,
                      const U8 *pData,
                      unsigned NumBytes,
                      U32 Flags,
                      void *pUserDataContext)
{
    (void)hDevice;
    (void)pUserDataContext;

    static size_t  frame_bytes      = 0u;
    static uint8_t current_buffer   = 0u;
    static uint8_t throw_away_frame = 0u;
    USBH_STATUS    status1          = USBH_STATUS_SUCCESS;
    I8             is_stream_stopped;

    if (Status != USBH_STATUS_SUCCESS)
    {
        s_stream_err_cnt++;
        if (s_stream_err_cnt >= USB_STREAM_ERROR_THRESHOLD)
        {
            status1 = USBH_STATUS_DEVICE_ERROR;
        }
        else if (Status != USBH_STATUS_DEVICE_REMOVED)
        {
            status1 = USBH_VIDEO_GetStreamState(hStream, &is_stream_stopped);
            if ((status1 == USBH_STATUS_SUCCESS) && (is_stream_stopped == 1))
            {
                status1 = USBH_VIDEO_RestartStream(hStream);
            }
        }

        if ((Status == USBH_STATUS_DEVICE_REMOVED) ||
            (status1 != USBH_STATUS_SUCCESS))
        {
            U8 evt = DEVICE_EVT_DISCONNECTED;
            (void)xQueueSend(s_devstate_mailbox, &evt, 0);
            s_stream_err_cnt = 0;
        }

        frame_bytes      = 0u;
        throw_away_frame = 0u;
        USBH_VIDEO_Ack(hStream);
        return;
    }

    s_stream_err_cnt = 0;
    bool end_of_frame = (Flags & USBH_UVC_END_OF_FRAME) == USBH_UVC_END_OF_FRAME;

    if (current_buffer >= NUM_IMAGE_BUFFERS)
    {
        current_buffer   = 0u;
        frame_bytes      = 0u;
        throw_away_frame = 1u;
        USBH_VIDEO_Ack(hStream);
        return;
    }

    if (throw_away_frame)
    {
        frame_bytes = 0u;
        if (end_of_frame)
        {
            throw_away_frame = 0u;
        }
        USBH_VIDEO_Ack(hStream);
        return;
    }

    if (frame_bytes + NumBytes <= CAMERA_BUFFER_SIZE)
    {
        memcpy(&s_yuv_frames[current_buffer][frame_bytes], pData, NumBytes);
        frame_bytes += NumBytes;
    }
    else
    {
        /* Oversized frame (camera/format mismatch): drop it. */
        throw_away_frame = 1u;
        frame_bytes      = 0u;
        USBH_VIDEO_Ack(hStream);
        return;
    }

    if (end_of_frame)
    {
        if (frame_bytes == CAMERA_BUFFER_SIZE)
        {
            s_image_buf[current_buffer].NumBytes = frame_bytes;
            __DMB();
            s_image_buf[current_buffer].BufReady = 1u;
            __DMB();
            s_last_buffer = current_buffer;
            s_capture_count++;
            __DMB();

            current_buffer = (uint8_t)((current_buffer + 1u) % NUM_IMAGE_BUFFERS);
            s_image_buf[current_buffer].BufReady = 0u;
            s_image_buf[current_buffer].NumBytes = 0u;
        }
        frame_bytes = 0u;
    }

    USBH_VIDEO_Ack(hStream);
}
CY_SECTION_ITCM_END

/*******************************************************************************
 * Device-ready: enumerate formats and open the stream. Generic version of the
 * demo's _OnDevReady — accepts any UVC camera that offers uncompressed
 * CAMERA_WIDTHxCAMERA_HEIGHT and picks the frame interval closest to
 * TARGET_FRAME_INTERVAL; otherwise falls back to the first advertised mode.
 ******************************************************************************/
static void _OnDevReady(U8 DevIndex)
{
    USBH_VIDEO_INPUT_HEADER_INFO input_header;
    USBH_VIDEO_DEVICE_HANDLE     hDevice;
    USBH_VIDEO_INTERFACE_INFO    iface_info;
    USBH_VIDEO_STREAM_CONFIG     stream_cfg;
    USBH_VIDEO_STREAM_HANDLE     stream;
    USBH_VIDEO_FORMAT_INFO       format;
    USBH_VIDEO_FRAME_INFO        frame;
    USBH_STATUS                  status;
    unsigned best_format_idx   = 0u;
    unsigned best_frame_idx    = 0u;
    unsigned best_interval_idx = 1u;
    uint32_t best_interval_err = 0xFFFFFFFFu;
    unsigned found             = 0u;
    U8       mb_event          = 0u;

    memset(&hDevice, 0, sizeof(hDevice));
    memset(&stream, 0, sizeof(stream));

    if (USBH_VIDEO_Open(DevIndex, &hDevice) != USBH_STATUS_SUCCESS)
    {
        return;
    }

    if (USBH_VIDEO_GetInterfaceInfo(hDevice, &iface_info) != USBH_STATUS_SUCCESS)
    {
        USBH_VIDEO_Close(hDevice);
        return;
    }

    status = USBH_VIDEO_GetInputHeader(hDevice, &input_header);
    if (status == USBH_STATUS_SUCCESS)
    {
        for (unsigned i = 0; i < input_header.bNumFormats; i++)
        {
            if (USBH_VIDEO_GetFormatInfo(hDevice, i, &format) != USBH_STATUS_SUCCESS)
            {
                continue;
            }
            if (format.FormatType != USBH_VIDEO_VS_FORMAT_UNCOMPRESSED)
            {
                continue;
            }
            unsigned num_frames = format.u.UncompressedFormat.bNumFrameDescriptors;
            for (unsigned j = 0; j < num_frames; j++)
            {
                if (USBH_VIDEO_GetFrameInfo(hDevice, i, j, &frame) != USBH_STATUS_SUCCESS)
                {
                    continue;
                }
                if ((frame.bFrameIntervalType == 0u) ||
                    (frame.wWidth != CAMERA_WIDTH) ||
                    (frame.wHeight != CAMERA_HEIGHT))
                {
                    continue;
                }
                for (unsigned k = 0; k < frame.bFrameIntervalType; k++)
                {
                    uint32_t ival = frame.u.dwFrameInterval[k];
                    uint32_t err  = (ival > TARGET_FRAME_INTERVAL)
                                  ? (ival - TARGET_FRAME_INTERVAL)
                                  : (TARGET_FRAME_INTERVAL - ival);
                    if (err < best_interval_err)
                    {
                        best_interval_err = err;
                        best_format_idx   = i;
                        best_frame_idx    = j;
                        best_interval_idx = k + 1u;  /* 1-based per emUSB API */
                        found             = 1u;
                    }
                }
            }
        }

        if ((found == 0u) && (input_header.bNumFormats > 0u))
        {
            /* Fall back to the camera's first advertised mode. The data
             * callback drops frames that don't match the buffer size. */
            best_format_idx   = 0u;
            best_frame_idx    = 0u;
            best_interval_idx = 1u;
            found             = 1u;
        }
    }

    if (found == 1u)
    {
        memset(&stream_cfg, 0, sizeof(stream_cfg));
        stream_cfg.FormatIdx        = best_format_idx;
        stream_cfg.FrameIdx         = best_frame_idx;
        stream_cfg.FrameIntervalIdx = best_interval_idx;
        stream_cfg.pfDataCallback   = _cbOnData;

        s_last_buffer = 0u;
        xQueueReset(s_devstate_mailbox);

        if (USBH_VIDEO_OpenStream(hDevice, &stream_cfg, &stream) == USBH_STATUS_SUCCESS)
        {
            CAM_SHM_HDR->camera_state = CAM_STATE_STREAMING;

            /* Park here until the device disconnects or errors out. */
            for (;;)
            {
                if (xQueueReceive(s_devstate_mailbox, &mb_event,
                                  pdMS_TO_TICKS(100)) == pdTRUE)
                {
                    break;
                }
            }
            USBH_VIDEO_CloseStream(stream);
        }
    }
    else
    {
        CAM_SHM_HDR->camera_state = CAM_STATE_UNSUPPORTED;
        while (s_device_connected)
        {
            USBH_OS_Delay(50);
        }
    }

    USBH_VIDEO_Close(hDevice);
    if (mb_event == DEVICE_EVT_DISCONNECTED)
    {
        s_device_connected = 0u;
    }
    CAM_SHM_HDR->camera_state = s_device_connected ? CAM_STATE_CONNECTED
                                                   : CAM_STATE_NO_CAMERA;
}

/*******************************************************************************
 * emUSB-Host task wrappers
 ******************************************************************************/
static void USBH_Task_Wrapper(void *arg)
{
    (void)arg;
    USBH_Task();
}

static void USBH_ISRTask_Wrapper(void *arg)
{
    (void)arg;
    USBH_ISRTask();
}

/*******************************************************************************
 * Webcam control task: brings up the USB host stack and serves device events.
 ******************************************************************************/
static void camera_usb_task(void *arg)
{
    (void)arg;
    U8 dev_index;

    USBH_Init();
    (void)xTaskCreate(USBH_Task_Wrapper, "USBH_Task", 8192u / sizeof(StackType_t),
                      NULL, TASK_PRIO_USBH_MAIN, &s_usbh_main_handle);
    (void)xTaskCreate(USBH_ISRTask_Wrapper, "USBH_isr", 8192u / sizeof(StackType_t),
                      NULL, TASK_PRIO_USBH_ISR, &s_usbh_isr_handle);

    s_video_mailbox    = xQueueCreate(4, sizeof(U8));
    s_devstate_mailbox = xQueueCreate(1, sizeof(U8));
    configASSERT(s_video_mailbox != NULL);
    configASSERT(s_devstate_mailbox != NULL);

    USBH_VIDEO_Init();
    USBH_VIDEO_AddNotification(&s_notification_hook, _cbOnAddRemoveDevice, NULL);

    for (;;)
    {
        if (pdTRUE == xQueueReceive(s_video_mailbox, &dev_index, pdMS_TO_TICKS(100)))
        {
            _OnDevReady(dev_index);
        }
    }
}

/*******************************************************************************
 * Encoder task: whenever a new raw frame lands, JPEG-encode it straight into
 * the next shared-memory slot under the slot's seqlock and publish it.
 ******************************************************************************/
static void camera_encoder_task(void *arg)
{
    (void)arg;
    uint32_t last_encoded = 0u;
    uint32_t next_slot    = 0u;
    cam_shm_hdr_t *shm    = CAM_SHM_HDR;
    static cam_change_state_t s_change;   /* luma signature of the last published frame */

    static uint32_t s_last_cam_state = CAM_STATE_NO_CAMERA;

    for (;;)
    {
        /* Reset change-detection across a disconnect/reconnect so the first frame of a new stream
         * always publishes — its scene is unrelated to whatever was framed before the gap. */
        uint32_t cam_state = shm->camera_state;
        if (cam_state != s_last_cam_state)
        {
            if (cam_state != CAM_STATE_STREAMING) s_change.have = false;
            s_last_cam_state = cam_state;
        }

        if (s_capture_count == last_encoded)
        {
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }
        last_encoded = s_capture_count;

        uint8_t buf_idx = s_last_buffer;
        if (!s_image_buf[buf_idx].BufReady)
        {
            continue;
        }

        /* Frame-skip: a frame visually identical to the last published one is dropped here, before
         * the JPEG encode and the publish — saving both CPU and Wi-Fi bandwidth on a static scene. */
        if (!cam_frame_changed(s_yuv_frames[buf_idx], CAMERA_WIDTH, CAMERA_HEIGHT,
                               &s_change, CAM_CHANGE_THRESHOLD))
        {
            continue;
        }

        /* Pick a slot the CM33 is neither transmitting (reader_slot) nor about
         * to grab (latest_slot), so we never scribble on a frame mid-send.
         * With CAM_SHM_NUM_SLOTS >= 3 one is always free. */
        uint32_t reserved  = shm->reader_slot;
        uint32_t published = shm->latest_slot;
        for (uint32_t i = 0u; i < CAM_SHM_NUM_SLOTS; i++)
        {
            uint32_t cand = (next_slot + i) % CAM_SHM_NUM_SLOTS;
            if ((cand != reserved) && (cand != published))
            {
                next_slot = cand;
                break;
            }
        }

        uint8_t *slot = CAM_SHM_SLOT(next_slot);

        shm->seq[next_slot]++;          /* odd: writer active */
        __DMB();

        /* Close the reservation race: if the forwarder reserved this slot in
         * the window since we picked it, back off rather than tear its frame. */
        if (shm->reader_slot == next_slot)
        {
            shm->seq[next_slot]++;      /* restore even */
            __DMB();
            continue;
        }

        int jpeg_size = jpeg_encode_yuyv(s_yuv_frames[buf_idx],
                                         CAMERA_WIDTH, CAMERA_HEIGHT,
                                         JPEG_QUALITY,
                                         slot, CAM_JPEG_MAX);

        /* Raw-buffer tear guard: only NUM_IMAGE_BUFFERS raw buffers exist and the encoder runs
         * below the USB tasks, so _cbOnData may have recycled buf_idx during this (slow) encode.
         * The producer clears BufReady the instant it starts overwriting a buffer, so a now-clear
         * BufReady means the JPEG may be of a half-overwritten frame -> discard it (the wire CRC /
         * TLS cannot catch raw-side tearing). */
        bool torn = !s_image_buf[buf_idx].BufReady;

        if (jpeg_size > 0 && !torn)
        {
            shm->size[next_slot] = (uint32_t)jpeg_size;
            shm->t_us[next_slot] = (uint32_t)(xTaskGetTickCount() *
                                              (1000000u / configTICK_RATE_HZ));
            __DMB();
            shm->seq[next_slot]++;      /* even: stable */
            __DMB();
            shm->latest_slot = next_slot;
            shm->frame_id++;
            shm->frames_published++;
            __DMB();
            next_slot = (next_slot + 1u) % CAM_SHM_NUM_SLOTS;
        }
        else
        {
            shm->seq[next_slot]++;      /* restore even on encode failure or a torn raw frame */
            __DMB();
            if (torn) shm->frames_dropped_torn++;
            else      shm->encode_errors++;
        }
        shm->frames_captured = s_capture_count;
    }
}

/*******************************************************************************
 * Public API
 ******************************************************************************/
bool camera_stream_create_tasks(void)
{
    /* Publish a clean shared-memory header before the CM33 can look at it.
     * (The CM33 boots us, so it may already be polling: magic goes last.) */
    cam_shm_hdr_t *shm = CAM_SHM_HDR;
    memset((void *)shm, 0, sizeof(*shm));
    shm->width        = CAMERA_WIDTH;
    shm->height       = CAMERA_HEIGHT;
    shm->camera_state = CAM_STATE_NO_CAMERA;
    shm->latest_slot  = CAM_SLOT_NONE;   /* nothing published yet */
    shm->reader_slot  = CAM_SLOT_NONE;   /* CM33 not transmitting yet */
    __DMB();
    shm->magic = CAM_SHM_MAGIC;
    __DMB();

    if (pdPASS != xTaskCreate(camera_usb_task, "Cam USB",
                              configMINIMAL_STACK_SIZE * 16, NULL,
                              USB_WEBCAM_TASK_PRIORITY, NULL))
    {
        return false;
    }
    if (pdPASS != xTaskCreate(camera_encoder_task, "Cam Encode",
                              configMINIMAL_STACK_SIZE * 16, NULL,
                              ENCODER_TASK_PRIORITY, NULL))
    {
        return false;
    }
    return true;
}
