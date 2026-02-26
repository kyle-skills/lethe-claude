# Lethe — Surgical JSONL Compaction Plugin Design

**Date:** 2026-02-25
**Last Updated:** 2026-02-26
**Status:** Active design (aligned with `skills/lethe/SKILL.md` v1.1.0)
**Scope:** Standalone Claude Code plugin for public release
**Dependencies:** Python 3.10+ (standard library only)

---

## Overview

Claude Code's built-in `/compact` produces one global summary and replaces the
entire prior transcript context. Lethe uses a segment-aware approach instead:

- preserve session identity and final working state
- drop deterministic noise (thinking/progress)
- summarize high-volume operational segments
- evaluate ambiguous segments semantically before deciding

The objective is surgical context reduction with safe resume continuity.

---

## Core Architecture: Python Eyes + Claude Brain

Two layers with explicit responsibilities:

### Python Layer (Structural Analysis + Splicing)

- Parses JSONL and walks the `parentUuid` chain
- Classifies chain entries into structural segment types
- Builds manifest metadata (segment counts, token estimates, previews, grouping)
- Re-synthesizes JSONL from cut-plan actions (`keep` / `summarize` / `drop`)
- Verifies chain integrity before atomic overwrite

Python stays structurally aware and semantically conservative.

### Claude Layer (Semantic Decisioning)

- Reads manifest + centralized rules table
- Applies deterministic rules first
- Evaluates only ambiguous segment classes via `--read-segment`
- Writes concise sidecar summaries for summarized segments
- Produces complete cut-plan covering all segments

Claude performs judgment; Python performs deterministic transformations.

---

## JSONL Mechanics

Empirically reflected in `lethe_utils.py` and splicer behavior:

- Session history is reconstructed by walking `parentUuid` links from leaf to root.
- Chain-participating types include:
  - `user`, `assistant`, `progress`
  - `system` entries whose `subtype` is in the allowed chain set (not only `compact_boundary`; also metadata-style subtypes and `microcompact_boundary`)
- Some UUID-bearing entries are bridge types (`saved_hook_context`) and are not emitted as chain entries; walker bridges through them.
- Unreachable entries are not resumed.
- Live-session writes are memory-resident in the running process; JSONL edits take effect after stop + resume.

---

## Plugin Structure

```text
lethe/
  .claude-plugin/
    plugin.json
  skills/
    lethe/
      SKILL.md
      references/
        compactor.md
        rules.md
      examples/
        example-segment-manifest.md
        example-cut-plan-with-sidecars.md
        example-splice-result.md
      scripts/
        lethe-discover.py
        lethe-analyze.py
        lethe-splice.py
        lethe_utils.py
```

---

## Skill Router (`skills/lethe/SKILL.md`)

### Router Mode Split

- If invoked with `SESSION_ID` argument:
  - validate UUID shape (`8-4-4-4-12` hex)
  - if invalid: stop with error
  - then follow `references/compactor.md` exactly
- If invoked with no args:
  - execute self-compaction flow

### Self-Compaction Mode (no arguments)

1. Generate watermark UUID (`uuidgen`, fallback to Python UUID generation).
2. Emit exact marker: `COMPACT_WATERMARK:<uuid>`.
3. Resolve parent Claude PID from shell PPID and verify process args contain `claude`.
4. Create concise resume prompt (1-2 sentences).
5. Run discovery:
   - `python3 scripts/lethe-discover.py <watermark_uuid> --pid $CLAUDE_PID`
6. Parse discovery JSON: `session_id`, `project_slug`, `terminal_launch`.
7. If terminal is undetected (`terminal_launch == null`), stop and print manual command:
   - `claude "/lethe <session_id> --project-slug <project_slug>"`
8. Otherwise create `/tmp/lethe/<session_id>/launch.sh` and launch compactor in a new terminal using:
   - `env -u CLAUDECODE`
   - `--permission-mode acceptEdits`
   - `/lethe <session_id> --project-slug <project_slug> --orchestrate <claude_pid> <resume_prompt>`
   - `nohup ... &` + `disown`
9. Emit: `Compaction launched. This session will be terminated shortly.`
10. Stop output and wait to be terminated by the compactor flow.

Implementation details baked into the skill:

- heredoc delimiter uses fresh UUID-based sentinel to avoid collisions
- resume prompt escaping is required (`\\` and `\"`)
- launch scripts intentionally live under `/tmp/lethe/<session_id>/` until reboot

---

## Autonomous Guardrails

From `SKILL.md`:

Proceed directly to self-compaction with no confirmation when:

- user explicitly asks for compaction
- prior instructions/plan already permit Lethe use
- autonomous plan execution is active and context is filling up

If Lethe has not been mentioned/permitted earlier:

1. require context usage > 70%
2. require substantial history (>= 15 interaction groups)
3. ask user confirmation before compacting
4. after decline, do not ask again until > 85%

