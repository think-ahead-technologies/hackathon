/*******************************************************************************
 * File Name        : imu_stream.c
 *
 * Description      : See imu_stream.h. Ported from the CM55 project's
 *                    imu_stream_task with one addition: commands can now also
 *                    arrive over the Wi-Fi TCP transport, and the stream is
 *                    sent to whichever transport issued the start command.
 *
 *  Transport arbitration: 'S' claims the stream for the transport it arrived
 *  on; 'Q' from either transport (or a TCP client disconnect) releases it.
 *  'CFG' applies from either transport at any time. The UART rate cap stays
 *  at 200 Hz (115200-baud ceiling); the TCP push rate is capped at 250 Hz,
 *  decoupled from the sensor ODR (see TCP_STREAM_MAX_HZ).
 *******************************************************************************/

#include "imu_stream.h"

#include "cybsp.h"

#include "FreeRTOS.h"
#include "task.h"

#include "uart_stream.h"
#include "imu_app.h"
#include "mag_app.h"
#include "tcp_stream.h"

#include <stdio.h>

/*******************************************************************************
 * Macros
 ******************************************************************************/
#define IMU_TASK_NAME               ("BMI270 Stream Task")
#define IMU_TASK_STACK_SIZE         (configMINIMAL_STACK_SIZE * 12)
/* Must stay BELOW the Wi-Fi/lwIP worker threads (CY_RTOS_PRIORITY_HIGH ==
 * configMAX_PRIORITIES*5/7 == 5 on this 7-level config) AND below the
 * camera/audio forwarders (configMAX_PRIORITIES-4). The BMI270 hardware FIFO
 * buffers samples, so even if this task is briefly starved it loses no data —
 * it just drains a slightly larger batch next time. Running it low therefore
 * gives the network and the other media tasks priority without dropping IMU
 * samples. */
#define IMU_TASK_PRIORITY           (configMAX_PRIORITIES - 5)

/* The KitProg3 bridge runs the debug UART at 115200 baud (8N1); 31-byte frames
 * cap that link at ~200 Hz, so the UART path streams a decimated single sample
 * per period. The TCP path instead drains the BMI270 FIFO every
 * TCP_FIFO_DRAIN_MS and ships every captured sample as a batch, so the full
 * sensor ODR (up to 1600 Hz) is recorded gap-free. */
#define UART_STREAM_MAX_HZ          (200u)
#define TCP_STREAM_MAX_HZ           (250u)
/* Drain interval: longer means fewer, larger batches => far fewer TCP segments
 * per second (one ~1 KB send per drain instead of ~200 tiny sends/s), which is
 * what the lwIP/WHD path is sensitive to. No samples are lost: the BMI270 FIFO
 * buffers everything between drains (20 ms @ 1600 Hz = 32 frames, well under the
 * ~170-frame hardware FIFO). */
#define TCP_FIFO_DRAIN_MS           (20u)
#define TCP_FIFO_MAX_BATCHES_CYCLE  (8u)

typedef enum
{
    TRANSPORT_NONE = 0,
    TRANSPORT_UART,
    TRANSPORT_TCP
} transport_t;

/*******************************************************************************
* Derives the per-sample task period (in RTOS ticks, minimum one) from the
* configured accelerometer ODR, capped per transport.
*******************************************************************************/
static TickType_t stream_period_ticks(uint16_t acc_odr, transport_t transport)
{
    uint32_t cap = (transport == TRANSPORT_TCP) ? TCP_STREAM_MAX_HZ
                                                : UART_STREAM_MAX_HZ;
    uint32_t hz = (acc_odr == 0u) ? cap : acc_odr;
    if (hz > cap)
    {
        hz = cap;
    }
    TickType_t period = pdMS_TO_TICKS(1000u / hz);
    return (period == 0u) ? 1u : period;
}

