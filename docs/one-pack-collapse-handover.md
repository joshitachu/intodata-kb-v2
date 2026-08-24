# One Pack collapse -- implementation handover

Status: in progress. Owner: Joshua. Written 2026-08-23.

Goal (decided by the owner, supersedes `one-pack-program.md` scope questions):
`plugin.yaml` is the ONLY creatable pack format that can alter generation --
analysis focus, phases, or output documents. Every authoring surface in Nexus
(chat, Pack Workbench, anywhere else) must produce that one format. Anything
that is not a *creatable pack* is out of scope even if "pack" is in its name.

No LFE cycle for this program, by owner decision.

---

## 1. Scope contract

### In scope -- converge on `plugin.yaml` or delete

| Thing | Why it qualifies | Action |
|---|---|---|
| `PackArtifactV3` / private Analysis Pack | Creatable pack that appends Phase 3 docs | Fold into `docs:`, delete schema |
| Style Pack (`harness_style_pack_versions`) | Creatable pack altering doc presentation; source of `ctx.nexus_presentation`, which currently OUTRANKS `plugin.yaml` `audience:` (`prompts.py:362`) | Fold into `audience:` |
| InstructionPack (`instruction_pack_dir`, `services/pack_loader.py`, `InstructionPackPanel.tsx`, DB column) | Prompt pack altering generation | Delete outright, dead legacy |
| Nexus Pack v2 **pack framing** (`layer`, `governance`, `trust`, `composition.py` stacking) | Competing pack format | Delete framing ONLY -- see out-of-scope |
| Chat capabilities `nexus.packs.create`, `nexus.packs.drafts.*` | Authoring surface emitting the wrong format | Repoint at `plugin.yaml` |
| `harness_packs.py` router (~1200 lines) | Serves the deleted formats; **zero frontend callers** (verified) | Delete |

### Out of scope -- do NOT touch

- **The capability registry**: `harness/packs/runtime.py` + `packs/fixtures/*.json`.
  Named "pack", but it is the agent-chat tool allowlist
  (`harness/registry.py:245`, `agent_chat_runtime.py:1887`,
  `workflow_resume.py:592`). Deleting the fixtures empties the registry and
  agent chat loses every tool (recon R9). A rename out of the pack namespace is
  optional cleanup, not required.
- `data/doc_catalog.yaml` -- built-in default doc set for no-pack runs, not
  user-creatable. `pack-repos/nexus-baseline` already mirrors it as the forkable
  version.
- `documentation_obligations()` (`platforms/strategies.py:90,154,199,252`), the
  5 `intodoc/**/ANALYSIS_GUIDE.md` files, `data/prompt_templates/*`, the phase3
  prompt spine -- platform semantics and protected core.
- Redaction, fabrication validators, budgets, `GROUP_SUMMARY` floor,
  staging -> atomic swap.
- `PackContract` (`providers/custom/pack_contract.py`) -- already the one
  compiler. Keeps.
- `StylePresentation` as a **chat-answer** formatting type
  (`capability_handlers.py:157` -> `generation/prompt_builder.py`). Not a pack.
  Keep a non-pack presentation type for chat when Style Packs go.

---

## 2. Baselines (measured 2026-08-23, before any change)

| Suite | Result |
|---|---|
| `RAG_pipeline/backend` | **64 failed / 2249 passed** |
| `knowledge_base/mcp-server` | **11 failed / 1792 passed** |

All pre-existing. Verified by stashing changes and re-running. Causes:
`tests/test_scripts/test_agent_runtime_*` (fixture/corpus), missing
`OPENAI_API_KEY` in the test env (`test_conversation_task_capabilities`,
`test_project_ownership_api`), GitHub-app route fixtures, model catalogue,
component-description fixtures.

**Any future run must be compared against these numbers, not against zero.**

---

## 3. Done

### 3.1 Orphaned v2 registry deleted
The half-built "unified pack registry" from `one-pack-program.md` s11. Its
schema authority (`mcp_server/services/pack_schema_v2.py`) was already deleted,
so the whole chain was dead-ended. Zero callers, no frontend proxy.

