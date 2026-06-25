/*******************************************************************************
 * File Name        : tcp_stream.c
 *
 * Description      : See tcp_stream.h. Single-client TCP server built on the
 *                    secure-sockets library (plain TCP, no TLS). Frame encoding
 *                    and command parsing are shared with the UART transport via
 *                    stream_proto.c.
 *******************************************************************************/

#include "tcp_stream.h"
#include "stream_proto.h"
#include "uart_stream.h"
#include "cam_shm.h"
#include "audio_stream.h"

#include "cy_secure_sockets.h"

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"

#include <string.h>

/*******************************************************************************
 * Configuration
 ******************************************************************************/
#define TCP_LISTEN_BACKLOG        (1)
/* How long cy_socket_send may block waiting for TCP window space before it
 * fails (and we drop the client). 500 ms was the executioner: log analysis of
 * every disconnect showed each one is preceded by a 1.5-2.1 s pause in the data
 * stream (a Wi-Fi-link stall — almost certainly the PC adapter's background
 * scan/roam), and at 500 ms the very next blocking send failed and tore the
 * session down (the host then split the recording into a new file). The pauses
 * top out around ~2.1 s, so 5 s rides all of them out — the send completes when
 * the link recovers and the stream continues on ONE log file — while still
 * dropping a genuinely dead peer so last-connection-wins reconnect can take
 * over. No extra data is lost vs. dropping: nothing transmits during a Wi-Fi
 * stall either way; this just avoids the teardown + ~1.1 s reconnect + file
 * split. (IMU buffers in the BMI270 HW FIFO, audio in its ring, meanwhile.) */
#define TCP_SEND_TIMEOUT_MS       (5000u)
#define TCP_CMD_QUEUE_DEPTH       (8u)
#define TCP_RX_CHUNK              (64u)

/*******************************************************************************
 * Local state
 ******************************************************************************/
typedef struct
{
    uart_cmd_t cmd;
    uart_cfg_t cfg;
} tcp_cmd_msg_t;

static cy_socket_t       s_listener = CY_SOCKET_INVALID_HANDLE;
static cy_socket_t       s_client   = CY_SOCKET_INVALID_HANDLE;
static SemaphoreHandle_t s_client_mutex;
static QueueHandle_t     s_cmd_queue;

/* Receive line assembly buffer (commands are short ASCII lines). */
static char     s_rx_line[STREAM_CMD_LINE_MAX];
static uint32_t s_rx_len;

/*******************************************************************************
 * Drops the connected client, but ONLY if it is still the exact socket the
 * caller was operating on (pass the handle you sent/received on). Safe from any
 * thread; no-op if the client already changed or is gone.
 *
 * Why the handle guard matters: a send can fail (e.g. the SO_SNDTIMEO fired on a
 * momentarily stalled link) at the same instant on_connect_request adopts a NEW
 * client (last-connection-wins) and deletes the old socket. Without the guard,
 * the failed sender would then evict the freshly reconnected, healthy client —
 * a self-inflicted reconnect storm during exactly the link flapping the long
 * send timeout is meant to survive. Comparing against `expected` makes the drop
 * a no-op once the socket we used is no longer the current one.
 ******************************************************************************/
static void drop_client_if(cy_socket_t expected, const char *why)
{
    cy_socket_t victim = CY_SOCKET_INVALID_HANDLE;

    (void)xSemaphoreTake(s_client_mutex, portMAX_DELAY);
    if ((s_client != CY_SOCKET_INVALID_HANDLE) && (s_client == expected))
    {
        victim   = s_client;
        s_client = CY_SOCKET_INVALID_HANDLE;
        s_rx_len = 0u;
    }
    (void)xSemaphoreGive(s_client_mutex);

    if (victim != CY_SOCKET_INVALID_HANDLE)
    {
        (void)cy_socket_disconnect(victim, 0u);
        (void)cy_socket_delete(victim);
        uart_stream_print("[tcp] client dropped: ");
        uart_stream_print(why);
        uart_stream_print("\r\n");
    }
}

/*******************************************************************************
 * Socket callbacks (secure-sockets worker thread context)
 ******************************************************************************/
