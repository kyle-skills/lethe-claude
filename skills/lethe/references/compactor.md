<skill name="lethe-compactor" version="1.3">

<metadata>
type: reference
parent-skill: lethe
tier: 3
protocol: Compactor Protocol
permissions: Bash (kill, cat, mkdir, chmod, nohup, ps, python3, uuidgen)
</metadata>

<sections>
- arguments
- communication-mode
- phase-1-kill
- phase-2-analyze
- phase-3-decide
- phase-4-splice
- phase-4-standard-compact-fallback
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
--fallback-resume-permission MODE    — Optional. Caller-provided fallback for the
                                       resumed session's --permission-mode. Only used
                                       when no env var or .lethe_config specifies
                                       resume_permission. Intended for orchestration
                                       callers (e.g., Souffleur).
```

Parse these from the skill invocation arguments:
- First token is SESSION_ID.
- If present, `--project-slug` takes one value (`PROJECT_SLUG`).
- If `--orchestrate` follows: next token is PID, remainder is RESUME_PROMPT
  (may be empty).
- If `--fallback-resume-permission` follows: next token is MODE.
If only SESSION_ID is provided, skip Phase 1 and begin at Phase 2.

When `--orchestrate` is not provided, the target session must already be
stopped. Compacting a live session risks data loss — entries written after
the read but before the atomic rename are silently overwritten.
</core>
</section>

<section id="communication-mode">
<mandatory>
## Communication Mode Detection

Before Phase 1, determine whether `AskUserQuestion` is available.

- If available: `COMM_MODE=interactive`
- If unavailable: `COMM_MODE=relay`

Routing rules:
- In `interactive` mode, user-facing output stays in normal session output.
- In `relay` mode, all user-facing output/messages must be routed via
  `SendMessage` (no interactive prompts).
- In `relay` mode, any branch that normally asks the user to choose launch
  behavior defaults to launch behavior automatically.
</mandatory>
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
## Phase 4: Staged Splice + Compact-Size Gate

1. Resolve JSONL path for the target session:
   - If `PROJECT_SLUG` is provided:
     ```bash
     JSONL_PATH="$HOME/.claude/projects/$PROJECT_SLUG/$SESSION_ID.jsonl"
     ```
   - Otherwise:
     ```bash
     JSONL_PATH="$(ls -1 "$HOME"/.claude/projects/*/"$SESSION_ID".jsonl 2>/dev/null | head -n 1)"
     ```
   If `JSONL_PATH` is empty or missing, STOP with error.

2. Resolve config once for both gate and relaunch permissions:
   ```bash
   python3 scripts/lethe-config.py --project-dir <cwd> [--fallback-resume-permission <MODE>]
   ```
   where `<cwd>` is from the Phase 2 manifest metadata (or `INITIAL_CWD` fallback).
   Parse and store:
   - `compact_size` (default `400000`, from `LETHE_COMPACT_SIZE` / `compact_size`)
   - `resume_permission`

3. Create a working candidate file:
   ```bash
   WORKING_JSONL="${JSONL_PATH}.working"
   cp "$JSONL_PATH" "$WORKING_JSONL"
   ```

4. Run the splicer against the working copy only:
   ```bash
   python3 scripts/lethe-splice.py $SESSION_ID \
     --cut-plan /tmp/lethe/$SESSION_ID/cut-plan.json \
     --jsonl-path "$WORKING_JSONL" \
     --no-backup
   ```

5. Parse splice result and verify:
   - `ok == true`
   - `chain_verification.ok == true`
   On failure:
   - discard working copy
   - STOP (do not modify original JSONL)

6. Evaluate compact-size gate:
   - Read `new_tokens_est` from splice result.
   - Gate condition:
     ```text
     new_tokens_est <= compact_size
     ```

7. Gate pass:
   - Backup original using Lethe-specific naming:
     ```bash
     TS="$(date -u +%Y%m%d-%H%M%S)"
     RAND="$(python3 - <<'PY'
