// ABOUTME: Tests for parse_manifest — the fixed-schema Contract A field extractor.

#include <math.h>
#include <string.h>

#include "manifest.h"
#include "score.h"
#include "test_util.h"

// The pretty-printed manifest the pipeline emits (package.py, indent=2) — whitespace + newlines.
static const char *PRETTY =
    "{\n"
    "  \"model_id\": \"pdm-anomaly\",\n"
    "  \"target\": \"pse84/ethos-u55-128\",\n"
    "  \"input\": {\n"
    "    \"shape\": [ 1, 49, 40, 1 ],\n"
    "    \"dtype\": \"int8\",\n"
    "    \"scale\": 0.018,\n"
    "    \"zero_point\": -12\n"
    "  },\n"
    "  \"output\": {\n"
    "    \"shape\": [ 1, 2 ],\n"
    "    \"dtype\": \"int8\",\n"
    "    \"scale\": 0.004,\n"
    "    \"zero_point\": 0\n"
    "  },\n"
    "  \"arena_bytes\": 524288,\n"
    "  \"version\": \"pdm-anomaly@2026.06.16-c1\",\n"
    "  \"sha256\": \"8d2314a285349d73cbbbf7f79da8e07d15cdfbefc1463ed6f18e0e5da863052f\"\n"
    "}\n";

void run_manifest_tests(void) {
    model_contract_t c;
    uint8_t sha[32];
    bool ok = parse_manifest((const uint8_t *)PRETTY, strlen(PRETTY), &c, sha);
    CHECK(ok == true);

    // input shape, resolved from the input{} region (not output's shape).
    CHECK(c.input_shape[0] == 1);
    CHECK(c.input_shape[1] == 49);
    CHECK(c.input_shape[2] == 40);
    CHECK(c.input_shape[3] == 1);
    CHECK(c.output_shape[0] == 1);
    CHECK(c.output_shape[1] == 2);

    CHECK_STR_EQ(c.input_dtype, "int8");
    CHECK(fabsf(c.input_scale - 0.018f) < 1e-6f);
    CHECK(c.input_zero_point == -12);
    CHECK(c.arena_bytes == 524288u);

    // sha256 hex -> 32 bytes (check the ends).
    CHECK(sha[0] == 0x8d);
    CHECK(sha[31] == 0x2f);

    // A compact (no-whitespace) manifest parses identically.
    const char *compact =
        "{\"input\":{\"shape\":[1,49,40,1],\"dtype\":\"int8\",\"scale\":0.018,\"zero_point\":-12},"
        "\"output\":{\"shape\":[1,2]},\"arena_bytes\":524288,"
        "\"sha256\":\"8d2314a285349d73cbbbf7f79da8e07d15cdfbefc1463ed6f18e0e5da863052f\"}";
    model_contract_t c2;
    uint8_t sha2[32];
    CHECK(parse_manifest((const uint8_t *)compact, strlen(compact), &c2, sha2) == true);
    CHECK(c2.input_shape[1] == 49);
    CHECK(c2.arena_bytes == 524288u);
    CHECK(sha2[0] == 0x8d);

    // Missing required fields -> rejected.
    const char *no_sha = "{\"input\":{\"shape\":[1,49,40,1],\"dtype\":\"int8\",\"scale\":0.018,"
                         "\"zero_point\":-12},\"output\":{\"shape\":[1,2]},\"arena_bytes\":524288}";
    CHECK(parse_manifest((const uint8_t *)no_sha, strlen(no_sha), &c2, sha2) == false);

    const char *no_input = "{\"output\":{\"shape\":[1,2]},\"arena_bytes\":1,"
                           "\"sha256\":\"00\"}";
    CHECK(parse_manifest((const uint8_t *)no_input, strlen(no_input), &c2, sha2) == false);

    // --- scoring params (output quant + embedding centroid/threshold) -------------------------
    // Mirrors pipeline/model-meta.json: the device dequantizes the int8 embedding with output
    // scale/zero_point, then L2-distances to the per-unit centroid and alerts over threshold.
    static const char *SCORING =
        "{\"input\":{\"shape\":[1,49,40,1],\"dtype\":\"int8\",\"scale\":0.0258,\"zero_point\":-128},"
        "\"output\":{\"shape\":[1,8],\"dtype\":\"int8\",\"scale\":0.2020,\"zero_point\":-9},"
        "\"arena_bytes\":131072,"
        "\"feature_config\":{\"n_fft\":16,\"scale_eps\":0.001},"
        "\"embedding\":{\"score\":\"l2_to_centroid\",\"dim\":8,"
        "\"centroid\":[0.3362,-2.1457,-4.9178,2.8203,2.2957,2.9266,1.4891,2.7015],"
        "\"threshold\":21.4134,\"dwell_w\":3},"
        "\"sha256\":\"8d2314a285349d73cbbbf7f79da8e07d15cdfbefc1463ed6f18e0e5da863052f\"}";
    score_params_t p;
    CHECK(parse_manifest_scoring((const uint8_t *)SCORING, strlen(SCORING), &p) == true);
    // output quant must come from output{}, not feature_config's scale_eps or input's scale.
    CHECK(fabsf(p.out_scale - 0.2020f) < 1e-6f);
    CHECK(p.out_zero_point == -9);
    CHECK(fabsf(p.centroid[0] - 0.3362f) < 1e-4f);
    CHECK(fabsf(p.centroid[7] - 2.7015f) < 1e-4f);
    CHECK(fabsf(p.threshold - 21.4134f) < 1e-4f);

    // No embedding block -> rejected (PRETTY has output quant but no centroid/threshold).
    CHECK(parse_manifest_scoring((const uint8_t *)PRETTY, strlen(PRETTY), &p) == false);
}
