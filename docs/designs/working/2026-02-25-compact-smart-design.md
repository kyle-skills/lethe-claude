# smart-compact — Intelligent JSONL Compaction Plugin Design

**Date:** 2026-02-25
**Status:** Design approved (2026-02-25)
**Scope:** Standalone Claude Code plugin for public release
**Dependencies:** None (fully self-contained)
**Reviews:** Gemini Pro analysis + Claude Code teammate review (2026-02-25)

---

## Overview

Claude Code's built-in `/compact` summarizes the entire conversation and replaces it wholesale. This destroys important early context (plans, initial setup, architectural decisions), treats all content equally, and handles MCP tool outputs particularly poorly — they're enormous and survive standard compaction badly.

`smart-compact` is a Claude Code plugin that performs **surgical compaction** — it intelligently identifies what to cut, preserves what matters, and generates targeted summaries for removed sections. It replaces the "summarize everything" approach with a "keep the skeleton, trim the fat" approach.

---

## Core Architecture: Python Eyes + Claude Brain

Two layers with a clean separation of responsibility:

### Python Layer (Structural Analysis)
- Parses JSONL files, walks the parentUuid linked list chain
- Classifies entries by structural type at **hyper-granular** level (each sub-type gets its own segment)
- Links related segments via `interaction_group_id` (e.g., a user message, its tool calls, and the assistant response share a group)
- Calculates sizes and estimated token counts per segment
- Outputs structured JSON manifests
- **Has NO opinion on importance** — maps structure, doesn't judge it

### Claude Layer (Semantic Analysis + Decision Making)
- Receives the structural manifest
- Reads early conversation to understand session context (brainstorming? implementation? debugging?)
- For each segment, applies centralized rules to decide: KEEP, SUMMARIZE, or DROP
- Writes summaries for sections marked for compaction
- Builds a cut-plan JSON and hands it to the splicer
- Guided by a rules file that can branch for future flag support (--strict/--relaxed)

---

## JSONL Mechanics

These are empirically verified facts about Claude Code's JSONL format:

- Each entry has `uuid` and `parentUuid` forming a singly-linked list
- The loader (`elH`) walks backwards from the leaf via `parentUuid` pointers — NOT sequential by line number
- Entries not reachable from the leaf are invisible — no stubs needed for removed entries
- Only `user`, `assistant`, `progress`, and `system:compact_boundary` types participate in the chain
- **Live sessions keep context in process memory. JSONL modifications only take effect after kill + resume.**
- **`claude --resume <SESSION_ID> "prompt"` is verified** — the trailing prompt argument is processed as the next user turn on resume. This is how compact-then-continue works and has been empirically tested.

---

## Plugin Structure

```
smart-compact/
  plugin.json
  skill/
    SKILL.md                      # Router + self-compaction flow
    references/
      compactor.md                # Full compactor logic (unified flow)
      rules.md                    # Centralized compaction rules
    examples/
      example-segment-manifest.md
      example-cut-plan-with-sidecars.md
      example-splice-result.md
    scripts/
      compact-analyze.py          # JSONL parse → segment manifest (JSON)
      compact-splice.py           # Re-synthesis splicer: cut-plan → new JSONL
      compact-discover.py         # Watermark → SESSION_ID + terminal/PID/cwd
```

The `skill/` layout (not `skills/<name>/`) matches the staged skill repository
convention used in this workspace and is the expected loader shape for these
single-skill packages.

---

## Skill Router (SKILL.md)

The SKILL.md is a thin router (~30 lines). It is always loaded when the skill is invoked.

### Router Logic

```
<mandatory>
If invoked with a SESSION_ID argument → Read references/compactor.md. Follow it exactly.
Ignore everything below this line.
</mandatory>
```

### Self-Compaction Mode (no arguments)

When a session invokes the skill with no arguments, SKILL.md provides prep instructions:

1. Generate a random UUID watermark: run `uuidgen`
2. Output exactly: `COMPACT_WATERMARK:<uuid>`
3. Discover own Claude PID from shell parent: `ps -o ppid= $$`, then verify parent args contain `claude`
4. Determine a summary prompt for the resumed self — what should you continue doing after compaction? Write it concisely.
5. Run `compact-discover.py <uuid> --pid <pid>` → returns JSON with `session_id`, `project_slug`, `terminal_launch`, `cwd`
6. Launch external compactor using the returned `terminal_launch` template:
   `<terminal_launch> claude "/smart-compact <session_id> --project-slug <project_slug> --orchestrate <pid> <resume_prompt>"`
