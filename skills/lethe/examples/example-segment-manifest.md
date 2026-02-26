<skill name="lethe-example-manifest" version="1.1">

<metadata>
type: example
parent-skill: lethe
tier: 3
</metadata>

<core>
# Example Segment Manifest

This example shows the JSON manifest produced by `lethe-analyze.py` for a
realistic session, with a reading guide for field interpretation.

## Manifest JSON (abbreviated)

Note: real manifests have contiguous segment IDs (`1..N`). This abbreviated
example intentionally shows only a subset of segments for readability.

```json
{
  "session_id": "a1b2c3d4-5678-9abc-def0-123456789abc",
  "metadata": {
    "cwd": "/home/user/project",
    "version": "2.2.1",
    "gitBranch": "feature/rate-limiting"
  },
  "total_lines": 847,
  "chain_length": 312,
  "estimated_tokens": 185000,
  "segment_count": 12,
  "type_distribution": {
    "context_header": 1,
    "thinking": 2,
    "tool_chain": 3,
    "progress": 2,
    "conversation": 2,
    "mcp_chain": 1,
    "git_diff": 1
  },
  "segments": [
    {
      "id": 1,
      "type": "context_header",
      "original_type": "conversation",
      "description": "Session context header (plan, setup, initial discussion)",
      "interaction_group_id": 1,
      "line_range": [0, 24],
      "chain_entry_count": 8,
      "estimated_tokens": 4200,
      "content_preview": "I need to add rate limiting to the backend API. The current setup has no request throttling...",
      "entry_uuids": ["uuid-001", "uuid-002", "uuid-003", "uuid-004", "uuid-005", "uuid-006", "uuid-007", "uuid-008"],
      "tool_names": [],
      "mcp_tools": false,
      "has_errors": false,
      "non_chain_lines": [3, 7, 15]
    },
    {
      "id": 3,
      "type": "tool_chain",
      "description": "Tool chain (Read, Grep, Glob)",
      "interaction_group_id": 2,
      "line_range": [30, 89],
      "chain_entry_count": 24,
      "estimated_tokens": 35000,
      "content_preview": "Let me explore the existing middleware structure...",
      "entry_uuids": ["uuid-020", "..."],
      "tool_names": ["Read", "Grep", "Glob"],
      "mcp_tools": false,
      "has_errors": false,
      "non_chain_lines": []
    },
    {
      "id": 5,
      "type": "tool_chain",
      "description": "Tool chain (Edit, Write)",
      "interaction_group_id": 3,
      "line_range": [95, 180],
      "chain_entry_count": 32,
      "estimated_tokens": 48000,
      "content_preview": "I'll create the rate limiting middleware...",
      "entry_uuids": ["uuid-050", "..."],
      "tool_names": ["Edit", "Write", "Read"],
      "mcp_tools": false,
      "has_errors": false,
      "non_chain_lines": [100, 145]
    },
    {
      "id": 8,
      "type": "mcp_chain",
      "description": "MCP tool chain (mcp__local-rag__query_documents)",
      "interaction_group_id": 5,
      "line_range": [220, 260],
      "chain_entry_count": 6,
      "estimated_tokens": 28000,
      "content_preview": "Let me check the knowledge base for authentication patterns...",
      "entry_uuids": ["uuid-100", "..."],
      "tool_names": ["mcp__local-rag__query_documents"],
      "mcp_tools": true,
      "has_errors": false,
      "non_chain_lines": []
    }
  ]
}
```

## Field Reading Guide

### Top-level Fields
- **`chain_length`** vs **`total_lines`**: Chain entries are linked by parentUuid; non-chain entries (progress markers, system metadata) are associated with segments by position
- **`estimated_tokens`**: Character count / 4 — rough approximation, not exact tokenization
- **`type_distribution`**: Quick overview of session shape — many `tool_chain` segments suggest heavy implementation work

### Per-Segment Fields
- **`id`**: 1-based segment identifier used by cut-plans (`segment_id` must start at 1)
- **`type`**: Structural classification. Look up in the rules.md mapping table to determine the default compaction rule
- **`original_type`**: Only present on `context_header` segments — shows what the type would have been without header promotion
- **`interaction_group_id`**: Groups entries between user text messages. Segments with the same ID belong to the same user-assistant exchange. Useful for understanding conversation flow
- **`line_range`**: 0-indexed range `[start, end]` into the parsed JSONL entries array
- **`tool_names`**: For `tool_chain` segments, determines which sub-rule applies (Read/Grep/Glob → Aggressive, Edit/Write → Moderate). Use the most conservative rule when multiple tool types are present
- **`mcp_tools`**: Quick flag — if true, this segment contains MCP server calls
- **`has_errors`**: If true, at least one tool_result in the segment has `is_error: true`
- **`content_preview`**: First 200 characters of text content — enough to understand the segment's topic without reading full content
- **`non_chain_lines`**: Indices of non-chain entries (progress markers, metadata) that fall within this segment's line range. These are kept or dropped along with the segment
- **`chain_entry_count`**: Number of chain-participating entries in the segment

### Using the Manifest for Decisions

1. Check `type_distribution` — if many segments need evaluation, the safety valve may apply
2. For each segment, look up its `type` in the rules table
3. For `tool_chain` segments, check `tool_names` to determine the sub-rule
4. For "Evaluate" segments, use `content_preview` for initial assessment before reading full content via `--read-segment`
5. `has_errors` on an `error_chain` segment suggests reading it to check resolution status
</core>

</skill>
