// ABOUTME: Bounded JSON field scanning shared by the Contract A/E parsers (no NUL assumption).
// ABOUTME: Pure helpers — locate a key's value, test whitespace, copy a quoted string.

#ifndef JSON_SCAN_H
#define JSON_SCAN_H

#include <stdbool.h>
#include <stddef.h>

// Bounded substring search (no NUL assumption on the input). NULL if not found.
const char *json_mem_find(const char *hay, size_t hlen, const char *needle);

// True for JSON insignificant whitespace (space, tab, CR, LF).
int json_is_ws(char c);

// Position just after `"key" :` (whitespace skipped), within [hay, hay+hlen); NULL if absent.
const char *json_value_after(const char *hay, size_t hlen, const char *quoted_key);

// Copy a quoted string value into out (NUL-terminated, truncated to cap). Returns true iff the
// closing quote was found within [p, end).
bool json_parse_quoted(const char *p, const char *end, char *out, size_t cap);

#endif  // JSON_SCAN_H
