################################################################################
# \file Makefile
# \version 1.0
#
# \brief
# Makefile for configuring the Cortex-M33 core project
#
################################################################################
# \copyright
# (c) 2024-2025, Infineon Technologies AG, or an affiliate of Infineon Technologies AG.
# SPDX-License-Identifier: Apache-2.0
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

include ../common.mk

################################################################################
# Basic Configuration
################################################################################

# Name of application (used to derive name of final linked file).
#
# If APPNAME is edited, ensure to update or regenerate launch
# configurations for your IDE.
APPNAME=proj_cm33_ns

# If set to "true" or "1", display full command-lines when building.
VERBOSE=


################################################################################
# Advanced Configuration
################################################################################

# Enable optional code that is ordinarily disabled by default.
#
# Available components depend on the specific targeted hardware and firmware
# in use. In general, if you have
#
#    COMPONENTS=foo bar
#
# ... then code in directories named COMPONENT_foo and COMPONENT_bar will be
# added to the build
#
COMPONENTS+=FREERTOS LWIP MBEDTLS SECURE_SOCKETS
# No ML on CM33: inference runs on CM55 + the Ethos-U55 NPU; CM33-NS handles Wi-Fi/NATS and
# forwards the score from the shared SOCMEM mailbox (see shared_score.h / proj_cm55).

# Like COMPONENTS, but disable optional code that was enabled by default.
DISABLE_COMPONENTS=

CORE=CM33
CORE_NAME=CM33_0

# By default the build system automatically looks in the Makefile's directory
# tree for source code and builds it. The SOURCES variable can be used to
# manually add source code to the build process from a location not searched
# by default, or otherwise not found by the build system.
# Pull in the vendor-neutral firmware logic + HAL from ../../firmware (single source of truth,
# shared with the host test build). CM33 does NOT run the model — model_loader_cm33.c drives the
# deploy/shadow path by commanding CM55 over shared SOCMEM; the real inference is on CM55
# (proj_cm55) and the scores arrive back via the same mailbox. score.c provides score_default_params
# for the boot fallback (the model-carried params seam; see firmware/docs/cm55-model-loading.md).
FW_SRC := ../../firmware/src
SOURCES+=$(FW_SRC)/model_slot.c \
         $(FW_SRC)/model_contract.c \
         $(FW_SRC)/shadow.c \
         $(FW_SRC)/nats_proto.c \
         $(FW_SRC)/meta_store.c \
         $(FW_SRC)/manifest.c \
         $(FW_SRC)/deploy.c \
         $(FW_SRC)/capture.c \
         $(FW_SRC)/score.c \
         $(FW_SRC)/platform_hal_pse84.c \
         $(FW_SRC)/model_loader_cm33.c \
         $(FW_SRC)/device_main.c

# Like SOURCES, but for include directories. Value should be paths to
# directories (without a leading -I).
INCLUDES+=../../firmware/include

# Custom configuration of mbedtls library.
MBEDTLSFLAGS = MBEDTLS_USER_CONFIG_FILE='"configs/mbedtls_user_config.h"'

# Add additional defines to the build process (without a leading -D).
DEFINES+=$(MBEDTLSFLAGS) CYBSP_WIFI_CAPABLE CY_RETARGET_IO_CONVERT_LF_TO_CRLF CY_RTOS_AWARE 

# Default configuration of mbedtls library.
DEFINES+=MBEDTLS_CONFIG_FILE='"mbedtls/mbedtls_config.h"'

# PSA configuration of mbedtls library.
DEFINES+=MBEDTLS_PSA_CRYPTO_CONFIG_FILE='"configs/ifx_psa_crypto_config.h"'

# Define the following macro in the application's Makefile to mandatorily disable the custom 
# configuration header file.
DEFINES += HTTP_DO_NOT_USE_CUSTOM_CONFIG
DEFINES += MQTT_DO_NOT_USE_CUSTOM_CONFIG

# Connectivity-first firmware build: no QSPI flash persistence yet (E84 serial-flash
# bring-up pending), so platform_hal_pse84.c uses its no-persistence flash backend.
DEFINES += HAL_FLASH_STUB

# Wi-Fi credentials. An SSID/password containing spaces does NOT survive a -D define
# through make (it word-splits into broken flags), so the robust path is a local,
# gitignored header that's force-included ahead of platform_hal_pse84.c's defaults:
#   proj_cm33_ns/wifi_credentials.h:
#     #define WIFI_SSID     "My Network"
#     #define WIFI_PASSWORD "secret"
# If that header is absent, space-free creds may instead be passed on the command line:
#   make build WIFI_SSID=foo WIFI_PASSWORD=bar
# With neither, platform_hal_pse84.c's "MY_WIFI_SSID"/... defaults apply.
WIFI_CREDS_HEADER := $(wildcard wifi_credentials.h)
ifneq ($(WIFI_CREDS_HEADER),)
CFLAGS += -include $(abspath wifi_credentials.h)
else ifneq ($(WIFI_SSID),)
DEFINES += WIFI_SSID='"$(WIFI_SSID)"' WIFI_PASSWORD='"$(WIFI_PASSWORD)"'
endif

# NATS broker target, build-overridable:
#   make build NATS_HOST=10.247.217.107 NATS_PORT=4222 NATS_DISABLE_TLS=1
# NATS_DISABLE_TLS=1 selects a plaintext TCP socket (no TLS) for an open broker.
ifneq ($(NATS_HOST),)
DEFINES += NATS_HOST='"$(NATS_HOST)"'
endif
ifneq ($(NATS_PORT),)
DEFINES += NATS_PORT=$(NATS_PORT)
endif
ifeq ($(NATS_DISABLE_TLS),1)
DEFINES += NATS_DISABLE_TLS
endif

# Select softfp or hardfp floating point. Default is softfp.
VFP_SELECT+=

# Additional / custom C compiler flags.
#
# NOTE: Includes and defines should use the INCLUDES and DEFINES variable
# above.
CFLAGS+=

# Additional / custom C++ compiler flags.
#
# NOTE: Includes and defines should use the INCLUDES and DEFINES variable
# above.
CXXFLAGS+=

# Additional / custom assembler flags.
#
# NOTE: Includes and defines should use the INCLUDES and DEFINES variable
# above.
ASFLAGS+=

# Additional / custom linker flags.
LDFLAGS+=

# Additional / custom libraries to link in to the application.
LDLIBS+=

# Path to the linker script to use (if empty, use the default linker script).
LINKER_SCRIPT=

# Custom pre-build commands to run.
PREBUILD=

# Custom post-build commands to run.
POSTBUILD=

################################################################################
# Paths
################################################################################

# Relative path to the project directory (default is the Makefile's directory).
#
# This controls where automatic source code discovery looks for code.
CY_APP_PATH=

# Relative path to the shared repo location.
#
# All .mtb files have the format, <URI>#<COMMIT>#<LOCATION>. If the <LOCATION> 
# field begins with $$ASSET_REPO$$, then the repo is deposited in the path 
# specified by the CY_GETLIBS_SHARED_PATH variable. The default location is one 
# directory level above the current app directory.
# This is used with CY_GETLIBS_SHARED_NAME variable, which specifies the 
# directory name.
CY_GETLIBS_SHARED_PATH?=../..

# Directory name of the shared repo location.
#
CY_GETLIBS_SHARED_NAME?=mtb_shared

include $(CY_TOOLS_DIR)/make/start.mk
