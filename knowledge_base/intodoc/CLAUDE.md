# intodoc -- Integration Scanner CLI

> This file is also the package readme (`pyproject.toml` `readme`) and is copied by the
> mcp-server Dockerfile; keep it. Documentation generation is not done here -- it runs in
> the MCP server's harness (see `knowledge_base/CLAUDE.md` and
> `knowledge_base/.agent/system/mcp-workflow.md`).

## What is this?
A Python CLI that scans integration codebases (Talend, Workato, Lobster, and a Generic
fallback) and produces a structured, credential-redacted scan cache (JSON) that the MCP
server serves to the documentation harness. The only command is `scan`.

## Quick Start
```bash
cd intodoc
pip install -e ".[dev]"
intodoc scan <source-dir> --platform talend --output-dir ../intodoc-output
```
Other options: `--redaction-level`, `--redaction-config`, `--no-redaction-audit`,
`--allow-redaction-failures` (see `src/intodoc/cli.py`).

## Key Architecture
- **Platform-explicit** (ADR-027): the user declares `--platform talend|workato|lobster|generic`,
  which loads that platform's parser, distiller, file rules, prompts and best practices.
- **Platform plugins**: `src/intodoc/platforms/<name>/`, registered in `platforms/registry.py`.
  Inheritance: `PlatformPlugin -> GenericPlugin -> WorkatoPlugin/LobsterPlugin`
  (`TalendPlugin` extends `PlatformPlugin` directly); `BaseDistiller -> TalendDistiller /
  WorkatoDistiller / LobsterDistiller / GenericDistiller`.
- **Prompt resolution**: `platforms/<name>/prompts/ANALYSIS_GUIDE.md` / `PHASE_3_GUIDE.md`
  override `platforms/_shared/prompts/`; the MCP server reads them
  (`mcp-server/src/mcp_server/services/scanner_service.py`).
- **Two-layer credential filter** (ADR-002): text-level `CredentialFilter` before parsing,
  structural `post_filter` after parsing, then `redaction_verifier` blocks output that still
  holds residual secrets.
- **Deterministic passes after parsing**: mapping accounting + capability ledger, lineage
  matrix (`scan/lineage.json`) and landscape rollup (`scan/landscape.json`), each gated on
  redaction verification.

## Project Layout
- `src/intodoc/cli.py` -- Click CLI entry point (`scan`)
- `src/intodoc/config.py` -- Pydantic Settings configuration
- `src/intodoc/models.py` -- shared Pydantic data models
- `src/intodoc/lineage.py`, `landscape.py` -- deterministic lineage and landscape passes
- `src/intodoc/mapping_accounting.py`, `capability_ledger.py` -- coverage ledgers
- `src/intodoc/scanner/` -- file walking (`walker.py`, `fswalk.py`), git analysis, credential
  filter, post-parse filter, redaction verifier, evidence store
- `src/intodoc/platforms/` -- `base.py`, `base_parser.py`, `base_distiller.py`, `registry.py`,
  and plugins `talend/`, `workato/`, `lobster/`, `generic/`, `_shared/prompts/`
- `reference-docs/` -- 9 reference docs (00-08) defining output format/depth for doc agents
- `tests/` -- pytest suites (`test_platforms/`, `test_scanner/`, `test_lineage/`, `test_landscape/`)

## Enterprise Artifact Fidelity (Sep 2026)

Talend scans now cover the artifact categories that used to be invisible, and
publish two deterministic ledgers so coverage is measurable rather than assumed.
Full rationale and known gaps: `.agent/decisions/003-enterprise-artifact-fidelity.md`.

New scan artifacts (all additive; historic `scan_result.json` still loads):

| Artifact | Contents |
|----------|----------|
| `scan/structures.json` | TDM structures (`Structures/**/*.xml`) as path-qualified element trees, with `parse_outcome` + `unmatched_tags` |
| `scan/xslt.json` | XSLT stylesheets (templates, value-producing instructions, variables, includes) plus `referenced_by` component bindings |
| `scan/services.json` | ESB service designs (`services/*.item` + `*.wsdl`) as portType/operation names |
| `scan/mapping_accounting.json` | Per mapper component: offered / written / mapped / constant / lookup / dynamic field counts, lookups, filters, rejects, and one row per written field (tMap, tXMLMap, TDM, XSLT) |
| `scan/capability_ledger.json` | Every construct the scan saw with a capability status, plus `file_outcomes` that sums to the inventory row count |
| `scan/sanitized/` | Redacted mirror of every textual source file, with a manifest of what was written and what was refused (categories only, never values) |

Rules that matter when working on this area:

- **Identity beats the label cache.** `tRunJob` `PROCESS` and `cTalendJob`
  `SELECTED_JOB_NAME` are Studio label caches that go stale on rename;
  `*:PROCESS_TYPE_PROCESS` (the repository GUID) is authoritative. Disagreement is
  a `stale_label` warning, and `reference_source` records which one was used.
  `USE_DYNAMIC_JOB` means `resolution="dynamic"` with no target.
- **Absence is not a value.** `ACTIVATE` is omitted on an active component, so
  `Component.active` derives from absence. Call flags are `bool | None` so "not
  stated" stays distinguishable from "false".
- **A dynamic destination is not a channel identity.** `context.*` / `globalMap`
  destinations collapse to the non-identity placeholder so no shared dependency is
  fabricated between routes that resolve differently per environment.
- **Publishing raw text needs the structural redaction rule at text level.**
  Layer 1's key/value pattern is XML-attribute shaped with a fixed key list; the
  structural layer redacts by token-aware variable NAME. The mirror applies
  `CredentialFilter.redact_named_value_pairs` and then REFUSES any file that still
  trips `detect_residual`. Never widen the mirror's extension set without checking
  that the filter can prove that format clean.
- **A gap is a row, not a silence.** Unsupported constructs, unresolved
  references and unenumerable schemas are ledger rows with a status. Never drop
  one to keep an output tidy.

## Known Pitfalls
The single list is in `knowledge_base/CLAUDE.md` "Known Pitfalls".

## Rules
- Read `.agent/README.md` before planning scanner work; update `.agent/` docs when
  architecture/models/config change.
- Run the credential filter BEFORE any LLM call -- never send raw credentials to an API.
- No workarounds, generalize fixes, use design patterns: same as root `CLAUDE.md` rules 9-11.
