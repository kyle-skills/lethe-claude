# Review #4 Fix Plan

**Date:** 2026-02-25
**Source:** `temp/reviews/00-consolidated-review-4.md` (18 parallel reviewers, 132 findings)
**Status:** Design decisions captured, implementation plan pending

---

## Design Decisions (Discussed and Approved)

### D1: project_slug forwarding + Phase 1 watermark removal (F-002, F-017, F-018)
**Decision:** Option 3 — Middle ground
- Forward `project_slug` from discovery output through SKILL.md to compactor for Phase 2 performance
- Remove Phase 1 watermark grep entirely (discovery already verified it)
- Simplifies Phase 1 to just PID verification and kill
**Files:** SKILL.md (steps 6, 8), compactor.md (Phase 1, Phase 2)

### D2: Tool name strings in rules.md (F-040)
**Decision:** Downgrade to nit. Do NOT add explicit tool name table.
- Tool names are self-evident (Read, Edit, Bash, etc.)
- A rigid table creates maintenance burden for yet-unknown tools
- Add brief note that tool names are literal Claude Code tool names
**Files:** rules.md (mapping table area)

### D3: Malformed JSONL line handling (F-054)
**Decision:** Option 2 — Count and warn
- If malformed lines found, emit warning with count to stderr
- Don't preserve as sentinels (over-engineering for corrupted files)
- Add comment documenting the index-shift limitation
**Files:** compact_utils.py (parse_jsonl)

### D4: `st` terminal template removal (F-073)
**Decision:** Remove `st` from templates entirely
- Niche terminal, manual fallback handles it
- The `sh -c` wrapping is fundamentally incompatible with `shlex.quote`
- Not worth the complexity of a special quoting path
**Files:** compact-discover.py (TERMINAL_TEMPLATES, TERMINAL_ALIASES)

### D5: `{command}` in cwd double-substitution (F-074)
**Decision:** Document as unsupported
- Astronomically unlikely (literal `{command}` in a directory path)
- Add one-line note in code comment
- Not worth changing sentinel across all templates
**Files:** compact-discover.py (comment near template substitution)

### D6: Thinking + text misclassification (F-058)
**Decision:** Fix as bug — classify as `conversation` when both thinking and text present
- Currently drops text response content, which is a real data loss bug
- Thinking content in the segment gets evaluated by compactor rather than auto-dropped
**Files:** compact_utils.py (classify_entry, around line 251)

### D7: Summary count verification on re-compaction (F-104)
**Decision:** Track generated summary UUIDs during build_new_jsonl
- Only count entries whose UUID matches a freshly generated one in verification
- Fixes idempotency guarantee
**Files:** compact-splice.py (build_new_jsonl, verify_new_chain)

### D8: Remove `default=str` from json.dumps (F-105)
**Decision:** Remove from both compact-splice.py and compact-analyze.py
- TypeError should surface as a visible bug, not be silently suppressed
**Files:** compact-splice.py (line 447), compact-analyze.py (lines 246, 257, 260)

### D9: Final segment identification (F-025, F-044)
**Decision:** Add explicit step in compactor Phase 3
- "The last segment that is not an Always Drop type gets KEEP regardless of its type-based rule"
- Also clarify in rules.md that "final segment" means last non-Always-Drop segment
**Files:** compactor.md (Phase 3), rules.md (final segment definition)

### D10: Standardize template substitution on .replace() (F-077)
**Decision:** Replace `.format()` with `.replace()` in discovery mode
- Safer with brace-containing strings
- Consistent with detect-terminal mode
**Files:** compact-discover.py (discovery mode template substitution)

### D11: Separate exit code for terminal-not-found (F-076)
**Decision:** Add `EXIT_TERMINAL_NOT_FOUND = 3`
- Distinguishes watermark-not-found (2) from terminal-not-found (3) for callers
**Files:** compact-discover.py (exit code constants, detect-terminal exit path)

### D12: Context header boundary clarity (F-092)
**Decision:** Code clarity fix, no behavior change
- Initialize `header_end = -1` and handle explicitly
- Add clear comments explaining priority: content boundary > minimum group > maximum cap
**Files:** compact-analyze.py (identify_context_header)

