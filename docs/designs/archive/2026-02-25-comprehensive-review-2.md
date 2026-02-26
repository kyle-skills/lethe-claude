# smart-compact — Comprehensive Pre-Release Review #2

**Date:** 2026-02-25
**Status:** ALL FINDINGS RESOLVED — Fix pass completed 2026-02-25
**Reviewers:** 19 parallel agents (2 skill-reviewers [opus+haiku], 3 per-file skill deep reviews, 8 script reviews [2 per script], 2 compactor path traces, 2 design-doc alignment reviews [opus+haiku], 1 fix verification, 1 examples/templates proposal)

---

## Executive Summary

This second comprehensive review deployed 19 independent reviewers in parallel, each instructed to be the final gate before publication. Every line of every file was read for comprehension. The review found **1 showstopper**, **8 additional critical issues**, **18 major issues**, and numerous minor/nit findings.

**The showstopper:** `CHAIN_SYSTEM_SUBTYPES` in `compact_utils.py` is catastrophically incomplete. Empirical testing against 41 real JSONL files shows that `system:stop_hook_summary`, `system:turn_duration`, and `system:microcompact_boundary` all participate in conversation chains but are excluded from `is_chain_entry()`. This causes `walk_chain()` to silently truncate chains — **14 of 21 sessions tested produce broken chains**. One session with 136 real chain entries yielded only 3 through the filtered walker. The tool is non-functional on the majority of real sessions.

### Finding Statistics

| Severity | Count | Status |
|----------|-------|--------|
| Showstopper | 1 | Must fix before any testing |
| Critical | 8 | Must fix before release |
| Major | 18 | Should fix before release |
| Minor | ~40 | Fix or defer with justification |
| Nit | ~25 | Optional |

### Top 5 Findings by Impact

1. **CHAIN_SYSTEM_SUBTYPES incomplete** — Tool non-functional on 67% of real sessions (compact_utils.py:16)
2. **Duplicated `build_segments`** — Analyzer and splicer maintain independent copies with divergent logic; any drift causes wrong segments to be kept/dropped/summarized (analyze:45, splice:63)
3. **`{cwd}` placeholder never substituted in Phase 5** — Terminal relaunch fails for all terminals that use working directory (compactor.md:215)
4. **Verification `all_summaries_reachable` is dead code** — Initialized True, never set False, excluded from `ok` aggregation (splice:448-455)
5. **No `fsync` before atomic rename** — Power loss can lose both new JSONL and original (splice:501-507)

---

## Showstopper

### S1. `CHAIN_SYSTEM_SUBTYPES` is catastrophically incomplete — tool non-functional on majority of real sessions

**File:** `compact_utils.py` line 16
**Found by:** compact_utils.py Script Review B (empirically verified against 41 real JSONL files)
**Corroborated by:** compact_utils.py Script Review A

**Current code:**
```python
CHAIN_SYSTEM_SUBTYPES = {"compact_boundary"}
```

**Missing subtypes (confirmed as chain participants via empirical analysis):**
- `stop_hook_summary` — 45 occurrences across tested sessions, present in virtually all sessions
- `turn_duration` — 21 occurrences, common in all sessions
- `microcompact_boundary` — 9 occurrences, present in compacted sessions
- `local_command` — present in sessions using slash commands

**Impact chain:**
1. `is_chain_entry()` returns `False` for these entries
2. `walk_chain()` builds `uuid_to_idx` excluding them
3. When backward walk encounters a `parentUuid` pointing to one of these entries, the UUID is not in the map
4. Chain walk `break`s silently (line 99-100) — no warning, no error
5. Result: truncated chain covering only the entries after the last missing-subtype entry
6. Manifest shows a fraction of the real session
7. Splice operates on the truncated chain, producing a JSONL that has **lost most of the conversation**

**Empirical evidence:** 14 of 21 sessions tested have broken chain walks. One session with a full chain of 136 entries only yields 3 through the filtered walker.

**Fix required:**
```python
CHAIN_SYSTEM_SUBTYPES = {
    "compact_boundary", "microcompact_boundary",
    "stop_hook_summary", "turn_duration", "local_command"
}
```

**Follow-on required (S1b):** Once these entries join the chain, `classify_entry()` must handle them. Currently they fall through to `return "conversation"` (line 221), which is incorrect. They need explicit classification — likely as `"progress"` (metadata entries) or a new `"system_meta"` type, with a corresponding row in the rules.md mapping table.

---

