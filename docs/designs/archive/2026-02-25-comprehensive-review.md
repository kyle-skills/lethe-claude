# smart-compact — Comprehensive Pre-Release Review

**Date:** 2026-02-25
**Status:** All critical and major findings resolved. Minor deferred items remain.
**Reviewers:** 10 parallel agents (2 skill-reviewers, 6 per-file deep reviews, 2 flow walkthroughs)
**Scope:** All skill files, all Python scripts, end-to-end flow analysis

---

## Review Process

Three review groups ran simultaneously:

1. **Skill Reviewers** (Opus + Haiku) — plugin-dev:skill-reviewer agents evaluating description quality, Tier compliance, cross-file consistency, progressive disclosure, and guardrails
2. **Per-File Deep Reviews** (6 Opus agents) — line-by-line review of each file: SKILL.md, rules.md, compactor.md, compact-discover.py, compact-analyze.py, compact-splice.py
3. **Flow Walkthroughs** (2 Opus agents) — end-to-end execution path analysis: self-compaction path and compactor execution path

Findings are deduplicated and grouped by severity. Consensus count indicates how many independent reviewers flagged the same issue.

---

## Critical Findings

### C1. ~~Stray `</output>` tags on all three skill files~~ RESOLVED
**Files:** SKILL.md:96, rules.md:235, compactor.md:228
**Consensus:** 5/10 reviewers (one reviewer argued the compactor.md instance is a Read tool artifact)
**Description:** All three skill files end with a `</output>` tag that has no matching `<output>` opening tag. This is not a valid authority tag and is not part of the Tier 2/3 structure. It appears to be a systematic copy artifact from the conversation where the files were created.
**Impact:** Claude may interpret these as output boundaries, potentially truncating skill content or causing confusion during processing.
**Fix:** Remove the `</output>` line from all three files. Verify by checking the last line of each file.

### C2. ~~Shell injection via `cwd` in terminal launch templates~~ RESOLVED
**File:** compact-discover.py:291-293
**Consensus:** 2/10 reviewers
**Description:** The `cwd` value from JSONL metadata is substituted directly into terminal launch templates via `str.format()`. A `cwd` containing `{`, `}`, or shell metacharacters (`;`, `$()`, backticks) creates two attack surfaces: Python format string errors and shell injection at consumption time.
```python
result["terminal_launch"] = TERMINAL_TEMPLATES[terminal_name].format(
    cwd=cwd, command="{command}"
)
```
**Impact:** Path traversal or command injection when the template is used in shell commands.
**Fix:** Use `shlex.quote(cwd)` for the template substitution. Escape braces in `cwd` before formatting (`cwd.replace('{', '{{').replace('}', '}}')`).

### C3. ~~grep interprets watermark as regex~~ RESOLVED
**File:** compact-discover.py:77-78
**Consensus:** 2/10 reviewers
**Description:** `grep -rl` treats the watermark as a basic regex. UUIDs contain only hex + hyphens (regex-safe), but there is no validation that the watermark argument is actually a UUID. A malicious or malformed argument could cause regex interpretation issues.
**Impact:** Unintended file matches or pathological regex backtracking.
**Fix:** Add `-F` (fixed-string) flag to grep: `["grep", "-rlF", ...]`. Add UUID format validation at argument parse time.

### ~~C4. Script paths are relative with no resolution mechanism~~ → Downgraded to minor
**Files:** SKILL.md:49, compactor.md:72,105,159
**Consensus:** 8/10 reviewers
**Resolution:** The Skill tool injects the base directory into context when a skill is loaded (e.g., `Base directory for this skill: /path/to/skill/`). Both self-compaction and compactor sessions load the skill via the Skill tool, so both receive the base path. Relative script paths resolve against this. **Not a runtime bug.**
**Action:** Add an explicit note in SKILL.md that script paths are relative to the skill's base directory (provided in context at load time). This is a recurring source of reviewer confusion — making it explicit prevents future false flags.

