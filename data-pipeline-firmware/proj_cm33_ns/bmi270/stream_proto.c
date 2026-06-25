/*******************************************************************************
 * File Name        : stream_proto.c
 *
 * Description      : See stream_proto.h. The encoding/parsing logic moved here
 *                    verbatim from uart_stream.c when the TCP transport was
 *                    added, so both transports share one implementation.
 *******************************************************************************/

#include "stream_proto.h"

#include <string.h>
#include <stdlib.h>

uint16_t stream_crc16_update(uint16_t crc, const uint8_t *data, uint32_t len)
{
    for (uint32_t i = 0u; i < len; i++)
    {
        crc ^= data[i];
        for (uint8_t b = 0u; b < 8u; b++)
        {
            crc = (crc & 0x0001u) ? (uint16_t)((crc >> 1) ^ 0xA001u)
                                  : (uint16_t)(crc >> 1);
        }
    }
    return crc;
}

uint16_t stream_crc16(const uint8_t *data, uint32_t len)
{
    return stream_crc16_update(0xFFFFu, data, len);
}

uint32_t stream_encode_sample(uint8_t *buf, const uart_imu_sample_t *sample)
{
    uint8_t *p = buf;

    *p++ = UART_FRAME_MAGIC0;
    *p++ = UART_FRAME_MAGIC1;
    *p++ = UART_FRAME_TYPE_IMU;
    *p++ = (uint8_t)(UART_IMU_PAYLOAD_LEN & 0xFFu);
    *p++ = (uint8_t)((UART_IMU_PAYLOAD_LEN >> 8) & 0xFFu);

    uint8_t *payload = p;

    /* int32 t_us (little-endian) */
    *p++ = (uint8_t)(sample->t_us & 0xFFu);
    *p++ = (uint8_t)((sample->t_us >> 8) & 0xFFu);
    *p++ = (uint8_t)((sample->t_us >> 16) & 0xFFu);
    *p++ = (uint8_t)((sample->t_us >> 24) & 0xFFu);

    for (int i = 0; i < 3; i++)
    {
        *p++ = (uint8_t)(sample->acc[i] & 0xFFu);
        *p++ = (uint8_t)((sample->acc[i] >> 8) & 0xFFu);
    }
    for (int i = 0; i < 3; i++)
    {
        *p++ = (uint8_t)(sample->gyr[i] & 0xFFu);
        *p++ = (uint8_t)((sample->gyr[i] >> 8) & 0xFFu);
    }
    *p++ = (uint8_t)(sample->temp & 0xFFu);
    *p++ = (uint8_t)((sample->temp >> 8) & 0xFFu);

    for (int i = 0; i < 3; i++)
    {
        *p++ = (uint8_t)(sample->mag[i] & 0xFFu);
        *p++ = (uint8_t)((sample->mag[i] >> 8) & 0xFFu);
    }

    uint16_t crc = stream_crc16(payload, UART_IMU_PAYLOAD_LEN);
    *p++ = (uint8_t)(crc & 0xFFu);
    *p++ = (uint8_t)((crc >> 8) & 0xFFu);

    return (uint32_t)(p - buf);
}

uint32_t stream_encode_status(uint8_t *buf, uint8_t imu_src, uint8_t mag_src,
                              const char *reason)
{
    uint8_t *p = buf;

    uint32_t rlen = 0u;
    if (reason != NULL)
    {
        rlen = (uint32_t)strlen(reason);
        if (rlen > STREAM_STATUS_REASON_MAX) { rlen = STREAM_STATUS_REASON_MAX; }
    }
    uint16_t payload_len = (uint16_t)(2u + rlen);

    *p++ = UART_FRAME_MAGIC0;
    *p++ = UART_FRAME_MAGIC1;
    *p++ = UART_FRAME_TYPE_STATUS;
    *p++ = (uint8_t)(payload_len & 0xFFu);
    *p++ = (uint8_t)((payload_len >> 8) & 0xFFu);

    uint8_t *payload = p;
    *p++ = imu_src;
    *p++ = mag_src;
    for (uint32_t i = 0u; i < rlen; i++)
    {
        *p++ = (uint8_t)reason[i];
    }

    uint16_t crc = stream_crc16(payload, payload_len);
    *p++ = (uint8_t)(crc & 0xFFu);
    *p++ = (uint8_t)((crc >> 8) & 0xFFu);

    return (uint32_t)(p - buf);
}

uart_cmd_t stream_parse_line(const char *line, uart_cfg_t *out_cfg)
{
    if ((line[0] == 'S' || line[0] == 's') && line[1] == '\0')
    {
        return UART_CMD_START;
    }
    if ((line[0] == 'Q' || line[0] == 'q') && line[1] == '\0')
    {
        return UART_CMD_STOP;
    }

    if (strncmp(line, "CFG", 3) == 0)
    {
        /* Tokenize a writable copy. */
        char buf[STREAM_CMD_LINE_MAX];
        strncpy(buf, line, STREAM_CMD_LINE_MAX - 1);
        buf[STREAM_CMD_LINE_MAX - 1] = '\0';

        char *save = NULL;
        char *tok  = strtok_r(buf, ",", &save);          /* "CFG"      */
        char *a    = strtok_r(NULL, ",", &save);          /* accOdr     */
        char *b    = strtok_r(NULL, ",", &save);          /* accRange   */
        char *c    = strtok_r(NULL, ",", &save);          /* gyrOdr     */
        char *d    = strtok_r(NULL, ",", &save);          /* gyrRange   */
        char *e    = strtok_r(NULL, ",", &save);          /* power      */
        (void)tok;

        if (a && b && c && d && out_cfg)
        {
            out_cfg->acc_odr   = (uint16_t)atoi(a);
            out_cfg->acc_range = (uint8_t)atoi(b);
            out_cfg->gyr_odr   = (uint16_t)atoi(c);
            out_cfg->gyr_range = (uint16_t)atoi(d);
            out_cfg->power[0]  = '\0';
            if (e)
            {
                strncpy(out_cfg->power, e, sizeof(out_cfg->power) - 1);
                out_cfg->power[sizeof(out_cfg->power) - 1] = '\0';
            }
            return UART_CMD_CONFIG;
        }
    }

    return UART_CMD_NONE;
}