## Critical Findings

### C1. `{cwd}` placeholder never substituted in Phase 5 terminal relaunch

**File:** `compactor.md` lines 195-217 (Section A), lines 229-235 (Section B)
**Found by:** compactor.md deep review, SKILL.md deep review, Opus skill-reviewer, happy path trace
**Convergence:** 4 independent reviewers flagged this

In compactor.md Phase 5, `compact-discover.py --detect-terminal $$` returns `terminal_launch` with `{cwd}` as a literal placeholder (confirmed: compact-discover.py lines 289-290 substitutes `cwd="{cwd}"`). The instructions only mention replacing `{command}`, never `{cwd}`. The resulting launch command would contain literal `{cwd}` as a directory path, causing the terminal to either error or launch in the wrong directory.

Contrast with self-compaction mode: discovery with `--pid` resolves `{cwd}` from JSONL metadata (line 343: `cwd=shlex.quote(cwd)`). The two modes have inconsistent API contracts — discovery mode returns a ready-to-use template (only `{command}` placeholder), while `--detect-terminal` mode returns a template with both `{cwd}` and `{command}` as placeholders.

**Fix:** Phase 5 instructions must explicitly say to substitute both `{cwd}` (from manifest metadata) and `{command}` in the template. Or: add `--cwd` parameter to `--detect-terminal` mode so it returns a fully-resolved template.

### C2. Duplicated `build_segments` between analyzer and splicer — segment ID divergence risk

**File:** `compact-analyze.py` lines 45-137, `compact-splice.py` lines 63-119
**Found by:** Opus skill-reviewer, both splice reviews, both analyze reviews, both utils reviews, both path traces
**Convergence:** 8+ independent reviewers flagged this (most-flagged issue in entire review)

Both scripts maintain independent `build_segments()` implementations with differences:
- Analyzer: 1-indexed line ranges (`line_idx + 1`), tracks `interaction_group_id`, `tool_names`, `mcp_tools`, `has_errors`, `estimated_tokens`, `chain_entry_count`, calls `identify_context_header()` post-segmentation
- Splicer: 0-indexed line ranges (`line_idx`), tracks only `id`, `type`, `line_range`, `entry_uuids`, `non_chain_lines`, never calls `identify_context_header()`
- Tool-type tracking diverges: analyzer uses `extract_tool_names()`, splicer uses inline content block iteration with `break`

Currently both produce matching segment IDs because the boundary logic (segment on type change) is equivalent. But this is fragile coincidence — any future change to one copy that isn't mirrored in the other would cause the cut-plan's segment IDs to refer to wrong segments in the splicer, leading to **silent data corruption**.

**Fix:** Extract shared `build_segments` into `compact_utils.py`. Both scripts should call the same function. The splicer can ignore fields it doesn't need.

### C3. Verification `all_summaries_reachable` is dead code

**File:** `compact-splice.py` lines 448-455
**Found by:** Opus skill-reviewer, both splice reviews, error path trace
**Convergence:** 4 independent reviewers flagged this

```python
all_summaries_reachable = True
for _, entry in new_chain:
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str) and content.startswith("[smart-compact summary]"):
        pass  # Presence in chain is sufficient
```

Variable initialized `True`, body is `pass`, never set `False`, and **excluded from the `ok` aggregation** (lines 492-497). This provides zero verification value. If a summary pair was silently dropped during splice, this check would not catch it.

Additionally, verification does NOT check that summarized segment original UUIDs are absent from the chain. A bug that preserved both the original entries AND the summary pair would go undetected.

**Fix:** Either implement properly (count summaries vs summarized segments, verify original UUIDs absent) or remove the dead code.

### C4. No `fsync` before atomic rename in `write_jsonl`

**File:** `compact-splice.py` lines 501-507
**Found by:** compact-splice.py Script Review B (re-escalated from deferred m25 in first review)

The file is written and closed (flushing to OS page cache) then renamed. Without `f.flush(); os.fsync(f.fileno())` before close, a power loss or kernel panic can result in a zero-length or truncated file after the rename is persisted but the data blocks are not. The backup exists but requires manual recovery.

**Fix:** Add `f.flush(); os.fsync(f.fileno())` before the rename.

### C5. Self-compaction launch script path not session-scoped

**File:** `SKILL.md` lines 71-72
**Found by:** SKILL.md deep review, fix verification review

