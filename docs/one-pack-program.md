# One Pack Across Nexus -- Program Spec

Status: active. Owner: Joshua. Written 2026-08-20.

This is the working spec for collapsing the five pack concepts in this repo into
one manifest format, and for giving that format the power to change what every
pipeline phase looks at -- not just what documents come out at the end.

Subagents working on this program should read this file first. It is the source
of truth for scope, authority boundaries, and acceptance.

---

## 1. Goal

One pack format across Nexus. A pack owns **attention at every phase** (what
each phase looks at, and what question is asked of it) plus the **output set**.
Extraction stays universal. Shared packs come through GitHub; private packs are
authored in-app.

### Why the current design cannot do this

Today a user-authored pack can only ever ADD to the legacy output. The
mechanism is one operator, `analysis_parser.py:47`:

```python
section_names: set[str] = set(PHASE2_SECTION_NAMES) | extra_section_names
```

A pack's declared sections are UNIONED with the fixed 16. So a pack whose
purpose is "find SQL injection" still has to emit COMPONENTS, EXECUTION_FLOW,
TRANSFORMATION_DETAIL, RISK_FLAGS and the rest. Its own lens is a minority of
the output, the token budget is spent on sections nobody asked for, and the
model's attention is split across a contract that contradicts the pack's
purpose.

Same story at the document level: `harness.py:3861`
(`_apply_private_analysis_packs`) appends `PRIVATE_ANALYSIS__*` docs to the
standard set and explicitly does not touch prior phases.

### Non-goals

- Changing the scanner or what it extracts
- Changing the protected core (see section 5)
- Multi-tenancy
- New UI design language

---

## 2. The three layers

The key decomposition. Extraction is universal; selection and interrogation are
pack-owned.

| Layer | Example | Owner |
|---|---|---|
| **Extraction** -- pull everything out of the source | scanner captures SQL bodies, tMap expressions, params, context vars, code, verbatim | platform, universal. A SQL-injection pack wants the SAME complete extraction, not a different scanner |
| **Attention** -- which extracted evidence enters which phase call, and what question is asked of it | "screen chunks for sql_queries + orm_entities", "analyze for taint paths, not execution flow" | **pack, at every phase.** This is the missing capability |
| **Output** -- doc set, sections, audience | `INJECTION_FINDINGS.md`, `REMEDIATION_BACKLOG.md` | pack |

---

## 3. The one manifest

Base format is `plugin.yaml` (the existing intodoc plugin pack), extended.
Rationale: it is the only existing format that can already change per-phase
attention, and the five bundled packs plus `pack-repos/table-inventory-pack`
are already in it, so migration is small.

### 3.1 Default pack (foundation layer)

```yaml
schema_version: 2
id: intodata.engineering-handover
name: Engineering Handover
version: 1.0.0
description: >
  Full technical handover of an integration codebase.

# ---- AUTHORITY ----------------------------------------------------------
layer: foundation                        # foundation | functional | style
governance:
  owner: intodata                        # intodata | organization | project | user
  scope: global                          # global | organization | project | user
  provenance: intodata-packs             # foundation only; Git/PR + digest

# ---- CAPABILITIES (foundation ONLY, never user-writable) ----------------
capabilities:
  provider: mcp.docs.default
  workflows:
    - id: documentation.generate
      capability_ids: [mcp.docs.generate_project_documentation, ...]
  scopes:
    permissions: [documents.read]
    project_required: true
  policy:
    evidence_required: true
    read_only: false
    approval_required_for: [mcp.docs.update_from_evidence]

# ---- LENS: what each phase looks at ------------------------------------
lens:
  focus:                                 # Phase 1 chunk screening
    include: [source_summary, sql_queries, routes, handlers, auth,
              config_references, db_connections, ddl_schema, orm_entities]
    exclude: [binary, static_assets, generated_files]

  sections:
    mode: extend                         # extend | replace
    declare:
      - id: COMPONENTS
        question: "what units exist, of what type, with what parameters"
      - id: EXECUTION_FLOW
        question: "what calls what, in what order, triggered how"
      # ... the 13 required + 3 conditional sections, made explicit

  requirements: |                        # additive; Nexus platform policy wins
    Talend-specific required evidence: job purpose, component names/types,
    tMap/tJava logic, context variables, routines, connections, endpoints,
    die-on-error handling, inactive components, migration concerns.

# ---- OUTPUT ------------------------------------------------------------
docs:
  - name: 01_ARCHITECTURE_MAP
    sections: [Overview, Component Topology, Connections, Evidence Register]
    phase2_sections: [COMPONENTS, EXECUTION_FLOW, EXTERNAL_SYSTEMS]
  - name: 10_UNIT_REFERENCE
    per_unit: true
    sections: [Purpose, Components, Code, Context, Discrepancies]

# ---- PRESENTATION ------------------------------------------------------
audience:
  roles: [integration engineer, platform architect]
  tone: neutral
  technical_depth: deep
  output_organization: sectioned

# ---- ACCEPTANCE --------------------------------------------------------
quality_gates:
  mode: blocking
  min_doc_bytes: { 01_ARCHITECTURE_MAP: 5000 }
  required_sections: { 01_ARCHITECTURE_MAP: [Component Topology] }
  required_terms: [source, evidence]

# ---- EXECUTION (foundation only) ---------------------------------------
depth:
  investigation: run
  analysis: { policy: run, concurrency: 6 }
  scorecard: skip
  doc_bundles: run

compatibility:
  platforms: [talend, workato, lobster, generic]
```

