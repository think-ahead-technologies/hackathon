// ABOUTME: Tests for Contract A manifest validation against the firmware's fixed expectations.

#include "model_contract.h"
#include "test_util.h"

static firmware_contract_t fw_build(void) {
    firmware_contract_t fw = {0};
    fw.expected_input_shape[0] = 1;
    fw.expected_input_shape[1] = 49;
    fw.expected_input_shape[2] = 40;
    fw.expected_input_shape[3] = 1;
    strcpy(fw.expected_input_dtype, "int8");
    fw.arena_capacity = 524288;  // sizeof(tensor_arena) in the image
    return fw;
}

static model_contract_t matching_model(void) {
    model_contract_t m = {0};
    m.input_shape[0] = 1;
    m.input_shape[1] = 49;
    m.input_shape[2] = 40;
    m.input_shape[3] = 1;
    strcpy(m.input_dtype, "int8");
    m.arena_bytes = 400000;
    return m;
}

void run_model_contract_tests(void) {
    firmware_contract_t fw = fw_build();

    model_contract_t ok = matching_model();
    CHECK(contract_validate(&ok, &fw) == CONTRACT_OK);

    // Arena bigger than the fixed build-time buffer -> refused (can't grow at runtime).
    model_contract_t big = matching_model();
    big.arena_bytes = fw.arena_capacity + 1;
    CHECK(contract_validate(&big, &fw) == CONTRACT_ARENA_TOO_LARGE);

    // Different input shape -> would break feature pre-processing -> refused.
    model_contract_t shape = matching_model();
    shape.input_shape[1] = 64;
    CHECK(contract_validate(&shape, &fw) == CONTRACT_INPUT_SHAPE_MISMATCH);

    // Different input dtype -> refused.
    model_contract_t dtype = matching_model();
    strcpy(dtype.input_dtype, "int16");
    CHECK(contract_validate(&dtype, &fw) == CONTRACT_INPUT_DTYPE_MISMATCH);

    // Every result code has a non-empty human-readable reason.
    CHECK(contract_result_str(CONTRACT_OK)[0] != '\0');
    CHECK(contract_result_str(CONTRACT_ARENA_TOO_LARGE)[0] != '\0');
}
