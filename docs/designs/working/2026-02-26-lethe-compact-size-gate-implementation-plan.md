# Lethe — Compact-Size Gate Implementation Plan

**Date:** 2026-02-26  
**Status:** Ready for implementation  
**Depends on:** `docs/designs/2026-02-26-lethe-compact-size-gate-design.md`

---

## 1) Objective

Implement a staged splice gate for Lethe so sip compaction is only committed when the resulting estimated context size is within a configurable threshold. If not, route to a standard compact fallback based on run mode.

Core outcomes:
- `LETHE_COMPACT_SIZE` / `compact_size` config support with existing precedence model.
- `/3` token estimation normalization across Lethe.
- Staged `.jsonl.working` splice + gate + promotion/rollback.
- Standard compact fallback protocol with one retry then fail closed.
- Global communication routing based on `AskUserQuestion` availability.

---

## 2) Scope

### In scope
- Lethe scripts, skill protocol docs, and tests needed to implement gate + fallback behavior.
- Non-README documentation updates required for implementation correctness.

### Out of scope
- README content changes in this session (handled by another session; see final section).
- Refactoring Lethe into new architecture beyond required behavior updates.
- New CLI flag for compact threshold.

---

## 3) Implementation Order

## Phase A — Config Support (`LETHE_COMPACT_SIZE`)

### A1. Extend config model in `lethe_utils.py`
- Add key mapping:
  - env: `LETHE_COMPACT_SIZE`
  - config file: `compact_size`
- Add default:
  - `compact_size = 400000`
- Add validator for positive integer values.
- Invalid values must:
  - warn to stderr
  - fall back safely to default

### A2. Extend CLI contract in `lethe-config.py`
- Include `compact_size` in JSON output.
- Keep exit behavior unchanged (always 0 with safe fallback values).

### A3. Tests (`tests/test_config.py`)
- Add coverage for:
  - env override
  - project config override
  - home fallback
  - invalid compact_size handling
  - default behavior
- Ensure precedence remains:
  1. env
  2. project config
  3. home config
  4. hardcoded default

---

## Phase B — Estimation Normalization (`/3` Everywhere)

### B1. Update token estimate math in `lethe_utils.py`
- Replace `chars / 4` assumptions with `chars / 3` in segment/token estimation paths.

### B2. Update token estimate math in `lethe-splice.py`
- Replace all estimation paths to `/3`:
  - segment keep calculations
  - summary estimate calculations
  - any reduction stats depending on those values

### B3. Tests (`tests/test_chain_and_splice.py`)
- Update/extend assertions to match `/3` model where expected token counts are validated.

---

## Phase C — Staged Splice Gate (No Splicer Re-architecture)

### C1. Keep `lethe-splice.py` as synthesis engine
- Do not add a `--stage` mode.
- Use existing `--jsonl-path` support to splice into working copy.

### C2. Update compactor protocol flow (`references/compactor.md`)
- After cut-plan generation:
  1. Resolve original JSONL path.
  2. Copy original -> `<session>.jsonl.working`.
  3. Run splice with:
     - `--jsonl-path <working>`
     - `--no-backup`
  4. Parse splice output and gate on:
     - `new_tokens_est <= compact_size`
- Gate pass:
  1. Backup original to:
     - `<session>.jsonl.lethe-<ts>-<rand>`
  2. Promote working file over original (`mv`).
- Gate fail:
  - delete/discard working file
  - route by mode matrix (Phase E)

### C3. Failure safety
- Original JSONL must never be replaced unless:
  1. splice succeeded on working copy
  2. gate passed
  3. original backup succeeded

---

## Phase D — Standard Compact Fallback Protocol

### D1. Define fallback trigger points
- Orchestrated mode: gate fail -> fallback compact flow.
- Manual mode:
  - interactive -> report/recommend only
  - non-interactive -> auto fallback compact flow

### D2. Implement stable fallback flow in compactor protocol
- Steps:
  1. capture baseline line count from target JSONL
  2. launch compact session:
     - `claude --resume <SESSION_ID> "compact"`
  3. monitor JSONL lines after baseline for `compact_boundary`
  4. on boundary detection, terminate compact session
  5. relaunch resumed session using existing resume prompt + permission policy

### D3. Retry/fail-closed policy
- On compact timeout/error:
  - retry once
- On second failure:
  - fail closed
  - do not continue relaunch in that cycle

### D4. Monitoring implementation choice
- Prefer a small dedicated watcher script under `skills/lethe/scripts/` for deterministic line tracking and timeout behavior.
- If no script is added, protocol text must still define exact baseline+poll semantics clearly enough for deterministic execution.

