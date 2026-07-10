# Talend to Azure Migration Swap Pack

An IntoData Swap Pack that analyzes a Talend project and generates a
deployable Talend-to-Azure migration bundle.

Point the platform at a Talend project (job `.item` files, contexts,
metadata connections, routines) and the pack produces five documents:

| Document | Content |
|---|---|
| `01_TALEND_ANALYSIS` | Job, component, context, and connection inventory with migration classification per job |
| `02_AZURE_TARGET_ARCHITECTURE` | Target Azure design, Talend-to-Azure component mapping, design decisions, risks |
| `03_ADF_PIPELINES` | Ready-to-deploy Azure Data Factory JSON: linked services, datasets, pipelines, data flows, triggers |
| `04_INFRA_DEPLOYMENT` | Bicep templates, per-environment parameter files, and an idempotent `deploy.sh` |
| `05_CUTOVER_RUNBOOK` | Wave-based migration sequence, validation SQL, parallel-run guidance, cutover and rollback steps |

## Design principles

- **Drop-in output.** Artifacts in `03_ADF_PIPELINES` and `04_INFRA_DEPLOYMENT`
  are emitted as complete files (bold filename line + fenced code block).
  Save them under the stated paths, run `deploy.sh`, set the listed Key
  Vault secrets, and publish - no editing of artifact bodies.
- **No secrets in artifacts.** Credentials become Key Vault references named
  `kv-<connection>-<field>`; Talend context variables become ADF/Bicep
  parameters with the same names for traceability.
- **Safe by default.** Triggers deploy in `Stopped` state; nothing runs
  until the cutover runbook says to start it.
- **Evidence-grounded.** Untranslatable logic (custom Java, missing
  evidence) becomes a clearly marked `TODO_<component>` placeholder inside
  the pipeline plus an Evidence Notes entry - never silently dropped or
  invented.

## Runtime Files

- `plugin.yaml` defines the pack name, version, generation phases, document
  set, quality gates, and runner steps.
- `prompts/investigation.md` targets Talend artifacts: jobs, tMap logic,
  contexts, connections, routines, orchestration.
- `prompts/phase2.md` extracts per-job migration records and classifies
  each job as COPY, TRANSFORM, ORCHESTRATION, or CUSTOM.
- `prompts/scorecard.md` scores migration complexity, evidence
  completeness, and automation confidence per job.
- `prompts/phase3.md` enforces the deployable-artifact and naming rules
  across all documents.
- `prompts/docs/*.md` gives per-document instructions.
- `examples/*.md` gives style and structure examples.

## Authoring Files

- `AGENTS.md` tells AI coding agents how to work in this pack.
- `.codex/skills/tune-swap-pack/SKILL.md` gives Codex a local tuning workflow.
- `.claude/commands/tune-pack.md` is a legacy Claude Code workflow kept for compatibility with older authoring habits.

The platform treats these authoring files as text. It does not execute local
skills, commands, or scripts from an uploaded pack.

## Customize

1. Edit `plugin.yaml` when the document set, phase behavior, or quality
   gates should change.
2. Edit `prompts/phase2.md` when analysis is missing Talend facts you need.
3. Edit `prompts/phase3.md` when artifact conventions (naming, Key Vault
   patterns) should change across all docs.
4. Edit `prompts/docs/*.md` when one document needs specific sections or
   focus (for example, targeting Synapse or Fabric instead of ADF).
5. Update `version` in `plugin.yaml` before uploading a new release.

## Upload

Zip this directory with `plugin.yaml` at the zip root, then upload it in the
dashboard under Swap Packs. You can also push the pack to GitHub and register
the repository URL from the dashboard.