Deleted: `harness/packs/registry_service.py` (1226),
`harness/packs/manifest_v2.py` (434), `harness/packs/manifest_generator.py`
(460), `db/models/pack_registry.py`,
`tests/test_harness/test_manifest_generator.py`,
`tests/test_api/test_pack_manifest_assist.py`.

Edited: `api/routes/harness_packs.py` (dropped `PackManifestGenerator` import,
`PackManifestAssistRequest`/`Response`, and `POST
/builder/drafts/{id}/manifest-assist`), `db/models/__init__.py` (dropped the
`pack_registry` import + 3 `__all__` entries).

**Still open from this step:** tables `packs`, `pack_versions`,
`pack_activations` remain in Postgres. Migrations `084_unified_pack_registry`
and `086_pack_registry_timestamps` are MID-CHAIN
(`083 -> 084 -> 085 -> 086 -> 087 -> 088`), so do NOT delete those files --
add a forward migration that drops the three tables. 084/086 touch nothing else
(verified: 3 `create_table` + indexes; 086 only adds timestamp columns).

### 3.2 `src/harness/providers/mcp_packs.py` -- the RAG -> MCP pack gateway (NEW)
Follows the `mcp_projects.py` adapter pattern. Methods: `draft_pack`,
`create_personal_pack`, `list_packs`, `select_project_pack`, `publish_pack`.

- Identity: forwards the user's `access_token` cookie; MCP's `_caller_user_id`
  reads the same JWT `sub`, so `plugin_packs.owner_user_id` lines up and the
  personal-pack lane is correctly user-scoped with no new auth plumbing.
- Boundary hardening: refuses traversal paths, absolute paths, non-md/yaml
  extensions, >40 files, oversized content, bad project/pack ids -- *before*
  crossing the service boundary, because a draft is model output.
- Errors are redacted (`_safe_text`) so an upstream detail cannot leak a token.
- `select_project_pack` targets `/projects/{id}/plugin`, NOT `/plugins/...` --
  MCP owns project state, not the pack registry.

Tests: `tests/test_harness/test_mcp_packs_provider.py`, 12 passing.

### 3.3 Refinement in the single authority (NEW)
**This was the actual blocker for one format.** MCP's `pack_author.draft_pack()`
accepted only `intent` -- it could start fresh but never refine. That is *why*
chat-side authoring grew its own format: multi-turn refinement had nowhere to go.

- `services/pack_author.py`: added `MAX_CURRENT_MANIFEST_CHARS = 40_000`, the
  `_REFINE` clause, `_prompt(intent, platform, current_manifest)`, and
  `draft_pack(..., current_manifest=)`.
- `api/routes/plugins.py`: `DraftPackRequest.current_manifest` (max 40k) and
  forwarded in the route.
- `harness/providers/mcp_packs.py`: `draft_pack(..., current_manifest=)`.

Result is still validated through the same loader, so a hostile
`current_manifest` cannot smuggle `layer:` / `certification:` through --
asserted by test.

Tests: 4 added to `tests/test_services/test_pack_author.py` (16 passing total).

### 3.4 Verification after the above
| Suite | Result | Delta |
|---|---|---|
| RAG | 64 failed / **2261** passed | +12 new tests, same 64 failures |
| MCP | 11 failed / **1796** passed | +4 new tests, same 11 failures |

Zero regressions.

---

## 4. Not done -- remaining work in order

### Slice A: re-type the draft lifecycle (OWNER DECISION: keep the table)
Decision taken: **keep `harness_pack_drafts`**, change its `artifact` from
`PackArtifactV3` JSON to `{manifest_yaml, files}`. Rationale: chat drafts
survive a reload and the `/pack?draft={id}` deep link in chat keeps working.
Rejected alternative: stateless (delete `PackDraftService` +
`builder_workflow.py`, ~1200 lines, chat holds the manifest in conversation
state) -- simpler but loses resumable drafts and the deep link.

