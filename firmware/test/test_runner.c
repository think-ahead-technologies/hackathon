// ABOUTME: Host test entrypoint — runs every pure-logic suite and tallies pass/fail.
// ABOUTME: Returns non-zero on any failure so `make test` fails loudly in CI.

#include "test_util.h"

int g_checks = 0;
int g_failures = 0;

int main(void) {
    printf("model_slot\n");
    run_model_slot_tests();
    printf("model_contract\n");
    run_model_contract_tests();
    printf("shadow\n");
    run_shadow_tests();
    printf("nats_proto\n");
    run_nats_proto_tests();
    printf("meta_store\n");
    run_meta_store_tests();
    printf("manifest\n");
    run_manifest_tests();
    printf("deploy\n");
    run_deploy_tests();
    printf("capture\n");
    run_capture_tests();

    printf("\n%d checks, %d failures\n", g_checks, g_failures);
    return g_failures ? 1 : 0;
}
