/*******************************************************************************
 * File Name        : audio_stream.c
 *
 * Description      : See audio_stream.h. PDM capture (MXPDM PDM0, channels 2/3
 *                    = left/right) feeding a lock-free SPSC ring buffer drained
 *                    by a forwarder task. The PDM block, its clock and the
 *                    P8_5/P8_6 pins are configured by the KIT_PSE84_AI BSP
 *                    (CYBSP_PDM_config, channel_2_config, channel_3_config);
 *                    we only bring the driver up and stream the result.
 *
 *  Concurrency: single producer (PDM ISR appends samples, owns the write
 *  index) and single consumer (forwarder task copies chunks out, owns the read
 *  index). Each index is written by exactly one context and read by the other,
 *  so no lock is needed on Cortex-M where aligned 32-bit accesses are atomic.
 *******************************************************************************/

#include "audio_stream.h"
#include "tcp_stream.h"
#include "uart_stream.h"

#include "cybsp.h"
#include "cy_pdl.h"

#include "FreeRTOS.h"
#include "task.h"

/*******************************************************************************
 * Configuration
 ******************************************************************************/
#define PDM_HW                  (PDM0)
#define PDM_LEFT_CH             (2u)   /* channel_2_config */
#define PDM_RIGHT_CH            (3u)   /* channel_3_config; carries the IRQ */
#define PDM_ISR_PRIORITY        (3u)

#define AUDIO_SAMPLE_RATE       (16000u)
#define AUDIO_CHANNELS          (2u)
#define AUDIO_BITS              (16u)

/* One wire frame carries AUDIO_CHUNK_PAIRS stereo sample-pairs. 512 pairs at
 * 16 kHz is a 32 ms chunk -> ~31 frames/s, ~2 KB payload each. */
#define AUDIO_CHUNK_PAIRS       (512u)
#define AUDIO_CHUNK_SAMPLES     (AUDIO_CHUNK_PAIRS * AUDIO_CHANNELS)  /* int16s */

/* Ring holds 8 chunks (~256 ms) to ride out send/scheduling jitter. Must be a
 * power of two so the index wrap is a cheap mask. */
#define AUDIO_RING_SAMPLES      (8u * AUDIO_CHUNK_SAMPLES)            /* 8192   */
#define AUDIO_RING_MASK         (AUDIO_RING_SAMPLES - 1u)

#define AUDIO_FWD_TASK_NAME     ("Audio Fwd Task")
#define AUDIO_FWD_TASK_STACK    (configMINIMAL_STACK_SIZE * 4)
#define AUDIO_FWD_TASK_PRIORITY (configMAX_PRIORITIES - 4)
#define AUDIO_POLL_MS           (10u)

/*******************************************************************************
 * State
 ******************************************************************************/
static int16_t           s_ring[AUDIO_RING_SAMPLES];
static volatile uint32_t s_w;          /* write index  (ISR owns)  */
static volatile uint32_t s_r;          /* read index   (task owns) */
static volatile uint32_t s_dropped;    /* sample-pairs dropped on ring-full */
static volatile uint32_t s_overflows;  /* PDM FIFO overflow events */

/* Chunk copied out of the ring for transmission (task-private). */
static int16_t           s_send[AUDIO_CHUNK_SAMPLES];

static const cy_stc_sysint_t s_pdm_irq_cfg =
{
    .intrSrc      = (IRQn_Type)CYBSP_PDM_CHANNEL_3_IRQ,
    .intrPriority = PDM_ISR_PRIORITY,
};

/*******************************************************************************
 * PDM interrupt: drain both channel FIFOs in lockstep into the ring. Reads the
 * actual fill level (rather than assuming the trigger count) so it stays
 * correct regardless of the BSP's configured trigger level.
 ******************************************************************************/
static void pdm_isr(void)
{
    uint32_t status = Cy_PDM_PCM_Channel_GetInterruptStatusMasked(PDM_HW,
                                                                  PDM_RIGHT_CH);

    if (status & CY_PDM_PCM_INTR_RX_TRIGGER)
    {
        /* Both channels are clocked together; take the common count so L/R
         * stay paired and any tiny imbalance is left for the next interrupt. */
        uint32_t nl = Cy_PDM_PCM_Channel_GetNumInFifo(PDM_HW, PDM_LEFT_CH);
        uint32_t nr = Cy_PDM_PCM_Channel_GetNumInFifo(PDM_HW, PDM_RIGHT_CH);
        uint32_t pairs = (nl < nr) ? nl : nr;

        uint32_t w    = s_w;
        uint32_t used = (w - s_r) & AUDIO_RING_MASK;
        uint32_t free = AUDIO_RING_MASK - used;   /* keep one slot empty */

        for (uint32_t i = 0u; i < pairs; i++)
        {
            int16_t l = (int16_t)Cy_PDM_PCM_Channel_ReadFifo(PDM_HW, PDM_LEFT_CH);
            int16_t r = (int16_t)Cy_PDM_PCM_Channel_ReadFifo(PDM_HW, PDM_RIGHT_CH);

            if (free >= 2u)
            {
                s_ring[w] = l;  w = (w + 1u) & AUDIO_RING_MASK;
                s_ring[w] = r;  w = (w + 1u) & AUDIO_RING_MASK;
                free -= 2u;
            }
            else
            {
                /* Ring full (no consumer / link stalled): still drain the HW
                 * FIFO above so it doesn't overflow, but drop the sample. */
                s_dropped++;
            }
        }

        __DMB();
        s_w = w;
        Cy_PDM_PCM_Channel_ClearInterrupt(PDM_HW, PDM_RIGHT_CH,
                                          CY_PDM_PCM_INTR_RX_TRIGGER);
    }

    if (status & (CY_PDM_PCM_INTR_RX_OVERFLOW | CY_PDM_PCM_INTR_RX_FIR_OVERFLOW |
                  CY_PDM_PCM_INTR_RX_IF_OVERFLOW | CY_PDM_PCM_INTR_RX_UNDERFLOW))
    {
        s_overflows++;
        Cy_PDM_PCM_Channel_ClearInterrupt(PDM_HW, PDM_RIGHT_CH,
                                          CY_PDM_PCM_INTR_MASK);
    }
}

