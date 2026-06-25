/*******************************************************************************
 * File Name        : wifi_config.h
 *
 * Description      : Wi-Fi settings for the CM33 non-secure application.
 *
 *  Two modes, selected by WIFI_USE_STA:
 *
 *  STA (WIFI_USE_STA = 1, default): the board JOINS an existing network — e.g.
 *      your phone's hotspot — as a client. Put the laptop on the SAME network
 *      and it keeps internet access, so Windows stops background-scanning for a
 *      "real" network (that scanning is what caused the ~2 s link stalls that
 *      tore the stream down). The board gets a DHCP address from the AP and
 *      PRINTS it on the UART at boot; point the host bridge at that address:
 *          python host_server/imu_server.py --tcp <printed-ip>
 *      NOTE: some Android hotspots isolate clients (block device-to-device
 *      traffic). If the laptop can't reach the board, flip WIFI_USE_STA to 0
 *      and rebuild to fall back to the board's own SoftAP.
 *
 *  SoftAP (WIFI_USE_STA = 0): the board hosts its own network at a fixed IP;
 *      the laptop joins it directly (no internet on that link).
 *******************************************************************************/

#ifndef WIFI_CONFIG_H
#define WIFI_CONFIG_H

/* 1 = join an existing AP (phone hotspot) as a station; 0 = host a SoftAP. */
#define WIFI_USE_STA             (1)

/* --- STA mode: the network the board JOINS (e.g. your phone's hotspot) --- */
#define WIFI_STA_SSID            "Pixel_4793_nomap"   /* <-- set to your hotspot SSID */
#define WIFI_STA_PASSWORD        "test1467"      /* <-- set to your hotspot pass */
/* The join MUST be given a concrete security type (the WCM forbids passing
 * CY_WCM_SECURITY_UNKNOWN to cy_wcm_connect_ap). Set this to match your phone:
 *   - WPA2-Personal (AES)  -> CY_WCM_SECURITY_WPA2_AES_PSK   (this default; the
 *                             common Android hotspot setting)
 *   - WPA3 / WPA2-WPA3 mix -> CY_WCM_SECURITY_WPA3_WPA2_PSK  (typical iPhone)
 *   - WPA3-only            -> CY_WCM_SECURITY_WPA3_SAE
 * Easiest: set your phone's hotspot to "WPA2-Personal" and leave this as-is.
 * If the join keeps failing (see the UART "[wifi] STA join attempt N failed"),
 * the security type is the first thing to check. */
#define WIFI_STA_SECURITY        CY_WCM_SECURITY_WPA2_AES_PSK
/* The AP's DHCP assigns the address; no static IP (board prints it at boot). */

/* --- SoftAP mode: the network the board ADVERTISES (fallback) --- */
#define WIFI_AP_SSID             "PSOC-IMU"
#define WIFI_AP_PASSWORD         "psoc-imu-1234"   /* WPA2, min. 8 characters */
#define WIFI_AP_SECURITY         CY_WCM_SECURITY_WPA2_AES_PSK
#define WIFI_AP_CHANNEL          (1u)
#define WIFI_AP_BAND             CY_WCM_WIFI_BAND_2_4GHZ

/* --- Fixed addressing for SoftAP mode (board is DHCP server / gateway) --- */
#define WIFI_AP_IP               "192.168.10.1"
#define WIFI_AP_NETMASK          "255.255.255.0"
#define WIFI_AP_GATEWAY          "192.168.10.1"

/* TCP port of the binary streaming server (see tcp_stream.c). Same in both
 * modes — only the board's IP differs (fixed in AP mode, DHCP in STA mode). */
#define TCP_STREAM_PORT          (5000u)

#endif /* WIFI_CONFIG_H */
