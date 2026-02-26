---
name: lethe
description: >-
  Lethe — a surgical context compacter. A measured sip from the river of
  forgetfulness. Use when the user asks to "compact", "compact this session",
  "compact my context", "trim my conversation", or when running as a
  compactor with a SESSION_ID argument. Can also be used proactively when
  context usage exceeds 70% and autonomous compaction is permitted.
version: 1.0.0
---

<sections>
- router
- self-compaction
- autonomous-guardrails
</sections>

# Lethe

<context>
You are executing the Lethe protocol, a surgical memory-management skill.
Like a sip from the mythical river of oblivion, your task is to selectively
wash away intermediate reasoning, deprecated file reads, and successful tool
outputs from the transcript, while strictly preserving the session's core
context and architectural decisions. Two modes: self-compaction (no arguments)
and compactor mode (SESSION_ID argument).
</context>

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

<reference path="examples/example-splice-result.md" load="recommended">
Example splice result JSON with verification field reading guide.
</reference>

<guidance>
Script paths in this skill (e.g., `scripts/lethe-discover.py`) are relative to
the skill's base directory, which is provided in the context when the skill is
loaded via the Skill tool.
</guidance>

<section id="router">
<mandatory>
If invoked with a SESSION_ID argument:
Validate that SESSION_ID is a valid UUID (8-4-4-4-12 hex format). If not,
report the error and STOP — do not proceed with an invalid session identifier.
Read references/compactor.md. Follow it exactly. The sections below are for
self-compaction mode only and do not apply when operating as a compactor.
</mandatory>
</section>

<section id="self-compaction">
<mandatory>
- Never run concurrent compactions for the same session ID.
</mandatory>

<core>
## Self-Compaction Mode (no arguments)

1. Generate watermark: run `uuidgen` (or `python3 -c "import uuid; print(uuid.uuid4())"`)
   and capture the result. If both fail, STOP and report the error.
2. Output exactly: `COMPACT_WATERMARK:<uuid>` (this writes the watermark to the JSONL)
3. Discover own Claude PID using a single parent lookup:
   `CLAUDE_PID="$(ps -o ppid= $$ | tr -d ' ')"`
   Then verify with `ps -o args= -p "$CLAUDE_PID"` and require `claude` in args.
   If the verify step fails, STOP and report:
   "Could not verify Claude parent process from shell PPID."
4. Determine a resume prompt: write a concise 1-2 sentence summary of what this
   session should continue doing after compaction completes.
5. Run discovery:
   `python3 scripts/lethe-discover.py <watermark_uuid> --pid $CLAUDE_PID`
   If the script exits non-zero, report the error from stderr and STOP.
6. Parse the JSON output: extract `session_id`, `project_slug`, `terminal_launch`.
   `cwd` is also returned, but in self-compaction mode it is already pre-resolved
   inside `terminal_launch`.
7. If `terminal_launch` is null (terminal undetectable):
   Output: "Terminal could not be detected. Exit this session first, then run:"
   `claude "/lethe <session_id> --project-slug <project_slug>"`
   where `<session_id>` is from the discovery output. STOP — do not proceed.
8. Build a launch script to avoid nested quoting issues:
   ```bash
   mkdir -p /tmp/lethe/<session_id>
   DELIM="LAUNCH_$(uuidgen | tr -d '-')"
   cat > /tmp/lethe/<session_id>/launch.sh << "$DELIM"
   #!/bin/bash
   exec env -u CLAUDECODE claude --permission-mode acceptEdits \
     "/lethe <session_id> --project-slug <project_slug> --orchestrate <claude_pid> <resume_prompt>"
   $DELIM
   chmod +x /tmp/lethe/<session_id>/launch.sh
   ```
   Substitute `<session_id>`, `<project_slug>`, `<claude_pid>`, and
   `<resume_prompt>` with actual values. Use the heredoc boundary to avoid
   delimiter-collision issues.
   Because the `/lethe ...` command string is double-quoted, escape
   backslashes (`\` → `\\`) and double quotes (`"` → `\"`) in the resume prompt
   before substitution.
   Then launch via the terminal template:
   `nohup <terminal_launch with {command} replaced by /tmp/lethe/<session_id>/launch.sh> > /dev/null 2>&1 &`
   followed by `disown`.
   The launch script at `/tmp/lethe/<session_id>/` persists until system
   reboot (`/tmp` is ephemeral). No explicit cleanup is needed.
   `uuidgen` is reused here for the delimiter; availability was already verified
   in step 1.
   - `--permission-mode acceptEdits` is required — the compactor runs kill commands,
     writes to /tmp, and modifies JSONL files in ~/.claude/projects/.
   - `env -u CLAUDECODE` prevents nested session conflicts.
   If the launch command fails, report the error and provide the manual command:
   "Exit this session first, then run: `claude \"/lethe <session_id> --project-slug <project_slug>\"`"
9. Output: "Compaction launched. This session will be terminated shortly."
10. Stop output and wait for termination. Do not run additional tools or emit
    follow-up text; the compactor will terminate this session shortly.
</core>
</section>

<section id="autonomous-guardrails">
<mandatory>
## When to Compact

Proceed directly to self-compaction — no confirmation needed — in any of
these cases:

- The user explicitly asks to compact
- An implementation plan, task instructions, or prior conversation mentions
  Lethe as available or permitted (e.g., "use Lethe if needed")
- The session is operating autonomously under a plan and context is filling up
- If no prior mention/permission exists, follow the proactive threshold checks
  in the guidance below (first threshold: 70% context usage).
</mandatory>

<guidance>
### Proactive Invocation (no prior mention of Lethe)

If Lethe has never been mentioned or permitted in the session context,
and Claude determines compaction would be beneficial, ask first:

1. Context usage must exceed 70% (from system context messages). If context
   percentage is unavailable, do not invoke proactively.
2. The session must have substantial history (at least 15 interaction groups).
3. Ask: "Context is at [X]%. I can perform a Lethe compaction to free up space
   while preserving key decisions and context. This will briefly restart the
   session. Proceed?"
4. If declined, do not suggest again until context exceeds 85%.
</guidance>
</section>