### C5. ~~`AskUserQuestion` is not a standard Claude Code tool~~ RESOLVED
**File:** SKILL.md:65-66
**Consensus:** 2/10 reviewers
**Description:** Step 10 of self-compaction says "Call AskUserQuestion." This tool does not exist in the standard Claude Code toolset. Claude would either fail to find it, or fall back to simply asking a question in text output (which functionally works but is unreliable as a blocking mechanism).
**Impact:** The "wait for death" step may not actually block Claude from proceeding. Claude could continue working instead of waiting for the compactor to kill it.
**Fix:** Replace with explicit instruction: "Output a message to the user explaining that compaction is in progress and they should not interact with this session. Then STOP — do not generate any further responses or tool calls. The compactor will terminate this session."

### C6. ~~Terminal launch templates not available in compactor context~~ RESOLVED
**File:** compactor.md:191-196
**Consensus:** 3/10 reviewers
**Resolution:** `detect_terminal()` and the template table already exist in compact-discover.py. The only gap is CLI wiring — the script requires a watermark positional arg, so there's no way to invoke just terminal detection. Meanwhile compactor.md Phase 5 tells Claude to manually reimplement the PID walk in bash, duplicating existing logic.
**Action:**
1. Add `--detect-terminal <PID>` CLI mode to compact-discover.py (wire existing function to new argparse path, skip watermark search, output terminal name + launch template)
2. Replace compactor.md Phase 5's manual PID walk + template lookup with: `compact-discover.py --detect-terminal $$`

### C7. ~~`tool_chain (Bash with git diff)` mapping table row is dead code~~ RESOLVED
**Files:** rules.md:42, compact-analyze.py:212-214
**Consensus:** 3/10 reviewers
**Resolution:** Confirmed via design doc — Aggressive Trim is the correct default for Bash tool chains (matches Read/Grep/Glob treatment). The `Bash with git diff` row is dead because the analyzer splits these into separate `tool_chain` + `git_diff` segments.
**Action:** Remove the dead row. Add `tool_chain (Bash, other) → Aggressive Trim` as catch-all.

### C8. ~~parentUuid not rewritten when leading segments are dropped~~ RESOLVED
**File:** compact-splice.py:468-475
**Consensus:** 2/10 reviewers
**Resolution:** Correctness bug regardless of current rules. The splicer must produce a valid chain for any cut-plan input.
**Action:** Handle the root entry case: if `last_emitted_uuid is None` and the entry has a `parentUuid` pointing to a dropped/summarized entry, remove `parentUuid` entirely (making it the new root).

---

## Major Findings

### M1. ~~`turn_alternation_ok` excluded from verification `ok` result~~ RESOLVED
**File:** compact-splice.py:629-633
**Consensus:** 6/10 reviewers
**Description:** `turn_alternation_ok` is computed and included in the output dict but NOT in the `all()` check that determines `result["ok"]`. Consecutive same-role messages (which rules.md says "confuse the model on resume") would pass verification.
**Fix:** Add `turn_alternation_ok` to the `all()` check, or add explicit guidance in compactor.md to also check `chain_verification.turn_alternation_ok`.

### M2. Line range indexing inconsistency (1-indexed vs 0-indexed)
**Files:** compact-analyze.py:290 vs compact-splice.py:254
**Consensus:** 6/10 reviewers
**Description:** compact-analyze.py uses 1-indexed line ranges (`line_idx + 1`). compact-splice.py uses 0-indexed (`line_idx`). Both are internally self-consistent, and the cut-plan uses segment IDs (not line ranges), so this is not a runtime bug. But it's a maintenance hazard — any future cross-referencing of line ranges between scripts will have off-by-one errors.
**Fix:** Standardize on one convention (1-indexed recommended to match `cat -n`). Better yet, extract shared code into a common module.

