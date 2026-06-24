// ABOUTME: Contract A validation — refuse a model that won't fit or won't match pre-processing.

#include <string.h>

#include "model_contract.h"

contract_result_t contract_validate(const model_contract_t *m, const firmware_contract_t *fw) {
    // Arena is fixed at build time and cannot grow at runtime — reject anything larger.
    if (m->arena_bytes > fw->arena_capacity) {
        return CONTRACT_ARENA_TOO_LARGE;
    }
    // A different input shape would break the feature pre-processing the firmware does.
    for (int i = 0; i < 4; i++) {
        if (m->input_shape[i] != fw->expected_input_shape[i]) {
            return CONTRACT_INPUT_SHAPE_MISMATCH;
        }
    }
    if (strncmp(m->input_dtype, fw->expected_input_dtype, sizeof(m->input_dtype)) != 0) {
        return CONTRACT_INPUT_DTYPE_MISMATCH;
    }
    return CONTRACT_OK;
}

const char *contract_result_str(contract_result_t r) {
    switch (r) {
        case CONTRACT_OK:                   return "ok";
        case CONTRACT_ARENA_TOO_LARGE:      return "model arena exceeds the fixed tensor_arena";
        case CONTRACT_INPUT_SHAPE_MISMATCH: return "input shape does not match pre-processing";
        case CONTRACT_INPUT_DTYPE_MISMATCH: return "input dtype does not match pre-processing";
    }
    return "unknown";
}
