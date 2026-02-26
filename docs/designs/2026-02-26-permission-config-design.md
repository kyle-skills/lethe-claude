# Lethe — Permission Configuration Design

**Date:** 2026-02-26
**Status:** Approved
**Scope:** Configurable permission modes for compactor and resumed sessions
**Dependencies:** Python 3.10+ (match/case)

---

## Overview

Lethe currently hardcodes `--permission-mode acceptEdits` for the compactor
session launch and uses no permission flag for resumed sessions. This design
adds two configurable permission flags, resolved from environment variables
and `.lethe_config` files with cascading priority.

The design is strict about security escalation: `bypassPermissions` is only
applied when explicitly and validly configured. Invalid values warn and fall
back to defaults — a misconfigured session gets permission prompts, never
silent escalation.

---

## Config Keys

| Key | Env Var | `.lethe_config` Key | Valid Values | Default |
|---|---|---|---|---|
| Compactor permission | `LETHE_COMPACTOR_PERMISSION` | `compactor_permission` | `acceptEdits`, `bypassPermissions` | `acceptEdits` |
| Resume permission | `LETHE_RESUME_PERMISSION` | `resume_permission` | `acceptEdits`, `bypassPermissions` | `null` (no flag) |

- **Compactor permission** controls `--permission-mode` on the compactor
  session launched during self-compaction (SKILL.md step 8).
- **Resume permission** controls `--permission-mode` on the resumed session
  launched during Phase 5 of the compactor protocol (both Section A and
  Section B).
- When resume permission is null (default), no `--permission-mode` flag is
  emitted — Claude Code uses its own default behavior.

---

## Resolution Order

Each key is resolved independently through a cascading priority chain:

1. **Environment variable** (`LETHE_COMPACTOR_PERMISSION`, `LETHE_RESUME_PERMISSION`)
2. **Project-level `.lethe_config`** (in the project root directory)
3. **User-level `.lethe_config`** (in `$HOME`)
4. **Hardcoded default** (`acceptEdits` for compactor, `null` for resume)

Stop loading lower-priority files once both keys are resolved.

Environment variables always take precedence over file-based config. A user
can set `LETHE_RESUME_PERMISSION` globally in `$HOME/.lethe_config` and
override `LETHE_COMPACTOR_PERMISSION` per-project in the project root's
`.lethe_config`.

---

## `.lethe_config` Format

Flat key=value, one per line. Comments start with `#`. Empty lines ignored.

```
# Project-level Lethe configuration
compactor_permission=bypassPermissions
resume_permission=acceptEdits
```

Keys use the same names as the env vars without the `LETHE_` prefix.
Whitespace around `=` is not supported (matches standard dotfile conventions).
Unknown keys are silently ignored (forward-compatible with future config).

---

## Validation

Each value is validated with Python 3.10+ `match`/`case`:

```python
match value:
    case "acceptEdits" | "bypassPermissions":
        return value
    case _:
        warn(f"Invalid value '{value}' for {key}, using default")
        return None  # caller applies default
```

The validation is an explicit allowlist. Invalid values (typos, invented modes,
empty strings) fall back to the default for that key. A warning is printed to
stderr so the user knows their config was ignored.

**Security invariant:** An invalid `compactor_permission` value results in
`acceptEdits` (the safe default). An invalid `resume_permission` value results
in no `--permission-mode` flag (Claude Code's own default). Invalid input
never escalates permissions.

---

## Implementation: `lethe_utils.py`

Three additions to `lethe_utils.py`:

### `resolve_config(project_dir: str | None = None) -> dict`

Main entry point. Returns:
```python
{"compactor_permission": "acceptEdits", "resume_permission": None}
```

Algorithm:
1. Initialize result with `None` for both keys.
2. Read env vars. For each key, if env var is set and non-empty, validate
   and store.
3. If both keys resolved, return early.
4. If `project_dir` is provided and `<project_dir>/.lethe_config` exists,
   parse it. For each unresolved key found, validate and store.
5. If both keys resolved, return early.
6. If `$HOME/.lethe_config` exists, parse it. For each unresolved key
   found, validate and store.
7. Apply defaults for any still-unresolved keys.
8. Return result.

### `_parse_lethe_config(path: Path) -> dict`

Parses a single `.lethe_config` file. Returns a dict of raw string values
(unvalidated). Skips comment lines, empty lines, and lines without `=`.

### `_validate_permission(key: str, value: str) -> str | None`

Validates a permission value using `match`/`case`. Returns the value if valid,
`None` if invalid (with a warning to stderr).

---

## Implementation: `lethe-config.py`

Thin CLI wrapper around `resolve_config()`:

```
Usage: lethe-config.py [--project-dir PATH]
```

Outputs resolved config as JSON to stdout:
```json
{"compactor_permission": "acceptEdits", "resume_permission": null}
```

Exit code: always `0` — config resolution never fails. Invalid values produce
stderr warnings but the output is always valid JSON with safe defaults.

---

## Skill File Changes

### SKILL.md — Self-Compaction (step 8)

Insert a config resolution step before building the launch script:

```bash
python3 scripts/lethe-config.py --project-dir <cwd>
```

Parse the JSON output. Use `compactor_permission` in the launch script's
`--permission-mode` argument:

```bash
exec env -u CLAUDECODE claude --permission-mode <compactor_permission> \
  "/lethe <session_id> ..."
```

The manual fallback command (terminal undetectable, step 7) remains unchanged —
no permission mode on manual commands since the user is driving interactively.

### compactor.md — Phase 5 (both sections)

At the start of Phase 5, before branching into Section A or B, resolve config:

```bash
python3 scripts/lethe-config.py --project-dir <cwd>
```

Parse `resume_permission` from the output.

**Section A (orchestrated relaunch):** If `resume_permission` is non-null,
include `--permission-mode <resume_permission>` in the relaunch script's
`claude --resume` command. If null, omit the flag entirely.

**Section B (user prompt relaunch):** Same conditional pattern.

**Manual fallback commands** (terminal undetectable cases): Also conditionally
include the flag so the printed command matches the user's config.

---

## Project Directory Discovery

The `--project-dir` argument receives the project root `cwd`:

- **SKILL.md:** `cwd` is returned by `lethe-discover.py` from JSONL metadata.
- **Compactor.md:** `cwd` is resolved from manifest metadata, with
  `INITIAL_CWD` as fallback.

If `cwd` is unavailable in either context, omit `--project-dir`. The
resolution skips the project-level file and continues to `$HOME` → defaults.

---

## Files Changed

| File | Change |
|---|---|
| `skill/scripts/lethe_utils.py` | Add `resolve_config`, `_parse_lethe_config`, `_validate_permission` |
| `skill/scripts/lethe-config.py` | New file — CLI wrapper |
| `skill/SKILL.md` | Add config resolution step before launch script, use `compactor_permission` |
| `skill/references/compactor.md` | Add config resolution at Phase 5 start, conditional `--permission-mode` in relaunch/resume scripts and manual fallback commands |
| `README.md` | Update Permissions and Planned Features sections |
| `plugin.json` | Version bump (1.0.0 → 1.1.0) |

---

## Not In Scope

- Other planned config keys (`LETHE_MODE`, `LETHE_DRY_RUN`, etc.) — future
  work that will extend the same config infrastructure.
- Changes to the compactor's own Bash commands (kill, python3, mkdir) — those
  still require either user confirmation or allow rules regardless of
  permission mode.
- `.lethe_config` schema validation or migration tooling.