This is bigger than it looks. `draft_service.py` derives real behaviour from V3
*fields*, not just storage:

- `DraftRecord.artifact: dict` (`:102`) and `PrivateVersionRecord.artifact:
  PackArtifactV3` (`:116`)
- `normalized_audience(artifact)` (`:132`) -- reads `artifact.audience.render`;
  becomes the manifest's `audience:` block
- `artifact_hash(artifact, receipts)` (`:137-153`) -- content hash; changing the
  artifact shape changes every hash, and `HarnessPackPreviewReceiptRow` is keyed
  on it (see recon R10, the cross-service hash contract)
- `_validation(candidate)` (`:222,244,283,304,322,411`) -- V3 Pydantic
  validation; must become "does MCP's `load_plugin_manifest` accept this"
- `_parse_artifact` (`:325`)
- compatibility gate reads `artifact.capability_requirements` (`:522,542`) and
  `artifact.output_documents` (`:550`)

`builder_workflow.py`: `PackGenerationResult.artifact: PackArtifactV3` (`:99`),
`_parse_model_proposal` (`:336`), `_draft_summary` (`:408`), and the LangGraph
mutate loop (`:179-226`). The model call should be REPLACED by
`McpPackProvider.draft_pack(...)` -- do not keep a second model path.

Then the 8 handlers in `capability_handlers.py`
(`_PackDraftHandler` `:953`, `PackCreateHandler` `:1106`,
`PackDraftCreate/Read/Mutate/Configure/Validate/Save/Abandon` `:1246-1704`) and
`_pack_generation_instruction` (`:928`), which still writes a V3-shaped brief.

Test modules that assert on V3 and must be rewritten, not deleted:
`test_pack_draft_builder.py`, `test_pack_create_generation.py`,
`test_effect_runtime.py`, `test_pack_runtime.py`,
`test_pack_reference_attachments.py`, `test_conversation_task_capabilities.py`.

**Do not land this partially.** A handler set where `create` emits
`plugin.yaml` while `drafts.*` still emits V3 is worse than either end state.

### Slice B: delete `PackArtifactV3` + the cross-service wire
- RAG: `PackArtifactV3` and its sub-models from `harness/packs/models.py`;
  `nexus_private_analysis_packs` param + validation in
  `harness/providers/mcp_docs.py:244,298-331`
- MCP: `schemas.py:887,985-997`, `providers/base.py:90`,
  `harness.py:4091-4116` (`_apply_private_analysis_packs`, called at `:4132`),
  `_private_analysis_instructions`, `private_analysis_instructions_for_document`,
  `providers/opencode_cli.py:595,993`
- Note `harness.py:4106` currently `raise RuntimeError` on doc-name collision --
  goes away with it

### Slice C: delete `harness_packs.py` + its tables
Verified zero frontend callers (`grep -rn "harness/packs"` in
`app/ components/ lib/` returns nothing; the UI is on `/api/packs/*` ->
`/mcp/plugins/*`). Reachable only via chat capabilities, so it goes after
Slice A.

8 tables in `db/models/harness_pack.py`: `harness_custom_pack_versions`,
`harness_pack_activation_snapshots`, `harness_private_analysis_packs`,
`harness_private_analysis_pack_versions`,
`harness_private_analysis_pack_shares`, `harness_pack_attachment_blobs`,
`harness_pack_attachment_bindings`, `harness_pack_promotion_proposals`.
Keep for now: `harness_pack_drafts` (Slice A),
`harness_project_pack_activations`, `harness_organization_governance_policies`,
`harness_pack_preview_receipts`, `harness_style_pack_versions` (Slice D).

**Dump these tables before dropping** -- live Postgres has real uptime.

### Slice D: Style Pack -> `audience:`
Also fix the precedence inversion: `prompts.py:362`
`nexus_presentation or presentation_from_pack_audience(audience)` means the
request-level style **outranks** the pack. If `plugin.yaml` is the source of
truth, invert it. Keep a non-pack presentation type for chat answers.

