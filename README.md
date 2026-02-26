# Lethe: Surgical Context Compaction for Claude Code

In Greek mythology, souls in the Underworld were required to drink from the River Lethe to completely erase the memories of their past lives before reincarnation. Claude Code's native `/compact` command takes this exact approach — a deep, destructive drink that clobbers the timeline and wipes the slate entirely clean.

The `lethe` skill is a measured *sip* of forgetfulness. Instead of a blunt memory wipe, `lethe` surgically washes away the chaotic middle of a session. It selectively dissolves the intermediate tool calls, raw outputs, and conversational bloat, while perfectly preserving your overarching architectural plans, core instructions, and current state.

## Lethe vs `/compact`

| | `/compact` | `lethe` |
|---|---|---|
| **Method** | Summarizes the entire conversation into a single block | Classifies every segment, then makes per-segment KEEP / SUMMARIZE / DROP decisions |
| **Preserves** | A best-effort summary | Context header, architectural decisions, user preferences, final working state, prior compaction boundaries |
| **Removes** | Everything | Thinking blocks, progress markers, exploration output, large tool results — the stuff you don't need on resume |
| **Summaries** | One monolithic summary | Targeted 1-2 sentence summaries injected at the position of the original segment |
| **Idempotent** | Each run re-summarizes the prior summary | Safe to run repeatedly — prior summaries are preserved, not re-summarized |
| **Typical reduction** | ~90%+ (aggressive) | 40-75% (surgical) |

## Installation

Clone into your Claude Code skills directory:

```bash
git clone https://github.com/kyle-skills/lethe-claude.git ~/.claude/skills/lethe
```

No dependencies beyond Python 3.10+ (standard library only).

## Usage

### Interactive: compact the current session

```
/lethe
```

That's it. Lethe discovers its own session, launches a compactor in a new terminal, gracefully terminates the current session, compacts the JSONL, and relaunches with a resume prompt. The whole process is automatic.

### Manual: compact a stopped session

```
/lethe <session-id>
```

Use this to compact a session that's already exited. Lethe analyzes, decides, splices, then reports results. No session is killed or relaunched.

### Proactive compaction

When installed, Lethe can also trigger proactively during long sessions:

- If a plan or task instructions mention Lethe as available, it compacts autonomously when context fills up.
- Otherwise, it asks permission when context usage exceeds 70%.
- If declined, it won't ask again until 85%.

## How It Works

Lethe splits the work between Python (fast structural analysis) and Claude (semantic judgment):

```
┌─────────────────────────────────────────────────────────┐
│                    Python Scripts                        │
│  Parse JSONL → Walk parentUuid chain → Classify entries  │
│  → Group into segments → Produce manifest                │
└──────────────────────┬──────────────────────────────────┘
                       │ manifest.json
                       ▼
┌─────────────────────────────────────────────────────────┐
│                    Claude (Compactor)                     │
│  Read manifest → Apply rules table → Read ambiguous      │
│  segments → Write summaries → Produce cut-plan           │
└──────────────────────┬──────────────────────────────────┘
                       │ cut-plan.json + summary files
                       ▼
┌─────────────────────────────────────────────────────────┐
│                    Python (Splicer)                       │
│  Load cut-plan → Re-synthesize JSONL → Rewrite chain     │
│  → Verify integrity → Atomic write with backup           │
└─────────────────────────────────────────────────────────┘
```

### The five phases

1. **Kill** — Gracefully terminates the target session (SIGTERM → grace period → SIGKILL only if needed). Skipped for manual compaction.
2. **Analyze** — `lethe-analyze.py` parses the JSONL, walks the `parentUuid` chain, classifies every entry, groups them into typed segments, and outputs a manifest.
3. **Decide** — Claude reads the manifest, applies the rules table segment-by-segment, reads ambiguous segments for evaluation, writes summaries for segments marked SUMMARIZE, and produces a cut-plan.
4. **Splice** — `lethe-splice.py` re-synthesizes the JSONL from the cut-plan. Kept entries preserve their original UUIDs. Summaries are injected as user-assistant pairs at the original position. The chain is rewired and verified.
5. **Post-splice** — Reports results and either relaunches the session in a new terminal (orchestrated mode) or shows the manual resume command.

