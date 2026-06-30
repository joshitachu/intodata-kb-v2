# Plugin Pack Format

An IntoData plugin pack is a folder with `plugin.yaml` at its root.

## Minimal Manifest

```yaml
name: my-pack
version: "1.0.0"
description: >
  Short description shown in the Plugin Packs UI.

docs:
  - name: MY_DOCUMENT
    sections:
      - Required Section

quality_gates:
  mode: blocking
  required_sections:
    MY_DOCUMENT:
      - Required Section
  required_terms:
    - source
  min_doc_bytes:
    MY_DOCUMENT: 500

steps:
  - id: full_pipeline
    type: harness
```

## Manifest Fields

`name`: Unique pack name. The server uses this to register the pack.

`version`: Display version.

`description`: Short UI description.

`docs`: List of final markdown documents the harness should generate. Each `name` creates `<name>.md`.

`docs[].sections`: Sections expected by the pack prompts. These are also useful as editor guidance.

`docs[].evidence_profile`: Optional evidence profile name used by the document.

`evidence_profiles`: Optional named evidence category sets. Use this to say what kinds of facts a pack needs, such as `sql_queries`, `ddl_schema`, `orm_entities`, `db_connections`, or `config_references`.

`evidence_agents`: Optional evidence agent declarations. Each agent writes normalized claims to `_evidence_registry/`.

`evidence_agents[].uses`: Import a built-in harness evidence agent, for example `builtin/sql_usage_agent`.

`evidence_agents[].prompt_file`: Use a pack-owned prompt file instead of a built-in. The path is relative to the pack root, for example `prompts/agents/ddl_alignment.md`.

`evidence_agents[].categories`: Evidence categories produced by the agent.

`evidence_agents[].inputs`: Named input sets the agent expects, such as `facts_manifest`, `preprocess_chunks`, `source_context`, `engineering_facts`, or `evidence_registry`.

`quality_gates`: Optional deterministic grading rules for pack output.

`quality_gates.mode`: `blocking` or `advisory`. Defaults to `blocking` when quality gates are present.

`quality_gates.required_sections`: Per-document headings that must appear in generated markdown.

`quality_gates.required_terms`: Terms that must appear somewhere in all generated docs.

`quality_gates.required_evidence_terms`: Evidence/provenance terms that must appear somewhere in all generated docs.

`quality_gates.min_doc_bytes`: Per-document minimum generated size.

`steps`: Execution steps. For harness packs, keep a single `type: harness` step.

## Prompt Files

The harness looks for these optional files:

```text
prompts/phase2.md
prompts/scorecard.md
prompts/phase3.md
prompts/docs/<DOC_NAME>.md
```

When a file is missing, the harness falls back to its default behavior. For predictable pack output, keep all files present.

## GitHub Registration

The repository root must be the pack root:

```text
github-repo/
|-- plugin.yaml
`-- prompts/
```

Do not put the pack inside a nested folder unless users register that nested folder by ZIP upload. GitHub clone registration validates only the cloned repository root.
