/*******************************************************************************
* File Name: main.c
*
* Description: CM33 non-secure entry point for the edge-device firmware. Brings up
* the BSP, PSA crypto, debug UART and the CM55 core, then launches the vendor-neutral
* device orchestration loop (firmware/src/device_main.c) as a FreeRTOS task. That task
* brings Wi-Fi up, connects to NATS over TLS, publishes Contract B inference results,
* and services Contract C model deploys.
*
* Adapted from the PSOC_Edge_Wi-Fi_HTTPS_Client code example.
*******************************************************************************/

#include "cybsp.h"
#include "FreeRTOS.h"
#include "task.h"
#include "cyabs_rtos.h"
#include "cyabs_rtos_impl.h"
#include "cy_time.h"
#include "psa/crypto.h"
#include "retarget_io_init.h"
#include "platform_hal.h"   /* hal_flash_init — QSPI bring-up for the model slots */

/*******************************************************************************
* Macros
*******************************************************************************/
#define DEVICE_TASK_NAME            ("device")
#define DEVICE_TASK_STACK_SIZE      (16U * 1024U)
#define DEVICE_TASK_PRIORITY        (1U)

#define CM55_BOOT_WAIT_TIME_US      (10U)
#define LPTIMER_0_WAIT_TIME_USEC    (62U)
#define APP_LPTIMER_INTERRUPT_PRIORITY (1U)

/* App boot address for the CM55 project. */
#define CM55_APP_BOOT_ADDR          (CYMEM_CM33_0_m55_nvm_START + CYBSP_MCUBOOT_HEADER_SIZE)

/*******************************************************************************
* Global Variables
*******************************************************************************/
static mtb_hal_lptimer_t lptimer_obj;
static mtb_hal_rtc_t rtc_obj;
static TaskHandle_t device_task_handle;

/* The vendor-neutral orchestration loop (firmware/src/device_main.c). Only returns
 * on a fatal init failure; its steady state is the publish/deploy loop. */
extern int device_main(void);

/*******************************************************************************
* Function Definitions
*******************************************************************************/
static void lptimer_interrupt_handler(void)
{
    mtb_hal_lptimer_process_interrupt(&lptimer_obj);
}

static void setup_tickless_idle_timer(void)
{
    cy_stc_sysint_t lptimer_intr_cfg =
    {
        .intrSrc = CYBSP_CM33_LPTIMER_0_IRQ,
        .intrPriority = APP_LPTIMER_INTERRUPT_PRIORITY
    };

    if (CY_SYSINT_SUCCESS != Cy_SysInt_Init(&lptimer_intr_cfg, lptimer_interrupt_handler))
    {
        handle_app_error();
    }
    NVIC_EnableIRQ(lptimer_intr_cfg.intrSrc);

    if (CY_MCWDT_SUCCESS != Cy_MCWDT_Init(CYBSP_CM33_LPTIMER_0_HW, &CYBSP_CM33_LPTIMER_0_config))
    {
        handle_app_error();
    }
    Cy_MCWDT_Enable(CYBSP_CM33_LPTIMER_0_HW, CY_MCWDT_CTR_Msk, LPTIMER_0_WAIT_TIME_USEC);

    if (CY_RSLT_SUCCESS != mtb_hal_lptimer_setup(&lptimer_obj, &CYBSP_CM33_LPTIMER_0_hal_config))
    {
        handle_app_error();
    }
    cyabs_rtos_set_lptimer(&lptimer_obj);
}

static void setup_clib_support(void)
{
    Cy_RTC_Init(&CYBSP_RTC_config);
    Cy_RTC_SetDateAndTime(&CYBSP_RTC_config);
    mtb_clib_support_init(&rtc_obj);
}

/* FreeRTOS task wrapper around the device orchestration loop. */
static void device_task(void *arg)
{
    (void)arg;
    (void)device_main();   /* returns only on fatal init failure */
    printf("[device] orchestration loop exited (fatal init failure); halting task\n");
    vTaskSuspend(NULL);
    for (;;) { }
}

int main(void)
{
    cy_rslt_t result;

    /* Initialize the Board Support Package. */
    result = cybsp_init();
    if (CY_RSLT_SUCCESS != result)
    {
        handle_app_error();
    }

    setup_clib_support();
    setup_tickless_idle_timer();
    init_retarget_io();

    /* PSA Crypto backs the secure-enclave HAL (signature verify, sha256, nkey sign). */
    if (PSA_SUCCESS != psa_crypto_init())
    {
        handle_app_error();
    }

    /* Bring up the QSPI serial flash that holds the A/B model slots + metadata, and enable XIP so a
     * deployed model is memory-mapped for the NPU. No-op on the connectivity (HAL_FLASH_STUB) build. */
    if (!hal_flash_init())
    {
        handle_app_error();
    }

    printf("\x1b[2J\x1b[;H");
    printf("===============================================================\n");
    printf("PSOC Edge Edge-Device Firmware: Wi-Fi + NATS (connectivity build)\n");
    printf("===============================================================\n\n");

    /* Boot the CM55 core (idle in this build). */
    Cy_SysEnableCM55(MXCM55, CM55_APP_BOOT_ADDR, CM55_BOOT_WAIT_TIME_US);

    __enable_irq();

    result = xTaskCreate(device_task, DEVICE_TASK_NAME, DEVICE_TASK_STACK_SIZE,
                         NULL, DEVICE_TASK_PRIORITY, &device_task_handle);
    if (pdPASS == result)
    {
        vTaskStartScheduler();
    }

    /* Should never get here. */
    handle_app_error();
    return 0;
}

/* [] END OF FILE */