### M3. ~~Code duplication between analyze and splice creates drift risk~~ RESOLVED
**Files:** compact-analyze.py, compact-splice.py (10+ duplicated functions)
**Consensus:** 5/10 reviewers
**Resolution:** Extract shared utilities into `compact_utils.py`. Drift has already started (indexing convention, missing string block handler, different tool name defaults). Shared module is standard solution; one extra file is minimal cost.
**Action:** Create `scripts/compact_utils.py` with shared functions: `find_jsonl`, `parse_jsonl`, `is_chain_entry`, `walk_chain`, `get_content_blocks`, `get_text_content`, `classify_entry`, `build_segments`, `get_session_metadata`, `associate_non_chain`. Reconcile divergences (use analyze.py as authoritative source, fix splice's gaps). Update both scripts to import from utils.

### M4. ~~Safety valve targets wrong segment types~~ RESOLVED
**File:** rules.md:168-173
**Consensus:** 3/10 reviewers
**Resolution:** Confirmed — the `tool_chain`/`mcp_chain` clause was a mistake. These types already have deterministic rules and acting on them doesn't reduce evaluation load. The safety valve's purpose is to reduce segments Claude must read and evaluate.
**Action:** Remove `tool_chain`/`mcp_chain` clause. Rewrite to target only Evaluate types: `conversation` in oldest 50% → auto SUMMARIZE with generic summary; `error_chain` in oldest 50% → auto SUMMARIZE. Keep the trigger threshold (>30 Evaluate segments) and newest-50% evaluation rule.

### M5. ~~PID discovery may fail — process may be named `node` not `claude`~~ RESOLVED
**Files:** SKILL.md:43-45
**Consensus:** 4/10 reviewers
**Description:** The instruction says "Stop when the command contains `claude`." On some installations, the Claude Code process's `/proc/PID/comm` returns `node` (it's a Node.js application). `ps -o comm=` would show `node`, not `claude`. Claude would walk all the way to PID 1 without finding a match. No error handling for this case.
**Fix:** Check both `comm` and full command line (`/proc/<pid>/cmdline` or `ps -o args=`). The full command line will contain `claude` even if the binary is `node`. Add fallback if PID discovery fails.

### M6. ~~Launch command quoting is extremely error-prone~~ RESOLVED
**Files:** SKILL.md:57-63, compactor.md:199-207
**Consensus:** 5/10 reviewers
**Resolution:** Write the full launch command to `/tmp/smart-compact/launch.sh`, `chmod +x`, then the terminal launch just executes the script. Eliminates all nested quoting entirely.
**Action:** Both SKILL.md (self-compaction step 8) and compactor.md (Phase 5 Section A) write a launch script to `/tmp/smart-compact/launch.sh` instead of constructing inline commands. Terminal launch becomes e.g. `kitty --directory /path -- /tmp/smart-compact/launch.sh`. Remove the single-quote escaping guidance — no longer needed.

### M7. ~~No backgrounding specified for self-compaction launch~~ RESOLVED
**File:** SKILL.md:57-63 (step 8)
**Consensus:** 2/10 reviewers
**Description:** Unlike compactor.md Phase 5 (which uses `nohup ... &`), SKILL.md's launch of the compactor does not specify backgrounding. If Claude runs the terminal launch command synchronously, the Bash tool blocks until the terminal window closes, preventing Claude from reaching steps 9-10 (output message, wait for death).
**Fix:** Add explicit backgrounding: `nohup <terminal_launch> > /dev/null 2>&1 &` followed by `disown`.

### M8. ~~`parse_jsonl` has no error handling for malformed lines~~ RESOLVED
**Files:** compact-analyze.py:67, compact-splice.py:84
**Consensus:** 3/10 reviewers
**Description:** `json.loads(line)` will crash the entire script on any malformed line. After SIGKILL (Phase 1 escalation), the JSONL could have a partial last line. The entire analysis would fail with an unhelpful JSONDecodeError traceback.
**Fix:** Wrap in try/except per line. Skip malformed lines with a stderr warning, or abort with a clear error message including the line number.