7. Output: "Compaction launched. This session will be terminated shortly."
8. Stop output and wait for termination. Do NOT proceed with any other work. The compactor will kill this session.

---

## Compactor Logic (references/compactor.md)

Runs when SESSION_ID is provided — handles both user-invoked and orchestrated modes.

### Arguments

```
$SESSION_ID                          — Required. Target session to compact.
--project-slug $PROJECT_SLUG         — Optional. Project directory slug under
                                       ~/.claude/projects/ for faster lookup.
--orchestrate $PID "$RESUME_PROMPT"  — Optional. Kill PID, then relaunch after splice.
                                       RESUME_PROMPT is optional within --orchestrate.
```

### Unified Flow

**Phase 1: Kill** (ONLY if --orchestrate provided, otherwise skip to Phase 2)
1. Verify PID is alive: `kill -0 $PID`
2. Verify PID belongs to Claude: `ps -o args= -p $PID` must contain `claude`
3. Send SIGTERM: `kill $PID` (allows Claude Code to flush buffers and clean up)
4. Wait for graceful shutdown: retry `kill -0` up to 5 times with 2s sleep (10s total grace period)
5. If still alive: `kill -9 $PID` (escalate to SIGKILL)
6. Final verification: confirm PID is dead

**Phase 2: Analyze**
7. Record `INITIAL_CWD` fallback from current process (`pwd`, then `$HOME` fallback)
8. Run `compact-analyze.py $SESSION_ID --output .../manifest.json` (include `--project-slug` when available)
9. Read the manifest: segments, token estimates, metadata
10. Empty-session case (`No chain entries found`) aborts compaction early

