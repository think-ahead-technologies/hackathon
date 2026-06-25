/*******************************************************************************
 * File Name        : wifi_app.c
 *
 * Description      : See wifi_app.h. Brings up the Wi-Fi SoftAP (the board
 *                    hosts its own network at a fixed IP) and then starts the
 *                    TCP streaming server (tcp_stream.c) on it.
 *
 *  A dedicated LED task renders the current Wi-Fi state on User LED1 so we get
 *  continuous feedback even while the (blocking) WCM calls run on the worker
 *  task. Patterns (User LED1):
 *      INIT        - heartbeat: one short blink per second (scheduler alive,
 *                    worker not yet in WCM)
 *      STARTING    - steady fast blink (~3 Hz): inside cy_wcm_init/start_ap
 *      AP_UP       - double-blink then pause (distinct from a solid power LED)
 *      FAILED      - very fast blink (~8 Hz)
 *******************************************************************************/

#include "wifi_app.h"
#include "wifi_config.h"
#include "tcp_stream.h"
#include "uart_stream.h"

#include "cybsp.h"
#include "cy_wcm.h"
#include "cy_secure_sockets.h"
#include "cy_sd_host.h"

#include "FreeRTOS.h"
#include "task.h"

#include <string.h>
#include <stdio.h>

#define WIFI_TASK_NAME        ("WiFi SoftAP Task")
#define WIFI_TASK_STACK_SIZE  (configMINIMAL_STACK_SIZE * 12)
#define WIFI_TASK_PRIORITY    (configMAX_PRIORITIES - 3)

#define LED_TASK_NAME         ("WiFi LED Task")
#define LED_TASK_STACK_SIZE   (configMINIMAL_STACK_SIZE)
#define LED_TASK_PRIORITY     (configMAX_PRIORITIES - 1)

/* SDIO / host-wake bring-up parameters (match the Infineon PSOC Edge Wi-Fi
 * code examples, e.g. mtb-example-psoc-edge-wifi-web-server). */
#define APP_SDIO_INTERRUPT_PRIORITY       (7U)
#define APP_HOST_WAKE_INTERRUPT_PRIORITY  (2U)
#define APP_SDIO_FREQUENCY_HZ             (25000000U)
#define SDHC_SDIO_64BYTES_BLOCK           (64U)
#define SYSPM_SKIP_MODE                   (0U)
#define SYSPM_CALLBACK_ORDER              (10U)

/*******************************************************************************
 * State
 ******************************************************************************/
typedef enum
{
    WST_INIT = 0,
    WST_STARTING,
    WST_AP_UP,
    WST_FAILED
} wifi_state_t;

static volatile wifi_state_t s_state       = WST_INIT;
static volatile bool         s_ap_up       = false;
static volatile uint32_t     s_ipv4        = 0u;
static volatile cy_rslt_t    s_last_result = 0;

/* The WCM config must outlive cy_wcm_init: WHD keeps using the SDIO instance
 * and the wl/host-wake pin objects embedded in it. */
static cy_wcm_config_t           s_wcm_config;
static mtb_hal_sdio_t            s_sdio_instance;
static cy_stc_sd_host_context_t  s_sdhc_host_context;

#if (CY_CFG_PWR_SYS_IDLE_MODE == CY_CFG_PWR_MODE_DEEPSLEEP)
static cy_stc_syspm_callback_params_t s_sdcard_ds_params =
{
    .context = &s_sdhc_host_context,
    .base    = CYBSP_WIFI_SDIO_HW
};

static cy_stc_syspm_callback_t s_sdhc_deepsleep_cb =
{
    .callback       = Cy_SD_Host_DeepSleepCallback,
    .skipMode       = SYSPM_SKIP_MODE,
    .type           = CY_SYSPM_DEEPSLEEP,
    .callbackParams = &s_sdcard_ds_params,
    .prevItm        = NULL,
    .nextItm        = NULL,
    .order          = SYSPM_CALLBACK_ORDER
};
#endif /* (CY_CFG_PWR_SYS_IDLE_MODE == CY_CFG_PWR_MODE_DEEPSLEEP) */

/*******************************************************************************
 * SDIO + host-wake interrupt plumbing (canonical PSOC Edge Wi-Fi bring-up)
 ******************************************************************************/
