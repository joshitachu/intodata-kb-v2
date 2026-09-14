# ADR-012: One Pack Format, And A Pack Cannot Weaken The Evidence Floor

## Status
Accepted 2026-08; supersedes the governed multi-format pack stack

## Context

Pack configuration had accumulated several parallel formats and activation lanes
(`pack_schema_v2.py` / `schema_version: 2` with layer + governance + capabilities, a
`data/packs/` + `MCP_DEFAULT_PACK_ID` lane, `/mcp/packs/preview`, a RAG-side DB pack
registry, and ZIP pack upload). Each was a separate way for a pack to change behaviour.

## Decision

There is exactly one pack format and it is `plugin.yaml` (`services/plugin_schema.py`). A
pack is a directory tree and lives in one of two lanes, distinguished only by
`plugin_packs.owner_user_id`: personal (owner-private, git-initialised so publishing is a
push) or shared (a branch of the starter repo, available to everyone). Extraction is
platform-owned and identical for every pack.

## Rationale

"Extraction is platform-owned and identical for every pack (a pack cannot weaken the
evidence floor). Three blocks change behaviour -- `phases` (whether each phase runs, with
which model/concurrency), `lens` (`focus` = which evidence categories are screened,
`sections` = the declared analysis contract and the question each section must answer,
`requirements` = standing prompt rules), and `docs` (the output set plus `phase2_sections`
routing)."

"`POST /mcp/plugins/{id}/publish` pushes a personal pack as `pack/{name}` and opens a pull
request -- it ASKS; a human merge is what grants everyone access."

"An unsatisfied declared section does NOT fail anything. A security pack pointed at code
with no dynamic SQL *should* come back empty for that section -- that is a correct result,
not a defect."

## Consequences

- `PackContract.hard_required_names` is the split that guarantees the floor: the whole
  required set for a no-pack run, and the `GROUP_SUMMARY` floor alone for any pack.
- Unsatisfied declared sections are recorded in
  `_assessment/pack_section_coverage.json` and the documents publish clean.
- A pack that declares a document name with no matching `prompts/docs/<NAME>.md` renders
  Phase 3 with an empty doc-specific slot, so the Pack Architect renders the prompt file
  deterministically alongside the manifest.
- A pack prompt file that no render call site reads is dead weight that reads as
  authoritative; reachability is enforced by a test.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md and knowledge_base/CLAUDE.md` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