---

## Compactor Protocol (`references/compactor.md`)

### Arguments

```text
$SESSION_ID                          required
--project-slug $PROJECT_SLUG         optional
--orchestrate $PID "$RESUME_PROMPT"  optional
```

- Without `--orchestrate`: target session must already be stopped.
- With `--orchestrate`: execute kill phase first, then analyze/decide/splice, then relaunch.

### Phase 1: Kill (only with `--orchestrate`)

1. `kill -0 $PID` liveness check
2. verify process args contain `claude`
3. `kill $PID` (SIGTERM)
4. wait up to 10s total (5x2s checks)
5. if still alive, `kill -9 $PID`
6. verify process is dead

Mandatory safety:

- never skip graceful SIGTERM attempt
- on abort in phases 1-4, cleanup `/tmp/lethe/$SESSION_ID/`

### Phase 2: Analyze

1. capture fallback cwd (`INITIAL_CWD`)
2. run analyzer:
   - `python3 scripts/lethe-analyze.py $SESSION_ID --output /tmp/lethe/$SESSION_ID/manifest.json`
   - optional: `--project-slug`
3. read manifest and distribution summary
4. handle empty chain case (`No chain entries found`) as non-compactable stop

### Phase 3: Decide

1. read `references/rules.md`
2. check evaluation load; if >30 evaluate segments, apply safety valve
3. force KEEP for final non-Always-Drop segment
4. for each segment:
   - Always Drop -> `drop`
   - Always Keep -> `keep`
   - Aggressive Trim -> `summarize` (1-2 sentences)
   - Moderate Trim -> `summarize` (3-5 sentence paragraph)
   - Evaluate -> read via:
     - `python3 scripts/lethe-analyze.py $SESSION_ID --read-segment $SEGMENT_ID`
     - on read failure: default KEEP
5. write sidecar files at `/tmp/lethe/$SESSION_ID/summary-<id>.txt`
6. write complete cut-plan `/tmp/lethe/$SESSION_ID/cut-plan.json`

### Phase 4: Splice

1. run splicer:
   - `python3 scripts/lethe-splice.py $SESSION_ID --cut-plan /tmp/lethe/$SESSION_ID/cut-plan.json`
   - optional: `--project-slug`
2. parse stdout JSON
3. require:
   - `ok: true`
   - `chain_verification.ok: true`
4. capture reduction metrics and segment action counts
5. if reduction <5%, report as negligible reduction

Failure handling:

- never proceed to post-splice when splice verification fails
- report error + backup/original preservation context
- cleanup `/tmp/lethe/$SESSION_ID/`

### Phase 5: Post-Splice

Branch exclusively by orchestration mode.

#### Section A: Orchestrated Relaunch (`--orchestrate`)

1. resolve relaunch cwd from manifest metadata, then `INITIAL_CWD`, then `$HOME`
2. detect terminal:
   - `python3 scripts/lethe-discover.py --detect-terminal $$ --cwd <cwd>`
3. if undetected: emit manual resume command
   - `env -u CLAUDECODE claude --resume <session-id> ["prompt"]`
4. otherwise write relaunch script in `/tmp/lethe/$SESSION_ID/relaunch.sh`
5. launch through terminal template with `nohup ... &` and `disown`
6. keep `/tmp/lethe/$SESSION_ID/` (ephemeral) for script lifecycle

#### Section B: User Prompt (no `--orchestrate`)

1. print success + reduction stats
2. ask whether to launch resumed session in new terminal
3. if no: print manual resume command
4. if yes: detect terminal, generate `/tmp/lethe/$SESSION_ID/resume.sh`, launch via template

---

## Compaction Rules (`references/rules.md`)

### Rule Precedence

1. Always Drop (`thinking`, `progress`)
2. Positional Always Keep (`context_header`, final non-Always-Drop segment)
3. Type-based mapping table for remaining segments

### Segment Type → Rule Mapping

| Segment Type | Rule | Notes |
|---|---|---|
| `context_header` | Always Keep | Positional session identity |
| `(final segment)` | Always Keep | Last non-Always-Drop segment |
| `boundary` | Always Keep | Preserve prior compaction boundaries |
| `thinking` | Always Drop | Internal reasoning |
| `progress` | Always Drop | Streaming/metadata markers |
| `mcp_chain` | Aggressive Trim | Large MCP outputs |
| `error_chain` | Evaluate | Keep unresolved, summarize resolved, drop transient |
| `tool_chain` (Read/Grep/Glob) | Aggressive Trim | Keep findings only |
| `tool_chain` (Edit/Write) | Moderate Trim | Preserve what changed + why |
| `tool_chain` (other built-ins) | Aggressive Trim | Include Bash and non-targeted tools |
| `task_result` | Aggressive Trim | Outcome only |
| `git_diff` | Aggressive Trim | Files + change intent |
| `conversation` | Evaluate | Decision content vs casual chat |