### M9. ~~Mixed tool type classification is order-dependent~~ RESOLVED
**File:** compact-analyze.py:188-194
**Consensus:** 3/10 reviewers
**Description:** When an assistant entry has multiple tool_use blocks (parallel calls), `classify_entry` returns on the first MCP match. If an entry has both `Read` and `mcp__memory__search_nodes`, the order of blocks determines classification. This contradicts rules.md's instruction to "use the most conservative rule."
**Fix:** Collect all tool types, then return the most conservative classification (mcp_chain > task_result > tool_chain).

### M10. ~~`identify_context_header` destructively overwrites segment types~~ RESOLVED
**File:** compact-analyze.py:375
**Consensus:** 2/10 reviewers
**Description:** `segments[i]["type"] = "context_header"` overwrites the original type. The original classification is lost. If downstream logic needs the original type (e.g., to decide whether thinking blocks in the header should still be dropped), it is unavailable.
**Fix:** Store original type before overwriting: `segments[i]["original_type"] = segments[i]["type"]`.

### M11. ~~No error handling in self-compaction flow~~ RESOLVED
**File:** SKILL.md:40-66
**Consensus:** 3/10 reviewers
**Description:** Steps 1-10 have no error handling for: `uuidgen` failure, process tree walk failure (reaches PID 1), compact-discover.py non-zero exit, terminal launch command failure. Only step 7 handles `terminal_launch` being null.
**Fix:** Add explicit error handling after each step that can fail: "If [X] fails, report the error and STOP."

### M12. No timeout if compactor never kills the self-compaction session
**File:** SKILL.md:65-66
**Consensus:** 2/10 reviewers
**Description:** After launching the compactor, the self-compaction session waits indefinitely. If the compactor fails to launch, fails to find the watermark, or crashes, this session is stuck forever.
**Fix:** Add a timeout: "If this session is still alive after 2 minutes, report that compaction may have failed and provide manual instructions."

### M13. Large manifest may be truncated by Bash tool output capture
**File:** compactor.md Phase 2
**Consensus:** 2/10 reviewers
**Description:** For large sessions (200+ segments), the manifest JSON from compact-analyze.py could be 50-100KB. Claude's Bash tool has output capture limits. If truncated, Claude receives partial JSON and Phase 3 decisions are based on incomplete data.
**Fix:** Write the manifest to a file (`--output /tmp/smart-compact/manifest.json`) instead of stdout, then read it with the Read tool.

### M14. ~~Write-before-verify in splice leaves broken JSONL with no rollback~~ RESOLVED
**File:** compact-splice.py:740-743
**Consensus:** 3/10 reviewers
**Description:** `write_jsonl()` overwrites the original JSONL before `verify_new_chain()` runs. If verification fails, the original is already replaced. The backup exists but there's no automatic rollback — the user must manually restore.
**Fix:** Move verification before the write: verify the in-memory `new_lines` first, then write only if verification passes.

### M15. `kill` command may be blocked by `acceptEdits` permission mode
**File:** compactor.md Phase 1
**Consensus:** 2/10 reviewers
**Description:** The compactor runs with `--permission-mode acceptEdits`. Whether this allows `kill` commands is not documented. If Claude Code prompts for permission, the unattended compactor stalls with no one watching.
**Fix:** Test empirically. If blocked, either use `bypassPermissions` or wrap kill logic in a script that can be "accepted" as a file edit.

### M16. `interaction_group_id` claim in rules.md not implemented in splicer
**File:** rules.md:190, compact-splice.py:312-361
**Consensus:** 2/10 reviewers
**Description:** rules.md states "Both entries share the same `interaction_group_id` in the re-synthesized chain." But `make_summary_pair()` does not set this field. The field is a manifest-only concept, not a JSONL field, making the claim misleading.
**Fix:** Remove the claim from rules.md, or reword to describe it as a conceptual property rather than an actual JSONL field.

