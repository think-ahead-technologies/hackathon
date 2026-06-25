// ABOUTME: Minimal dependency-free assert harness for the host-side firmware logic tests.
// ABOUTME: CHECK macros tally into globals; the runner returns non-zero if any check fails.

#ifndef TEST_UTIL_H
#define TEST_UTIL_H

#include <stdio.h>
#include <string.h>

extern int g_checks;
extern int g_failures;

#define CHECK(cond)                                                       \
    do {                                                                  \
        g_checks++;                                                       \
        if (!(cond)) {                                                    \
            g_failures++;                                                 \
            printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);      \
        }                                                                 \
    } while (0)

#define CHECK_STR_EQ(a, b)                                                \
    do {                                                                  \
        g_checks++;                                                       \
        if (strcmp((a), (b)) != 0) {                                      \
            g_failures++;                                                 \
            printf("  FAIL %s:%d  \"%s\" != \"%s\"\n",                    \
                   __FILE__, __LINE__, (a), (b));                         \
        }                                                                 \
    } while (0)

void run_model_slot_tests(void);
void run_model_contract_tests(void);
void run_shadow_tests(void);
void run_nats_proto_tests(void);
void run_meta_store_tests(void);
void run_manifest_tests(void);
void run_deploy_tests(void);
void run_capture_tests(void);
void run_wear_fault_tests(void);
void run_bearing_rf_tests(void);

#endif  // TEST_UTIL_H
