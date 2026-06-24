// ABOUTME: Contract A (manifest) validation against the firmware's fixed build-time assumptions.
// ABOUTME: The "refuse a mismatched model on load" safety beat — vendor-neutral, pure logic.

#ifndef MODEL_CONTRACT_H
#define MODEL_CONTRACT_H

#include <stdint.h>

// The model's self-described contract, parsed from the deployed manifest (Contract A).
typedef struct {
    int32_t  input_shape[4];   // e.g. {1, 49, 40, 1}
    int32_t  output_shape[2];  // {1, K} embedding — device scores L2 distance to the per-unit centroid
    char     input_dtype[8];   // "int8"
    float    input_scale;      // quant scale  (pre-processing depends on these)
    int32_t  input_zero_point; // quant zero-point
    uint32_t arena_bytes;      // worst-case tensor-arena the model needs
} model_contract_t;

// What this firmware build can actually accommodate. `tensor_arena` is fixed at build
// time and cannot grow at runtime, so a model needing more arena must be refused.
typedef struct {
    int32_t  expected_input_shape[4];
    char     expected_input_dtype[8];
    uint32_t arena_capacity;   // sizeof(tensor_arena) baked into the image
} firmware_contract_t;

typedef enum {
    CONTRACT_OK = 0,
    CONTRACT_ARENA_TOO_LARGE,        // model would overflow the fixed arena
    CONTRACT_INPUT_SHAPE_MISMATCH,   // would break the feature pre-processing
    CONTRACT_INPUT_DTYPE_MISMATCH,
} contract_result_t;

// Validate a model's contract against the firmware's fixed expectations. A new model
// with a different input shape / dtype, or one too big for the arena, is rejected here
// BEFORE it is ever loaded — the refusal is itself a safety beat (model-pipeline.md).
contract_result_t contract_validate(const model_contract_t *m, const firmware_contract_t *fw);

// Human-readable reason, for the log line that accompanies a refusal.
const char *contract_result_str(contract_result_t r);

#endif  // MODEL_CONTRACT_H
