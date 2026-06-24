# ABOUTME: Host build of the vendor-neutral firmware logic + its unit tests (no board needed).
# ABOUTME: The TFLM/Ethos-U/SMIF integration (model_loader.cc, device_main.c) is built on-target only.

CC      ?= cc
# -iquote (not -I): our headers are included with "...", so this keeps include/ off the
# <...> search path — otherwise our include/features.h shadows glibc's <features.h> on Linux.
CFLAGS  ?= -std=c11 -Wall -Wextra -Werror -iquote include
BUILD   := build

# Pure, host-testable logic — no hardware, no TFLM.
LOGIC_SRC := src/model_slot.c src/model_contract.c src/shadow.c src/nats_proto.c \
             src/meta_store.c src/manifest.c src/deploy.c src/capture.c
TEST_SRC  := test/test_runner.c test/test_model_slot.c test/test_model_contract.c \
             test/test_shadow.c test/test_nats_proto.c test/test_meta_store.c \
             test/test_manifest.c test/test_deploy.c test/test_capture.c

.DEFAULT_GOAL := test

## test: build and run the host-side unit tests for the vendor-neutral logic
.PHONY: test
test: $(BUILD)/tests
	./$(BUILD)/tests

$(BUILD)/tests: $(LOGIC_SRC) $(TEST_SRC) | $(BUILD)
	$(CC) $(CFLAGS) -iquote test $(LOGIC_SRC) $(TEST_SRC) -o $@

## crosslang: prove deploy_frame.py output parses byte-identically through the C deploy/manifest code
.PHONY: crosslang
crosslang: $(BUILD)/harness_deploy
	python3 test/crosslang_test.py ./$(BUILD)/harness_deploy

$(BUILD)/harness_deploy: src/deploy.c src/manifest.c test/harness_deploy.c | $(BUILD)
	$(CC) $(CFLAGS) src/deploy.c src/manifest.c test/harness_deploy.c -o $@

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

$(BUILD):
	mkdir -p $(BUILD)

## clean: remove build artifacts
.PHONY: clean
clean:
	rm -rf $(BUILD)
