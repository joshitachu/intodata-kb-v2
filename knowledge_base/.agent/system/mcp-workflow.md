# MCP Documentation Generation Workflow

The custom harness runs a 7-phase pipeline orchestrated by `PipelineRunner`
(`providers/custom/pipeline/runner.py`). Each phase is a `Phase` subclass in
`providers/custom/pipeline/phases/`, except `evidence_quality_gate.py` which
lives at `providers/custom/pipeline/evidence_quality_gate.py`. The harness
provider (`harness.py`) owns setup, state management, enterprise gate
evaluation, and the staging swap; phase bodies live in the individual phase
files.

## Phase Table

| # | Name | Class file | What it does | LLM calls (min/max) | Skip condition |
|---|------|-----------|--------------|---------------------|----------------|
| 1 | phase1_investigation | `phase1_investigation.py` | Runs `SingleShotInvestigator` per scanner group to extract structured evidence (facts manifest, chunk reports). Writes `_investigation/` and `facts_manifest.json`. | N .. N*3 (N = groups) | `plan.investigation_mode == "skip"` |
| 2 | phase2_analysis | `phase2_analysis.py` | Produces structured group-level analysis (`_analysis/{group}.md`). Supports three sub-modes: direct (small projects), bounded (pack policy), and DAG-tiered. Persists one agent task per group under `_agent_tasks/phase2/`. | N .. N*4 | `plan.analysis_mode == "skip"` |
| 2.6 | phase26_scorecard | `phase26_scorecard.py` | Generates the quality/risk scorecard from analysis output. Single persisted agent task. | 1 .. 2 | `plan.scorecard == False` |
| 2.8 | phase28_bundles | `phase28_bundles.py` | Deterministic reassembly of analysis sections into per-doc evidence bundles (`_analysis/_doc_pack_*.md`). No LLM calls. | 0 | `plan.doc_bundles == False` |
| 2.9 | evidence_quality_gate | `pipeline/evidence_quality_gate.py` | Pure Python thin-bundle check over `_preprocess/{group}.md`. Emits `THIN_BUNDLE` warnings and `_assessment/thin_bundle_report.json`. | 0 | never skipped |
| 3 | phase3_docs | `phase3_docs.py` | Writes one markdown document per required doc in `docs_staging/`. Persists one agent task per doc under `_agent_tasks/phase3/`. Runs `EvidenceTraceabilityValidator` post-write. | N .. N*3 (N = required docs) | never skipped |
| 4 | phase4_cleanup | `phase4_cleanup.py` | Enterprise gate evaluation, audit bundle write, engineering artifact archival, and run log. No LLM calls. | 0 | never skipped |

Inputs flow forward: Phase 1 populates `facts_manifest.json`; Phase 2 reads it
and writes `_analysis/`; Phase 2.6 reads `_analysis/`; Phase 2.8 reads
`_analysis/` and writes `_analysis/_doc_pack_*.md`; the evidence quality gate
reads `_preprocess/`; Phase 3 reads `_analysis/_doc_pack_*.md`, `_preprocess/`,
and `_analysis/`; Phase 4 reads all prior artifacts.

## Plugin Pack Integration

A `plugin.yaml` at the pack root controls per-phase behaviour via `PhasePolicy`
(`services/plugin_schema.py`):

```
phases:
  investigation: skip | run | {policy: skip, model: ..., concurrency: 4}
  analysis:      skip | run | bounded | {policy: bounded, model: ..., concurrency: 6}
  scorecard:     skip | run | {policy: run, model: ...}
  doc_bundles:   skip | run
```

`PhasePolicy.policy_for(phase)` returns the policy string.
`PhasePolicy.model_for(phase)` returns an optional model override.
`PhasePolicy.concurrency_for(phase)` returns an optional concurrency override.

Prompt overrides: place a `.md` file at `{plugin_pack_dir}/prompts/{phase_name}.md`
(e.g. `prompts/investigation.md`, `prompts/phase2_analysis.md`). The phase reads
the override at runtime and passes it as the prompt preamble.

Doc catalog override: place a `doc_catalog.yaml` at the pack root. If present,
`load_doc_catalog()` prefers it over `data/doc_catalog.yaml`.