Launch script written to `/tmp/smart-compact/launch.sh` — a fixed, non-session-specific path. Two simultaneous self-compactions would clobber each other's launch scripts. The compactor correctly uses session-scoped paths (`/tmp/smart-compact/$SESSION_ID/`), but self-compaction does not.

**Fix:** Use `/tmp/smart-compact/<session_id>/launch.sh` (requires creating the directory first).

### C6. `UnicodeDecodeError` not caught in `compact-discover.py`

**File:** `compact-discover.py` lines 93-106, 132-156
**Found by:** compact-discover.py Script Review B

`subprocess.run(..., text=True)` decodes using system encoding. If a JSONL file contains non-UTF-8 bytes (corrupted after SIGKILL), `UnicodeDecodeError` is raised. This inherits from `ValueError`, NOT `OSError`, so it escapes the `except (subprocess.TimeoutExpired, OSError)` handlers. A single corrupted file crashes watermark discovery for ALL sessions.

**Fix:** Add `ValueError` to exception handlers, or use `encoding='utf-8', errors='replace'`.

### C7. `is_pid_alive()` accepts PID 0 and negative PIDs

**File:** `compact-discover.py` lines 244-252
**Found by:** compact-discover.py Script Review B

`os.kill(0, 0)` sends signal 0 to the entire process group, always succeeds. `os.kill(-N, 0)` targets process group N. No validation that PID is positive.

**Fix:** Add `if pid <= 0: return False` at function entry.

### C8. Heredoc quoting contradiction in compactor.md Phase 5

**File:** `compactor.md` lines 204-211
**Found by:** compactor.md deep review

The heredoc uses `<< 'RELAUNCH_EOF'` (single-quoted delimiter) which suppresses shell variable expansion. But the instruction says "Substitute `$SESSION_ID` and `$RESUME_PROMPT` with actual values." The code block shows `$SESSION_ID` implying shell expansion. Claude must understand it needs to do string-level substitution (write literal values), not shell variable expansion. This is confusing and error-prone.

**Fix:** Change code block to show `<actual-session-id>` placeholders instead of `$SESSION_ID`, or add explicit clarification that Claude does string substitution because the heredoc is single-quoted.

---

## Major Findings

### M1. `CHAIN_TYPES` missing `saved_hook_context`

**File:** `compact_utils.py` line 15
**Found by:** compact_utils.py Script Review A (empirically verified)

`saved_hook_context` is a real entry type with `uuid` and `parentUuid` that participates in the conversation chain. Missing from `CHAIN_TYPES` causes chain truncation in sessions that use hooks.

### M2. `walk_chain` silently truncates on missing parent entries

**File:** `compact_utils.py` lines 99-100
**Found by:** compact_utils.py Script Reviews A & B

When a `parentUuid` references an entry not in `uuid_to_idx`, the walk simply `break`s with no warning. The caller receives a partial chain with no indication it was truncated.

### M3. `walk_chain` leaf-finding doesn't filter sidechain entries

**File:** `compact_utils.py` lines 82-88
**Found by:** Opus skill-reviewer, splice review B, error path trace

`is_chain_entry()` does not check `isSidechain`. If the last entry in the JSONL is a sidechain entry, `walk_chain` starts from it and traverses the wrong branch. The original `jsonl-splice.py` found the leaf as "last entry with any uuid" without type filtering.

### M4. `classify_entry` `preceding_tool_type` tracks only first tool_use block

**File:** `compact-analyze.py` lines 73-82, `compact-splice.py` lines 83-93
**Found by:** analyze Script Reviews A & B

Both scripts track `preceding_tool_type` using only the first tool name (`tool_names[0]` / first `tool_use` block), while `classify_entry` itself checks ALL tool_use blocks and picks the most conservative. For mixed tool calls (e.g., `Read` + `mcp__memory`), the tool_result user entry could be classified differently than its paired tool_use entry, creating false segment boundaries.

### M5. rules.md mapping table "first match wins" instruction is ambiguous

**File:** `rules.md` lines 30, 46
**Found by:** rules.md deep review

"First match wins" implies top-to-bottom scanning, but `tool_chain` has three sub-rows requiring `tool_names` disambiguation, and `(final segment)` is a positional rule at the bottom that would never be reached by type-first matching. The instruction should clarify that positional rules override type-based rules.

### M6. Safety valve auto-SUMMARIZE for `error_chain` requires reading to determine resolution status

**File:** `rules.md` lines 171-172
**Found by:** rules.md deep review

