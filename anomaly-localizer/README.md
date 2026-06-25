# anomaly-localizer

Joins the on-device anomaly score with the localizer's floor-map fix: every anomaly the board reports
is pinned to **where the box was on the loop** when it fired. One enriched anomaly in → one located
anomaly out, classified `"inference"` so it crosses the Vector boundary like the bare anomaly.

## Topics

| Direction | Subject | Contract |
|---|---|---|
| in  | `edge.anomaly.<line>.<container>`   | [anomaly-data](../nats-topics/anomaly-data/contract.md) — inference score |
| in  | `edge.position.<line>.<container>`  | [positinal-data](../nats-topics/positinal-data/contract.md) — floor-map fix |
| out | `edge.localized-anomaly.<line>.<container>` | [localized-anomaly-data](../nats-topics/localized-anomaly-data/contract.md) |

## How it works

Last-known-position join: the service keeps the most recent `edge.position` fix per
`<line>.<container>` and stamps `segment`/`x`/`y`/`pos_t_host_us`/`pos_age_ms` onto each incoming
`edge.anomaly`. All anomaly fields pass through unchanged; `data_classification` is forced to
`"inference"` and `bytes` is recomputed for Vector's egress audit.

- **Streaming, fire-and-forget.** No windowed time alignment — `pos_age_ms` (`ts - pos_t_host_us`)
  exposes how stale the stamped fix is so a consumer can discount it.
- **Anomaly is never dropped.** No fix yet for a stream → location fields go out `null`. A bare
  anomaly beats a lost cycle.

## Config (env)

- `ANOMALY_SUBJECT` / `POSITION_SUBJECT` — input subjects (default `edge.anomaly.*.*` /
  `edge.position.*.*`).
- `OUT_PREFIX` — output namespace (default `edge.localized-anomaly`).
- `NATS_URL`, `NATS_NKEY_SEED`, `NATS_CA_FILE`, `NATS_TLS_HOSTNAME` — same auth knobs as the splitter.

## Test

Unit (pure join logic, no NATS):

```
pip install -r requirements.txt
pytest test_anomaly_localizer.py
```

End-to-end (runs the real service in-process against a live NATS — boots `main.main()`, feeds
`edge.position` + `edge.anomaly`, asserts the `edge.localized-anomaly` join). Only needs NATS, not a
separate container:

```
docker compose up -d nats        # or any nats://host:4222
python e2e_replay.py             # exit 0 PASS, 1 FAIL, 2 if no NATS reachable
NATS_URL=nats://host:4222 python e2e_replay.py   # custom server
```

Scenario covered: anomaly before any fix → location `null` (never dropped); anomaly after a fix →
located with `pos_age_ms`; a newer fix → last-known-position wins.