/*******************************************************************************
* Sends a STATUS frame to every transport that can currently carry it.
*******************************************************************************/
static void send_status_all(imu_source_t src, mag_source_t mag)
{
    uart_stream_send_status((uint8_t)src, (uint8_t)mag, mag_app_status_str());
    tcp_stream_send_status((uint8_t)src, (uint8_t)mag, mag_app_status_str());
}

/*******************************************************************************
* Function Name: imu_stream_task
********************************************************************************
* Handles commands from both transports and, while streaming, pushes one IMU
* frame per period to the transport that owns the stream.
*******************************************************************************/
static void imu_stream_task(void *arg)
{
    CY_UNUSED_PARAMETER(arg);

    imu_source_t src = imu_app_init();
    /* The magnetometer is on a separate I3C controller (not the IMU's I2C bus);
     * mag_app_init() brings that controller up itself. */
    mag_source_t mag = mag_app_init();

    uart_stream_print("\r\n=== PSOC Edge E84 - BMI270 Motion Studio ===\r\n");
    if (src == IMU_SOURCE_BMI270)
    {
        uart_stream_print("BMI270 detected on I2C. Streaming real sensor data.\r\n");
    }
    else
    {
        uart_stream_print("BMI270 NOT found (or SensorAPI absent): streaming "
                          "SYNTHETIC demo data. Run 'make getlibs' and rebuild "
                          "for the real sensor.\r\n");
    }
    if (mag == MAG_SOURCE_BMM350)
    {
        uart_stream_print("BMM350 detected. Magnetic heading available "
                          "(fuses out gyro yaw drift in the web UI).\r\n");
    }
    else
    {
        uart_stream_print("BMM350 NOT found: streaming SYNTHETIC (static) "
                          "magnetometer field. Reason: ");
        uart_stream_print(mag_app_status_str());
        uart_stream_print("\r\n");
    }
    uart_stream_print("Transports: KitProg3 UART (115200) and TCP over the "
                      "SoftAP (see boot lines above).\r\n");

    /* Also emit the source/diagnostic as an in-band STATUS frame so the web UI
     * can display it regardless of when the browser attaches. */
    send_status_all(src, mag);

    /* 'armed' is the user's intent (set by Start, cleared by Stop). It is
     * deliberately NOT cleared on a TCP send failure: like the camera/audio
     * forwarders, IMU streaming pauses while the client is gone and resumes
     * automatically on reconnect, so a transient drop no longer kills it. */
    bool        armed      = false;
    transport_t active     = TRANSPORT_NONE;
    uint16_t    cfg_odr    = 100u;
    TickType_t  period     = stream_period_ticks(cfg_odr, TRANSPORT_NONE);
    TickType_t  last_wake  = xTaskGetTickCount();
    uint32_t    idle_ticks = 0u;
    bool        tcp_was_up = false;
    uart_cfg_t  cfg;
    /* Static (not on the task stack): only this task touches it. */
    static uart_imu_sample_t batch[TCP_STREAM_BATCH_MAX];
    /* Latest magnetometer reading, refreshed at <= ~400 Hz and stamped onto
     * each IMU sample (mag rides on IMU frames). */
    int16_t     mag_cached[3] = { 0, 0, 0 };
    uint32_t    mag_cnt       = 0u;
    /* High-rate diagnostics: real samples drained per ~2 s window + FIFO state,
     * to pin whether a sub-ODR capture rate is sensor-bound or drain-bound. */
    uint32_t    dbg_drained   = 0u;
    TickType_t  dbg_last      = xTaskGetTickCount();

    for (;;)
    {
        /* Drain pending command(s) from both transports. A command acts on /
         * claims the transport it arrived on. */
        for (transport_t from = TRANSPORT_UART; from <= TRANSPORT_TCP; from++)
        {
            uart_cmd_t cmd;
            while ((cmd = (from == TRANSPORT_UART)
                              ? uart_stream_poll_command(&cfg)
                              : tcp_stream_poll_command(&cfg)) != UART_CMD_NONE)
            {
                switch (cmd)
                {
                    case UART_CMD_START:
                        armed      = true;
                        active     = from;
                        period     = stream_period_ticks(cfg_odr, active);
                        last_wake  = xTaskGetTickCount();
                        tcp_was_up = false;        /* force a fresh-flush edge */
                        send_status_all(src, mag);
                        uart_stream_print("[stream] started\r\n");
                        break;

                    case UART_CMD_STOP:
                        armed  = false;
                        active = TRANSPORT_NONE;
                        uart_stream_print("[stream] stopped\r\n");
                        break;

                    case UART_CMD_CONFIG:
                    {
                        bool cfg_ok = imu_app_configure(&cfg);
                        cfg_odr = cfg.acc_odr;
                        period  = stream_period_ticks(cfg_odr, active);
                        /* Report the ACTUAL ODR the sensor accepted, not just
                         * the request — a silent apply failure used to look
                         * like success here and leave the sensor at its old
                         * (low) rate while the host believed it was high. */
                        uint8_t a_odr = 0u, g_odr = 0u;
                        imu_app_fifo_debug(NULL, NULL, NULL, &a_odr, &g_odr);
                        char line[96];
                        (void)snprintf(line, sizeof(line),
                                       "[config] %s  req acc=%u gyr=%u Hz | "
                                       "sensor odr-enum acc=0x%02X gyr=0x%02X\r\n",
                                       cfg_ok ? "applied" : "FAILED",
                                       (unsigned)cfg.acc_odr, (unsigned)cfg.gyr_odr,
                                       (unsigned)a_odr, (unsigned)g_odr);
                        uart_stream_print(line);
                        break;
                    }

                    default:
                        break;
                }
            }
        }

        if (armed && (active == TRANSPORT_TCP) && imu_app_fifo_active())
        {
            /* High-rate path: drain the BMI270 FIFO and ship whatever it holds
             * as batched frames. The sensor buffers in hardware, so this stays
             * gap-free even when the task is preempted or briefly paused. */
            bool up = tcp_stream_connected();
            if (up && !tcp_was_up)
            {
                imu_app_fifo_flush();      /* start the session from a clean FIFO */
                send_status_all(src, mag);
            }
            tcp_was_up = up;

            /* Refresh the mag at most ~400 Hz (the BMM350 ceiling): at IMU ODRs
             * above 400 Hz we read once every cfg_odr/400 samples and reuse the
             * cached value for the rest. */
            uint32_t mag_div = (cfg_odr > 400u) ? ((cfg_odr + 399u) / 400u) : 1u;

            int     n;
            uint32_t drained_batches = 0u;
            do
            {
                n = imu_app_read_fifo(batch, TCP_STREAM_BATCH_MAX);
                if (n > 0)
                {
                    dbg_drained += (uint32_t)n;
                    if (up)
                    {
                        for (int i = 0; i < n; i++)
                        {
                            /* Refresh the magnetometer every mag_div samples so
                             * the recorded mag tracks the IMU sample rate up to
                             * the BMM350's 400 Hz, without flooding the I3C bus
                             * with duplicate reads at higher IMU ODRs. */
                            if (mag_cnt == 0u)
                            {
                                if (!mag_app_read(mag_cached))
                                {
                                    mag_cached[0] = mag_cached[1] = mag_cached[2] = 0;
                                }
                            }
                            mag_cnt = (mag_cnt + 1u) % mag_div;
                            batch[i].mag[0] = mag_cached[0];
                            batch[i].mag[1] = mag_cached[1];
                            batch[i].mag[2] = mag_cached[2];
                        }
                        if (!tcp_stream_send_samples(batch, (uint32_t)n))
                        {
                            up = false;    /* client dropped; discard remainder,
                                            * stay armed, resume on reconnect */
                        }
                    }
                    /* when !up the samples are simply discarded (already pulled
                     * out of the HW FIFO so it cannot overflow) */
                }
                drained_batches++;
            } while ((n > 0) &&
                     (drained_batches < TCP_FIFO_MAX_BATCHES_CYCLE));
            /* Was `n == TCP_STREAM_BATCH_MAX`: a full-batch test. But the FIFO
             * extract returns ~29 (not exactly 32) per read, so that condition
             * exited after a SINGLE batch every cycle — leaving the rest of a
             * backed-up FIFO to overflow. Looping while any samples remain (up
             * to the per-cycle cap) clears the backlog each cycle. */

            /* Periodic diagnostic: the REAL drained rate (the host-side rate is
             * blurred by network batching) plus the FIFO state that explains it.
             *   fifo_peak near ~2048 B  -> drain-bound (sensor outruns the read)
             *   fifo_peak small + low Hz -> sensor-bound (ODR not really high)
             *   acc_len >> gyr_len       -> accel/gyro ODR mismatch (FIFO pairs
             *                               at min, so the slow one sets the rate)
             *   odr-enum 0x0C = 1600 Hz; 0x09 = 200 Hz (see imu_app.h). */
            if ((xTaskGetTickCount() - dbg_last) >= pdMS_TO_TICKS(2000u))
            {
                uint16_t avail_max = 0u, acc_len = 0u, gyr_len = 0u;
                uint8_t  a_odr = 0u, g_odr = 0u;
                imu_app_fifo_debug(&avail_max, &acc_len, &gyr_len, &a_odr, &g_odr);
                uint32_t ms = (uint32_t)(xTaskGetTickCount() - dbg_last)
                              * (1000u / configTICK_RATE_HZ);
                if (ms == 0u) { ms = 1u; }
                char line[136];
                (void)snprintf(line, sizeof(line),
                               "[imu-dbg] %lu samp / %lu ms (~%lu Hz)  fifo_peak=%uB"
                               "  acc_len=%u gyr_len=%u  odr-enum acc=0x%02X gyr=0x%02X\r\n",
                               (unsigned long)dbg_drained, (unsigned long)ms,
                               (unsigned long)(dbg_drained * 1000u / ms),
                               avail_max, acc_len, gyr_len,
                               (unsigned)a_odr, (unsigned)g_odr);
                uart_stream_print(line);
                dbg_drained = 0u;
                dbg_last    = xTaskGetTickCount();
            }

            vTaskDelay(pdMS_TO_TICKS(TCP_FIFO_DRAIN_MS));
        }
        else if (armed && (active != TRANSPORT_NONE))
        {
            /* UART, or TCP without the FIFO (synthetic source): one decimated
             * sample per period. */
            uart_imu_sample_t sample;
            if (imu_app_read(&sample))
            {
                if (!mag_app_read(sample.mag))
                {
                    sample.mag[0] = sample.mag[1] = sample.mag[2] = 0;
                }

                if (active == TRANSPORT_UART)
                {
                    uart_stream_send_sample(&sample);
                }
                else if (tcp_stream_connected())
                {
                    /* Ignore the result: a failed send drops the client, but we
                     * stay armed and resume when it reconnects. */
                    (void)tcp_stream_send_sample(&sample);
                }
            }
            if ((xTaskGetTickCount() - last_wake) > (2u * period))
            {
                last_wake = xTaskGetTickCount();
            }
            vTaskDelayUntil(&last_wake, period);
        }
        else
        {
            /* Idle: poll commands at a relaxed cadence and re-advertise the
             * sensor status ~1 Hz so a freshly-attached client sees it. */
            vTaskDelay(pdMS_TO_TICKS(20));
            if (++idle_ticks >= 50u)
            {
                idle_ticks = 0u;
                send_status_all(src, mag);
            }
        }
    }
}

/*******************************************************************************
 * Public API
 ******************************************************************************/
bool imu_stream_create_task(void)
{
    /* Bring up the streaming UART before the scheduler so early boot logs are
     * visible in a terminal / the web UI console. */
    if (!uart_stream_init())
    {
        return false;
    }

    return (pdPASS == xTaskCreate(imu_stream_task, IMU_TASK_NAME,
                                  IMU_TASK_STACK_SIZE, NULL,
                                  IMU_TASK_PRIORITY, NULL));
}
