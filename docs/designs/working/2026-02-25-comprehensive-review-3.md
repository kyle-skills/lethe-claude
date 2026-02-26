# smart-compact — Comprehensive Pre-Release Review #3

**Date:** 2026-02-25
**Status:** FINDINGS REPORTED — Awaiting fix pass
**Reviewers:** 21 parallel agents (2 skill-reviewers [opus+haiku], 5 per-file skill deep reviews [opus], 8 script reviews [2 per script, opus], 2 compactor path traces [opus], 2 design-doc alignment reviews [opus+haiku], 1 fix verification [opus], 1 examples/templates proposal [opus])

---

## Executive Summary

This third comprehensive review deployed 21 independent reviewers in parallel, each instructed to be the final gate before publication. Every line of every file was read for comprehension. The review found **1 showstopper**, **10 consolidated critical issues**, **22 consolidated major issues**, and numerous minor/nit findings.

**The showstopper:** `CHAIN_SYSTEM_SUBTYPES` is missing `api_error` — a system subtype empirically confirmed in 11 of 30+ real sessions (73 occurrences). When `api_error` entries bridge between chain entries, `walk_chain()` truncates at the gap, losing all conversation entries before the `api_error`. This was discovered through empirical testing against real JSONL files by the compact_utils.py Review A agent, which ran the scripts against live session data.

**Previous review status:** The fix verifier confirmed 54 of 59 findings from Review #2 were correctly fixed, with 2 design-doc-only issues remaining and 1 partially addressed. No regressions were introduced.

### Finding Statistics

| Severity | Count | Status |
|----------|-------|--------|
| Showstopper | 1 | Must fix before any testing |
| Critical | 10 | Must fix before release |
| Major | 22 | Should fix before release |
| Minor | ~45 | Fix or defer with justification |
| Nit | ~30 | Optional |

### Top 10 Findings by Impact

1. **`api_error` missing from `CHAIN_SYSTEM_SUBTYPES`** — Chain truncation in 11+ real sessions (compact_utils.py:20-23) [S1]
2. **Phase 5 Section B massively underspecified** — Missing terminal fallback, cwd handling, template substitution (compactor.md:240-253) [C1]
3. **`{cwd}` never shell-quoted in `--detect-terminal` mode** — Paths with spaces break terminal launch (compact-discover.py:294-296, compactor.md:220) [C2]
4. **`microcompact_boundary` classified as `progress` (Always Drop) instead of `boundary` (Always Keep)** — Loses compaction history markers (compact_utils.py:212-216) [C3]
5. **Resume prompt unquoted in relaunch.sh** — Multi-word prompts break, shell metacharacters interpreted (compactor.md:213) [C4]
6. **`associate_non_chain_lines` duplicated between analyze and splice** — Divergent implementations risk data integrity drift (analyze:89-122, splice:65-83) [C5]
7. **`st` terminal template uses nonexistent `-d` flag** — Stock suckless terminal will error (compact-discover.py:62) [C6]
8. **`bridge_entries` accesses `entry["parentUuid"]` without guard** — KeyError crash on malformed bridge entries (compact_utils.py:110) [C7]
9. **"Most conservative wins" priority inverted for task_result** — Edit/Write content in task_result segments gets aggressive trim instead of moderate (compact_utils.py:239-244) [C8]
10. **No warning about compacting live sessions in manual mode** — Silent data loss possible (compactor.md:33-36) [C9]

---

## Showstopper

### S1. `CHAIN_SYSTEM_SUBTYPES` missing `api_error` — chain truncation in real sessions

**File:** `compact_utils.py` lines 20-23
**Found by:** compact_utils.py Review A (empirically verified against 30+ real JSONL files)
**Corroborated by:** compact_utils.py Review B (flagged `entry["parentUuid"]` access pattern)

**Current code:**
```python
CHAIN_SYSTEM_SUBTYPES = {
    "compact_boundary", "microcompact_boundary",
    "stop_hook_summary", "turn_duration", "local_command",
}
```

**Missing subtype (confirmed as chain participant via empirical analysis):**
- `api_error` — 73 occurrences across 30+ sessions, present in 11 sessions, unique UUIDs (no reuse like `saved_hook_context`)