**Phase 3: Decide** (Claude's job)
11. Read `references/rules.md` for compaction rules
12. Review the manifest summary: total segments, total estimated tokens, segment type distribution
13. Apply Context Budget Safety Valve when evaluate-load exceeds 30 segments (auto-summaries skip `--read-segment`)
14. Identify final non-Always-Drop segment and force KEEP
15. For each segment in the manifest, consult the segment-to-rule mapping table:
    - If rules say "always drop" → mark DROP
    - If rules say "always keep" → mark KEEP
    - If rules say "evaluate" → read segment content via `compact-analyze.py $SESSION_ID --read-segment $SEGMENT_ID`, then decide: KEEP, SUMMARIZE, or DROP (on read failure, default KEEP)
16. For each segment marked SUMMARIZE: write a concise summary capturing decisions, outcomes, and file changes. Do NOT include raw tool outputs, full file contents, or verbose exploration. Write each summary to `/tmp/smart-compact/$SESSION_ID/summary-$SEGMENT_ID.txt`.
17. Build cut-plan JSON and write to temp file:
```json
{
  "session_id": "...",
  "actions": [
    {"segment_id": 1, "action": "keep"},
    {"segment_id": 2, "action": "summarize", "summary_file": "/tmp/smart-compact/$SESSION_ID/summary-2.txt"},
    {"segment_id": 3, "action": "drop"}
  ]
}
```

**Phase 4: Splice**
18. Run `compact-splice.py $SESSION_ID --cut-plan /path/to/plan.json` (include `--project-slug` when available)
19. Verify result JSON shows `ok: true` and `chain_verification.ok: true`
20. If verification fails → report error and STOP (do not relaunch)
21. Track reduction stats; <5% is treated as negligible reduction in reporting

**Phase 5: Post-Splice**
- If `--orchestrate` provided → go to Section A (Orchestrated Relaunch)
- Otherwise → go to Section B (User Prompt)

#### Section A: Orchestrated Relaunch
22. Resolve relaunch cwd from manifest metadata; fallback to `INITIAL_CWD`, then `$HOME`
23. Detect terminal with `compact-discover.py --detect-terminal $$ --cwd <cwd>`
24. If terminal is undetected, output explicit manual resume command and stop
25. Build relaunch script with `env -u CLAUDECODE claude --resume ...` (escape `"`/`\` in resume prompt before substitution)
26. Launch via `nohup <terminal_launch with {command} replaced> ... &`, then `disown`
27. Exit. Compactor's job is done.

#### Section B: User Prompt
22. Output: "Session compacted successfully. [reduction stats]"
23. Ask user: "Launch resumed session?"
    - Yes → detect terminal, build resume script, launch
    - No → output: `env -u CLAUDECODE claude --resume $SESSION_ID`

---

## Compaction Rules (references/rules.md)

Centralized rules guiding segment-level decisions. Applied in order — first match wins.

### Segment Type → Rule Mapping Table

| Segment Type | Default Rule | Notes |
|---|---|---|
| `context_header` | Always Keep | First segment — session identity |
| `thinking` | Always Drop | Internal reasoning, never needed on resume |
| `progress` | Always Drop | Streaming markers, no content |
| `boundary` | Always Keep | Existing compact boundaries, preserve |
| `mcp_chain` | Aggressive Trim | MCP results are enormous, summarize aggressively |
| `error_chain` | Evaluate | May reveal workarounds — Claude decides |
| `tool_chain` (Read/Grep/Glob) | Aggressive Trim | Exploration results, keep only findings |
| `tool_chain` (Edit/Write) | Moderate Trim | Preserve what changed and why |
| `git_diff` | Aggressive Trim | Summarize files changed |
| `task_result` | Aggressive Trim | Subagent results, keep outcome only |
| `conversation` | Evaluate | May be critical decisions or casual chat |
| (final segment) | Always Keep | Last non-Always-Drop segment before compaction |

### Always Drop
- **Thinking blocks** (message content blocks with `"type": "thinking"`) — internal reasoning, never needed on resume
- **Progress entries** — streaming markers with no content

### Always Keep
- **Context header** — first segment of conversation (see "Context Header Definition" below)
- **Existing compact boundaries** — preserve prior compaction markers
- **Final segment** — the last segment that is not an Always Drop type
- **User preference statements** — explicit user instructions about workflow (if Claude identifies them during evaluation)

### Aggressive Trim (summarize to ~1-2 sentences)
- **MCP tool results** — summarize to "Queried [source], found [key result]"
- **Large git diffs** — summarize to files changed + nature of changes
- **File read results** — summarize to "Read [file], noted [key finding]"
- **Task/subagent results** — summarize to outcome only

### Moderate Trim (summarize to ~1 paragraph)
- **Built-in tool chains** (Edit/Write sequences) — preserve what was changed and why
- **Implementation blocks** — preserve decisions and file modifications, drop raw content
- **Debugging sequences** — preserve root cause and fix, drop exploration

### Evaluate (Claude reads and decides)
- **Error/retry chains** — may reveal bugs worked around rather than fixed; summarize noting that errors preceded success
- **Conversation blocks** — may contain critical decisions or casual chat
- **Mixed tool/conversation sequences** — context-dependent
- **Anything not matching above rules**

### Context Header Definition

The context header is the first N segments of the conversation, defined as: everything from the start until the first `tool_chain` or `mcp_chain` segment that is NOT immediately preceded by a conversation segment in the same interaction group. In practice, this captures the initial plan discussion, setup instructions, and any early exploration before the first substantial work block. Minimum: first interaction group. Maximum: first 10% of total segments.

### Context Budget Safety Valve

If the manifest contains more than 30 segments requiring evaluation, apply automatic collapse:
- `conversation` segments in the oldest 50% of the conversation → auto SUMMARIZE with generic summary
- `error_chain` segments in the oldest 50% → auto SUMMARIZE with error context
- Only evaluate segments in the newest 50%
- This prevents the compactor from exhausting its own context reading segment content

### Idempotency (Already-Compacted Sessions)

If the target session has been previously compacted (contains `boundary` segments from prior `/compact` operations):
- Existing `boundary` segments are Always Keep
- The summary text following a boundary is part of the next `conversation` segment — treated normally
- smart-compact can safely run on sessions that were previously compacted by `/compact` or by smart-compact itself

### Summary Entry Format

When replacing segments with summaries, the splicer emits a **user→assistant pair** to preserve turn structure:
- **User entry**: `"[smart-compact summary] <summary text>"`
- **Assistant entry**: `"Understood. Context from previous work has been preserved as a summary above."`

This prevents consecutive same-role messages which can confuse the model on resume. Both entries share the same `interaction_group_id` in the re-synthesized chain.

### Future: Mode Branching (not yet implemented)
```
--strict:  Aggressive Trim → Always Drop, Moderate Trim → Aggressive Trim
--relaxed: Aggressive Trim → Moderate Trim, Evaluate → default KEEP
```

---

## Python Scripts

### compact-discover.py

**Purpose:** Watermark-based session discovery + environment detection.

**Usage:**
```bash
compact-discover.py <WATERMARK_UUID> [--pid <PID>]
```

**Output (JSON to stdout):**
```json
{
  "session_id": "139bfece-...",
  "jsonl_path": "/home/user/.claude/projects/.../139bfece-....jsonl",
  "project_slug": "-home-user-project",
  "cwd": "/home/user/project",
  "terminal": "kitty",
  "terminal_launch": "kitty --directory /home/user/project -- {command}",
  "pid": 12345,
  "pid_alive": true
}
```

**Logic:**
1. Scan `~/.claude/projects/**/*.jsonl` for WATERMARK_UUID via `grep -rl`
   - Retry up to 5 times with 1s sleep (handles write-buffer flush delay)
   - If not found after retries → exit 2
2. Extract session_id from filename (filename IS the session ID)
3. Extract project_slug from parent directory name
4. Parse tail JSONL entries for cwd metadata
5. If `--pid` provided:
   - Walk process tree upward from PID via `/proc/$PID/status` (Linux) or `ps -o ppid=` (macOS fallback)
   - Identify terminal binary (kitty, gnome-terminal, wezterm, alacritty, etc.)
   - Map to launch template from built-in lookup table, substituting `cwd` from JSONL metadata
   - If terminal not recognized → `"terminal": null`, `"terminal_launch": null`

**Exit codes:** 0=success, 1=bad args, 2=watermark not found, 3=terminal not found (`--detect-terminal` mode only)

---

### compact-analyze.py

**Purpose:** Structural analysis of a session's JSONL. Produces a segment manifest.

**Usage:**
```bash
compact-analyze.py <SESSION_ID> [--project-slug <SLUG>]
compact-analyze.py <SESSION_ID> --read-segment <SEGMENT_ID>
```

The `--project-slug` is the directory name under `~/.claude/projects/` that contains the session's JSONL (e.g., `-home-user-project`). It's derived from the project's working directory with `/` replaced by `-` and the leading `-` preserved. When omitted, the script searches all project directories for a matching session ID.

**Mode 1 — Full analysis (default):**

Output is a JSON manifest containing:
- Session metadata (cwd, version, gitBranch)
- Total lines, chain length, estimated tokens
- Segments array, each containing:
  - `id`, `type`, `description`
  - `interaction_group_id` — links related segments (e.g., a user message, its tool calls, and the assistant response)
  - `line_range`, `chain_entry_count`
  - `estimated_tokens` (len/4 approximation)
  - `content_preview` (first ~200 chars)
  - `entry_uuids` (list of all chain entry UUIDs in segment)
  - `tool_names` (list of tool names used), `mcp_tools` (boolean), `has_errors` (boolean)
  - `non_chain_lines` — line numbers of non-chain entries (file-history-snapshot, metadata, etc.) that fall within this segment's line range

**Segment types (hyper-granular — each sub-type is its own segment):**
- `context_header` — first interaction group(s) of conversation (see rules.md for definition)
- `conversation` — user or assistant text with no tool use
- `tool_chain` — built-in tool use/result pairs (Read, Edit, Write, Grep, Glob, Bash, etc.)
- `mcp_chain` — MCP tool use/result pairs (name starts with `mcp__`)
- `task_result` — Task tool use + large subagent result
- `thinking` — assistant entry containing thinking content blocks
- `error_chain` — tool results with `is_error: true`
- `boundary` — compact_boundary system entries
- `git_diff` — Bash tool results containing diff output (detected by `diff --git` or `@@` markers)
- `progress` — streaming progress markers with no content

Note: the `mixed` type from earlier drafts is eliminated. Hyper-granular segmentation means each entry is classified by its specific structural type. Related entries are linked by `interaction_group_id` rather than merged into one segment. This allows rules to target `thinking` for DROP while keeping the `conversation` response in the same interaction group.

**Segmentation algorithm (hyper-granular with interaction grouping):**
1. Walk chain from root to leaf (chronological order)
2. Classify each chain entry by structural type:
   - `user` with text content → `conversation`
   - `assistant` with text content (no tool_use) → `conversation`
   - `assistant` with thinking content blocks and no user-visible text → `thinking`
   - `assistant` with both thinking and non-empty text → `conversation` (preserve text)
   - `assistant` with `tool_use` where name starts `mcp__` → `mcp_chain`
   - `assistant` with `tool_use` name=`Task` → `task_result`
   - `assistant` with `tool_use` (built-in) → `tool_chain`
   - `user` with `tool_result` where `is_error=true` → `error_chain`
   - `user` with `tool_result` containing diff markers → `git_diff`
   - `user` with `tool_result` (normal) → inherits type from preceding `tool_use`
   - `system` with `compact_boundary` → `boundary`
   - `progress` → `progress`
3. Create a new segment each time the structural type changes
4. Assign `interaction_group_id`: increment the group ID each time a new `user` text message (non-tool-result) appears. All entries between two user text messages share a group.
5. Context header: first interaction group(s) per rules.md definition
6. Token estimation: `len(text_content) / 4` per entry, summed per segment
7. Non-chain entry association: for each non-chain entry (file-history-snapshot, summary, etc.), assign it to the segment whose chain entries' line range contains it. Entries between segments default to the preceding segment.

**Mode 2 — Read segment (`--read-segment`):**

Returns the raw JSONL entry objects for a specific segment, so Claude can read actual content for summarization decisions.

**Exit codes:** 0=success, 1=bad args, 2=JSONL not found, 3=segment not found

---

### compact-splice.py

**Purpose:** Re-synthesis splicer. Takes a cut-plan, builds a completely new JSONL.

**Usage:**
```bash
compact-splice.py <SESSION_ID> --cut-plan <PATH_TO_PLAN_JSON>
    [--project-slug <SLUG>] [--no-backup]
```

**Cut-plan JSON format:**
```json
{
  "session_id": "...",
  "actions": [
    {"segment_id": 1, "action": "keep"},
    {"segment_id": 2, "action": "summarize", "summary_file": "/tmp/smart-compact/$SESSION_ID/summary-2.txt"},
    {"segment_id": 3, "action": "drop"},
    {"segment_id": 4, "action": "keep"}
  ]
}
```

Summary text is stored in sidecar files (one per summarized segment) rather than inline JSON strings. This avoids escaping issues with multi-line markdown, quotes, and special characters in summaries. The splicer reads each `summary_file` at splice time.

**Algorithm (re-synthesis):**
1. Parse original JSONL
2. Walk full chain root → leaf
3. **Safety check**: scan for unknown entry types. If any entry has a `type` not in the known set (`user`, `assistant`, `progress`, `system`, `file-history-snapshot`, `summary`, `saved_hook_context`, `queue-operation`, `pr-link`), log a warning. If the unknown type has a `uuid` (participates in chain), abort with exit code 4 — the JSONL format may have changed.
4. Load cut-plan and segment manifest (from compact-analyze.py cache or re-derive)
5. Build new chain in memory:
   - `keep`: copy all chain entries, preserve original UUIDs
   - `drop`: skip entirely
   - `summarize`: emit a **user→assistant pair** to preserve turn structure:
     - User entry: `"[smart-compact summary] <text from summary_file>"`
     - Assistant entry: `"Understood. Context from previous work has been preserved as a summary above."`
     - Both get new UUIDs; user entry's parentUuid → previous chain entry, assistant entry's parentUuid → user entry
6. Rewrite `parentUuid` pointers for transitions: the first entry after a summarize/drop action points to the last emitted entry (either the assistant ack from a summary pair, or the last keep entry).
7. Handle non-chain entries: use the `non_chain_lines` field from the segment manifest.
   - Non-chain entries in `keep` segments → preserved, inserted at their original relative position
   - Non-chain entries in `drop`/`summarize` segments → discarded
   - Non-chain entries between segments (not claimed by any segment) → preserved
8. Merge chain + kept non-chain entries, sorted by original line position
9. Verify new chain in-memory before writing:
    - All `keep` segment UUIDs reachable from leaf
    - Generated summary entries present (`all_summaries_present`)
    - Original UUIDs from summarized segments absent (`summarized_uuids_absent`)
    - No `drop` segment UUIDs reachable
    - Turn alternation flag is informational only (`turn_alternation_ok`)
10. On verification success: create timestamped backup (`.jsonl.bak-YYYYMMDD-HHMMSS-<random4>`) and write atomically (temp file → rename)
11. On verification failure: do not write

**Output (JSON to stdout):**
```json
{
  "ok": true,
  "original_lines": 1573,
  "new_lines": 420,
  "original_tokens_est": 185000,
  "new_tokens_est": 48000,
  "reduction_pct": 74.1,
  "segments_kept": 3,
  "segments_dropped": 2,
  "segments_summarized": 4,
  "backup_path": "...jsonl.bak-20260225-143022",
  "chain_verification": {
    "ok": true,
    "new_chain_length": 180,
    "all_keeps_reachable": true,
    "all_summaries_present": true,
    "summarized_uuids_absent": true,
    "no_drops_reachable": true,
    "turn_alternation_ok": true
  }
}
```

**Exit codes:** 0=success, 1=bad args, 2=JSONL not found, 3=cut-plan invalid, 4=chain integrity error, 5=verification failed, 6=unexpected error

---

## Session Discovery (No Hooks Required)

The watermark approach eliminates the need for any SessionStart hook:

1. Session generates a random UUID watermark
2. Session outputs it to conversation (writes to its JSONL)
3. `compact-discover.py <WATERMARK_UUID>` greps all JSONL files across all projects
4. Filename of matching file IS the session ID
5. Retry loop (up to 5 attempts, 1s intervals) handles write-buffer flush delay

This works even with multiple concurrent sessions in the same project — UUIDs are globally unique. One grep, one result.

---

## Terminal Detection

For orchestrated relaunch, the compactor needs to know how to open a new terminal window.

**Discovery:** Walk process tree upward from Claude's PID to find terminal binary.

**Lookup table (built into compact-discover.py):**

| Terminal | Launch Template |
|---|---|
| kitty | `kitty --directory {cwd} -- {command}` |
| gnome-terminal | `gnome-terminal --working-directory={cwd} -- {command}` |
| wezterm | `wezterm start --cwd {cwd} -- {command}` |
| alacritty | `alacritty --working-directory {cwd} -- {command}` |
| konsole | `konsole --workdir {cwd} -e {command}` |
| xterm | `xterm -e {command}` |

**Known limitations:**
- SSH sessions: cannot spawn terminal on remote
- Docker/containers: no GUI terminal available
- VS Code integrated terminal: cannot spawn new tab programmatically
- tmux/screen: nested session spawning is unreliable

When terminal is undetectable, the skill falls back to outputting the resume command for the user to run manually.

---

## Zombie Prevention

When the compactor launches a resumed session and then exits, the child process must survive:

- Use `nohup <command> &` to prevent SIGHUP on compactor exit
- Follow with `disown` to remove from job table
- The resumed session runs independently in its own terminal window

---

## Key Design Decisions

1. **Single operation** — All splices happen in one pass via re-synthesis, not multiple kill+resume cycles
2. **Re-synthesis over in-place editing** — Build a new chain in memory, write a fresh file. Avoids index corruption from non-contiguous cuts.
3. **Hyper-granular segmentation with interaction groups** — Each sub-type gets its own segment. Related entries are linked by `interaction_group_id`. This allows rules to target specific types (e.g., drop `thinking` while keeping the response in the same group) without losing the logical grouping context.
4. **Rules in markdown with explicit mapping table** — Compaction rules live in `rules.md` with a segment-type-to-rule mapping table. Easy to iterate and branch for future --strict/--relaxed modes.
5. **Python is structurally aware but semantically ignorant** — It maps, it doesn't judge. Semantic labels like "implementation" or "debugging" are Claude's job, not Python's. Python only reports structural types and tool names.
6. **Claude is the decision-maker** — Reads the map, reads the rules, decides what to cut, writes summaries.
7. **Summary pairs preserve turn structure** — Summaries are injected as user→assistant pairs to prevent consecutive same-role messages that confuse the model.
8. **Sidecar files for summaries** — Summary text stored in separate files, referenced by path in the cut-plan JSON. Avoids JSON escaping issues with complex markdown.
9. **Generic/public** — No project-specific dependencies. Works for any Claude Code user.
10. **No hooks required** — Watermark-based session discovery works universally.
11. **Token cost tracking** — Python estimates tokens per segment so Claude can prioritize high-cost targets.
12. **Context budget safety valve** — Auto-collapse old segments when evaluation load exceeds compactor capacity.
13. **Graceful degradation** — If terminal detection fails, falls back to manual resume command.
14. **PID verification before kill** — Compactor verifies the orchestrated PID still maps to a Claude process before signaling.
15. **Graceful kill with escalation** — SIGTERM first with 10s grace period, SIGKILL only as last resort. Allows Claude Code to flush buffers.
16. **Unknown entry type safety** — Splicer aborts if it encounters unknown chain-participating entry types, protecting against JSONL format changes.

---

## Open Questions

1. Summary length targets — proportional to original segment size, or fixed cap per segment?
2. macOS process tree walking — needs `ps`-based implementation since `/proc` doesn't exist. Core logic is the same, just different system calls.
3. Windows support — entirely different terminal/process model. Defer to future version.
4. Should the plugin bundle a SessionStart hook as an optional optimization (skip watermark when hook is available)?
5. Plugin distribution — Claude Code marketplace, standalone git repo, or both?
