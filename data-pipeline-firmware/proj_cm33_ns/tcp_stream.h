/*******************************************************************************
 * File Name        : tcp_stream.h
 *
 * Description      : Wi-Fi TCP transport for the Motion Studio stream. Runs a
 *                    single-client TCP server (secure-sockets) that carries
 *                    exactly the same bytes as the UART transport: binary
 *                    sample/status frames out, newline-terminated ASCII
 *                    commands (S / Q / CFG,...) in. The host-side bridge
 *                    (host_server/imu_server.py --tcp) connects to it.
 *
 *  Threading model:
 *    - tcp_stream_init() is called once after the network (SoftAP) is up.
 *    - Sends happen on the sensor task; socket callbacks (accept/receive/
 *      disconnect) run on the secure-sockets worker thread. A mutex guards
 *      the client socket handle; received commands are handed to the sensor
 *      task through a FreeRTOS queue.
 *******************************************************************************/

#ifndef TCP_STREAM_H
#define TCP_STREAM_H

#include "uart_stream.h"   /* uart_imu_sample_t / uart_cfg_t / uart_cmd_t */

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*******************************************************************************
* Starts the TCP server on the given port (call once, network must be up).
* Returns true on success.
*******************************************************************************/
bool tcp_stream_init(uint16_t port);

/*******************************************************************************
* True while a client is connected.
*******************************************************************************/
bool tcp_stream_connected(void);

/*******************************************************************************
* Encodes and sends one IMU sample frame to the connected client.
* Returns false if there is no client or the send failed (the client is
* dropped on failure, so the caller can simply stop streaming).
*******************************************************************************/
bool tcp_stream_send_sample(const uart_imu_sample_t *sample);

/*******************************************************************************
* Encodes and sends a batch of IMU sample frames back-to-back in a single
* socket write (one mutex acquire, one send) so a high-rate stream costs far
* fewer syscalls than one frame per sample. The bytes on the wire are identical
* to count separate tcp_stream_send_sample() calls. count must be
* <= TCP_STREAM_BATCH_MAX. Returns false if there is no client or the send
* failed (the client is dropped on failure).
*******************************************************************************/
#define TCP_STREAM_BATCH_MAX   (32u)
bool tcp_stream_send_samples(const uart_imu_sample_t *samples, uint32_t count);

/*******************************************************************************
* Encodes and sends a STATUS frame (best-effort; no-op without a client).
*******************************************************************************/
void tcp_stream_send_status(uint8_t imu_src, uint8_t mag_src, const char *reason);

/*******************************************************************************
* Sends one camera frame (type 0x30): 8-byte camera header + JPEG bitstream,
* framed and CRC'd like every other frame. The JPEG data is sent directly
* from the caller's buffer (shared SOCMEM) without an intermediate copy.
* Returns false if there is no client or the send failed.
*******************************************************************************/
bool tcp_stream_send_camera(uint32_t frame_id, uint16_t width, uint16_t height,
                            const uint8_t *jpeg, uint32_t jpeg_len);

/*******************************************************************************
* Sends one audio frame (type 0x40): 8-byte audio header + interleaved PCM,
* framed and CRC'd like every other frame. Returns false if there is no client
* or the send failed (the client is dropped on failure).
*******************************************************************************/
bool tcp_stream_send_audio(uint32_t seq, uint16_t sample_rate, uint8_t channels,
                           uint8_t bits, const uint8_t *pcm, uint32_t pcm_len);

/*******************************************************************************
* Returns the next command received from the client, or UART_CMD_NONE.
* When UART_CMD_CONFIG is returned, *out_cfg is populated.
*******************************************************************************/
uart_cmd_t tcp_stream_poll_command(uart_cfg_t *out_cfg);

#ifdef __cplusplus
}
#endif

#endif /* TCP_STREAM_H */
