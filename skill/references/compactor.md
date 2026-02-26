<skill name="lethe-compactor" version="1.0">

<metadata>
type: reference
parent-skill: lethe
tier: 3
protocol: Compactor Protocol
permissions: Bash (kill, cat, mkdir, chmod, nohup, ps, python3, uuidgen)
</metadata>

<sections>
- arguments
- phase-1-kill
- phase-2-analyze
- phase-3-decide
- phase-4-splice
- phase-5-post-splice
</sections>

<section id="arguments">
<core>
# Compactor Protocol

## Arguments

```
$SESSION_ID                          — Required. Target session to compact.
--project-slug $PROJECT_SLUG         — Optional. Project directory slug under
                                       ~/.claude/projects/ for faster JSONL lookup.
--orchestrate $PID "$RESUME_PROMPT"  — Optional. Kills the calling session's PID,
                                       then relaunches after splice.
                                       RESUME_PROMPT is optional within --orchestrate.
                                       If omitted, session resumes with no initial prompt.
```

Parse these from the skill invocation arguments:
- First token is SESSION_ID.
- If present, `--project-slug` takes one value (`PROJECT_SLUG`).
- If `--orchestrate` follows: next token is PID, remainder is RESUME_PROMPT
  (may be empty).
If only SESSION_ID is provided, skip Phase 1 and begin at Phase 2.

When `--orchestrate` is not provided, the target session must already be
stopped. Compacting a live session risks data loss — entries written after
the read but before the atomic rename are silently overwritten.
</core>
</section>

<section id="phase-1-kill">
<mandatory>
## Phase 1: Kill (ONLY if --orchestrate provided)

Skip this phase entirely if --orchestrate was not provided. Proceed to Phase 2.
</mandatory>

<core>
1. **Verify PID is alive**: `kill -0 $PID` — if already dead, skip to Phase 2.
2. **Verify PID belongs to Claude**: run `ps -o args= -p $PID`. The result
   must contain `claude`. If not, abort with error: "PID $PID does
   not appear to be a Claude process. Aborting to prevent
   killing an unrelated process."
3. **Send SIGTERM**: `kill $PID` — allows Claude Code to flush buffers and clean up.
4. **Wait for graceful shutdown**: retry `kill -0 $PID` up to 5 times with 2s sleep
   between attempts (10s total grace period).
5. **Escalate if needed**: if still alive after grace period, `kill -9 $PID`.
6. **Final verification**: confirm PID is dead with `kill -0 $PID` (expect failure).
</core>

<mandatory>
- Never send SIGKILL without first attempting SIGTERM and waiting the full
  grace period. SIGKILL prevents buffer flushing and may corrupt the JSONL.
- If aborting at any phase, clean up the working directory (if it exists):
  `rm -rf /tmp/lethe/$SESSION_ID/` before stopping.
</mandatory>
</section>

<section id="phase-2-analyze">
<core>
## Phase 2: Analyze

1. Record an initial cwd fallback for post-splice relaunch:
   `INITIAL_CWD="$(pwd 2>/dev/null || printf '%s' "$HOME")"`
2. Run structural analysis:
   ```bash
   mkdir -p /tmp/lethe/$SESSION_ID
   python3 scripts/lethe-analyze.py $SESSION_ID \
     --output /tmp/lethe/$SESSION_ID/manifest.json
   ```
   If `PROJECT_SLUG` is available, pass
   `--project-slug "$PROJECT_SLUG"` for faster JSONL discovery.
3. Read the manifest from `/tmp/lethe/$SESSION_ID/manifest.json`.
4. Review the manifest summary: total segments, total estimated tokens,
   segment type distribution.
5. If analyze fails with "No chain entries found in JSONL", treat it as an
   empty/uncompactable session and STOP.

If the script exits non-zero, report the error and STOP. If stdout is empty,
inspect stderr first. Do not proceed
to Phase 3 with a missing or incomplete manifest.
</core>
</section>

<section id="phase-3-decide">
<core>
## Phase 3: Decide

This is the semantic decision phase. Claude reads the manifest, applies rules,
and produces a cut-plan.

1. Read `references/rules.md` (relative to skill root) for compaction rules
   and the segment-to-rule mapping table.
2. Review the manifest summary to understand the session's shape: how many
   segments of each type, total token estimate, interaction group distribution.
3. **Context Budget Safety Valve check**: if the manifest lists more than
   30 segments that require evaluation (segments whose rule is "Evaluate"),
   apply the Context Budget Safety Valve from rules.md before proceeding.
   Safety-valve segments are auto-SUMMARIZE, skip `--read-segment`, and still
   write summary sidecar files in step 6.
4. Explicitly identify the **final segment**: the last segment whose type is
   not an Always Drop type (`thinking`, `progress`). Force KEEP on this segment.