Additional rule details:

- `microcompact_boundary` is classified as progress (Always Drop).
- During Evaluate, explicit user preferences/instructions are Always Keep.
- For mixed tool types in one segment, use conservative rule (Moderate over Aggressive).

### Context Header Definition

Header spans from session start until first substantial work boundary, with:

- minimum: first interaction group
- maximum: first 10% of segments (min 1)
- thinking/progress skipped for boundary detection and remain droppable

### Context Budget Safety Valve

Trigger when evaluate segments > 30:

- oldest 50% evaluate-able `conversation` -> auto summarize
- oldest 50% evaluate-able `error_chain` -> auto summarize
- evaluate only latter 50%
- write sidecars for safety-valve summaries like normal summaries

### Summary Format Contract

Splicer injects summary replacement as user-assistant pair:

- User: `[lethe summary] <summary text>`
- Assistant: `Understood. Context from previous work has been preserved as a summary above.`

Sidecar files store only summary text (without prefix).

### Idempotency

- Existing `boundary` segments are preserved.
- Existing summary content is treated as normal conversation segments.
- Prefer KEEP for prior Lethe summaries unless clearly redundant.
- Segments immediately after boundary are handled conservatively.

### Future Modes (deferred)

```text
--strict:  Aggressive Trim -> Always Drop, Moderate Trim -> Aggressive Trim
--relaxed: Aggressive Trim -> Moderate Trim, Evaluate -> KEEP
```

---

## Python Scripts

### `lethe-discover.py`

Purpose:

- find session JSONL from watermark
- extract `session_id`, `project_slug`, `cwd`
- detect terminal by parent-process walk
- return launch template with `{command}` placeholder

Modes:

```bash
lethe-discover.py <WATERMARK_UUID> [--pid <PID>]
lethe-discover.py --detect-terminal <PID>
```

Exit codes:

- `0` success
- `1` bad args
- `2` watermark not found
- `3` terminal not found in detect mode

### `lethe-analyze.py`

Purpose:

- parse and classify chain entries
- build segment manifest for decision phase
- expose raw segment reads for Evaluate segments

Usage:

```bash
lethe-analyze.py <SESSION_ID> [--project-slug <SLUG>] [--output <PATH>]
lethe-analyze.py <SESSION_ID> --read-segment <SEGMENT_ID> [--project-slug <SLUG>]
```

Exit codes:

- `0` success
- `1` bad args
- `2` JSONL not found
- `3` segment not found

### `lethe-splice.py`

Purpose:

- apply cut-plan to produce new JSONL
- inject summary pairs for summarized segments
- verify chain invariants before writing
- write atomically with backup by default

Usage:

```bash
lethe-splice.py <SESSION_ID> --cut-plan <PATH_TO_PLAN_JSON> [--project-slug <SLUG>] [--no-backup]
```

Exit codes:

- `0` success
- `1` bad args
- `2` JSONL not found
- `3` cut-plan invalid
- `4` unknown chain-participating entry type
- `5` verification failed
- `6` unexpected error

---

## Terminal Detection

`lethe-discover.py` supports these terminal templates:

| Terminal | Launch Template |
|---|---|
| kitty | `kitty --directory {cwd} -- {command}` |
| gnome-terminal | `gnome-terminal --working-directory={cwd} -- {command}` |
| wezterm | `wezterm start --cwd {cwd} -- {command}` |
| alacritty | `alacritty --working-directory {cwd} -- {command}` |
| konsole | `konsole --workdir {cwd} -e {command}` |
| xterm | `xterm -e {command}` |
| foot | `foot --working-directory={cwd} {command}` |
| ghostty | `ghostty -e {command}` |
| urxvt | `urxvt -cd {cwd} -e {command}` |

Aliases handled:

- `gnome-terminal-server` / `gnome-terminal-*` -> `gnome-terminal`
- `wezterm-gui` -> `wezterm`
- `kitty-main` -> `kitty`

If detection fails, Lethe falls back to manual resume commands.

---

## Key Design Decisions

1. Re-synthesis over in-place mutation to avoid parent pointer corruption.
2. Segmentation is structural and deterministic; semantic trimming decisions happen in Claude.
3. Decision logic is centralized in `rules.md` with explicit precedence.
4. Summary replacements are standardized user-assistant pairs for stable turn shape.
5. Context-budget safety valve avoids compactor self-exhaustion.
6. Unknown chain-type detection fails closed to avoid silent data corruption.
7. Orchestrated flow prioritizes safe shutdown (SIGTERM first, SIGKILL last).
8. No hooks required; watermark discovery is sufficient for session targeting.

---

## Open Questions

1. Should Lethe add optional Windows-specific terminal detection and launch templates?
2. Should summary acknowledgment text be configurable while preserving turn-structure guarantees?
3. Should future strict/relaxed modes be exposed as user-facing flags in v1.1?