### 3.2 User pack (functional layer) -- the target use case

Shorter, narrower, and declares `mode: replace`. No prompt files. No phase
policy. No model choice.

```yaml
schema_version: 2
id: user.42.sql-injection-review
name: SQL Injection Review
version: 0.3.0
layer: functional
governance:
  owner: user
  scope: project

lens:
  focus:
    include: [sql_queries, orm_entities, db_connections, config_references, routes]
    exclude: [binary, static_assets, generated_files]
  sections:
    mode: replace                        # only these, plus GROUP_SUMMARY
    declare:
      - id: INJECTION_SINKS
        question: "every place user-controlled data reaches a query; name the unit and quote the construction site"
      - id: PARAMETERIZATION
        question: "parameterized vs concatenated, verbatim, per sink"
      - id: TAINT_PATHS
        question: "source to sink, naming each hop"
      - id: EXPLOITABILITY
        question: "is the sink reachable from an external entrypoint"
  requirements: |
    Required evidence: query construction sites, string concatenation into SQL,
    dynamic table/column names, ORM raw-query escapes, input validation at entry.

docs:
  - name: INJECTION_FINDINGS
    sections: [Summary, Findings by Severity, Taint Paths, Evidence Register]
    phase2_sections: [INJECTION_SINKS, TAINT_PATHS, EXPLOITABILITY]
  - name: REMEDIATION_BACKLOG
    per_unit: true
    sections: [Sink, Current Code, Proposed Fix, Effort]

audience:
  roles: [security reviewer, tech lead]
  technical_depth: deep

quality_gates:
  mode: blocking
  required_terms: [file, query, parameter]
```

---

## 4. Authority tiers

| Field | user / project (`functional`) | org admin | intodata (`foundation`) |
|---|---|---|---|
| `lens.focus.include/exclude` | yes, from the closed 9-category vocabulary | caps which categories | defines vocabulary + regexes |
| `lens.sections.declare` | yes, own sections and questions | caps count | -- |
| `lens.sections.mode: replace` | yes | can forbid | -- |
| `lens.requirements` | yes, bounded text | -- | ships platform defaults |
| `docs[]` name / sections / order | yes, replaces the set | caps doc count | ships the default pack |
| `docs[].per_unit` | yes, subject to budget estimate | caps | -- |
| `docs[].phase2_sections` | yes | -- | -- |
| `audience` fields | yes, whitelisted enums | whitelists values | defines enums |
| `quality_gates` thresholds | yes, upward only | floors | floors |
| `quality_gates.mode` | no | can force `blocking` | yes |
| `depth.*` (phase policy) | no | no | yes |
| `capabilities.*` | no | no | yes |
| `evidence_agents` | no | no | yes |
| model / concurrency | no | tier allowlist | yes |
| `governance.provenance` | no, server-owned | no | yes |

The `focus` vocabulary is CLOSED. `evidence_bundles.py:27` `CATEGORY_PATTERNS`
defines exactly nine includable categories -- `sql_queries`, `ddl_schema`,
`orm_entities`, `db_connections`, `config_references`, `routes`, `handlers`,
`auth`, `source_summary` -- and `_EXCLUDE_PATTERNS` defines three excludes:
`binary`, `static_assets`, `generated_files`. A user selects from that list and
cannot invent a category the screener cannot evaluate.