/*******************************************************************************
 * Brings up the PDM block + both channels and enables the RX-trigger IRQ.
 ******************************************************************************/
static bool pdm_start(void)
{
    if (CY_PDM_PCM_SUCCESS != Cy_PDM_PCM_Init(PDM_HW, &CYBSP_PDM_config))
    {
        return false;
    }

    Cy_PDM_PCM_Channel_Enable(PDM_HW, PDM_LEFT_CH);
    Cy_PDM_PCM_Channel_Enable(PDM_HW, PDM_RIGHT_CH);

    if ((CY_PDM_PCM_SUCCESS != Cy_PDM_PCM_Channel_Init(PDM_HW, &channel_2_config,
                                                       PDM_LEFT_CH)) ||
        (CY_PDM_PCM_SUCCESS != Cy_PDM_PCM_Channel_Init(PDM_HW, &channel_3_config,
                                                       PDM_RIGHT_CH)))
    {
        return false;
    }

    /* Moderate gain for both mics (preview level). */
    (void)Cy_PDM_PCM_SetGain(PDM_HW, PDM_LEFT_CH,  CY_PDM_PCM_SEL_GAIN_11DB);
    (void)Cy_PDM_PCM_SetGain(PDM_HW, PDM_RIGHT_CH, CY_PDM_PCM_SEL_GAIN_11DB);

    /* The trigger interrupt is serviced on the right channel only. */
    Cy_PDM_PCM_Channel_ClearInterrupt(PDM_HW, PDM_RIGHT_CH, CY_PDM_PCM_INTR_MASK);
    Cy_PDM_PCM_Channel_SetInterruptMask(PDM_HW, PDM_RIGHT_CH, CY_PDM_PCM_INTR_MASK);

    if (CY_SYSINT_SUCCESS != Cy_SysInt_Init(&s_pdm_irq_cfg, &pdm_isr))
    {
        return false;
    }
    NVIC_ClearPendingIRQ(s_pdm_irq_cfg.intrSrc);
    NVIC_EnableIRQ(s_pdm_irq_cfg.intrSrc);

    Cy_PDM_PCM_Activate_Channel(PDM_HW, PDM_LEFT_CH);
    Cy_PDM_PCM_Activate_Channel(PDM_HW, PDM_RIGHT_CH);
    return true;
}

/*******************************************************************************
 * Forwarder task: copies completed chunks out of the ring and sends them.
 ******************************************************************************/
static void audio_fwd_task(void *arg)
{
    (void)arg;
    uint32_t seq = 0u;

    if (!pdm_start())
    {
        uart_stream_print("[audio] PDM init failed - mic disabled\r\n");
        vTaskDelete(NULL);
        return;
    }
    uart_stream_print("[audio] PDM mics streaming (16 kHz stereo)\r\n");

    for (;;)
    {
        vTaskDelay(pdMS_TO_TICKS(AUDIO_POLL_MS));

        if (!tcp_stream_connected())
        {
            s_r = s_w;          /* drop backlog so we don't burst on connect */
            continue;
        }

        /* Ship every whole chunk currently buffered. */
        while (((s_w - s_r) & AUDIO_RING_MASK) >= AUDIO_CHUNK_SAMPLES)
        {
            uint32_t r = s_r;
            for (uint32_t k = 0u; k < AUDIO_CHUNK_SAMPLES; k++)
            {
                s_send[k] = s_ring[(r + k) & AUDIO_RING_MASK];
            }
            __DMB();
            s_r = (r + AUDIO_CHUNK_SAMPLES) & AUDIO_RING_MASK;

            if (!tcp_stream_send_audio(seq, AUDIO_SAMPLE_RATE, AUDIO_CHANNELS,
                                       AUDIO_BITS, (const uint8_t *)s_send,
                                       AUDIO_CHUNK_SAMPLES * sizeof(int16_t)))
            {
                break;          /* client gone; reset on the next poll */
            }
            seq++;
        }
    }
}

/*******************************************************************************
 * Public API
 ******************************************************************************/
bool audio_stream_create_task(void)
{
    return (pdPASS == xTaskCreate(audio_fwd_task, AUDIO_FWD_TASK_NAME,
                                  AUDIO_FWD_TASK_STACK, NULL,
                                  AUDIO_FWD_TASK_PRIORITY, NULL));
}
