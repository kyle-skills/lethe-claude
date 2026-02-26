"""
Lethe: Shared utilities for JSONL session analysis.

Provides JSONL parsing, parentUuid chain walking, entry classification,
segment building, non-chain line association, token estimation, and session
metadata extraction. Used by lethe-analyze.py and lethe-splice.py as
the single source of truth for core data structures and classification logic.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Known chain-participating types (from JSONL spec)
CHAIN_TYPES = {"user", "assistant", "progress"}
# Types that have uuid/parentUuid but are NOT chain participants — they're
# state snapshots that reuse a small number of UUIDs across many entries.
# walk_chain "bridges" through these transparently.
BRIDGE_TYPES = {"saved_hook_context"}
# compact_boundary MUST remain in this set — is_chain_entry uses it to include
# system entries in the chain. classify_entry handles compact_boundary specially
# (returns "boundary"), but removing it from here would break chain walking.
CHAIN_SYSTEM_SUBTYPES = {
    "compact_boundary", "microcompact_boundary",
    "stop_hook_summary", "turn_duration", "local_command",
    "api_error",
}

# Diff markers for git_diff detection.
# Uses "@@ -" instead of "@@ " for specificity — plain "@@ " could appear in text.
DIFF_MARKERS = ("diff --git", "@@ -", "--- a/", "+++ b/")


def find_jsonl(session_id: str, project_slug: str | None = None) -> Path:
    """Find the JSONL file for a session ID."""
    claude_dir = Path.home() / ".claude" / "projects"

    if not claude_dir.exists():
        raise FileNotFoundError(f"Claude projects directory not found: {claude_dir}")

    if project_slug:
        path = claude_dir / project_slug / f"{session_id}.jsonl"
        if path.exists():
            return path
        raise FileNotFoundError(f"JSONL not found at {path}")

    for project_dir in claude_dir.iterdir():
        if not project_dir.is_dir():
            continue
        path = project_dir / f"{session_id}.jsonl"
        if path.exists():
            return path

    raise FileNotFoundError(
        f"No JSONL found for session {session_id} in {claude_dir}"
    )


def parse_jsonl(path: Path) -> list[dict]:
    """Parse JSONL file into list of dicts.

    Note: malformed lines are skipped, so list indices may not correspond
    to original file line numbers. This means bridge resolution using
    positional lookups and manifest line_range values are offset by the
    number of skipped lines.
    """
    lines = []
    malformed_count = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    malformed_count += 1
                    continue
    if malformed_count:
        print(f"Warning: skipped {malformed_count} malformed JSONL line(s) in {path.name}", file=sys.stderr)
    return lines


def resolve_summary_file_path(
    summary_file: str,
    session_id: str,
    base_dir: Path | None = None,
) -> Path:
    """Resolve and validate a summary sidecar path.

    The resolved file must exist and be located under:
        /tmp/lethe/<session_id>/

    The check is performed on canonical paths, which prevents traversal via
    ".." and symlink escapes.
    """
    if not isinstance(summary_file, str) or not summary_file.strip():
        raise ValueError("Summary file path must be a non-empty string")

    candidate = Path(summary_file)
    if not candidate.is_absolute():
        raise ValueError(f"Summary file {summary_file} must be an absolute path")

    root = Path("/tmp/lethe") if base_dir is None else Path(base_dir)
    session_dir = (root / session_id).resolve()

    try:
        resolved = candidate.resolve(strict=True)
    except OSError as e:
        raise ValueError(f"Cannot read summary file {summary_file}: {e}") from e

    if session_dir not in resolved.parents:
        raise ValueError(
            f"Summary file {summary_file} must be under {session_dir}/"
        )
    if not resolved.is_file():
        raise ValueError(f"Summary file {summary_file} is not a regular file")

    return resolved


def is_chain_entry(entry: dict) -> bool:
    """Check if an entry participates in the parentUuid chain."""
    if "uuid" not in entry:
        return False
    etype = entry.get("type", "")
    if etype in CHAIN_TYPES:
        return True
    if etype == "system":
        if entry.get("subtype") in CHAIN_SYSTEM_SUBTYPES:
            return True
        # Warn about unknown system subtypes that have UUIDs — they may need
        # to be added to CHAIN_SYSTEM_SUBTYPES to avoid chain truncation.
        if entry.get("parentUuid") is not None:
            subtype = entry.get("subtype", "<none>")
            if subtype not in ("summary",):  # known non-chain system subtypes
                print(f"Warning: system entry with UUID has unrecognized subtype '{subtype}' — may need chain inclusion", file=sys.stderr)
    return False


def walk_chain(lines: list[dict]) -> list[tuple[int, dict]]:
    """Walk the parentUuid chain from root to leaf (chronological order).

    Returns list of (line_index, entry_dict).

    Non-chain entries with UUIDs (e.g., saved_hook_context) are transparently
    bridged — their parentUuid is followed without adding them to the chain.
    These entries reuse a small set of UUIDs across many occurrences, so
    position-aware lookup is used to find the correct version.
    """
    uuid_to_idx = {}
    for i, entry in enumerate(lines):
        u = entry.get("uuid")
        if u and is_chain_entry(entry):
            if u in uuid_to_idx:
                prev_idx = uuid_to_idx[u]
                prev_parent = lines[prev_idx].get("parentUuid", "none")[:12] if lines[prev_idx].get("parentUuid") else "none"
                curr_parent = entry.get("parentUuid", "none")[:12] if entry.get("parentUuid") else "none"
                print(f"Warning: duplicate UUID {u[:12]}... at lines {prev_idx+1} (parent={prev_parent}...) and {i+1} (parent={curr_parent}...), keeping later", file=sys.stderr)
            uuid_to_idx[u] = i

    # Build bridge map for non-chain entries with UUIDs (e.g., saved_hook_context).
    # These have parentUuid but reuse UUIDs across many entries (state snapshots).
    # Maps uuid -> sorted list of (line_idx, parentUuid).
    bridge_entries = {}
    for i, entry in enumerate(lines):
        u = entry.get("uuid")
        if u and not is_chain_entry(entry) and entry.get("type") in BRIDGE_TYPES:
            parent = entry.get("parentUuid")
            if parent is not None:
                bridge_entries.setdefault(u, []).append((i, parent))

    # Find leaf (prefer non-sidechain; fallback to sidechain-only sessions)
    leaf_uuid = None
    used_sidechain_leaf = False
    for entry in reversed(lines):
        u = entry.get("uuid")
        if not u or not is_chain_entry(entry):
            continue
        if not entry.get("isSidechain"):
            leaf_uuid = u
            used_sidechain_leaf = False
            break
        if leaf_uuid is None:
            leaf_uuid = u
            used_sidechain_leaf = True

    if not leaf_uuid:
        raise ValueError("No chain entries found in JSONL")
    if used_sidechain_leaf:
        print(
            "Warning: no non-sidechain leaf found; using sidechain chain head",
            file=sys.stderr,
        )

    # Walk backwards from leaf, bridging through non-chain entries
    chain = []
    current = leaf_uuid
    referrer_pos = len(lines)  # position of entry that referenced current as parent

    max_steps = len(lines) + 1  # +1 lets a valid root parent=None terminate cleanly
    for _ in range(max_steps):  # safety limit prevents infinite loops
        if current is None:
            break

        if current in uuid_to_idx:
            idx = uuid_to_idx[current]
            entry = lines[idx]
            chain.append((idx, entry))
            referrer_pos = idx
            current = entry.get("parentUuid")
        elif current in bridge_entries:
            # Bridge through non-chain entry — find version closest to referrer
            bridges = bridge_entries[current]
            bridge_parent = None
            for b_idx, b_parent in reversed(bridges):
                if b_idx < referrer_pos:
                    bridge_parent = b_parent
                    referrer_pos = b_idx
                    break
            if bridge_parent is None:
                print(f"Warning: chain truncated — could not bridge through {current[:12]}...", file=sys.stderr)
                break
            current = bridge_parent
        else:
            print(f"Warning: chain truncated — parentUuid {current[:12]}... not found in chain entries", file=sys.stderr)
            break
    else:
        # Safety limit exhausted — likely a cycle in parentUuid references
        print(f"Warning: chain walk hit safety limit ({max_steps} iterations) — possible cycle in parentUuid chain", file=sys.stderr)

    chain.reverse()

    # Sanity check: warn if many chain entries were not reached (possible fork)
    total_chain_entries = sum(1 for e in lines if is_chain_entry(e) and not e.get("isSidechain"))
    if len(chain) < total_chain_entries * 0.5 and total_chain_entries > 10:
        print(f"Warning: chain walk found {len(chain)} of {total_chain_entries} non-sidechain chain entries — possible orphaned fork", file=sys.stderr)

    return chain


def get_content_blocks(entry: dict) -> list[dict]:
    """Extract content blocks from an entry's message."""
    msg = entry.get("message") or {}
    content = msg.get("content", [])
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def get_text_content(entry: dict) -> str:
    """Extract all text content from an entry for token estimation."""
    msg = entry.get("message") or {}
    content = msg.get("content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "thinking":
                    parts.append(block.get("thinking", ""))
                elif block.get("type") == "tool_use":
                    parts.append(json.dumps(block.get("input", {})))
                elif block.get("type") == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        parts.append(result_content)
                    elif isinstance(result_content, list):
                        for sub in result_content:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                parts.append(sub.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)

    return ""


def classify_entry(entry: dict, preceding_tool_type: str | None) -> str:
    """Classify a single chain entry by structural type.

    Returns one of: conversation, thinking, tool_chain, mcp_chain,
    task_result, error_chain, git_diff, boundary, progress
    """
    etype = entry.get("type", "")

    # System entries
    if etype == "system":
        subtype = entry.get("subtype")
        if subtype == "compact_boundary":
            return "boundary"
        # Other chain-participating system subtypes are metadata (droppable).
        # microcompact_boundary: internal optimization markers with no summary
        # content — classified as progress (Always Drop). See rules.md.
        if subtype in CHAIN_SYSTEM_SUBTYPES:
            return "progress"

    # Progress entries
    if etype == "progress":
        return "progress"

    blocks = get_content_blocks(entry)
    block_types = {b.get("type") for b in blocks}

    # Assistant entries
    if etype == "assistant":
        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        if tool_uses:
            return _classify_tool_chain(tool_uses)

        # No tool_use — check for thinking
        if "thinking" in block_types:
            # If both thinking and text content are present, classify as
            # conversation to preserve the text response (data loss bug if
            # classified as thinking → Always Drop).
            has_text = any(
                b.get("type") == "text" and b.get("text", "").strip()
                for b in blocks
            )
            if has_text:
                return "conversation"
            return "thinking"

        return "conversation"

    # User entries
    if etype == "user":
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        if tool_results:
            # Check for errors
            for tr in tool_results:
                if tr.get("is_error"):
                    return "error_chain"

            # Check for git diff content. Simplification: classifies the
            # entire entry as git_diff if any tool_result contains diff markers,
            # even if other tool_results in the same entry do not.
            text = get_text_content(entry)
            if any(marker in text for marker in DIFF_MARKERS):
                return "git_diff"

            # Inherit type from preceding tool_use
            if preceding_tool_type:
                return preceding_tool_type

            return "tool_chain"

        # Plain user text
        return "conversation"

    return "conversation"


def _classify_tool_chain(tool_uses: list[dict]) -> str:
    """Classify tool_use blocks into chain type (mcp_chain > task_result > tool_chain)."""
    tool_types = set()
    for tu in tool_uses:
        name = tu.get("name", "")
        if name.startswith("mcp__"):
            tool_types.add("mcp_chain")
        elif name == "Task":
            tool_types.add("task_result")
        else:
            tool_types.add("tool_chain")
    # Priority: mcp_chain > task_result > tool_chain (most specific wins)
    if "mcp_chain" in tool_types:
        return "mcp_chain"
    if "task_result" in tool_types:
        return "task_result"
    return "tool_chain"


def extract_tool_names(entry: dict) -> list[str]:
    """Extract tool names from tool_use blocks in an entry."""
    names = []
    for block in get_content_blocks(entry):
        if block.get("type") == "tool_use":
            names.append(block.get("name", "unknown"))
    return names


def build_segments(chain: list[tuple[int, dict]]) -> list[dict]:
    """Segment the chain by structural type changes.

    Each segment contains consecutive entries of the same type.
    Uses 0-indexed line ranges (matching Python list indices).
    Tracks all metadata needed by both analyzer and splicer.
    """
    if not chain:
        return []

    segments = []
    current_segment = None
    # Group 0 exists only if the chain starts with non-user entries (system,
    # assistant). First user text message increments to group 1.
    interaction_group_id = 0
    preceding_tool_type = None

    for line_idx, entry in chain:
        etype = entry.get("type", "")

        # Increment interaction group on new user text messages (not tool results)
        if etype == "user":
            blocks = get_content_blocks(entry)
            has_tool_result = any(b.get("type") == "tool_result" for b in blocks)
            if not has_tool_result:
                interaction_group_id += 1

        # Classify this entry
        seg_type = classify_entry(entry, preceding_tool_type)

        # Track preceding tool type for tool_result inheritance
        if etype == "assistant":
            tool_uses = [b for b in get_content_blocks(entry) if b.get("type") == "tool_use"]
            if tool_uses:
                preceding_tool_type = _classify_tool_chain(tool_uses)
        elif etype == "user":
            blocks = get_content_blocks(entry)
            has_tool_result = any(b.get("type") == "tool_result" for b in blocks)
            if not has_tool_result:
                preceding_tool_type = None

        # Start new segment or extend current
        if current_segment is None or current_segment["type"] != seg_type:
            if current_segment is not None:
                segments.append(current_segment)

            current_segment = {
                "id": len(segments) + 1,
                "type": seg_type,
                "interaction_group_id": interaction_group_id,
                "line_range": [line_idx, line_idx],  # 0-indexed
                "entries": [],
                "entry_uuids": [],
                "tool_names": [],
                "mcp_tools": False,
                "has_errors": False,
                "estimated_tokens": 0,
                "chain_entry_count": 0,
                "non_chain_lines": [],
            }

        # Update segment
        current_segment["line_range"][1] = line_idx
        current_segment["entries"].append((line_idx, entry))
        current_segment["chain_entry_count"] += 1

        uid = entry.get("uuid")
        if uid:
            current_segment["entry_uuids"].append(uid)

        # Tool names
        names = extract_tool_names(entry)
        for n in names:
            if n not in current_segment["tool_names"]:
                current_segment["tool_names"].append(n)
            if n.startswith("mcp__"):
                current_segment["mcp_tools"] = True

        # Error detection
        for block in get_content_blocks(entry):
            if block.get("type") == "tool_result" and block.get("is_error"):
                current_segment["has_errors"] = True

        # Token estimation: chars / 4 approximation. Counts message content
        # only — excludes entry metadata overhead (~50 tokens per entry).
        text = get_text_content(entry)
        current_segment["estimated_tokens"] += len(text) // 4

    if current_segment is not None:
        segments.append(current_segment)

    return segments


def associate_non_chain_lines(
    lines: list[dict], segments: list[dict]
) -> None:
    """Assign non-chain entries to segments by line position.

    Non-chain entries within a segment's line range are assigned to that segment.
    Entries between segments are assigned to the preceding segment.
    Entries before the first segment remain unassigned.

    Note: O(S×L) where S=segments, L=non-chain lines. Acceptable for expected
    session sizes (typically <1000 segments, <10000 lines).
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
        if entry.get("sessionId") is not None and metadata["sessionId"] is None:
            metadata["sessionId"] = entry["sessionId"]
        if entry.get("cwd") is not None and metadata["cwd"] is None:
            metadata["cwd"] = entry["cwd"]
        if entry.get("version") is not None and metadata["version"] is None:
            metadata["version"] = entry["version"]
        if entry.get("gitBranch") is not None and metadata["gitBranch"] is None:
            metadata["gitBranch"] = entry["gitBranch"]
        if all(v is not None for v in metadata.values()):
            break
    return metadata


# Config keys and their corresponding env vars
_CONFIG_KEYS = {
    "compactor_permission": "LETHE_COMPACTOR_PERMISSION",
    "resume_permission": "LETHE_RESUME_PERMISSION",
}

# Defaults applied when resolution completes without finding a value
_CONFIG_DEFAULTS = {
    "compactor_permission": "acceptEdits",
    "resume_permission": None,
}


def resolve_config(project_dir: str | None = None, caller_overrides: dict | None = None) -> dict:
    """Resolve Lethe permission configuration.

    Resolution order (per key, first match wins):
    1. Environment variable (LETHE_COMPACTOR_PERMISSION, LETHE_RESUME_PERMISSION)
    2. Project-level .lethe_config (in project_dir)
    3. User-level .lethe_config (in $HOME)
    4. Caller-provided fallbacks (lowest priority, below all user config)
    5. Hardcoded default (acceptEdits for compactor, None for resume)

    Caller overrides are intended for orchestration systems (e.g., Souffleur)
    that pass a suggested permission mode. They never override user config.

    Returns: {"compactor_permission": str, "resume_permission": str | None}
    """
    result: dict[str, str | None] = {k: None for k in _CONFIG_KEYS}
    resolved: set[str] = set()

    # 1. Environment variables
    for key, env_var in _CONFIG_KEYS.items():
        raw = os.environ.get(env_var, "").strip()
        if raw:
            validated = _validate_permission(key, raw)
            if validated is not None:
                result[key] = validated
                resolved.add(key)

    if len(resolved) == len(_CONFIG_KEYS):
        return result

    # 2. Project-level .lethe_config
    if project_dir:
        project_config = Path(project_dir) / ".lethe_config"
        parsed = _parse_lethe_config(project_config)
        for key in _CONFIG_KEYS:
            if key not in resolved and key in parsed:
                validated = _validate_permission(key, parsed[key])
                if validated is not None:
                    result[key] = validated
                    resolved.add(key)

    if len(resolved) == len(_CONFIG_KEYS):
        return result

    # 3. User-level .lethe_config
    home_config = Path.home() / ".lethe_config"
    parsed = _parse_lethe_config(home_config)
    for key in _CONFIG_KEYS:
        if key not in resolved and key in parsed:
            validated = _validate_permission(key, parsed[key])
            if validated is not None:
                result[key] = validated
                resolved.add(key)

    # 4. Caller-provided fallbacks (below all user config)
    if caller_overrides:
        for key in _CONFIG_KEYS:
            if key not in resolved and key in caller_overrides:
                raw = caller_overrides[key]
                if raw:
                    validated = _validate_permission(key, raw)
                    if validated is not None:
                        result[key] = validated
                        resolved.add(key)

    # 5. Apply defaults for any still-unresolved keys
    for key in _CONFIG_KEYS:
        if key not in resolved:
            result[key] = _CONFIG_DEFAULTS[key]

    return result


def _parse_lethe_config(path: Path) -> dict:
    """Parse a .lethe_config file into a dict of raw string values.

    Format: flat key=value, one per line. Comments (#) and empty lines skipped.
    Lines without '=' are skipped. No whitespace trimming around '='.
    Unknown keys are included (forward-compatible).
    Returns empty dict if file doesn't exist or can't be read.
    """
    result = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key] = value
    except OSError:
        pass
    return result


def _validate_permission(key: str, value: str) -> str | None:
    """Validate a permission mode value.

    Returns the value if valid, None if invalid (with a warning to stderr).
    Uses an explicit allowlist — invalid input never escalates permissions.
    """
    match value:
        case "acceptEdits" | "bypassPermissions":
            return value
        case _:
            print(f"Warning: invalid value '{value}' for {key}, using default", file=sys.stderr)
            return None