static cy_rslt_t on_receive(cy_socket_t socket_handle, void *arg)
{
    CY_UNUSED_PARAMETER(arg);

    uint8_t chunk[TCP_RX_CHUNK];

    uart_stream_print("[tcp] rx event\r\n");
    for (;;)
    {
        /* cy_socket_recv blocks until it fills the full requested length (or
         * the receive timeout, default 10 s) — never ask for more than is
         * actually buffered, or short commands stall the callback thread
         * and collide with concurrent sends. */
        uint32_t avail  = 0u;
        uint32_t optlen = sizeof(avail);
        cy_rslt_t res = cy_socket_getsockopt(socket_handle, CY_SOCKET_SOL_SOCKET,
                                             CY_SOCKET_SO_BYTES_AVAILABLE,
                                             &avail, &optlen);
        if (res != CY_RSLT_SUCCESS)
        {
            uart_stream_print("[tcp] rx: BYTES_AVAILABLE failed\r\n");
            break;
        }
        if (avail == 0u)
        {
            break;
        }

        uint32_t want = (avail < sizeof(chunk)) ? avail : (uint32_t)sizeof(chunk);
        uint32_t n    = 0u;
        res = cy_socket_recv(socket_handle, chunk, want,
                             CY_SOCKET_FLAGS_NONE, &n);
        if ((res != CY_RSLT_SUCCESS) || (n == 0u))
        {
            uart_stream_print("[tcp] rx: recv failed\r\n");
            break;
        }

        for (uint32_t i = 0u; i < n; i++)
        {
            char ch = (char)chunk[i];
            if (ch == '\n' || ch == '\r')
            {
                if (s_rx_len > 0u)
                {
                    s_rx_line[s_rx_len] = '\0';
                    s_rx_len = 0u;

                    tcp_cmd_msg_t msg;
                    memset(&msg, 0, sizeof(msg));
                    msg.cmd = stream_parse_line(s_rx_line, &msg.cfg);
                    if (msg.cmd != UART_CMD_NONE)
                    {
                        uart_stream_print("[tcp] cmd queued\r\n");
                        /* Queue full -> drop the oldest by receiving once. */
                        if (xQueueSend(s_cmd_queue, &msg, 0) != pdPASS)
                        {
                            tcp_cmd_msg_t scrap;
                            (void)xQueueReceive(s_cmd_queue, &scrap, 0);
                            (void)xQueueSend(s_cmd_queue, &msg, 0);
                        }
                    }
                }
            }
            else if (s_rx_len < (STREAM_CMD_LINE_MAX - 1u))
            {
                s_rx_line[s_rx_len++] = ch;
            }
            else
            {
                s_rx_len = 0u;   /* overflow: discard the malformed line */
            }
        }
    }
    return CY_RSLT_SUCCESS;
}

static cy_rslt_t on_disconnect(cy_socket_t socket_handle, void *arg)
{
    CY_UNUSED_PARAMETER(arg);

    /* Drop only if this exact socket is still the active client (the guard
     * inside drop_client_if also re-checks under the mutex, closing the race
     * with a concurrent on_connect_request that may have replaced it). */
    drop_client_if(socket_handle, "peer disconnect");
    return CY_RSLT_SUCCESS;
}