---

## Phase E — Global Communication Routing

### E1. Capability detection
- Determine capability mode from tool availability:
  - if `AskUserQuestion` available -> interactive mode
  - otherwise -> non-interactive mode

### E2. Route all user-facing output
- Interactive mode:
  - current direct prompts/output behavior
- Non-interactive mode:
  - route user-facing notifications through `SendMessage`

### E3. Manual launch prompt behavior
- Interactive mode:
  - ask launch question as today
- Non-interactive mode:
  - do not prompt
  - default to launch path

### E4. Ensure coverage points
- Success reports
- Launch PID messages
- Terminal-undetected fallback messages
- Gate-fail notices/recommendations
- Compact retry/fail-closed notifications

---

## Phase F — Skill/Reference Updates

### F1. `skills/lethe/SKILL.md`
- Add overarching communication-routing policy.
- Add compact-size gate references at routing level.

### F2. `skills/lethe/references/compactor.md`
- Insert staged gate sequence in phase order.
- Add `LETHE_COMPACT_SIZE` usage notes and gate checks.
- Add mode matrix for gate-fail actions.
- Add standard compact fallback + retry/fail-closed semantics.
- Ensure commands use `"compact"` (no slash) for fallback launch step.

### F3. Keep design references aligned
- Ensure any references/examples remain consistent with staged gate flow and fallback behavior.

---

## Phase G — Verification

Run full test suite and targeted checks:

1. `python3 -m unittest tests/test_config.py`
2. `python3 -m unittest tests/test_chain_and_splice.py`
3. Any newly added fallback-watcher tests (if script added)
4. Manual dry checks:
   - orchestrated gate pass
   - orchestrated gate fail -> fallback -> success
   - manual interactive gate fail -> report/recommend only
   - manual non-interactive gate fail -> auto fallback launch path

Completion gate:
- All tests pass before marking implementation complete.

---

## 4) Suggested Commit Slices

1. Config + tests (`compact_size`).
2. `/3` estimator normalization + tests.
3. Staged gate protocol + fallback logic docs (+ watcher script/tests if added).
4. SKILL.md communication routing updates.

Keep commits behavior-focused to simplify review and rollback.

---

## 5) Risk Notes and Mitigations

- Risk: Gate promotion accidentally overwrites original without backup.
  - Mitigation: explicit backup-success check before `mv`.
- Risk: Divergent estimate formulas across files.
  - Mitigation: normalize all estimators to `/3` in one pass and verify via tests.
- Risk: Non-interactive mode accidentally tries interactive prompt.
  - Mitigation: central capability branch and explicit routing language in protocol.
- Risk: Fallback compact hangs.
  - Mitigation: timeout + one retry + fail-closed policy.

---

## 6) README Updates (Another Session Only — Not for Codex Here)

**This section is strictly a handoff checklist for a separate session.  
Do not edit README in this Codex implementation session.**

Update `README.md` with the following exact content-level changes:

1. **Overview / How It Works**
- Add staged gate behavior after splice:
  - Lethe splices a working copy first
  - evaluates resulting estimated tokens
  - commits only if within threshold
  - otherwise discards candidate and routes to standard compact strategy

2. **Configuration**
- Add a new row in config table:
  - `LETHE_COMPACT_SIZE` / `compact_size`
  - positive integer
  - default `400000`
- Document precedence exactly:
  1. env
  2. project `.lethe_config`
  3. home `.lethe_config`
  4. default
- Document invalid value behavior:
  - warning + safe fallback to default

3. **Estimation Model**
- Update documentation to state Lethe now uses conservative `/3` token estimation.
- Remove/replace any `/4` references.

4. **Fallback Behavior**
- Document gate-fail handling:
  - orchestrated mode -> standard compact fallback flow
  - manual interactive mode -> report/recommend standard compact
  - manual non-interactive mode -> auto standard compact launch path

5. **Standard Compact Flow**
- Add concise flow summary:
  - baseline snapshot
  - `claude --resume <session> "compact"`
  - watch compact boundary
  - kill compact session
  - relaunch
- Include retry semantics:
  - one retry then fail closed

6. **Communication Routing**
- Add section noting Lethe adapts output routing by tool capability:
  - interactive sessions use normal prompts/output
  - non-interactive sessions relay user-facing status via `SendMessage`

7. **Safety / Backup Semantics**
- Replace generic backup description with Lethe-specific naming:
  - `.jsonl.lethe-<ts>-<rand>`
- Clarify original JSONL is only replaced after:
  - successful working-copy splice
  - gate pass
  - successful original backup creation