**Empirical proof:** Session `70ed4525-50be-410d-be75-905e05b97815`:
```
line 28: user    (uuid=f3d016d9...) -> parent=2903dc7d...
line 29: system/api_error (uuid=8115a526...) -> parent=f3d016d9...
line 30: system/api_error (uuid=b2389420...) -> parent=8115a526...
line 31: assistant (uuid=e96e138e...) -> parent=b2389420...
```

The assistant at line 31 has `parentUuid` pointing to the `api_error` at line 30. Since `api_error` is not in `CHAIN_SYSTEM_SUBTYPES`, `is_chain_entry()` returns `False`. `walk_chain()` cannot find `b2389420...` in `uuid_to_idx`, truncates with a warning, and loses all 28 entries before the `api_error`.

**Exhaustive subtype survey:** The reviewer ran `grep` across ALL JSONL files in `~/.claude/projects/` and found exactly 6 system subtypes in the wild: the 5 in the set plus `api_error`. The set is now confirmed complete with this addition.

**Fix:** Add `"api_error"` to `CHAIN_SYSTEM_SUBTYPES`. `classify_entry()` already handles this correctly — `api_error` would fall into `return "progress"` (Always Drop), which is the correct behavior (API error metadata is not needed on resume).

---

## Critical Findings

### C1. Phase 5 Section B is massively underspecified compared to Section A

**File:** `compactor.md` lines 240-253
**Found by:** Manual Path Tracer (showstopper), Opus Skill-Reviewer (showstopper), compactor.md Deep Review
**Corroborated by:** Haiku Skill-Reviewer, SKILL.md Deep Review

Section A (orchestrated relaunch) is fully specified with 7 numbered steps. Section B (user prompt) is missing critical details that Section A includes:

| Detail | Section A | Section B |
|--------|-----------|-----------|
| Terminal detection fallback (null terminal) | Line 207-208: explicit fallback | **MISSING** — no instruction for what to do if terminal detection fails after user says "yes" |
| `{cwd}` retrieval from manifest | Line 201: explicit step | **MISSING** — never instructs retrieving cwd |
| `{cwd}` and `{command}` substitution | Lines 220-221: explicit instruction | **MISSING** — line 252 says `nohup <terminal_launch>` with no substitution guidance |
| Shell quoting of cwd | Not explicit in either | **MISSING** in both |
| Explicit exit step | Line 231: "Exit" | **MISSING** |

**Impact:** An LLM following Section B literally would execute a terminal launch command containing literal `{cwd}` and `{command}` placeholder strings.

**Fix:** Make Section B self-contained with the same level of specificity as Section A, adapted for the interactive context.

---

### C2. `{cwd}` never shell-quoted in `--detect-terminal` mode — paths with spaces break

**File:** `compact-discover.py` lines 294-296, `compactor.md` lines 220-221
**Found by:** compact-discover.py Review A (critical), compact-discover.py Review B (critical)
**Corroborated by:** Manual Path Tracer (major), Orchestrated Path Tracer (major)

In `--detect-terminal` mode, `terminal_launch` contains literal `{cwd}` placeholder. In discovery mode, `shlex.quote(cwd)` is applied (line 348-349). The asymmetry means callers of `--detect-terminal` must quote cwd themselves, but **no caller does** and **no documentation says to**.

Additionally, if `cwd` contains curly braces (valid on Linux, e.g., `/home/user/{project}`), using Python's `.format()` to substitute raises `KeyError` because `{project}` looks like an unresolved placeholder.

**Impact:** Launch commands fail for any path containing spaces, which is common on macOS (`~/Library/Application Support/`) and occasionally on Linux.

**Fix:** Either add `--cwd` parameter to `--detect-terminal` mode for internal quoting, or add explicit quoting instruction to compactor.md.

---

### C3. `microcompact_boundary` classified as `progress` (Always Drop) instead of `boundary` (Always Keep)

**File:** `compact_utils.py` lines 212-216
**Found by:** rules.md Deep Review (critical), compact-analyze.py Review A (major)
**Corroborated by:** SKILL.md Deep Review, compact_utils.py Review A

