/******************************************************************************
 * \file mtb_bmm350.h
 *
 * \brief
 *     This file is the public interface of the BMM350 magnetic sensor.
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


#pragma once

#include "bmm350.h"
#include "cy_pdl.h"
#include "cy_result.h"

#if defined(__cplusplus)
extern "C"
{
#endif

/**
 * Structure holding the BMM instance specific information.
 *
 * Application code should not rely on the specific content of this struct.
 * They are considered an implementation detail which is subject to change
 * between platforms and/or library releases.
 */
typedef struct
{
    struct bmm350_dev           sensor;
} mtb_bmm350_t;

/** Structure holding the magnetometer data read from the device */
typedef struct
{
    /** data */
    struct bmm350_mag_temp_data sensor_data;
} mtb_bmm350_data_t;

/**
 * Enumeration used for selecting I2C/I3C physical address.
 */
typedef enum
{
    /** I2C/I3C physical address */
    MTB_BMM350_ADDRESS_DEFAULT = BMM350_I2C_ADSEL_SET_LOW,
    MTB_BMM350_ADDRESS_SEC     = BMM350_I2C_ADSEL_SET_HIGH
} mtb_bmm350_address_t;

/*****************************************************************************
* Function name: mtb_bmm350_init_i3c
*****************************************************************************
* Summary:
* This function initializes the I3C context, configures the BMM350, and sets
* platform-dependent function pointers
*
* Parameters:
*  dev             Pointer to a BMM350 object. The caller must allocate the memory
*                  for this object but the init function will initialize its contents
*  i3c_hw          I3C core to use for communicating with the BMM350 sensor
*  i3c_context     I3C context to use for communicating with the BMM350 sensor
*  i3c_device      I3C device structure to use for communicating with the BMM350 sensor
* Return:
*  cy_rslt_t    CY_RSLT_SUCCESS if properly initialized, else an error indicating
*               what went wrong
*
*****************************************************************************/
cy_rslt_t mtb_bmm350_init_i3c(mtb_bmm350_t* dev, I3C_CORE_Type* i3c_hw,
                              cy_stc_i3c_context_t* i3c_context, cy_stc_i3c_device_t* i3c_device);

/*****************************************************************************
* Function Name: mtb_bmm350_read
*****************************************************************************
* Summary:
* This function gets the sensor data for Magnetometer
*
* Parameters:
*  dev     Pointer to a BMM350 object. The caller must allocate the memory
*          for this object but the init function will initialize its contents.
*  data    The magnetometer data read from the BMM350 sensor
*
* Return:
*  cy_rslt_t    CY_RSLT_SUCCESS if properly read, else an error indicating
*               what went wrong
*
*****************************************************************************/
cy_rslt_t mtb_bmm350_read(mtb_bmm350_t* dev, mtb_bmm350_data_t* data);

/*****************************************************************************
* Function Name: mtb_bmm350_set_odr_performance
*****************************************************************************
* Summary:
* This function sets the ODR and averaging factor
*
* Parameters:
*  odr          BMM350 ODR data rate
*  performance  BMM350 averaging performance parameter
*  dev          Pointer to a BMM350 object. The caller must allocate the memory
*               for this object but the init function will initialize its contents
*
* Return:
*  cy_rslt_t    CY_RSLT_SUCCESS if test passed, else an error indicating
*               what went wrong
*
*****************************************************************************/
cy_rslt_t mtb_bmm350_set_odr_performance(enum bmm350_data_rates odr,
                                         enum bmm350_performance_parameters performance,
                                         mtb_bmm350_t* dev);

/*****************************************************************************
* Function Name: mtb_bmm350_selftest
*****************************************************************************
* Summary:
* Performs Magnetometer self tests
* Note: These tests cause a soft reset of the device and device should be
* reconfigured after a test.
*
* Parameters:
*  dev    Pointer to a BMM350 object. The caller must allocate the memory
*         for this object but the init function will initialize its contents
*
* Return:
*  cy_rslt_t    CY_RSLT_SUCCESS if test passed, else an error indicating
*               what went wrong
*
*****************************************************************************/
cy_rslt_t mtb_bmm350_selftest(mtb_bmm350_t* dev);

/*****************************************************************************
* Function Name: mtb_bmm350_free_pin
*****************************************************************************
* Summary:
* Frees up any resources allocated by the magnetic_sensor as part of \ref mtb_bmm350_init_i3c().
*
* Parameters:
*  dev          Pointer to a BMM350 object. The caller must allocate the memory
*               for this object but the init function will initialize its contents
*
*****************************************************************************/
void mtb_bmm350_free_pin(mtb_bmm350_t* dev);

#if defined(__cplusplus)
}
#endif

/* [] END OF FILE */