User packs are YAML-only. No arbitrary `prompts/*.md` files -- that is the
injection vector and the review-bypass. Section `question` text and
`lens.requirements` are length-capped fields, not files. Foundation packs keep
prompt files because Git/PR review stands behind them.

---

## 5. The protected core -- never expressible in any manifest

There is no field for these, so there is nothing to abuse and no whitelist to
maintain. This is what makes handing packs to users defensible.

| Protected | Where it lives |
|---|---|
| Credential redaction | pre-LLM filter |
| Fabrication validators: component-echo, no-unavailability, protected-fact, unit-count | `providers/custom/fabrication_validators.py` |
| The enforced prompt spine: canonical unit count, doc-pack traceability, pipeline-gap rule, protected-fact rule, narrative rules, Evidence Register, ASCII | `data/prompt_templates/phase3_doc.md` (~110 lines, always injected) |
| Call / token budgets, phase floors, partition planning | `_reserve_model_budget`, `docgen_max_run_calls` |
| RBAC, capability allowlist, project scoping | RAG backend |
| `GROUP_SUMMARY` present in every Phase 2 output | section floor |
| Staging -> atomic swap, failure isolation | `harness.py` |

A pack decides WHAT we look at and WHAT we ship. It cannot decide HOW
rigorously we do it.

---

## 6. Field -> code seam mapping

The manifest compiles into the internal execution contract that already exists.
No new execution engine.

| Manifest field | Code seam | Change needed |
|---|---|---|
| `lens.focus.include` | `_pack_investigation_categories()` -> `CATEGORY_PATTERNS` (`harness.py:742-768`) | none, works today |
| `lens.sections.declare` | `extra_section_names` -> `parse_phase2_sections` (`analysis_parser.py:47`) | **`mode: replace` -- stop unioning with `PHASE2_SECTION_NAMES`** |
| `lens.sections[].question` | `phase2_analysis.md` `${SECTION_HEADINGS}` | headings today, headings + questions after |
| `lens.requirements` | `platform_analysis_requirements()` (`platforms/strategies.py`) | make pack-overridable |
| `docs[].name` | `_required_docs` (`harness.py:699-707`) | none, replaces today |
| `docs[].sections` | `_doc_section_requirements_extra` | none |
| `docs[].phase2_sections` | `_doc_section_bundle_map` | **stop blanking it at `harness.py:704`** |
| `docs[].per_unit` | `_per_job_doc_units` / per-unit expansion | unify `per_job` / `per_unit` naming |
| `audience` | `nexus_presentation` -> `presentation_instructions_for_phase` | none, wired |
| `quality_gates` | `QualityGates` (`plugin_schema.py`) | none |
| `depth` | `PhasePolicy` / `PhaseConfig` | rename only |
| `capabilities` | Nexus `PackManifest.workflows` / `default_scopes` / `policy_hints` | port from `harness/packs/fixtures/*.json` |

---

## 7. Audit findings

### 7.0 RECON COMPLETE -- read `docs/one-pack-recon.md` first

The recon agent finished and its report at `docs/one-pack-recon.md` (four
sections plus RISKS) SUPERSEDES this section wherever they conflict. Headline
numbers: **26 HARD sites, 19 SOFT, 1 dead, across 11 modules** -- not the 5
sites the table below plans for. `mode: replace` remains reachable, but it is
four independent call paths plus a fifth for living docs, not one coherent
change.

**HARD sites the original pass missed -- read these before editing anything:**

1. `harness.py:3019-3024` -- raises `RuntimeError` when a Talend Phase 2 body
   lacks the 13 sections. The raise is CAUGHT by
   `_contain_phase2_group_failures` (`harness.py:2024`), so `mode: replace`
   does not crash: **every group becomes a PARTIAL stub and the run publishes
   stubs while reporting success.** Reachable via `_phase2_group_direct`
   (`harness.py:3315`) whenever `analysis_policy == "bounded"` or on the
   small-project direct path. This is the program's most dangerous failure
   mode -- silent, and it looks like success.
2. `harness.py:3786-3790` -- iterates `PHASE2_SECTION_NAMES` instead of its own
   `section_names` argument; the `if not selected` fallback dumps the entire
   analysis into `UNCLASSIFIED`. The table below wrongly marks this "no change".
3. `harness.py:5879-5885` -- plus a `_parse_phase2_sections` call at `:5884`
   with no `extra_section_names`.
