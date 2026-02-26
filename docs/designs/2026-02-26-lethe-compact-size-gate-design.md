# Lethe — Compact-Size Gate + Standard Compact Fallback Design

**Date:** 2026-02-26  
**Status:** Draft approved for implementation planning  
**Scope:** Lethe compactor flow, config precedence, communication routing, fallback behavior

---

## 1) Goal

Add a post-splice size gate to Lethe so surgical compaction is only committed when the resulting session remains below a configured context threshold. If not, route to a standard compact strategy (mode-dependent), while preserving Lethe's existing semantic decision pipeline.

This avoids relaunching already context-saturated sessions after a sip-style compaction.

---

## 2) Non-Goals

- No replacement of Lethe semantic decisioning with script-only cut logic.
- No new CLI flag for compact threshold.
- No rename of existing permission keys or permission precedence behavior.
- No change to Lethe's core segment rules beyond estimate divisor normalization.

---

## 3) Core Pipeline (Full, Not Condensed)

Existing Lethe phases remain intact, with a staged splice gate inserted after cut-plan creation:

1. Parse arguments and determine run mode.
2. Detect interaction capability mode:
   - `AskUserQuestion` available -> interactive mode
   - `AskUserQuestion` unavailable -> non-interactive mode
3. Resolve configuration (permissions + compact size threshold).
4. Phase 1 kill (only with `--orchestrate`).
5. Phase 2 analyze (`lethe-analyze.py` -> manifest).
6. Phase 3 decide (Claude creates cut-plan + summary sidecars).
7. Build working candidate from original JSONL:
   - `cp <session>.jsonl <session>.jsonl.working`
8. Run splice on working file (not original):
   - `python3 scripts/lethe-splice.py <SESSION_ID> --cut-plan ... --jsonl-path <session>.jsonl.working --no-backup`
9. Parse splice result and evaluate gate:
   - use `new_tokens_est`
   - compare to resolved compact threshold
10. Gate pass:
   - create Lethe-specific original backup: `<session>.jsonl.lethe-<ts>-<rand>`
   - promote working candidate over original (`mv`)
   - continue normal post-splice path
11. Gate fail:
   - discard working copy
   - route to standard compact behavior according to mode matrix (Section 6)

Important: Lethe semantic decisions remain in Phase 3. Scripts remain structural/synthesis only.

---

## 4) Config: `LETHE_COMPACT_SIZE`

Add new config key with the same precedence model as existing Lethe config.

### Key mapping

- Env var: `LETHE_COMPACT_SIZE`
- `.lethe_config` key: `compact_size`
- Default: `400000`

### Resolution order (per key, first match wins)

1. Environment variable
2. Project-level `.lethe_config`
3. User-level `.lethe_config`
4. Hardcoded default

### Validation

- Must parse as positive integer.
- Invalid values warn on stderr and fall back to `400000`.
- No hard failure due to malformed threshold config.

No new `--flag` is added for this threshold.

---

## 5) Estimation Standardization

Normalize Lethe token estimation to `/3` everywhere (currently mixed `/4` assumptions in utility/splice estimates).

### Requirement

- All gate-relevant and reported token estimates use the same divisor: `chars / 3`.
- Update estimate code paths consistently so reduction stats and gate decision share one math model.

This is intentionally conservative.

---

## 6) Communication Routing (Overarching)

All user-facing outputs route through a single messaging policy based on capability detection:

- Interactive mode (`AskUserQuestion` available):
  - normal direct prompts/output
- Non-interactive mode (`AskUserQuestion` unavailable):
  - route user-facing notifications through `SendMessage` instead of direct prompt-style UX

This applies globally, including success messages, launch notices, fallback notices, and recommendations.

---

## 7) Gate-Fail Behavior Matrix

### A. Orchestrated flow (`--orchestrate` provided)

- Gate fail -> run standard compact fallback flow.

### B. Manual flow (`--orchestrate` absent), interactive mode

- Gate fail -> report that sip compaction result is still too large.
- Recommend standard compact command/flow.
- Do not auto-run fallback.

### C. Manual flow (`--orchestrate` absent), non-interactive mode

- Gate fail -> silently default to launch path using standard compact fallback flow.
- Route status/progress to `SendMessage`.

---

## 8) Standard Compact Fallback Protocol (Lethe)

When fallback is selected, use the same stable pattern already validated in Souffleur semantics:

1. Capture baseline JSONL state (line count) for target session.
2. Launch compact session:
   - `claude --resume <SESSION_ID> "compact"`  
   (no slash prefix in command text for this protocol)
3. Watch JSONL lines after baseline for compact completion marker (`compact_boundary`).
4. On completion, terminate compact session.
5. Relaunch resumed session with configured prompt/permission policy.

Retry policy:
- 1 retry on compact failure/timeout.
- Second failure -> fail closed (no further relaunch attempts in that cycle).

---

## 9) File-Level Change Plan

### Scripts

- `skills/lethe/scripts/lethe_utils.py`
  - add `compact_size` config resolution + validation
  - normalize estimator math to `/3`
- `skills/lethe/scripts/lethe-config.py`
  - include `compact_size` in JSON output contract
- `skills/lethe/scripts/lethe-splice.py`
  - no structural staging feature required
  - continue using `--jsonl-path` on working copy
  - align estimate calculations to `/3`

### Skill docs/protocol

- `skills/lethe/SKILL.md`
  - add capability-based communication routing rule
  - define non-interactive message routing requirement
- `skills/lethe/references/compactor.md`
  - insert staged working-copy gate sequence
  - define `.jsonl.lethe-<ts>-<rand>` backup naming on gate pass
  - define gate-fail mode matrix
  - add standard compact fallback protocol + retry/fail-closed semantics

### README

- update behavior description for staged splice gate and fallback path
- document `LETHE_COMPACT_SIZE` / `compact_size`
- document communication routing behavior in non-interactive sessions

### Tests

- extend `tests/test_config.py` for `compact_size` resolution + validation
- extend/adjust splice tests to assert `/3`-based estimates
- add fallback protocol tests at doc/protocol level where executable tests are not practical

---

## 10) Failure Semantics

- If staged splice fails verification: preserve original, fail current path.
- If gate parse/comparison fails: treat as error, preserve original, do not promote working file.
- If fallback compact fails twice: fail closed.
- In all failure cases, cleanup working artifacts where safe, while preserving declared backups.

Safety invariant:
- Original JSONL is never overwritten unless gate passes and backup is created successfully.

---

## 11) Acceptance Criteria

- Lethe keeps analyze/decide/splice semantics intact.
- Gate uses staged working copy, never direct original overwrite before gate pass.
- Threshold is configurable via `LETHE_COMPACT_SIZE` / `compact_size` with existing precedence.
- Token estimation uses `/3` consistently.
- Gate fail behavior matches mode matrix exactly.
- Non-interactive sessions route user-facing messages via `SendMessage`.
- Standard compact fallback uses baseline -> compact -> watch -> kill -> relaunch with 1 retry then fail closed.
- Backup naming on promotion uses `.jsonl.lethe-<ts>-<rand>`.

