#!/usr/bin/env python3
"""
smart-compact: Re-synthesis Splicer — Cut-plan → New JSONL

Takes a cut-plan JSON (produced by the compactor's decision phase) and
re-synthesizes a new JSONL file. Kept segments preserve original UUIDs,
dropped segments are removed, and summarized segments are replaced with
user→assistant summary pairs.

Usage:
    compact-splice.py <SESSION_ID> --cut-plan <PATH_TO_PLAN_JSON>
        [--project-slug <SLUG>] [--no-backup]

Exit codes:
    0 = success
    1 = bad arguments
    2 = JSONL not found
    3 = cut-plan invalid
    4 = unknown chain entry type (format may have changed)
    5 = verification failed
    6 = unexpected error
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import string
import sys
import uuid as uuid_mod
from datetime import datetime, timezone
from pathlib import Path

from compact_utils import (
    BRIDGE_TYPES,
    build_segments,
    classify_entry,
    find_jsonl,
    get_content_blocks,
    get_text_content,
    is_chain_entry,
    parse_jsonl,
    walk_chain,
)

EXIT_SUCCESS = 0
EXIT_BAD_ARGS = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_PLAN_INVALID = 3
EXIT_CHAIN_INTEGRITY = 4
EXIT_VERIFY_FAILED = 5
EXIT_UNEXPECTED = 6

# Known entry types
KNOWN_TYPES = {
    "user", "assistant", "progress", "system",
    "file-history-snapshot", "summary", "saved_hook_context",
    "queue-operation", "pr-link",
}


def associate_non_chain(lines: list[dict], segments: list[dict]) -> None:
    """Assign non-chain entries to segments by line position."""
    seg_ranges = [(s["line_range"][0], s["line_range"][1], s["id"]) for s in segments]
    seg_by_id = {s["id"]: s for s in segments}

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


# ---------------------------------------------------------------------------
# Splice logic
# ---------------------------------------------------------------------------

def get_session_metadata(lines: list[dict]) -> dict:
    metadata = {"sessionId": None, "version": None, "cwd": None, "gitBranch": None}
    for entry in lines:
        if entry.get("sessionId") and not metadata["sessionId"]:
            metadata["sessionId"] = entry["sessionId"]
        if entry.get("version") and not metadata["version"]:
            metadata["version"] = entry["version"]
        if entry.get("cwd") and not metadata["cwd"]:
            metadata["cwd"] = entry["cwd"]
        if entry.get("gitBranch") and not metadata["gitBranch"]:
            metadata["gitBranch"] = entry["gitBranch"]
        if all(metadata.values()):
            break
    return metadata


def make_summary_pair(
    summary_text: str, parent_uuid: str | None, metadata: dict,
) -> tuple[dict, dict]:
    """Create a user→assistant summary pair.

    Returns (user_entry, assistant_entry) with proper parentUuid wiring.
    """
    user_uuid = str(uuid_mod.uuid4())
    assistant_uuid = str(uuid_mod.uuid4())
    ts = datetime.now(timezone.utc).isoformat()

    base = {
        "isSidechain": False,
        "sessionId": metadata.get("sessionId"),
        "version": metadata.get("version", "unknown"),
        "cwd": metadata.get("cwd", os.getcwd()),
        "timestamp": ts,
    }
    if metadata.get("gitBranch"):
        base["gitBranch"] = metadata["gitBranch"]

    user_entry = {
        **base,
        "type": "user",
        "message": {
            "role": "user",
            "content": f"[smart-compact summary] {summary_text}",
        },
        "uuid": user_uuid,
        # userType "external" marks this as injected content, not direct user input.
        # This does not affect Claude Code behavior — it's metadata only.
        "userType": "external",
    }
    # Only include parentUuid if we have a parent (omit for new root)
    if parent_uuid is not None:
        user_entry["parentUuid"] = parent_uuid

    assistant_entry = {
        **base,
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "Understood. Context from previous work has been preserved as a summary above.",
                }
            ],
        },
        "uuid": assistant_uuid,
        "parentUuid": user_uuid,
    }

    return user_entry, assistant_entry


def load_cut_plan(path: str) -> dict:
    """Load and validate the cut-plan JSON."""
    plan_path = Path(path)
    if not plan_path.exists():
        raise FileNotFoundError(f"Cut-plan not found: {path}")

    with open(plan_path) as f:
        plan = json.load(f)

    if "actions" not in plan:
        raise ValueError("Cut-plan missing 'actions' array")

    for action in plan["actions"]:
        if "segment_id" not in action:
            raise ValueError(f"Action missing 'segment_id': {action}")
        if action.get("action") not in ("keep", "drop", "summarize"):
            raise ValueError(
                f"Invalid action '{action.get('action')}' for segment {action['segment_id']}"
            )
        if action["action"] == "summarize" and "summary_file" not in action:
            raise ValueError(
                f"Summarize action for segment {action['segment_id']} missing 'summary_file'"
            )

    return plan


def safety_check(lines: list[dict]) -> list[str]:
    """Check for unknown entry types that participate in the chain.

    Returns list of warnings. Raises if unknown chain-participating type found.
    """
    warnings = []
    for entry in lines:
        etype = entry.get("type", "")
        if etype and etype not in KNOWN_TYPES:
            if is_chain_entry(entry):
                raise ValueError(
                    f"Unknown chain-participating entry type '{etype}' "
                    f"(uuid={entry['uuid'][:12]}...). The JSONL format may have changed. "
                    f"Aborting to prevent data corruption."
                )
            warnings.append(f"Unknown non-chain entry type '{etype}' (ignored)")
    return warnings


def build_new_jsonl(
    lines: list[dict],
    chain: list[tuple[int, dict]],
    segments: list[dict],
    action_map: dict[int, dict],
    metadata: dict,
) -> tuple[list[dict], dict]:
    """Re-synthesize the JSONL based on the cut-plan.

    Returns (new_lines, stats).
    """
    seg_by_id = {s["id"]: s for s in segments}
    uuid_to_seg = {}
    for seg in segments:
        for uid in seg["entry_uuids"]:
            uuid_to_seg[uid] = seg["id"]

    # Build the new chain
    new_chain_entries = []  # (original_line_idx | None, entry_dict)
    last_emitted_uuid = None
    kept_non_chain_lines = set()

    # Pre-build uuid→entry map for O(1) lookups during token estimation
    uuid_to_entry = {}
    for _, entry in chain:
        uid = entry.get("uuid")
        if uid:
            uuid_to_entry[uid] = entry

    stats = {
        "segments_kept": 0,
        "segments_dropped": 0,
        "segments_summarized": 0,
        "original_tokens_est": 0,
        "new_tokens_est": 0,
    }

    for seg in segments:
        action_info = action_map.get(seg["id"])
        if action_info is None:
            # Segment not in cut-plan — default to keep
            action_info = {"action": "keep"}

        action = action_info["action"]

        # Token estimation for this segment using pre-built map
        seg_tokens = sum(
            len(get_text_content(uuid_to_entry[uid])) // 4
            for uid in seg["entry_uuids"]
            if uid in uuid_to_entry
        )
        stats["original_tokens_est"] += seg_tokens

        if action == "keep":
            stats["segments_kept"] += 1
            stats["new_tokens_est"] += seg_tokens

            seg_uuids = set(seg["entry_uuids"])
            for line_idx, entry in chain:
                uid = entry.get("uuid")
                if uid not in seg_uuids:
                    continue

                new_entry = dict(entry)

                # Rewrite parentUuid if this is the first entry after a gap
                if last_emitted_uuid is not None and new_entry.get("parentUuid"):
                    # Check if parent is still reachable
                    parent = new_entry["parentUuid"]
                    parent_seg_id = uuid_to_seg.get(parent)
                    if parent_seg_id is not None:
                        parent_action = action_map.get(parent_seg_id, {}).get("action", "keep")
                        if parent_action in ("drop", "summarize"):
                            new_entry["parentUuid"] = last_emitted_uuid
                    else:
                        # Parent is not a chain entry (e.g., saved_hook_context
                        # bridge entry filtered from output). Rewrite to maintain
                        # chain continuity.
                        new_entry["parentUuid"] = last_emitted_uuid
                elif last_emitted_uuid is None and "parentUuid" in new_entry:
                    del new_entry["parentUuid"]  # New root entry

                new_chain_entries.append((line_idx, new_entry))
                last_emitted_uuid = uid

            # Keep non-chain lines from this segment
            for ncl in seg.get("non_chain_lines", []):
                kept_non_chain_lines.add(ncl)

        elif action == "drop":
            stats["segments_dropped"] += 1
            # Non-chain lines in dropped segments are discarded

        elif action == "summarize":
            stats["segments_summarized"] += 1

            # Read summary from sidecar file
            summary_file = action_info.get("summary_file", "")
            try:
                summary_text = Path(summary_file).read_text().strip()
            except (OSError, FileNotFoundError) as e:
                raise ValueError(f"Cannot read summary file {summary_file}: {e}")

            # Estimate summary tokens
            stats["new_tokens_est"] += len(summary_text) // 4 + 20  # +20 for ack

            parent = last_emitted_uuid
            if parent is None:
                # Edge case: summarizing the very first segment
                parent = None

            user_entry, assistant_entry = make_summary_pair(
                summary_text, parent, metadata,
            )

            new_chain_entries.append((None, user_entry))
            new_chain_entries.append((None, assistant_entry))
            last_emitted_uuid = assistant_entry["uuid"]
            # Non-chain lines in summarized segments are discarded

    # Collect non-chain entries to keep (from kept segments + unclaimed entries)
    non_chain_to_keep = []
    all_claimed_non_chain = set()
    for seg in segments:
        for ncl in seg.get("non_chain_lines", []):
            all_claimed_non_chain.add(ncl)

    for i, entry in enumerate(lines):
        if is_chain_entry(entry):
            continue
        # Drop bridge-type entries (e.g., saved_hook_context) — they're ephemeral
        # state snapshots with duplicate UUIDs that would have stale parentUuids
        # in the compacted JSONL. The session creates fresh ones on resume.
        if entry.get("type") in BRIDGE_TYPES:
            continue
        if i in kept_non_chain_lines:
            non_chain_to_keep.append((i, entry))
        elif i not in all_claimed_non_chain:
            # Unclaimed non-chain entries (between segments) → preserve
            non_chain_to_keep.append((i, entry))

    # Merge: chain entries + non-chain entries, sorted by original position
    # Synthetic entries (line_idx=None) are inserted at the position of the
    # segment they replace
    all_entries = []
    for line_idx, entry in new_chain_entries:
        if line_idx is not None:
            all_entries.append((line_idx, entry))
        else:
            # Synthetic entries: place them at a position that maintains order
            # Use the position just after the last real entry
            # Synthetic entries use fractional positions (e.g., 42.5) to sort
            # between real entries without shifting indices. This is safe because
            # positions are only used for ordering, never as array indices.
            if all_entries:
                pos = all_entries[-1][0] + 0.5  # fractional to sort between
            else:
                pos = -1
            all_entries.append((pos, entry))

    for line_idx, entry in non_chain_to_keep:
        all_entries.append((line_idx, entry))

    all_entries.sort(key=lambda x: x[0])

    new_lines = [entry for _, entry in all_entries]
    return new_lines, stats


def verify_new_chain(
    new_lines: list[dict],
    segments: list[dict],
    action_map: dict[int, dict],
) -> dict:
    """Verify the re-synthesized chain integrity."""
    try:
        new_chain = walk_chain(new_lines)
    except ValueError as e:
        return {
            "ok": False,
            "error": f"Chain walk failed: {e}",
            "new_chain_length": 0,
            "all_keeps_reachable": False,
            "all_summaries_present": False,
            "summarized_uuids_absent": False,
            "no_drops_reachable": False,
            "turn_alternation_ok": False,
        }

    chain_uuids = {entry.get("uuid") for _, entry in new_chain}

    # Check kept segment UUIDs are reachable
    all_keeps_reachable = True
    for seg in segments:
        action = action_map.get(seg["id"], {}).get("action", "keep")
        if action == "keep":
            for uid in seg["entry_uuids"]:
                if uid not in chain_uuids:
                    all_keeps_reachable = False
                    break

    # Check dropped segment UUIDs are NOT reachable
    no_drops_reachable = True
    for seg in segments:
        action = action_map.get(seg["id"], {}).get("action", "keep")
        if action == "drop":
            for uid in seg["entry_uuids"]:
                if uid in chain_uuids:
                    no_drops_reachable = False
                    break

    # Check summary count matches summarized segments
    expected_summaries = sum(
        1 for seg in segments
        if action_map.get(seg["id"], {}).get("action") == "summarize"
    )
    actual_summaries = 0
    for _, entry in new_chain:
        content = entry.get("message", {}).get("content", "")
        if isinstance(content, str) and content.startswith("[smart-compact summary]"):
            actual_summaries += 1
    all_summaries_present = actual_summaries == expected_summaries

    # Check original UUIDs of summarized segments are absent
    summarized_uuids_absent = True
    summarized_uuids = set()
    for seg in segments:
        if action_map.get(seg["id"], {}).get("action") == "summarize":
            summarized_uuids.update(seg["entry_uuids"])
    for uid in summarized_uuids:
        if uid in chain_uuids:
            summarized_uuids_absent = False
            break

    # Check turn alternation (no consecutive same-role messages)
    # Allow progress entries between same-role turns
    turn_alternation_ok = True
    last_role = None
    for _, entry in new_chain:
        if entry.get("type") == "progress":
            continue
        if entry.get("type") == "system":
            last_role = None
            continue
        role = entry.get("message", {}).get("role")
        if role and role == last_role:
            turn_alternation_ok = False
            break
        if role:
            last_role = role

    result = {
        "ok": False,
        "new_chain_length": len(new_chain),
        "all_keeps_reachable": all_keeps_reachable,
        "all_summaries_present": all_summaries_present,
        "summarized_uuids_absent": summarized_uuids_absent,
        "no_drops_reachable": no_drops_reachable,
        "turn_alternation_ok": turn_alternation_ok,
    }
    # turn_alternation_ok is informational only — Claude Code's built-in /compact
    # creates consecutive user entries (resume prompt + command), so pre-compacted
    # sessions legitimately fail this check.
    result["ok"] = all([
        all_keeps_reachable,
        all_summaries_present,
        summarized_uuids_absent,
        no_drops_reachable,
    ])
    return result


def write_jsonl(path: Path, lines: list[dict]):
    """Write JSONL atomically (temp file + fsync → rename)."""
    tmp_path = path.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w") as f:
        for entry in lines:
            f.write(json.dumps(entry, separators=(",", ":"), default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp_path.rename(path)


def main():
    parser = argparse.ArgumentParser(
        description="smart-compact: Re-synthesis splicer — cut-plan → new JSONL"
    )
    parser.add_argument("session_id", help="Session ID to splice")
    parser.add_argument(
        "--cut-plan", required=True,
        help="Path to cut-plan JSON file",
    )
    parser.add_argument(
        "--project-slug", default=None,
        help="Project directory slug under ~/.claude/projects/",
    )
    parser.add_argument(
        "--jsonl-path", default=None,
        help="Direct path to JSONL file (bypasses session ID lookup)",
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip creating timestamped backup",
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
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(EXIT_FILE_NOT_FOUND)

    lines = parse_jsonl(jsonl_path)

    # Safety check: unknown entry types
    try:
        warnings = safety_check(lines)
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(EXIT_CHAIN_INTEGRITY)

    # Load cut-plan
    try:
        plan = load_cut_plan(args.cut_plan)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(EXIT_PLAN_INVALID)

    # Build action map: segment_id → action info
    action_map = {}
    for action in plan["actions"]:
        action_map[action["segment_id"]] = action

    # Walk chain and build segments
    chain = walk_chain(lines)
    segments = build_segments(chain)
    associate_non_chain(lines, segments)
    metadata = get_session_metadata(lines)
    if not metadata.get("sessionId"):
        metadata["sessionId"] = args.session_id

    # Verify all segments have actions
    missing = [s["id"] for s in segments if s["id"] not in action_map]
    if missing:
        print(
            f"WARNING: Segments {missing} not in cut-plan, defaulting to keep",
            file=sys.stderr,
        )

    # Re-synthesize
    try:
        new_lines, stats = build_new_jsonl(
            lines, chain, segments, action_map, metadata,
        )
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(EXIT_PLAN_INVALID)

    # Backup
    backup_path_str = None
    if not args.no_backup:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        backup_path = jsonl_path.with_suffix(f".jsonl.bak-{ts}-{rand}")
        shutil.copy2(jsonl_path, backup_path)
        backup_path_str = str(backup_path)

    # Verify BEFORE writing
    verification = verify_new_chain(new_lines, segments, action_map)

    # Build result
    reduction_pct = 0.0
    if stats["original_tokens_est"] > 0:
        reduction_pct = round(
            (1 - stats["new_tokens_est"] / stats["original_tokens_est"]) * 100, 1
        )

    result = {
        "ok": verification["ok"],
        "original_lines": len(lines),
        "new_lines": len(new_lines),
        "original_tokens_est": stats["original_tokens_est"],
        "new_tokens_est": stats["new_tokens_est"],
        "reduction_pct": reduction_pct,
        "segments_kept": stats["segments_kept"],
        "segments_dropped": stats["segments_dropped"],
        "segments_summarized": stats["segments_summarized"],
        "backup_path": backup_path_str,
        "chain_verification": verification,
    }

    if not verification["ok"]:
        # Do NOT write — report error and backup path
        print(json.dumps(result, indent=2))
        msg = "VERIFICATION FAILED. Original preserved."
        if backup_path_str:
            msg += f" Backup at: {backup_path_str}"
        print(msg, file=sys.stderr)
        sys.exit(EXIT_VERIFY_FAILED)

    # Verification passed — write the new JSONL
    write_jsonl(jsonl_path, new_lines)

    print(json.dumps(result, indent=2))
    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