`classify_entry()` only returns `"boundary"` for `compact_boundary` (line 212). All other `CHAIN_SYSTEM_SUBTYPES` including `microcompact_boundary` fall through to `return "progress"` (line 216), which maps to **Always Drop**.

`microcompact_boundary` is a compaction boundary marker from Claude Code's built-in micro-compaction. Dropping it erases evidence of prior compactions, violating the idempotency principle stated in rules.md (lines 207-225): "Existing `boundary` segments are Always Keep (preserves compaction markers)."

**Fix:** Either add `microcompact_boundary` to the boundary check at line 212, or explicitly document in rules.md that only full compact boundaries are preserved while micro-compact boundaries are dropped (with justification).

---

### C4. Resume prompt unquoted in relaunch.sh — multi-word prompts break

**File:** `compactor.md` lines 213, 218-219
**Found by:** Orchestrated Path Tracer (major MA08, MA10)
**Corroborated by:** Opus Skill-Reviewer (critical C3), SKILL.md Deep Review (critical C2)

The relaunch.sh template shows:
```bash
exec env -u CLAUDECODE claude --resume <session-id> <resume-prompt>
```

If the resume prompt is "Continue implementing rate limiting", this produces:
```bash
exec env -u CLAUDECODE claude --resume abc-123 Continue implementing rate limiting
```

`Continue`, `implementing`, `rate`, and `limiting` become separate arguments. Shell metacharacters in the prompt (quotes, backticks, `$`) would be interpreted.

SKILL.md (line 82-83) has escaping guidance for the self-compaction launch script, but compactor.md Phase 5A has **no equivalent escaping instruction**.

**Fix:** Add quoting around the resume prompt in the template and add an escaping instruction matching SKILL.md line 82-83.

---

### C5. `associate_non_chain_lines` duplicated between analyze and splice with divergent implementations

**File:** `compact-analyze.py` lines 89-122, `compact-splice.py` lines 65-83
**Found by:** compact-analyze.py Review B (major), compact-splice.py Review A (major M3), compact-splice.py Review B (critical C1)
**Corroborated by:** compact_utils.py Review A, compact_utils.py Review B (major 2.1)

Two independent implementations of the same algorithm:
- **Analyze version** (`associate_non_chain_lines`): Re-initializes `non_chain_lines = []`, uses O(n*m) linear scan for segment lookup
- **Splice version** (`associate_non_chain`): Does NOT re-initialize, uses O(1) dict lookup

Both also have `get_session_metadata` duplicated with different field sets (analyze: 3 fields, splice: 4 fields including `sessionId`).

**Risk:** A bug fix in one that isn't mirrored in the other creates silent data integrity divergence.

