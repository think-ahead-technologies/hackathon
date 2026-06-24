<!-- ABOUTME: Design for CM55 loading its NPU model from the active QSPI flash slot (OTA target). -->
<!-- ABOUTME: Cross-core control protocol (CM33 owns flash+verdict, CM55 owns NPU), shadow re-wiring. -->

# CM55 loads its model from the active flash slot

**Status:** implemented (see the per-file status table at the end) — host-tested and on-target
compile-pending. The vendor-neutral logic and the CM33↔CM55 control protocol are written and the
host suite is green; nothing here has been built or run on the E84 BSP yet.
**Prerequisite:** QSPI flash bring-up (drop `HAL_FLASH_STUB`). Without real flash there is
nothing in a slot for CM55 to map, so deploys abort cleanly and CM55 runs its baked model; see the
README's "What's still on the embedded team".

## Problem

OTA is wired end-to-end on the control side (`deploy.c`, `manifest.c`, `model_contract.c`,
`shadow.c`, `meta_store.c` — all host-tested) and a deploy can be received, verified, and written
to the inactive A/B slot. But the model that actually runs never comes from a slot:

- Inference runs on **CM55 + Ethos-U55** via `npu_infer.c`, which loads `g_vela_model_data` — a
  Vela blob **baked into the firmware binary** as a C array (`model_vela_data.c`).
- The OTA loader (`model_loader.cc`, TFLM-from-flash) and the deploy orchestration
  (`device_main.c`) live on **CM33-NS**, which originally linked a no-op `model_loader_stub.c`
  (now the real `model_loader_cm33.c` driver, see §3) and does
  **not** drive the NPU.

So a deployed model lands in flash and is never executed, and the shadow promote/rollback path is
orphaned — `device_main.c` admits it can no longer evaluate shadow because inference moved cores
(see the note at the bottom of `device_main()`).

## Key insight

The model bytes do **not** need to cross the cross-core mailbox. SMIF XIP maps QSPI flash into a
shared address window (`SMIF_XIP_BASE`, `0x60000000`) that **both** cores can read. CM33 owns
*writing* the slot (it has the SMIF/PSA HAL and the NATS feed); CM55 just **XIP-reads the same
offset** and hands the pointer to ml-middleware.

What crosses the boundary is small: a **control command + slot offset + scoring params** one way,
and the **scores** the other. This also gives a clean split that resolves the orphaned shadow path:

> **Data plane on CM55, verdict plane on CM33.** CM55 *produces* the active and candidate scores;
> CM33 keeps *deciding* with the unchanged, tested `shadow.c`.

## Architecture

```
        CM33-NS (control plane)                 CM55 (data plane)
   ┌──────────────────────────────┐      ┌──────────────────────────────┐
   │ NATS Contract C deploy        │      │ BMI270 → features_from_accel  │
   │ deploy.c reassemble → slot    │      │ npu runtime: g_active,        │
   │ hal_flash_program (SMIF write)│      │              g_candidate      │
   │ hal_verify_signature (PSA)    │      │ score_distance per-role params│
   │ meta_store atomic flip        │      └───────────┬──────────────────┘
   │ shadow_observe / shadow_decide│                  │
   └───────┬──────────────────▲───┘                  │
           │ ctrl cmd          │ scores               │
           ▼                   │                      │
   ┌───────────────────────────┴──────────────────────┴───┐
   │  m33_m55_shared SOCMEM (0x262fc000)                   │
   │   shared_score_t      (CM55 → CM33: active+candidate) │
   │   shared_model_ctrl_t (CM33 → CM55: LOAD/PROMOTE)     │
   └───────────────────────────────────────────────────────┘
                          │ both cores XIP-read
                          ▼
   ┌───────────────────────────────────────────────────────┐
   │  QSPI NOR @ SMIF XIP base 0x60000000                  │
   │   slot A model flatbuffer | slot B model flatbuffer    │
   └───────────────────────────────────────────────────────┘
```

