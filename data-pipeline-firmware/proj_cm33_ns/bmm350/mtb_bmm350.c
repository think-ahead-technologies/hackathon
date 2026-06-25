/******************************************************************************
 * \file mtb_bmm350.c
 *
 * \brief
 *     This file contains the functions for interacting with the
 *     BMM350 magnetic sensor.
 *
 ********************************************************************************
 * \copyright
 * Copyright (c) 2025 Infineon Technologies AG
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *******************************************************************************/

#include "mtb_bmm350.h"

#include <stdlib.h> // For malloc
#include <string.h> // For memcpy

/******************************************************************************
* Macros
******************************************************************************/
#define _SOFT_RESET_DELAY_US       300
#define _I3C_CMD_LENGTH            (1u)

/******************************************************************************
* Global variables
******************************************************************************/
static cy_stc_i3c_context_t* _bmm350_i3c_context = NULL;
static I3C_CORE_Type* _bmm350_i3c_hw = NULL;
static cy_stc_i3c_device_t* _bmm350_i3c_device = NULL;

/* DEBUG: raw bytes read straight off the wire from reg 0x00 during bring-up,
 * with no SensorAPI dummy-byte interpretation. Lets the app report exactly
 * what the BMM350 returns (e.g. 0x33 at offset 0 = no I3C dummy bytes;
 * 0x33 at offset 2 = 2 dummy bytes; all-zero = read returns no data). */
static uint8_t _bmm350_probe[6] = { 0u, 0u, 0u, 0u, 0u, 0u };

const uint8_t* mtb_bmm350_debug_probe(void)
{
    return _bmm350_probe;
}

/*****************************************************************************
* Local function Prototypes
*****************************************************************************/

static BMM350_INTF_RET_TYPE _bmm350_i3c_read(uint8_t reg_addr, uint8_t* reg_data, uint32_t length,
                                             void* intf_ptr);

static BMM350_INTF_RET_TYPE _bmm350_i3c_write(uint8_t reg_addr, const uint8_t* reg_data,
                                              uint32_t length,
                                              void* intf_ptr);

static void _bmm350_delay_us(uint32_t period, void* intf_ptr);


/*****************************************************************************
* Function name: _bmm350_wait_xfer
*****************************************************************************
* Summary:
*  Waits for an in-flight I3C controller read/write to finish. The cy_i3c
*  read/write transfers complete via the I3C interrupt handler (it clears the
*  BUSY bit). On this kit the magnetometer runs on the CM55 core, where that
*  peripheral interrupt is not wired into the NVIC, so we PUMP the handler by
*  hand here instead of spinning on a status bit that the ISR would otherwise
*  set. The loop is bounded so an unresponsive device fails gracefully rather
*  than hanging the caller forever (the original upstream loop was unbounded).
*
* Parameters:
*  result   The status returned by the transfer-start call, passed through on
*           successful completion.
*
* Return:
*  BMM350_INTF_RET_SUCCESS-style result, or -1 on transfer halt / timeout.
*****************************************************************************/
#define _BMM350_XFER_GUARD   (1000000UL)

static BMM350_INTF_RET_TYPE _bmm350_wait_xfer(cy_rslt_t result)
{
    uint32_t guard = 0UL;
    uint32_t status;

    do
    {
        /* Service the I3C handler only when an *enabled* interrupt is actually
         * pending - exactly the condition under which the NVIC would invoke it.
         * Calling it unconditionally consumes the response queue before the data
         * has landed, which corrupts reads (chip-id came back wrong -> the
         * sensor looked "not found"). */
        if (0U != (Cy_I3C_GetInterruptStatus(_bmm350_i3c_hw) &
                   Cy_I3C_GetInterruptStatusMask(_bmm350_i3c_hw)))
        {
            Cy_I3C_Interrupt(_bmm350_i3c_hw, _bmm350_i3c_context);
        }
        status = Cy_I3C_GetBusStatus(_bmm350_i3c_hw, _bmm350_i3c_context);

        if (0U != (status & CY_I3C_CONTROLLER_HALT_STATE))
        {
            Cy_I3C_Resume(_bmm350_i3c_hw, _bmm350_i3c_context);
            return (BMM350_INTF_RET_TYPE)-1;
        }
    } while ((0U != (status & CY_I3C_CONTROLLER_BUSY)) && (++guard < _BMM350_XFER_GUARD));

    if (0U != (status & CY_I3C_CONTROLLER_BUSY))
    {
        return (BMM350_INTF_RET_TYPE)-1;   /* timed out */
    }
    return (BMM350_INTF_RET_TYPE)result;
}


