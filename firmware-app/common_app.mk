################################################################################
# \file common_app.mk
# \version 1.0
#
# \brief
# Settings shared across the entire application.
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
# Paths
################################################################################

# Locate ModusToolbox helper tools folders in default installation
# locations for Windows, Linux, and macOS.
CY_WIN_HOME=$(subst \,/,$(USERPROFILE))
CY_TOOLS_PATHS ?= $(wildcard \
    $(CY_WIN_HOME)/ModusToolbox/tools_* \
    $(HOME)/ModusToolbox/tools_* \
    /Applications/ModusToolbox/tools_*)

# If you install ModusToolbox software in a custom location, add the path to its
# "tools_X.Y" folder (where X and Y are the version number of the tools
# folder).
CY_TOOLS_PATHS+=

# Default to the newest installed tools folder, or the users override (if it's
# found).
CY_TOOLS_DIR=$(lastword $(sort $(wildcard $(CY_TOOLS_PATHS))))

# Absolute path to the compiler's "bin" directory. The variable name depends on 
# the toolchain used for the build. Refer to the ModusToolbox user guide to get 
# the correct variable name for the toolchain used in your build.
#
# The default path depends on the selected TOOLCHAIN and is set in the 
# Make recipe.
CY_COMPILER_GCC_ARM_DIR?=

ifeq ($(CY_TOOLS_DIR),)
$(error Unable to find any of the available CY_TOOLS_PATHS -- $(CY_TOOLS_PATHS))
endif

################################################################################
# Shared library cache
################################################################################

# The middleware is resolved into a shared cache OUTSIDE this repo (multi-GB,
# gitignored). Every build needs CY_GETLIBS_SHARED_PATH + CY_GETLIBS_SHARED_NAME
# to point at it: the getlibs-generated proj_*/libs/mtb.mk hardcodes each library
# as $(CY_GETLIBS_SHARED_PATH)/$(CY_GETLIBS_SHARED_NAME)/<lib>. With them unset the
# paths collapse to "/mtb_shared/..." and the build aborts with
# "Libraries: core-make recipe-make ... not found".
#
# Default to the known local cache so a bare `make firmware`/`build`/`program`
# just works. Override on the CLI or environment for a different location, e.g.:
#   make build CY_GETLIBS_SHARED_PATH=/path/to/parent CY_GETLIBS_SHARED_NAME=mtb_shared
CY_GETLIBS_SHARED_NAME ?= mtb_shared
ifneq ($(wildcard $(abspath ../$(CY_GETLIBS_SHARED_NAME))),)
CY_GETLIBS_SHARED_PATH ?= $(abspath ..)
else ifneq ($(wildcard $(abspath ../../$(CY_GETLIBS_SHARED_NAME))),)
CY_GETLIBS_SHARED_PATH ?= $(abspath ../..)
else ifneq ($(wildcard $(HOME)/mtw/$(CY_GETLIBS_SHARED_NAME)),)
CY_GETLIBS_SHARED_PATH ?= $(HOME)/mtw
endif
