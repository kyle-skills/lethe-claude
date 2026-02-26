---
name: smart-compact
description: >-
  This skill should be used when the user asks to "compact this session",
  "smart compact", "surgical compaction", "compact my context", or
  "trim my conversation", or when running as a compactor with a SESSION_ID argument. It can also be invoked proactively when context
  usage exceeds 70% and the session has substantial work history, provided
  explicit user confirmation is obtained first. Performs intelligent,
  segment-level JSONL compaction that preserves critical context while
  surgically removing tool output bloat, thinking blocks, and stale exploration.
version: 1.0
---

<sections>
- router
- self-compaction
- autonomous-guardrails
</sections>

# smart-compact

<context>
Surgical JSONL conversation compaction. Python scripts handle structural analysis
(parse JSONL, walk parentUuid chain, classify segments by type). Claude makes
semantic KEEP/SUMMARIZE/DROP decisions per segment using centralized rules.
Two modes: self-compaction (no arguments) and compactor (with SESSION_ID).
Do not run multiple compactions on the same session simultaneously.
</context>

<guidance>
Script paths in this skill (e.g., `scripts/compact-discover.py`) are relative to
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
<core>
## Self-Compaction Mode (no arguments)

1. Generate watermark: run `uuidgen` (or `python3 -c "import uuid; print(uuid.uuid4())"`)
   and capture the result. If both fail, STOP and report the error.
2. Output exactly: `COMPACT_WATERMARK:<uuid>` (this writes the watermark to the JSONL)
3. Discover own PID: run `ps -o ppid= $$` to get the parent PID, then walk up
   the process tree by repeating `ps -o ppid= <pid>`. At each level, check the
   full command line with `ps -o args= <pid>` (or read `/proc/<pid>/cmdline` on
   Linux). Stop when the command line contains `claude`. Note: the binary may be
   named `node`, so check `args`/`cmdline` not just `comm`. Record as `$CLAUDE_PID`.
   If PID 1 is reached without finding `claude`, STOP and report: "Could not find
   Claude process in process tree."
4. Determine a resume prompt: write a concise 1-2 sentence summary of what this
   session should continue doing after compaction completes.
5. Run discovery:
   `python3 scripts/compact-discover.py <watermark_uuid> --pid $CLAUDE_PID`
   If the script exits non-zero, report the error from stderr and STOP.
6. Parse the JSON output: extract `session_id`, `terminal_launch`
7. If `terminal_launch` is null (terminal undetectable):
   Output: "Terminal could not be detected. To compact this session manually, run:"
   `claude "/smart-compact <session_id>"`
   where `<session_id>` is from the discovery output. STOP — do not proceed.
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
   Substitute `<session_id>`, `<claude_pid>`, and `<resume_prompt>` with actual
   values. Use the heredoc boundary to avoid quote-escaping issues.
   Note: if the resume prompt contains single quotes, escape them as `'\''`
   before inserting into the heredoc.
   Then launch via the terminal template:
   `nohup <terminal_launch with {command} replaced by /tmp/smart-compact/<session_id>/launch.sh> > /dev/null 2>&1 &`
   followed by `disown`.
   The launch script at `/tmp/smart-compact/<session_id>/` persists until system
   reboot (`/tmp` is ephemeral). No explicit cleanup is needed.
   - `--permission-mode acceptEdits` is required — the compactor runs kill commands,
     writes to /tmp, and modifies JSONL files in ~/.claude/projects/.
   - `env -u CLAUDECODE` prevents nested session conflicts.
   If the launch command fails, report the error and provide the manual command:
   `claude "/smart-compact <session_id>"`
9. Output: "Compaction launched. This session will be terminated shortly."
10. STOP — do not generate any further responses or tool calls. The compactor
    will terminate this session. Do not proceed with any other work.
</core>
</section>

<section id="autonomous-guardrails">
<mandatory>
## When to Compact

Proceed directly to self-compaction — no confirmation needed — in any of
these cases:

- The user explicitly asks to compact
- An implementation plan, task instructions, or prior conversation mentions
  smart-compact as available or permitted (e.g., "use smart-compact if needed")
- The session is operating autonomously under a plan and context is filling up
</mandatory>

<guidance>
### Proactive Invocation (no prior mention of smart-compact)

If smart-compact has never been mentioned or permitted in the session context,
and Claude determines compaction would be beneficial, ask first:

1. Context usage must exceed 70% (from system context messages). If context
   percentage is unavailable, do not invoke proactively.
2. The session must have substantial history (at least 15 interaction groups).
3. Ask: "Context is at [X]%. I can perform a smart-compact to free up space
   while preserving key decisions and context. This will briefly restart the
   session. Proceed?"
4. If declined, do not suggest again until context exceeds 85%.
</guidance>
</section>
