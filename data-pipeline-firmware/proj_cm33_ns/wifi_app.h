/*******************************************************************************
 * File Name        : wifi_app.h
 *
 * Description      : Wi-Fi SoftAP bring-up for the CM33 non-secure core. The
 *                    board hosts the network configured in wifi_config.h
 *                    (Wi-Fi Connection Manager, CYW55513 over SDIO) at a fixed
 *                    IP, then starts the TCP streaming server (tcp_stream.c).
 *                    Progress is reported on User LED1; see wifi_app.c for the
 *                    blink patterns.
 *******************************************************************************/

#ifndef WIFI_APP_H
#define WIFI_APP_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Creates the FreeRTOS tasks that start the SoftAP + TCP streaming server. */
void wifi_app_create_task(void);

/* True once the SoftAP is up. */
bool wifi_app_is_connected(void);

/* Returns the AP's IPv4 address (0 until the AP is up). */
uint32_t wifi_app_ipv4(void);

#ifdef __cplusplus
}
#endif

#endif /* WIFI_APP_H */