static cy_rslt_t on_connect_request(cy_socket_t socket_handle, void *arg)
{
    CY_UNUSED_PARAMETER(arg);

    cy_socket_sockaddr_t peer;
    uint32_t             peer_len = sizeof(peer);
    cy_socket_t          accepted = CY_SOCKET_INVALID_HANDLE;

    cy_rslt_t result = cy_socket_accept(socket_handle, &peer, &peer_len,
                                        &accepted);
    if (result != CY_RSLT_SUCCESS)
    {
        return result;
    }

    /* Low-latency small frames + failure detection on the accepted socket. */
    uint32_t nodelay = 1u;
    (void)cy_socket_setsockopt(accepted, CY_SOCKET_SOL_TCP,
                               CY_SOCKET_SO_TCP_NODELAY,
                               &nodelay, sizeof(nodelay));
    uint32_t snd_timeout = TCP_SEND_TIMEOUT_MS;
    (void)cy_socket_setsockopt(accepted, CY_SOCKET_SOL_SOCKET,
                               CY_SOCKET_SO_SNDTIMEO,
                               &snd_timeout, sizeof(snd_timeout));
    /* No RCVTIMEO override: reads in on_receive are sized to the buffered
     * byte count, so they never block on the (default 10 s) receive timeout. */

    /* NOTE: deliberately no TCP keepalive here. On this lwIP/WHD path the
     * keepalive timer aborted every connection that only carried outbound
     * traffic (the host bridge is silent between commands). Ghost clients
     * are handled by the last-connection-wins eviction below instead. */

    cy_socket_opt_callback_t cb;
    cb.callback = on_receive;
    cb.arg      = NULL;
    (void)cy_socket_setsockopt(accepted, CY_SOCKET_SOL_SOCKET,
                               CY_SOCKET_SO_RECEIVE_CALLBACK,
                               &cb, sizeof(cb));
    cb.callback = on_disconnect;
    (void)cy_socket_setsockopt(accepted, CY_SOCKET_SOL_SOCKET,
                               CY_SOCKET_SO_DISCONNECT_CALLBACK,
                               &cb, sizeof(cb));

    /* Last connection wins: adopt the newcomer and evict any previous
     * client. A refuse-while-busy policy would let one wedged connection
     * (e.g. dropped without FIN during a Wi-Fi roam) lock out the bridge's
     * reconnect attempts forever. */
    (void)xSemaphoreTake(s_client_mutex, portMAX_DELAY);
    cy_socket_t old = s_client;
    s_client = accepted;
    s_rx_len = 0u;
    (void)xSemaphoreGive(s_client_mutex);

    if (old != CY_SOCKET_INVALID_HANDLE)
    {
        (void)cy_socket_disconnect(old, 0u);
        (void)cy_socket_delete(old);
        uart_stream_print("[tcp] client replaced by new connection\r\n");
    }
    else
    {
        uart_stream_print("[tcp] client connected\r\n");
    }
    return CY_RSLT_SUCCESS;
}

/*******************************************************************************
 * Sends len bytes on the client socket; s_client_mutex must be held and the
 * client checked. Loops over partial sends.
 ******************************************************************************/
static bool send_locked(const uint8_t *data, uint32_t len)
{
    while (len > 0u)
    {
        uint32_t  sent   = 0u;
        cy_rslt_t result = cy_socket_send(s_client, data, len,
                                          CY_SOCKET_FLAGS_NONE, &sent);
        if ((result != CY_RSLT_SUCCESS) || (sent == 0u))
        {
            return false;
        }
        data += sent;
        len  -= sent;
    }
    return true;
}

/*******************************************************************************
 * Sends a fully encoded frame to the client. Returns false (and drops the
 * client) on any send failure — at our data rates a stalled link recovers
 * faster through reconnect than through retries.
 ******************************************************************************/
static bool send_frame(const uint8_t *frame, uint32_t len)
{
    bool        ok   = false;
    cy_socket_t sock = CY_SOCKET_INVALID_HANDLE;

    (void)xSemaphoreTake(s_client_mutex, portMAX_DELAY);
    sock = s_client;                       /* the socket we send on this call */
    if (sock != CY_SOCKET_INVALID_HANDLE)
    {
        ok = send_locked(frame, len);
    }
    (void)xSemaphoreGive(s_client_mutex);

    if (!ok && (sock != CY_SOCKET_INVALID_HANDLE))
    {
        drop_client_if(sock, "send failed");
    }
    return ok;
}

/*******************************************************************************
 * Public API
 ******************************************************************************/
bool tcp_stream_init(uint16_t port)
{
    s_client_mutex = xSemaphoreCreateMutex();
    s_cmd_queue    = xQueueCreate(TCP_CMD_QUEUE_DEPTH, sizeof(tcp_cmd_msg_t));
    if ((s_client_mutex == NULL) || (s_cmd_queue == NULL))
    {
        return false;
    }

    if (cy_socket_create(CY_SOCKET_DOMAIN_AF_INET, CY_SOCKET_TYPE_STREAM,
                         CY_SOCKET_IPPROTO_TCP, &s_listener) != CY_RSLT_SUCCESS)
    {
        return false;
    }

    cy_socket_opt_callback_t cb;
    cb.callback = on_connect_request;
    cb.arg      = NULL;
    if (cy_socket_setsockopt(s_listener, CY_SOCKET_SOL_SOCKET,
                             CY_SOCKET_SO_CONNECT_REQUEST_CALLBACK,
                             &cb, sizeof(cb)) != CY_RSLT_SUCCESS)
    {
        return false;
    }

    cy_socket_sockaddr_t addr;
    memset(&addr, 0, sizeof(addr));
    addr.port                  = port;
    addr.ip_address.version    = CY_SOCKET_IP_VER_V4;
    addr.ip_address.ip.v4      = 0u;   /* any interface (only the AP exists) */

    if (cy_socket_bind(s_listener, &addr, sizeof(addr)) != CY_RSLT_SUCCESS)
    {
        return false;
    }
    if (cy_socket_listen(s_listener, TCP_LISTEN_BACKLOG) != CY_RSLT_SUCCESS)
    {
        return false;
    }
    return true;
}