The safety valve template says "Error encountered in [tool/context], [resolved/unresolved]" but the safety valve's purpose is to skip reading segments. The `content_preview` in the manifest (200 chars) may not contain resolution info. This creates an impossible instruction.

### M7. `acceptEdits` permission mode may not cover `kill` commands

**File:** `SKILL.md` line 74, `compactor.md` Phase 1
**Found by:** SKILL.md deep review, Opus skill-reviewer

`--permission-mode acceptEdits` specifically covers file edits. Bash tool invocations like `kill` and `kill -9` may still prompt for approval, blocking the compactor in an unattended terminal session.

### M8. Missing `plugin.json`

**File:** (non-existent)
**Found by:** Opus skill-reviewer, Opus design doc alignment

The design document specifies a `plugin.json` at the repo root. Without it, the skill cannot be installed as a Claude Code plugin.

### M9. `parse_jsonl` doesn't specify encoding and has no binary data protection

**File:** `compact_utils.py` lines 44-56
**Found by:** compact_utils.py Script Review A, compact-discover.py Script Review B

`open(path, "r")` uses platform-dependent encoding. No `encoding="utf-8"` specified. A non-UTF-8 byte after SIGKILL-induced corruption crashes the entire parse.

### M10. `KNOWN_TYPES` in splice.py incomplete — missing `saved_hook_context`

**File:** `compact-splice.py` lines 56-60
**Found by:** compact_utils.py Script Reviews A & B

Also includes phantom `turn_duration` as a top-level type (it only exists as a system subtype). Missing `saved_hook_context` causes safety check to abort on sessions with hook context entries.

### M11. Race condition between cleanup and relaunch script execution

**File:** `compactor.md` lines 219-220
**Found by:** happy path trace

`rm -rf /tmp/smart-compact/$SESSION_ID/` runs immediately after `nohup` launch. If the terminal hasn't read `relaunch.sh` before cleanup, the script is deleted and the resumed session never starts.

### M12. No concurrency control — two compactors corrupt JSONL

**File:** (systemic)
**Found by:** error path trace

No file locking, no PID file. Two concurrent compactors on the same session share the same temp file path (`.jsonl.tmp`), creating a data race. Also share `/tmp/smart-compact/$SESSION_ID/` working directory.

### M13. `chain_continuous` verification is tautological

**File:** `compact-splice.py` lines 457-464
**Found by:** happy path trace

The check verifies that consecutive entries in `walk_chain()` output have matching `parentUuid`/`uuid` — but `walk_chain` constructs the chain BY following `parentUuid`, so this is true by construction. The check can never fail and provides zero verification value.

### M14. Large manifest may be truncated by Bash tool output capture

**File:** `compact-analyze.py` (systemic)
**Found by:** fix verification review (deferred M13, re-flagged as concern)

For sessions with 200+ segments, the manifest JSON could be 50-100KB. Claude's Bash tool has output limits. If truncated, Claude receives partial JSON and makes decisions on incomplete data — a **silent data corruption risk**.

### M15. `interaction_group_id` claim in rules.md is factually incorrect

**File:** `rules.md` line 190
**Found by:** rules.md deep review, fix verification review

"Both entries share the same `interaction_group_id` in the re-synthesized chain." But `interaction_group_id` is a manifest-only concept, not a JSONL field. `make_summary_pair()` does not set it. The claim is misleading.

### M16. No PID ownership verification before kill

**File:** `compactor.md` Phase 1
**Found by:** error path trace

The compactor kills a PID received as an argument without verifying it still belongs to a Claude process. PID recycling (between self-compaction launch and compactor start) could cause killing an innocent process.

### M17. Context budget safety valve — design doc disagrees with implementation

**File:** Design doc lines 228-231 vs rules.md lines 165-177
**Found by:** Both design doc alignment reviews