## 1. Extend the mailbox with a control channel

`shared_score.h` today is one-way (CM55 → CM33: score + features). Add a second struct for the
CM33 → CM55 direction, and widen the score struct to carry the candidate during shadow. Both live
at fixed offsets in the existing `m33_m55_shared` SOCMEM region.

```c
// CM33 -> CM55 model control. CM33 bumps cmd_seq to issue; CM55 sets ack_seq when done.
typedef enum { MC_NONE = 0, MC_LOAD_ACTIVE, MC_LOAD_CANDIDATE, MC_PROMOTE, MC_CLEAR_CAND } mc_cmd_t;

// Scoring travels WITH the model (see §4): a deployed model has its own output quantization and
// its own embedding space, so the old compiled-in centroid is meaningless against it.
typedef struct {
    float    centroid[SCORE_EMBED_DIM]; // per-unit healthy baseline (from manifest)
    float    threshold;                 // alert quantile
    float    out_scale;                 // model output dequant
    int32_t  out_zero_point;
} score_params_t;

typedef struct {
    volatile uint32_t     cmd_seq;       // CM33 increments to post a command
    volatile uint32_t     cmd;           // mc_cmd_t
    volatile uint32_t     target_offset; // QSPI offset of the slot to load (XIP = base + offset)
    volatile uint32_t     target_len;    // flatbuffer length (cache-invalidate range)
    volatile score_params_t params;      // scoring for the slot being loaded
    volatile uint32_t     ack_seq;       // CM55 sets = cmd_seq when handled
    volatile int32_t      status;        // 0 = OK, <0 = load failed
} shared_model_ctrl_t;

// Widen the existing score struct to carry the candidate while shadowing:
typedef struct {
    volatile uint32_t magic, seq;
    volatile float    score;             // ACTIVE model score (as today)
    volatile float    candidate_score;   // valid only while shadowing
    volatile uint32_t have_candidate;    // 1 while a candidate is loaded
    volatile int8_t   features[FEAT_OUT_LEN];
} shared_score_t;
```

**Implementation note (layering).** `score_params_t` lives in `score.h`, not `shared_score.h`. It
is a scoring concept, and keeping it there means the pure host-tested scorer (`score.c`) does not
have to depend on the cross-core SOCMEM mailbox header. `shared_score.h` `#include`s `score.h` and
embeds `score_params_t` in `shared_model_ctrl_t`. The mailbox also widened with a fixed control
offset (`SHARED_CTRL_OFFSET = 0x1000`, 4 KB past the score struct) so the two overlays don't
collide as the score struct grows.

## 2. CM55 — make the runtime reload-capable

Refactor `npu_infer.c` to hold **two** model handles + arenas (mirroring what `model_loader.cc`
already does for TFLM, but for `mtb_ml_*`), and load from an XIP pointer instead of the baked array.
CM55 has multi-MB SOCMEM, so two ~128 KB arenas are comfortable.

```c
static mtb_ml_model_t *g_active, *g_candidate;
static score_params_t  g_active_p, g_cand_p;
alignas(16) static uint8_t g_arena_a[NPU_ARENA_BYTES], g_arena_c[NPU_ARENA_BYTES];

bool npu_load(mtb_ml_model_t **dst, uint8_t *arena, uint32_t off, uint32_t len) {
    SCB_InvalidateDCache_by_Addr((void *)(SMIF_XIP_BASE + off), len);   // see §5
    mtb_ml_model_bin_t bin = {
        .name       = "wear",
        .model_bin  = (const uint8_t *)(SMIF_XIP_BASE + off),           // XIP, not g_vela_model_data
        .model_size = len,
        .arena_size = NPU_ARENA_BYTES,
    };
    return mtb_ml_model_init(&bin, arena, dst) == CY_RSLT_SUCCESS;
}
```

The CM55 task loop (`firmware-app/proj_cm55/main.c`) gains a command poll at the top of each window:

