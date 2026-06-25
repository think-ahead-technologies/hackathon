/*******************************************************************************
 * File Name        : sensor_i2c.c
 *
 * Description      : See sensor_i2c.h. Implements the shared sensor-bus init and
 *                    blocking register read/write helpers using the PDL SCB
 *                    manual-master API. Extracted from imu_app.c so the BMI270
 *                    and BMM350 drivers can share one bus / one context.
 *
 *******************************************************************************/

#include "sensor_i2c.h"

#include "cybsp.h"
#include "cy_scb_i2c.h"
#include "cy_sysclk.h"

#define I2C_HW          CYBSP_I2C_CONTROLLER_HW     /* SCB0 */
#define I2C_TIMEOUT_MS  (10u)

/* Target bus rate. The BSP configures SCB0 for ~100 kHz, which is fine for
 * register config but caps the BMI270 FIFO drain far below the sensor's output
 * (1600 Hz x 12 B = ~19 KB/s needs a fast bus). The BMI270 supports I2C
 * Fast-mode-plus up to 1 MHz; 900 kHz leaves margin under that ceiling. */
#define I2C_TARGET_HZ   (900000u)
/* Put the SCB source clock in the FM+ usable window (~24 MHz) before asking the
 * PDL to pick SCL phases for the target rate. */
#define I2C_SCB_CLK_HZ  (24000000u)

static cy_stc_scb_i2c_context_t s_i2c_ctx;
static bool s_ready = false;
static bool s_init_done = false;

bool sensor_i2c_init(void)
{
    if (s_init_done)
    {
        return s_ready;
    }
    s_init_done = true;

    if (Cy_SCB_I2C_Init(I2C_HW, &CYBSP_I2C_CONTROLLER_config, &s_i2c_ctx)
            == CY_SCB_I2C_SUCCESS)
    {
        /* Speed up the bus for the high-rate FIFO drain. Done after Init (block
         * still disabled) and before Enable so the new timing applies cleanly.
         * Fully data-driven: read the current divider + clock to recover the
         * source frequency, then choose a divider that lands the SCB clock near
         * I2C_SCB_CLK_HZ regardless of the BSP's source clock. SetDataRate then
         * computes valid SCL phases for the target and CLAMPS to an achievable
         * rate — so a wrong estimate degrades gracefully (slower bus) instead of
         * overspeeding past the BMI270's 1 MHz limit. If anything here misfires,
         * bring-up simply falls back to synthetic (visible on the console). */
        en_clk_dst_t grp = (en_clk_dst_t)CYBSP_I2C_CONTROLLER_CLK_DIV_GRP_NUM;
        uint32_t cur_div = Cy_SysClk_PeriPclkGetDivider(
                               grp, CYBSP_I2C_CONTROLLER_CLK_DIV_HW,
                               CYBSP_I2C_CONTROLLER_CLK_DIV_NUM);
        uint32_t scb_now = Cy_SysClk_PeriPclkGetFrequency(
                               grp, CYBSP_I2C_CONTROLLER_CLK_DIV_HW,
                               CYBSP_I2C_CONTROLLER_CLK_DIV_NUM);
        if (scb_now != 0u)
        {
            uint32_t source  = scb_now * (cur_div + 1u);  /* divider reg = N-1 */
            uint32_t new_div = (source + (I2C_SCB_CLK_HZ / 2u)) / I2C_SCB_CLK_HZ;
            if (new_div == 0u) { new_div = 1u; }
            Cy_SysClk_PeriPclkDisableDivider(
                grp, CYBSP_I2C_CONTROLLER_CLK_DIV_HW,
                CYBSP_I2C_CONTROLLER_CLK_DIV_NUM);
            (void)Cy_SysClk_PeriPclkSetDivider(
                grp, CYBSP_I2C_CONTROLLER_CLK_DIV_HW,
                CYBSP_I2C_CONTROLLER_CLK_DIV_NUM, new_div - 1u);
            Cy_SysClk_PeriPclkEnableDivider(
                grp, CYBSP_I2C_CONTROLLER_CLK_DIV_HW,
                CYBSP_I2C_CONTROLLER_CLK_DIV_NUM);
            uint32_t scb_new = Cy_SysClk_PeriPclkGetFrequency(
                                   grp, CYBSP_I2C_CONTROLLER_CLK_DIV_HW,
                                   CYBSP_I2C_CONTROLLER_CLK_DIV_NUM);
            (void)Cy_SCB_I2C_SetDataRate(I2C_HW, I2C_TARGET_HZ, scb_new);
        }

        Cy_SCB_I2C_Enable(I2C_HW);
        s_ready = true;
    }
    return s_ready;
}

int sensor_i2c_read(uint8_t addr, uint8_t reg, uint8_t *data, uint32_t len)
{
    if (Cy_SCB_I2C_MasterSendStart(I2C_HW, addr, CY_SCB_I2C_WRITE_XFER,
                                   I2C_TIMEOUT_MS, &s_i2c_ctx) != CY_SCB_I2C_SUCCESS)
    {
        goto fail;
    }
    if (Cy_SCB_I2C_MasterWriteByte(I2C_HW, reg, I2C_TIMEOUT_MS, &s_i2c_ctx)
            != CY_SCB_I2C_SUCCESS)
    {
        goto fail;
    }
    if (Cy_SCB_I2C_MasterSendReStart(I2C_HW, addr, CY_SCB_I2C_READ_XFER,
                                     I2C_TIMEOUT_MS, &s_i2c_ctx) != CY_SCB_I2C_SUCCESS)
    {
        goto fail;
    }
    for (uint32_t i = 0u; i < len; i++)
    {
        cy_en_scb_i2c_command_t ack = (i < (len - 1u)) ? CY_SCB_I2C_ACK
                                                       : CY_SCB_I2C_NAK;
        if (Cy_SCB_I2C_MasterReadByte(I2C_HW, ack, &data[i], I2C_TIMEOUT_MS,
                                      &s_i2c_ctx) != CY_SCB_I2C_SUCCESS)
        {
            goto fail;
        }
    }
    (void)Cy_SCB_I2C_MasterSendStop(I2C_HW, I2C_TIMEOUT_MS, &s_i2c_ctx);
    return 0;

fail:
    (void)Cy_SCB_I2C_MasterSendStop(I2C_HW, I2C_TIMEOUT_MS, &s_i2c_ctx);
    return -1;
}

int sensor_i2c_write(uint8_t addr, uint8_t reg, const uint8_t *data, uint32_t len)
{
    if (Cy_SCB_I2C_MasterSendStart(I2C_HW, addr, CY_SCB_I2C_WRITE_XFER,
                                   I2C_TIMEOUT_MS, &s_i2c_ctx) != CY_SCB_I2C_SUCCESS)
    {
        goto fail;
    }
    if (Cy_SCB_I2C_MasterWriteByte(I2C_HW, reg, I2C_TIMEOUT_MS, &s_i2c_ctx)
            != CY_SCB_I2C_SUCCESS)
    {
        goto fail;
    }
    for (uint32_t i = 0u; i < len; i++)
    {
        if (Cy_SCB_I2C_MasterWriteByte(I2C_HW, data[i], I2C_TIMEOUT_MS, &s_i2c_ctx)
                != CY_SCB_I2C_SUCCESS)
        {
            goto fail;
        }
    }
    (void)Cy_SCB_I2C_MasterSendStop(I2C_HW, I2C_TIMEOUT_MS, &s_i2c_ctx);
    return 0;

fail:
    (void)Cy_SCB_I2C_MasterSendStop(I2C_HW, I2C_TIMEOUT_MS, &s_i2c_ctx);
    return -1;
}
