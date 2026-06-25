/*******************************************************************************
 * File Name        : uart_stream.h
 *
 * Description      : WebSerial streaming transport for the BMI270 Motion Studio
 *                    web UI. Initializes the KitProg3 debug UART (SCB2), encodes
 *                    IMU samples into the binary frame format understood by
 *                    bmi270_web_streaming.html, and parses the ASCII commands the
 *                    browser sends back (S / Q / CFG).
 *
 *  Wire format (little-endian), one frame per sample:
 *      0xAB 0xCD              magic
 *      type   (uint8)        UART_FRAME_TYPE_IMU = 0x10
 *      len    (uint16)       payload length (= UART_IMU_PAYLOAD_LEN = 24)
 *      payload (len bytes)
 *      crc16  (uint16)       CRC-16/IBM (poly 0xA001, init 0xFFFF) over payload
 *
 *  IMU payload (24 bytes):
 *      int32  t_us           free-running microsecond timestamp
 *      int16  acc_x,y,z      raw signed counts at the configured range
 *      int16  gyr_x,y,z      raw signed counts at the configured range
 *      int16  temp_raw       BMI270 temperature register value
 *      int16  mag_x,y,z      BMM350 field, 1/256 microtesla per count
 *
 *  NOTE: the magnetometer fields were appended after temp_raw, so the original
 *  offsets (acc/gyr/temp) are unchanged - older decoders that stop at 18 bytes
 *  still read acc/gyr/temp correctly.
 *******************************************************************************/

#ifndef UART_STREAM_H
#define UART_STREAM_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define UART_FRAME_MAGIC0       (0xABu)
#define UART_FRAME_MAGIC1       (0xCDu)
#define UART_FRAME_TYPE_IMU     (0x10u)
#define UART_FRAME_TYPE_STATUS  (0x20u)   /* sensor source + diagnostic text */
#define UART_IMU_PAYLOAD_LEN    (24u)

/** One decoded IMU sample in raw sensor counts. */
typedef struct
{
    int32_t t_us;
    int16_t acc[3];
    int16_t gyr[3];
    int16_t temp;
    int16_t mag[3];     /* BMM350 field, 1/256 uT per count */
} uart_imu_sample_t;

/** Configuration parsed from a "CFG,..." command sent by the browser. */
typedef struct
{
    uint16_t acc_odr;   /* Hz   */
    uint8_t  acc_range; /* g    */
    uint16_t gyr_odr;   /* Hz   */
    uint16_t gyr_range; /* dps  */
    char     power[16]; /* "normal" | "performance" | "lowpower" */
} uart_cfg_t;

/** Commands decoded from the receive line buffer. */
typedef enum
{
    UART_CMD_NONE = 0,
    UART_CMD_START,     /* 'S' */
    UART_CMD_STOP,      /* 'Q' */
    UART_CMD_CONFIG     /* 'CFG,...' -> fills out_cfg */
} uart_cmd_t;

/*******************************************************************************
* Initializes the debug UART (SCB2) using the BSP-generated configuration.
* Returns true on success.
*******************************************************************************/
bool uart_stream_init(void);

/*******************************************************************************
* Sends a NUL-terminated string (used for human-readable boot/diagnostic logs).
*******************************************************************************/
void uart_stream_print(const char *str);

/*******************************************************************************
* Encodes and transmits one IMU sample as a binary frame. Blocking with FIFO
* back-pressure (yields to the scheduler when the TX FIFO is full).
*******************************************************************************/
void uart_stream_send_sample(const uart_imu_sample_t *sample);

/*******************************************************************************
* Drains the UART receive FIFO and returns the next decoded command, if any.
* When UART_CMD_CONFIG is returned, *out_cfg is populated.
*******************************************************************************/
uart_cmd_t uart_stream_poll_command(uart_cfg_t *out_cfg);

/*******************************************************************************
* Sends a STATUS frame (type 0x20) carrying the active sensor sources and a
* short diagnostic string, so the web UI can always show whether the IMU/mag
* are real or synthetic (and why). Payload: imu_src(u8), mag_src(u8), then the
* NUL-free reason text.
*******************************************************************************/
void uart_stream_send_status(uint8_t imu_src, uint8_t mag_src, const char *reason);

#ifdef __cplusplus
}
#endif

#endif /* UART_STREAM_H */
