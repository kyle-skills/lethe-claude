<skill name="lethe-example-cut-plan" version="1.0">

<metadata>
type: example
parent-skill: lethe
tier: 3
</metadata>

<core>
# Example Cut-Plan with Sidecar Files

This example shows a realistic cut-plan for a 12-segment session, demonstrating
all three action types: KEEP, DROP, and SUMMARIZE.

## Cut-Plan JSON

Written to `/tmp/lethe/a1b2c3d4-5678-9abc-def0-123456789abc/cut-plan.json`:

```json
{
  "session_id": "a1b2c3d4-5678-9abc-def0-123456789abc",
  "actions": [
    {"segment_id": 1, "action": "keep"},
    {"segment_id": 2, "action": "drop"},
    {"segment_id": 3, "action": "summarize", "summary_file": "/tmp/lethe/a1b2c3d4-5678-9abc-def0-123456789abc/summary-3.txt"},
    {"segment_id": 4, "action": "drop"},
    {"segment_id": 5, "action": "summarize", "summary_file": "/tmp/lethe/a1b2c3d4-5678-9abc-def0-123456789abc/summary-5.txt"},
    {"segment_id": 6, "action": "keep"},
    {"segment_id": 7, "action": "drop"},
    {"segment_id": 8, "action": "summarize", "summary_file": "/tmp/lethe/a1b2c3d4-5678-9abc-def0-123456789abc/summary-8.txt"},
    {"segment_id": 9, "action": "summarize", "summary_file": "/tmp/lethe/a1b2c3d4-5678-9abc-def0-123456789abc/summary-9.txt"},
    {"segment_id": 10, "action": "drop"},
    {"segment_id": 11, "action": "keep"},
    {"segment_id": 12, "action": "keep"}
  ]
}
```

### Segment Decisions Explained

| Seg | Type | Rule | Action | Rationale |
|-----|------|------|--------|-----------|
| 1 | context_header | Always Keep | KEEP | Session identity and initial plan |
| 2 | thinking | Always Drop | DROP | Internal reasoning blocks |
| 3 | tool_chain (Read, Grep) | Aggressive Trim | SUMMARIZE | File exploration results |
| 4 | progress | Always Drop | DROP | Streaming markers |
| 5 | tool_chain (Edit, Write) | Moderate Trim | SUMMARIZE | Implementation changes |
| 6 | conversation | Evaluate → KEEP | KEEP | Architectural decision discussion |
| 7 | thinking | Always Drop | DROP | Internal reasoning blocks |
| 8 | mcp_chain | Aggressive Trim | SUMMARIZE | Large MCP tool results |
| 9 | git_diff | Aggressive Trim | SUMMARIZE | Diff output |
| 10 | progress | Always Drop | DROP | Streaming markers |
| 11 | error_chain | Evaluate → KEEP | KEEP | Unresolved errors |
| 12 | conversation | Always Keep (final) | KEEP | Final segment — positional rule |

## Sidecar Summary Files

### `/tmp/lethe/.../summary-3.txt` (Aggressive Trim)

```
Explored backend/src/ for authentication middleware. Found JWT validation in auth.ts and session management in session-store.ts.
```

### `/tmp/lethe/.../summary-5.txt` (Moderate Trim)

```
Implemented rate limiting middleware in backend/src/middleware/rate-limiter.ts. Added per-endpoint configuration with defaults of 100 req/min for API routes and 20 req/min for auth routes. Updated server.ts to register the middleware before route handlers. Added rate limit headers (X-RateLimit-Remaining, X-RateLimit-Reset) to all responses.
```

### `/tmp/lethe/.../summary-8.txt` (Aggressive Trim)

```
Queried local-rag for authentication patterns. Found existing JWT implementation uses RS256 with 15-minute expiry.
```

### `/tmp/lethe/.../summary-9.txt` (Aggressive Trim)

```
Modified rate-limiter.ts and server.ts: added sliding window algorithm, per-IP tracking, and configurable whitelist.
```

## Key Points

- Summary text does NOT include the `[lethe summary]` prefix — the splicer adds that
- All sidecar files use the session-scoped path under `/tmp/lethe/<session_id>/`
- Aggressive Trim summaries are 1-2 sentences regardless of original segment size
- Moderate Trim summaries are 3-5 sentences preserving what changed and why
- Every segment in the manifest has a corresponding action — none are skipped
</core>

</skill>