/******************************************************************************
* mtb_bmm350_init_i3c
******************************************************************************/
cy_rslt_t mtb_bmm350_init_i3c(mtb_bmm350_t* dev, I3C_CORE_Type* i3c_hw,
                              cy_stc_i3c_context_t* i3c_context, cy_stc_i3c_device_t* i3c_device)
{
    cy_rslt_t rslt;
    uint8_t int_ctrl, err_reg_data = 0, soft_reset = 0;
    static struct bmm350_pmu_cmd_status_0 pmu_cmd_stat_0;
    static cy_stc_i3c_ccc_cmd_t cccCmd;
    static cy_stc_i3c_ccc_payload_t cccPayload;

    CY_ASSERT(NULL != i3c_context);
    CY_ASSERT(NULL != dev);

    _bmm350_i3c_context = i3c_context;
    _bmm350_i3c_hw = i3c_hw;
    _bmm350_i3c_device = i3c_device;

    /* Configure I3C CCC settings */
    cccCmd.address = CY_I3C_BROADCAST_ADDR;
    cccCmd.cmd = CY_I3C_CCC_RSTDAA(true);
    cccCmd.data = &cccPayload;
    cccCmd.data->data = NULL;
    cccCmd.data->len = 0U;

    /* Configure BMM350 sensor settings */
    dev->sensor.intf_ptr = NULL;
    dev->sensor.read = _bmm350_i3c_read;
    dev->sensor.write = _bmm350_i3c_write;
    dev->sensor.delay_us = _bmm350_delay_us;
    dev->sensor.mraw_override = NULL;

    rslt = Cy_I3C_SendCCCCmd(_bmm350_i3c_hw, &cccCmd, _bmm350_i3c_context);
    if (CY_RSLT_SUCCESS != rslt)
    {
        return rslt;
    }

    rslt =
        Cy_I3C_ControllerAttachI3CDevice(_bmm350_i3c_hw, _bmm350_i3c_device, _bmm350_i3c_context);
    if (CY_RSLT_SUCCESS != rslt)
    {
        return rslt;
    }

    rslt = Cy_I3C_ControllerStartEntDaa(_bmm350_i3c_hw, _bmm350_i3c_context);
    if (CY_RSLT_SUCCESS != rslt)
    {
        return rslt;
    }

    soft_reset = BMM350_CMD_SOFTRESET;

    rslt = bmm350_set_regs(BMM350_REG_CMD, &soft_reset, _I3C_CMD_LENGTH, &(dev->sensor));
    if (BMM350_OK != rslt)
    {
        return rslt;
    }
    else
    {
        rslt = bmm350_delay_us(BMM350_SOFT_RESET_DELAY, &(dev->sensor));
    }

    rslt = Cy_I3C_SendCCCCmd(_bmm350_i3c_hw, &cccCmd, _bmm350_i3c_context);
    if (CY_RSLT_SUCCESS != rslt)
    {
        return rslt;
    }

    rslt =
        Cy_I3C_ControllerAttachI3CDevice(_bmm350_i3c_hw, _bmm350_i3c_device, _bmm350_i3c_context);
    if (CY_RSLT_SUCCESS != rslt)
    {
        return rslt;
    }

    rslt = Cy_I3C_ControllerStartEntDaa(_bmm350_i3c_hw, _bmm350_i3c_context);
    if (CY_RSLT_SUCCESS != rslt)
    {
        return rslt;
    }

    /* DEBUG: raw-dump regs 0x00.. straight off the wire (bypasses the SensorAPI
     * dummy-byte handling) so the app can report what the device really sends. */
    (void)_bmm350_i3c_read(BMM350_REG_CHIP_ID, _bmm350_probe, sizeof(_bmm350_probe), NULL);

    rslt = bmm350_init(&(dev->sensor));
    if (BMM350_OK != rslt)
    {
        return rslt;
    }

    rslt = bmm350_get_pmu_cmd_status_0(&pmu_cmd_stat_0, &(dev->sensor));
    if (BMM350_OK != rslt)
    {
        return rslt;
    }

    /* Get error data */
    rslt = bmm350_get_regs(BMM350_REG_ERR_REG, &err_reg_data, _I3C_CMD_LENGTH, &(dev->sensor));
    if (BMM350_OK != rslt)
    {
        return rslt;
    }

    /* Configure interrupt settings */
    rslt = bmm350_configure_interrupt(BMM350_PULSED,
                                      BMM350_ACTIVE_HIGH,
                                      BMM350_INTR_PUSH_PULL,
                                      BMM350_UNMAP_FROM_PIN,
                                      &(dev->sensor));
    if (BMM350_OK != rslt)
    {
        return rslt;
    }

    /* Enable data ready interrupt */
    rslt = bmm350_enable_interrupt(BMM350_ENABLE_INTERRUPT, &(dev->sensor));
    if (BMM350_OK != rslt)
    {
        return rslt;
    }

    /* Get interrupt settings */
    rslt = bmm350_get_regs(BMM350_REG_INT_CTRL, &int_ctrl, _I3C_CMD_LENGTH, &(dev->sensor));
    if (BMM350_OK != rslt)
    {
        return rslt;
    }

    /* Set ODR and performance */
    rslt = bmm350_set_odr_performance(BMM350_DATA_RATE_25HZ, BMM350_AVERAGING_8, &(dev->sensor));
    if (BMM350_OK != rslt)
    {
        return rslt;
    }

    /* Enable all axis */
    rslt = bmm350_enable_axes(BMM350_X_EN, BMM350_Y_EN, BMM350_Z_EN, &(dev->sensor));
    if (BMM350_OK == rslt)
    {
        rslt = bmm350_set_powermode(BMM350_NORMAL_MODE, &(dev->sensor));
    }

    return (BMM350_OK == rslt)
            ? CY_RSLT_SUCCESS
            : rslt;
}