```c
for (;;) {
    if (CTRL->cmd_seq != last_seq) {            // CM33 issued a command
        switch (CTRL->cmd) {
        case MC_LOAD_ACTIVE:    npu_load(&g_active, g_arena_a, CTRL->target_offset, CTRL->target_len);
                                g_active_p = CTRL->params; break;
        case MC_LOAD_CANDIDATE: npu_load(&g_candidate, g_arena_c, CTRL->target_offset, CTRL->target_len);
                                g_cand_p = CTRL->params; break;
        case MC_PROMOTE:        swap(&g_active, &g_candidate); g_active_p = g_cand_p;
                                mtb_ml_model_deinit(g_candidate); g_candidate = NULL; break;
        case MC_CLEAR_CAND:     mtb_ml_model_deinit(g_candidate); g_candidate = NULL; break;
        }
        CTRL->status  = /* result */ 0;
        CTRL->ack_seq = CTRL->cmd_seq;
        last_seq      = CTRL->cmd_seq;
    }

    sample_window(accel_window);
    features_from_accel(accel_window, FEAT_WINDOW_SAMPLES, features);

    float s = score_with(npu_run(g_active, features), &g_active_p);
    SHARED_SCORE->score = score_dwell(&dwell, s);
    if (g_candidate) {                          // run the candidate on the SAME window
        SHARED_SCORE->candidate_score = score_with(npu_run(g_candidate, features), &g_cand_p);
        SHARED_SCORE->have_candidate  = 1;
    } else {
        SHARED_SCORE->have_candidate  = 0;
    }
    mirror_features();
    SHARED_SCORE->seq++;
    SHARED_SCORE->magic = SHARED_SCORE_MAGIC;
}
```

**Fallback:** if CM33 never posts a valid `LOAD_ACTIVE` (virgin board, empty flash), CM55 falls
back to the baked-in `g_vela_model_data` + compiled-in `score.c` constants — i.e. today's behavior
is the factory slot-A image. This design is additive and never a regression.

## 3. CM33 — drive CM55 instead of the stub

`model_loader_stub.c` was renamed to `model_loader_cm33.c` and is now a real implementation that
**drives the mailbox** (CM33 never touches the NPU):

```c
bool model_loader_load_active(slot_id_t slot) {
    model_meta_t m;
    if (!hal_meta_read(&m)) return false;
    const slot_meta_t *s = &m.slot[slot];
    if (!s->valid || s->len == 0)   // no flash model: CM55 keeps its baked-in boot model
        return true;
    score_params_t p; score_default_params(&p);   // TODO(step 3): real params from the manifest
    return ctrl_post(MC_LOAD_ACTIVE, s->flash_offset, s->len, &p) == 0;
}
bool model_loader_load_candidate(slot_id_t slot) { /* same, MC_LOAD_CANDIDATE; false if no model */ }
```

`ctrl_post` writes the command struct, bumps `cmd_seq` (after a `__DMB()` so CM55 sees the fields
first), then spins (yielding via `hal_sleep_ms`) until `ack_seq == cmd_seq`, returning `status == 0`
or `-1` on timeout. `model_loader_infer()` no longer computes anything — it reads
`SHARED_SCORE->score` / `candidate_score` / `have_candidate` (CM55 is the only thing that runs the
NPU), returning 0 until `magic` confirms CM55 is live.

The empty-slot short-circuit matters for the connectivity-first build (`HAL_FLASH_STUB`): there
`hal_meta_read` synthesizes slot A active with `len == 0`, so `load_active` returns true without a
command and CM55 runs the baked model — the device boots and publishes exactly as today.

**Scoring params (done — step 3).** `slot_meta_t` now carries a `score_params_t`. At deploy time
`device_main` parses it from the manifest (`parse_manifest_scoring`) and persists it with the slot;
`load_active` / `load_candidate` post the slot's stored params (falling back to
`score_default_params()` only for a pre-params slot, detected by `out_scale == 0`). Because the
params live in the persisted metadata, a load at boot also scores correctly — no separate setter and
no dependence on stale build-time constants. CM55 *applies* these params in its task loop
(`cm55_apply_ctrl`), which polls `SHARED_MODEL_CTRL` and loads/promotes via the `npu_*` API.

