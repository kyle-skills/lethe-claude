<skill name="lethe-example-splice-result" version="1.0">

<metadata>
type: example
parent-skill: lethe
tier: 3
</metadata>

<core>
# Example Splice Result

This example shows the JSON result produced by `lethe-splice.py`, with a
reading guide for the nested verification structure.

## Success Case

```json
{
  "ok": true,
  "original_lines": 847,
  "new_lines": 412,
  "original_tokens_est": 185000,
  "new_tokens_est": 62000,
  "reduction_pct": 66.5,
  "segments_kept": 4,
  "segments_dropped": 3,
  "segments_summarized": 5,
  "backup_path": "/home/user/.claude/projects/-home-user-project/a1b2c3d4-....jsonl.bak-20260225-143022-x7k2",
  "chain_verification": {
    "ok": true,
    "new_chain_length": 98,
    "all_keeps_reachable": true,
    "all_summaries_present": true,
    "summarized_uuids_absent": true,
    "no_drops_reachable": true,
    "turn_alternation_ok": true
  }
}
```

## Failure Case

```json
{
  "ok": false,
  "original_lines": 847,
  "new_lines": 410,
  "original_tokens_est": 185000,
  "new_tokens_est": 61500,
  "reduction_pct": 66.8,
  "segments_kept": 4,
  "segments_dropped": 3,
  "segments_summarized": 5,
  "backup_path": "/home/user/.claude/projects/-home-user-project/a1b2c3d4-....jsonl.bak-20260225-143022-x7k2",
  "chain_verification": {
    "ok": false,
    "new_chain_length": 96,
    "all_keeps_reachable": false,
    "all_summaries_present": true,
    "summarized_uuids_absent": true,
    "no_drops_reachable": true,
    "turn_alternation_ok": true
  }
}
```

On failure, the original JSONL is NOT overwritten. The backup path is still
created. Report the verification details and stop.

## Reading Guide

### Top-level Fields
- **`ok`**: Master success flag — `true` only if `chain_verification.ok` is also `true`. Check this first.
- **`reduction_pct`**: Percentage of tokens removed. Typical range: 40-75%.
- **`backup_path`**: Timestamped backup of the original JSONL. Always present unless `--no-backup` was used.

### Chain Verification (nested object)
Both `result.ok` AND `result.chain_verification.ok` must be checked. The
top-level `ok` mirrors the verification `ok`, but the nested object provides
diagnostic detail on failure.

- **`all_keeps_reachable`**: Every UUID from kept segments is in the new chain. If false: a kept entry was lost during re-synthesis.
- **`all_summaries_present`**: Summary user entries (`[lethe summary]` prefix) match the number of summarized segments. If false: a summary pair was not emitted.
- **`summarized_uuids_absent`**: Original UUIDs from summarized segments are NOT in the new chain. If false: an original entry was kept alongside its summary.
- **`no_drops_reachable`**: UUIDs from dropped segments are NOT in the new chain. If false: a dropped entry leaked through.
- **`turn_alternation_ok`**: No consecutive same-role messages. **Informational only** — this check does not affect the `ok` flag. Pre-compacted sessions may legitimately have consecutive user entries from Claude Code's built-in `/compact`.
</core>

</skill>