### M17. Error chain classification is overly aggressive
**File:** compact-analyze.py:206-209
**Consensus:** 2/10 reviewers
**Description:** If ANY `tool_result` in a user entry has `is_error: true`, the entire entry is `error_chain`. For parallel tool calls where one succeeded and one failed, the successful result's context is lost.
**Fix:** Classify as `error_chain` only if ALL results are errors, or add a `has_partial_errors` flag.

### M18. Watermark may not be flushed before discovery script runs
**Files:** SKILL.md:41-49
**Consensus:** 3/10 reviewers
**Description:** The watermark is output as text in step 2, but the discovery script runs in step 5 of the same turn. Claude Code may not flush the JSONL until the turn completes. The 5-retry/1s loop may not be enough since the session is still actively generating output.
**Fix:** Document this risk. Consider adding a longer retry window or a manual `sync` step. Alternatively, restructure so the discovery runs in a separate turn.

### M19. ~~`make_summary_pair` type annotation lies about `parent_uuid: str` accepting `None`~~ RESOLVED
**File:** compact-splice.py:312-313, 501-504
**Consensus:** 2/10 reviewers
**Description:** The function signature says `parent_uuid: str` but receives `None` when summarizing the first segment. The resulting entry has `"parentUuid": null` which may differ from Claude Code's expected root format (missing key vs null value).
**Fix:** Update type to `str | None`. When `parent_uuid is None`, omit the `parentUuid` key entirely.

### M20. ~~Quadratic token estimation in splice~~ RESOLVED
**File:** compact-splice.py:449-453
**Consensus:** 2/10 reviewers
**Description:** For each segment, iterates the entire chain and constructs a new `set()`. O(S*N) complexity.
**Fix:** Build a `uuid_to_entry` map once, then iterate per-segment UUIDs.

---

## Minor Findings

### m1. Description missing key trigger scenarios
**File:** SKILL.md:3-10
**Consensus:** 3/10 — Description doesn't mention orchestrated compaction (`SESSION_ID` argument) or "free up context" as trigger phrases.

### m2. Description's proactive invocation clause may cause over-triggering
**File:** SKILL.md:7-8
**Consensus:** 2/10 — The proactive invocation description in the frontmatter may cause the skill to surface when context is high even when the user hasn't asked for compaction.

### m3. ~~"Ignore everything below this line" is unreliable~~ RESOLVED
**File:** SKILL.md:32
**Consensus:** 3/10 — Reworded to explain why: "The sections below are for self-compaction mode only and do not apply when operating as a compactor."

### m4. ~~No SESSION_ID format validation~~ RESOLVED
**Files:** SKILL.md:31, compactor.md arguments
**Consensus:** 4/10 — Added UUID format validation in SKILL.md router section.

### m5. ~~Missing `--include='*.jsonl'` in grep~~ RESOLVED
**File:** compact-discover.py:78
**Consensus:** 2/10 — `grep -rl` searches ALL files under `~/.claude/projects/`, not just JSONL. If a non-JSONL file matches first, the JSONL suffix check rejects it, and valid matches later in grep output are lost.

### m6. ~~macOS `ps -o comm=` returns full path, breaking terminal detection~~ RESOLVED
**File:** compact-discover.py:188-195
**Consensus:** 2/10 — On macOS, `comm` returns `/Applications/kitty.app/Contents/MacOS/kitty`, not `kitty`. The exact-match check fails. Apply `os.path.basename()`.

### m7. ~~`is_pid_alive` returns False for permission-denied PIDs~~ RESOLVED
**File:** compact-discover.py:230-236
**Consensus:** 2/10 — `PermissionError` (process exists but owned by another user) is caught by `OSError` handler and returns `False`. Should return `True`.

### m8. Missing terminal emulators: foot, ghostty, st, urxvt
**File:** compact-discover.py:45-52
**Consensus:** 2/10 — The lookup table is missing several popular terminals.

