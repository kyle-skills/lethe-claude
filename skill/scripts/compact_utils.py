"""
smart-compact: Shared utilities for JSONL parsing, chain walking, and entry classification.

Used by compact-analyze.py and compact-splice.py. Single source of truth for
core data structures and classification logic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Known chain-participating types (from JSONL spec)
CHAIN_TYPES = {"user", "assistant", "progress"}
# Types that have uuid/parentUuid but are NOT chain participants — they're
# state snapshots that reuse a small number of UUIDs across many entries.
# walk_chain "bridges" through these transparently.
BRIDGE_TYPES = {"saved_hook_context"}
CHAIN_SYSTEM_SUBTYPES = {
    "compact_boundary", "microcompact_boundary",
    "stop_hook_summary", "turn_duration", "local_command",
    "api_error",
}

# Diff markers for git_diff detection
DIFF_MARKERS = ("diff --git", "@@ ", "--- a/", "+++ b/")


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
    to original file line numbers.
    """
    lines = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Warning: Skipping malformed JSON at line {i+1}: {e}", file=sys.stderr)
                    continue
    return lines


def is_chain_entry(entry: dict) -> bool:
    """Check if an entry participates in the parentUuid chain."""
    if "uuid" not in entry:
        return False
    etype = entry.get("type", "")
    if etype in CHAIN_TYPES:
        return True
    if etype == "system" and entry.get("subtype") in CHAIN_SYSTEM_SUBTYPES:
        return True
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
                print(f"Warning: duplicate UUID {u[:12]}... at lines {uuid_to_idx[u]+1} and {i+1}, keeping later", file=sys.stderr)
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

    # Find leaf (last non-sidechain chain entry by line position)
    leaf_uuid = None
    for entry in reversed(lines):
        u = entry.get("uuid")
        if u and is_chain_entry(entry) and not entry.get("isSidechain"):
            leaf_uuid = u
            break

    if not leaf_uuid:
        raise ValueError("No chain entries found in JSONL")

    # Walk backwards from leaf, bridging through non-chain entries
    chain = []
    current = leaf_uuid
    referrer_pos = len(lines)  # position of entry that referenced current as parent

    for _ in range(len(lines)):  # safety limit prevents infinite loops
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

    chain.reverse()
    return chain


def get_content_blocks(entry: dict) -> list[dict]:
    """Extract content blocks from an entry's message."""
    msg = entry.get("message", {})
    content = msg.get("content", [])
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def get_text_content(entry: dict) -> str:
    """Extract all text content from an entry for token estimation."""
    msg = entry.get("message", {})
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
            # Collect all tool types, return the most conservative
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

        # No tool_use — check for thinking
        if "thinking" in block_types:
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

            # Check for git diff content
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
        # Use most-conservative-wins logic matching classify_entry
        if etype == "assistant":
            tool_uses = [b for b in get_content_blocks(entry) if b.get("type") == "tool_use"]
            if tool_uses:
                tool_types = set()
                for tu in tool_uses:
                    name = tu.get("name", "")
                    if name.startswith("mcp__"):
                        tool_types.add("mcp_chain")
                    elif name == "Task":
                        tool_types.add("task_result")
                    else:
                        tool_types.add("tool_chain")
                if "mcp_chain" in tool_types:
                    preceding_tool_type = "mcp_chain"
                elif "task_result" in tool_types:
                    preceding_tool_type = "task_result"
                else:
                    preceding_tool_type = "tool_chain"
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

        # Token estimation
        text = get_text_content(entry)
        current_segment["estimated_tokens"] += len(text) // 4

    if current_segment is not None:
        segments.append(current_segment)

    return segments
