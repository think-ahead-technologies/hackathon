/*******************************************************************************
* File Name        : main.c
*
* Description      : CM55 application: USB (UVC) webcam capture + JPEG
*                    encoding (see camera/camera_stream.c). Encoded frames are
*                    published into the m33_m55_shared SOCMEM region; the CM33
*                    forwards them over the Wi-Fi TCP stream alongside the
*                    IMU data. The sensor streaming itself lives in
*                    proj_cm33_ns (shared core with the Wi-Fi stack).
*
* Related Document : See README.md
*
********************************************************************************
* Copyright 2023-2025, Cypress Semiconductor Corporation (an Infineon company) or
* an affiliate of Cypress Semiconductor Corporation.  All rights reserved.
*
* This software, including source code, documentation and related
* materials ("Software") is owned by Cypress Semiconductor Corporation
* or one of its affiliates ("Cypress") and is protected by and subject to
* worldwide patent protection (United States and foreign),
* United States copyright laws and international treaty provisions.
* Therefore, you may use this Software only as provided in the license
* agreement accompanying the software package from which you
* obtained this Software ("EULA").
* If no EULA applies, Cypress hereby grants you a personal, non-exclusive,
* non-transferable license to copy, modify, and compile the Software
* source code solely for use in connection with Cypress's
* integrated circuit products.  Any reproduction, modification, translation,
* compilation, or representation of this Software except as specified
* above is prohibited without the express written permission of Cypress.
*
* Disclaimer: THIS SOFTWARE IS PROVIDED AS-IS, WITH NO WARRANTY OF ANY KIND,
* EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, NONINFRINGEMENT, IMPLIED
* WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. Cypress
* reserves the right to make changes to the Software without notice. Cypress
* does not assume any liability arising out of the application or use of the
* Software or any product or circuit described in the Software. Cypress does
* not authorize its products for use in any products where a malfunction or
* failure of the Cypress product may reasonably be expected to result in
* significant property damage, injury or death ("High Risk Product"). By
* including Cypress's product in a High Risk Product, the manufacturer
* of such system or application assumes all risk of such use and in doing
* so agrees to indemnify Cypress against all liability.
*******************************************************************************/

/*******************************************************************************
* Header Files
*******************************************************************************/
#include "cybsp.h"
#include "cy_time.h"

#include "FreeRTOS.h"
#include "task.h"
#include "cyabs_rtos.h"
#include "cyabs_rtos_impl.h"

#include "camera_stream.h"

/*******************************************************************************
 * Macros
 ******************************************************************************/
#define LPTIMER_1_WAIT_TIME_USEC    (62U)
#define APP_LPTIMER_INTERRUPT_PRIORITY (1U)

/*******************************************************************************
 * Global Variables
 ******************************************************************************/
static mtb_hal_lptimer_t lptimer_obj;
static mtb_hal_rtc_t     rtc_obj;

/*******************************************************************************
* Function Name: handle_app_error
*******************************************************************************/
static void handle_app_error(void)
{
    __disable_irq();
    CY_ASSERT(0);
    while (true) { }
}

/*******************************************************************************
* Function Name: setup_clib_support
*******************************************************************************/
static void setup_clib_support(void)
{
    /* RTC initialization is done in the CM33 non-secure project. */
    mtb_clib_support_init(&rtc_obj);
}

/*******************************************************************************
* Function Name: lptimer_interrupt_handler
*******************************************************************************/
static void lptimer_interrupt_handler(void)
{
    mtb_hal_lptimer_process_interrupt(&lptimer_obj);
}

/*******************************************************************************
* Function Name: setup_tickless_idle_timer
*******************************************************************************/
static void setup_tickless_idle_timer(void)
{
    cy_stc_sysint_t lptimer_intr_cfg =
    {
        .intrSrc      = CYBSP_CM55_LPTIMER_1_IRQ,
        .intrPriority = APP_LPTIMER_INTERRUPT_PRIORITY
    };

    cy_en_sysint_status_t interrupt_init_status =
        Cy_SysInt_Init(&lptimer_intr_cfg, lptimer_interrupt_handler);
    if (CY_SYSINT_SUCCESS != interrupt_init_status)
    {
        handle_app_error();
    }

    NVIC_EnableIRQ(lptimer_intr_cfg.intrSrc);

    cy_en_mcwdt_status_t mcwdt_init_status =
        Cy_MCWDT_Init(CYBSP_CM55_LPTIMER_1_HW, &CYBSP_CM55_LPTIMER_1_config);
    if (CY_MCWDT_SUCCESS != mcwdt_init_status)
    {
        handle_app_error();
    }

    Cy_MCWDT_Enable(CYBSP_CM55_LPTIMER_1_HW, CY_MCWDT_CTR_Msk,
                    LPTIMER_1_WAIT_TIME_USEC);

    cy_rslt_t result =
        mtb_hal_lptimer_setup(&lptimer_obj, &CYBSP_CM55_LPTIMER_1_hal_config);
    if (CY_RSLT_SUCCESS != result)
    {
        handle_app_error();
    }

    cyabs_rtos_set_lptimer(&lptimer_obj);
}

/*******************************************************************************
* Function Name: main
*******************************************************************************/
int main(void)
{
    cy_rslt_t result = cybsp_init();
    if (CY_RSLT_SUCCESS != result)
    {
        handle_app_error();
    }

    setup_clib_support();
    setup_tickless_idle_timer();

    __enable_irq();

    /* USB webcam capture + JPEG encoding; frames go to the CM33 via the
     * shared SOCMEM region for Wi-Fi streaming. */
    if (!camera_stream_create_tasks())
    {
        handle_app_error();
    }

    vTaskStartScheduler();

    /* Should never reach here. */
    handle_app_error();
    return 0;
}

/* [] END OF FILE */