### D13: Adjacent summaries same-role messages (F-109)
**Decision:** Document as known limitation
- `turn_alternation_ok` already detects this and is intentionally excluded from pass/fail
- Fixing changes summary format for edge cases — not worth the complexity
- Add comment in verification explaining the design choice
**Files:** compact-splice.py (verify_new_chain, comment only)

### D14: thinking/progress in context header range (F-094)
**Decision:** Option 1 — Skip thinking/progress during boundary detection
- Header detection should not promote Always Drop segments to Always Keep
- `identify_context_header` should skip over thinking/progress when scanning for the boundary
**Files:** compact-analyze.py (identify_context_header)

### D15: sessionId in manifest metadata (F-093)
**Decision:** Strip sessionId from metadata output
- Redundant with top-level `session_id` field
- Update example manifest accordingly (already omits it, so example is now correct)
**Files:** compact-analyze.py (build_manifest or metadata output)

---

## Mechanical Fixes (No Discussion Needed)

These findings have clear, unambiguous fixes:

### Showstopper
| ID | Fix | Files |
|----|-----|-------|
| F-001 | Remove stale `</output>` tags from all three skill files | SKILL.md, compactor.md, rules.md |

### Critical
| ID | Fix | Files |
|----|-----|-------|
| F-123 | Add comment explaining why identify_context_header is intentionally omitted in splicer | compact-splice.py |

### Major
| ID | Fix | Files |
|----|-----|-------|
| F-003 | Simplify PID discovery to single `ps -o ppid=` + verify | SKILL.md (step 3) |
| F-004 | Add double-quote escaping guidance for resume prompt | SKILL.md (step 8), compactor.md (Phase 5A) |
| F-005 | Change manual fallback to instruct user to exit session first | SKILL.md (step 7) |
| F-019 | Add concrete argument parsing example for --orchestrate | compactor.md |
| F-020 | Add defensive cwd handling (fallback if null/empty) | compactor.md (Phase 5A) |
| F-021 | Specify manual resume command in Section A terminal-failure fallback | compactor.md (Phase 5A) |
| F-022 | Align terminology: use "Context Budget Safety Valve" or add alias | compactor.md |
| F-023 | Clarify safety valve segments skip --read-segment, write sidecars in step 5 | compactor.md (Phase 3) |
| F-024 | Add guidance to check stderr when stdout is empty on non-zero exit | compactor.md (Phase 4) |
| F-041 | Clarify error_chain evaluate guidelines reference adjacent segments | rules.md |
| F-042 | Remove "Task as tool_chain" parenthetical | rules.md |
| F-043 | Cross-reference 10% cap in context header definition | rules.md |
| F-055 | Add comment explaining compact_boundary coupling in CHAIN_SYSTEM_SUBTYPES | compact_utils.py |
| F-056 | Add warning for system entries with UUID not in CHAIN_SYSTEM_SUBTYPES | compact_utils.py |
| F-057 | Extract duplicated tool-type classification into helper function | compact_utils.py |
| F-075 | Add UnicodeDecodeError to subprocess exception tuples | compact-discover.py |
| F-078 | Raise error if --cwd provided without --detect-terminal | compact-discover.py |
| F-079 | Use exact match for aliases except gnome-terminal- (with comment) | compact-discover.py |
| F-106 | Add directory fsync after rename | compact-splice.py |
| F-107 | Use segment line_range[0] as base position for synthetic entries | compact-splice.py |
| F-108 | Add duplicate segment_id detection in load_cut_plan | compact-splice.py |
| F-109 | Document adjacent-summary same-role as known limitation (D13) | compact-splice.py |
| F-124 | Verify plugin loader works with current layout; update design doc | plugin.json, design doc |
| F-125 | Update design doc Phase 5 to match implementation | design doc |

