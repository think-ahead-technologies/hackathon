# ABOUTME: Host build of the vendor-neutral firmware logic + its unit tests (no board needed).
# ABOUTME: The TFLM/Ethos-U/SMIF integration (model_loader.cc, device_main.c) is built on-target only.

CC      ?= cc
# -iquote (not -I): our headers are included with "...", so this keeps include/ off the
# <...> search path — otherwise our include/features.h shadows glibc's <features.h> on Linux.
CFLAGS  ?= -std=c11 -Wall -Wextra -Werror -iquote include
BUILD   := build

# Pure, host-testable logic — no hardware, no TFLM.
LOGIC_SRC := src/model_slot.c src/model_contract.c src/shadow.c src/nats_proto.c \
             src/camera_proto.c src/cam_change.c \
             src/meta_store.c src/manifest.c src/deploy.c src/capture.c src/json_scan.c
TEST_SRC  := test/test_runner.c test/test_model_slot.c test/test_model_contract.c \
             test/test_shadow.c test/test_nats_proto.c test/test_camera_proto.c \
             test/test_cam_change.c \
             test/test_meta_store.c \
             test/test_manifest.c test/test_deploy.c test/test_capture.c

.DEFAULT_GOAL := test

## test: build and run the host-side unit tests for the vendor-neutral logic
.PHONY: test
test: $(BUILD)/tests
	./$(BUILD)/tests

$(BUILD)/tests: $(LOGIC_SRC) $(TEST_SRC) | $(BUILD)
	$(CC) $(CFLAGS) -iquote test $(LOGIC_SRC) $(TEST_SRC) -o $@

## test-san: run the unit tests under AddressSanitizer + UndefinedBehaviorSanitizer.
## Catches out-of-bounds access and integer/UB the plain build can't see — the parsers
## are driven with adversarial input, so this is where a bad offset/memcpy surfaces.
## -fno-sanitize-recover=all makes the first finding a hard, non-zero exit (CI-friendly).
SAN_FLAGS := -fsanitize=address,undefined -fno-sanitize-recover=all -g -O1
.PHONY: test-san
test-san: $(BUILD)/tests_san
	ASAN_OPTIONS=abort_on_error=1 UBSAN_OPTIONS=print_stacktrace=1 ./$(BUILD)/tests_san

$(BUILD)/tests_san: $(LOGIC_SRC) $(TEST_SRC) | $(BUILD)
	$(CC) $(CFLAGS) $(SAN_FLAGS) -iquote test $(LOGIC_SRC) $(TEST_SRC) -o $@

## test-m32: build and run the suite as a 32-bit binary so size_t-width bugs surface on the
## host — e.g. a 32-bit offset+len overflow in the frame/manifest parsers that the 64-bit
## build (where size_t is wide enough to never wrap) physically cannot see. Needs a
## 32-bit-capable toolchain (Linux: apt-get install gcc-multilib). Skipped with a note where
## the toolchain can't target 32-bit (e.g. macOS), so it's a no-op locally and real in CI.
.PHONY: test-m32
test-m32: | $(BUILD)
	@if echo 'int main(void){return 0;}' | $(CC) -m32 -x c - -o /dev/null 2>/dev/null; then \
		echo "building 32-bit test suite ($(CC) -m32)"; \
		$(CC) $(CFLAGS) -m32 -iquote test $(LOGIC_SRC) $(TEST_SRC) -o $(BUILD)/tests_m32 && \
		./$(BUILD)/tests_m32; \
	else \
		echo "skip test-m32: $(CC) cannot build 32-bit binaries on this host"; \
		echo "  (Linux CI: install gcc-multilib; macOS dropped 32-bit toolchain support)"; \
	fi

## lint: static analysis with cppcheck (install: brew install cppcheck).
## Scans every src/ file — including the on-target HAL/orchestration the host build skips
## — for the bounds/overflow/uninitialized family. Missing BSP headers are tolerated, not
## errors. --error-exitcode=1 fails CI on any finding; inline // cppcheck-suppress for VERIFY seams.
.PHONY: lint
lint:
	@command -v cppcheck >/dev/null 2>&1 || { echo "cppcheck not found — install with: brew install cppcheck"; exit 1; }
	cppcheck --enable=warning,style,performance,portability --std=c11 \
	         --inline-suppr --suppress=missingIncludeSystem --suppress=missingInclude \
	         --error-exitcode=1 --quiet -I include src

## crosslang: prove deploy_frame.py output parses byte-identically through the C deploy/manifest code
.PHONY: crosslang
crosslang: $(BUILD)/harness_deploy
	python3 test/crosslang_test.py ./$(BUILD)/harness_deploy

$(BUILD)/harness_deploy: src/deploy.c src/manifest.c src/json_scan.c test/harness_deploy.c | $(BUILD)
	$(CC) $(CFLAGS) src/deploy.c src/manifest.c src/json_scan.c test/harness_deploy.c -o $@

## nats-login: drive our real nats_proto auth code through a live login against a throwaway nats-server
.PHONY: nats-login
nats-login: $(BUILD)/harness_nats_login
	python3 test/nats_login_test.py ./$(BUILD)/harness_nats_login

$(BUILD)/harness_nats_login: src/nats_proto.c test/harness_nats_login.c | $(BUILD)
	$(CC) $(CFLAGS) src/nats_proto.c test/harness_nats_login.c -o $@

## features-cross: prove the C feature extractor matches spectro.py byte-for-byte (needs the export venv)
PYEXPORT := ../wear_detector/export/.venv/bin/python
.PHONY: features-cross
features-cross: $(BUILD)/harness_features
	$(PYEXPORT) test/feature_cross_test.py ./$(BUILD)/harness_features

$(BUILD)/harness_features: src/features.c test/harness_features.c | $(BUILD)
	$(CC) $(CFLAGS) src/features.c test/harness_features.c -lm -o $@

## score-cross: prove the C scorer matches baseline.distances (needs the export venv + built model)
.PHONY: score-cross
score-cross: $(BUILD)/harness_score
	$(PYEXPORT) test/score_cross_test.py ./$(BUILD)/harness_score

$(BUILD)/harness_score: src/score.c test/harness_score.c | $(BUILD)
	$(CC) $(CFLAGS) src/score.c test/harness_score.c -lm -o $@

## compile-commands: (re)generate compile_commands.json so clangd resolves our "..." headers
.PHONY: compile-commands
compile-commands:
	python3 tools/gen_compile_commands.py

$(BUILD):
	mkdir -p $(BUILD)

## clean: remove build artifacts
.PHONY: clean
clean:
	rm -rf $(BUILD)