4. The living-docs subsystem -- `true_patch_executor.py:302, 630-634,
   1163-1170` and `patch_section_map.py:43-67, 97, 131-145`. An independent
   call path, absent from the table below.

**A layer this spec did not model: prompt assets.** Six files assert the
16-section contract in TEXT. These are now an immutable Nexus foundation layer:

- 4 files under `data/prompt_templates/phase2_platform_sections/`, resolved by
  `prompt_renderer.py:41-48`, which has no plugin branch
- 5 `ANALYSIS_GUIDE.md` files inside `intodoc/`, injected into every Phase 2
  call including `bounded` mode via `harness.py:6316-6338`

A manifest may declare focused sections, but it cannot replace platform
semantics. Nexus always promotes the platform guide to system policy. Packs may
append lower-authority guidance through
`prompts/extensions/platforms/<platform>.md`; conflicts are ignored.

**Corrections to the table below:**

- `validators.py:620` is NOT missing a platform check. It sits inside the
  Talend branch, guarded by the early return at `validators.py:580`. There is
  no pre-existing bug to fix.
- `synthesis_impact_mapper.py` is fully doc-name agnostic -- the doc name
  appears only in its docstring. Verified tolerant, no change needed.

**No existing pack -> section seam (recon R3).** `extra_section_names` is fed
only by the scanner guide. `PluginPackManifest` has no `lens`, `sections`, or
`phase2_sections` field, and `steps` is required non-empty. The contract object
is new build, not a rewire.

**`prompt_packs.py` cannot be deleted as a unit (recon).**
`materialize_prompt_packs()` is live and unrelated to the section constants.
Section 8's verdict on it applies to the CONSTANTS only.

### 7.1 DECISIONS taken after recon

**D1. `_plugin_mode` becomes a contract object, not a filesystem path.**
Today `_plugin_mode` is `plugin_pack_dir is not None` (`harness.py:716`) and
every legacy-contract off-switch keys on it -- a git-clone directory path.
Section 9 of this spec says user packs are DB-stored with no ZIP upload, so the
two are incompatible (recon R5). Rejected option: materialise DB packs to a
temp dir to keep the path-based check working. That is a workaround, and repo
rules 9 and 10 (no workarounds, generalize fixes) forbid it. Chosen: introduce
an explicit `PackContract` object, resolved from either a v2 manifest row or a
pack directory, and replace `_plugin_mode` plus the ~12 `platform: str`
validator/prompt/bundle signatures with it. This is the root-cause fix and it
is what makes `mode: replace` a contract decision rather than a path accident.

**D2. `doc_quality_scorer` becomes contract-driven, in agent 2's scope.**
`doc_quality_scorer.py:39` computes publishable doc keys from the default
catalog at IMPORT time and returns `overall=0.0, docs=[]` for pack-named output
(recon R6). Section 9's "preview-and-score before activation" gate is a
substitute control for dropping Git review on user packs, so a scorer that
returns zero for every pack makes that control fictional. It must score against
the active pack's declared docs. Not deferrable to agent 3.



Best finding: **the section contract is already platform-conditional, and only
Talend enforces the 13 sections.**

```python
# validators.py:735  group_analysis_is_complete
required = (GROUP_SUMMARY_SECTION,) if platform != "talend" else (*REQUIRED_SECTION_NAMES, GROUP_SUMMARY_SECTION)
```

Same conditional in `missing_required_analysis_sections` (`validators.py:711`),
`normalize_analysis_guide_headings` (`validators.py:700`), and the agent-output
check (`harness.py:5878`). For Workato / Lobster / Generic the enforced floor is
ALREADY just `GROUP_SUMMARY`. So `mode: replace` with a `GROUP_SUMMARY` floor is
not a new concept -- it is existing non-Talend behaviour, generalized from
"platform" to "the pack's declared contract".

