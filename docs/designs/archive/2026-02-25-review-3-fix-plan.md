# Review #3 Fix Pass — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all showstopper, critical, and targeted major findings from comprehensive review #3.

**Architecture:** Edits across 8 existing files + 1 new example file + 1 new future-work doc. Python script changes are isolated to individual functions. Skill doc changes are self-contained sections. No new dependencies, no schema changes.

**Tech Stack:** Python 3 (scripts), Markdown (skill docs)

**Review file:** `docs/designs/working/2026-02-25-comprehensive-review-3.md`

---

## Decisions Made During Design Review

| Finding | Decision |
|---------|----------|
| C1 | Mirror Section A — make Section B fully self-contained |
| C2 | Option A — add `--cwd` parameter to `--detect-terminal` for internal quoting |
| C3 | Drop — document rationale in rules.md (micro-compact markers have no summary content) |
| C5 | Extract both functions to `compact_utils.py`, use splice's efficient version, analyze ignores extra fields |
| C8 | Fix comment only — aggressive-first priority is correct |
| C9 | Add live session warning (straightforward doc edit) |
| M1 | Fix — UUID-based heredoc delimiter to prevent shell injection |
| M4 | Use `AskUserQuestion` instead of STOP |
| M7 | Align version strings |
| M10/M11 | Defer — document in working/ for future |
| M13 | Add `<reference>` pointer tags |
| M14 | Broad guardrails are intentional, keep as-is |
| M15 | Keep current behavior — preserve all error context |
| M19 | Validate segment_id type |
| M20 | Drop wins — positional Always Keep doesn't override Always Drop |

---

## Task 1: Showstopper — Add `api_error` to chain subtypes

**Files:**
- Modify: `skill/scripts/compact_utils.py:20-23`

**Step 1: Edit `CHAIN_SYSTEM_SUBTYPES`**

In `compact_utils.py` line 20-23, add `"api_error"` to the set:

```python
CHAIN_SYSTEM_SUBTYPES = {
    "compact_boundary", "microcompact_boundary",
    "stop_hook_summary", "turn_duration", "local_command",
    "api_error",
}
```

`classify_entry()` already handles this correctly — `api_error` falls through to `return "progress"` (Always Drop), which is correct behavior.

**Step 2: Commit**

```bash
cd skills_staged/smart-compact
git add skill/scripts/compact_utils.py
git commit -m "fix(S1): add api_error to CHAIN_SYSTEM_SUBTYPES

Prevents chain truncation when api_error entries bridge between
chain entries. Confirmed present in 11 of 30+ real sessions."
```

---

## Task 2: Critical code fixes in `compact_utils.py` (C3, C7, C8)

**Files:**
- Modify: `skill/scripts/compact_utils.py:110, 212-216, 239`

**Step 1: Fix C7 — guard `bridge_entries` parentUuid access**

In `compact_utils.py` line 110, replace:

```python
bridge_entries.setdefault(u, []).append((i, entry["parentUuid"]))
```

with:

```python
parent = entry.get("parentUuid")
if parent is not None:
    bridge_entries.setdefault(u, []).append((i, parent))
```

**Step 2: Add C3 rationale comment**

In `compact_utils.py` line 214, update the comment to document the C3 decision:

```python
        # Other chain-participating system subtypes are metadata (droppable).
        # microcompact_boundary: internal optimization markers with no summary
        # content — classified as progress (Always Drop). See rules.md.
```

**Step 3: Fix C8 — update comment to match actual behavior**

In `compact_utils.py` line 239, replace:

```python
            # Most conservative wins: mcp_chain > task_result > tool_chain
```

with:

```python
            # Priority: mcp_chain > task_result > tool_chain (most specific wins)
```

**Step 4: Commit**

```bash
cd skills_staged/smart-compact
git add skill/scripts/compact_utils.py
git commit -m "fix(C3,C7,C8): guard parentUuid access, fix comment, document microcompact

C7: Use .get() for bridge entry parentUuid to prevent KeyError.
C8: Fix misleading 'most conservative wins' comment.
C3: Document that microcompact_boundary is intentionally Always Drop."
```

---

## Task 3: Fix `compact-discover.py` (C2, C6, C10)