bool tcp_stream_connected(void)
{
    return s_client != CY_SOCKET_INVALID_HANDLE;
}

bool tcp_stream_send_sample(const uart_imu_sample_t *sample)
{
    uint8_t frame[STREAM_SAMPLE_FRAME_LEN];
    uint32_t len = stream_encode_sample(frame, sample);
    return send_frame(frame, len);
}

bool tcp_stream_send_samples(const uart_imu_sample_t *samples, uint32_t count)
{
    /* One static assembly buffer: this is only ever called from the single IMU
     * streaming task, and the bytes are copied into the socket before return. */
    static uint8_t batch[TCP_STREAM_BATCH_MAX * STREAM_SAMPLE_FRAME_LEN];

    if ((samples == NULL) || (count == 0u) || (count > TCP_STREAM_BATCH_MAX))
    {
        return false;
    }
    if (!tcp_stream_connected())
    {
        return false;
    }

    uint32_t len = 0u;
    for (uint32_t i = 0u; i < count; i++)
    {
        len += stream_encode_sample(&batch[len], &samples[i]);
    }

    bool        ok   = false;
    cy_socket_t sock = CY_SOCKET_INVALID_HANDLE;
    (void)xSemaphoreTake(s_client_mutex, portMAX_DELAY);
    sock = s_client;
    if (sock != CY_SOCKET_INVALID_HANDLE)
    {
        ok = send_locked(batch, len);
    }
    (void)xSemaphoreGive(s_client_mutex);

    if (!ok && (sock != CY_SOCKET_INVALID_HANDLE))
    {
        drop_client_if(sock, "imu batch send failed");
    }
    return ok;
}

void tcp_stream_send_status(uint8_t imu_src, uint8_t mag_src, const char *reason)
{
    if (!tcp_stream_connected())
    {
        return;
    }
    uint8_t frame[STREAM_STATUS_FRAME_MAX];
    uint32_t len = stream_encode_status(frame, imu_src, mag_src, reason);
    (void)send_frame(frame, len);
}

bool tcp_stream_send_camera(uint32_t frame_id, uint16_t width, uint16_t height,
                            const uint8_t *jpeg, uint32_t jpeg_len)
{
    if ((jpeg == NULL) || (jpeg_len == 0u) ||
        (jpeg_len > (0xFFFFu - CAM_FRAME_HDR_LEN)))
    {
        return false;
    }
    if (!tcp_stream_connected())
    {
        return false;
    }

    /* Wire frame header + 8-byte camera payload header in one small buffer;
     * the JPEG itself is sent straight out of the caller's (shared SOCMEM)
     * buffer to avoid a 60 KB copy on this core. */
    uint16_t payload_len = (uint16_t)(CAM_FRAME_HDR_LEN + jpeg_len);
    uint8_t  hdr[5u + CAM_FRAME_HDR_LEN];
    hdr[0]  = UART_FRAME_MAGIC0;
    hdr[1]  = UART_FRAME_MAGIC1;
    hdr[2]  = CAM_FRAME_TYPE;
    hdr[3]  = (uint8_t)(payload_len & 0xFFu);
    hdr[4]  = (uint8_t)((payload_len >> 8) & 0xFFu);
    hdr[5]  = (uint8_t)(frame_id & 0xFFu);
    hdr[6]  = (uint8_t)((frame_id >> 8) & 0xFFu);
    hdr[7]  = (uint8_t)((frame_id >> 16) & 0xFFu);
    hdr[8]  = (uint8_t)((frame_id >> 24) & 0xFFu);
    hdr[9]  = (uint8_t)(width & 0xFFu);
    hdr[10] = (uint8_t)((width >> 8) & 0xFFu);
    hdr[11] = (uint8_t)(height & 0xFFu);
    hdr[12] = (uint8_t)((height >> 8) & 0xFFu);

    uint16_t crc = stream_crc16_update(0xFFFFu, &hdr[5], CAM_FRAME_HDR_LEN);
    crc = stream_crc16_update(crc, jpeg, jpeg_len);
    uint8_t trailer[2] = { (uint8_t)(crc & 0xFFu), (uint8_t)((crc >> 8) & 0xFFu) };

    bool        ok   = false;
    cy_socket_t sock = CY_SOCKET_INVALID_HANDLE;
    (void)xSemaphoreTake(s_client_mutex, portMAX_DELAY);
    sock = s_client;
    if (sock != CY_SOCKET_INVALID_HANDLE)
    {
        ok = send_locked(hdr, sizeof(hdr)) &&
             send_locked(jpeg, jpeg_len) &&
             send_locked(trailer, sizeof(trailer));
    }
    (void)xSemaphoreGive(s_client_mutex);

    if (!ok && (sock != CY_SOCKET_INVALID_HANDLE))
    {
        drop_client_if(sock, "camera send failed");
    }
    return ok;
}