5. For each segment in the manifest, look up its type in the segment-to-rule
   mapping table in `references/rules.md` (relative to skill root) and apply
   the corresponding action:
   - **Always Drop** → mark DROP
   - **Always Keep** → mark KEEP
   - **Aggressive Trim** → mark SUMMARIZE (target: 1-2 sentences)
   - **Moderate Trim** → mark SUMMARIZE (target: 1 paragraph, 3-5 sentences)
   - **Evaluate** → read the segment content via:
     ```bash
     python3 scripts/lethe-analyze.py $SESSION_ID --read-segment $SEGMENT_ID
     ```
     `--read-segment` returns a JSON array of raw chain entry objects.
     Then decide: KEEP, SUMMARIZE, or DROP based on the content and the
     evaluation guidance in rules.md.
     If `--read-segment` exits non-zero or returns unusable output, default that
     segment to KEEP and continue.
   The mapping table is authoritative — it covers all segment types including
   `git_diff`, `task_result`, and tool sub-types. Do not hardcode type-to-rule
   mappings here; always consult the table.
6. For each segment marked SUMMARIZE: write a concise summary to a sidecar file.
   ```bash
   mkdir -p /tmp/lethe/$SESSION_ID
   # Write summary to: /tmp/lethe/$SESSION_ID/summary-$SEGMENT_ID.txt
   ```
7. Build the cut-plan JSON and write to `/tmp/lethe/$SESSION_ID/cut-plan.json`:
   ```json
   {
     "session_id": "...",
     "actions": [
       {"segment_id": 1, "action": "keep"},
       {"segment_id": 2, "action": "summarize", "summary_file": "/tmp/lethe/$SESSION_ID/summary-2.txt"},
       {"segment_id": 3, "action": "drop"},
       {"segment_id": 4, "action": "keep"}
     ]
   }
   ```
</core>

<mandatory>
- Always `mkdir -p /tmp/lethe/$SESSION_ID/` before writing any sidecar files.
- Summaries focus on WHAT was decided and WHAT changed — not HOW.
  Never include raw file contents, full tool outputs, verbose exploration steps,
  or internal reasoning in summaries.
- The cut-plan must account for every segment in the manifest. No segment
  may be left unaddressed — if unsure, default to KEEP.
- Summary length is independent of segment size. Even a 100k-token segment
  gets 1-2 sentences for Aggressive Trim. Do not scale summary length
  proportionally to original size.
</mandatory>

<guidance>
Summary length targets by trim level:
- **Aggressive Trim**: 1-2 sentences. Focus on the key outcome or finding.
- **Moderate Trim**: 1 short paragraph (3-5 sentences). Preserve what changed,
  why it was changed, and any decisions made.
- **Evaluate → SUMMARIZE**: Use Aggressive or Moderate length based on the
  information density of the content.
</guidance>
</section>

<section id="phase-4-splice">
<core>
## Phase 4: Splice

1. Run the re-synthesis splicer:
   ```bash
   python3 scripts/lethe-splice.py $SESSION_ID \
     --cut-plan /tmp/lethe/$SESSION_ID/cut-plan.json
   ```
   If `PROJECT_SLUG` is available, pass `--project-slug "$PROJECT_SLUG"`.
2. Parse the result JSON from stdout.
3. Verify the result shows `"ok": true` and `chain_verification.ok: true`.
4. Record the reduction stats for reporting:
   `original_tokens_est`, `new_tokens_est`, `reduction_pct`,
   `segments_kept`, `segments_dropped`, `segments_summarized`.
5. If `reduction_pct` is below 5%, call it a negligible reduction in the
   final report so users can decide whether future compactions are worth it.
</core>

<mandatory>
Never proceed past a failed splice. A broken JSONL means the session cannot
be resumed safely. If `ok` is false or chain verification fails:
1. Report the error details.
2. If `backup_path` is present, note it. If null, state that the original JSONL
   was not overwritten.
3. Clean up working directory: `rm -rf /tmp/lethe/$SESSION_ID/`
4. STOP. Do not attempt Phase 5.

If the splicer exits non-zero and stdout is empty, inspect stderr first.

Note: if the compactor aborts at any phase (1-4), always clean up
`/tmp/lethe/$SESSION_ID/` before stopping to avoid stale artifacts.
</mandatory>
</section>

<section id="phase-5-post-splice">
<mandatory>
## Phase 5: Post-Splice

If --orchestrate was provided → follow Section A. Otherwise → follow Section B.
Do not mix sections. Execute exactly one.

Before branching into Section A or B, resolve permission configuration:
```bash
python3 scripts/lethe-config.py --project-dir <cwd>
```
where `<cwd>` is from the Phase 2 manifest metadata (or `INITIAL_CWD` fallback).
Parse the JSON output and extract `resume_permission`.
If `resume_permission` is null, omit `--permission-mode` from all relaunch/resume
commands below. If non-null, include `--permission-mode <resume_permission>`.
</mandatory>

<core>
### Section A: Orchestrated Relaunch (--orchestrate provided)