/******************************************************************************
* mtb_bmm350_read
******************************************************************************/
cy_rslt_t mtb_bmm350_read(mtb_bmm350_t* dev, mtb_bmm350_data_t* data)
{
    cy_rslt_t rslt;
    rslt = bmm350_get_compensated_mag_xyz_temp_data(&(data->sensor_data), &(dev->sensor));
    return (BMM350_OK == rslt)
            ? CY_RSLT_SUCCESS
            : rslt;
}


/******************************************************************************
* mtb_bmm350_set_odr_performance
******************************************************************************/
cy_rslt_t mtb_bmm350_set_odr_performance(enum bmm350_data_rates odr,
                                         enum bmm350_performance_parameters performance,
                                         mtb_bmm350_t* dev)
{
    cy_rslt_t rslt;
    rslt = bmm350_set_odr_performance(odr, performance, &(dev->sensor));
    return (BMM350_OK == rslt)
            ? CY_RSLT_SUCCESS
            : rslt;
}


/******************************************************************************
* mtb_bmm350_selftest
******************************************************************************/
cy_rslt_t mtb_bmm350_selftest(mtb_bmm350_t* dev)
{
    struct bmm350_self_test out_data;
    cy_rslt_t rslt;
    rslt = bmm350_perform_self_test(&out_data, &(dev->sensor));
    Cy_SysLib_DelayUs(_SOFT_RESET_DELAY_US);
    return (BMM350_OK == rslt)
            ? CY_RSLT_SUCCESS
            : rslt;
}


/******************************************************************************
* mtb_bmm350_free_pin
******************************************************************************/
void mtb_bmm350_free_pin(mtb_bmm350_t* dev)
{
    _bmm350_i3c_context = NULL;
}


/*****************************************************************************
* Function name: _bmm350_i3c_read
*****************************************************************************
* Summary:
* This internal function reads I3C function map to host MCU
*
* Parameters:
*  reg_addr    8-bit register address of the sensor
*  reg_data    Data from the specified address
*  len         Length of the reg_data array
*  intf_ptr    Void pointer that can enable the linking of descriptors for interface related
*  callbacks
*
* Return:
*  int8_t     Status of execution
*
*****************************************************************************/
static BMM350_INTF_RET_TYPE _bmm350_i3c_read(uint8_t reg_addr, uint8_t* reg_data,
                                             uint32_t len, void* intf_ptr)
{
    cy_stc_i3c_controller_xfer_config_t data;
    cy_rslt_t result;

    CY_UNUSED_PARAMETER(intf_ptr);

    result = Cy_I3C_ControllerWriteByte(_bmm350_i3c_hw, _bmm350_i3c_device->dynamicAddress,
                                        reg_addr, _bmm350_i3c_context);

    if (CY_RSLT_SUCCESS != result)
    {
        Cy_I3C_Resume(_bmm350_i3c_hw, _bmm350_i3c_context);
    }

    data.targetAddress = _bmm350_i3c_device->dynamicAddress;
    data.buffer = (void*)reg_data;
    data.bufferSize = len;
    data.toc = true;

    result = Cy_I3C_ControllerRead(_bmm350_i3c_hw, &data, _bmm350_i3c_context);

    if (CY_RSLT_SUCCESS != result)
    {
        Cy_I3C_Resume(_bmm350_i3c_hw, _bmm350_i3c_context);
        return (BMM350_INTF_RET_TYPE)-1;
    }

    return _bmm350_wait_xfer(result);
}