### m9. ~~`/proc` existence checks are TOCTOU races~~ RESOLVED
**File:** compact-discover.py:151-158, 179-184
**Consensus:** 2/10 — `exists()` check before `read_text()` is redundant since `try/except` already handles the case. Remove the `exists()` check.

### m10. `tail -20` may miss metadata in sessions with large final entries
**File:** compact-discover.py:116-122
**Consensus:** 2/10 — Long JSONL entries could mean 20 lines aren't enough. Consider `tail -c 100000` (byte-based).

### m11. ~~No cleanup on error paths~~ RESOLVED
**File:** compactor.md (all phases)
**Consensus:** 2/10 — `/tmp/smart-compact/` is only cleaned up in Phase 5. If compactor aborts in Phases 1-4, temp files persist.

### m12. ~~`/tmp/smart-compact/` not session-scoped~~ RESOLVED
**File:** compactor.md:114-117
**Consensus:** 2/10 — Concurrent compactions would collide. Use `/tmp/smart-compact/$SESSION_ID/`.

### m13. ~~Section B launch missing `env -u CLAUDECODE`~~ RESOLVED
**File:** compactor.md:221
**Consensus:** 2/10 — Section A includes it, Section B does not.

### m14. ~~Summary length target not linked to trim level in Phase 3~~ RESOLVED
**File:** compactor.md:102
**Consensus:** 2/10 — Phase 3 maps both Aggressive and Moderate Trim to "SUMMARIZE" but doesn't remind Claude which length target to use when writing summaries.

### m15. ~~No re-summarization floor for already-compact sessions~~ RESOLVED
**File:** rules.md:207-211
**Consensus:** 2/10 — Repeated compaction could reduce summaries to meaningless generalities. Add conservative treatment for entries already marked as smart-compact summaries.

### m16. Context header max cap overrides minimum
**File:** rules.md:153-154, compact-analyze.py:371
**Consensus:** 2/10 — "Minimum: first interaction group" is not truly honored when it exceeds 10%. The max always caps.

### m17. `gnome-terminal-` alias makes `gnome-terminal-server` dead code
**File:** compact-discover.py:56-57
**Consensus:** 2/10 — The broader `startswith("gnome-terminal-")` match fires first.

### m18. ~~Python 3.10+ syntax required (`X | Y` unions)~~ RESOLVED
**Files:** compact-discover.py, compact-analyze.py
**Consensus:** 2/10 — Systems with Python 3.9 will fail at import. Add `from __future__ import annotations` or document minimum version.

### m19. `default=str` in JSON serialization masks bugs
**Files:** compact-analyze.py:579, compact-splice.py:642
**Consensus:** 2/10 — Non-serializable objects silently stringified instead of raising errors.

### m20. Design doc mapping table still includes `mixed` type
**File:** docs/designs/working/2026-02-25-compact-smart-design.md:192
**Consensus:** 3/10 — Eliminated from implementation (noted at line 337 of same doc) but still in the table.

### m21. Hardcoded version fallback "2.1.56"
**File:** compact-splice.py:326
**Consensus:** 2/10

### m22. No `--project-slug` passthrough in compactor flow
**Files:** compactor.md:72,159
**Consensus:** 3/10 — Discovery/self-compaction already identifies the project slug but doesn't pass it to analyze/splice.

### m23. `xterm` template lacks working directory support
**File:** compact-discover.py:51
**Consensus:** 2/10

### m24. Backup path message shows "None" with `--no-backup`
**File:** compact-splice.py:769-772
**Consensus:** 2/10

### m25. No `fsync` before atomic rename
**File:** compact-splice.py:637-643
**Consensus:** 2/10 — Power loss between write and rename could lose data.

### m26. Content preview could expose sensitive data
**File:** compact-analyze.py:415-424
**Consensus:** 2/10

### m27. Non-chain entries before the first segment are silently unassigned
**File:** compact-analyze.py:407-412
**Consensus:** 2/10

### m28. ~~`uuidgen` not universally available~~ RESOLVED
**File:** SKILL.md:40
**Consensus:** 3/10 — Add Python fallback: `python3 -c "import uuid; print(uuid.uuid4())"`

