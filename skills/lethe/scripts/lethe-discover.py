#!/usr/bin/env python3
"""
Lethe: Session Discovery + Environment Detection

Finds a Claude Code session by watermark UUID and detects the terminal
environment for orchestrated relaunch.

Modes:
    Discovery:      lethe-discover.py <WATERMARK_UUID> [--pid <PID>]
    Terminal only:  lethe-discover.py --detect-terminal <PID>

Discovery output (JSON to stdout):
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

Terminal-only output (JSON to stdout):
    {
      "terminal": "kitty",
      "terminal_launch": "kitty --directory {cwd} -- {command}"
    }

Exit codes:
    0 = success
    1 = bad arguments
    2 = watermark not found (discovery mode)
    3 = terminal not found (--detect-terminal mode)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

EXIT_SUCCESS = 0
EXIT_BAD_ARGS = 1
EXIT_WATERMARK_NOT_FOUND = 2
EXIT_TERMINAL_NOT_FOUND = 3

# Terminal binary → launch template.
# {cwd} and {command} are substituted at runtime via .replace().
# Limitation: if cwd contains the literal string "{command}", the caller's
# .replace("{command}", ...) will corrupt the path. Unsupported edge case.
TERMINAL_TEMPLATES = {
    "kitty": "kitty --directory {cwd} -- {command}",
    "gnome-terminal": "gnome-terminal --working-directory={cwd} -- {command}",
    "wezterm": "wezterm start --cwd {cwd} -- {command}",
    "alacritty": "alacritty --working-directory {cwd} -- {command}",
    "konsole": "konsole --workdir {cwd} -e {command}",
    "xterm": "xterm -e {command}",  # xterm has no working-directory flag; caller must cd
    "foot": "foot --working-directory={cwd} {command}",
    "ghostty": "ghostty -e {command}",  # ghostty uses cwd of parent process
    "urxvt": "urxvt -cd {cwd} -e {command}",
}

# Aliases: some systems report different binary names
TERMINAL_ALIASES = {
    "gnome-terminal-": "gnome-terminal",  # gnome-terminal-server
    "gnome-terminal-server": "gnome-terminal",
    "wezterm-gui": "wezterm",
    "kitty-main": "kitty",
}

UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

WATERMARK_RETRIES = 5
WATERMARK_RETRY_SLEEP = 1  # seconds


def find_watermark(watermark: str) -> Path | None:
    """Search all JSONL files under ~/.claude/projects/ for the watermark string.

    Uses grep -rl for speed — avoids parsing every JSONL file in Python.
    Returns the path to the matching JSONL file, or None.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None

    try:
        result = subprocess.run(
            ["grep", "-rlF", "--include=*.jsonl", watermark, str(claude_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Take the first match — UUIDs are globally unique
            match = result.stdout.strip().split("\n")[0]
            path = Path(match)
            if path.suffix == ".jsonl" and path.exists():
                return path
    except (subprocess.TimeoutExpired, OSError, ValueError, UnicodeDecodeError):
        pass

    return None


def find_watermark_with_retry(watermark: str) -> Path | None:
    """Retry watermark search to handle write-buffer flush delay."""
    for attempt in range(WATERMARK_RETRIES):
        path = find_watermark(watermark)
        if path is not None:
            return path
        if attempt < WATERMARK_RETRIES - 1:
            time.sleep(WATERMARK_RETRY_SLEEP)
    return None


def extract_session_metadata(jsonl_path: Path) -> dict:
    """Extract cwd from the JSONL's last few entries.

    Reads from the end since metadata fields are on most entries and
    the latest values are most relevant. Only extracts cwd — version and
    gitBranch are available via lethe-analyze.py's full metadata extraction.
    """
    metadata = {"cwd": None}

    try:
        # Read last 200 lines — sessions with many progress entries at the end
        # may need more than 50 lines to find metadata fields.
        result = subprocess.run(
            ["tail", "-200", str(jsonl_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in reversed(result.stdout.strip().split("\n")):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("cwd"):
                metadata["cwd"] = entry["cwd"]
                break
    except (subprocess.TimeoutExpired, OSError, ValueError, UnicodeDecodeError):
        pass

    return metadata


def get_parent_pid(pid: int) -> int | None:
    """Get the parent PID of a process.

    Tries /proc first (Linux), falls back to ps (macOS/other).
    """
    # Linux: /proc is fast and reliable
    proc_status = Path(f"/proc/{pid}/status")
    try:
        for line in proc_status.read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split(":")[1].strip())
    except (OSError, ValueError):
        pass

    # Fallback: ps works on macOS and most Unix systems
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError, UnicodeDecodeError):
        pass

    return None


def get_process_comm(pid: int) -> str | None:
    """Get the command name (comm) of a process."""
    # Linux: /proc/pid/comm
    proc_comm = Path(f"/proc/{pid}/comm")
    try:
        return proc_comm.read_text().strip()
    except OSError:
        pass

    # Fallback: ps (macOS may return full path like /Applications/kitty.app/.../kitty)
    try:
        result = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return os.path.basename(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, UnicodeDecodeError):
        pass

    return None


def detect_terminal(pid: int) -> tuple[str | None, str | None]:
    """Walk the process tree upward from PID to find the terminal binary.

    Returns (terminal_name, terminal_binary) or (None, None).
    """
    current_pid = pid
    visited = set()

    while current_pid and current_pid > 1 and current_pid not in visited:
        visited.add(current_pid)
        comm = get_process_comm(current_pid)
        if comm:
            # Check direct match
            if comm in TERMINAL_TEMPLATES:
                return comm, comm
            # Check aliases. Use startswith only for gnome-terminal- because
            # /proc/pid/comm truncates to 15 chars ("gnome-terminal-" from
            # "gnome-terminal-server"). All other aliases use exact match.
            for alias, canonical in TERMINAL_ALIASES.items():
                if alias == "gnome-terminal-":
                    if comm.startswith(alias):
                        return canonical, comm
                elif comm == alias:
                    return canonical, comm

        parent = get_parent_pid(current_pid)
        if parent is None or parent == current_pid:
            break
        current_pid = parent

    return None, None


def is_pid_alive(pid: int) -> bool:
    """Check if a process is still running.

    Note: returns True for zombie processes (state Z). Callers using this
    for the discovery output's pid_alive field should be aware of this.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # Process exists but owned by another user
    except (OSError, ProcessLookupError):
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Lethe: Session discovery + environment detection"
    )
    parser.add_argument(
        "watermark",
        nargs="?",
        default=None,
        help="Watermark UUID to search for in JSONL files",
    )
    parser.add_argument(
        "--pid",
        type=int,
        default=None,
        help="PID of the Claude process (for terminal detection)",
    )
    parser.add_argument(
        "--detect-terminal",
        type=int,
        default=None,
        metavar="PID",
        help="Terminal-only mode: detect terminal for the given PID and exit",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for terminal launch (quoted internally). Used with --detect-terminal.",
    )

    args = parser.parse_args()

    if args.cwd is not None and args.detect_terminal is None:
        parser.error("--cwd requires --detect-terminal")

    # --- Terminal-only mode ---
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
        sys.exit(EXIT_SUCCESS if terminal_name else EXIT_TERMINAL_NOT_FOUND)

    # --- Discovery mode (requires watermark) ---
    if args.watermark is None:
        parser.error("watermark is required (or use --detect-terminal <PID>)")

    # Validate watermark UUID format
    if not UUID_PATTERN.match(args.watermark):
        print(
            json.dumps({"error": "Invalid watermark: must be a valid UUID"}),
            file=sys.stderr,
        )
        sys.exit(EXIT_BAD_ARGS)

    # Step 1: Find the JSONL file containing the watermark
    jsonl_path = find_watermark_with_retry(args.watermark)
    if jsonl_path is None:
        print(
            json.dumps({"error": f"Watermark not found after {WATERMARK_RETRIES} retries"}),
            file=sys.stderr,
        )
        sys.exit(EXIT_WATERMARK_NOT_FOUND)

    # Step 2: Extract session_id from filename, project_slug from parent dir
    session_id = jsonl_path.stem  # filename without .jsonl
    project_slug = jsonl_path.parent.name

    # Step 3: Extract metadata from JSONL
    metadata = extract_session_metadata(jsonl_path)

    # Step 4: Build result
    result = {
        "session_id": session_id,
        "jsonl_path": str(jsonl_path),
        "project_slug": project_slug,
        "cwd": metadata["cwd"],
        "terminal": None,
        "terminal_launch": None,
        "pid": args.pid,
        "pid_alive": None,
    }

    # Step 5: Terminal detection (only if --pid provided)
    if args.pid is not None:
        result["pid_alive"] = is_pid_alive(args.pid)

        terminal_name, _ = detect_terminal(args.pid)
        if terminal_name:
            result["terminal"] = terminal_name
            try:
                fallback_cwd = os.getcwd()
            except (OSError, FileNotFoundError):
                fallback_cwd = str(Path.home())
            cwd = metadata["cwd"] or fallback_cwd
            result["terminal_launch"] = TERMINAL_TEMPLATES[terminal_name].replace(
                "{cwd}", shlex.quote(cwd)
            )

    print(json.dumps(result, indent=2))
    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