Design says "auto-collapse `tool_chain` and `mcp_chain`" (already have deterministic rules, don't need evaluation). Implementation correctly targets `conversation` and `error_chain` (the "Evaluate" types). **Implementation is right, design is wrong.** But the disagreement should be resolved by updating the design doc.

### M18. Empty manifest / all-drop plan causes unhandled `ValueError` crash

**File:** `compact-splice.py` line 604, `compact_utils.py` line 91
**Found by:** analyze Script Review A, error path trace

`walk_chain()` raises `ValueError("No chain entries found in JSONL")` when no chain entries exist. This exception is not caught in `verify_new_chain()` or its callers. An all-drop cut-plan or empty manifest would crash with a traceback instead of a clean error.

---

## Minor Findings (Consolidated)

### Skill Documentation
- m1. Description missing orchestrated mode trigger phrase (SKILL.md)
- m2. Autonomous guardrails mixes hard rules and soft heuristics under `<mandatory>` (SKILL.md:96-119)
- m3. Resume prompt single-quote vulnerability in launch script (SKILL.md:75)
- m4. `cwd` extracted in step 6 but never used in self-compaction (SKILL.md:64)
- m5. No explicit mention of required permissions (compactor.md)
- m6. Section B launch missing launch script pattern, uses inline command (compactor.md:232-235)
- m7. Section B `> >` ambiguous redirect syntax (compactor.md:233-234)
- m8. Phase 1 watermark verification is entirely agent-driven (no script reuse)
- m9. No cleanup on Phase 1-3 abort (cleanup instruction buried in Phase 4)
- m10. `env -u CLAUDECODE` explanation only in Section A, not Section B

### Rules & Decision Logic
- m11. "User preference statements" in Always Keep is conditional on evaluation, not unconditional (rules.md:75-77)
- m12. "Exploration chains" template implies multi-segment grouping, but compaction is per-segment (rules.md:90)
- m13. Context header max 10% uses integer division — misleading for small sessions (rules.md:154)
- m14. "First interaction group" not defined in rules.md (term from analyze.py)
- m15. Future modes `--relaxed` "default KEEP" is ambiguous (rules.md:227)
- m16. Summary format doesn't specify JSONL entry `type` field values (rules.md:187-188)
- m17. Post-boundary conversation segments should be treated conservatively (rules.md idempotency)

### Python Scripts
- m18. Unused import: `DIFF_MARKERS` in compact-analyze.py (line 28)
- m19. `DIFF_MARKERS` `"@@ "` can false-positive on non-diff content
- m20. `default=str` in `json.dumps` silently converts non-serializable objects (splice:506, analyze:388)
- m21. Hardcoded version fallback `"2.1.56"` will become stale (splice:177)
- m22. `get_session_metadata` iterates all lines when `gitBranch` is absent
- m23. Fractional line positions for synthetic entries are fragile (splice:404-408)
- m24. Backup path message says "Backup at: None" when `--no-backup` used (splice:632)
- m25. `userType: "external"` on summary entries may have behavioral implications (splice:193)
- m26. `safety_check` uses `"uuid" in entry` instead of `is_chain_entry()` (splice:253)
- m27. `EXIT_PID_ERROR = 3` defined but never used in compact-discover.py (line 52)
- m28. Terminal-only mode always exits 0 even on detection failure (discover:282-293)
- m29. Terminal-only mode `format()` call is a no-op (discover:289-291)
- m30. `xterm` template lacks `{cwd}` support (discover:62)
- m31. Missing terminal emulators: foot, ghostty, st, urxvt (discover)
- m32. `tail -20` may miss metadata in sessions with large final entries (discover:131)
- m33. No `.gitignore` — `__pycache__/` committed
- m34. `parse_jsonl` docstring says "preserving line numbers" but skipped lines break index correspondence
- m35. `find_jsonl` doesn't handle `~/.claude/projects/` non-existence
- m36. `original_type` preserved in segment dict but not emitted in manifest output
- m37. `--read-segment` returns only chain entries, excludes non-chain (undocumented)
- m38. `entry_count` and `chain_entry_count` always identical — redundant fields
- m39. Launch script not cleaned up (stale `/tmp/smart-compact/launch.sh`)
- m40. `walk_chain` doesn't handle duplicate UUIDs — last writer wins silently

---

## First Review Fix Verification Summary

The fix verification reviewer checked all 37 "resolved" findings from the first review:

| Category | Count | Result |
|----------|-------|--------|
| Critical (C1-C8) | 8 | All PASS — correctly resolved |
| Major (resolved) | 15 | 14 PASS, 1 PARTIAL (M3: `build_segments` still duplicated) |
| Major (deferred) | 5 | 3 Acceptable, 1 CONCERN (M13: large manifest truncation), 1 Needs rewording (M16: interaction_group_id) |
| Minor (resolved) | 14 | All PASS |
| Minor (deferred) | 16 | All acceptable |

**Key concern from verification:** M3 (code deduplication) was marked "RESOLVED" but `build_segments()` remains duplicated between analyze and splice. Only the classification and parsing logic was centralized in `compact_utils.py`.

---

## Design Document Alignment Summary

Two independent reviewers compared the design doc against implementation:

| Area | Alignment | Notes |
|------|-----------|-------|
| Core architecture | Good | Python structural / Claude semantic split correctly implemented |
| Plugin structure | **MISMATCH** | Design: `skills/smart-compact/`, Implementation: `skill/`; `plugin.json` missing |
| Skill router | Good | Added UUID validation, Python fallback (improvements) |
| Phase 1 (Kill) | Good | Matches design specification |
| Phase 2 (Analyze) | Good | Matches with minor additions |
| Phase 3 (Decide) | **MISMATCH** | Safety valve targets different segment types (implementation is correct) |
| Phase 4 (Splice) | Good | Verification-before-write improvement over design |
| Phase 5 (Post-Splice) | **MISMATCH** | Re-detects terminal instead of using manifest; `{cwd}` not substituted |
| Compaction rules | Good | All segment types and rules implemented |
| Python scripts | Good | All three scripts match design with practical additions |
| Session discovery | Good | Watermark approach correctly implemented |
| Terminal detection | Good | With `--detect-terminal` mode addition |

**Unresolved design disagreements:**
1. Context budget safety valve segment types (design wrong, implementation right)
2. Phase 5 terminal detection method (design says manifest, implementation re-detects)
3. AskUserQuestion blocking call (design specifies it, implementation just says STOP)

---

## Examples/Templates Recommendation

The examples reviewer recommends **2 example files**:

1. **Example cut-plan with sidecar files** (`skill/examples/example-cut-plan-with-sidecars.md`)
   - Rating: Recommended
   - Shows all three action types across a realistic 12-segment session
   - Includes properly formatted sidecar summary files demonstrating Aggressive and Moderate trim levels
   - Demonstrates the session-scoped path convention

2. **Example segment manifest** (`skill/examples/example-segment-manifest.md`)
   - Rating: Recommended
   - Shows manifest JSON with realistic field values
   - Includes a reading guide for how the compactor interprets each field
   - Demonstrates `interaction_group_id` linking and `tool_names` sub-typing

Other candidates assessed as Skip or Nice-to-have:
- Context headers: Skip (handled by Python, no Claude decision)
- Before/after JSONL: Nice-to-have (Claude doesn't interact with raw JSONL)
- Decision matrix template: Skip (rules.md already serves this purpose)

---

## Priority Action Items

### Tier 0: Showstopper (blocks all testing)
1. **Fix `CHAIN_SYSTEM_SUBTYPES`** — Add `stop_hook_summary`, `turn_duration`, `microcompact_boundary`, `local_command` (S1)
2. **Add `classify_entry` handling for new chain types** — Map to `"progress"` or new `"system_meta"` type (S1b)
3. **Update rules.md mapping table** — Add row for the new type(s)

### Tier 1: Critical (must fix before release)
4. **Fix `{cwd}` substitution in Phase 5** — Instruct both placeholder replacements (C1)
5. **Extract shared `build_segments` into `compact_utils.py`** — Eliminate duplication (C2)
6. **Fix or remove dead verification code** — `all_summaries_reachable` + add summarized-UUID-absent check (C3)
7. **Add `fsync` before rename** in `write_jsonl` (C4)
8. **Session-scope the self-compaction launch script path** (C5)
9. **Add `CHAIN_TYPES` entry for `saved_hook_context`** (M1)
10. **Add `saved_hook_context` to `KNOWN_TYPES`** in splice (M10)
11. **Fix heredoc quoting documentation** in compactor.md Phase 5 (C8)
12. **Catch `UnicodeDecodeError`** in compact-discover.py (C6)
13. **Validate PID > 0** in `is_pid_alive` (C7)
14. **Add chain truncation warning** to `walk_chain` (M2)

### Tier 2: Major (should fix before release)
15. Filter sidechain entries in `walk_chain` leaf-finding (M3)
16. Fix `preceding_tool_type` to use most-conservative-wins like `classify_entry` (M4)
17. Clarify "first match wins" for positional rules in rules.md (M5)
18. Remove `[resolved/unresolved]` from safety valve error_chain template (M6)
19. Create `plugin.json` (M8)
20. Add `encoding="utf-8"` to `parse_jsonl` (M9)
21. Add sleep/self-cleanup before relaunch script deletion (M11)
22. Handle empty chain in `verify_new_chain` (M18)
23. Add manifest file output mode (`--output`) for large sessions (M14)
24. Fix `interaction_group_id` claim in rules.md (M15)
25. Update design doc safety valve to match implementation (M17)

### Tier 3: Minor (fix or defer with justification)
26. Add `.gitignore` with `__pycache__/`
27. Create example files (2 recommended)
28. Address remaining ~40 minor findings as time permits

---

## Positive Aspects (From All Reviewers)

Despite the critical findings, all reviewers noted significant strengths:

1. **Architecture is sound** — Clean separation between Python structural analysis and Claude semantic decisions
2. **Safety-first design** — Backup before write, verification before write, graceful SIGTERM→SIGKILL escalation, watermark verification before kill
3. **Watermark-based discovery** — Clever, eliminates hook dependencies, universally portable
4. **Progressive disclosure** — SKILL.md is lean router, heavy lifting in references/scripts
5. **Authority tags well-used** — `<mandatory>` for non-negotiable rules, `<core>` for essential steps, `<guidance>` for advisory
6. **Rules table is well-structured** — Single auditable mapping table, extensible with future `--strict`/`--relaxed` modes
7. **Idempotency handling** — Explicitly addresses re-compaction and prior `/compact` summaries
8. **Context budget safety valve** — Prevents the compactor from exhausting its own context
9. **Sidecar files for summaries** — Practical solution to JSON escaping issues
10. **First review fixes were thorough** — 37 of 37 resolved findings verified as correctly addressed

---

## Reviewer Index

| # | Agent | Model | Focus | Key Findings |
|---|-------|-------|-------|-------------|
| 1 | Opus skill-reviewer | opus | Overall skill quality | C2, C3, C4 (cwd), 8M, 10m, 6N |
| 2 | Haiku skill-reviewer | haiku | Overall skill quality | 1C, 2M, 9m |
| 3 | Deep review: SKILL.md | opus | Router and self-compaction | C5 (launch path), C1 (cwd), 5M, 10m, 3N |
| 4 | Deep review: compactor.md | opus | Compactor protocol | C1 (cwd), C8 (heredoc), 8M, 14m, 5N |
| 5 | Deep review: rules.md | opus | Compaction rules | 4M (table ordering, safety valve), 9m, 8N |
| 6 | Script review A: discover | opus | Discovery & terminal detection | 2C (quoting), 7M, 13m, 5N |
| 7 | Script review B: discover | opus | Unusual corners | 2C (UnicodeDecodeError, PID 0), 4M, 7m, 5N |
| 8 | Script review A: analyze | opus | Segmentation & manifest | 2C (dup segments, ValueError), 5M, 7m, 6N |
| 9 | Script review B: analyze | opus | Semantic correctness | 1M (preceding_tool_type), 7M, confirmed 7 deferred |
| 10 | Script review A: splice | opus | Splicing & verification | 3C, 5M, 10m, 6N |
| 11 | Script review B: splice | opus | Data safety focus | 5C (fsync, temp path, verification gaps), 7M, 8m, 5N |
| 12 | Script review A: utils | opus | Shared utilities | 3C (CHAIN_TYPES empirical), 7M, 10m, 5N |
| 13 | Script review B: utils | opus | Integration correctness | 2C (CHAIN_SYSTEM_SUBTYPES, classify), 6M, 9m, 3N |
| 14 | Path trace: happy path | opus | End-to-end success flow | C1 (cwd), M11 (race), 24 issues total |
| 15 | Path trace: error paths | opus | 13 failure scenarios | 3H, 9M, 8L across 13 scenarios |
| 16 | Design alignment: opus | opus | Design vs implementation | 1C (plugin.json), 4H, 8M, 12L |
| 17 | Design alignment: haiku | haiku | Design vs implementation | 5C, 8H, 3M |
| 18 | Fix verification | opus | First review fix status | 37/37 resolved PASS, 3 deferred concerns |
| 19 | Examples proposal | opus | Template recommendations | 2 recommended, 6 skip/nice-to-have |

---

## Fix Pass Resolution (2026-02-25)

All findings resolved in a single fix pass using parallel agents. Decisions documented in brainstorming session before implementation.

### Resolution Summary

| Finding | Status | Resolution |
|---------|--------|------------|
| **S1** CHAIN_SYSTEM_SUBTYPES | FIXED | Added `microcompact_boundary`, `stop_hook_summary`, `turn_duration`, `local_command` to set |
| **S1b** New subtype classification | FIXED | Mapped all new subtypes → `"progress"` (Always Drop). No rules.md table changes needed |
| **C1** `{cwd}` substitution | FIXED | Added explicit substitution instruction to compactor.md Phase 5 |
| **C2** Duplicated `build_segments` | FIXED | Extracted shared function to `compact_utils.py` with 0-indexed ranges, most-conservative-wins tool tracking |
| **C3** Dead `all_summaries_reachable` | FIXED | Replaced with real checks: summary count verification + summarized UUID absence check |
| **C4** Missing `fsync` | FIXED | Added `f.flush(); os.fsync(f.fileno())` before rename in `write_jsonl` |
| **C5** Non-session-scoped launch path | FIXED | Changed to `/tmp/smart-compact/<session_id>/launch.sh` |
| **C6** `UnicodeDecodeError` uncaught | FIXED | Added `ValueError` to except clauses in discover.py |
| **C7** PID ≤ 0 accepted | FIXED | Added `if pid <= 0: return False` guard |
| **C8** Heredoc placeholders | FIXED | Changed `$SESSION_ID`/`$RESUME_PROMPT` → `<session-id>`/`<resume-prompt>` |
| **M1** Missing `saved_hook_context` | FIXED | Added to `CHAIN_TYPES` |
| **M2** Silent chain truncation | FIXED | Added stderr warning before break |
| **M3** Sidechain leaf-finding | FIXED | Added `not entry.get("isSidechain")` filter |
| **M4** `preceding_tool_type` inconsistency | FIXED | Shared `build_segments` uses most-conservative-wins logic |
| **M5** Rules table order | FIXED | Positional rules moved to top with **Positional** markers |
| **M6** Safety valve template | FIXED | Removed `[resolved/unresolved]`, uses `content_preview` only |
| **M7** `acceptEdits` permissions | DEFERRED | Not a concern per user — future config will add bypass option |
| **M8** Missing `plugin.json` | FIXED | Created minimal manifest at repo root |
| **M9** Missing encoding in `parse_jsonl` | FIXED | Added `encoding="utf-8", errors="replace"` |
| **M10** `KNOWN_TYPES` incomplete | FIXED | Added `saved_hook_context`, removed phantom `turn_duration` |
| **M11** Race condition with `rm -rf` | FIXED | Removed `rm -rf` — let `/tmp` cleanup on reboot |
| **M12** Concurrency | DOCUMENTED | Added single-user limitation note to SKILL.md |
| **M13** Tautological `chain_continuous` | FIXED | Removed dead check, added `turn_alternation_ok` to `ok` aggregation |
| **M14** Large manifest | FIXED | Added `--output PATH` flag to analyzer |
| **M15** `interaction_group_id` claim | FIXED | Corrected to "preserves turn structure at position" |
| **M16** PID ownership | FIXED | Added `ps -o comm=` verification step in compactor.md Phase 1 |
| **M17** Design doc safety valve | FIXED | Updated to match implementation (targets `conversation` and `error_chain`) |
| **M18** Empty chain crash | FIXED | Wrapped `walk_chain` in try/except ValueError in `verify_new_chain` |
| **m1-m40** Minor findings | FIXED | All addressed — see per-file agent reports |

### Files Modified

| File | Changes |
|------|---------|
| `skill/scripts/compact_utils.py` | S1, S1b, M1, M2, M3, M4, M9, C2, m34, m35, m38, m40 |
| `skill/scripts/compact-analyze.py` | C2, M14, m18, m22, m36, m37, m38 |
| `skill/scripts/compact-splice.py` | C3, C4, M10, M13, M18, m20, m21, m23, m24, m25, m26 |
| `skill/scripts/compact-discover.py` | C6, C7, m27, m28, m29, m30, m31, m32 |
| `skill/SKILL.md` | C5, M12, m1, m2, m3, m4, m39 |
| `skill/references/compactor.md` | C1, C8, M11, M16, m5, m6, m7, m9, m10 |
| `skill/references/rules.md` | M5, M6, M15, m11, m12, m13, m14, m15, m16, m17 |
| `docs/designs/working/2026-02-25-compact-smart-design.md` | M17 |

### New Files Created

| File | Purpose |
|------|---------|
| `plugin.json` | Plugin manifest (M8) |
| `.gitignore` | Exclude `__pycache__/` and `*.pyc` (m33) |
| `skill/examples/example-cut-plan-with-sidecars.md` | Example cut-plan with sidecar summaries |
| `skill/examples/example-segment-manifest.md` | Example manifest JSON with reading guide |