**Files:**
- Modify: `skill/scripts/compact-discover.py:62, 275-298`

**Step 1: Fix C6 — remove `st` template**

In `compact-discover.py` line 62, replace the `st` entry:

```python
    "st": "st -d {cwd} -e {command}",
```

with:

```python
    "st": "st -e sh -c 'cd {cwd} && exec {command}'",
```

**Step 2: Fix C10 — remove no-op `.format()` in `--detect-terminal` mode**

In `compact-discover.py` lines 294-296, replace:

```python
            result["terminal_launch"] = TERMINAL_TEMPLATES[terminal_name].format(
                cwd="{cwd}", command="{command}"
            )
```

with:

```python
            result["terminal_launch"] = TERMINAL_TEMPLATES[terminal_name]
```

**Step 3: Fix C2 — add `--cwd` parameter to `--detect-terminal` mode**

This requires three changes:

a) Add the argument to the parser. After line 280 (the `--detect-terminal` argument), add:

```python
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for terminal launch (quoted internally). Used with --detect-terminal.",
    )
```

b) In the `--detect-terminal` block (starting at line 285), update to use `--cwd` for quoting. Replace the block from `if args.detect_terminal is not None:` through the `sys.exit`:

```python
    if args.detect_terminal is not None:
        terminal_name, _ = detect_terminal(args.detect_terminal)
        result = {
            "terminal": terminal_name,
            "terminal_launch": None,
        }
        if terminal_name:
            template = TERMINAL_TEMPLATES[terminal_name]
            if args.cwd:
                result["terminal_launch"] = template.replace(
                    "{cwd}", shlex.quote(args.cwd)
                )
            else:
                result["terminal_launch"] = template
        print(json.dumps(result, indent=2))
        sys.exit(EXIT_SUCCESS if terminal_name else EXIT_WATERMARK_NOT_FOUND)
```

Using `.replace()` instead of `.format()` avoids `KeyError` on templates with other brace-delimited text, and `shlex.quote()` handles paths with spaces and special characters.

**Step 4: Commit**

```bash
cd skills_staged/smart-compact
git add skill/scripts/compact-discover.py
git commit -m "fix(C2,C6,C10): shell-quote cwd, fix st template, remove format() no-op

C2: Add --cwd parameter to --detect-terminal for internal quoting.
C6: Fix st template to work with stock suckless terminal.
C10: Use raw template string instead of no-op .format() call."
```

---

## Task 4: Extract duplicated functions to `compact_utils.py` (C5)

**Files:**
- Modify: `skill/scripts/compact_utils.py` (add shared functions)
- Modify: `skill/scripts/compact-analyze.py:89-122, 169-181` (replace with imports)
- Modify: `skill/scripts/compact-splice.py:65-103` (replace with imports)

**Step 1: Add shared functions to `compact_utils.py`**

Add at the end of `compact_utils.py` (after `build_segments`), the unified versions based on splice's more efficient implementation:

```python
def associate_non_chain_lines(
    lines: list[dict], segments: list[dict]
) -> None:
    """Assign non-chain entries to segments by line position.

    Non-chain entries within a segment's line range are assigned to that segment.
    Entries between segments are assigned to the preceding segment.
    Entries before the first segment remain unassigned.
    """
    seg_ranges = [(s["line_range"][0], s["line_range"][1], s["id"]) for s in segments]
    seg_by_id = {s["id"]: s for s in segments}

    for seg in segments:
        seg["non_chain_lines"] = []

    for i, entry in enumerate(lines):
        if is_chain_entry(entry):
            continue
        assigned = False
        for start, end, sid in seg_ranges:
            if start <= i <= end:
                seg_by_id[sid]["non_chain_lines"].append(i)
                assigned = True
                break
        if not assigned:
            for seg in reversed(segments):
                if seg["line_range"][1] < i:
                    seg["non_chain_lines"].append(i)
                    break


def get_session_metadata(lines: list[dict]) -> dict:
    """Extract session metadata from JSONL entries.

    Returns dict with sessionId, cwd, version, gitBranch.
    Not all fields may be present — callers should use .get() for optional fields.
    """
    metadata = {"sessionId": None, "cwd": None, "version": None, "gitBranch": None}
    for entry in lines:
        if entry.get("sessionId") and not metadata["sessionId"]:
            metadata["sessionId"] = entry["sessionId"]
        if entry.get("cwd") and not metadata["cwd"]:
            metadata["cwd"] = entry["cwd"]
        if entry.get("version") and not metadata["version"]:
            metadata["version"] = entry["version"]
        if entry.get("gitBranch") and not metadata["gitBranch"]:
            metadata["gitBranch"] = entry["gitBranch"]
        if all(metadata.values()):
            break
    return metadata
```

