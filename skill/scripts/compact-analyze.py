#!/usr/bin/env python3
"""
smart-compact: Structural Analysis — JSONL → Segment Manifest

Parses a Claude Code session's JSONL, walks the parentUuid chain, classifies
entries by structural type, groups them into segments, and produces a JSON
manifest for the compactor's semantic decision phase.

Usage:
    compact-analyze.py <SESSION_ID> [--project-slug <SLUG>]
    compact-analyze.py <SESSION_ID> --read-segment <SEGMENT_ID> [--project-slug <SLUG>]

Exit codes:
    0 = success
    1 = bad arguments
    2 = JSONL not found
    3 = segment not found (--read-segment mode)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from compact_utils import (
    associate_non_chain_lines,
    build_segments,
    find_jsonl,
    get_session_metadata,
    get_text_content,
    is_chain_entry,
    parse_jsonl,
    walk_chain,
)

EXIT_SUCCESS = 0
EXIT_BAD_ARGS = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_SEGMENT_NOT_FOUND = 3


def identify_context_header(segments: list[dict]) -> None:
    """Mark the context header segment(s).

    Context header: everything from start until the first tool_chain or mcp_chain
    that is NOT immediately preceded by a conversation segment in the same
    interaction group.

    Min: first interaction group (but capped by max).
    Max: first 10% of segments (at least 1).
    """
    if not segments:
        return

    max_header_segments = max(1, len(segments) // 10)
    first_group = segments[0].get("interaction_group_id")

    # Find header boundary by content rules
    header_end = 0
    for i, seg in enumerate(segments):
        if i >= max_header_segments:
            break

        if seg["type"] in ("tool_chain", "mcp_chain"):
            if i > 0 and segments[i - 1]["type"] == "conversation" \
               and segments[i - 1]["interaction_group_id"] == seg["interaction_group_id"]:
                header_end = i
                continue
            break

        header_end = i

    # Apply minimum: at least the first interaction group, capped by max
    first_group_end = 0
    for i, seg in enumerate(segments):
        if seg["interaction_group_id"] != first_group:
            break
        first_group_end = i

    # Minimum overrides content rules, but max always caps
    header_end = min(max(header_end, first_group_end), max_header_segments - 1)

    # Mark header segments (preserve original type for reference)
    for i in range(header_end + 1):
        segments[i]["original_type"] = segments[i]["type"]
        segments[i]["type"] = "context_header"


def build_content_preview(entries: list[tuple[int, dict]], max_len: int = 200) -> str:
    """Build a preview string from the first text content in entries."""
    for _, entry in entries:
        text = get_text_content(entry)
        if text.strip():
            preview = text.strip()[:max_len]
            if len(text.strip()) > max_len:
                preview += "..."
            return preview
    return ""


def build_description(seg: dict) -> str:
    """Build a human-readable description for a segment."""
    stype = seg["type"]
    tools = seg["tool_names"]

    if stype == "context_header":
        return "Session context header (plan, setup, initial discussion)"
    if stype == "conversation":
        return "User-assistant conversation"
    if stype == "thinking":
        return "Internal reasoning (thinking blocks)"
    if stype == "progress":
        return "Streaming progress markers"
    if stype == "boundary":
        return "Prior compaction boundary"
    if stype == "error_chain":
        tool_str = f" ({', '.join(tools)})" if tools else ""
        return f"Error in tool results{tool_str}"
    if stype == "git_diff":
        return "Git diff output"
    if stype == "task_result":
        return "Task/subagent result"
    if stype == "mcp_chain":
        tool_str = ", ".join(tools) if tools else "MCP tools"
        return f"MCP tool chain ({tool_str})"
    if stype == "tool_chain":
        tool_str = ", ".join(tools) if tools else "built-in tools"
        return f"Tool chain ({tool_str})"

    return stype


def build_manifest(
    session_id: str, lines: list[dict], chain: list[tuple[int, dict]],
    segments: list[dict], metadata: dict,
) -> dict:
    """Build the full manifest JSON."""
    total_tokens = sum(s["estimated_tokens"] for s in segments)

    # Type distribution
    type_dist = {}
    for seg in segments:
        t = seg["type"]
        type_dist[t] = type_dist.get(t, 0) + 1

    # Build segment output (strip internal 'entries' field)
    seg_output = []
    for seg in segments:
        seg_dict = {
            "id": seg["id"],
            "type": seg["type"],
            "description": build_description(seg),
            "interaction_group_id": seg["interaction_group_id"],
            "line_range": seg["line_range"],
            "chain_entry_count": seg["chain_entry_count"],
            "estimated_tokens": seg["estimated_tokens"],
            "content_preview": build_content_preview(seg["entries"]),
            "entry_uuids": seg["entry_uuids"],
            "tool_names": seg["tool_names"],
            "mcp_tools": seg["mcp_tools"],
            "has_errors": seg["has_errors"],
            "non_chain_lines": seg["non_chain_lines"],
        }
        if "original_type" in seg:
            seg_dict["original_type"] = seg["original_type"]
        seg_output.append(seg_dict)

    return {
        "session_id": session_id,
        "metadata": metadata,
        "total_lines": len(lines),
        "chain_length": len(chain),
        "estimated_tokens": total_tokens,
        "segment_count": len(segments),
        "type_distribution": type_dist,
        "segments": seg_output,
    }


def main():
    parser = argparse.ArgumentParser(
        description="smart-compact: Structural analysis — JSONL → segment manifest"
    )
    parser.add_argument("session_id", help="Session ID to analyze")
    parser.add_argument(
        "--project-slug", default=None,
        help="Project directory slug under ~/.claude/projects/",
    )
    parser.add_argument(
        "--jsonl-path", default=None,
        help="Direct path to JSONL file (bypasses session ID lookup)",
    )
    parser.add_argument(
        "--read-segment", type=int, default=None, metavar="SEGMENT_ID",
        help="Return raw chain entries for a specific segment by ID (non-chain entries excluded)",
    )
    parser.add_argument(
        "--output", default=None, metavar="PATH",
        help="Write manifest JSON to file instead of stdout",
    )

    args = parser.parse_args()

    # Find and parse JSONL
    try:
        if args.jsonl_path:
            jsonl_path = Path(args.jsonl_path)
            if not jsonl_path.exists():
                raise FileNotFoundError(f"JSONL not found at {jsonl_path}")
        else:
            jsonl_path = find_jsonl(args.session_id, args.project_slug)
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(EXIT_FILE_NOT_FOUND)

    lines = parse_jsonl(jsonl_path)
    chain = walk_chain(lines)
    metadata = get_session_metadata(lines)

    # Build segments
    segments = build_segments(chain)
    identify_context_header(segments)
    associate_non_chain_lines(lines, segments)

    # Mode 2: Read specific segment
    if args.read_segment is not None:
        target = None
        for seg in segments:
            if seg["id"] == args.read_segment:
                target = seg
                break

        if target is None:
            print(
                json.dumps({"error": f"Segment {args.read_segment} not found"}),
                file=sys.stderr,
            )
            sys.exit(EXIT_SEGMENT_NOT_FOUND)

        # Return raw entry objects for this segment
        output = []
        for line_idx, entry in target["entries"]:
            output.append(entry)
        print(json.dumps(output, indent=2, default=str))
        sys.exit(EXIT_SUCCESS)

    # Mode 1: Full manifest
    manifest = build_manifest(
        args.session_id, lines, chain, segments, metadata,
    )
    # default=str handles datetime objects that may appear in metadata
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump(manifest, f, indent=2, default=str)
        print(str(output_path))
    else:
        print(json.dumps(manifest, indent=2, default=str))
    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