bool tcp_stream_send_audio(uint32_t seq, uint16_t sample_rate, uint8_t channels,
                           uint8_t bits, const uint8_t *pcm, uint32_t pcm_len)
{
    if ((pcm == NULL) || (pcm_len == 0u) ||
        (pcm_len > (0xFFFFu - AUDIO_FRAME_HDR_LEN)))
    {
        return false;
    }
    if (!tcp_stream_connected())
    {
        return false;
    }

    /* Wire frame header + 8-byte audio payload header; the PCM is sent straight
     * from the caller's buffer. */
    uint16_t payload_len = (uint16_t)(AUDIO_FRAME_HDR_LEN + pcm_len);
    uint8_t  hdr[5u + AUDIO_FRAME_HDR_LEN];
    hdr[0]  = UART_FRAME_MAGIC0;
    hdr[1]  = UART_FRAME_MAGIC1;
    hdr[2]  = AUDIO_FRAME_TYPE;
    hdr[3]  = (uint8_t)(payload_len & 0xFFu);
    hdr[4]  = (uint8_t)((payload_len >> 8) & 0xFFu);
    hdr[5]  = (uint8_t)(seq & 0xFFu);
    hdr[6]  = (uint8_t)((seq >> 8) & 0xFFu);
    hdr[7]  = (uint8_t)((seq >> 16) & 0xFFu);
    hdr[8]  = (uint8_t)((seq >> 24) & 0xFFu);
    hdr[9]  = (uint8_t)(sample_rate & 0xFFu);
    hdr[10] = (uint8_t)((sample_rate >> 8) & 0xFFu);
    hdr[11] = channels;
    hdr[12] = bits;

    uint16_t crc = stream_crc16_update(0xFFFFu, &hdr[5], AUDIO_FRAME_HDR_LEN);
    crc = stream_crc16_update(crc, pcm, pcm_len);
    uint8_t trailer[2] = { (uint8_t)(crc & 0xFFu), (uint8_t)((crc >> 8) & 0xFFu) };

    bool        ok   = false;
    cy_socket_t sock = CY_SOCKET_INVALID_HANDLE;
    (void)xSemaphoreTake(s_client_mutex, portMAX_DELAY);
    sock = s_client;
    if (sock != CY_SOCKET_INVALID_HANDLE)
    {
        ok = send_locked(hdr, sizeof(hdr)) &&
             send_locked(pcm, pcm_len) &&
             send_locked(trailer, sizeof(trailer));
    }
    (void)xSemaphoreGive(s_client_mutex);

    if (!ok && (sock != CY_SOCKET_INVALID_HANDLE))
    {
        drop_client_if(sock, "audio send failed");
    }
    return ok;
}

uart_cmd_t tcp_stream_poll_command(uart_cfg_t *out_cfg)
{
    tcp_cmd_msg_t msg;
    if ((s_cmd_queue != NULL) && (xQueueReceive(s_cmd_queue, &msg, 0) == pdPASS))
    {
        if ((msg.cmd == UART_CMD_CONFIG) && (out_cfg != NULL))
        {
            *out_cfg = msg.cfg;
        }
        return msg.cmd;
    }
    return UART_CMD_NONE;
}