**Fix:** Extract both into `compact_utils.py` as shared functions (using the splice version's more efficient implementation).

---

### C6. `st` (suckless terminal) template uses nonexistent `-d` flag

**File:** `compact-discover.py` line 62
**Found by:** compact-discover.py Review A (critical C2)

```python
"st": "st -d {cwd} -e {command}",
```

Stock `st` from `st.suckless.org` does not have a `-d` flag. It only exists in some patched builds. Running this against stock `st` produces an unknown option error.

**Fix:** Remove `st` from templates, or change to `st -e sh -c 'cd {cwd} && exec {command}'`.

---

### C7. `bridge_entries` accesses `entry["parentUuid"]` without guard — KeyError crash

**File:** `compact_utils.py` line 110
**Found by:** compact_utils.py Review B (critical 3.1)
**Corroborated by:** compact_utils.py Review A (noted in bridge analysis)

```python
bridge_entries.setdefault(u, []).append((i, entry["parentUuid"]))
```

Uses subscript notation `entry["parentUuid"]` which raises `KeyError` if a bridge-type entry lacks a `parentUuid` field. All other parentUuid accesses in `walk_chain` use `.get()`.

**Fix:** Use `entry.get("parentUuid")` with a guard:
```python
parent = entry.get("parentUuid")
if parent is not None:
    bridge_entries.setdefault(u, []).append((i, parent))
```

---

### C8. "Most conservative wins" priority inverted for `task_result`

**File:** `compact_utils.py` lines 239-244
**Found by:** compact-analyze.py Review A (critical, finding 11)

The comment says "Most conservative wins: mcp_chain > task_result > tool_chain." But per rules.md:
- `tool_chain` (Edit/Write) = **Moderate Trim** (most conservative)
- `task_result` = Aggressive Trim
- `mcp_chain` = Aggressive Trim

An assistant entry with both `Task` and `Edit` tool_use blocks returns `task_result` (Aggressive Trim), but the Edit content should get Moderate Trim. The priority returns the **more aggressive** classification, not the most conservative.

**Practical impact:** Extremely unlikely scenario (Task + Edit in same turn), but the stated intent contradicts the implementation.

**Fix:** Either reorder priority to `tool_chain > task_result > mcp_chain` or update the comment to accurately describe the actual behavior.

---

### C9. No warning about compacting live sessions in manual mode

**File:** `compactor.md` lines 33-36
**Found by:** Manual Path Tracer (critical, M-02 elevated)
**Corroborated by:** compact-splice.py Review B (M4 edge case)

Manual mode (SESSION_ID only, no `--orchestrate`) has no Phase 1 kill step. If a user compacts a live session, the JSONL is actively written to while splice reads/modifies it. The atomic rename would overwrite the JSONL, and entries written by the live session after the read are lost.

**Fix:** Add explicit warning in compactor.md's arguments section: "When `--orchestrate` is not provided, the target session must already be stopped. Compacting a live session causes data loss."

---

### C10. `format()` no-op in `--detect-terminal` mode — creates latent injection risk

**File:** `compact-discover.py` lines 294-296
**Found by:** compact-discover.py Review B (critical CRIT-2)
**Corroborated by:** compact-discover.py Review A (nit n2)

```python
result["terminal_launch"] = TERMINAL_TEMPLATES[terminal_name].format(
    cwd="{cwd}", command="{command}"
)
```

This `.format()` call replaces `{cwd}` with `{cwd}` — a literal no-op. If a future template contains additional brace-delimited text (e.g., `{title}`), `.format()` raises `KeyError`. Using the raw template string directly is safer.

**Fix:** Replace with `result["terminal_launch"] = TERMINAL_TEMPLATES[terminal_name]`

---

## Major Findings

### M1. Heredoc delimiter collision with resume prompt content

**File:** `compactor.md` lines 210-214 (RELAUNCH_EOF), SKILL.md lines 73-78 (LAUNCH_EOF), compactor.md lines 244-248 (RESUME_EOF)
**Found by:** Orchestrated Path Tracer (critical C04)

If a resume prompt contains the heredoc delimiter string (e.g., `RELAUNCH_EOF`) on its own line, the heredoc terminates prematurely, producing a malformed script. No sanitization or check exists.

**Impact:** Extremely unlikely but unhandled. A resume prompt containing code examples or documentation could theoretically trigger this.

---

### M2. Phase 1 watermark verification requires locating JSONL before Phase 2 discovery

**File:** `compactor.md` lines 47-48
**Found by:** compactor.md Deep Review (showstopper S1), Orchestrated Path Tracer (major MA01)

Phase 1 instructs: `grep for COMPACT_WATERMARK: in ~/.claude/projects/*/$SESSION_ID.jsonl`. But Phase 2 is where `compact-analyze.py` uses `find_jsonl()` to properly discover the file. Phase 1 uses a shell glob before any proper discovery has occurred. If the session exists in multiple project directories, Phase 1 might grep one file while Phase 2 analyzes another.

**Fix:** Either add an explicit file-discovery step before Phase 1, or use `compact-discover.py` or `find_jsonl` to resolve the path first.

---

### M3. Large manifest output to stdout may exhaust compactor context

**File:** `compactor.md` lines 78-83
**Found by:** Haiku Skill-Reviewer (critical CRI-1), Orchestrated Path Tracer (major MA04), Manual Path Tracer (major M-04)
**Corroborated by:** compact-analyze.py Review B, Opus Skill-Reviewer (major M-3)

Phase 2 captures the manifest JSON from stdout. For large sessions (200+ segments), this could be 50-100KB. The `--output PATH` flag exists in the script but is **never mentioned** in compactor.md.

**Fix:** Update Phase 2 to use `--output` for file-based output, with the compactor reading via the Read tool.

---

### M4. Self-compaction step 10 uses "STOP" instead of blocking mechanism

**File:** `SKILL.md` lines 95-96
**Found by:** Design Doc Alignment Opus (major M1), SKILL.md Deep Review (major, downgraded from critical)
**Corroborated by:** Haiku Design Doc Alignment

The design doc specifies `AskUserQuestion` as a blocking wait. Implementation says "STOP — do not generate any further responses." This doesn't create a blocking wait — the session could continue generating output before the compactor kills it.

---

### M5. `--project-slug` never passed through from compactor protocol to scripts

**File:** `compactor.md` lines 79, 167-169
**Found by:** compactor.md Deep Review (critical C3), Opus Skill-Reviewer (major M-3)

Both `compact-analyze.py` and `compact-splice.py` accept `--project-slug` for faster/safer JSONL discovery. The compactor protocol never instructs the compactor to use it, even when available from the discovery output.

---

### M6. Segment IDs can drift between Phase 2 and Phase 4

**File:** `compact-splice.py` lines 550-553, `compact_utils.py` line 347
**Found by:** Manual Path Tracer (major M-12), Orchestrated Path Tracer (major MA05)
**Corroborated by:** compact-splice.py Review B (major M4)

Both scripts independently call `build_segments()` which assigns sequential IDs. If the JSONL changes between calls (unlikely in orchestrated mode, possible in manual mode), IDs mismatch. The splicer silently defaults unmatched segments to "keep" (line 248-249) rather than aborting.

---

### M7. Version mismatch between plugin.json and SKILL.md

**File:** `plugin.json` line 4 (`"0.1.0"`), `SKILL.md` line 11 (`1.0`)
**Found by:** Opus Skill-Reviewer (critical C-2), Haiku Skill-Reviewer (minor MIN-3)
**Corroborated by:** Design Doc Alignment Opus (nit n1), SKILL.md Deep Review (major M1)

---

### M8. `turn_alternation_ok` excluded from `ok` aggregation — weakens verification

**File:** `compact-splice.py` lines 468-476
**Found by:** compact-splice.py Review B (major M2)
**Corroborated by:** compact-splice.py Review A (related to M5 verification gap)

The splicer cannot distinguish between pre-existing and splicer-introduced alternation violations. If the splicer has a bug that creates consecutive user entries, this check fires but does **not** block the write.

---

### M9. `all_summaries_present` only checks user entries, not paired assistant entries

**File:** `compact-splice.py` lines 419-429
**Found by:** compact-splice.py Review B (major M1)

The verification counts summary entries by checking for the `[smart-compact summary]` prefix on strings. The assistant acknowledgment has `content` as a `list` (not string), so `isinstance(content, str)` is `False` — it's never counted. If a bug emitted only the user entry without the assistant pair, verification would still pass.

---

### M10. tmux/screen sessions prevent terminal detection entirely

**File:** `compact-discover.py` lines 217-242
**Found by:** compact-discover.py Review A (major M1), compact-discover.py Review B (major MAJ-2)

Process tree walk cannot find the terminal emulator when running inside tmux/screen (the terminal is in a separate process tree). Returns null, forcing manual fallback. Affects a significant portion of developer users.

---

### M11. macOS primary terminals (Terminal.app, iTerm2) completely unsupported

**File:** `compact-discover.py` lines 53-64
**Found by:** compact-discover.py Review B (major MAJ-1)

The two most common macOS terminal emulators are absent from templates. The script is effectively Linux-only for automated terminal relaunch.

---

### M12. `gnome-terminal --working-directory` deprecated in GNOME 41+

**File:** `compact-discover.py` line 55
**Found by:** compact-discover.py Review B (major MAJ-3)

GNOME 41 (Ubuntu 22.04+, Fedora 35+) deprecated `--working-directory`. The flag is silently ignored, so the terminal opens in the wrong directory.

---

### M13. No `<reference>` pointer tags connecting skill documents to examples

**File:** `SKILL.md`, `compactor.md`
**Found by:** Opus Skill-Reviewer (major M-2)

Two example files exist but are never referenced from SKILL.md or compactor.md via `<reference path="..." load="...">` tags. Claude will only discover them if it reads the directory listing.

---

### M14. Autonomous guardrails too broad — "operating autonomously under a plan" allows blanket self-compaction

**File:** `SKILL.md` lines 100-111
**Found by:** SKILL.md Deep Review (major M4)

The `<mandatory>` block authorizes autonomous compaction without confirmation when "the session is operating autonomously under a plan and context is filling up." This is extremely broad — any session running from task instructions qualifies.

---

### M15. Error chain classification overly aggressive — any `is_error: true` classifies entire entry

**File:** `compact_utils.py` lines 256-259
**Found by:** Haiku Skill-Reviewer (critical CRI-2), compact_utils.py Review A (major M4)

If ANY `tool_result` in a user entry has `is_error: true`, the entire entry becomes `error_chain`. For parallel tool calls where one succeeded and one failed, successful results' context is misclassified.

---

### M16. `O(n*m*k)` complexity in analyze's `associate_non_chain_lines`

**File:** `compact-analyze.py` lines 106-115
**Found by:** compact-analyze.py Review B (major finding 11.1)
**Corroborated by:** compact_utils.py Review B (minor 11.4)

Inner loop scans all segments linearly to find by ID. The splicer's version uses O(1) dict lookup. For large sessions, this could be slow.

---

### M17. Non-chain entries before first segment silently unassigned

**File:** `compact-analyze.py` lines 117-122, `compact-splice.py` lines 65-83
**Found by:** compact-splice.py Review A (critical C2), compact-analyze.py Review A (major finding 32)
**Corroborated by:** compact_utils.py Review A (major M3), compact-splice.py Review B (critical C2)

Entries before the first segment's line range can't be assigned to any segment. In the splicer, unclaimed entries are preserved (safe but undocumented). In the analyzer, they don't appear in any segment's `non_chain_lines` (manifest is inaccurate).

---

### M18. Fractional position sorting for synthetic entries depends on stable sort and float==int equality

**File:** `compact-splice.py` lines 352-373
**Found by:** compact-splice.py Review A (critical C3)

Synthetic summary entries use fractional positions (e.g., 11.5, 12.0). An unclaimed non-chain entry at integer 12 sorts alongside the float 12.0 based on Python's `float == int` comparison and stable sort. Correctness depends on insertion order — fragile and undocumented.

---

### M19. `segment_id` type not validated in cut-plan — string "3" silently fails to match integer 3

**File:** `compact-splice.py` lines 174-176
**Found by:** compact-splice.py Review A (major M2), compact-splice.py Review B (minor m7)

`load_cut_plan` checks for presence but not type of `segment_id`. If `"segment_id": "3"` (string), the dict lookup fails (`3 != "3"`), and all segments silently default to "keep" — a no-op splice with no error.

---

### M20. "No exceptions" for thinking blocks contradicts positional Always Keep override

**File:** `rules.md` lines 59-60, 30, 36
**Found by:** rules.md Deep Review (major finding 7.1)

`always-drop` says thinking entries have "**No exceptions.**" But positional rules (line 30) are "checked first regardless of type." If the final segment is thinking-type, these rules contradict.

---

### M21. `tool_chain` sub-type rules don't cover all tool names

**File:** `rules.md` lines 42-44, 49-51
**Found by:** rules.md Deep Review (major finding 2.1)

The rules list Read/Grep/Glob (Aggressive), Edit/Write (Moderate), Bash/other (Aggressive). But tools like `TodoRead`, `TodoWrite`, `Task`, `Skill`, `ToolSearch` are not explicitly categorized. The "other" parenthetical implies Aggressive, but this is not stated explicitly.

---

### M22. Design doc internally contradictory — `mixed` type listed in mapping table but declared eliminated

**File:** Design doc lines 192, 337
**Found by:** Design Doc Alignment Opus (critical C2)

The mapping table still lists `mixed | Evaluate | Context-dependent` while line 337 says "the `mixed` type from earlier drafts is eliminated." Implementation correctly omits `mixed`.

---

## Minor Findings (Selected — ~45 total across all reviewers)

### Cross-cutting themes

| Theme | Count | Files Affected |
|-------|-------|----------------|
| Exit code `EXIT_WATERMARK_NOT_FOUND` reused for terminal-not-found | 5 reviewers | compact-discover.py:298 |
| `default=str` in json.dumps masks serialization bugs | 4 reviewers | analyze, splice |
| Token estimation `len//4` is rough approximation | 4 reviewers | compact_utils.py:385 |
| Undocumented `--jsonl-path` and `--output` flags | 3 reviewers | analyze, splice |
| `git_diff` detection can false-positive on `@@ ` | 3 reviewers | compact_utils.py:261-263 |
| No `__init__.py` in scripts directory | 2 reviewers | scripts/ |
| `parse_jsonl` line indices don't match file line numbers | 4 reviewers | compact_utils.py:54-70 |
| `interaction_group_id` starts at 0 (undocumented) | 3 reviewers | compact_utils.py:299 |
| PID comm check may not handle `nodejs` binary name | 2 reviewers | compactor.md:51-54 |
| `tail -50` may miss metadata in large final entries | 2 reviewers | compact-discover.py:134 |
| `alacritty -e` deprecated (0.13+) | 2 reviewers | compact-discover.py:57 |
| `xterm` template lacks `{cwd}` support (documented) | 2 reviewers | compact-discover.py:59 |
| `find_jsonl` doesn't find subagent JSONL files | 1 reviewer | compact_utils.py:29-51 |
| No directory fsync after atomic rename | 2 reviewers | compact-splice.py:480-488 |
| Orphan backups on repeated failed splices | 2 reviewers | compact-splice.py:577-582 |
| `KNOWN_TYPES` diverges from design doc | 2 reviewers | compact-splice.py:58-62 |
| Example files missing `<sections>` index | 2 reviewers | both examples |
| Example manifest type distribution differs from cut-plan example | 2 reviewers | examples |
| `get_content_blocks` crashes if `entry["message"]` is `None` | 1 reviewer | compact_utils.py:161 |
| `parentUuid: null` not rewritten when prior entries were emitted | 1 reviewer | compact-splice.py:274 |

---

## Design Doc Alignment Summary

### Deviations from design (positive — implementation is better)

| Area | Design Says | Implementation Does | Verdict |
|------|-------------|---------------------|---------|
| Verify-before-write | Write then verify | Verify then write | **Implementation is better** |
| PID safety check | Not mentioned | Checks `ps -o comm=` before kill | **Implementation is better** |
| Cleanup on abort | Not mentioned | `rm -rf` working directory | **Implementation is better** |
| Launch script pattern | Inline command | Heredoc to file (avoids quoting) | **Implementation is better** |
| `env -u CLAUDECODE` | Not mentioned | Prevents nested session conflicts | **Implementation is better** |
| Bridge types | Not in design | Transparent bridge traversal | **Implementation is better** |

### Deviations from design (needs attention)

| Area | Design Says | Implementation Does | Impact |
|------|-------------|---------------------|--------|
| `--detect-terminal` mode | Not designed | Added to compact-discover.py | Scope creep; works but undocumented in design |
| Entry field name | `entry_count` | `chain_entry_count` | Manifest field name mismatch |
| Safety valve targets | `tool_chain`/`mcp_chain` | `conversation`/`error_chain` | Design doc stale (2 locations) |
| Verification fields | `all_summaries_reachable` | `all_summaries_present` + `summarized_uuids_absent` | Design doc stale |
| Plugin structure | `skills/smart-compact/` | `skill/` | Path mismatch |
| Self-compaction wait | `AskUserQuestion` | "STOP" | Different blocking mechanism |

---

## Previous Review Fix Verification

**Verifier:** Fix Verification Agent (Opus)
**Method:** Exhaustive cross-check of all 59 findings from Review #2

| Status | Count | Details |
|--------|-------|---------|
| FIXED correctly | 54 | All showstoppers, all criticals, most majors/minors |
| NOT FIXED (design doc only) | 2 | M17 safety valve section lines 228-231; verification output example lines 435-437 |
| PARTIALLY FIXED | 1 | m20: `default=str` documented but not changed |
| DEFERRED (acceptable) | 2 | M7: `acceptEdits` permissions; m19: `@@ ` false positive |
| Regressions introduced | 0 | No regressions found |

---

## Examples/Templates Recommendations

**Proposer:** Examples/Templates Agent (Opus)

### Current examples (2 files — both well-constructed)
1. `example-segment-manifest.md` — Manifest JSON with field reading guide
2. `example-cut-plan-with-sidecars.md` — Cut-plan JSON with rationale table and sidecar files

### Recommended addition (1 file)

**`example-splice-result.md`** — Rating: **Recommended**

The splice result JSON has a nested `chain_verification` sub-object that is non-obvious. The LLM must check both `result.ok` AND `result.chain_verification.ok`, extract nested stats, and handle success vs failure cases. No example currently exists for this format.

Proposed content: success case JSON, failure case JSON, and a reading guide noting that `turn_alternation_ok` is informational-only.

**Token cost:** ~50-60 lines, ~800-1000 tokens. Reasonable for the parsing clarity it provides.

### Not recommended (6 proposals evaluated and rejected)
- Discovery output example — instructions explicit enough (skip)
- Self-compaction flow walkthrough — too high token cost (skip)
- Context budget safety valve example — rare path (skip)
- `--read-segment` output example — LLM reads for semantics, not structure (skip)
- Error/failure scenarios — error JSON is trivially simple (skip)
- Launch script templates — already inline where used (skip)

---

## Priority Fix Recommendations

### Tier 1: Must fix before any testing
1. **S1** — Add `"api_error"` to `CHAIN_SYSTEM_SUBTYPES` (1 line change)

### Tier 2: Must fix before release
2. **C1** — Make Phase 5 Section B self-contained (documentation)
3. **C2** — Add cwd quoting for `--detect-terminal` callers (script or documentation)
4. **C3** — Decide on `microcompact_boundary` classification (code + documentation)
5. **C4** — Add resume prompt quoting in relaunch.sh template (documentation)
6. **C5** — Extract duplicated functions into `compact_utils.py` (code refactor)
7. **C6** — Fix or remove `st` terminal template (1 line change)
8. **C7** — Use `.get()` for bridge entry parentUuid (1 line change)
9. **C9** — Add live session warning to manual mode (documentation)
10. **C10** — Replace format() no-op with direct template access (1 line change)

### Tier 3: Should fix before release
11. **M7** — Align version strings
12. **M3** — Use `--output` for large manifests
13. **M13** — Add `<reference>` pointer tags
14. **M19** — Validate `segment_id` type in cut-plan
15. **M20** — Resolve "no exceptions" vs positional rule contradiction

### Tier 4: Fix or defer with justification
16-22. Remaining major findings (M1, M2, M4-M6, M8-M12, M14-M18, M21-M22)

---

## Files Requiring Changes

| File | Issues | Priority |
|------|--------|----------|
| `compact_utils.py` | S1, C3, C7, C8, M15, M17 | **CRITICAL** |
| `compactor.md` | C1, C4, C9, M2, M3, M5 | **CRITICAL** |
| `compact-discover.py` | C2, C6, C10, M10, M11, M12 | **CRITICAL** |
| `compact-splice.py` | C5 (shared func), M8, M9, M18, M19 | **HIGH** |
| `compact-analyze.py` | C5 (shared func), M16 | **HIGH** |
| `SKILL.md` | M4, M7, M13, M14 | **HIGH** |
| `rules.md` | C3 (docs), M20, M21 | **HIGH** |
| `plugin.json` | M7 | **MEDIUM** |
| Design doc | M22, stale sections | **LOW** |
| New: `example-splice-result.md` | Recommended addition | **MEDIUM** |