1. Retrieve `cwd` from the Phase 2 manifest metadata. If null/empty, use
   `INITIAL_CWD` from Phase 2 step 1. If that is unavailable, use `$HOME`.
2. Detect the terminal for relaunch:
   ```bash
   python3 scripts/lethe-discover.py --detect-terminal $$ --cwd <cwd>
   ```
   where `<cwd>` is the value retrieved from manifest metadata in step 1.
   Parse the JSON output: extract `terminal` and `terminal_launch`.
   If `terminal` is null, output:
   "Terminal not detected. Exit this session first, then run:
   `env -u CLAUDECODE claude [--permission-mode <resume_permission>] --resume $SESSION_ID`"
   Include `--permission-mode <resume_permission>` only when `resume_permission` is non-null.
   If `RESUME_PROMPT` is non-empty, append it as a single double-quoted trailing
   argument so the final command looks like:
   `env -u CLAUDECODE claude [--permission-mode <resume_permission>] --resume <session-id> "prompt text"`
   Do not emit doubled wrapping like `""prompt text""`. Then stop.
3. Build a launch script to avoid nested quoting:
   ```bash
   DELIM="RELAUNCH_$(uuidgen | tr -d '-')"
   cat > /tmp/lethe/$SESSION_ID/relaunch.sh << "$DELIM"
   #!/bin/bash
   exec env -u CLAUDECODE claude [--permission-mode <resume_permission>] --resume <session-id> "<resume-prompt>"
   $DELIM
   chmod +x /tmp/lethe/$SESSION_ID/relaunch.sh
   ```
   Substitute `<session-id>` and `<resume-prompt>` with actual values in the
   heredoc content. The resume prompt must be double-quoted in the exec line.
   Escape backslashes (`\` → `\\`) and double quotes (`"` → `\"`) in the
   prompt before substitution.
   Include `--permission-mode <resume_permission>` only when `resume_permission` is non-null.
   If RESUME_PROMPT was not provided, omit it from the `claude --resume` command.
   The UUID-based heredoc delimiter prevents injection if the resume prompt
   contains a delimiter string.
4. Replace `{command}` in `terminal_launch` with the relaunch script path.
   Launch via the terminal template:
   ```bash
   nohup <terminal_launch with {command} replaced> > /dev/null 2>&1 &
   ```
   `env -u CLAUDECODE` prevents nested session conflicts.
5. `disown` the background process.
6. The working directory at `/tmp/lethe/$SESSION_ID/` is ephemeral
   and will be cleaned up on system reboot. Do not delete it — the relaunch
   script may still be in use.
7. Exit. The compactor's job is done.

### Section B: User Prompt (--orchestrate NOT provided)

1. Output compaction results:
   "Session $SESSION_ID compacted successfully.
   Reduction: [original_tokens_est] → [new_tokens_est] tokens ([reduction_pct]%).
   Segments: [kept] kept, [summarized] summarized, [dropped] dropped."
2. Ask the user: "Launch the resumed session in a new terminal?"
   - If no: output the manual command:
     `env -u CLAUDECODE claude [--permission-mode <resume_permission>] --resume $SESSION_ID`
     Include `--permission-mode` only when `resume_permission` is non-null. Then stop.
   - If yes: continue to step 3.
3. Retrieve `cwd` from the Phase 2 manifest metadata. If null/empty, use
   `INITIAL_CWD` from Phase 2 step 1. If that is unavailable, use `$HOME`.
4. Detect the terminal for relaunch:
   ```bash
   python3 scripts/lethe-discover.py --detect-terminal $$ --cwd <cwd>
   ```
   Parse the JSON output: extract `terminal` and `terminal_launch`.
   If `terminal` is null, output the manual command:
   "Exit this session first, then run:
   `env -u CLAUDECODE claude [--permission-mode <resume_permission>] --resume $SESSION_ID`"
   Include `--permission-mode` only when `resume_permission` is non-null. Then stop.
5. Build a launch script to avoid nested quoting:
   ```bash
   DELIM="RESUME_$(uuidgen | tr -d '-')"
   cat > /tmp/lethe/$SESSION_ID/resume.sh << "$DELIM"
   #!/bin/bash
   exec env -u CLAUDECODE claude [--permission-mode <resume_permission>] --resume <session-id>
   $DELIM
   chmod +x /tmp/lethe/$SESSION_ID/resume.sh
   ```
   Substitute `<session-id>` with the actual session ID.
   Include `--permission-mode <resume_permission>` only when `resume_permission` is non-null.
   `env -u CLAUDECODE` prevents nested session conflicts.
6. Launch via the terminal template. Replace `{command}` in `terminal_launch`
   with `/tmp/lethe/$SESSION_ID/resume.sh`:
   ```bash
   nohup <terminal_launch with {command} replaced> > /dev/null 2>&1 &
   ```
   followed by `disown`.
7. The working directory at `/tmp/lethe/$SESSION_ID/` will be cleaned
   up on system reboot.
</core>
</section>

</skill>
