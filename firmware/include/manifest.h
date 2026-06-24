// ABOUTME: Parse the (signature-verified) Contract A manifest JSON into the firmware contract.
// ABOUTME: Fixed-schema field extractor — no general JSON lib needed; pure, host-tested.

#ifndef MANIFEST_H
#define MANIFEST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "model_contract.h"

// Parse a manifest into the firmware-facing contract plus the bound flatbuffer sha256.
// The manifest is trusted by the time this runs (its signature is verified first), so this
// targets the known pipeline schema rather than defending against arbitrary JSON. Returns
// false if any required field is missing or malformed.
bool parse_manifest(const uint8_t *manifest, size_t len, model_contract_t *out, uint8_t sha[32]);

#endif  // MANIFEST_H
