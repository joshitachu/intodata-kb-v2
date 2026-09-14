# Git-driven document updates

The OpenCode update lane now uses separate analysis, editing and review
contracts. Each contract runs in a fresh bounded OpenCode session. Nexus owns
source collection, validation, publication and the run journal.

## Execution

1. Webhook and poller enter the same per-project OS lock. After acquiring it,
   reload the last successfully documented commit. Duplicate and older events
   are skipped. Divergent history requires an explicit repair attempt.
2. Recover interrupted, authorized publications before starting new work.
   A prepared approval draft has no activation-intent receipt and is not
   automatically published by recovery.
3. Compare exact Git commits. Read source through Git objects, not the working
   checkout. Include before/after content, rename/deletion paths, recent commit
   messages and bounded related-file evidence. Redact source evidence.
4. Use the existing router and the section evidence index to suggest documents.
   With a valid provenance baseline, unmapped new modules can use semantic
   analysis instead of being rejected solely for missing folder mappings.
5. Analyze behavior changes and intent before editing. Require exact evidence
   quotes, explicit findings, document/section targets, no-change explanations
   and unresolved questions. The analyst may request one expansion of up to 20
   exact source paths, still within the total evidence budget.
6. Validate the analysis. Unresolved questions, unavailable quotes, unaccounted
   changed paths, omitted suggested documents and oversized plans hold the run.
7. Ask the editor for precisely the planned operations. Validate file hashes,
   paths and operation coverage. Preserve sections outside the assignment and
   reject conflicts with indexed human edits or protected blocks.
8. Independently review the actual source, analysis, current docs and patch.
   Require verdicts for every finding and no reported unsupported claims,
   missed impacts or contradictions. Bind the review to exact artifact hashes.
9. Apply the patch in the private candidate. In shadow mode, retain the proposal
   without preparing or activating a RAG revision. Other stages retain their
   approval policy. Recheck active document bytes before publication.
10. Prepare changed RAG artifacts, record activation intent, activate and swap
    documents. Retain compensation/recovery state if interrupted. Update the
    evidence index and documented commit after success.

## Configuration and rollout

The existing settings still apply:

- `MCP_LIVING_DOC_PATCHING_ENABLED=true` selects this OpenCode lane.
- `MCP_OPENCODE_MODEL` must explicitly name the provider/model route. Empty
  settings hold reingestion rather than allowing an implicit provider default.
- `MCP_LIVING_DOC_ROLLOUT_STAGE=shadow` generates and checks proposals without
  changing active documents, active RAG or the documented commit.
- `approval_only`, `auto_low_risk` and `auto_scoped` retain their existing
  publication policies. Agent-produced edits still require approval in
  `auto_low_risk` under the existing deterministic-only policy.

No production environment settings are changed by this implementation. Start
with historical replays and shadow proposals before enabling automatic
publication. Shadow/held candidates are retained, not retried endlessly; use an
explicit repair attempt when intentionally rerunning a held revision.

Evidence defaults are at most 80 files and 600,000 source bytes, with at most
100,000 bytes per file version. Incomplete/binary evidence holds instead of
silently being treated as complete. The normal workflow uses three model
contracts, or four when additional source context is requested. Existing
per-contract timeouts and document operation limits still apply.

## Journal and provenance

Under `output/<project>/_version_history/`:

- `runs/<id>.json`: durable per-delivery receipts, including queued, interrupted,
  skipped, held, successful and recovered outcomes. Summaries include analysis,
  review and aggregate provider usage when those stages were reached.
- `CHANGELOG.md`: readable history derived from those receipts, including
  before/after changes, intent attribution, planned edits and actual outcome.
- `evidence_index.json`: section hashes and source paths, updated after
  publication. Existing sections acquire evidence relationships progressively.
- `drift_audit.json`: a periodic structural audit of missing evidence mappings,
  unexpected document changes and source changes since the recorded index.

The poller runs the audit at most daily for an unchanged revision. These files
are outside `docs/` and are not indexed as current system behavior. The existing
successful-update history remains available for compatibility.

Candidate workspaces retain `_reingestion/evidence.json`, `analysis.json`,
`patch_report.json`, `review.json` and the proposed evidence index. Additional
context requests are retained too. The current analyst input includes commit
messages, not PR descriptions; unknown intent must remain unknown or explicitly
inferred.

## Human text

Changes to previously indexed sections are detected before an automatic edit
overwrites them. For explicit protection, including before the first index:

```markdown
<!-- nexus:manual:start -->
This explanation is maintained by the project owner.
<!-- nexus:manual:end -->
```

An attempted change to such a block holds the update. Existing editorial-overlay
handling continues to mark revision-bound overlays stale when their canonical
document changes. Nexus cannot retrospectively distinguish all human content
from generated content in a legacy document without markers or a baseline.

## Historical evaluation

From `mcp-server`, with an explicit model and its credentials configured:

```bash
.venv/bin/python scripts/replay_document_update.py \
  --repo /path/to/repository \
  --before <full-before-commit> --after <full-after-commit> \
  --baseline-docs /path/to/docs-at-before-commit \
  --output /path/to/new-replay-directory \
  --expected /path/to/expectations.json
```

The replay uses real OpenCode but never calls publication or RAG. It requires a
new output directory and leaves the checkout and baseline docs unchanged.
It records proposal content, analysis/review, time, tokens and model cost where
reported by the provider. Expectations can check missed/unexpected documents
and required/forbidden text:

```json
{
  "required_docs": ["operations.md"],
  "allowed_docs": ["operations.md", "_rag_support/operations.md"],
  "required_text": {"operations.md": ["three attempts"]},
  "forbidden_text": {"operations.md": ["one attempt"]}
}
```

Curate cases for configuration changes, downstream consumers, additions,
renames, deletions, behavior-preserving refactors, human-edit conflicts and
incomplete evidence. Compare actual proposals, not just model verdicts.

## Limits

Exact quotes prove provenance, not that the model interpreted them correctly.
The separate review is an additional check, not a correctness guarantee.
Related-file discovery is heuristic plus one agent-requested expansion, not a
complete language-level dependency graph. The periodic audit is structural;
it does not silently claim that all existing prose is semantically correct.

The per-project lock serializes automatic Git runs on a shared filesystem.
Manual generation and other editing paths retain their existing coordination;
document hash checks detect intervening edits, but RAG and filesystem state do
not share a distributed transaction. Crash recovery retains the existing
revision checks and requires a durable activation-intent receipt.

The test suite uses scripted agent responses and in-memory RAG. Passing those
tests establishes workflow behavior; live-model quality and production rollout
must be evaluated separately using the replay command and shadow runs.