import random, string
print(''.join(random.choices(string.ascii_lowercase + string.digits, k=4)))
PY
)"
     BACKUP_PATH="${JSONL_PATH}.lethe-${TS}-${RAND}"
     cp "$JSONL_PATH" "$BACKUP_PATH"
     mv "$WORKING_JSONL" "$JSONL_PATH"
     ```
   - Continue to Phase 5.

8. Gate fail:
   - discard working copy (`rm -f "$WORKING_JSONL"`)
   - set `GATE_FAILED=true`
   - continue to Phase 4 fallback routing.
</core>

<mandatory>
Never proceed past a failed staged splice. A broken JSONL means the session
cannot be resumed safely. If staged splice fails verification:
1. Report the error details.
2. State that the original JSONL was not overwritten.
3. Clean up working directory: `rm -rf /tmp/lethe/$SESSION_ID/`
4. STOP. Do not attempt Phase 5.

If gate passes, original JSONL must only be replaced after backup succeeds.

If the splicer exits non-zero and stdout is empty, inspect stderr first.

Note: if the compactor aborts at any phase (1-4), always clean up
`/tmp/lethe/$SESSION_ID/` before stopping to avoid stale artifacts.
</mandatory>
</section>

<section id="phase-4-standard-compact-fallback">
<core>
## Phase 4 Fallback: Standard Compact

This fallback runs only when `GATE_FAILED=true`.

Mode routing:
- If `--orchestrate` is provided: run fallback compact flow.
- If `--orchestrate` is not provided and `COMM_MODE=interactive`:
  report/recommend standard compact (`claude --resume "$SESSION_ID" "compact"`) and STOP.
- If `--orchestrate` is not provided and `COMM_MODE=relay`:
  run fallback compact flow automatically (no prompt).

Fallback compact flow:
1. Capture baseline:
   ```bash
   BASELINE_LINES="$(wc -l < "$JSONL_PATH")"
   ```
2. Detect terminal and launch an external compact session:
   - Detect terminal launch template:
     ```bash
     python3 scripts/lethe-discover.py --detect-terminal $$ --cwd <cwd>
     ```
   - Build compact script:
     ```bash
     DELIM="FALLBACK_COMPACT_$(uuidgen | tr -d '-')"
     cat > /tmp/lethe/$SESSION_ID/fallback-compact.sh << "$DELIM"
     #!/bin/bash
     echo $$ > /tmp/lethe/$SESSION_ID/fallback-compact.pid
     exec env -u CLAUDECODE claude [--permission-mode <resume_permission>] --resume <session-id> "compact"
     $DELIM
     chmod +x /tmp/lethe/$SESSION_ID/fallback-compact.sh
     ```
   - Launch using terminal template (`{command}` -> script path) with:
     `nohup ... &` followed by `disown`.
3. Watch JSONL lines after baseline for:
   `{"type":"system","subtype":"compact_boundary", ...}`
4. On detection, terminate fallback compact session using captured PID.
5. Continue to Phase 5 relaunch rules.

Watcher rules:
- Parse only lines with index `> BASELINE_LINES`.
- Parse appended lines as JSON; ignore malformed lines.
- Timeout after 300 seconds if no compact boundary appears.

Retry policy:
- First fallback compact failure (timeout or step error): retry once.
- Second fallback compact failure: fail closed and STOP.
</core>

<mandatory>
In `COMM_MODE=relay`, user-facing fallback status updates must be routed via
`SendMessage`, including retry and fail-closed outcomes.
</mandatory>
</section>

<section id="phase-5-post-splice">
<mandatory>
## Phase 5: Post-Splice

This phase runs only when one of these is true:
- Staged gate passed and working JSONL was promoted, or
- Standard compact fallback completed successfully.

Use `resume_permission` resolved in Phase 4:
- if null, omit `--permission-mode`
- if non-null, include `--permission-mode <resume_permission>`

In `COMM_MODE=relay`, route all user-facing output in this phase via
`SendMessage`.
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
   echo $$ > /tmp/lethe/<session-id>/claude.pid
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
6. Wait briefly for the launched session to start and write its PID:
   ```bash
   for i in $(seq 1 5); do sleep 1; [ -f /tmp/lethe/$SESSION_ID/claude.pid ] && break; done
   LAUNCHED_PID=$(cat /tmp/lethe/$SESSION_ID/claude.pid 2>/dev/null || echo "unknown")
   ```
7. Output compaction results and launched PID:
   "Session $SESSION_ID compacted and relaunched.
   Reduction: [original_tokens_est] → [new_tokens_est] tokens ([reduction_pct]%).
   Segments: [kept] kept, [summarized] summarized, [dropped] dropped.
   Launched PID: $LAUNCHED_PID"
8. The working directory at `/tmp/lethe/$SESSION_ID/` is ephemeral
   and will be cleaned up on system reboot. Do not delete it — the relaunch
   script may still be in use.
9. Exit. The compactor's job is done.

### Section B: User Prompt (--orchestrate NOT provided)

1. Output compaction results:
   "Session $SESSION_ID compacted successfully.
   Reduction: [original_tokens_est] → [new_tokens_est] tokens ([reduction_pct]%).
   Segments: [kept] kept, [summarized] summarized, [dropped] dropped."
2. Branch by communication mode:
   - `COMM_MODE=interactive`: Ask "Launch the resumed session in a new terminal?"
     - If no: output the manual command:
       `env -u CLAUDECODE claude [--permission-mode <resume_permission>] --resume $SESSION_ID`
       Include `--permission-mode` only when `resume_permission` is non-null. Then stop.
     - If yes: continue to step 3.
   - `COMM_MODE=relay`: Do not ask. Default to launch behavior and continue to step 3.
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
   echo $$ > /tmp/lethe/<session-id>/claude.pid
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
7. Wait briefly for the launched session to start and write its PID:
   ```bash
   for i in $(seq 1 5); do sleep 1; [ -f /tmp/lethe/$SESSION_ID/claude.pid ] && break; done
   LAUNCHED_PID=$(cat /tmp/lethe/$SESSION_ID/claude.pid 2>/dev/null || echo "unknown")
   ```
8. Output: "Launched PID: $LAUNCHED_PID"
9. The working directory at `/tmp/lethe/$SESSION_ID/` will be cleaned
   up on system reboot.
</core>
</section>

</skill>