static void sdio_interrupt_handler(void)
{
    mtb_hal_sdio_process_interrupt(&s_sdio_instance);
}

static void host_wake_interrupt_handler(void)
{
    mtb_hal_gpio_process_interrupt(&s_wcm_config.wifi_host_wake_pin);
}

/*******************************************************************************
 * Brings up the SDIO host that connects to the CYW55513 and prepares the
 * WL_REG_ON / HOST_WAKE pin objects inside s_wcm_config. Must run before
 * cy_wcm_init(). Returns true on success.
 ******************************************************************************/
static bool app_sdio_init(void)
{
    cy_stc_sysint_t sdio_intr_cfg =
    {
        .intrSrc      = CYBSP_WIFI_SDIO_IRQ,
        .intrPriority = APP_SDIO_INTERRUPT_PRIORITY
    };

    cy_stc_sysint_t host_wake_intr_cfg =
    {
        .intrSrc      = CYBSP_WIFI_HOST_WAKE_IRQ,
        .intrPriority = APP_HOST_WAKE_INTERRUPT_PRIORITY
    };

    if (CY_SYSINT_SUCCESS != Cy_SysInt_Init(&sdio_intr_cfg,
                                            sdio_interrupt_handler))
    {
        return false;
    }
    NVIC_EnableIRQ(CYBSP_WIFI_SDIO_IRQ);

    if (CY_RSLT_SUCCESS != mtb_hal_sdio_setup(&s_sdio_instance,
                                              &CYBSP_WIFI_SDIO_sdio_hal_config,
                                              NULL, &s_sdhc_host_context))
    {
        return false;
    }

    Cy_SD_Host_Enable(CYBSP_WIFI_SDIO_HW);
    Cy_SD_Host_Init(CYBSP_WIFI_SDIO_HW,
                    CYBSP_WIFI_SDIO_sdio_hal_config.host_config,
                    &s_sdhc_host_context);
    Cy_SD_Host_SetHostBusWidth(CYBSP_WIFI_SDIO_HW, CY_SD_HOST_BUS_WIDTH_4_BIT);

    mtb_hal_sdio_cfg_t sdio_hal_cfg =
    {
        .frequencyhal_hz = APP_SDIO_FREQUENCY_HZ,
        .block_size      = SDHC_SDIO_64BYTES_BLOCK
    };
    mtb_hal_sdio_configure(&s_sdio_instance, &sdio_hal_cfg);

#if (CY_CFG_PWR_SYS_IDLE_MODE == CY_CFG_PWR_MODE_DEEPSLEEP)
    /* Keep the SDIO host alive across tickless-idle DeepSleep entries. */
    Cy_SysPm_RegisterCallback(&s_sdhc_deepsleep_cb);
#endif

    mtb_hal_gpio_setup(&s_wcm_config.wifi_wl_pin,
                       CYBSP_WIFI_WL_REG_ON_PORT_NUM, CYBSP_WIFI_WL_REG_ON_PIN);
    mtb_hal_gpio_setup(&s_wcm_config.wifi_host_wake_pin,
                       CYBSP_WIFI_HOST_WAKE_PORT_NUM, CYBSP_WIFI_HOST_WAKE_PIN);

    if (CY_SYSINT_SUCCESS != Cy_SysInt_Init(&host_wake_intr_cfg,
                                            host_wake_interrupt_handler))
    {
        return false;
    }
    NVIC_EnableIRQ(CYBSP_WIFI_HOST_WAKE_IRQ);

    return true;
}

/*******************************************************************************
 * LED helpers (User LED1)
 ******************************************************************************/
static inline void led(bool on)
{
    Cy_GPIO_Write(CYBSP_USER_LED1_PORT, CYBSP_USER_LED1_PIN,
                  on ? CYBSP_LED_STATE_ON : CYBSP_LED_STATE_OFF);
}