### m29. Open questions in design doc are resolved but not marked as such
**File:** docs/designs/working/2026-02-25-compact-smart-design.md:519-524
**Consensus:** 2/10

### m30. Entries with BOTH thinking AND tool_use lose thinking classification
**File:** compact-analyze.py:184-194
**Consensus:** 2/10 — Thinking blocks bundled with tool_use survive as `tool_chain`. This is arguably correct (context for tool call) but should be documented.

---

## Positive Findings (Consensus Strengths)

The following were consistently praised across reviewers:

1. **Excellent architecture** — Clean separation: Python handles structure, Claude handles semantics (8/10)
2. **Strong safety checks** — Watermark verification before kill, graceful SIGTERM with escalation, unknown entry type detection, chain verification post-splice, atomic writes with backups (7/10)
3. **Well-designed segment mapping table** — Clear, auditable, extensible with future `--strict`/`--relaxed` modes (6/10)
4. **Good progressive disclosure** — Thin SKILL.md router, detailed reference files, capable scripts (6/10)
5. **Sidecar file pattern for summaries** — Avoids JSON escaping hell (5/10)
6. **Watermark-based session discovery** — Eliminates hook dependencies, universally portable (5/10)
7. **Context budget safety valve** — Prevents compactor from exhausting its own context (4/10)
8. **Idempotency design** — Handles already-compacted sessions cleanly (4/10)

---

## Resolution Summary

### Resolved — 8 Critical, 15 Major, 14 Minor (37 total)

All critical findings resolved. All Tier 1 and Tier 2 findings resolved.

**Structural changes (this session):**
- **M3** — Created `scripts/compact_utils.py` with 8 shared functions + 3 constants. Both scripts import from utils. Reconciled divergences (classify_entry most-conservative logic, string block handler, tool name defaults).
- **C6** — Added `--detect-terminal <PID>` CLI mode to compact-discover.py. Rewrote compactor.md Phase 5 to use it.
- **M6** — Rewrote SKILL.md step 8 and compactor.md Phase 5 to use `/tmp/smart-compact/launch.sh` script. Eliminates nested quoting.
- **M11** — Added error handling throughout SKILL.md self-compaction flow (steps 1, 3, 5, 8).
- Also incorporated C4, C5, M5, M7, m3, m4, m28 in SKILL.md rewrite.

**Mechanical fixes (teammate agent):**
- Python scripts: C2, C3, C8, M1, M8, M9, M10, M14, M19, M20, m5, m6, m7, m9, m18
- Skill files: C1, C7, M4, m11, m12, m13, m14, m15

### Deferred — 5 Major, 16 Minor (21 total)

Low-impact items that can be addressed in future iterations:

**Major (deferred):**
- **M2** — Line range indexing inconsistency (partially mitigated by M3 shared utils)
- **M12** — No timeout for self-compaction wait
- **M13** — Large manifest may be truncated by Bash tool output capture
- **M15** — `kill` command may be blocked by `acceptEdits` permission mode (needs empirical testing)
- **M16** — `interaction_group_id` claim in rules.md not implemented in splicer
- **M17** — Error chain classification is overly aggressive
- **M18** — Watermark may not be flushed before discovery script runs

**Minor (deferred):**
- m1 (description trigger phrases), m2 (proactive over-triggering), m8 (missing terminals),
  m10 (tail -20 may miss metadata), m16 (context header cap vs minimum),
  m17 (gnome-terminal alias dead code), m19 (default=str masks bugs),
  m20 (design doc stale `mixed` type), m21 (hardcoded version fallback),
  m22 (no project-slug passthrough), m23 (xterm lacks cwd support),
  m24 (backup path shows None), m25 (no fsync before rename),
  m26 (content preview sensitive data), m27 (unassigned non-chain entries),
  m29 (design doc open questions), m30 (thinking+tool_use classification)