### Minor (implement)
| ID | Fix | Files |
|----|-----|-------|
| F-006 | Replace "AskUserQuestion" with natural language instruction | SKILL.md |
| F-026 | Change Phase 1 PID check from `comm` to `args` | compactor.md |
| F-027 | Add --read-segment error handling (default to KEEP) | compactor.md |
| F-031 | Add `env -u CLAUDECODE` to Section B manual command | compactor.md |
| F-044 | Clarify "final segment" = last non-Always-Drop | rules.md |
| F-045 | Rephrase rule precedence as explicit 3-step sequence | rules.md |
| F-046 | Change "oldest 50%" to "first 50% of segments by position" | rules.md |
| F-051 | Fix thinking block mechanism description (content blocks, not XML tags) | rules.md |
| F-059 | Change `entry.get("message", {})` to `entry.get("message") or {}` | compact_utils.py |
| F-065 | Add cycle detection warning in walk_chain safety limit | compact_utils.py |
| F-080 | Document zombie process behavior in is_pid_alive | compact-discover.py |
| F-081 | Update alacritty template from `-e` to `--` | compact-discover.py |
| F-082 | Increase tail from 50 to 200 lines for metadata extraction | compact-discover.py |
| F-093 | Strip sessionId from metadata output — redundant with top-level field (D15) | compact-analyze.py |
| F-094 | Skip thinking/progress during header boundary detection (D14) | compact-analyze.py |
| F-098 | Wrap walk_chain call in try/except for ValueError | compact-analyze.py, compact-splice.py |
| F-110 | Move backup creation to after verification | compact-splice.py |
| F-111 | Log warning for chain entries without recognized types | compact-splice.py |
| F-113 | Use tempfile or random suffix for temp file name | compact-splice.py |
| F-114 | Validate summary file paths under /tmp/smart-compact/ | compact-splice.py |
| F-115 | Validate summary files are non-empty | compact-splice.py |
| F-117 | Wrap walk_chain in splice main() with try/except | compact-splice.py |
| F-130 | Add note that example segment IDs are always contiguous | example-segment-manifest.md |

### Minor (defer or document-only)
| ID | Action | Rationale |
|----|--------|-----------|
| F-007 | Add note that cwd is available but not needed | Documentation only |
| F-008 | Cross-reference 70% threshold in mandatory block | Documentation only |
| F-009 | Note that {cwd} is pre-resolved in self-compaction mode | Documentation only |
| F-010 | Note uuidgen availability verified in step 1 | Documentation only |
| F-011 | Move orphaned guidance block into appropriate section | Documentation only |
| F-012 | Shorten YAML description; move details to body | Documentation only |
| F-028 | Clarify project_slug availability wording | Addressed by D1 |
| F-029 | Note empty session fails at Phase 2 | Documentation only |
| F-030 | Add uuidgen to permissions list | Documentation only |
| F-032 | Document initial cwd usage | Documentation only |
| F-047 | Add mixed error/success note to Evaluate guidelines | Documentation only |
| F-048 | Elevate idempotency preference to mandatory | Documentation only |
| F-049 | Note MCP diffs classified as mcp_chain | Documentation only |
| F-050 | Merge duplicated tool_chain sub-type paragraphs | Documentation only |
| F-060 | Add comment noting O(S×L); acceptable for expected sizes | Documentation only |
| F-061 | Log both entries' parentUuids in duplicate UUID warning | Low risk |
| F-062 | Add post-walk sanity check for orphaned chain entries | Low risk |
| F-063 | Document token estimation limitation | Documentation only |
| F-064 | Use `is not None` for metadata truthy check | Low risk |
| F-066 | Comment documenting git_diff classification simplification | Documentation only |
| F-083 | Wrap os.getcwd() with try/except fallback | Edge case |
| F-084 | Acknowledge tmux/screen in error message | Deferred (future terminal support) |
| F-085 | Document --detect-terminal without --cwd behavior | Documentation only |
| F-095 | Add double-call guard to identify_context_header | Defensive |
| F-096 | Validate --read-segment >= 1 | Defensive |
| F-097 | Comment explaining non-chain exclusion from --read-segment | Documentation only |
| F-112 | Comment explaining system-entry reset in turn alternation | Documentation only |
| F-116 | Add top-level try/except for EXIT_UNEXPECTED | Defensive |
| F-126 | Remove default=str from compact-analyze.py too | Addressed by D8 |
| F-127 | Update design doc KNOWN_TYPES | Design doc maintenance |
| F-131 | Note segment ID is 1-based in example | Documentation only |