| Consumer | Behaviour | Work needed |
|---|---|---|
| `analysis_parser.py:47` | `set(PHASE2_SECTION_NAMES) \| extra` | the pivot: take the contract, do not union |
| `phase2_claims.py:218` | `sections=ANALYSIS_SECTION_NAMES` in the claim spec | contract-driven |
| `phase2_claims.py:724` | writes `### SECTION` + UNKNOWN stub for all 16 | **this is the mechanism that forces the legacy shape** -- contract-driven |
| `prompts.py:139` | prompt asks for all 16 headings | contract-driven |
| `validators.py:620` | enforces all 13 with NO platform check -- inconsistent with the other three | contract-driven; also fixes an existing bug |
| `validators.py:700/711/735`, `harness.py:5878` | platform-conditional, floor already `GROUP_SUMMARY` | swap platform -> contract |
| `harness.py:2856`, `3788`, `204-213` | order + dedupe, presence-tolerant | no change |
| `true_patch_executor.py:278` | `if name in sections`, emits unknown names after | no change, already tolerant |

Doc-level replace looks equally safe: `harness.py:1601` already drops
`09_EXECUTIVE_SUMMARY` conditionally, and `harness.py:4010-4011` handles its
absence. NOT yet verified: the enterprise doc-set validator, and
`synthesis_impact_mapper.py` / `patch_section_map.py` doc-name dependencies.

---

## 8. The five pack concepts to collapse

| # | Concept | Artifact | Surfaces | Verdict |
|---|---|---|---|---|
| 1 | plugin pack | `plugin.yaml` | `routes/plugins.py`, `plugin_packs/*`, dashboard `PluginPacksPage.tsx` | **KEEP** -- becomes the one manifest |
| 2 | Nexus Pack v2 | `PackManifest` JSON, foundation/functional/style | `packs/composition.py`, `catalog.py`, `models.py`, `PackLibrary`, `PackManager` | absorb `layer` + `capabilities`, then delete |
| 3 | Style Pack | `StylePresentation` rows | `StylePackEditor.tsx`, `harness_style_pack_versions` | absorb into `audience:`, delete |
| 4 | private Analysis Pack | `PackArtifactV3` | `AnalysisPackBuilder.tsx`, `draft_service.py`, `PackWorkbench` | **keep the lifecycle, delete the schema** |
| 5 | InstructionPack | `*.md` preambles + `questionnaire.json` | `services/pack_loader.py`, `InstructionPackPanel.tsx`, `config.instruction_pack_dir` | delete outright, dead legacy layer |

Plus `providers/custom/prompt_packs.py`, the legacy Claude-CLI prompt-corpus
adapter that owns `ANALYSIS_SECTION_NAMES`. The constant becomes the default
pack's `lens.sections`, then the module goes.

### Keep and repoint

- `draft_service.py` -- lifecycle (drafts, versions, fork, attachments, preview, share, propose_promotion), re-typed to the new manifest
- `project_service.py` -- activation, snapshots, org policy, quality scoring
- `pack_governance.py` -- provenance + digest, foundation layer only
- `PackWorkbench.tsx`, `AnalysisPackBuilder.tsx`, `packCommand.ts`, `app/pack/page.tsx`, `DocumentationPackSelector.tsx` -> one editor + one library, mounted in chat, dashboard, project settings
- `harness.py` execution contract -- unchanged; only what feeds it changes

---

## 9. Distribution model

Two shelves in the toolbox.

| | IntoData packs | My packs |
|---|---|---|
| Origin | GitHub PR, digest-pinned | authored in-app |
| Visible to | everyone | just the author, then optionally shared |
| Scope | `global` | `user` -> `project` -> `organization` |
| Editable | no, immutable, versioned in Git | yes, versioned in DB |

Ladder: **private -> share to project -> share to org -> propose promotion ->
GitHub PR -> becomes an IntoData pack.** Only the last rung needs an engineer.
`draft_service.py` already has `share()` and `propose_promotion()`.

Mechanisms that already exist and should be reused, not reinvented:
`PackScope` / `PackOwner` enums in `harness/packs/models.py`; server-owned id
namespaces enforced by `_canonical_style_pack_id` (`project_service.py:182-198`).

GitHub stays mandatory for the foundation layer only. Rationale: PR review was
necessary because the old format could ship arbitrary prompt files into
privileged phase calls and set `phases:` / `model:` / `evidence_agents:`. Under
the v2 split a user pack is bounded YAML with none of that, so there is nothing
left for a reviewer to catch that a validator cannot catch mechanically. Do not
reopen ZIP upload; in-app authoring with DB storage is strictly better.

### Substitute controls for dropping Git on user packs (non-optional)

- Record `pack_id + version + content_hash + activated_by + activated_at` on
  every `GenerateRun`. The snapshot machinery
  (`harness_pack_activation_snapshots`) exists but is not wired to doc runs.