## What Gets Preserved, What Gets Removed

Every segment in the conversation is classified and assigned a rule:

| What | Rule | Result |
|---|---|---|
| Session header (initial plan, setup) | Always Keep | Untouched |
| Final working state | Always Keep | Untouched |
| Prior compaction boundaries | Always Keep | Untouched |
| User preferences and instructions | Always Keep | Untouched |
| Thinking blocks | Always Drop | Removed entirely |
| Progress/streaming markers | Always Drop | Removed entirely |
| File reads, greps, globs | Aggressive Trim | 1-2 sentence summary |
| MCP tool results | Aggressive Trim | 1-2 sentence summary |
| Git diffs | Aggressive Trim | 1-2 sentence summary |
| Subagent/Task results | Aggressive Trim | 1-2 sentence summary |
| Edit/Write operations | Moderate Trim | Short paragraph (what changed + why) |
| Conversations | Evaluate | Claude reads and decides: keep decisions, summarize planning, drop chat |
| Error chains | Evaluate | Claude reads and decides: keep unresolved, summarize resolved, drop transient |

Summaries are intentionally terse. A 100k-token MCP result becomes 1-2 sentences. The goal is to preserve *what was learned*, not *how it was found*.

## Terminal Support

Lethe auto-detects your terminal emulator by walking the process tree. Supported terminals:

| Terminal | Working directory | Notes |
|---|---|---|
| kitty | `--directory` | |
| wezterm | `--cwd` | Also detects `wezterm-gui` |
| alacritty | `--working-directory` | |
| gnome-terminal | `--working-directory` | Also detects `gnome-terminal-server` |
| konsole | `--workdir` | |
| foot | `--working-directory` | |
| urxvt | `-cd` | |
| xterm | *(none)* | No working directory flag; uses caller's cwd |
| ghostty | *(none)* | Uses parent process cwd |

If your terminal isn't detected, Lethe falls back to printing a manual `claude --resume` command.

## Safety

- **Atomic writes** — New JSONL is written to a temp file, fsynced, then renamed over the original. No partial writes.
- **Timestamped backups** — The original JSONL is copied to a `.bak-YYYYMMDD-HHMMSS-xxxx` file before overwrite.
- **Chain verification** — After splicing, Lethe walks the new chain and verifies: all kept UUIDs are reachable, all summaries are present, no dropped entries leaked through, and turn alternation is intact. If verification fails, the original is not overwritten.
- **Unknown type safety** — If the JSONL contains entry types that Lethe doesn't recognize, it aborts rather than risk data corruption.
- **Graceful kill** — SIGTERM first, 10-second grace period for buffer flush, SIGKILL only as last resort.
- **Idempotent** — Safe to run on sessions already compacted by `/compact` or by Lethe itself. Prior summaries are preserved, not re-summarized.

## Project Structure

```
lethe/
├── plugin.json                          # Plugin manifest
├── skill/
│   ├── SKILL.md                         # Main skill (routing, self-compaction, guardrails)
│   ├── references/
│   │   ├── compactor.md                 # 5-phase compactor protocol
│   │   └── rules.md                     # Segment classification rules + mapping table
│   ├── examples/
│   │   ├── example-segment-manifest.md  # Annotated manifest with field guide
│   │   ├── example-cut-plan-with-sidecars.md  # Cut-plan + summary file examples
│   │   └── example-splice-result.md     # Splice result with verification guide
│   └── scripts/
│       ├── lethe_utils.py               # Shared: JSONL parsing, chain walking, classification
│       ├── lethe-analyze.py             # Structural analysis → segment manifest
│       ├── lethe-discover.py            # Session discovery + terminal detection
│       └── lethe-splice.py              # Cut-plan → re-synthesized JSONL
└── docs/                                # Design documents and review history
```

## Requirements

- **Claude Code** with plugin/skill support
- **Python 3.10+** (standard library only — no pip dependencies)
- **Linux** (process tree walking uses `/proc`; `ps` fallback for other platforms)

## License

MIT