### Nit (implement if touching the file anyway)
| ID | Fix | Files |
|----|-----|-------|
| F-013 | Trim context/frontmatter overlap | SKILL.md |
| F-015 | Move concurrent-compaction warning to mandatory/guidance | SKILL.md |
| F-033 | Move cleanup instruction to Phase 2 | compactor.md |
| F-036 | Add negligible-reduction guidance | compactor.md |
| F-039 | Mention --read-segment output format | compactor.md |
| F-052 | Add safety valve threshold rationale | rules.md |
| F-067 | Update module docstring | compact_utils.py |
| F-068 | Consider more specific diff marker | compact_utils.py |
| F-069 | Document interaction_group_id starting value | compact_utils.py |
| F-086 | Remove redundant == alias check | compact-discover.py |
| F-087 | Simplify detect_terminal return or document | compact-discover.py |
| F-088 | Include version/gitBranch in discovery output or remove extraction | compact-discover.py |
| F-099 | Cache stripped text in build_content_preview | compact-analyze.py |
| F-100 | Remove unused is_chain_entry import | compact-analyze.py |
| F-101 | Validate --output directory existence | compact-analyze.py |
| F-118 | Use UTC for backup timestamp | compact-splice.py |
| F-119 | Validate segment_id >= 1 in load_cut_plan | compact-splice.py |
| F-121 | Verify backup size after copy | compact-splice.py |
| F-122 | Threshold check for incomplete cut-plans | compact-splice.py |
| F-128 | Update design doc verification field names | design doc |
| F-132 | Document content_preview limitation for auto-summaries | rules.md |

### Nit (skip)
| ID | Rationale |
|----|-----------|
| F-014 | Placeholder formatting convention — style preference, not a bug |
| F-016 | Step 3 / discover.py overlap is by design (different purposes) |
| F-034 | Redundant mkdir -p confirmed intentional |
| F-035 | Heredoc pattern confirmed correct |
| F-037 | cut-plan session_id is for traceability |
| F-038 | Section A/B duplication — noting sync risk is sufficient |
| F-053 | Future strict mode — not implemented |
| F-070 | find_jsonl iteration order — session IDs are globally unique |
| F-071 | TypedDict — nice-to-have, not for this pass |
| F-072 | Redundant get_content_blocks calls — micro-optimization |
| F-089 | ProcessLookupError redundancy — keep for documentation value |
| F-090 | --detect-terminal PID validation — argparse handles it |
| F-091 | EXIT_BAD_ARGS overlap — document if confusing |
| F-102 | Content preview showing tool JSON — design choice |
| F-103 | build_description fallback — acceptable |
| F-120 | Token estimation excludes non-chain — documented approximation |
| F-129 | plugin.json routing — verify loader works |

---

## All Discussions Complete

All design decisions have been discussed and approved. No remaining open questions.

---

## Implementation Approach

When creating the implementation plan from this fix plan:

1. **Group by file** — minimize context switching
2. **Scripts first, then skill docs** — code fixes are more mechanical and testable
3. **Design doc updates last** — after implementation matches
4. **Test after each script** — run existing tests or manual verification
5. **Commit per logical group** — not per individual finding

### Suggested file order:
1. compact_utils.py (D3, D6, F-055 through F-072)
2. compact-discover.py (D4, D5, D10, D11, F-075 through F-091)
3. compact-analyze.py (D8, D12, F-092 through F-103)
4. compact-splice.py (D7, D8, F-104 through F-122)
5. rules.md (D2, D9, F-040 through F-053)
6. compactor.md (D1, F-017 through F-039)
7. SKILL.md (D1, F-001 through F-016)
8. examples/ (F-130, F-131, F-093)
9. design doc (F-124, F-125, F-127, F-128)