The orphaned `evaluate_shadow()` in `device_main.c` gets re-wired. Instead of CM33 invoking both
models itself (which it no longer can), the steady-state loop pulls **both** scores from the mailbox
via `model_loader_infer()` and feeds the *unchanged, tested* shadow logic:

```c
float candidate_score = 0.0f; bool have_candidate = false;
float score = model_loader_infer(NULL, 0, &candidate_score, &have_candidate);
publish_score(sock, score);
if (g_shadowing && have_candidate)
    evaluate_shadow(score, candidate_score);
```

`evaluate_shadow()` runs `shadow_observe` / `shadow_decide` and acts on the verdict. The
metadata-vs-runtime ordering matters: flip the persistent metadata **first** (so a reboot loads the
new active), then make CM55's live model match:

```c
case SHADOW_PROMOTE:
    if (hal_meta_read(&meta) && meta_promote(&meta, g_candidate_slot) &&
        hal_meta_write(&meta) && model_loader_promote()) {   // MC_PROMOTE: pointer-swap on CM55
        printf("[shadow] promoted slot %d\n", g_candidate_slot);
    }
    break;                                                   // MC_PROMOTE clears CM55's candidate
case SHADOW_ROLLBACK:
    model_loader_clear_candidate();                          // MC_CLEAR_CAND; keep incumbent
    break;
```

This adds one call to the `model_loader.h` interface — `bool model_loader_promote(void)` (the
CM33 driver posts `MC_PROMOTE`; on CM55 it is the `npu_promote_candidate()` pointer-swap, not a flash
reload). Promote clears the candidate role as part of the swap, so only the rollback path issues an
explicit `MC_CLEAR_CAND`.

`deploy_finalize()` already calls `model_loader_load_candidate()` and sets `g_shadowing = true` —
that path now actually reaches CM55. This resolves the note that used to sit at the end of
`device_main()` ("shadowing belongs on CM55"): CM55 *produces* both scores, CM33 keeps *deciding*.

## 4. Scoring params must ship with the model

`SCORE_CENTROID` / `SCORE_THRESHOLD` and the output dequant remain build constants in `score.c`, but
only as the boot fallback. A newly deployed model has its **own** output quantization and its **own**
embedding space — the old centroid is meaningless against it. So `parse_manifest_scoring()` extracts
the `embedding` centroid + `threshold` and the output `scale` / `zero_point` into a `score_params_t`;
`device_main` persists it in `slot_meta_t` at deploy time and the CM33 driver posts it with each
`MC_LOAD_*`. CM55 scores each role with that role's params. Scoring becomes **model-specific** instead
of build-specific, which is what makes the swap mean anything.

`score.c` changes from compiled-in constants to taking `score_params_t` as an argument
(`score_distance_with`); the old `score_distance` delegates to it with `score_default_params()`, so
behavior is byte-identical and the cross-language harness stays valid.

> **Pre-existing staleness (surfaced, not introduced).** `make score-cross` currently FAILS
> (`max|Δdistance| ≈ 16.4`) — and it fails identically on the unmodified `score.c`, so the refactor
> is provably behavior-preserving. The cause is that the compiled-in `SCORE_CENTROID` /
> `SCORE_OUT_SCALE` constants are stale relative to the regenerated `wear_detector/export/build/
> baseline.json` (the recent labeled-dataset / multimodal-trainer work moved the baseline). This is
> exactly what model-carried params fix: once CM33 parses centroid + output quant from the deployed
> manifest and posts them (step 3 below), the device stops depending on stale build-time constants.
> Refreshing the baked-in defaults is a separate change and is intentionally out of scope here.