### Slice E: InstructionPack deletion
`config.py:512 instruction_pack_dir`, `harness.py:7087,7112-7113`,
`db/models.py:80`, `schemas.py:208,1063`, `providers/__init__.py:16,78`,
`api/routes/projects.py:212,1154`, `services/pack_loader.py`,
`dashboard/src/components/InstructionPackPanel.tsx`.

### Slice F: Nexus v2 pack framing
Delete `composition.py` stacking and the `layer`/`governance`/`trust`
vocabulary. **Keep `runtime.py` + `fixtures/*.json`** (capability registry).
Also strip the now-ignored `schema_version: 2` / `layer:` / `governance:` blocks
from the 5 bundled `plugin_packs/*/plugin.yaml` -- Pydantic ignores unknown keys
so they load fine today, but they read as authoritative and are not
(`knowledge_base/CLAUDE.md` lists them as RETIRED).

---

## 5. End-to-end test plan (not yet run)

The owner explicitly asked for real E2E, not unit-test theatre.

**Container caveat:** `mcp-server` mounts only `data`, `intodoc-output`,
`output`, `packs` -- **`src` is NOT volume-mounted**, so any source change needs
`docker compose build mcp-server && docker compose up -d mcp-server` before live
testing. Same for `backend`.

1. `POST /mcp/plugins/draft` with a live model -- assert the manifest loads via
   `load_plugin_manifest` and the file set matches
   `required_generated_file_paths`.
2. Same call **with `current_manifest`** -- assert it refines rather than
   restarts (earlier choices preserved).
3. `POST /mcp/plugins/personal` -- persist; assert it appears in
   `GET /mcp/plugins` as `personal: true, usable: true`.
4. Same pack **through chat** -- assert byte-identical format to the Workbench
   route. This is the acceptance test for "one format everywhere".
5. `POST /mcp/plugins/{id}/publish` -- real branch push + PR against
   `MCP_SWAP_PACK_STARTER_REPO_URL`
   (`https://github.com/joshitachu/swap-pack-starter`).
   **`MCP_PACK_REPO_TOKEN` is NOT set in any `.env`** -- source it from
   `gh auth token` (`gh` is authed as `joshitachu`, scopes include `repo`).
   Owner has authorised the PR.
6. Bind the pack to a project (`PATCH /mcp/projects/{id}/plugin`) and run a
   **real legacy generation** with model `openrouter/stealth/ox-alpha`
   (resolved: `stealth/ox-alpha` is the only match among 422 OpenRouter models;
   `OPENROUTER_API_KEY` is present in the container env).
   Assert: output docs == the pack's declared `docs:`, NOT the default 10-doc
   catalog; and per `one-pack-program.md` s10 test 1, ALSO assert
   `state.partial_groups` empty, `state.thin_bundle_groups` empty, and
   `_assessment/failed_docs.json` records no stub. A bare "N docs" green is a
   FALSE PASS -- the silent-stub path (recon 7.0 item 1) looks like success.

---

## 6. Known traps

- `providers/opencode_cli.py` mirrors part of the harness contract and still
  reads `harness._plugin_mode` (7 sites). Any contract change must be mirrored
  there or that path diverges silently.
- `rag_bridge`'s `NN_` digit-prefix gate silently drops pack-named documents on
  5 secondary push paths (recon R8), so a pack declaring `INJECTION_FINDINGS.md`
  generates but never reaches RAG. Fix generally: the pack's declared doc names
  are the authority, not a filename convention.
- Patch templates (`phase2_patch.md`, `phase3_patch.md`,
  `phase3_synthesis_patch.md`) are rendered by `true_patch_executor.py:527`
  which builds `PromptRenderer()` with no `pack_dir` -- so a pack's wording
  applies on generate but not on update. The *contract* IS threaded
  (`_resolve_project_contract` `:462`); only the prompts are not. One-line fix.
