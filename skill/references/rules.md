<skill name="smart-compact-rules" version="1.0">

<metadata>
type: reference
parent-skill: smart-compact
tier: 3
protocol: Compaction Rules
</metadata>

<sections>
- segment-mapping-table
- always-drop
- always-keep
- aggressive-trim
- moderate-trim
- evaluate
- context-header-definition
- context-budget-safety-valve
- summary-format
- idempotency
- future-modes
</sections>

<section id="segment-mapping-table">
<core>
# Compaction Rules

## Segment Type to Rule Mapping

Positional rules (marked **Positional**) are checked first regardless of type.
Then apply type-based rules in table order.

| Segment Type | Default Rule | Notes |
|---|---|---|
| `context_header` | Always Keep | **Positional**: first segment — session identity |
| (final segment) | Always Keep | **Positional**: last assistant response before compaction |
| `boundary` | Always Keep | Existing compact boundaries from prior runs |
| `thinking` | Always Drop | Internal reasoning, never needed on resume |
| `progress` | Always Drop | Streaming markers, no content |
| `mcp_chain` | Aggressive Trim | MCP results are enormous, summarize aggressively |
| `error_chain` | Evaluate | May reveal workarounds — read before deciding |
| `tool_chain` (Read/Grep/Glob) | Aggressive Trim | Exploration results, keep only findings |
| `tool_chain` (Edit/Write) | Moderate Trim | Preserve what changed and why |
| `tool_chain` (Bash, other) | Aggressive Trim | Summarize command and exit status |
| `task_result` | Aggressive Trim | Subagent results, keep outcome only |
| `git_diff` | Aggressive Trim | Summarize files + nature of changes |
| `conversation` | Evaluate | May be critical decisions or casual chat |

To determine the tool sub-type for `tool_chain` segments, check the `tool_names`
field in the segment manifest. If the segment contains multiple tool types,
use the most conservative rule (Moderate Trim over Aggressive Trim).
</core>
</section>

<section id="always-drop">
<mandatory>
## Always Drop

- **Thinking blocks** — entries containing `<thinking>` tags. Internal reasoning
  is never needed on resume. No exceptions.
- **Progress entries** — streaming progress markers with no content value.
  These are display artifacts, not conversation.
</mandatory>
</section>

<section id="always-keep">
<mandatory>
## Always Keep

- **Context header** — the first segment(s) of the conversation as defined by
  the context-header-definition section. This is the session's identity.
- **Existing compact boundaries** — `boundary` type segments from prior
  `/compact` or smart-compact operations. Preserve the compaction history.
- **Final segment** — the last assistant response before compaction began.
  This preserves continuity on resume.
- **User preference statements** — during evaluation of "Evaluate" segments,
  if a segment is found to contain explicit user instructions about workflow,
  tools, or preferences, mark it KEEP regardless of its structural type.
  (This rule only applies during evaluation, not to segments with deterministic rules.)
</mandatory>
</section>

<section id="aggressive-trim">
<core>
## Aggressive Trim (summarize to 1-2 sentences)

Target summaries:
- **MCP tool results** → "Queried [source], found [key result]."
- **Large git diffs** → "Modified [files]: [nature of changes]."
- **File read results** → "Read [file], noted [key finding]."
- **Task/subagent results** → "[task description]: [outcome]."
- **Exploration chains** (Glob/Grep/Read sequences within a single segment) → "Explored [area], discovered [finding]."

Summary length is independent of segment size. Even a 100k-token MCP result
or a massive git diff gets 1-2 sentences. The goal is to preserve WHAT was
learned, not HOW it was found.
</core>
</section>

<section id="moderate-trim">
<core>
## Moderate Trim (summarize to 1 short paragraph)

Target summaries (3-5 sentences):
- **Edit/Write tool chains** → Preserve which files were modified, what changes
  were made, and the reason for each change.
- **Implementation blocks** → Preserve architectural decisions and file
  modifications. Drop raw file content, tool output, and iteration details.
- **Debugging sequences** → Preserve the root cause identified and the fix
  applied. Drop the exploration steps, failed attempts, and diagnostic output.
</core>
</section>

<section id="evaluate">
<core>
## Evaluate (read segment content, then decide)

For segments marked "Evaluate" in the mapping table, read the full segment
content via `compact-analyze.py --read-segment` and apply judgment:

**Error/retry chains:**
- If errors were followed by a successful workaround → SUMMARIZE, noting
  that errors preceded success and what the workaround was.
- If errors are unresolved or represent ongoing issues → KEEP.
- If errors are transient retries with no lasting impact → DROP.

**Conversation blocks:**
- Architectural decisions or design choices → KEEP
- User preferences or workflow instructions → KEEP
- Planning or strategy discussion → SUMMARIZE (capture the decisions made)
- Casual chat, greetings, meta-discussion → DROP
- Clarifying questions that led to decisions → SUMMARIZE (capture the decision,
  not the back-and-forth)
</core>

<guidance>
When evaluating, ask: "If this session resumes after compaction, would the
absence of this segment cause confusion, repeated work, or lost decisions?"
If yes → KEEP or SUMMARIZE. If no → DROP.
</guidance>
</section>

<section id="context-header-definition">
<core>
## Context Header Definition

The context header is the first N segments of the conversation, defined as:
everything from the start until the first `tool_chain` or `mcp_chain` segment
that is NOT immediately preceded by a conversation segment in the same
interaction group.

In practice, this captures the initial plan discussion, setup instructions,
and any early exploration before the first substantial work block begins.

**Minimum:** The first interaction group (at least one user-assistant exchange).
An interaction group starts at each user text message (not tool results) and
includes all subsequent entries until the next user text message.
**Maximum:** The first 10% of total segments (integer division, minimum 1).

If the session begins immediately with tool use (no plan discussion), the
context header is the first interaction group only.
</core>
</section>

<section id="context-budget-safety-valve">
<mandatory>
## Context Budget Safety Valve

If the manifest contains more than 30 segments requiring evaluation
(segments whose rule is "Evaluate" in the mapping table):

1. `conversation` segments in the **oldest 50%** of the conversation that would
   normally be "Evaluate" → auto SUMMARIZE with a generic summary: "Earlier
   discussion about [topic based on content_preview from manifest]."
2. `error_chain` segments in the **oldest 50%** → auto SUMMARIZE with:
   "Error encountered in [tool/context based on content_preview]."
3. Only evaluate segments in the **newest 50%** of the conversation.

This prevents the compactor from exhausting its own context window by reading
too many segments for evaluation.
</mandatory>
</section>

<section id="summary-format">
<mandatory>
## Summary Entry Format

When replacing segments with summaries, the splicer emits a **user-assistant
pair** to preserve turn structure and prevent consecutive same-role messages:

- **User entry**: `[smart-compact summary] <summary text>`
- **Assistant entry**: `Understood. Context from previous work has been preserved as a summary above.`

The summary pair preserves turn structure at the position of the original segment.
The splicer sets `type: "user"` on the summary entry and `type: "assistant"`
on the acknowledgment entry, matching standard conversation turn structure.
This format is non-negotiable — consecutive same-role messages confuse the
model on resume.

When writing summary sidecar files, write only the summary text itself
(without the `[smart-compact summary]` prefix — the splicer adds that).
</mandatory>
</section>

<section id="idempotency">
<core>
## Idempotency (Already-Compacted Sessions)

smart-compact can safely run on sessions that were previously compacted
by either `/compact` or smart-compact:

- Existing `boundary` segments are Always Keep (preserves compaction markers).
- Summary text following a boundary is part of the next `conversation` segment
  and is treated normally by the mapping table.
- Prior `[smart-compact summary]` entries appear as regular conversation
  segments and are evaluated normally. However, treat them conservatively —
  prefer KEEP over re-summarizing to avoid reducing already-condensed
  information to meaningless generalities.
- Segments immediately following a `boundary` segment should be treated
  conservatively (prefer KEEP) as they represent the most recent state
  after a prior compaction.
- The splicer handles chains that already contain synthetic summary entries
  without special treatment — they are just regular chain entries.
</core>
</section>

<section id="future-modes">
<context>
## Future: Mode Branching (not yet implemented)

The mapping table above represents the default mode. Future flags will shift
the table:

```
--strict:  Aggressive Trim → Always Drop, Moderate Trim → Aggressive Trim
--relaxed: Aggressive Trim → Moderate Trim, Evaluate → KEEP (skip reading)
```

The centralized mapping table design enables this branching without changing
the compactor flow — only the rules shift.
</context>
</section>

</skill>
