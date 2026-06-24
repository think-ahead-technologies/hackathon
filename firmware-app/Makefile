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

# Local convenience targets that don't need the ModusToolbox build machinery (serial console).
# Skip the heavy application.mk include for these so `make logs` is instant and toolchain-free.
LOCAL_GOALS := logs logs-reset
ifeq ($(filter $(LOCAL_GOALS),$(MAKECMDGOALS)),)
include common_app.mk
include $(CY_TOOLS_DIR)/make/application.mk
endif

## logs:       stream the device debug UART (Ctrl-C to stop). Override PORT=/dev/cu.usbmodemXXXX
.PHONY: logs
logs:
	@scripts/device-logs.sh

## logs-reset: reset the device, then stream from the boot banner
.PHONY: logs-reset
logs-reset:
	@scripts/device-logs.sh --reset