static void led_status_task(void *arg)
{
    CY_UNUSED_PARAMETER(arg);
    for (;;)
    {
        switch (s_state)
        {
            case WST_INIT:        /* heartbeat */
                led(true);  vTaskDelay(pdMS_TO_TICKS(80));
                led(false); vTaskDelay(pdMS_TO_TICKS(920));
                break;

            case WST_STARTING:    /* steady fast blink */
                led(true);  vTaskDelay(pdMS_TO_TICKS(160));
                led(false); vTaskDelay(pdMS_TO_TICKS(160));
                break;

            case WST_AP_UP:       /* double-blink then pause */
                led(true);  vTaskDelay(pdMS_TO_TICKS(110));
                led(false); vTaskDelay(pdMS_TO_TICKS(110));
                led(true);  vTaskDelay(pdMS_TO_TICKS(110));
                led(false); vTaskDelay(pdMS_TO_TICKS(700));
                break;

            case WST_FAILED:      /* very fast blink */
            default:
                led(true);  vTaskDelay(pdMS_TO_TICKS(60));
                led(false); vTaskDelay(pdMS_TO_TICKS(60));
                break;
        }
    }
}

/*******************************************************************************
 * Worker task: SoftAP bring-up, then the TCP streaming server.
 ******************************************************************************/