- Immutable versions. Editing creates v+1; never mutate a version that has
  produced a deliverable.
- Preview-and-score before activation, so nothing untested reaches a client doc.
- Delete means DELIST, not purge -- versions that produced deliverables are
  retained for audit.
- Org-level budget cap per pack. `per_unit: true` x unit count is the cost
  footgun; show the estimate in preview.

Private means invisible, not unregulated. A private pack still produces client
documents, so it still gets the protected core, the preview gate, and the audit
record.

---

## 10. Acceptance tests

1. A SQL-injection pack authored in-app produces **exactly 2 documents and 4
   Phase 2 sections** on a real Talend project **AND** `state.partial_groups`
   is empty, `state.thin_bundle_groups` is empty, and
   `_assessment/failed_docs.json` records no stubbed document. Without these
   three extra assertions the test passes on published stubs -- see recon R1
   and section 7.0 item 1. A naive "2 docs, 4 sections" assertion is NOT
   sufficient and must not be accepted as a green test.
2. The migrated default handover pack reproduces today's output structure --
   same 10 documents, same 16 Phase 2 sections.
3. No pack, hand-crafted or hostile, can disable a validator, skip redaction,
   or exceed the budget ceiling.
4. A pack declaring 4 sections causes the Phase 2 PROMPT to ask for 4 sections.
   Assert against the rendered prompt text, not just the parsed output --
   otherwise the prompt-asset layer (section 7.0) keeps demanding 16 while the
   parser accepts 4.
5. `doc_quality_scorer` returns a non-zero score for a pack-named document set
   (decision D2).

---

## 11. Agent briefs

Three agents, sequential. Each reads this file first.

### Agent 1 -- `recon` (opus, read-only)

Produce a written map. Change no files.

1. **Section consumers.** Every reader of `PHASE2_SECTION_NAMES`,
   `ANALYSIS_SECTION_NAMES`, `REQUIRED_SECTION_NAMES`,
   `CONDITIONAL_SECTION_NAMES`, and every hardcoded section-name literal.
   For each: file:line, hard (fails/rejects when a section is absent) vs soft
   (iterates and skips), and the exact edit to make it contract-driven.
   Confirm or correct the table in section 7.
2. **Doc consumers.** Every place that depends on a specific document name
   existing (`09_EXECUTIVE_SUMMARY`, `07_...`, etc). Include the enterprise
   doc-set validator, `synthesis_impact_mapper.py`, `patch_section_map.py`.
   Verdict per site: tolerant, or needs a graceful-degrade change.
3. **Pack surface inventory.** All five concepts: every file, test, migration,
   frontend import. Produce a dependency-ordered deletion plan (what can be
   removed first without breaking imports).
4. **Extraction artifacts.** What is written into `docs_staging/` by which
   phase, what survives Phase 4 cleanup and the atomic swap, and what a
   scan-fingerprint-keyed persisted evidence layer would need to store.

Deliverable: append findings to this file as section 12, or write
`docs/one-pack-recon.md` and reference it.

### Agent 2 -- `capability` (fable)

The capability arc. Read `docs/one-pack-recon.md` in full before writing code;
it supersedes section 7's original table. Scope was rebalanced after recon:
extraction persistence moved to agent 3 so this agent stays focused on the
contract and the pivot, which is the risky part.

Do the steps IN THIS ORDER. The ordering is the plan -- the semantics fall out
of the refactor almost for free once the contract flows, and doing them in the
other order means touching 26 sites twice.

1. **`PackContract` object.** One resolved, validated object carrying: declared
   sections (id + question, ordered), `mode` (extend | replace), the effective
   section floor (`GROUP_SUMMARY` only), declared docs + their
   `phase2_sections`, focus categories, and `requirements` text. Resolvable
   from EITHER a v2 manifest row (DB) or a pack directory. See decision D1.
2. **Thread it.** Replace `_plugin_mode` (`harness.py:716`) and the ~12
   `platform: str` parameters across the validator / prompt / bundle signatures
   that recon enumerates. Mechanical but wide; this is where the 26 HARD sites
   get resolved. `platform` stays where it genuinely means platform (Talend
   wording, ESB detection) -- it is only replaced where it is being used as a
   proxy for "which section contract applies".
3. **v2 schema + compiler.** Pydantic v2, per section 3. Both v1 and v2 parse
   during the compatibility window. Compiler output IS the `PackContract`.
