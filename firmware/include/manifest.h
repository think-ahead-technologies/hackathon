// ABOUTME: Parse the (signature-verified) Contract A manifest JSON into the firmware contract.
// ABOUTME: Fixed-schema field extractor — no general JSON lib needed; pure, host-tested.

#ifndef MANIFEST_H
#define MANIFEST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "model_contract.h"
#include "score.h"

// Parse a manifest into the firmware-facing contract plus the bound flatbuffer sha256.
// The manifest is trusted by the time this runs (its signature is verified first), so this
// targets the known pipeline schema rather than defending against arbitrary JSON. Returns
// false if any required field is missing or malformed.
bool parse_manifest(const uint8_t *manifest, size_t len, model_contract_t *out, uint8_t sha[32]);

// Parse the device's scoring parameters from the same manifest: the output quantization
// (output.scale / output.zero_point) and the per-unit healthy baseline (embedding.centroid +
// embedding.threshold). These travel WITH the model so the device scores a deployed model in its
// own embedding space rather than against compiled-in constants. Returns false if the output quant
// or the embedding block (centroid / threshold) is missing or malformed.
bool parse_manifest_scoring(const uint8_t *manifest, size_t len, score_params_t *out);

#endif  // MANIFEST_H
