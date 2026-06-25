/*******************************************************************************
 * File Name        : stream_proto.h
 *
 * Description      : Transport-independent pieces of the Motion Studio wire
 *                    protocol: CRC-16, binary frame encoding, and ASCII command
 *                    parsing. Shared by the UART transport (uart_stream.c) and
 *                    the Wi-Fi TCP transport (tcp_stream.c) so both emit
 *                    byte-identical frames and accept identical commands.
 *
 *  Frame format and command grammar are documented in uart_stream.h.
 *******************************************************************************/

#ifndef STREAM_PROTO_H
#define STREAM_PROTO_H

#include "uart_stream.h"   /* frame constants + uart_imu_sample_t/cfg/cmd types */

#ifdef __cplusplus
extern "C" {
#endif

/* Encoded frame sizes (header 5 + payload + CRC 2). */
#define STREAM_SAMPLE_FRAME_LEN   (5u + UART_IMU_PAYLOAD_LEN + 2u)   /* 31    */
#define STREAM_STATUS_REASON_MAX  (64u)
#define STREAM_STATUS_FRAME_MAX   (5u + 2u + STREAM_STATUS_REASON_MAX + 2u)

/* Longest accepted command line (excluding the terminator). */
#define STREAM_CMD_LINE_MAX       (64u)

/*******************************************************************************
* CRC-16/IBM (poly 0xA001, init 0xFFFF) over the frame payload.
*******************************************************************************/
uint16_t stream_crc16(const uint8_t *data, uint32_t len);

/*******************************************************************************
* Incremental form: seed with 0xFFFF, feed buffers in order. Used for large
* payloads (camera frames) that are sent without assembling one flat buffer.
*******************************************************************************/
uint16_t stream_crc16_update(uint16_t crc, const uint8_t *data, uint32_t len);

/*******************************************************************************
* Encodes one IMU sample into buf (must hold STREAM_SAMPLE_FRAME_LEN bytes).
* Returns the frame length.
*******************************************************************************/
uint32_t stream_encode_sample(uint8_t *buf, const uart_imu_sample_t *sample);

/*******************************************************************************
* Encodes a STATUS frame into buf (must hold STREAM_STATUS_FRAME_MAX bytes).
* reason may be NULL; it is truncated to STREAM_STATUS_REASON_MAX characters.
* Returns the frame length.
*******************************************************************************/
uint32_t stream_encode_status(uint8_t *buf, uint8_t imu_src, uint8_t mag_src,
                              const char *reason);

/*******************************************************************************
* Parses one complete command line ("S" / "Q" / "CFG,..."). When the result is
* UART_CMD_CONFIG, *out_cfg is populated. Returns UART_CMD_NONE for anything
* unrecognized.
*******************************************************************************/
uart_cmd_t stream_parse_line(const char *line, uart_cfg_t *out_cfg);

#ifdef __cplusplus
}
#endif

#endif /* STREAM_PROTO_H */
