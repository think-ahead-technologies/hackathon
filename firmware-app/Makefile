################################################################################
# \file Makefile
# \version 1.0
#
# \brief
# Top-level application make file.
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


################################################################################
# Basic Configuration
################################################################################

# Type of ModusToolbox Makefile Options include:
#
# COMBINED    -- Top Level Makefile usually for single standalone application
# APPLICATION -- Top Level Makefile usually for multi project application
# PROJECT     -- Project Makefile under Application
#
MTB_TYPE=APPLICATION

MTB_PROJECTS=proj_cm33_s proj_cm33_ns proj_cm55

################################################################################
# Paths
################################################################################

# Number of parallel build jobs (override with `make firmware JOBS=4`).
JOBS ?= 8

# Convenience build flags for `make firmware` / `make flash` (forwarded to the real build):
#   RELEASE=1 -> size-optimized Release build (-Os, ~10% smaller); default is Debug.
#   NO_TLS=1  -> plaintext NATS connection (no TLS handshake) for the PoC / open brokers.
# Any native MTB var (WIFI_SSID=, NATS_HOST=, NATS_PORT=, CONFIG=) still passes straight through.
FWFLAGS := $(if $(filter 1,$(RELEASE)),CONFIG=Release,) \
           $(if $(filter 1,$(NO_TLS)),NATS_DISABLE_TLS=1,)

# Local convenience targets that don't need the ModusToolbox build machinery (serial console,
# flashing via scripts, help). Skip the heavy application.mk include for these so they're instant
# and toolchain-free. `firmware` is intentionally NOT local: it recurses into the real build.
LOCAL_GOALS := logs logs-reset flash help
ifeq ($(filter $(LOCAL_GOALS),$(MAKECMDGOALS)),)
include common_app.mk
include $(CY_TOOLS_DIR)/make/application.mk
endif

## firmware:   one-shot full build of all 3 cores -> build/app_combined.hex.
##             Flags: RELEASE=1 (size-optimized -Os), NO_TLS=1 (plaintext NATS). Also passes
##             WIFI_SSID=.. NATS_HOST=.. etc. straight through.
##             e.g. `make firmware RELEASE=1 NO_TLS=1 WIFI_SSID=net WIFI_PASSWORD=pw`
.PHONY: firmware
firmware:
	@if [ ! -f proj_cm33_ns/libs/mtb.mk ]; then \
	  echo "==> First build: resolving middleware (make getlibs)…"; \
	  $(MAKE) getlibs; \
	fi
	@$(MAKE) build -j$(JOBS) $(FWFLAGS)

## flash:      back up the device, then build+program it. Same flags as firmware
##             (e.g. `make flash RELEASE=1 NO_TLS=1`).
.PHONY: flash
flash:
	@scripts/device-flash.sh $(FWFLAGS)

## logs:       stream the device debug UART (Ctrl-C to stop). Override PORT=/dev/cu.usbmodemXXXX
.PHONY: logs
logs:
	@scripts/device-logs.sh

## logs-reset: reset the device, then stream from the boot banner
.PHONY: logs-reset
logs-reset:
	@scripts/device-logs.sh --reset

## help:       list these convenience targets
.PHONY: help
help:
	@grep -hE '^## ' $(firstword $(MAKEFILE_LIST)) | sed 's/^## //'
