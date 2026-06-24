// ABOUTME: Contract E capture command — parse commands + keep a watch-set of segments to record.
// ABOUTME: Commands arrive one-by-one and accumulate; a stop command clears the set. Host-tested.

#ifndef CAPTURE_H
#define CAPTURE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Most segments the device will watch at once. Commands arrive one-by-one on the capture topic
// and accumulate into this set until a stop command clears it.
#define CAPTURE_MAX_SEGMENTS 8

// One parsed command off capture.<line>.<container>.cmd. Two shapes:
//   - add:  {request_id, label, segment}  -> add `segment` to the watch-set (records while the
//           device is on it). `label` is "healthy" for clean baseline or a fault class.
//   - stop: {stop: true}                  -> stop listening: clear the whole watch-set.
typedef struct {
    bool stop;
    char request_id[32];
    char label[32];
    char segment[32];
} capture_cmd_t;

// One watched segment and the metadata to stamp onto windows recorded while on it.
typedef struct {
    char segment[32];
    char label[32];
    char request_id[32];
} capture_entry_t;

// The accumulated watch-set. The device records a window whenever its current segment is in here.
typedef struct {
    capture_entry_t seg[CAPTURE_MAX_SEGMENTS];
    uint32_t        count;
    uint32_t        seq;  // monotonic index across all recorded windows (for the sink payload)
} capture_set_t;

// Parse a Contract E command. Targets the known schema (trusted control plane) rather than
// defending against arbitrary JSON, like parse_manifest(). Sets out->stop for a stop command.
// For an add command, returns false if `segment` is missing; `label`/`request_id` default to "".
bool capture_parse_cmd(const uint8_t *buf, size_t len, capture_cmd_t *out);

// Clear the watch-set (stop listening) and reset the sequence counter.
void capture_set_reset(capture_set_t *s);

// Apply one parsed command: stop clears the set; otherwise add/refresh the segment. Adding a
// segment already present updates its label/request_id; adding when full is a no-op (returns the
// command's effect via the set state). Safe to call every time a command arrives.
void capture_apply(capture_set_t *s, const capture_cmd_t *c);

// True while at least one segment is being watched.
bool capture_listening(const capture_set_t *s);

// Return the watch-set entry for `current_segment` (so its label/request_id stamp the window), or
// NULL when the device is not on any watched segment (or position is unknown: "" / NULL).
const capture_entry_t *capture_match(const capture_set_t *s, const char *current_segment);

// Advance the sequence counter after a recorded window is published.
void capture_advance(capture_set_t *s);

#endif  // CAPTURE_H
