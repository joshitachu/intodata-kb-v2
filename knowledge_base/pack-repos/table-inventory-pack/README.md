# IntoData Table Inventory Pack

This repository is a cloneable IntoData plugin pack. The application can register it directly from a GitHub URL because `plugin.yaml` is at the repository root.

Use this pack when the documentation goal is a database table and DDL inventory for a codebase with embedded SQL, ORM usage, DDL gaps, or schema migration questions.

## Quick Start

1. Fork or clone this repository.
2. Edit `plugin.yaml` if you want to rename the pack, change output documents, or adjust quality gates.
3. Edit files in `prompts/` to tune the analysis and writing instructions.
4. Push the repository to GitHub.
5. In IntoData, open Plugin Packs, choose GitHub URL, paste the repository URL, and select Clone & Register.

The app clones the repo and validates the root `plugin.yaml`. No build step is required.

## Required Layout

```text
.
|-- plugin.yaml
|-- README.md
`-- prompts
    |-- phase2.md
    |-- phase3.md
    |-- scorecard.md
    `-- docs
        |-- TABLE_INVENTORY.md
        `-- DDL_REBUILD_SCRIPTS.md
```

## What Each File Does

`plugin.yaml` defines the pack name, version, generated documents, harness step, and deterministic quality gates.

`prompts/phase2.md` tells the harness what evidence to extract during analysis.

`prompts/phase3.md` tells the harness how to write the final markdown documents.

`prompts/scorecard.md` tells the harness how to assess the run for this pack's domain.

`prompts/docs/*.md` contains document-specific instructions. Each file name should match a document name declared in `plugin.yaml`.

## Quality Gates

This pack uses blocking quality gates:

- `required_sections`: headings that must appear in each generated document.
- `required_terms`: terms that must appear somewhere in the generated pack output.
- `required_evidence_terms`: evidence/provenance terms that must appear.
- `min_doc_bytes`: minimum generated size per document.

Blocking gates fail the run when unmet. Change `mode: blocking` to `mode: advisory` if you want failures logged without blocking generation.

## Safe Customization

Common changes:

- Rename the pack by changing `name` in `plugin.yaml`.
- Add or remove generated docs in `docs`.
- Add matching doc prompt files in `prompts/docs/`.
- Tune required sections and terms under `quality_gates`.
- Tighten or loosen `min_doc_bytes`.

Avoid changing `steps` unless you are extending the app. The default harness step is:

```yaml
steps:
  - id: full_pipeline
    type: harness
```

## Upload As ZIP

If you upload a ZIP instead of using GitHub, zip the repository contents so `plugin.yaml` is either at the zip root or inside one top-level folder.

Allowed file types are `.yaml`, `.yml`, `.md`, and `.txt`.