/*****************************************************************************
* Function name: _bmm350_i3c_write
*****************************************************************************
* Summary:
* This internal function writes I3C function map to host MCU
*
* Parameters:
*  reg_addr    8-bit register address of the sensor
*  reg_data    Data from the specified address
*  len         Length of the reg_data array
*  intf_ptr    Void pointer that can enable the linking of descriptors for interface related
*  callbacks
*
* Return:
*  int8_t     Status of execution
*
*****************************************************************************/
/*****************************************************************************
* Function name: _bmm350_reenumerate
*****************************************************************************
* Re-runs I3C dynamic addressing (RSTDAA -> attach -> ENTDAA) so the BMM350 is
* reachable at its dynamic address again. A BMM350 soft reset detaches the part
* from the I3C bus (it reverts to answering only via ENTDAA on its static
* address), so every reset must be followed by re-enumeration before the next
* register access - otherwise reads come back as all-zeros.
*****************************************************************************/
static cy_rslt_t _bmm350_reenumerate(void)
{
    cy_stc_i3c_ccc_cmd_t     cccCmd;
    cy_stc_i3c_ccc_payload_t cccPayload;
    cy_rslt_t                rslt;

    cccCmd.address    = CY_I3C_BROADCAST_ADDR;
    cccCmd.cmd        = CY_I3C_CCC_RSTDAA(true);
    cccCmd.data       = &cccPayload;
    cccCmd.data->data = NULL;
    cccCmd.data->len  = 0U;

    rslt = Cy_I3C_SendCCCCmd(_bmm350_i3c_hw, &cccCmd, _bmm350_i3c_context);
    if (CY_RSLT_SUCCESS != rslt)
    {
        return rslt;
    }

    rslt = Cy_I3C_ControllerAttachI3CDevice(_bmm350_i3c_hw, _bmm350_i3c_device,
                                            _bmm350_i3c_context);
    if (CY_RSLT_SUCCESS != rslt)
    {
        return rslt;
    }

    return Cy_I3C_ControllerStartEntDaa(_bmm350_i3c_hw, _bmm350_i3c_context);
}

static BMM350_INTF_RET_TYPE _bmm350_i3c_write(uint8_t reg_addr, const uint8_t* reg_data,
                                              uint32_t len, void* intf_ptr)
{
    cy_stc_i3c_controller_xfer_config_t data;
    cy_rslt_t result;

    CY_UNUSED_PARAMETER(intf_ptr);

    uint8_t* tmp = (uint8_t*)malloc(len + 1);
    if (NULL == tmp)
    {
        return (BMM350_INTF_RET_TYPE)-1;
    }
    tmp[0] = reg_addr;
    memcpy(&tmp[1], reg_data, len);

    data.targetAddress = _bmm350_i3c_device->dynamicAddress;
    data.buffer = (void*)tmp;
    data.bufferSize = len + 1;
    data.toc = false;

    result = Cy_I3C_ControllerWrite(_bmm350_i3c_hw, &data, _bmm350_i3c_context);
    if (CY_RSLT_SUCCESS != result)
    {
        Cy_I3C_Resume(_bmm350_i3c_hw, _bmm350_i3c_context);
        free(tmp);   /* upstream leaks this buffer on every write; release it */
        return (BMM350_INTF_RET_TYPE)-1;
    }

    BMM350_INTF_RET_TYPE rc = _bmm350_wait_xfer(result);

    free(tmp);   /* upstream leaks this buffer on every write; release it */

    /* A soft reset (write 0xB6 to CMD reg 0x7E) detaches the BMM350 from the
     * I3C bus. The SensorAPI issues this inside bmm350_init() / bmm350_soft_reset()
     * and then immediately reads registers; without re-enumeration those reads
     * hit the now-detached dynamic address and return 0x00 (CHIP_ID misreads as
     * 0 -> BMM350_E_DEV_NOT_FOUND). Wait out the reset and re-run dynamic
     * addressing so the device is reachable before the SensorAPI's next access. */
    if ((BMM350_INTF_RET_SUCCESS == rc) && (BMM350_REG_CMD == reg_addr) &&
        (len >= 1u) && (BMM350_CMD_SOFTRESET == reg_data[0]))
    {
        Cy_SysLib_Delay(25u);          /* BMM350_SOFT_RESET_DELAY (24 ms) + margin */
        (void)_bmm350_reenumerate();
    }

    return rc;
}


/*****************************************************************************
* Function name: _bmm350_delay_us
*****************************************************************************
* Summary:
* This internal function maps delay function to host MCU
*
* Parameters:
*  period    The time period in microseconds
*  intf_ptr  Void pointer that can enable the linking of descriptors for interface related callbacks
*
* Return:
*  void
*
*****************************************************************************/
static void _bmm350_delay_us(uint32_t period, void* intf_ptr)
{
    CY_UNUSED_PARAMETER(intf_ptr);

    Cy_SysLib_DelayUs(period);
}


#if defined(__cplusplus)
}
#endif


/* [] END OF FILE */