static void wifi_task(void *arg)
{
    CY_UNUSED_PARAMETER(arg);

    s_state = WST_STARTING;

    /* SDIO host + WL_REG_ON / HOST_WAKE pin objects must exist before WCM
     * powers the CYW55513 (a zeroed config hard-faults inside WHD). */
    if (!app_sdio_init())
    {
        s_state = WST_FAILED;
        uart_stream_print("[wifi] SDIO bring-up FAILED\r\n");
        vTaskDelete(NULL);
        return;
    }

#if WIFI_USE_STA
    s_wcm_config.interface               = CY_WCM_INTERFACE_TYPE_STA;
#else
    s_wcm_config.interface               = CY_WCM_INTERFACE_TYPE_AP;
#endif
    s_wcm_config.wifi_interface_instance = &s_sdio_instance;

    s_last_result = cy_wcm_init(&s_wcm_config);
    if (s_last_result != CY_RSLT_SUCCESS)
    {
        s_state = WST_FAILED;
        uart_stream_print("[wifi] cy_wcm_init FAILED\r\n");
        vTaskDelete(NULL);
        return;
    }

#if WIFI_USE_STA
    /* Station mode: join an existing AP (e.g. the phone hotspot) and take a
     * DHCP address. cy_wcm_connect_ap is blocking; retry until the AP is in
     * range / powered, with LED feedback, instead of giving up on one miss. */
    cy_wcm_connect_params_t connect_params;
    cy_wcm_ip_address_t     ip_addr;
    memset(&connect_params, 0, sizeof(connect_params));
    memcpy(connect_params.ap_credentials.SSID, WIFI_STA_SSID,
           strlen(WIFI_STA_SSID));
    memcpy(connect_params.ap_credentials.password, WIFI_STA_PASSWORD,
           strlen(WIFI_STA_PASSWORD));
    connect_params.ap_credentials.security = WIFI_STA_SECURITY;

    {
        char msg[96];
        (void)snprintf(msg, sizeof(msg),
                       "[wifi] STA: joining '%s' ...\r\n", WIFI_STA_SSID);
        uart_stream_print(msg);
    }

    s_last_result = ~CY_RSLT_SUCCESS;
    for (uint32_t attempt = 1u; s_last_result != CY_RSLT_SUCCESS; attempt++)
    {
        memset(&ip_addr, 0, sizeof(ip_addr));
        s_last_result = cy_wcm_connect_ap(&connect_params, &ip_addr);
        if (s_last_result != CY_RSLT_SUCCESS)
        {
            char msg[96];
            (void)snprintf(msg, sizeof(msg),
                           "[wifi] STA join attempt %lu failed (0x%08lX), "
                           "retrying...\r\n",
                           (unsigned long)attempt, (unsigned long)s_last_result);
            uart_stream_print(msg);
            vTaskDelay(pdMS_TO_TICKS(3000));
        }
    }

    s_ipv4 = ip_addr.ip.v4;
#else
    cy_wcm_ap_config_t ap_config;
    memset(&ap_config, 0, sizeof(ap_config));
    memcpy(ap_config.ap_credentials.SSID, WIFI_AP_SSID, strlen(WIFI_AP_SSID));
    memcpy(ap_config.ap_credentials.password, WIFI_AP_PASSWORD,
           strlen(WIFI_AP_PASSWORD));
    ap_config.ap_credentials.security = WIFI_AP_SECURITY;
    ap_config.band                    = WIFI_AP_BAND;
    ap_config.channel                 = WIFI_AP_CHANNEL;

    s_last_result = cy_wcm_set_ap_ip_setting(&ap_config.ip_settings,
                                             WIFI_AP_IP, WIFI_AP_NETMASK,
                                             WIFI_AP_GATEWAY, CY_WCM_IP_VER_V4);
    if (s_last_result == CY_RSLT_SUCCESS)
    {
        s_last_result = cy_wcm_start_ap(&ap_config);
    }

    if (s_last_result != CY_RSLT_SUCCESS)
    {
        s_state = WST_FAILED;
        uart_stream_print("[wifi] SoftAP start FAILED\r\n");
        vTaskDelete(NULL);
        return;
    }

    s_ipv4 = ap_config.ip_settings.ip_address.ip.v4;
#endif
    s_ap_up = true;

    /* Keep the CM33 out of DeepSleep while the AP is serving. In tickless
     * idle the core otherwise drops into DeepSleep between events, and
     * inbound WLAN->host traffic stalls until something else wakes it —
     * the peer's TCP gives up after ~10 s of unanswered retransmissions
     * and tears the connection down. (CPU Sleep still happens; this is a
     * mains/power-bank-powered streaming device, not a coin-cell node.) */
    mtb_hal_syspm_lock_deepsleep();

    /* Network is up: start the TCP streaming server on it. */
    bool tcp_ok = (cy_socket_init() == CY_RSLT_SUCCESS)
               && tcp_stream_init((uint16_t)TCP_STREAM_PORT);

    s_state = tcp_ok ? WST_AP_UP : WST_FAILED;

    /* Format the IPv4 address (network byte order: first octet in the low byte,
     * matching the Infineon WCM examples). */
    char ipstr[16];
    (void)snprintf(ipstr, sizeof(ipstr), "%u.%u.%u.%u",
                   (unsigned)(s_ipv4 & 0xFFu),
                   (unsigned)((s_ipv4 >> 8) & 0xFFu),
                   (unsigned)((s_ipv4 >> 16) & 0xFFu),
                   (unsigned)((s_ipv4 >> 24) & 0xFFu));

    char msg[224];
#if WIFI_USE_STA
    (void)snprintf(msg, sizeof(msg),
                   "[wifi] STA joined '%s'  IP %s  TCP port %u (%s)\r\n"
                   "[wifi] Laptop: join the SAME network, then run "
                   "imu_server.py --tcp %s:%u\r\n",
                   WIFI_STA_SSID, ipstr, (unsigned)TCP_STREAM_PORT,
                   tcp_ok ? "listening" : "TCP START FAILED",
                   ipstr, (unsigned)TCP_STREAM_PORT);
#else
    (void)snprintf(msg, sizeof(msg),
                   "[wifi] SoftAP up: SSID '%s'  IP %s  TCP port %u (%s)\r\n"
                   "[wifi] Laptop: join '%s', then run "
                   "imu_server.py --tcp %s:%u\r\n",
                   WIFI_AP_SSID, ipstr, (unsigned)TCP_STREAM_PORT,
                   tcp_ok ? "listening" : "TCP START FAILED",
                   WIFI_AP_SSID, ipstr, (unsigned)TCP_STREAM_PORT);
#endif
    uart_stream_print(msg);

    vTaskDelete(NULL);
}

/*******************************************************************************
 * Public API
 ******************************************************************************/
void wifi_app_create_task(void)
{
    (void)xTaskCreate(led_status_task, LED_TASK_NAME, LED_TASK_STACK_SIZE, NULL,
                      LED_TASK_PRIORITY, NULL);
    (void)xTaskCreate(wifi_task, WIFI_TASK_NAME, WIFI_TASK_STACK_SIZE, NULL,
                      WIFI_TASK_PRIORITY, NULL);
}

bool wifi_app_is_connected(void)
{
    return s_ap_up;
}

uint32_t wifi_app_ipv4(void)
{
    return s_ipv4;
}