**Step 2: Update `compact-analyze.py` imports and remove local functions**

Add `associate_non_chain_lines` and `get_session_metadata` to the import block (line 27-34):

```python
from compact_utils import (
    associate_non_chain_lines,
    build_segments,
    find_jsonl,
    get_session_metadata,
    get_text_content,
    is_chain_entry,
    parse_jsonl,
    walk_chain,
)
```

Delete the local `associate_non_chain_lines` function (lines 89-122) and the local `get_session_metadata` function (lines 169-181) entirely.

Note: the analyze version previously only had 3 metadata fields (`cwd`, `version`, `gitBranch`). The shared version adds `sessionId`. The analyze code uses `metadata` only for the manifest output dict, which accepts all fields — no code changes needed to handle the extra field.

**Step 3: Update `compact-splice.py` imports and remove local functions**

Add `associate_non_chain_lines` and `get_session_metadata` to the import block (line 37-47):

```python
from compact_utils import (
    BRIDGE_TYPES,
    associate_non_chain_lines,
    build_segments,
    classify_entry,
    find_jsonl,
    get_content_blocks,
    get_session_metadata,
    get_text_content,
    is_chain_entry,
    parse_jsonl,
    walk_chain,
)
```

Delete the local `associate_non_chain` function (lines 65-83) and the local `get_session_metadata` function (lines 90-103) entirely.

Update the call site at line 553 from `associate_non_chain(lines, segments)` to `associate_non_chain_lines(lines, segments)` (the shared function uses the full name).

**Step 4: Commit**

```bash
cd skills_staged/smart-compact
git add skill/scripts/compact_utils.py skill/scripts/compact-analyze.py skill/scripts/compact-splice.py
git commit -m "refactor(C5): extract duplicated functions to compact_utils.py

Move associate_non_chain_lines and get_session_metadata from both
analyze and splice into compact_utils.py. Uses splice's O(1) dict
lookup implementation. Single source of truth prevents drift."
```

---

## Task 5: Validate `segment_id` type in cut-plan (M19)

**Files:**
- Modify: `skill/scripts/compact-splice.py:174-176`

**Step 1: Add type coercion in `load_cut_plan`**

In `compact-splice.py`, in the `load_cut_plan` function, after line 176 (`if action.get("action") not in ...`), add type coercion. Replace the loop body starting at line 174:

```python
    for action in plan["actions"]:
        if "segment_id" not in action:
            raise ValueError(f"Action missing 'segment_id': {action}")
        action["segment_id"] = int(action["segment_id"])
        if action.get("action") not in ("keep", "drop", "summarize"):
```

This coerces `"3"` (string) to `3` (int), preventing silent match failures in the action_map dict lookup.

**Step 2: Commit**

```bash
cd skills_staged/smart-compact
git add skill/scripts/compact-splice.py
git commit -m "fix(M19): coerce segment_id to int in cut-plan loader

Prevents silent no-op splice when segment_id is string '3' instead
of integer 3 — dict lookup would fail, defaulting all segments to keep."
```

---

## Task 6: Documentation fixes in `rules.md` (C3 docs, M20, M21)

**Files:**
- Modify: `skill/references/rules.md:30-31, 37, 49-51, 59-60`

**Step 1: Fix M20 — clarify positional vs Always Drop precedence**

In `rules.md` lines 30-31, replace:

```
Positional rules (marked **Positional**) are checked first regardless of type.
Then apply type-based rules in table order.
```

with:

```
Positional rules (marked **Positional**) are checked first regardless of type,
except for Always Drop types (thinking, progress) which are always dropped
even in positional positions.
Then apply type-based rules in table order.
```

