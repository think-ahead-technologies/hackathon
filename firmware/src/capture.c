// ABOUTME: Contract E capture command parser + segment watch-set — directed data gathering.
// ABOUTME: Bounded scanning over the JSON bytes (no NUL assumption); pure, host-tested.

#include <stdio.h>
#include <string.h>

#include "capture.h"
#include "json_scan.h"

// Extract an optional quoted string field; leave `out` as "" if the key is absent.
static void parse_quoted_opt(const char *j, size_t len, const char *key, char *out, size_t cap) {
    out[0] = '\0';
    const char *p = json_value_after(j, len, key);
    if (p) {
        json_parse_quoted(p, j + len, out, cap);
    }
}

// True when the value just after a "key" is the literal `true` (within [p, end)).
static bool value_is_true(const char *p, const char *end) {
    while (p < end && json_is_ws(*p)) {
        p++;
    }
    // cppcheck-suppress knownConditionTrueFalse  // FP: cppcheck assumes the ws-skip ran to
    // `end`; on the {"stop":true} path p lands on 't' with >=4 bytes left (test_capture.c).
    return (size_t)(end - p) >= 4 && memcmp(p, "true", 4) == 0;
}

bool capture_parse_cmd(const uint8_t *buf, size_t len, capture_cmd_t *out) {
    const char *j = (const char *)buf;
    memset(out, 0, sizeof(*out));

    // A stop command ({"stop":true}) clears the watch-set; it carries no segment.
    const char *p = json_value_after(j, len, "\"stop\"");
    // cppcheck-suppress knownConditionTrueFalse  // FP propagated from value_is_true (see above).
    if (p && value_is_true(p, j + len)) {
        out->stop = true;
        return true;
    }

    // Otherwise it's an add command: a segment is required (recording is segment-driven).
    parse_quoted_opt(j, len, "\"segment\"", out->segment, sizeof(out->segment));
    if (out->segment[0] == '\0') {
        return false;
    }
    parse_quoted_opt(j, len, "\"request_id\"", out->request_id, sizeof(out->request_id));
    parse_quoted_opt(j, len, "\"label\"", out->label, sizeof(out->label));
    return true;
}

void capture_set_reset(capture_set_t *s) {
    memset(s, 0, sizeof(*s));
}

// Find a watched segment by id, or NULL.
static capture_entry_t *find_segment(capture_set_t *s, const char *segment) {
    for (uint32_t i = 0; i < s->count; i++) {
        if (strcmp(s->seg[i].segment, segment) == 0) {
            return &s->seg[i];
        }
    }
    return NULL;
}

void capture_apply(capture_set_t *s, const capture_cmd_t *c) {
    if (c->stop) {
        capture_set_reset(s);  // stop listening
        return;
    }
    // Re-adding a watched segment refreshes its label; a new one appends if there's room.
    capture_entry_t *e = find_segment(s, c->segment);
    if (e == NULL) {
        if (s->count >= CAPTURE_MAX_SEGMENTS) {
            return;  // set full — ignore (the operator can stop and re-issue a tighter set)
        }
        e = &s->seg[s->count++];
    }
    snprintf(e->segment, sizeof(e->segment), "%s", c->segment);
    snprintf(e->label, sizeof(e->label), "%s", c->label);
    snprintf(e->request_id, sizeof(e->request_id), "%s", c->request_id);
}

bool capture_listening(const capture_set_t *s) {
    return s->count > 0;
}

const capture_entry_t *capture_match(const capture_set_t *s, const char *current_segment) {
    if (current_segment == NULL || current_segment[0] == '\0') {
        return NULL;  // position unknown -> not on any watched segment
    }
    return find_segment((capture_set_t *)s, current_segment);
}

void capture_advance(capture_set_t *s) {
    s->seq++;
}
