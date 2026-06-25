/*******************************************************************************
 * File Name        : uart_stream.c
 *
 * Description      : See uart_stream.h. Implements the WebSerial transport on
 *                    the KitProg3 debug UART (SCB2) using PDL SCB calls. The BSP
 *                    (cybsp_init) already routes the pins and configures the SCB
 *                    clock; here we only Init + Enable the block, mirroring the
 *                    pattern used by the Face ID reference firmware.
 *
 *                    Frame encoding and command parsing live in stream_proto.c,
 *                    shared with the Wi-Fi TCP transport (tcp_stream.c).
 *
 *******************************************************************************/

#include "uart_stream.h"
#include "stream_proto.h"

#include "cybsp.h"
#include "cy_scb_uart.h"
#include "cy_syslib.h"

#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"

#include <string.h>

/*******************************************************************************
 * Local state
 ******************************************************************************/
static cy_stc_scb_uart_context_t s_uart_ctx;

/* Serializes TX between tasks: the sensor task emits binary frames while the
 * Wi-Fi stack logs through printf/_write (see below). Interleaving bytes
 * inside one frame would burn that frame's CRC. */
static SemaphoreHandle_t s_tx_mutex;

/* Receive line assembly buffer for ASCII commands. */
#define RX_LINE_MAX (STREAM_CMD_LINE_MAX)
static char     s_rx_line[RX_LINE_MAX];
static uint32_t s_rx_len;

/*******************************************************************************
 * Blocking write of a raw byte buffer with TX-FIFO back-pressure. Pushes bytes
 * while space is available and yields to the scheduler when the FIFO fills, so
 * we never busy-spin the CPU during a burst.
 ******************************************************************************/
static void uart_write_blocking(const uint8_t *data, uint32_t len)
{
    const uint32_t fifo_size = Cy_SCB_GetFifoSize(CYBSP_DEBUG_UART_HW);
    uint32_t sent = 0u;

    bool locked = (s_tx_mutex != NULL) &&
                  (xTaskGetSchedulerState() == taskSCHEDULER_RUNNING);
    if (locked)
    {
        (void)xSemaphoreTake(s_tx_mutex, portMAX_DELAY);
    }

    while (sent < len)
    {
        uint32_t in_fifo = Cy_SCB_UART_GetNumInTxFifo(CYBSP_DEBUG_UART_HW);
        uint32_t space   = (fifo_size > in_fifo) ? (fifo_size - in_fifo) : 0u;

        if (space == 0u)
        {
            taskYIELD();
            continue;
        }

        uint32_t chunk = (len - sent < space) ? (len - sent) : space;
        for (uint32_t i = 0u; i < chunk; i++)
        {
            (void)Cy_SCB_UART_Put(CYBSP_DEBUG_UART_HW, (uint32_t)data[sent++]);
        }
    }

    if (locked)
    {
        (void)xSemaphoreGive(s_tx_mutex);
    }
}

/*******************************************************************************
 * Newlib _write retarget. The platform's default stub is CY_ASSERT(false),
 * which kills the firmware on the first printf — and the Wi-Fi stack (WHD)
 * logs through printf. Route stdout/stderr to the streaming UART instead;
 * the web UI and the host bridge both skip ASCII between binary frames.
 ******************************************************************************/
int _write(int file, char *ptr, int len)
{
    CY_UNUSED_PARAMETER(file);
    if ((ptr != NULL) && (len > 0))
    {
        uart_write_blocking((const uint8_t *)ptr, (uint32_t)len);
    }
    return len;
}

/*******************************************************************************
 * Public API
 ******************************************************************************/
bool uart_stream_init(void)
{
    cy_en_scb_uart_status_t st =
        Cy_SCB_UART_Init(CYBSP_DEBUG_UART_HW, &CYBSP_DEBUG_UART_config, &s_uart_ctx);
    if (CY_SCB_UART_SUCCESS != st)
    {
        return false;
    }

    Cy_SCB_UART_Enable(CYBSP_DEBUG_UART_HW);
    s_rx_len = 0u;
    if (s_tx_mutex == NULL)
    {
        s_tx_mutex = xSemaphoreCreateMutex();
    }
    return true;
}

void uart_stream_print(const char *str)
{
    if (str != NULL)
    {
        uart_write_blocking((const uint8_t *)str, (uint32_t)strlen(str));
    }
}

void uart_stream_send_sample(const uart_imu_sample_t *sample)
{
    uint8_t frame[STREAM_SAMPLE_FRAME_LEN];
    uint32_t len = stream_encode_sample(frame, sample);
    uart_write_blocking(frame, len);
}

void uart_stream_send_status(uint8_t imu_src, uint8_t mag_src, const char *reason)
{
    uint8_t frame[STREAM_STATUS_FRAME_MAX];
    uint32_t len = stream_encode_status(frame, imu_src, mag_src, reason);
    uart_write_blocking(frame, len);
}

uart_cmd_t uart_stream_poll_command(uart_cfg_t *out_cfg)
{
    uart_cmd_t cmd = UART_CMD_NONE;

    while (Cy_SCB_UART_GetNumInRxFifo(CYBSP_DEBUG_UART_HW) > 0u)
    {
        uint32_t rx = Cy_SCB_UART_Get(CYBSP_DEBUG_UART_HW);
        if (rx == CY_SCB_UART_RX_NO_DATA)
        {
            break;
        }

        char ch = (char)(rx & 0xFFu);

        if (ch == '\n' || ch == '\r')
        {
            if (s_rx_len > 0u)
            {
                s_rx_line[s_rx_len] = '\0';
                uart_cmd_t parsed = stream_parse_line(s_rx_line, out_cfg);
                s_rx_len = 0u;
                if (parsed != UART_CMD_NONE)
                {
                    /* Return the first complete command; any remaining bytes
                     * stay in the FIFO for the next poll. */
                    cmd = parsed;
                    break;
                }
            }
        }
        else if (s_rx_len < (RX_LINE_MAX - 1u))
        {
            s_rx_line[s_rx_len++] = ch;
        }
        else
        {
            /* Overflow: discard the malformed line. */
            s_rx_len = 0u;
        }
    }

    return cmd;
}
