// ABOUTME: Tests for capture_parse_cmd + the segment watch-set (Contract E).

#include <string.h>

#include "capture.h"
#include "test_util.h"

void run_capture_tests(void) {
    // An add command parses request_id / label / segment; stop is false.
    const char *add =
        "{\"request_id\":\"cap-1\",\"label\":\"bearing wear\",\"segment\":\"seg-4\"}";
    capture_cmd_t c;
    CHECK(capture_parse_cmd((const uint8_t *)add, strlen(add), &c) == true);
    CHECK(c.stop == false);
    CHECK_STR_EQ(c.request_id, "cap-1");
    CHECK_STR_EQ(c.label, "bearing wear");
    CHECK_STR_EQ(c.segment, "seg-4");

    // label / request_id are optional; only segment is required for an add.
    const char *minimal = "{\"segment\":\"seg-2\"}";
    capture_cmd_t c2;
    CHECK(capture_parse_cmd((const uint8_t *)minimal, strlen(minimal), &c2) == true);
    CHECK_STR_EQ(c2.segment, "seg-2");
    CHECK_STR_EQ(c2.label, "");

    // An add with no segment is rejected (recording is segment-driven).
    const char *no_seg = "{\"label\":\"healthy\"}";
    capture_cmd_t cbad;
    CHECK(capture_parse_cmd((const uint8_t *)no_seg, strlen(no_seg), &cbad) == false);

    // A stop command parses regardless of other fields.
    const char *stop = "{\"stop\":true}";
    capture_cmd_t cs;
    CHECK(capture_parse_cmd((const uint8_t *)stop, strlen(stop), &cs) == true);
    CHECK(cs.stop == true);

    // Watch-set: commands accumulate one-by-one; the device records while on any watched segment.
    capture_set_t s;
    capture_set_reset(&s);
    CHECK(capture_listening(&s) == false);
    CHECK(capture_match(&s, "seg-4") == NULL);

    capture_apply(&s, &c);   // add seg-4 (bearing wear)
    CHECK(capture_listening(&s) == true);
    capture_apply(&s, &c2);  // add seg-2
    CHECK(s.count == 2u);

    // On a watched segment -> match carries that segment's label; off it -> no match.
    const capture_entry_t *e = capture_match(&s, "seg-4");
    CHECK(e != NULL);
    CHECK_STR_EQ(e->label, "bearing wear");
    CHECK_STR_EQ(e->segment, "seg-4");
    CHECK(capture_match(&s, "seg-2") != NULL);
    CHECK(capture_match(&s, "seg-9") == NULL);  // not watched
    CHECK(capture_match(&s, "") == NULL);       // position unknown

    // Re-adding a watched segment refreshes its label without growing the set.
    capture_cmd_t refresh = {.segment = "seg-4", .label = "imbalance"};
    capture_apply(&s, &refresh);
    CHECK(s.count == 2u);
    CHECK_STR_EQ(capture_match(&s, "seg-4")->label, "imbalance");

    // seq advances per recorded window, independent of which segment.
    capture_advance(&s);
    capture_advance(&s);
    CHECK(s.seq == 2u);

    // A stop command clears the whole set: listening stops, nothing matches.
    capture_apply(&s, &cs);
    CHECK(capture_listening(&s) == false);
    CHECK(s.count == 0u);
    CHECK(capture_match(&s, "seg-4") == NULL);
}