## Key Config (env vars)

| Env var | Default | Effect |
|---------|---------|--------|
| `MCP_AGENT_PHASE1_CONCURRENCY` | 4 | Parallel groups in Phase 1 |
| `MCP_AGENT_PHASE2_CONCURRENCY` | 6 | Parallel groups in Phase 2 |
| `MCP_AGENT_PHASE3_CONCURRENCY` | 4 | Parallel docs in Phase 3 |
| `MCP_AGENT_PHASE1_AGENTIC` | false | Use `ChunkManifestInvestigator` instead of `SingleShotInvestigator` |
| `MCP_AGENT_PHASE1_BATCH_SIZE` | 20 | Units per extraction call in Phase 1 |
| `MCP_AGENT_PHASE2_TOOL_INPUT_TOKEN_CAP` | 180000 | Token cap for Phase 2 tool reads |
| `MCP_AGENT_PHASE3_TOOL_INPUT_TOKEN_CAP` | 320000 | Token cap for Phase 3 tool reads |

Model per phase is set by the plugin pack (`model_for`) or falls back to
`config.doc_generation_model`. The harness stores per-phase model overrides in
`_phase_model_overrides` at startup.

## Doc Catalog

Standard document definitions live in `data/doc_catalog.yaml`, not Python code.
`DocCatalog` (`providers/custom/doc_catalog.py`) loads and validates the YAML
via Pydantic. Plugin packs can supply a `doc_catalog.yaml` at their root to
replace the standard catalog for a specific pack.

## Prompt Templates

Prompt bodies live in `data/prompt_templates/`:

| File | Used by |
|------|---------|
| `phase2_analysis.md` | `group_analysis_prompt()` in `prompts.py` |
| `phase2_platform_sections/{platform}.md` | `_platform_section_depth()` in `prompts.py` |
| `phase3_doc.md` | `doc_prompt()` in `prompts.py` |
| `scorecard.md` | `scorecard_prompt()` in `prompts.py` |

`PromptRenderer` (`providers/custom/prompt_renderer.py`) performs `${SLOT}`
substitution. All slots must be provided by the caller; unknown slots are
ignored. Plugin pack prompt overrides are passed as the `PREAMBLE` slot.

## Evidence Traceability

`EvidenceTraceabilityValidator` (`providers/custom/evidence_traceability.py`)
runs after Phase 3 doc writes. It cross-checks that claims in each generated
document trace back to scanner evidence in the facts manifest, and writes a
traceability report to `_assessment/evidence_traceability.json`.

`validate_docs` / `validate_doc_depth` / `validate_source_provenance` produce
`_assessment/doc_validation_report.json` / `doc_depth_report.json` /
`source_provenance_report.json` after Phase 3. Failures are advisory warnings
emitted via `log_cb` (count + first 3 messages) — the docs ship as-written.
No model-call repair loop; no retry; no `RuntimeError`. The operator decides
whether to manually fix the missing section. Added 2026-06-11.

## Debug Forensics (`MCP_PIPELINE_DEBUG`)

`MCP_PIPELINE_DEBUG` (default `0`) gates the full forensic record under
`docs_staging/_debug/`. Default runs keep only the always-on breadcrumb
artifacts: per-call `_model_inputs/<phase>/<NNN>_<target>/manifest.json` and
`_agent_tasks/<phase>/<target>/{status.json,output.md}`. Setting the flag to
`1` also materializes: per-call `prompt.md` / `context_bundle.md` /
`request.json` / `response.json` snapshots, agent-task
`contract.json` / `validation_report.json` / `tool_call_ledger.json` /
`provider_metrics.json` traces, and the gated advisory reports under
`_assessment/` (phase costs, repair plans, consistency, reviewers, etc.).
The per-call `manifest.json` is the always-on breadcrumb needed to debug
"which file went into which call" without re-running with the flag.

## Dry-Run Estimate

`GET /mcp/projects/{project_id}/generate/estimate`

Calls `PipelineRunner.estimate()` which delegates to each phase's `estimate()`
method. Returns per-phase call counts (min/max) and projected cost in USD without
executing any LLM calls. The estimate uses the current execution plan and the
configured model name.