**Step 2: Fix M20 — remove "No exceptions" from Always Drop**

In `rules.md` lines 59-60, replace:

```
- **Thinking blocks** — entries containing `<thinking>` tags. Internal reasoning
  is never needed on resume. No exceptions.
```

with:

```
- **Thinking blocks** — entries containing `<thinking>` tags. Internal reasoning
  is never needed on resume, even in positional positions (e.g., final segment).
```

**Step 3: Add C3 documentation — micro-compact boundary rationale**

In `rules.md`, in the `always-drop` section after the progress entry (line 62), add:

```
- **Micro-compact boundaries** — `microcompact_boundary` system entries are
  internal optimization markers from Claude Code's auto-compaction. They carry
  no summary content (only `"Context microcompacted"` and token metadata).
  Unlike full `compact_boundary` entries, these are classified as progress
  (Always Drop).
```

**Step 4: Fix M21 — clarify tool_chain coverage**

In `rules.md` lines 49-51, after the existing tool sub-type rows in the mapping table, replace:

```
| `tool_chain` (Bash, other) | Aggressive Trim | Summarize command and exit status |
```

with:

```
| `tool_chain` (Bash, all others) | Aggressive Trim | Summarize command and exit status |
```

And add a note after line 51 (after the mapping table's `conversation` row):

```
`tool_chain` sub-type is determined by the `tool_names` field in the segment
manifest. Tools not explicitly listed (TodoRead, TodoWrite, Task as tool_chain,
Skill, ToolSearch, etc.) follow the "all others" rule: Aggressive Trim.
```

**Step 5: Commit**

```bash
cd skills_staged/smart-compact
git add skill/references/rules.md
git commit -m "docs(C3,M20,M21): clarify drop precedence, add microcompact rationale

M20: Always Drop overrides positional Always Keep for thinking/progress.
C3: Document microcompact_boundary as intentionally Always Drop.
M21: Clarify that unlisted tools follow 'all others' Aggressive Trim."
```

---

## Task 7: Documentation fixes in `compactor.md` (C1, C4, C9, M3, M5)

**Files:**
- Modify: `skill/references/compactor.md:33-36, 78-83, 167-169, 210-214, 233-257`

**Step 1: Fix C9 — add live session warning**

In `compactor.md`, after line 35 (`skip Phase 1 entirely and begin at Phase 2.`), add:

```
When `--orchestrate` is not provided, the target session must already be
stopped. Compacting a live session risks data loss — entries written after
the read but before the atomic rename are silently overwritten.
```

**Step 2: Fix M3 — use `--output` for manifest**

In `compactor.md` lines 78-83, replace the Phase 2 steps:

```
1. Run structural analysis:
   ```bash
   python3 scripts/compact-analyze.py $SESSION_ID
   ```
2. Capture the JSON manifest output.
3. Review the manifest summary: total segments, total estimated tokens,
   segment type distribution.
```

with:

```
1. Run structural analysis:
   ```bash
   mkdir -p /tmp/smart-compact/$SESSION_ID
   python3 scripts/compact-analyze.py $SESSION_ID \
     --output /tmp/smart-compact/$SESSION_ID/manifest.json
   ```
2. Read the manifest from `/tmp/smart-compact/$SESSION_ID/manifest.json`.
3. Review the manifest summary: total segments, total estimated tokens,
   segment type distribution.
```

**Step 3: Fix M5 — pass `--project-slug` when available**

In `compactor.md`, update the Phase 2 analyze command to include project-slug. The updated command from Step 2 becomes:

```bash
   python3 scripts/compact-analyze.py $SESSION_ID \
     --output /tmp/smart-compact/$SESSION_ID/manifest.json
```

Add after the code block:

```
If the discovery output from self-compaction included a `project_slug`,
pass it: `--project-slug $PROJECT_SLUG` for faster JSONL discovery.
```

Similarly for the Phase 4 splice command (line 167-169), add the same note after:

```bash
   python3 scripts/compact-splice.py $SESSION_ID \
     --cut-plan /tmp/smart-compact/$SESSION_ID/cut-plan.json
```

Add:

```
If `project_slug` is available, pass `--project-slug $PROJECT_SLUG`.
```

**Step 4: Fix C4 — add resume prompt quoting in relaunch template**

In `compactor.md` lines 210-219, update the relaunch.sh template. Replace:

```
3. Build a launch script to avoid nested quoting:
   ```bash
   cat > /tmp/smart-compact/$SESSION_ID/relaunch.sh << 'RELAUNCH_EOF'
   #!/bin/bash
   exec env -u CLAUDECODE claude --resume <session-id> <resume-prompt>
   RELAUNCH_EOF
   chmod +x /tmp/smart-compact/$SESSION_ID/relaunch.sh
   ```
   Substitute `<session-id>` and `<resume-prompt>` with actual values in the
   heredoc content. If RESUME_PROMPT was not provided, omit it from the
   `claude --resume` command.
```

with:

```
3. Build a launch script to avoid nested quoting:
   ```bash
   DELIM="RELAUNCH_$(uuidgen | tr -d '-')"
   cat > /tmp/smart-compact/$SESSION_ID/relaunch.sh << "$DELIM"
   #!/bin/bash
   exec env -u CLAUDECODE claude --resume <session-id> "<resume-prompt>"
   $DELIM
   chmod +x /tmp/smart-compact/$SESSION_ID/relaunch.sh
   ```
   Substitute `<session-id>` and `<resume-prompt>` with actual values in the
   heredoc content. The resume prompt must be double-quoted in the exec line.
   If RESUME_PROMPT was not provided, omit it from the `claude --resume` command.
   The UUID-based heredoc delimiter prevents injection if the resume prompt
   contains a delimiter string.
```

Note: This also addresses M1 (heredoc delimiter collision / injection vector).

**Step 5: Fix C1 — make Section B self-contained**

In `compactor.md` lines 233-257, replace Section B entirely:

```
### Section B: User Prompt (--orchestrate NOT provided)

1. Output compaction results:
   "Session $SESSION_ID compacted successfully.
   Reduction: [original_tokens_est] → [new_tokens_est] tokens ([reduction_pct]%).
   Segments: [kept] kept, [summarized] summarized, [dropped] dropped."
2. Ask the user: "Launch the resumed session in a new terminal?"
   - If no: output the manual command: `claude --resume $SESSION_ID` and stop.
   - If yes: continue to step 3.
3. Retrieve `cwd` from the Phase 2 manifest metadata.
4. Detect the terminal for relaunch:
   ```bash
   python3 scripts/compact-discover.py --detect-terminal $$ --cwd <cwd>
   ```
   Parse the JSON output: extract `terminal` and `terminal_launch`.
   If `terminal` is null, output the manual command:
   `claude --resume $SESSION_ID` and stop.
5. Build a launch script to avoid nested quoting:
   ```bash
   DELIM="RESUME_$(uuidgen | tr -d '-')"
   cat > /tmp/smart-compact/$SESSION_ID/resume.sh << "$DELIM"
   #!/bin/bash
   exec env -u CLAUDECODE claude --resume <session-id>
   $DELIM
   chmod +x /tmp/smart-compact/$SESSION_ID/resume.sh
   ```
   Substitute `<session-id>` with the actual session ID.
   `env -u CLAUDECODE` prevents nested session conflicts.
6. Launch via the terminal template. Replace `{command}` in `terminal_launch`
   with `/tmp/smart-compact/$SESSION_ID/resume.sh`:
   ```bash
   nohup <terminal_launch with {command} replaced> > /dev/null 2>&1 &
   ```
   followed by `disown`.
7. The working directory at `/tmp/smart-compact/$SESSION_ID/` will be cleaned
   up on system reboot.
```

**Step 6: Update Section A heredoc to use UUID delimiter (M1)**

In `compactor.md` lines 210-214, also update Section A's relaunch.sh template to use the same UUID-based delimiter pattern (already done in Step 4).

Also update Section A's `--detect-terminal` call (line 204) to pass `--cwd`:

```bash
   python3 scripts/compact-discover.py --detect-terminal $$ --cwd <cwd>
```

where `<cwd>` is the value retrieved from manifest metadata in step 1.

**Step 7: Commit**

```bash
cd skills_staged/smart-compact
git add skill/references/compactor.md
git commit -m "docs(C1,C4,C9,M1,M3,M5): overhaul compactor protocol

C1: Make Section B self-contained with all steps.
C4: Quote resume prompt, use UUID-based heredoc delimiter.
C9: Add live session warning for manual mode.
M1: UUID-based heredoc delimiters prevent injection.
M3: Use --output for manifest to avoid stdout bloat.
M5: Document --project-slug passthrough."
```

---

## Task 8: Documentation fixes in `SKILL.md` (M4, M7, M13)

**Files:**
- Modify: `skill/SKILL.md:11, 73-78, 95-96`
- Modify: `plugin.json:4`

**Step 1: Fix M7 — align versions**

In `plugin.json` line 4, change `"0.1.0"` to `"1.0.0"`.

In `SKILL.md` line 11, change `version: 1.0` to `version: 1.0.0`.

**Step 2: Fix M4 — use AskUserQuestion instead of STOP**

In `SKILL.md` lines 95-96, replace:

```
10. STOP — do not generate any further responses or tool calls. The compactor
    will terminate this session. Do not proceed with any other work.
```

with:

```
10. Use AskUserQuestion to block: ask "Compaction in progress — this session
    will be terminated by the compactor shortly. Do not continue."
    This creates a blocking wait that prevents further output while the
    compactor kills this session.
```

**Step 3: Fix M1 — update self-compaction heredoc to use UUID delimiter**

In `SKILL.md` lines 73-78, replace the launch script heredoc:

```
8. Build a launch script to avoid nested quoting issues:
   ```bash
   mkdir -p /tmp/smart-compact/<session_id>
   cat > /tmp/smart-compact/<session_id>/launch.sh << 'LAUNCH_EOF'
   #!/bin/bash
   exec env -u CLAUDECODE claude --permission-mode acceptEdits \
     "/smart-compact <session_id> --orchestrate <claude_pid> '<resume_prompt>'"
   LAUNCH_EOF
   chmod +x /tmp/smart-compact/<session_id>/launch.sh
   ```
```

with:

```
8. Build a launch script to avoid nested quoting issues:
   ```bash
   mkdir -p /tmp/smart-compact/<session_id>
   DELIM="LAUNCH_$(uuidgen | tr -d '-')"
   cat > /tmp/smart-compact/<session_id>/launch.sh << "$DELIM"
   #!/bin/bash
   exec env -u CLAUDECODE claude --permission-mode acceptEdits \
     "/smart-compact <session_id> --orchestrate <claude_pid> '<resume_prompt>'"
   $DELIM
   chmod +x /tmp/smart-compact/<session_id>/launch.sh
   ```
```

**Step 4: Fix M13 — add reference pointer tags**

In `SKILL.md`, after line 27 (end of `<context>` block), add before `<guidance>`:

```
<reference path="references/compactor.md" load="required">
Compactor protocol — full phase-by-phase instructions for orchestrated compaction.
</reference>

<reference path="references/rules.md" load="required">
Compaction rules — segment type mapping, trim levels, evaluation guidance.
</reference>

<reference path="examples/example-segment-manifest.md" load="recommended">
Example manifest JSON with field reading guide.
</reference>

<reference path="examples/example-cut-plan-with-sidecars.md" load="recommended">
Example cut-plan with sidecar summary files.
</reference>
```

**Step 5: Commit**

```bash
cd skills_staged/smart-compact
git add skill/SKILL.md plugin.json
git commit -m "docs(M4,M7,M13,M1): version alignment, AskUserQuestion, references

M4: Use AskUserQuestion for blocking wait instead of STOP.
M7: Align version to 1.0.0 across plugin.json and SKILL.md.
M13: Add reference pointer tags to examples and references.
M1: UUID-based heredoc delimiter in self-compaction launch script."
```

---

## Task 9: Design doc cleanup (M22)

**Files:**
- Modify: `docs/designs/working/2026-02-25-compact-smart-design.md:192, 228-231`

**Step 1: Fix M22 — remove `mixed` from mapping table**

In the design doc line 192, delete the row:

```
| `mixed` | Evaluate | Context-dependent |
```

This row contradicts line 337 which says `mixed` is eliminated.

**Step 2: Fix stale safety valve targets**

In the design doc lines 228-231, update the safety valve to match the implementation. The current text targets `tool_chain`/`mcp_chain` but the implementation targets `conversation`/`error_chain`. Update to match the implementation and rules.md.

**Step 3: Commit**

```bash
cd skills_staged/smart-compact
git add docs/designs/working/2026-02-25-compact-smart-design.md
git commit -m "docs(M22): remove stale mixed type and fix safety valve targets"
```

---

## Task 10: New example — splice result (Recommended addition)

**Files:**
- Create: `skill/examples/example-splice-result.md`

**Step 1: Create the example file**

Create `skill/examples/example-splice-result.md` following the Tier 3 format used by the existing examples:

```markdown
<skill name="smart-compact-example-splice-result" version="1.0">

<metadata>
type: example
parent-skill: smart-compact
tier: 3
</metadata>

<core>
# Example Splice Result

This example shows the JSON result produced by `compact-splice.py`, with a
reading guide for the nested verification structure.

## Success Case

```json
{
  "ok": true,
  "original_lines": 847,
  "new_lines": 412,
  "original_tokens_est": 185000,
  "new_tokens_est": 62000,
  "reduction_pct": 66.5,
  "segments_kept": 4,
  "segments_dropped": 3,
  "segments_summarized": 5,
  "backup_path": "/home/user/.claude/projects/-home-user-project/a1b2c3d4-....jsonl.bak-20260225-143022-x7k2",
  "chain_verification": {
    "ok": true,
    "new_chain_length": 98,
    "all_keeps_reachable": true,
    "all_summaries_present": true,
    "summarized_uuids_absent": true,
    "no_drops_reachable": true,
    "turn_alternation_ok": true
  }
}
```

## Failure Case

```json
{
  "ok": false,
  "original_lines": 847,
  "new_lines": 410,
  "original_tokens_est": 185000,
  "new_tokens_est": 61500,
  "reduction_pct": 66.8,
  "segments_kept": 4,
  "segments_dropped": 3,
  "segments_summarized": 5,
  "backup_path": "/home/user/.claude/projects/-home-user-project/a1b2c3d4-....jsonl.bak-20260225-143022-x7k2",
  "chain_verification": {
    "ok": false,
    "new_chain_length": 96,
    "all_keeps_reachable": false,
    "all_summaries_present": true,
    "summarized_uuids_absent": true,
    "no_drops_reachable": true,
    "turn_alternation_ok": true
  }
}
```

On failure, the original JSONL is NOT overwritten. The backup path is still
created. Report the verification details and stop.

## Reading Guide

### Top-level Fields
- **`ok`**: Master success flag — `true` only if `chain_verification.ok` is also `true`. Check this first.
- **`reduction_pct`**: Percentage of tokens removed. Typical range: 40-75%.
- **`backup_path`**: Timestamped backup of the original JSONL. Always present unless `--no-backup` was used.

### Chain Verification (nested object)
Both `result.ok` AND `result.chain_verification.ok` must be checked. The
top-level `ok` mirrors the verification `ok`, but the nested object provides
diagnostic detail on failure.

- **`all_keeps_reachable`**: Every UUID from kept segments is in the new chain. If false: a kept entry was lost during re-synthesis.
- **`all_summaries_present`**: Summary user entries (`[smart-compact summary]` prefix) match the number of summarized segments. If false: a summary pair was not emitted.
- **`summarized_uuids_absent`**: Original UUIDs from summarized segments are NOT in the new chain. If false: an original entry was kept alongside its summary.
- **`no_drops_reachable`**: UUIDs from dropped segments are NOT in the new chain. If false: a dropped entry leaked through.
- **`turn_alternation_ok`**: No consecutive same-role messages. **Informational only** — this check does not affect the `ok` flag. Pre-compacted sessions may legitimately have consecutive user entries from Claude Code's built-in `/compact`.
</core>

</skill>
```

**Step 2: Add reference pointer in SKILL.md**

This was already added in Task 8 Step 4 — add one more reference for the splice result example. Add after the cut-plan reference:

```
<reference path="examples/example-splice-result.md" load="recommended">
Example splice result JSON with verification field reading guide.
</reference>
```

**Step 3: Commit**

```bash
cd skills_staged/smart-compact
git add skill/examples/example-splice-result.md skill/SKILL.md
git commit -m "docs: add splice result example with verification reading guide

New example showing success/failure JSON and explaining the nested
chain_verification structure. Addresses review recommendation."
```

---

## Task 11: Future work doc for deferred findings (M10, M11)

**Files:**
- Create: `docs/designs/working/2026-02-25-future-terminal-support.md`

**Step 1: Create the doc**

```markdown
# Future: Terminal Detection Improvements

**Status:** Deferred from Review #3
**Date:** 2026-02-25

## M10: tmux/screen sessions prevent terminal detection

Process tree walking cannot find the terminal emulator when running inside
tmux or screen — the terminal is in a separate process tree. `detect_terminal()`
returns null, forcing manual fallback.

**Possible approach:** Check `$TMUX` and `$STY` environment variables first.
If set, the session is inside a multiplexer. The actual terminal can sometimes
be found via `tmux display-message -p '#{client_termname}'` or by examining
the tmux server's parent process.

**Impact:** Affects a significant portion of developer users who work in
terminal multiplexers.

## M11: macOS primary terminals unsupported

Terminal.app and iTerm2 are absent from `TERMINAL_TEMPLATES`. The script is
effectively Linux-only for automated terminal relaunch.

**Possible templates:**
- `Terminal.app`: `open -a Terminal <command>` (cwd handling unclear)
- `iTerm2`: AppleScript or `open -a iTerm <command>` with profile support

**Impact:** macOS users always hit the manual fallback path.

## M12: gnome-terminal `--working-directory` deprecated

GNOME 41+ (Ubuntu 22.04+, Fedora 35+) deprecated `--working-directory`.
The flag is silently ignored, so the terminal opens in the wrong directory.

**Possible fix:** Use `gnome-terminal -- sh -c 'cd {cwd} && exec {command}'`
pattern matching the `st` fix.
```

**Step 2: Commit**

```bash
cd skills_staged/smart-compact
git add docs/designs/working/2026-02-25-future-terminal-support.md
git commit -m "docs: document deferred terminal detection improvements (M10,M11,M12)"
```

---

## Task 12: Final verification

**Step 1: Run Python syntax check on all modified scripts**

```bash
cd skills_staged/smart-compact
python3 -m py_compile skill/scripts/compact_utils.py
python3 -m py_compile skill/scripts/compact-analyze.py
python3 -m py_compile skill/scripts/compact-splice.py
python3 -m py_compile skill/scripts/compact-discover.py
```

Expected: All pass with no output.

**Step 2: Verify imports resolve**

```bash
cd skills_staged/smart-compact/skill/scripts
python3 -c "from compact_utils import associate_non_chain_lines, get_session_metadata; print('OK')"
```

Expected: `OK`

**Step 3: Verify no remaining references to old local functions**

Search for the old function names that should now only exist in `compact_utils.py`:

```bash
cd skills_staged/smart-compact
grep -n "def associate_non_chain" skill/scripts/compact-analyze.py skill/scripts/compact-splice.py
grep -n "def get_session_metadata" skill/scripts/compact-analyze.py skill/scripts/compact-splice.py
```

Expected: No output (functions have been removed from both files).

**Step 4: Commit (if any fixes needed from verification)**

Only if verification reveals issues. Otherwise, the fix pass is complete.

---

## Summary

| Task | Findings | Files Changed |
|------|----------|---------------|
| 1 | S1 | compact_utils.py |
| 2 | C3, C7, C8 | compact_utils.py |
| 3 | C2, C6, C10 | compact-discover.py |
| 4 | C5 | compact_utils.py, compact-analyze.py, compact-splice.py |
| 5 | M19 | compact-splice.py |
| 6 | C3 docs, M20, M21 | rules.md |
| 7 | C1, C4, C9, M1, M3, M5 | compactor.md |
| 8 | M1, M4, M7, M13 | SKILL.md, plugin.json |
| 9 | M22 | design doc |
| 10 | New example | example-splice-result.md, SKILL.md |
| 11 | M10, M11, M12 | future-terminal-support.md |
| 12 | — | verification only |