### Front-end constraint (call this out loudly)

`features_from_accel` (FFT, n_fft, hop, 40-band filterbank) is compiled into CM55 and **fixed**. A
deployed model **must** keep the `[1,49,40,1]` int8 front-end — which `contract_validate()` already
enforces and **refuses** otherwise (`device_main.c`). OTA can swap weights / quantization / centroid,
**not** the feature pipeline. If the pipeline ever needs to change, that is a firmware reflash, not a
model push. The manifest's `feature_config` must match the baked front-end exactly.

## 5. Cache + SMIF coherence (the easy-to-miss bug)

- After CM33 SMIF-programs the slot, CM55's D-cache may hold stale lines for that XIP range →
  `SCB_InvalidateDCache_by_Addr(SMIF_XIP_BASE + off, len)` **before** `mtb_ml_model_init` (shown in
  §2). Cleanest alternative: mark the XIP region non-cacheable in the CM55 MPU.
- SMIF must be in **memory-mapped (XIP) mode**, not command mode, when CM55 reads. The cmd/ack
  handshake serializes this: CM33 only posts `LOAD_*` **after** the write + verify completes and SMIF
  is back in XIP mode.
- **No erase/read race by construction:** CM33 only ever writes the **INACTIVE** slot; CM55 only
  reads the **ACTIVE** slot (plus the candidate, which is loaded only *after* the write finishes).
  They never touch the same offset concurrently.

## 6. Boot sequence

1. CM33 brings up SMIF + PSA; `hal_meta_read()` → active slot.
2. CM33 posts `MC_LOAD_ACTIVE(active_offset, params)` **before** CM55 starts producing — or CM55
   spins on the first valid `cmd_seq`, falling back to the baked model after a timeout (§2).
3. CM55 loads from the slot, starts producing scores; `magic` flips to `SHARED_SCORE_MAGIC`.
4. Steady state: CM33 forwards `score` to NATS; on a Contract C deploy it writes the inactive slot,
   posts `LOAD_CANDIDATE`, shadows, then `PROMOTE` / `CLEAR_CAND`.

## Files touched

| File | Change | Status |
|---|---|---|
| `include/score.h` / `src/score.c` | add `score_params_t`, `score_default_params`, `score_distance_with`; `score_distance` delegates | ✅ done |
| `include/shared_score.h` | add `mc_cmd_t`, `shared_model_ctrl_t` (+ `SHARED_CTRL_OFFSET`); widen `shared_score_t` | ✅ done |
| `src/npu_infer.c` / `.h` | two model handles; `npu_load_slot(xip_off)`; promote/clear; per-role scoring | ✅ done |
| `model_loader_stub.c` → `model_loader_cm33.c` | drive ctrl mailbox; spin on ack; `infer` reads mailbox | ✅ done |
| `include/model_loader.h` | add `model_loader_promote()` | ✅ done |
| `src/manifest.c` / `include/manifest.h` | `parse_manifest_scoring()` — output quant + centroid/threshold → `score_params_t` | ✅ done |
| `include/model_slot.h` | persist `score_params_t` in `slot_meta_t` (transparent to `meta_store`) | ✅ done |
| `src/device_main.c` | re-wire `evaluate_shadow()` to mailbox scores + `MC_PROMOTE`; parse + persist scoring at deploy | ✅ done |
| `firmware-app/proj_cm33_ns/Makefile` | rename source; add `score.c` to the CM33 build | ✅ done |
| `firmware-app/proj_cm55/main.c` | command poll (`MC_*` via `npu_*`) + dual-score publish in the task loop | ✅ done |

## What stays unchanged

The tested decision logic (`shadow.c`, `model_slot.c`, `meta_store.c`, `deploy.c`,
`model_contract.c`) and the NATS / Contract-C wire path. This is deliberately a *plumbing* change
between cores plus making scoring model-carried — it does not touch the parts already proven by the
host test suite or the cross-language wire test.