4. **Immutable platform-policy path.** Keep
   `phase2_platform_sections/` server-owned, always inject the platform
   `ANALYSIS_GUIDE.md` as system policy, and expose only additive
   `prompts/extensions/platforms/<platform>.md` slots. A focused manifest may
   ask four questions, but it cannot redefine Talend/Workato/etc. semantics.
5. **`mode: replace` semantics**, including doc routing at `harness.py:704`
   (stop blanking `_doc_section_bundle_map`) and the living-docs path
   (`true_patch_executor.py`, `patch_section_map.py`).
6. **Migrate defaults.** `data/doc_catalog.yaml` ->
   `intodata.engineering-handover`; the 5 bundled packs in `plugin_packs/` ->
   v2; port the `capabilities` block from `harness/packs/fixtures/*.json`.
7. **`doc_quality_scorer` contract-driven** -- decision D2. Stop computing
   publishable keys from the default catalog at import time.
8. **Tests:** acceptance tests 1, 2, 4, 5 in section 10. Test 1 MUST include
   the three anti-stub assertions; a bare "2 docs, 4 sections" green is a false
   pass.

Hard constraints:

- Do NOT touch the protected core (section 5). If a change appears to require
  weakening a validator, redaction, the prompt spine, or a budget, stop and
  report instead of doing it.
- Do NOT delete anything. Deletions happen last, by the orchestrator.
- `harness.py` is 6,860 lines. Read surrounding code before editing; several
  sites look tolerant and are not (recon item 2).
- The silent-stub path (section 7.0 item 1) is the failure mode to design
  against. After the pivot, a pack whose sections are all satisfied must NOT
  land in `partial_groups`.

### Agent 3 -- `consolidate` (opus)

Depends on agent 2's schema. Adversarial review by the orchestrator happens
between 2 and 3.

0. **Persist the extraction layer** keyed by scan fingerprint (moved here from
   agent 2 after recon rebalancing -- it is independent of the contract work).
   Today `phase4_cleanup.py:83-102` keeps the ANALYSIS and deletes the
   EVIDENCE, which is inverted for a pack-based product: swapping lenses should
   reuse evidence, and the cached interpretation belongs to the OLD pack.
   Recon section 4 specifies which artifacts, sizes, and the cache key -- reuse
   the partition orchestrator's existing fingerprint notion, do not invent one.
1. One pack table + one activation table; migration collapsing the relevant
   parts of `059`-`074`.
2. One API router: collapse `routes/plugins.py` and `routes/harness_packs.py`.
3. Scope ladder (`user` -> `project` -> `organization` -> certified) reusing
   `PackScope` / `PackOwner` and the server-owned id namespace pattern.
4. Audit fields on `GenerateRun` (section 9).
5. Preview run + budget/quality estimate gate before activation.
6. One editor + one library component, mounted in the chat toolbox, the
   dashboard, and project settings. Repoint `/pack` (`lib/packCommand.ts`).
7. **Doc-name push paths (handed over by agent 2, recon D45/D35).** The
   `rag_bridge` digit-prefix gate silently drops non-`NN_`-prefixed documents on
   5 secondary push paths, so pack-named output (`INJECTION_FINDINGS.md`)
   generates but never reaches RAG. Same class of issue in
   `git_diff_patcher`'s doc-name heuristics. Fix generally -- a pack's declared
   doc names are the authority, not a filename convention.
8. **Thread `audience:` into presentation.** Agent 2 made it parse and validate
   but did not wire it; the existing `nexus_presentation` seam is fed from ctx
   by the RAG side. Connect manifest audience to
   `presentation_instructions_for_phase`.

Known trap: `providers/opencode_cli.py` mirrors part of the harness contract.
Agent 2 left it alone deliberately (legacy path, "delete nothing"), but any
contract change must be mirrored there or that path diverges silently.

---

## 12. Sequencing and gates

Sequential, because `harness.py` serializes almost everything.

```
recon (opus, read-only)
  -> orchestrator reviews map, updates this spec
capability (fable)
  -> orchestrator adversarial review: can a pack disable a validator,
     skip redaction, or blow the budget?
  -> pytest + npm test
consolidate (opus)
  -> pytest + npm test
deletions (orchestrator, inline, dependency order from recon)
```

No LFE cycle for this program by decision of the owner. The substitute gates
are: the recon map before implementation, an independent adversarial review
between agents 2 and 3, and full test runs between every stage.
