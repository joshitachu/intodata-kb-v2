# One Pack Across Nexus -- Recon Map

Agent 1 (`recon`) deliverable for `docs/one-pack-program.md`. Read-only pass; no
source file was modified.

Status: complete. Date 2026-08-20.

Conventions used throughout:

- Every line reference was opened and read. Where the program spec cites a line
  that is off by a few, the corrected line is given and the discrepancy noted.
- **HARD** = the site fails, raises, rejects the model output, or silently
  produces structurally wrong output when a declared section/document is absent.
- **SOFT** = the site iterates and skips; it is presence-tolerant and needs no
  change for `mode: replace`.
- **DEAD** = the code is unreachable in the current call graph. Listed because a
  static grep finds it and an implementer will otherwise waste time on it.

Path prefixes:

- `MCP` = `knowledge_base/mcp-server/src/mcp_server`
- `DATA` = `knowledge_base/mcp-server/data`
- `INTODOC` = `knowledge_base/intodoc/src/intodoc`
- `RAG` = `RAG_pipeline/backend/src`

---

## 0. Executive summary of findings

1. `mode: replace` is reachable **with caveats**, but **section 7 of the spec
   undercounts the HARD surface by roughly a factor of five**. The verified count
   is **26 HARD sites, 19 SOFT sites and 1 dead site** across 11 code modules,
   plus **6 prompt-asset files** that assert the 16-section contract in text and
   have no pack override path. Section 7 lists 5 sites needing change.
2. **The single highest-value miss: `harness.py:3019-3024` raises
   `RuntimeError` when a Talend Phase 2 body lacks the 13 required sections.**
   The raise is contained by the salvage path
   (`_contain_phase2_group_failures`, `harness.py:2024`), so a
   `mode: replace` run on a small/bounded Talend project does not crash --
   **every group silently becomes a PARTIAL stub and the run publishes stubs**.
   That is the worst possible failure mode: green run, empty product.
3. **Second miss: `harness.py:3786-3790`
   (`_targeted_analysis_bundle_parts`) iterates `PHASE2_SECTION_NAMES`, not the
   `section_names` argument.** Pack-declared sections can never be selected, and
   the `if not selected` fallback dumps the entire group analysis into
   `UNCLASSIFIED_OR_EXTRA_FINDINGS`. Spec section 7 lists `harness.py:3788` as
   "presence-tolerant, no change". That is wrong.
4. **The spec row `validators.py:620 | enforces all 13 with NO platform check`
   is incorrect.** Line 620 sits inside a Talend-only branch guarded by the
   early return at `validators.py:580`. All four guide-section validators are
   consistently platform-conditional. There is no pre-existing bug to fix there.
5. **There is no existing wiring from a pack to `extra_section_names` at all.**
   `extra_section_names` is populated only by
   `_guide_section_names(_preprocess/{analysis_guide_file})`
   (`harness.py:2780-2781`, `3652-3653`), and that guide file is written from
   the **intodoc scanner's** platform prompt corpus
   (`services/claude_service.py:1399-1401`, sourced at
   `services/scanner_service.py:966-968`). `PluginPackManifest`
   (`services/plugin_schema.py:153-165`) has no `lens`, no `sections`, and no
   `docs[].phase2_sections` field. Spec section 6's row
   "`lens.sections.declare` -> `extra_section_names`" describes a seam that must
   be built, not one that exists.
6. **The 16-section contract is asserted in six prompt-asset files that no pack
   can override**, including five inside the scanner package. See section 1.4.
   The spec's non-goal "changing the scanner" collides with this.
7. `mode: replace` at the **document** level is materially safer than at the
   section level. `09_EXECUTIVE_SUMMARY` absence is genuinely tolerated
   (verified), the enterprise doc-set validator is already gated off for pack
   runs, and `synthesis_impact_mapper.py` is fully doc-name agnostic.
8. **Everything that turns the legacy contract off is keyed on
   `self._plugin_mode`, which is `plugin_pack_dir is not None` -- a filesystem
   path from a git clone** (`services/claude_service.py:1301`,
   `PluginPack.path`). An in-app authored, DB-stored user pack has no directory.
   Either the pack must be materialised to disk per run, or `_plugin_mode` must
   be decoupled from directory presence. This is a structural precondition for
   the whole program, not a detail.

---

## 1. Section consumers

### 1.1 Where the constants are defined

| Site | What |
|---|---|
| `MCP/providers/custom/prompt_packs.py:15-29` | `REQUIRED_SECTION_NAMES` -- 13 literals |
| `MCP/providers/custom/prompt_packs.py:35-39` | `CONDITIONAL_SECTION_NAMES` -- `SOURCE_SQL`, `CUSTOM_CODE`, `ESB_ROUTES_SECTION` |
| `MCP/providers/custom/prompt_packs.py:41` | `ANALYSIS_SECTION_NAMES = REQUIRED + CONDITIONAL` (16) |
| `MCP/providers/custom/constants.py:3,9,13,17` | `GROUP_SUMMARY_SECTION`, `ESB_ROUTES_SECTION`, `CROSS_JOB_FINDINGS_SECTION`, `LEGACY_CROSS_CHUNK_SECTION` |
| `MCP/providers/custom/analysis_parser.py:19,21-29` | `ADDITIONAL_FINDINGS_SECTION`; `PHASE2_SECTION_NAMES` = 16 + 4 |
| `MCP/providers/custom/harness.py:176-182` | **duplicate** `ADDITIONAL_FINDINGS_SECTION` + `PHASE2_SECTION_NAMES` |
| `MCP/providers/custom/harness.py:183` | `BASELINE_BUNDLE_SECTIONS = {GROUP_SUMMARY_SECTION}` |

Note the duplication at `harness.py:176-182`: `PHASE2_SECTION_NAMES` is
constructed twice, independently, from the same imports. Threading a contract
object into `analysis_parser.parse_phase2_sections` alone leaves `harness.py`'s
copy live at three call sites (`:213`, `:2856-2857`, `:3788`).

### 1.2 Phase 2 section consumers -- site by site

| # | Site | What it does | HARD/SOFT | Minimal contract edit |
|---|---|---|---|---|
| S1 | `MCP/providers/custom/analysis_parser.py:47` | `section_names = set(PHASE2_SECTION_NAMES) \| extra_section_names` | **HARD** | Add `contract: SectionContract \| None = None` param; when present use `set(contract.names)` instead of the union. Keep the union as the v1 path. |
| S2 | `MCP/providers/custom/harness.py:176-182` | second definition of `PHASE2_SECTION_NAMES` | **HARD** (structural) | Delete; import from `analysis_parser`, or better, replace all three uses with `contract.ordered_names`. |
| S3 | `MCP/providers/custom/harness.py:204-215` `_guide_section_names` | returns guide h3 headings **not already in** `PHASE2_SECTION_NAMES` | **HARD** under replace | Filter against `contract.names` instead of the constant. Today a pack section whose name collides with a legacy name (e.g. `RISK_FLAGS`) is dropped from extras and, under replace, is then in neither set. |
| S4 | `MCP/providers/custom/harness.py:2780-2781`, `3652-3653` | build `extra_section_names` from the scanner guide file only | **HARD** (missing seam) | Source `extra` from the compiled pack contract; keep guide-derived extras as an additive v1 fallback. |
| S5 | `MCP/providers/custom/harness.py:2855-2869` `_assemble_phase2_task_sections` | `ordered_sections = PHASE2_SECTION_NAMES + sorted(extra - set(PHASE2_SECTION_NAMES))`; skips absent sections | SOFT | Replace `PHASE2_SECTION_NAMES` with `contract.ordered_names`. Behaviourally already tolerant; ordering only. Spec correct. |
| S6 | `MCP/providers/custom/harness.py:2874-2881` | GROUP_SUMMARY fallback assembly | SOFT | none -- `GROUP_SUMMARY` is the protected floor |
| S7 | `MCP/providers/custom/harness.py:2931-2938`, `2983-2986` | group-synthesis prompt asks for exactly `CROSS_JOB_FINDINGS` + `GROUP_SUMMARY`; prepends them if missing | SOFT | none -- floor sections |
| S8 | `MCP/providers/custom/harness.py:3005-3011` `_complete_phase2_sections` | calls `deterministic_phase2_section_patch(...)`; returns early only if `missing` is empty | **HARD** | Pass the contract down (see S22) so `missing` is computed against it |
| S9 | **`MCP/providers/custom/harness.py:3019-3024`** | `still_missing = missing_required_analysis_sections(patched, platform)`; **`raise RuntimeError`** | **HARD -- NOT IN SPEC SECTION 7** | Compute `still_missing` against the contract's required set; the floor is `GROUP_SUMMARY`. Verified reachable: called from `_phase2_group_direct` (`:3315`), which runs whenever `analysis_policy == "bounded"` (pack-declared) or `run` + `unit_count <= small_project_threshold` (`pipeline/phases/phase2_analysis.py:68, 80-85`). The raise is caught by `_contain_phase2_group_failures` (`harness.py:2024-2068`, invoked at `:3056`), which writes a PARTIAL stub per group and lets the run publish. |
| S10 | `MCP/providers/custom/harness.py:3320-3329` | `_validate_phase2_task_output(..., require_sections=not self._plugin_mode)`; **`raise RuntimeError`** on failure | **HARD** | Same swap; `require_sections` becomes "validate against the contract", not a plugin-mode on/off switch |
| S11 | `MCP/providers/custom/harness.py:3491-3494` | `normalize_analysis_guide_headings(text, platform)` in targeted repair | SOFT/needs-change | Pass the contract; today pack heading variants (`### INJECTION_SINKS: notes`) are never canonicalised |
| S12 | `MCP/providers/custom/harness.py:3626` `_bundle_sections_for_docs` | `sections = set(BASELINE_BUNDLE_SECTIONS)` then adds evidence-profile-mapped or catalog-mapped sections | SOFT | Keep the baseline floor; read `docs[].phase2_sections` from the pack (see D-level note at `:704`) |
| S13 | `MCP/providers/custom/harness.py:187-200` `_BUNDLE_CATEGORY_TO_PHASE2_SECTIONS` | maps 12 evidence-profile category strings to legacy section names | **HARD** | Under replace this map is meaningless. Prefer explicit `docs[].phase2_sections`; keep the map only as the v1 compatibility path. |
| S14 | **`MCP/providers/custom/harness.py:3786-3790`** | `for section in PHASE2_SECTION_NAMES if section in section_names` -- iterates the **constant**, not the argument; `:3791-3792` falls back to dumping the whole file under `UNCLASSIFIED` | **HARD -- SPEC SAYS "no change"** | `for section in contract.ordered_names if section in section_names`. This is the doc-evidence-pack selector: get it wrong and every Phase 3 doc receives an unstructured dump. |
| S15 | `MCP/providers/custom/harness.py:3679`, `3683` | doc-pack prose: "Use the CROSS_GROUP_REFS excerpts below", "Use the RISK_FLAGS excerpts below" | SOFT (cosmetic, misleading) | Template the section names from the contract |
| S16 | **`MCP/providers/custom/harness.py:5879-5885`** `_validate_phase2_task_output` | `required = (*REQUIRED_SECTION_NAMES, GROUP_SUMMARY_SECTION) if platform == "talend" else (GROUP_SUMMARY_SECTION,)`; `_parse_phase2_sections(text)` called **without** `extra_section_names` at `:5884` | **HARD** | Take `contract` instead of `platform`; pass `contract.extra_names` into the parse. Spec cites `harness.py:5878`; the actual expression is `5879-5883` and the parse bug at `5884` is unlisted. |
| S17 | `MCP/providers/custom/phase2_claims.py:218` | `sections=ANALYSIS_SECTION_NAMES` in `plan_claim_tasks` | **HARD** | Add `sections: tuple[str, ...]` parameter. This is the single upstream source for `spec.sections`, which then correctly drives `claim_task_prompt` (`:258`), `validate_claim_batch` (`:382`), `salvage_claim_batch` (`:562`), `unknown_batch` (`:694`) and `split_claim_task` (`:230-234`). One edit fixes five sites. |
| S18 | **`MCP/providers/custom/phase2_claims.py:724`** `render_claims_as_markdown` | `for section in ANALYSIS_SECTION_NAMES:` -- emits `### SECTION` + an `UNKNOWN` stub for all 16 regardless of what was claimed | **HARD** | Add a `sections` parameter; caller `harness.py:2246-2250` passes the contract. Spec correct: this is the mechanism that forces the legacy shape. |
| S19 | `MCP/providers/custom/phase2_claims.py:752` | relationship rollup: `claim.section in {"CROSS_GROUP_REFS", "ESB_ROUTES"}` | **HARD** (silently wrong) | Drive from `contract.relationship_sections` (or from `claim.kind == "relationship"` alone). Today a pack's `TAINT_PATHS` claims never reach `CROSS_JOB_FINDINGS`. |
| S20 | `MCP/providers/custom/phase2_claims.py:136`, `154-163`, `429` | `CODE_BEARING_SECTIONS`; `code_bearing_sections_with_evidence()`; `evidenced = ... & allowed_sections` at `:429` | SOFT | The intersection with `allowed_sections` makes it inert under replace. No change required; optionally let the pack declare which of its sections are code-bearing. |
| S21 | `MCP/providers/custom/phase2_claims.py:93-114` | `_DESIGN_SECTIONS`, `_OPERATIONS_SECTIONS`, `_RELATIONSHIP_SECTIONS` | **DEAD** | Verified: defined, never referenced anywhere in `src/` or `tests/`. Delete. A grep-driven implementer will otherwise "fix" three dead tuples. |
| S22 | `MCP/providers/custom/repair_planner.py:180-199` `deterministic_phase2_section_patch` | `normalize_analysis_guide_headings(text, platform)` then `missing_required_analysis_sections(normalized, platform)`; `:187` `patchable = [n for n in missing if n == "GROUP_SUMMARY"]` (bare literal, not the constant) | **HARD** | Change signature to `(text, *, contract, group)`. This function is the direct feeder of the S9 raise. |
| S23 | `MCP/providers/custom/repair_planner.py:78` `_PHASE2_SECTION_RE` | classification regex mentions `GROUP_SUMMARY` | SOFT | none |
| S24 | `MCP/providers/custom/prompts.py:139` | `section_headings = "\n".join(f"- \`### {name}\`" for name in ANALYSIS_SECTION_NAMES)` -> `${SECTION_HEADINGS}` | **HARD** | Take the contract; render `heading + question` per section (spec section 6 already anticipates this) |
| S25 | `MCP/providers/custom/prompts.py:158` `_platform_section_depth(facts)` -> `${PLATFORM_SECTION_DEPTH}` | injects the platform section-skeleton file (see 1.4) | **HARD** | Make the slot contract-driven; render one stub per declared section from its `question` |
| S26 | `MCP/providers/custom/prompts.py:164-176` `phase2_section_repair_prompt` | takes `missing_sections` as an argument | SOFT | none -- already contract-shaped |
| S27 | `MCP/providers/custom/validators.py:592-593` | non-Talend branch: floor is `GROUP_SUMMARY` only | SOFT | This is the target behaviour; generalise "platform != talend" to "contract" |
| S28 | `MCP/providers/custom/validators.py:619-624` | Talend branch: all 13 + `GROUP_SUMMARY` | **HARD** but already platform-gated | **Spec correction:** the spec says "no platform check". Line 620 is only reached after the early return at `:580` (`if facts.platform != "talend"`) and the `require_group_summary` bypass at `:558`. It is consistent with the other three. Swap platform -> contract. |
| S29 | `MCP/providers/custom/validators.py:642-652` | `ESB_ROUTES` required when `_esb_detected(_preprocess/talend_esb_facts.json)` | SOFT | Make conditional-section enforcement contract-declared. Only fires on Talend ESB projects and lands in an advisory report. |
| S30 | `MCP/providers/custom/validators.py:698-700` `normalize_analysis_guide_headings` | `section_names = (GROUP_SUMMARY,)`, plus all 16 when Talend; regex-canonicalises heading level/case/suffix | SOFT/needs-change | Take `contract.names`. Pack headings are otherwise never canonicalised, so `_has_section` misses them. |
| S31 | `MCP/providers/custom/validators.py:711-720` `missing_required_analysis_sections` | returns `REQUIRED_SECTION_NAMES` misses + `GROUP_SUMMARY` on Talend | **HARD** | Take `contract`; this is the function behind S9's raise |
| S32 | `MCP/providers/custom/validators.py:730-736` `group_analysis_is_complete` | resume/reuse decision: `(*REQUIRED_SECTION_NAMES, GROUP_SUMMARY)` on Talend | **HARD** | Take `contract`. Mitigated for plugin runs by `require_sections=not self._plugin_mode` at `harness.py:2157-2160`, `2587-2590`, `3170-3173`; NOT mitigated at `services/true_patch_executor.py:302` (see S37) or `services/reingestion_gate.py:181`. A pack analysis judged incomplete causes silent unconditional Phase 2 re-runs on every resume -- budget burn, not a crash. |
| S33 | `MCP/providers/custom/validators.py:662-689` `_has_section` | heading matcher, name-agnostic | SOFT | none |
| S34 | `MCP/providers/custom/pipeline/phases/phase2_analysis.py:174-198` | two `validate_analysis_guide_sections(..., require_group_summary=not plugin_mode)` calls + deterministic patch between them | SOFT | Residual mismatch is a `WarningCode.PHASE2_DEPTH_PARTIAL` warning (`:205-217`), not a raise. Pass the contract through instead of the plugin-mode boolean. |
| S35 | `MCP/services/true_patch_executor.py:266`, `999`, `1165` | `parse_phase2_sections(...)` called without `extra_section_names` (3 sites) | **HARD** | Thread the contract; pack sections currently collapse into `UNCLASSIFIED_OR_EXTRA_FINDINGS` |
| S36 | `MCP/services/true_patch_executor.py:277-285` `_reassemble_analysis` | emits `PHASE2_SECTION_NAMES` in order, then any remaining names | SOFT | Spec correct: already tolerant |
| S37 | `MCP/services/true_patch_executor.py:288-307` `_analysis_text_is_valid` | `group_analysis_is_complete(tmp, platform, require_sections=True)` -- hardcoded `True` | **HARD** | The G3 living-doc gate rejects every pack-shaped analysis on Talend. Take the contract. |
| S38 | `MCP/services/true_patch_executor.py:1163-1170` `_extract_sections` | `for name in PHASE2_SECTION_NAMES if name in sections and name in parsed` | **HARD** | Iterate the contract. Pack sections are silently dropped from the Phase 3 patch excerpt. |
| S39 | `MCP/services/true_patch_executor.py:630-634` | `group_sections & set(doc_entry.phase2_sections)` where `doc_entry` comes from `_get_doc_entry` -> `load_doc_catalog()` (`:1156-1160`) -- the **default** catalog | **HARD** | Resolve doc entries from the run's active pack, not the process-wide default catalog singleton |
| S40 | `MCP/services/patch_section_map.py:43-67` | `TALEND_COMPONENT_SECTION_MAP` / `WORKATO_COMPONENT_SECTION_MAP`: component type -> legacy section names | **HARD** | Under replace the whole delta->section mapping is wrong. Either make it pack-declared or fall back to "rewrite all declared sections". |
| S41 | `MCP/services/patch_section_map.py:70` `ALWAYS_REWRITE = ("GROUP_SUMMARY",)` | floor | SOFT | none |
| S42 | `MCP/services/patch_section_map.py:97` | `comp_map.get(comp_type) or comp_map.get("_default") or ["COMPONENTS"]` | **HARD** | Fallback must be a contract section, not `COMPONENTS` |
| S43 | `MCP/services/patch_section_map.py:131-145` `docs_for_sections` | intersects against `load_doc_catalog()` entries (default catalog); skips `phase2_sections: []` | **HARD** | Same as S39. Returns doc names the pack run never generated. |
| S44 | `MCP/scripts/bundle_analysis.py:23-66` | own hardcoded 15-name `ALL_SECTIONS` + per-bundle section sets | SOFT / **runtime-copied** | Only executed by the legacy `claude_cli` provider (`providers/claude_cli.py:288`), but the file is `shutil.copy2`-ed into `_preprocess/_bundle_analysis.py` on **every** run (`services/claude_service.py:778-781`). Looks dead to a static import search; is not. Leave alone or delete with the claude_cli provider. |
| S45 | `scripts/ab_prompt_eval.py:46-55` (repo root) | dev A/B eval harness, own 10-name list | SOFT | none (dev tool) |
| S46 | `MCP/providers/opencode_cli.py:1159-1162`, `1294-1300`, `1329` | mirrors the custom-harness validator wiring for the legacy OpenCode path | SOFT/duplicate | Any contract change must be mirrored here or the legacy path diverges |

Count over the 46 rows: **26 HARD** -- S1, S2, S3, S4, S8, S9, S10, S13, S14,
S16, S17, S18, S19, S22, S24, S25, S28, S31, S32, S35, S37, S38, S39, S40, S42,
S43. **19 SOFT** -- S5, S6, S7, S11, S12, S15, S20, S23, S26, S27, S29, S30,
S33, S34, S36, S41, S44, S45, S46 (of which S11 and S30 are
"tolerant-but-should-change", and S44 is the runtime-copied file that looks dead
to a static search). **1 DEAD** -- S21.

Of the 26 HARD, two (S28 `validators.py:619-624`, S32
`validators.py:730-736`) are already platform-gated and so are the ones the spec
partially anticipated; the other 24 are unguarded on the pack path.

Six prompt-asset files add a further HARD layer that is not code -- see 1.4.

### 1.3 HARD sites the spec did not list

These are the answer to "explicitly call out any HARD site I did not list",
ordered by damage.

| Rank | Site | Failure under `mode: replace` |
|---|---|---|
| 1 | `harness.py:3019-3024` (S9) | `RuntimeError` per group -> `_contain_phase2_group_failures` -> **every group becomes a PARTIAL stub, run publishes stubs and reports success** |
| 2 | `harness.py:3786-3790` (S14) | Doc-evidence packs contain an unstructured dump instead of the pack's sections; spec explicitly marks this "no change" |
| 3 | `harness.py:5879-5885` (S16) | Every Phase 2 task output rejected on Talend when `require_sections` is on; the `_parse_phase2_sections` call at `:5884` also omits `extra_section_names` |
| 4 | `harness.py:3320-3329` (S10) | `RuntimeError` on the direct/bounded path -- the same path a small pack demo will use |
| 5 | `harness.py:204-215` (S3) | Pack sections colliding with legacy names vanish from both the extras set and the replace contract |
| 6 | `repair_planner.py:180-199` (S22) | Feeder for #1; also hardcodes the bare string `"GROUP_SUMMARY"` at `:187` |
| 7 | `true_patch_executor.py:302, 1163-1170, 630-634` (S37, S38, S39) | Living-document reingestion is fully legacy-shaped: G3 gate rejects, section excerpt drops pack sections, doc routing reads the default catalog |
| 8 | `patch_section_map.py:43-67, 97, 131-145` (S40, S42, S43) | Same subsystem; delta->section and section->doc mapping both legacy-only |
| 9 | `phase2_claims.py:752` (S19) | Pack relationship claims never reach `CROSS_JOB_FINDINGS` |
| 10 | `harness.py:187-200` (S13) | `evidence_profiles` -> section mapping is dead weight under replace and silently narrows bundles |

### 1.4 Prompt-asset consumers (the layer the spec does not model)

The section contract is asserted in prompt text, not just code. None of these
files is pack-overridable today.

| Site | What | Override path |
|---|---|---|
| `DATA/prompt_templates/phase2_analysis.md:14` | "Every unit listed here MUST appear at least once in the **COMPONENTS** section" | none -- only `${PREAMBLE}` is pack-overridable (`prompts.py:140-144` -> `_resolve_preamble` at `prompts.py:60-74`, reading `{pack}/prompts/phase2.md`) |
| `DATA/prompt_templates/phase2_analysis.md:47-48` | `${SECTION_HEADINGS}` + a literal `- \`### GROUP_SUMMARY\`` | slot is contract-drivable (S24) |
| `DATA/prompt_templates/phase2_analysis.md:52-61` | `${PLATFORM_SECTION_DEPTH}` "OUTPUT SKELETON" + a literal `### GROUP_SUMMARY` block | slot is contract-drivable (S25) |
| `DATA/prompt_templates/phase2_platform_sections/talend.md` | **16** `### SECTION` stubs (lines 6, 18, 27, 48, 68, 77, 99, 108, 116, 124, 139, 155, 168, 179, 187, 194) | **none.** `PromptRenderer.render_platform_section_depth` (`MCP/providers/custom/prompt_renderer.py:41-48`) resolves `{platform}.md` then `generic.md` under the fixed template root. There is no plugin-pack branch anywhere in `prompt_renderer.py`. |
| `DATA/prompt_templates/phase2_platform_sections/{workato,lobster,generic}.md` | 15 stubs each | none, same |
| `INTODOC/platforms/talend/prompts/ANALYSIS_GUIDE.md:56-315` | 15 `### SECTION` sections, described to the model as "the authoritative reference" | **none, and it lives in the scanner package.** Read by `MCP/services/scanner_service.py:966-968`, written to `_preprocess/_analysis_guide.md` by `services/claude_service.py:1399-1401`, materialised into `_assessment/prompt_packs/_analysis_prompt_pack.md` by `providers/custom/prompt_packs.py:66-67,103-108`, and injected as Phase 2 shared context at `harness.py:6328-6330` -- **including in `bounded` mode** (`harness.py:6331-6338`). |
| `INTODOC/platforms/{workato,lobster,generic,_shared}/prompts/ANALYSIS_GUIDE.md` | 15-16 sections each | none, same |

Consequence: a `mode: replace` pack asking four questions will still receive, in
the same prompt, a 300-line authoritative guide demanding fifteen different
sections. That is not a validator problem -- it is an attention problem, exactly
the one section 1 of the spec says the program exists to fix. **This must be
solved for acceptance test 1 to produce clean output, and it cannot be solved
without either (a) making the guide/skeleton injection contract-conditional in
`harness.py:6316-6338` + `prompts.py:158`, or (b) touching files inside
`intodoc/`.** Option (a) is the correct fix and stays within the non-goals.

### 1.5 Is there a single place to thread a section contract?

**No. Four independent call paths, plus a fifth for living docs.** The minimum
set of signatures that must change:

Path A -- parse/assemble (Phase 2 output handling):

```
analysis_parser.parse_phase2_sections(text, extra_section_names)               # :32
  -> add contract param                                                        # :47 is the pivot
CustomHarnessProvider._parse_phase2_sections(text, extra_section_names)        # harness.py:2884
CustomHarnessProvider._assemble_phase2_task_sections(group, tasks, outputs, extra)  # harness.py:2828
harness._guide_section_names(guide_path)                                       # harness.py:203
```

Path B -- prompt construction:

```
prompts.group_analysis_prompt(...)                                            # prompts.py:~85
prompts._platform_section_depth(facts)                                        # prompts.py:55
PromptRenderer.render_platform_section_depth(platform)                        # prompt_renderer.py:41
phase2_claims.plan_claim_tasks(task, *, evidence_bytes, ...)                  # phase2_claims.py:194
phase2_claims.claim_task_prompt(*, facts, group, spec)                        # phase2_claims.py:246  (no change: reads spec.sections)
phase2_claims.render_claims_as_markdown(*, group, unit_names, batches)        # phase2_claims.py:714
```

Path C -- validation:

```
validators.normalize_analysis_guide_headings(text, platform)                  # :692
validators.missing_required_analysis_sections(text, platform)                 # :711
validators.group_analysis_is_complete(path, platform, *, require_sections)    # :730
validators.validate_analysis_guide_sections(dir, facts, *, require_group_summary)  # :546
repair_planner.deterministic_phase2_section_patch(text, *, platform, group)   # :180
CustomHarnessProvider._validate_phase2_task_output(text, units, *, platform, require_sections)  # harness.py:5861
CustomHarnessProvider._complete_phase2_sections(...)                          # harness.py:2990
```

Path D -- Phase 2.8 bundle routing:

```
CustomHarnessProvider._bundle_sections_for_docs(doc_names)                    # harness.py:3625
CustomHarnessProvider._targeted_analysis_bundle_parts(files, section_names, extra)  # harness.py:3775
CustomHarnessProvider._targeted_analysis_bundle_text(...)                     # harness.py:3768
```

Path E -- living-document reingestion (independent subsystem):

```
true_patch_executor._reassemble_analysis(preamble, sections)                  # :270  (no change)
true_patch_executor._analysis_text_is_valid(text, platform)                   # :288
TruePatchExecutor._extract_sections(analysis_text, sections)                  # :1162
TruePatchExecutor._get_doc_entry(doc_name)                                    # :1156
patch_section_map.sections_for_delta(platform, field_changes)                 # :83
patch_section_map.docs_for_sections(sections)                                 # :131
```

The recurring pattern in paths C, D and E is `platform: str` as the discriminator.
The cleanest single change is: **replace every `platform: str` parameter in these
signatures with `contract: SectionContract`**, where the contract carries
`ordered_names`, `required_names`, `conditional_names`, `floor_names`
(`{GROUP_SUMMARY}` always, protected), and `questions: dict[str, str]`.
`platform` then becomes one input to the *compiler* that builds the contract, not
a runtime discriminator scattered across 12 functions. That also fixes the
existing smell that `validators.py` takes `platform` in four places and derives
the same tuple four times.

A pragmatic sequencing note: paths A-D are the Phase 2/3 generation arc and must
change together. Path E (living docs) is only exercised by webhook reingestion
and can be gated -- reject pack-scoped reingestion with an explicit error until
it is ported, rather than shipping it silently wrong.

---

## 2. Document-name consumers

### 2.1 Verification of the spec's two claims

Both confirmed.

| Claim | Verdict |
|---|---|
| `harness.py:1601` already drops `09_EXECUTIVE_SUMMARY` conditionally | **Confirmed.** `if not ctx.enterprise_mode or self._plugin_mode: self._required_docs = [d for d in self._required_docs if d != "09_EXECUTIVE_SUMMARY"]` |
| `harness.py:4010-4011` handles its absence | **Confirmed**, at `:4010-4013` -- `has_executive_summary = executive_summary_doc in self._required_docs`, and the write is additionally gated at `:4035` (`if has_executive_summary and not self._plugin_mode`). Tolerant on both axes. |

### 2.2 Document-name sites

| # | Site | What it does | Verdict |
|---|---|---|---|
| D1 | `MCP/providers/custom/harness.py:699-707` | pack docs replace `_required_docs`, `_doc_assignments`, `_doc_bundle_map`, `_doc_section_requirements_extra` | TOLERANT (spec correct: "none, replaces today") |
| D2 | **`MCP/providers/custom/harness.py:704`** | `self._doc_section_bundle_map = {}  # pack runs: no Phase 2 section routing` | NEEDS-CHANGE (spec correct). Note `_expand_per_job_docs` (`:6249-6255`) also never repopulates it, so the blank persists through per-unit expansion. |
| D3 | `MCP/providers/custom/harness.py:1601` | drops `09_EXECUTIVE_SUMMARY` for non-enterprise or pack runs | TOLERANT |
| D4 | `MCP/providers/custom/harness.py:4010-4013`, `4035` | executive-summary dispatch gated on presence and `not plugin_mode` | TOLERANT |
| D5 | `MCP/providers/custom/harness.py:267` `_SYNTHESIS_DOCS` | `{"09_EXECUTIVE_SUMMARY.md", "07_QUALITY_SECURITY_RISK_SCORECARD.md"}` -- these cite sibling docs in their Evidence Register instead of scanner paths | TOLERANT (set membership; absent names simply never match). Pack synthesis docs get scanner-path expectations -- a quality wobble, not a break. |
| D6 | `MCP/providers/custom/harness.py:1296-1305`, `1321-1332` | output-token ceilings keyed on `00_AGENT_BRIEF` / `09_EXECUTIVE_SUMMARY` / `02_COMPONENT_INVENTORY` / `04_BUSINESS_RULES_AND_TRANSFORMATIONS`, else `config.phase3_max_tokens` | TOLERANT (falls through). Budget-quality issue only: a pack's per-unit doc still gets the `_per_job_doc_units` branch at `:1295`, which is correct. |
| D7 | `MCP/providers/custom/harness.py:3741` | `doc_targets.get("units", ["02_COMPONENT_INVENTORY", "00_AGENT_BRIEF", "08_CHANGE_PLAYBOOK"])` -- obligation default | NEEDS-CHANGE (mild): the default names docs a pack run does not have. `build_documentation_obligation_map(facts, required_docs=...)` already remaps when `required_docs is not None` (`validators.py:844-846`), so pass it here. |
| D8 | `MCP/providers/custom/harness.py:4210-4216` | `if doc_name == "02_COMPONENT_INVENTORY" and "## Canonical Unit Inventory" not in content:` appends the unit catalog | TOLERANT |
| D9 | `MCP/providers/custom/harness.py:6498-6508` | `09_EXECUTIVE_SUMMARY` context = scorecard + all other completed docs | TOLERANT |
| D10 | `MCP/providers/custom/harness.py:6720-6750` | engineering-fact artifact selection keyed on 6 doc names, with an `else` branch | TOLERANT (falls through to a generic set) |
| D11 | `MCP/providers/custom/harness.py:3839-3872` `_apply_private_analysis_packs` | appends `PRIVATE_ANALYSIS__{pack}__{output}`; raises only on a name collision (`:3858-3861`); sets `_doc_section_bundle_map[doc] = set()` (`:3866`) | TOLERANT, and confirms the spec's diagnosis: append-only, prior phases untouched |
| D12 | **`MCP/providers/custom/validators.py:22`** | `REQUIRED_DOCS = [d.name for d in _catalog.docs if not d.per_unit]` -- module-level, from the **default** catalog | NEEDS-CHANGE (structural). Every validator defaults to it. |
| D13 | `MCP/providers/custom/validators.py:747-750` `validate_docs` | `docs_to_validate = required_docs or REQUIRED_DOCS`; missing docs -> error | TOLERANT (caller passes `required_docs`) |
| D14 | `MCP/providers/custom/validators.py:753` | "Missing required scorecard" when `_assessment/scorecard.md` < 50 bytes | TOLERANT for packs (`enforce_default_contract`). Note the omitted-scorecard marker (`harness.py:1732`) exists precisely to satisfy this. |
| D15 | **`MCP/providers/custom/validators.py:893-946`** `_assistant_doc_contract_errors` -- **the enterprise doc-set validator** | iterates `REQUIRED_DOCS` (the constant, `:896`, ignoring the `required_docs` argument entirely) and hard-requires named H2 terms in 8 specific documents | TOLERANT *only because* the call site at `:787-790` is wrapped in `if enforce_default_contract:`. **The function itself is doc-name-blind to overrides.** If a v2 default pack ever runs with `enforce_default_contract=True` and renamed docs, this fires. Take `required_docs` and drive the term map from the pack. |
| D16 | `MCP/providers/custom/validators.py:962-1015` `_doc_evidence_pack_errors` | requires `_analysis/_doc_pack_{doc}.md` per doc and 9 named `##` headings inside each | TOLERANT for packs (gated at `:792`). Note the 9 headings are the ones `harness.py:3665-3697` writes, so this is self-consistent. |
| D17 | `MCP/providers/custom/validators.py:1037-1049` `validate_doc_depth` | `DOC_SECTION_REQUIREMENTS.get(doc, [])` (default catalog) when `enforce_default_contract`; `min_doc_bytes` drops to 50 otherwise | TOLERANT |
| D18 | `MCP/providers/custom/validators.py:1467-1484` `validate_source_provenance` | `docs.get("02_COMPONENT_INVENTORY")` for unit-path coverage; `inventory_name` fallback literal at `:1479` | TOLERANT (after the `enforce_default_contract` early return at `:1458-1466`) |
| D19 | `MCP/providers/custom/validators.py:834-842` | obligation `doc_targets` map of legacy doc names | TOLERANT -- overridden wholesale when `required_docs is not None` (`:844-846`) |
| D20 | `MCP/providers/custom/validators.py:908-946` | the 8 `require_terms(...)` blocks | see D15 |
| D21 | `MCP/providers/custom/doc_catalog.py:87-100` `load_doc_catalog` | **process-wide mutable singleton**, no pack-override parameter in production use | NEEDS-CHANGE (structural). `knowledge_base/CLAUDE.md` claims "Plugin packs can override this with their own `doc_catalog.yaml` at the pack root". **That is false in the current code** -- there is no such code path anywhere. Packs override docs only via `PluginPackManifest.docs` and the reassignment at `harness.py:699-707`. Every other `load_doc_catalog()` caller reads the default. |
| D22 | `MCP/providers/custom/prompts.py:145` | `effective_docs = required_docs or load_doc_catalog().required_docs` -> `${DOC_COUNT}` / `${DOC_LIST}` in the Phase 2 prompt | TOLERANT (caller passes) but verify the caller always does |
| D23 | `MCP/providers/custom/prompts.py:263`, `346`, `350` | `section_requirements` / `doc_specific_instructions_for(doc_name)` from the default catalog | TOLERANT (empty string / empty list for unknown names) |
| D24 | `MCP/providers/custom/prompts.py:238-242` | doc-specific artifact-priority prose naming 4 legacy docs | TOLERANT (cosmetic) |
| D25 | `MCP/providers/custom/prompts.py:351-353` | `if doc_name == "00_AGENT_BRIEF"` / `== "01_ARCHITECTURE_MAP"` special-casing | TOLERANT |
| D26 | `MCP/providers/custom/prompts.py:336-342` `_doc_specific_from_pack` | `{pack}/prompts/docs/{doc_name}.md`, with a `__`-stripped retry | TOLERANT and already pack-aware -- the one existing pack-override seam for doc prompts |
| D27 | **`MCP/services/doc_quality_scorer.py:39`** | `_PUBLISHABLE_DOC_KEYS = frozenset(load_doc_catalog().required_docs)` evaluated at **module import**; `:409-411` filters candidate files by it; `:413-418` returns `overall=0.0, docs=[]` when nothing matches | NEEDS-CHANGE. A pack producing `INJECTION_FINDINGS.md` scores **0.0 with an empty doc list**. Called from `harness.py:5362`, `api/routes/projects.py:2160`, `services/partition_orchestrator.py:496`. **Spec section 9's "preview-and-score before activation" gate depends on this function**, so this is not cosmetic -- it is a prerequisite for the substitute control the program relies on. |
| D28 | `MCP/services/doc_quality_scorer.py:29-37`, `58-120` | `_BASE_MIN_LINES_AT_50` and `_EXPECTED_SECTIONS` keyed on legacy doc names | TOLERANT once D27 is fixed (`.get(key, 500)` at `:52`) |
| D29 | **`MCP/services/patch_section_map.py:131-145`** `docs_for_sections` | returns default-catalog doc names | NEEDS-CHANGE (also S43) |
| D30 | **`MCP/services/synthesis_impact_mapper.py`** | **no doc name appears in executable code at all** -- `09_EXECUTIVE_SUMMARY` occurs only in the module docstring at `:6`. The mapper works off the document's own `## Heading` structure via `extract_h2_sections` (`patch_section_map.py:112-128`) | **TOLERANT.** Spec listed this as "NOT yet verified"; it is verified and needs no change. |
| D31 | `MCP/services/true_patch_executor.py:1156-1160` `_get_doc_entry` | default catalog lookup by name -> returns `None` for pack docs -> `continue` at `:620-621` | NEEDS-CHANGE (silently skips every pack doc; see S39) |
| D32 | `MCP/services/assessment_service.py:132-138` | hard-requires `docs/00_AGENT_BRIEF.md`; returns `{"status": "no_docs", "error": "00_AGENT_BRIEF.md not found"}` | NEEDS-CHANGE (graceful-degrade). Optional `07_QUALITY_SECURITY_RISK_SCORECARD.md` at `:150-158` is already tolerant. Reached from `api/routes/projects.py:3299-3308` (the HTML assessment view) -- a user-visible dead end for pack projects, not a pipeline break. |
| D33 | `MCP/services/component_description_service.py:65-70` | resolves `docs/02_COMPONENT_INVENTORY.md` | NEEDS-CHANGE (graceful-degrade); component-description feature only |
| D34 | `MCP/api/routes/projects.py:3496-3506` | `inventory_path = docs_dir / "02_COMPONENT_INVENTORY.md"`, returns `None` if absent | TOLERANT (documented `or None`) |
| D35 | `MCP/services/git_diff_patcher.py:795-816`, `828-834` | diff-path heuristics choosing among 6 legacy doc filenames; `_fallback_section_for_doc` requires `path.name[0].isdigit()` at `:820` | NEEDS-CHANGE (silently wrong): pack doc names without a numeric prefix are excluded by construction |
| D36 | `MCP/providers/custom/evidence_agents.py:44-80` `_CATEGORY_TO_DOCS` | evidence category -> legacy doc names, for `_evidence_registry/doc_slices/` | NEEDS-CHANGE (mild). Default evidence agents are off by default (`MCP_DOCGEN_DEFAULT_EVIDENCE_AGENTS_ENABLED`), but a foundation pack declaring `evidence_agents` hits it. |
| D37 | `MCP/services/claude_service.py:711-721`, `MCP/services/scanner_service.py:1051-1061` | legacy 9/10-name doc lists in scanner-prompt assembly | TOLERANT (prose) |
| D38 | `MCP/providers/claude_cli.py:97-99`, `122-134`, `361`, `377-380` | legacy provider: "always generate exactly the 9 assistant-grade docs" | out of scope (legacy claude_cli path) |
| D39 | `MCP/providers/opencode_cli.py:1483` | `executive = "09_EXECUTIVE_SUMMARY"` | out of scope (legacy path), mirror if kept |
| D40 | `MCP/services/partition_orchestrator.py:429-432` `_count_public_docs` | counts non-underscore top-level `*.md` | TOLERANT (name-agnostic; good pattern to copy) |
| D41 | `MCP/api/routes/projects.py:1996` | doc-count gate on top-level `*.md` | TOLERANT |
| D42 | `MCP/providers/custom/partition_planner.py:178-180` | `load_doc_catalog()` for the per-partition call estimate | NEEDS-CHANGE (mild): a pack with more/fewer docs is mis-estimated, which feeds the budget planner |
| D43 | `MCP/providers/custom/doc_slice_merge.py:8` | doc name in a comment | TOLERANT |
| D44 | `MCP/api/routes/generate_runs.py:189` | docstring example | TOLERANT |

### 2.3 RAG-side and push-path document-name dependencies

**Headline: `RAG_pipeline/` is doc-name agnostic.** A repo-wide grep for all 12
generated doc names across `RAG_pipeline/backend/src/` returns zero hits, as does
a grep for all 18 Phase 2 section names (only false positives: a
`// ===== SHARED COMPONENTS =====` comment and `metadata.external_systems`, a
topology JSON field). Doc/section names are correctly isolated as a
**generator-side** contract.

Verified tolerant:

| Site | Why tolerant |
|---|---|
| `RAG/ingestion/markdown_chunker.py:31, 93-154, 157-220` | Generic h1-h4 heading-tree parser; `section` = heading path, `unit` = deepest heading. No allowlist. Falls back to `chunk_text_semantic` for heading-less docs. |
| `RAG/ingestion/pipeline.py:45-65, 185, 192, 202, 298, 305` | Routes on file type; stamps `source_layer`/`artifact_type`/`document_role` from caller metadata, never from the filename |
| `RAG/document_roles.py:43-49` | Role from request metadata; blank defaults to `source` |
| `RAG/retrieval/search.py:121-142` + `RAG/config.py:151` | Rung-1 filter is `MatchAny(["canonical_docs"]) OR IsEmpty(source_layer)` -- keyed on **layer**, not filename |
| `RAG/retrieval/search.py:197-222` | `_LAYER_DISPLAY_LABELS` keyed on layer names, `.get(layer, "Internal Analysis")` fallback at `:220`; `friendly_display_name` parses path shape only |
| `RAG/retrieval/search.py:695-800` | `unit` exact-match boosting and `(document_id, section)` expansion compare retrieved payload values; sources without `section` pass through (`:778-783`) |
| `RAG/harness/providers/mcp_docs.py:35` | Generic `^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.md$` filename validation |
| `RAG_pipeline/frontend/components/DocList.tsx:95-143`, `app/api/docs/[projectId]/[filename]/route.ts:13,38` | Fully data-driven from `/api/docs/{projectId}`; only the extension is validated |
| `MCP/services/rag_support_package.py:376-405` | Layered-RAG layer assignment is by **directory**, not doc name |

Two incidental findings worth registering:

- `RAG/retrieval/search.py:831-853` `search_layered` (rung 2) has **no
  production caller** -- only `RAG_pipeline/backend/scripts/retrieval_eval.py:370`
  and tests. Deep search is not wired into the live query path.
- `MCP/services/rag_support_package.py:391` hardcodes
  `_assessment/scorecard.md`. Renaming that file silently drops the entire
  `scorecard` retrieval layer. Not a pack concern, but a latent trap.

Sites that **do** need change:

| # | Site | Verdict |
|---|---|---|
| D45 | `MCP/services/rag_bridge.py:514-519` `_read_md_files` | `is_numbered = name[0].isdigit()`; non-numbered files are skipped unless in `extra_filenames`. **The `NN_` prefix is load-bearing for the RAG push, not the individual names.** Verified: the primary generation path compensates -- `api/routes/projects.py:2098-2103` globs non-numeric, non-underscore `*.md` and passes them as `extra_doc_names`, and `:2361-2364` does the same for the manual re-push. `services/partition_orchestrator.py:520-536` builds an explicit `docs=` payload and bypasses the gate. **But five secondary push paths pass `output_dir` (or `filenames`) with no `extra_doc_names`: `services/true_patch_executor.py:831`, `services/git_diff_patcher.py:540`, `services/evidence_doc_update.py:147`, `services/evidence_doc_revert.py:107`, `mcp/server.py:504`.** On those paths a pack doc named `INJECTION_FINDINGS.md` is dropped by the digit gate before `filename_filter` is applied -- a **silent no-op push**, no error, no log. NEEDS-CHANGE: move the pack-doc allowlist inside `_read_md_files` (glob non-underscore `*.md`) instead of requiring six callers to remember. |
| D46 | `MCP/services/partition_orchestrator.py:66, 134, 587-595` | `CROSS_PROJECT_TOPOLOGY_DOC = "CROSS_PROJECT_TOPOLOGY.md"` -- the only one of the 12 names that is a hardcoded push-path constant. Single constant, low effort. TOLERANT for packs (packs do not produce it). |
| D47 | `knowledge_base/dashboard/src/components/HelpPanel.tsx:4-69` `DOC_TYPES` | Static list of "12 intodoc output document types" `00`-`11`, mounted at `src/App.tsx:260`. **Already wrong today**: it lists `01 Integration Catalogue`, `03 Technical Specifications`, `05 Data Dictionary & Lineage`, `08 Security & Compliance`, `09 Performance Assessment`, `10 Risk Assessment`, `11 Migration Assessment` -- none of which exist in `doc_catalog.yaml`. Its own comment admits there is no live data source. Informational rot; make it read the pack. |

Test/eval fixtures referencing doc names (low priority, 14 files, all in
`RAG_pipeline/`): `backend/scripts/eval_reports/layered_rag_slice6.json` (38
lines), `.../layered_rag_slice6.md` (8), `backend/scripts/eval_fixtures/koopmans.json`
(9), `.../layered_rag_phase2.json` (4),
`frontend/tests/fixtures/werkorder_org_business_topology.json` (73, mostly
`external_systems` keys), and 9 backend test files
(`test_harness/test_evidence_store.py` 9, `test_ingestion/test_markdown_chunker.py`
6, `test_harness/test_agentic_retrieval_capabilities.py` 5,
`test_retrieval/test_search.py` 4, `test_harness/test_mcp_docs_provider.py` 3,
`test_harness/test_readonly_runtime.py` 2, `test_scripts/test_retrieval_eval.py`
2, `test_harness/test_grounding.py` 1, `test_harness/test_semantic_tools.py` 1).
`knowledge_base/dashboard/` has zero fixture references.

### 2.4 Where doc-name coupling actually concentrates

Hit counts for the 12 doc names in `MCP` production source -- the files a rename
forces you to touch: `data/doc_catalog.yaml` 11 (source of truth),
`providers/custom/harness.py` 25, `providers/custom/evidence_agents.py` 24,
`providers/custom/validators.py` 19, `services/doc_quality_scorer.py` 19,
`services/claude_service.py` 16, `services/git_diff_patcher.py` 12,
`providers/claude_cli.py` 11, `providers/custom/prompts.py` 7,
`services/scanner_service.py` 7, `services/assessment_service.py` 5,
`services/partition_orchestrator.py` 5, `api/routes/projects.py` 4,
`services/component_description_service.py` 4,
`services/true_patch_executor.py` 2, plus one each in `config.py`, `schemas.py`,
`api/routes/generate_runs.py`, `providers/opencode_cli.py`,
`providers/custom/doc_slice_merge.py`, `providers/custom/noncode_evidence.py`,
`services/patch_section_map.py`, `services/synthesis_impact_mapper.py`.

### 2.5 Default document set (for acceptance test 2)

`DATA/doc_catalog.yaml` declares **11** entries, one of which is `per_unit`:

`00_AGENT_BRIEF` (:4), `01_ARCHITECTURE_MAP` (:27), `02_COMPONENT_INVENTORY`
(:53), `03_DATA_FLOWS_AND_LINEAGE` (:82), `04_BUSINESS_RULES_AND_TRANSFORMATIONS`
(:103), `05_RUNTIME_CONFIG_AND_EXTERNALS` (:125),
`06_OPERATIONS_TESTING_AND_DEBUGGING` (:142),
`07_QUALITY_SECURITY_RISK_SCORECARD` (:162), `08_CHANGE_PLAYBOOK` (:185),
`10_UNIT_REFERENCE` (:206, `per_unit: true`, `phase2_sections: []`),
`09_EXECUTIVE_SUMMARY` (:237, `phase2_sections: []`).

So "same 10 documents" in acceptance test 2 means: 9 always + `09_EXECUTIVE_SUMMARY`
when `enterprise_mode` and not a pack run, plus N expanded `10_UNIT_REFERENCE__*`
files when `MCP_DOCGEN_UNIT_DOCS_ENABLED=1` (default off). Pin the acceptance
assertion to that conditional shape, not to a flat count of 10.

---

## 3. Pack surface inventory and deletion order

Spec anchor: `docs/one-pack-program.md:322-330` (the five-row table),
`:332-342` ("Keep and repoint"), `:347-387` (distribution model).

**Migration range confirmed.** Spec section 12 cites `059`-`074`. Pack DDL lives
in `059`, `061`, `062`, `063`, `064`, `065`, `074`, plus an incidental data touch
in `067`. Nothing pack-related in `060`, `066`, `068`-`073`. Concepts 1 and 5
predate that range (`010`, `014`, `016`, `021`).

### 3.1 Concept 1 -- plugin pack (`plugin.yaml`) -- KEEP

Python source (`MCP` = `knowledge_base/mcp-server/src/mcp_server`):

| File | Role |
|---|---|
| `MCP/services/plugin_schema.py` | The manifest schema. `PluginPackManifest` `:153`, `PluginStep` `:84`, `PhaseConfig` `:99`, `PhasePolicy` `:107`, `QualityGates` `:76`, `DocDefinition` `:25`, `EvidenceProfile` `:41`, `EvidenceAgentDefinition` `:46`, `ValidationPolicy` `:71`, `PackCertification` `:147`, `load_plugin_manifest()` `:176` |
| `MCP/api/routes/plugins.py` | CRUD + Git install + file editor; `APIRouter(tags=["plugins"])` at `:55` |
| `MCP/services/pack_governance.py` | Certification/provenance/tree digest; singleton `pack_governance` at `:311` |
| `MCP/services/plugin_runner.py` | `PluginRunnerProvider` `:23` executes `steps[]`; `build_plugin_runner()` `:157` |
| `MCP/services/claude_service.py` | Resolves the effective pack `:1250-1258`, re-checks governance `:1282`, builds the provider `:1453-1458`, sets `plugin_pack_dir = Path(pack_row.path)` `:1301` |
| `MCP/services/git_poller.py` | Auto-update loop; `PluginPack` `:154`, `load_plugin_manifest` `:182` |
| `MCP/api/routes/webhook.py` | Push-driven refresh; local imports `:224`, `:268-269` |
| `MCP/api/routes/projects.py` | Per-run override `:1099-1101`, `:1124`, `:1861`, `:1959`; governance recheck `:1110` |
| `MCP/providers/custom/harness.py` | Manifest consumer; load `:686-689`; prompt overrides `:3209-3210`, `:6162-6256` |
| `MCP/providers/custom/prompts.py` | Pack prompt override resolution `:141`, `:211-214`, `:266-307`, `_examples_block` `:323`, `_doc_specific_from_pack` `:336` |
| `MCP/providers/custom/evidence_agents.py` | `:287`, `:293` -- `evidence_agents` / `evidence_profiles` via `getattr` |
| `MCP/providers/custom/evidence_bundles.py` | `:55`, `:83-90` -- same |
| `MCP/providers/custom/validators.py` | `:1107-1111` -- pack docs/gates via `getattr` |
| `MCP/providers/custom/pipeline/helpers.py` | `:52`, `:125-126` -- exposes `plugin_pack_dir` to phases |
| `MCP/providers/custom/pipeline/phases/phase1_investigation.py` | `:61-62` -- pack `prompts/investigation.md` |
| `MCP/providers/custom/pipeline/phases/phase26_scorecard.py` | `:63` |
| `MCP/providers/__init__.py` | `:16-17`, `:60`, `:78-79` -- factory threading |
| `MCP/db/models.py` | `PluginPack` ORM `:236-263`; `projects.plugin_pack_id` FK `:80-82` |
| `MCP/config.py` | `pack_governance_trusted_sources_json` `:68`; `swap_pack_starter_repo_url` `:57` |

Pack content directories:

- `knowledge_base/mcp-server/plugin_packs/` -- 5 git-tracked reference packs
  (`integration_assessor`, `quick_summary`, `table_inventory`,
  `vw_reverse_engineering`, `webhook_smoke_test`). **Not in the Docker image**
  (`knowledge_base/mcp-server/Dockerfile:45-49` copies only `pyproject.toml`,
  `src/`, `alembic/`, `alembic.ini`, `data/`). Dev/test only.
- `knowledge_base/mcp-server/data/plugin_packs/` -- 45 git-tracked files, 8
  directories; the **runtime** install root computed at `plugins.py:268` as
  `config.data_dir / "plugin_packs" / f"{repo}__{branch}__{digest}"`.
- `knowledge_base/mcp-server/data/swap_pack_starter/` -- starter skeleton,
  path-resolved at `plugins.py:186-187`.
- `knowledge_base/pack-repos/table-inventory-pack/` + `.zip` -- git-tracked
  sample pack repo.

DB: `plugin_packs` created `alembic/versions/014_add_plugin_packs.py:20-29`;
`manifest_name`+`repo_branch` `016_add_plugin_pack_branch_metadata.py:22-26`;
`origin`,`certification_status`,`certification_provenance`,`content_sha256`
`021_add_plugin_pack_governance.py:19-24`. Column `projects.plugin_pack_id`
`014:31-40`.

API (all `MCP/api/routes/plugins.py`): `GET /plugins/starter` `:461`;
`GET /plugins/starter.zip` `:481`; `POST /plugins/discover` `:491`;
`POST /plugins/branch-files` `:519`; `POST /plugins/branch-file` `:548`;
`POST` (upload variant) `:588`; `POST /plugins/upload` `:601`; `GET /plugins`
`:608`; `POST /plugins` `:615`; `PATCH /plugins/{pack_id}` `:698`;
`DELETE /plugins/{pack_id}` `:750`; `GET /plugins/{pack_id}/files` `:756`;
`GET|PUT /plugins/{pack_id}/files/{file_path:path}` `:773`, `:789`;
`PATCH /projects/{project_id}/plugin` `:851`.

Frontend: `knowledge_base/dashboard/src/components/PluginPacksPage.tsx`,
imported at `src/App.tsx:15` (mounted `:263`) and
`src/tests/ep000-ui.test.tsx:7`. Hooks `src/hooks/useProjects.ts:159` (`usePluginPacks`),
`:175`, `:236`, `:245`, `:254`, `:263`. Client `src/api/client.ts:239-335`
(`/mcp/plugins*`). The RAG side consumes plugin packs only as an integer id
(`RAG_pipeline/frontend/components/harness-packs/DocumentationPackSelector.tsx`,
`PackManager.tsx:171`).

Tests: `tests/test_api/{test_plugins,test_webhook,test_projects,test_rbac,test_generate_request_schema}.py`,
`tests/test_services/{test_plugin_runner,test_pack_governance,test_claude_service,test_git_poller}.py`,
`tests/test_providers/{test_custom_provider,test_evidence_agents,test_sql_usage}.py`,
`tests/test_pipeline/test_phase_failures.py`.

### 3.2 Concept 2 -- Nexus Pack v2 (`PackManifest` JSON)

Python source (`RAGB` = `RAG_pipeline/backend/src`):

| File | Role |
|---|---|
| `RAGB/harness/packs/__init__.py` | Re-export facade; `FIXTURE_PACK_DIRECTORY` `:19` |
| `RAGB/harness/packs/models.py` | ~50 pydantic types. `PackType` `:20`, `PackPhase` `:26`, `PackOwner` `:34`, `PackAuthority` `:41`, `PackScope` `:48`, `PackMutability` `:55`, `PackTrust` `:60`, `PackGovernance` `:132`, `PackManifest` `:326`, `PackSnapshot` `:405`, `PackStackSelection` `:416`, `CompiledPackStack` `:440`, `PackView` `:446`, `ProjectPackActivation` `:467` |
| `RAGB/harness/packs/loader.py` | JSON-only loader; rejects symlinks `:35-37` |
| `RAGB/harness/packs/catalog.py` | `PackCatalog` `:32`, `from_directory` `:52`, `resolve` `:82`, `manifest_hash` `:26`, include-cycle validation `:122-146`, reference whitelist `:148` |
| `RAGB/harness/packs/composition.py` | `PackStackCompiler` `:36` -- the foundation/functional/style layer engine. Layer ordering `:153-155`, presentation compile `:281`, permitted-style enforcement `:319`, `_canonical_hash` `:399` |
| `RAGB/harness/packs/runtime.py` | The execution seam. `active_pack_ids()` `:26`, `pack_directory()` `:31`, `build_pack_catalog` `:35`, `compile_active_pack_registry` `:68`, `compile_governed_pack_registry` `:89`, `governed_pack_receipt` `:110`, `_with_pack_snapshot` `:157` |
| `RAGB/harness/packs/project_service.py` | Activation, snapshots, org policy, style CRUD, quality scoring; `_canonical_style_pack_id` `:182-198`, `parse_style_manifest` `:201` |
| `RAGB/harness/packs/fixtures/documentation.json` | Foundation pack `nexus.documentation` (`:3`, phase `foundation` `:8`) |
| `RAGB/harness/packs/fixtures/data-integration.json` | Functional pack `nexus.data-integration` (`:3`), `includes: ["nexus.documentation"]` (`:16`) |
| `RAGB/db/models/harness_pack.py` | 13 ORM row classes `:13-306` |
| `RAGB/api/routes/harness_packs.py` | `APIRouter(prefix="/harness/packs")` `:58`; mounted `RAGB/api/main.py:282` |
| `RAGB/api/routes/harness.py` | `GET /harness/packs` `:111` |
| `RAGB/harness/registry.py` | `:234-237` -- `default_capability_registry()` deferred-imports `compile_active_pack_registry` |
| `RAGB/harness/workflow_resume.py` | `:34-35`, `:228`, `:426`, `:553`, `:580` |
| `RAGB/harness/agent_chat_runtime.py` | `:96-98`, `:231` |
| `RAGB/harness/providers/mcp_docs.py` | Emits `nexus_pack_ids` / `nexus_pack_snapshot` `:239-280` |
| `RAGB/harness/manifest.py` | `:93-96` reads the snapshot back out |
| `RAGB/harness/service.py` | `:54-58` |
| `RAGB/harness/capability_handlers.py` | `:151-165` forwards pack metadata into generation args |

MCP counterpart (the contract's other half): `MCP/schemas.py` --
`NexusPackSnapshot` `:649`, `NexusPresentation`, `NexusPrivateAnalysisPack`
`:603`, hash cross-check `:757-773`, size caps `:328-332`;
`MCP/services/nexus_execution_contract.py` -- `build_nexus_execution_receipt`
(hash verify `:70-74`), `presentation_instructions_for_phase` `:90`;
`MCP/providers/base.py:90`; `MCP/providers/custom/harness.py:3844-3848`.

DB: `harness_custom_pack_versions` created `059_harness_project_packs.py:19-33`,
`governance_state` added `061_pack_governance.py:47-51`;
`harness_pack_activation_snapshots` `059:34-48`, +
`stack_selection`/`compiled_stack_snapshot`/`compiled_stack_hash`/`compiled_presentation`
`061:110-114`, + `private_version_receipts` `062:88-89`;
`harness_project_pack_activations` `059:49-60`;
`harness_organization_governance_policies` `061:53-69` with a seeded `default`
row (`061:22-41`, `061:62-69`).

API (`RAGB/api/routes/harness_packs.py`, prefix `/api/v1/harness/packs`):
`GET /documentation-packs` `:559`;
`GET|PUT /projects/{project_id}/documentation-pack` `:581`, `:613`;
`GET|PUT /projects/{project_id}/workspace` `:647`, `:681`;
`POST /projects/{project_id}/generate` `:732`;
`POST /documentation-packs` -> 410 `:821`;
`GET /projects/{project_id}/recommendations` `:834`; `GET /policy` `:841`;
`GET /library` `:1193`; `GET /library/{pack_id}` `:1203`;
`GET|PUT /projects/{project_id}/activations/{workflow_key}` `:1337`, `:1352`;
`POST /projects/{project_id}/custom` -> 410 `:1385-1390`;
`POST /projects/{project_id}/custom/{pack_id}/versions` -> 410 `:1403-1408`;
plus `GET /harness/packs` at `RAGB/api/routes/harness.py:111`.

Frontend (`RAG_pipeline/frontend`): `components/harness-packs/PackManager.tsx`
<- `app/admin/application-profile/page.tsx:1` (mounted `:4`), tests
`tests/components/PackManager.test.tsx:3`,
`tests/components/ApplicationProfilePage.test.tsx:5` (mocked);
`PackLibrary.tsx` <- `PackManager.tsx:25` (used `:179`), no direct test;
`StackInspector.tsx` <- `PackManager.tsx:26` (`:180`), tested;
`DocumentationPackSelector.tsx` <- `PackManager.tsx:24` (`:171`), tested;
`lib/harnessPacks.ts` <- `PackManager.tsx:11` (13 fetchers `:28-102`);
`lib/harnessPacksProxy.ts` -> 8 Next route handlers under
`app/api/harness/packs/`; `app/pack/page.tsx` is a redirect-only shim ->
`lib/packWorkspace.ts`. Proxy routes: `route.ts`, `library/route.ts`,
`policy/route.ts`, `documentation-packs/route.ts`, `builder/[...path]/route.ts`,
`projects/[projectId]/{activations/[workflowKey],custom,custom/[packId]/versions,documentation-pack,recommendations,styles,styles/[packId]/quality,styles/[packId]/versions}/route.ts`,
`styles/private/{route.ts,[packId]/quality,[packId]/versions}`.
**No `knowledge_base/dashboard` surface.**

Tests: `RAG_pipeline/backend/tests/test_harness/{test_packs,test_pack_runtime,test_pack_governance,test_pack_governance_migration,test_project_packs,test_pack_reference_attachments,test_mcp_docs_provider,test_onboarding_workflow,test_manifest}.py`,
`tests/test_api/{test_harness,test_harness_pack_governance}.py`, and MCP-side
`knowledge_base/mcp-server/tests/test_services/test_nexus_execution_contract.py`.

### 3.3 Concept 3 -- Style Pack (`StylePresentation`)

| File | Role |
|---|---|
| `RAGB/harness/packs/models.py` | `StyleAudience` `:65`, `StyleTone` `:72`, `TechnicalDepth` `:78`, `OutputOrganization` `:84`, `FormattingOption` `:90`, `CommunicationOption` `:97`, `StyleField` `:103`, `StylePresentation` `:187`, `StyleGovernancePolicy` `:217` |
| `RAGB/harness/packs/composition.py` | `_compile_presentation` `:281-317`, `_validate_permitted_presentation` `:319-350`, `_validate_style_namespace` `:376` |
| `RAGB/harness/packs/project_service.py` | Synth manifest `:159-179`, `_canonical_style_pack_id` `:182-198`, `parse_style_manifest` `:201`, accessible-style query `:786-808`, `_style_record` `:841`, `_parse_presentation` `:943` |
| **`RAGB/generation/prompt_builder.py`** | **Non-pack consumer.** Imports `StylePresentation` at `:7`; `presentation_instruction()` `:195`; injected at `:183`, `:283`, `:432`, `:485`. Reached from `RAGB/api/routes/query.py:18` and `RAGB/generation/rag_chain.py:16` -- the live RAG answer path. |
| `RAGB/api/routes/harness_packs.py` | Style request/response models `:123`, `:145`; import `:26` |
| `RAGB/db/models/harness_pack.py` | `HarnessStylePackVersionRow` `:49-103` |
| `MCP/services/nexus_execution_contract.py` | Mirrors the six style enums into prose `:25-58`, applied only at Phase 3 `:90-118` |

DB: `harness_style_pack_versions` created `061_pack_governance.py:71-92`;
indexes `:93-109`, including **two partial-unique indexes keyed on the string
literals `scope = 'user'` (`:98`) and `scope = 'project'` (`:106`)**.

API: `GET|POST /styles/private` `:1219`, `:1227`;
`POST /styles/private/{pack_id}/versions` `:1240`;
`PUT /styles/private/{pack_id}/quality` `:1253`;
`GET|POST /projects/{project_id}/styles` `:1274`, `:1287`;
`POST /projects/{project_id}/styles/{pack_id}/versions` `:1302`;
`PUT /projects/{project_id}/styles/{pack_id}/quality` `:1317`.

Frontend: `components/harness-packs/StylePackEditor.tsx` <- `PackManager.tsx:27`
(used `:181`), `tests/components/StylePackEditor.test.tsx:3`. No dashboard surface.

### 3.4 Concept 4 -- private Analysis Pack (`PackArtifactV3`)

| File | Role |
|---|---|
| `RAGB/harness/packs/models.py` | `PackArtifactV3` `:569` + members: `AudienceRenderProfile` `:479`, `PackAudienceProfile` `:488`, `PackAnalysis` `:520`, `PackEvidenceRequirement` `:526`, `PackOutputSection` `:533`, `PackOutputDocument` `:539`, `PackScoringDimension` `:552`, `PackCapabilityRequirement` `:558`, `PackCompatibility` `:564`; additive-id validator `:583` |
| `RAGB/harness/packs/draft_service.py` | The lifecycle. Drafts, versions, fork, attachments, `share` `:528`, `preview` `:553`, `propose_promotion` `:562`, `_parse_artifact` `:780` |
| `RAGB/harness/packs/builder_workflow.py` | LangGraph conversational builder; parses **untrusted model JSON** into `PackArtifactV3` at `:365-370` -- a security control |
| `RAGB/harness/packs/reference_limits.py` | 3 shared caps, mirrored in `MCP/schemas.py:328-332` -- a security invariant |
| `RAGB/harness/packs/project_service.py` | Private-version activation `:608-611`, `:643-654`; attachment fetch `:900-909` |
| `RAGB/harness/providers/mcp_docs.py` | Re-validates the artifact before egress `:16-17`, `:298-331` -- a security control |
| `RAGB/harness/capability_handlers.py` | 8 capability handlers `:958-1693` |
| `RAGB/harness/executor.py` | `:29-34` imports 4 draft exception types for policy mapping |
| `RAGB/harness/conversation_state.py` | `HarnessPackDraftRow` `:31`; `TaskKind.PACK_DRAFT = "pack_draft"` `:60`; draft hydration `:748`, `:892` |
| `RAGB/harness/models.py` | 8 `nexus.packs.*` capability definitions `:585-885`; `PackArtifactV3` projection note `:318` |
| `RAGB/harness/registry.py` | `:27-34`, `:200-207` |
| `RAGB/harness/response_synthesis.py` | `:69` -- `"pack_draft"` output-contract allowlist |
| `RAGB/harness/workflow_family_inventory.py` | `:29-38` -- `pack_builder` family; table name as the **string** `"harness_pack_drafts"` at `:36` |
| `RAGB/rbac/permissions.py` | `MANAGE_PACKS = "manage_packs"` `:26` |
| `RAGB/db/models/harness_pack.py` | `HarnessPackDraftRow` `:157`, `HarnessPrivateAnalysisPackRow` `:191`, `...VersionRow` `:209`, `HarnessPackAttachmentBlobRow` `:228`, `HarnessPackAttachmentBindingRow` `:247`, `...ShareRow` `:276`, `HarnessPackPreviewReceiptRow` `:289`, `HarnessPackPromotionProposalRow` `:300` |

MCP mirror: `MCP/schemas.py:474-647` (`NexusPrivate*`),
`MCP/services/nexus_execution_contract.py:121-207`.

DB: `harness_pack_drafts` created `062_pack_draft_builder.py:21-35`,
`conversation_id` nullable + `discovery_context`
`065_detach_pack_drafts_from_conversations.py:20-36`;
`harness_private_analysis_packs` `062:36-45` (+`updated_at` `063:18-38`);
`harness_private_analysis_pack_versions` `062:46-58`;
`harness_private_analysis_pack_shares` `062:59-67`;
`harness_pack_preview_receipts` `062:68-76`;
`harness_pack_promotion_proposals` `062:77-87`;
`harness_pack_attachment_blobs` `064_pack_reference_attachments.py:39-53`;
`harness_pack_attachment_bindings` `064:54-71`;
`harness_pack_drafts.target_private_pack_id`/`.base_private_pack_version_id`
`064:22-23`; `permissions('manage_packs')` + admin grant
`074_pack_capability_permission.py:18-29`; `conversation_state` backfill
emitting `'pack_draft'` / `'nexus.packs.drafts.mutate'`
`067_unified_conversation_state.py:175-182`.

API (`harness_packs.py`): `POST /builder/drafts` `:855`; `POST` (drafts variant)
`:871`; `GET|PATCH /builder/drafts/{draft_id}` `:890`, `:898`;
`POST /builder/drafts/{draft_id}/attachments` `:914`;
`PATCH|DELETE .../attachments/{attachment_id}` `:937`, `:949`;
`GET .../attachments/{attachment_id}/content` `:962`;
`POST .../validate` `:982`; `.../save` `:990`; `.../abandon` `:1002`;
`GET /builder/private` `:1014`; `GET /builder/private/{version_id}` `:1019`;
`POST .../drafts` `:1027`; `GET .../attachments/{attachment_id}/content` `:1043`;
`POST .../compatibility` `:1063`; `.../preview` `:1074`; `.../share` `:1090`;
`.../promotion-proposals` `:1101`; `.../activate` `:1109`.

Frontend: `components/harness-packs/PackWorkbench.tsx` <-
`components/ChatLayout.tsx:13` (mounted `:143`), tested;
**`components/harness-packs/AnalysisPackBuilder.tsx` has NO production
importer** -- only `tests/components/AnalysisPackBuilder.test.tsx:3` (verified by
repo-wide grep excluding `node_modules`); `lib/packWorkspace.ts` <-
`app/pack/page.tsx:2`, `ChatLayout.tsx:14`, `ChatWindow.tsx:16`,
`lib/packCommand.ts:2`, `PackWorkbench.tsx:8`; `lib/packCommand.ts` <-
`components/ChatWindow.tsx:15`, `tests/lib/packCommand.test.ts:2`.

### 3.5 Concept 5 -- InstructionPack -- dead, with caveats

| File | Role |
|---|---|
| `MCP/services/pack_loader.py` | Whole module, 45 lines. `InstructionPack` `:9`, `PackLoader` `:16`, `load()` `:20`, `get()` `:44` |
| `MCP/config.py:463` | `instruction_pack_dir: str = "packs/default"` (env `MCP_INSTRUCTION_PACK_DIR`; prefix set `:14`) |
| `MCP/config.py:74` | `project_root = Path(__file__).resolve().parent.parent.parent.parent` |
| `MCP/providers/custom/harness.py:6845-6847` | `PackLoader(pack_dir).load() if pack_dir.exists() else None` |
| `MCP/providers/custom/prompts.py` | `InstructionPack` `:12`; preamble override `:63-73`; threaded `:88`, `:210`, `:253` |
| `MCP/providers/custom/harness.py:644` | `pack:` ctor param |
| `MCP/providers/custom/pipeline/helpers.py:13,48,121` | type-only re-exposure |
| `MCP/services/validation_service.py:27-37` | Questionnaire path; falls back to `DEFAULT_QUESTIONNAIRE` |
| `MCP/services/project_registry.py:151-164` | Per-project override write |
| `MCP/api/routes/projects.py` | `PackLoader` `:136`; `@router.get("/instruction-pack")` `:3122`, handler `:3123-3140`; questionnaire wiring `:3099-3100`; DTO plumbing `:212`, `:1141`, `:2540` |
| `MCP/db/models.py:79` | `projects.instruction_pack_dir` column |
| `MCP/schemas.py:53`, `:816` | DTO fields |

DB: `projects.instruction_pack_dir`, `alembic/versions/010_add_instruction_pack_dir.py:20`.
No dedicated table.

Frontend: `knowledge_base/dashboard/src/components/InstructionPackPanel.tsx` <-
`src/components/ProjectSettings.tsx:19` (mounted `:234-237`), no test;
`useInstructionPack()` `src/hooks/useProjects.ts:377-379`; client
`src/api/client.ts:414` (`/mcp/instruction-pack`);
`ModelSelectionPanel.tsx:24,49,164` displays the version string.

Tests: `tests/test_services/test_pack_loader.py` (3 tests `:19`, `:30`, `:40`);
`tests/test_api/test_projects.py:1231`.

**Deadness evidence.** `knowledge_base/packs/` exists on disk but is **empty and
untracked** (`git ls-files knowledge_base/packs` returns nothing). Dev
resolution: `project_root` = `knowledge_base` -> `knowledge_base/packs/default`
-> absent -> `pack = None`. Container resolution: `config.py` sits at
`/app/src/mcp_server/config.py` (`Dockerfile:28,46`), so `parent x4` = `/` ->
`/packs/default` -> absent -> `pack = None`. Both call sites are
`if pack_dir.exists()`-guarded (`harness.py:6847`,
`validation_service.py:34`). Note `docker-compose.yml:334` and
`deploy/deploy-client.sh:250` mount `./knowledge_base/packs` at
`/app/workspace/packs`, which **can never satisfy** `instruction_pack_dir`
because in-container `project_root` is `/`. That mismatch is itself evidence the
layer has been dead since containerisation.

### 3.6 `providers/custom/prompt_packs.py` -- the spec is wrong about this one

Spec `:344-346` says the module goes. **It cannot go as a unit.** The file holds
two unrelated things:

1. `REQUIRED_SECTION_NAMES` `:15`, `CONDITIONAL_SECTION_NAMES` `:35`,
   `ANALYSIS_SECTION_NAMES` `:41` -- imported by `analysis_parser.py:12`,
   `harness.py:96`, `phase2_claims.py:32`, `prompts.py:10`, `validators.py:16`.
   This is the constant the spec wants migrated.
2. `materialize_prompt_packs()` `:59` -- **live and unrelated.** Called from
   `harness.py:1677` and `opencode_cli.py:343`. Writes the staging prompt
   bundles. Plus `SCORECARD_SOURCE_FILES` `:43` and `_BUNDLE_REF_GLOBS` `:51` (a
   string-keyed glob registry). Its outputs are read back by path at
   `harness.py:4257`, `:6311`, `:6513`, `phase26_scorecard.py:51`,
   `opencode_cli.py:577`.

Deleting the file breaks the OpenCode provider and Phase 2.6/3/3.5 prompt
assembly. **Split it: move the section constants into the contract module, leave
`materialize_prompt_packs` where it is.**

### 3.7 Import graph

Within `RAGB/harness/packs/`:

```
models.py                       (leaf)
  <- loader.py:10, draft_service.py:32, builder_workflow.py:23,
     catalog.py:11, composition.py:12, runtime.py:12, project_service.py:30
reference_limits.py             (leaf)
  <- draft_service.py:33, providers/mcp_docs.py:17
loader.py       <- catalog.py:10
catalog.py      <- composition.py:11, runtime.py:11, project_service.py:28, __init__.py:5
composition.py  <- project_service.py:29
draft_service.py <- builder_workflow.py:22
runtime.py      <- project_service.py:61
__init__.py:19 (FIXTURE_PACK_DIRECTORY) <- runtime.py:10     [CYCLE]
project_service.py <- __init__.py:21 (deferred, noqa: E402)  [CYCLE]
```

Two cycles to respect: `__init__ -> project_service -> runtime -> __init__`, and
`__init__ -> catalog -> loader -> models`.

Outbound edges from `RAGB/harness/packs/`: `api/routes/harness.py:14-16`;
`api/routes/harness_packs.py:17-50`; `generation/prompt_builder.py:7`;
`harness/agent_chat_runtime.py:96-98`; `harness/capability_handlers.py:60-67`;
`harness/executor.py:29-34`; `harness/providers/mcp_docs.py:16-17`;
`harness/registry.py:235` (deferred, inside the function);
`harness/workflow_resume.py:34-35`; `harness/conversation_state.py:31`;
`db/models/__init__.py:51-64`.

Cross-service edge -- **no Python import, HTTP plus SHA-256**:

```
composition.py -> CompiledPackStack
  -> runtime.py:_with_pack_snapshot   (metadata["nexus_pack_snapshot"])
  -> capability_handlers.py:151-162
  -> providers/mcp_docs.py:239-331    (JSON body)
  == HTTP ==>
  MCP/schemas.py:711-773              (validate_nexus_pack_ids, hash equality)
  -> MCP/services/nexus_execution_contract.py:70-74   (second hash check)
  -> MCP/providers/custom/harness.py:3844
```

Both sides recompute the canonical hash independently. Any field change on one
side is a silent 422 on the other.

Frontend graphs:

```
RAG_pipeline/frontend
  app/admin/application-profile/page.tsx:1 -> PackManager.tsx
      -> DocumentationPackSelector.tsx (:24), PackLibrary.tsx (:25),
         StackInspector.tsx (:26), StylePackEditor.tsx (:27),
         lib/harnessPacks.ts (:11)
  components/ChatLayout.tsx:13 -> PackWorkbench.tsx -> lib/packWorkspace.ts (:8)
  components/ChatWindow.tsx:15 -> lib/packCommand.ts -> lib/packWorkspace.ts
  app/pack/page.tsx:2 -> lib/packWorkspace.ts
  AnalysisPackBuilder.tsx  <-- ORPHAN (test-only)

knowledge_base/dashboard
  src/App.tsx:15 -> PluginPacksPage.tsx -> useProjects.ts:159-268 -> client.ts:239-335
  src/components/ProjectSettings.tsx:19 -> InstructionPackPanel.tsx
  src/components/ProjectSettings.tsx:35 -> useProjects.ts:377 -> client.ts:414
```

### 3.8 Dependency-ordered deletion plan

**Wave 0 -- safe now, zero inbound production edges**

| Target | Why safe | Also delete |
|---|---|---|
| `RAG_pipeline/frontend/components/harness-packs/AnalysisPackBuilder.tsx` | Repo-wide grep minus `node_modules` finds exactly one importer: its own test | `tests/components/AnalysisPackBuilder.test.tsx` |
| `RAG_pipeline/frontend/app/api/harness/packs/projects/[projectId]/custom/route.ts` and `.../custom/[packId]/versions/route.ts` | Proxy to backend endpoints hard-coded `410 GONE` (`harness_packs.py:1385-1418`) | -- |
| `harness_packs.py:821-832` (`POST /documentation-packs` -> 410) | Tombstone, no caller | -- |
| InstructionPack: `MCP/services/pack_loader.py`, `InstructionPackPanel.tsx`, `useInstructionPack`, `client.ts:414`, `GET /instruction-pack` (`projects.py:3122`) | See caveat | `tests/test_services/test_pack_loader.py`, `tests/test_api/test_projects.py:1231` |

**Wave 0 caveat on InstructionPack.** It is unreachable *given current data*, not
structurally. Confirm first: (1) no production row has
`projects.instruction_pack_dir` non-NULL -- that column
(`010_add_instruction_pack_dir.py:20`) is a **DB-stored path** overriding the
config default at `harness.py:6846` and `validation_service.py:32`; (2)
`MCP_INSTRUCTION_PACK_DIR` and `MCP_PROJECT_ROOT` are unset in every deployment
(both settable, `config.py:14`); (3) remove the orphaned mount
`docker-compose.yml:334` and `deploy/deploy-client.sh:250`. Order inside Wave 0:
`prompts.py` signatures -> `harness.py:644,6845` -> `validation_service.py` ->
`projects.py` route + DTOs -> `pack_loader.py` -> dashboard -> column-drop
migration last.

**Wave 1 -- Concept 3 (Style Pack), after retyping consumers**

Blocked by one edge unrelated to packs: `RAGB/generation/prompt_builder.py:7`
imports `StylePresentation` and uses it in the **RAG chat/answer** path.

1. Move `StylePresentation` + the six style enums out of
   `harness/packs/models.py` into a neutral module (e.g.
   `src/generation/presentation.py`) or into the new manifest's `audience:` type.
   Repoint `prompt_builder.py:7`.
2. Repoint `MCP/services/nexus_execution_contract.py:25-58` enum-to-prose tables.
3. Delete the 8 style routes (`harness_packs.py:1219-1334`) and their 6 Next
   proxy routes.
4. Delete `StylePackEditor.tsx`, its `PackManager.tsx:27,181` call site,
   `lib/harnessPacks.ts:37-68` (6 style fetchers), and the test.
5. Delete `project_service.py:159-233` and `:786-841`;
   `composition.py:281-398`.
6. Delete `HarnessStylePackVersionRow` (`harness_pack.py:49-103`); drop
   `harness_style_pack_versions`.

**Load-bearing despite looking like Style plumbing:** `_canonical_style_pack_id`
(`project_service.py:182-198`) is the server-owned-namespace guard the spec
itself says to reuse (`docs/one-pack-program.md:384-386`). Port it, do not
delete it.

**Wave 2 -- Concept 2, absorb then delete (largest, most dangerous)**

Cannot start before Wave 1.

1. **`RAGB/harness/registry.py:234-237` is the choke point.**
   `default_capability_registry()` = `compile_active_pack_registry(base_capability_registry())`.
   Called from `api/routes/harness.py:107,298`,
   `harness/connectors/capabilities.py:22`,
   `harness/workflow_resume.py:426,553,580`, `harness/__init__.py:27`. Until this
   is repointed at the new manifest compiler, **nothing in `packs/runtime.py` or
   `packs/catalog.py` can be removed** -- the platform has zero capabilities
   without it.
2. Repoint `packs/runtime.py:157-231` (`_with_pack_snapshot`) to emit the new
   snapshot under the same `nexus_pack_*` keys, or version the receipt.
   `NEXUS_EXECUTION_RECEIPT_SCHEMA_VERSION`
   (`MCP/services/nexus_execution_contract.py:23`) exists for exactly this --
   bump it.
3. Then `composition.py`, `catalog.py`, `loader.py`, `fixtures/*.json`.
4. Then the activation routes (`harness_packs.py:1193-1383`), the
   `PackManager.tsx` subtree, `lib/harnessPacks.ts`.
5. Then drop `harness_custom_pack_versions`,
   `harness_project_pack_activations`, `harness_organization_governance_policies`.

**Do not drop `harness_pack_activation_snapshots`.** Spec section 9
(`docs/one-pack-program.md:377-379`) explicitly wants it wired to doc runs as the
substitute audit control. It is under-used, not obsolete.
`harness_pack_drafts.target_private_pack_id` and the `private_version_receipts`
column (`062:88`) hang off it.

**Wave 3 -- Concept 4, schema only (lifecycle stays)**

Per spec `:328`: keep `draft_service.py`, delete `PackArtifactV3`. Retype in this
order (each step is a compile-break if done out of order):

1. `models.py:479-600` -- `PackArtifactV3` + its 9 member types -> the new
   manifest type.
2. `draft_service.py:32,116,132,138,780-783`.
3. `builder_workflow.py:23,36,99,365-370` -- the untrusted-model-output parser.
   Its protected-field check is a security control; port it, do not drop it.
4. `providers/mcp_docs.py:16,321` -- egress re-validation, also a security
   control.
5. `project_service.py:38,116,654,948`.
6. MCP mirror `schemas.py:474-647` in lockstep with step 4.

Tables (`harness_pack_drafts`, `harness_private_analysis_pack*`,
`harness_pack_attachment_*`, `harness_pack_preview_receipts`,
`harness_pack_promotion_proposals`) **stay** -- they are the lifecycle. Only the
`artifact` JSON payload shape changes.

**Never delete:** `MCP/services/plugin_schema.py` (Concept 1 is the survivor);
`pack_governance.py` on both sides (spec `:334`); `draft_service.py`,
`project_service.py`, `PackWorkbench.tsx`, `packCommand.ts` (spec `:334-340`);
`packs/reference_limits.py` (mirrored in `MCP/schemas.py:328-332`, a security
invariant).

### 3.9 Runtime-only reference hazards -- a static import search misses all of these

**H1 (critical) -- fixture JSON loaded by path, gating the entire capability
registry.** `RAGB/config.py:232-233` declares `nexus_pack_directory: str = ""`
and `nexus_active_packs: str = "nexus.data-integration"`. An empty
`nexus_pack_directory` falls back to `FIXTURE_PACK_DIRECTORY` =
`RAGB/harness/packs/fixtures/` (`packs/runtime.py:31-33`); `packs/loader.py:34`
globs `*.json`; `catalog.py:82` resolves the include closure;
`registry.py:237` intersects capabilities with it. **Deleting
`fixtures/documentation.json` or `fixtures/data-integration.json` yields an empty
capability registry with no import error and no import-time test failure.** The
pack ids are string literals in a comma-separated env value, and
`includes: ["nexus.documentation"]` (`data-integration.json:16`) is a string
reference resolved at load. Also a literal at `project_service.py:135-136` (the
hard-coded default org policy).

**H2 (critical) -- the cross-process hash-verified contract.** Two independent
SHA-256 recomputations (`MCP/schemas.py:757-773`,
`MCP/services/nexus_execution_contract.py:70-74`) against
`packs/composition.py:399 _canonical_hash` / `catalog.py:26 manifest_hash`.
Change one field name in `PackManifest`, `CompiledPackStack`,
`StylePresentation` or `PackArtifactV3` and every real document generation fails
at the boundary with a hash mismatch. No shared module, so no static analysis
catches it.

**H3 (high) -- string-keyed capability metadata.** `packs/runtime.py:212-227`
writes 6 keys into `CapabilityDefinition.metadata`, read by string at
`capability_handlers.py:151-162`, `manifest.py:93-96`, `service.py:54-58`,
`providers/mcp_docs.py:239-331`. Plus a **capability-id-conditional** branch at
`runtime.py:203-207`: private analysis packs are injected only when
`capability.id == "mcp.docs.generate_project_documentation"`. And
`runtime.py:151` -- `capability.metadata.get("agent_control_plane") is True` is
the escape hatch keeping control-plane capabilities alive regardless of pack
selection; removing it silently locks the agent out of its own control plane.

**H4 (high) -- DB-stored type discriminators.**

| Value | Written | Read |
|---|---|---|
| `task_kind = 'pack_draft'` | `067:176`, `conversation_state.py:60` | `conversation_state.py:748,892`; `response_synthesis.py:69` |
| `capability_id = 'nexus.packs.drafts.mutate'` | `067:177` | registry lookup by string |
| `task_ref_id = 'pack:<id>'` | `067:175`, `conversation_state.py:758,912`, `capability_handlers.py:980` | parsed at `capability_handlers.py:1666-1669` (`removeprefix("pack:")`) |
| `authority_hint = {"kind": "pack_create"}` | `capability_handlers.py:1150,1260` | `conversation_state.py:980` |
| link `kind: "pack_draft"` / `"private_pack"` | `capability_handlers.py:1049,1132,1576,1629` | frontend link renderer |
| `harness_custom_pack_versions.governance_state = 'legacy'` | `061:47-51` server_default | `runtime.py:136-141` raises `PackRuntimeError` when `governance is None` |
| `harness_style_pack_versions.scope in ('user','project')` | app code | **two partial-unique DB indexes on those literals**, `061:98`, `061:106` |
| `plugin_packs.origin='legacy'`, `certification_status='legacy_untrusted'` | `021:23-24` | `pack_governance.py` |
| `permissions.codename = 'manage_packs'` | `074:18-29` | `harness/models.py` x8 (`:605`..`:880`), `rbac/permissions.py:26` |
| table name `"harness_pack_drafts"` as a **string** | `workflow_family_inventory.py:36` | asserted by `tests/test_harness/test_workflow_family_inventory.py` |

**H5 (high) -- style pack id namespace from a project-id hash.**
`project_service.py:186-188` builds `f"style.user.{owner_user_id}."` and
`f"style.project.{sha256(project_id)[:12]}."`. Existing
`harness_style_pack_versions.pack_id` values and stored
`compiled_stack_snapshot.style_pack_ids` arrays encode this scheme; changing the
algorithm orphans every stored style pack with no error. Also load-bearing:
`composition.py:376 _validate_style_namespace` and `project_service.py:190`
reject cross-namespace ids -- a tenancy control.

**H6 (medium) -- `getattr` duck-typing on the plugin manifest.** The plugin
manifest is read almost entirely through `getattr(..., default)`, so removing a
field yields silent default behaviour, not an error:
`providers/custom/harness.py:734,738,746,2083,3631,5956,6162-6177,6232,6256,6318`;
`providers/custom/validators.py:1107-1111` (including
`getattr(manifest, "model_fields_set", set())`);
`providers/custom/evidence_agents.py:287,293`;
`providers/custom/evidence_bundles.py:55,83-90`;
`services/claude_service.py:1253`; `api/routes/projects.py:212-213,3099`. Note
`PluginPackManifest._steps_not_empty` (`plugin_schema.py:166-173`) **mutates
`model_fields_set`** -- it discards `"validation"` after synthesizing it from
`quality_gates`, and `validators.py:1111` then reads `model_fields_set` to decide
behaviour. Two modules coupled through a pydantic internal.

**H7 (medium) -- files resolved by path, not import.**
`config.data_dir / "plugin_packs" / f"{repo}__{branch}__{digest}"`
(`plugins.py:268`); `config.data_dir / "swap_pack_starter"`
(`plugins.py:186-187`); `plugin_pack_dir / "prompts" / "investigation.md"`
(`phase1_investigation.py:62`); `.../prompts/phase2.md` (`harness.py:3209`);
`prompts/scorecard.md`, `prompts/phase3.md`, `prompts/docs/<DOC>.md`, `examples/`
(`prompts.py:141,214,266-337`);
`staging_dir/_assessment/prompt_packs/_*.md` (written `prompt_packs.py:63-99`,
read `harness.py:4257,6311,6513`, `phase26_scorecard.py:51`,
`opencode_cli.py:577`); `_BUNDLE_REF_GLOBS` string-keyed globs
(`prompt_packs.py:51-55`); `SCORECARD_SOURCE_FILES` 5 filenames
(`prompt_packs.py:43-49`); `plugin_packs/integration_assessor` resolved via
`Path(__file__).parents[2]` in `tests/test_services/test_plugin_runner.py:490`
(deleting that reference pack breaks a live test);
`data/prompt_templates/*` (8 entries).

**H8 (medium) -- dynamic import in a sibling that pack code depends on.**
`packs/runtime.py:47` calls
`default_connector_provider_registry().provider_keys()` to build the reference
whitelist; that registry resolves adapters with
`getattr(importlib.import_module(module_name), class_name)` at
`harness/connectors/catalog.py:53`. Pack manifests reference connector providers
by **string key**, and the keys exist only if the dynamic import succeeded -- so a
pack-manifest validation failure can be caused by an unrelated connector module
rename. The only other `importlib` use in the RAG backend is
`RAGB/runtime/client.py:145`. Neither `entry_points` nor plugin-discovery entry
points exist in either service.

**H9 (low) -- retired-but-live tombstones.** `harness_packs.py:821` and
`:1385-1418` return `410 GONE` with `response_model=PackManifest`. Deleting
`PackManifest` in Wave 2 breaks these endpoints at import time even though they
never return a body. Delete the tombstone routes before the model.

**H10 (low) -- two independent slash-command tables.**
`RAGB/harness/commands.py:43-46` registers `name="pack"` as pure metadata
(module docstring `:3-5`: "presentation hints only"), served at
`api/routes/conversations.py:106`, asserted by
`tests/test_harness/test_slash_commands.py:11`. The frontend independently
parses `list` / `create` / `use <pack> for <project>` client-side at
`lib/packCommand.ts:8-42`. Two string tables that must stay in sync.

### 3.10 Deletion risk ranking

| Rank | Item | Why |
|---|---|---|
| 1 | `packs/fixtures/*.json` + `settings.nexus_active_packs` | Deletion produces a silently empty capability registry (H1) |
| 2 | The `nexus_pack_snapshot` hash contract | Cross-process, no import, fails only on real generation (H2) |
| 3 | `registry.py:234-237` | Single choke point for all of Concept 2 |
| 4 | `prompt_builder.py:7` (`StylePresentation`) | Concept 3 leaks into the live RAG answer path |
| 5 | `_canonical_style_pack_id` | Looks like Style plumbing; is a tenancy control the spec wants kept |
| 6 | `harness_pack_activation_snapshots` | Looks under-used; is the designated audit substitute |
| 7 | `prompt_packs.py` | Spec says delete; half the module is live and unrelated |
| 8 | `projects.instruction_pack_dir` non-NULL rows | The only thing between "dead layer" and a live per-project override |

---

## 4. Extraction artifacts and a fingerprint-keyed evidence layer

### 4.1 Corrections to assumptions

| Assumption | Reality |
|---|---|
| `evidence_quality_gate` lives under `phases/` | `MCP/providers/custom/pipeline/evidence_quality_gate.py:15` -- package root. Confirmed by the import at `harness.py:1593`. |
| 7 phases | An 8th, `Phase37BusinessTopology` (`MCP/providers/custom/pipeline/phases/phase37_business_topology.py:24`), is imported at `harness.py:1588-1590` and is the sole writer of `_topology/`. It survives cleanup and the swap. |
| `MCP_PHASE2_TASK_EVIDENCE_BYTES` default 70000 (per `knowledge_base/CLAUDE.md`) | Code says **48_000** -- `MCP/config.py:269`. The doc is stale. |
| `topology/partition_state.json` is under staging | It is `output/{project_id}/topology/partition_state.json` -- **outside** `docs_staging/` and outside the swap. |
| `_debug/` written by phases | Written only through `MCP/providers/custom/debug_artifacts.py:29-37`; no-op unless `MCP_PIPELINE_DEBUG=1` (`config.py:355`). |

### 4.2 What each phase writes into `docs_staging/`

Pre-harness (`MCP/services/claude_service.py`):

| Target | Site |
|---|---|
| `docs_staging/`, `_analysis/`, `_assessment/` mkdir + chmod | `:1200-1207` |
| `.generation.lock` | `:1148` -> `services/generation_run_state.py:27,57,79` |
| `_run_manifest.json` | `:1164` -> `generation_run_state.py:31,122,141,168` |
| fresh-run wipe of staging children (lock preserved) | `:1180-1188` |
| `_preprocess/{group}.md` (+ `_part1/_part2`, `_part_ci{k}`) | `:485-486`, `:623` |
| `_preprocess/_analysis_guide.md`, `_analysis_prompts*.md`, `_phase3_guide.md`, ref bundles, `_scoring_rubric.md`, `_anti_patterns.md`, `_red_flags.md`, `_security_checklist.md`, `_pattern_library.md`, `_bundle_analysis.py`, `_doc_templates.md` | `:642, 673, 689, 750, 772, 780, 1090` |
| `_manifest.json` (stale-scan detection) | `:1377` |
| `_assessment/quick_scan.md` | `:804`, `:967` |

Harness setup (`MCP/providers/custom/harness.py::run`):

| Target | Site |
|---|---|
| `facts_manifest.json` | `:1632-1633` |
| `_preprocess/talend_esb_facts.json` | `:1655` |
| top-level engineering-fact artifacts (`engineering_facts.json/.md`, `config_runtime_map.json/.md`, `entity_index.csv`, `usage_index.csv`, `dependency_map.md`, `change_impact_matrix.md`, `architecture_map.md`, `data_flow_map.md`, `api_surface.md`, `test_coverage_map.md`, `known_risks.md`) | `:1659-1668` -> `providers/custom/engineering_facts/{json,csv,markdown}_writer.py` |
| `cross_project_refs.md` (partition runs) | `:1676` |
| `_assessment/prompt_packs/*.md` | `:1677` -> `prompt_packs.py:63-73` |
| `_evidence/{profile}.{json,md}` (plugin packs) | `:1679` -> `evidence_bundles.py:62-66` |
| `_evidence_registry/{evidence_registry.json, evidence_brief.md, phase2_slices/*, doc_slices/*}` | `:1680`, `:6212` -> `evidence_agents.py:445,553,582,685` |
| `_plans/{execution_plan, node_results.json, graph_events.jsonl}` | `:895-896, 1022, 1036, 6160-6161` |
| `_investigation/` (+ `_summary.md` on skip) | `:1754-1755, 1774, 1780` |
| `_assessment/scorecard.md` (omitted-scorecard marker) | `:1732` |
| `_topology/cross_project_index.json` (partition runs) | `:6677-6678` |

Phase 1 (`pipeline/phases/phase1_investigation.py`): `_investigation/` mkdir
`:57-58`; `_investigation/_tasks/{task_id}.md` `:83-85` ->
`evidence_tasks.py:306-316` (`TASKS_DIRNAME` at `evidence_tasks.py:31`);
`_investigation/{group}.md|.json` `:167, 231-234, 268-271, 318`;
`_investigation/_summary.md` `:337-341`; `_run_manifest.json` chunk record
`:87`; `_investigation/_bundles/{doc}.md` `harness.py:5968-5971`.

Phase 2: `_analysis/` mkdir `harness.py:1997-1998, 2041-2042, 2081-2082,
3041-3042, 3068-3072`; `_analysis/{group}.md` `:2185, 3377`;
`_analysis/_tasks/*` via `phase2_analysis.py:84-93` -> `harness.py:2191-2196,
2613-2614`; `_analysis/_claims/*` and `_analysis/_context_packets/*`;
`_analysis/_coverage_units/{group}.json` `:1049-1052`;
`_analysis/_coverage_report.json` `phase2_analysis.py:103-105, 127-129`;
`_analysis/_depth_report.json` `:150-152, 161-163`;
`_analysis/_guide_sections_report.json` `:179-181, 196-198`;
`_analysis/_cross_cutting_facts.md` `harness.py:5669-5674`;
`_analysis/_noncode_project_context.md` `:6526`; `_transient/claim_repair/*` via
`providers/custom/transient_artifacts.py:23,31`;
`_agent_tasks/phase2_group/{target}/{status.json,output.md}`
`providers/custom/agent_runtime.py:369,390,422,475,493`;
`_model_inputs/phase2.claims/{NNN}_{target}/manifest.json` `harness.py:5062-5067`;
`_model_slices/{phase}/{slice}.md` `:4627-4628`;
`_assessment/slice_cap_flags.json` `:4800-4801`;
`_assessment/phase_costs.json` `:1979-1982`.

Phase 2.6: `_assessment/` mkdir `phase26_scorecard.py:44-45`;
`_assessment/scorecard.md` (real or "SCORECARD UNAVAILABLE") `:136-145, 189`;
`_debug/_assessment/_scorecard_contract_errors.json` `:177-180` (gated);
`_agent_tasks/scorecard/scorecard/*`; `_transient/` reduce inputs `:152`.

Phase 2.8: `_analysis/_doc_pack_{doc}.md` `phase28_bundles.py:32` ->
`harness.py:3615-3617`; `_analysis/_doc_pack_manifest.json` `harness.py:3618`;
`documentation_obligations.json` `phase28_bundles.py:33` -> `harness.py:3620-3623`.

Evidence quality gate: `_assessment/thin_bundle_report.json` only --
`evidence_quality_gate.py:53-66` (reads `_preprocess/{group}.md` sizes at
`:31-36`, 500-byte threshold at `:16`).

Phase 3: `{doc_name}.md` at staging root `phase3_docs.py:46`, rewritten with a
thin-bundle notice at `:51-54`; `_analysis/_evidence_{doc}.md`
`providers/custom/target_evidence.py:563-566`; unit-filtered fact-map copies
`harness.py:6759` -> `target_evidence.py:629-631`; `_analysis/_phase3_views/`
`harness.py:6744`; `_analysis/_unit_docs_digest.md` `:4153-4155`;
`_assessment/doc_validation_report.json` `phase3_docs.py:126-128`;
`_assessment/doc_depth_report.json` `:155-157`;
`_assessment/source_provenance_report.json` `:172-174, 187-189`;
`_assessment/enterprise_gates.json` + `_audit/{enterprise_gates.json,
final_doc_hashes.json}` `:203` -> `harness.py:5508-5518`;
`_assessment/failed_docs.json` `harness.py:4039-4040`; gated
`_debug/_assessment/traceability_{doc}.json` `phase3_docs.py:77-93` and
`_debug/_assessment/reviewers/*.json` `harness.py:5500-5503`;
`_agent_tasks/phase3_doc/{doc}/*`; `_model_inputs/phase3/{NNN}_{doc}/manifest.json`.

Phase 3.7 business topology (`phase37_business_topology.py`): `_topology/` mkdir
`:57-58`; `validation_report.json` `:59`; `state.json` `:60, 436-447`;
`business_topology.json` `:61`; `_topology/evidence/*.jsonl` + `evidence_ids.json`
`:66` -> `services/business_topology/evidence.py:350-351, 570-581`;
`_topology/factual_evidence_index.json` and
`_topology/_doc_pack_business_topology.md` `evidence.py:642-644`;
`_topology/_context/*` `services/business_topology/context.py:166-179`.

### 4.3 Phase 4 cleanup -- exact behaviour

`MCP/providers/custom/pipeline/phases/phase4_cleanup.py` (134 lines).

Writes / transforms:

| Line | Action |
|---|---|
| `:44-46` | if `_assessment/enterprise_gates.json` is missing, recompute and write `_audit/{enterprise_gates.json, final_doc_hashes.json}` |
| `:47` | `archive_engineering_artifacts` -> **moves** every top-level engineering-fact file into `_facts/` (`harness.py:5520-5533`, `shutil.move` at `:5533`; source list from `state.engineering_fact_artifacts`, `harness.py:1666-1668`) |
| `:64-67` | `_debug/index.json` (gated) |
| `:68-78` | `_debug/_assessment/warnings.json` (gated) |
| `:79-81` | `_debug/provider_calls.json`, `_debug/_audit/provider_calls.json` (gated) |
| `:85-90` | `build_rag_support_package` |
| `:104-117` | `output/{project_id}/logs/run_{project_id}_{ts}.log` (outside staging) |

`build_rag_support_package` (`MCP/services/rag_support_package.py:240-307`):
`:265-267` recreates `_rag_support/phase2_analysis/` from scratch; `:269`
`resolve_group_analysis_files` (`:89-118`) assigns each `_analysis/*.md` to a
facts-manifest group via `analysis_stem_matches_group` (`:67-86`,
case-sensitive, longest match wins), never assigning `_doc_pack_*`, `_bundle_*`
or any `_`-prefixed report; `:271-287` `shutil.copy2` per owned slice with bytes
+ sha256; `:296-298` writes `_rag_support/manifest.json` including
`missing_groups`; `:303-305` also copies `_analysis/_doc_pack_manifest.json` to
`_rag_support/_doc_pack_manifest.json`.

Deletes -- the entire deletion surface, `:99-102`:

```
for directory in ("_preprocess", "_analysis", "_transient"):
    shutil.rmtree(target, ignore_errors=True)
```

Keeps everything else: `{doc}.md`, `facts_manifest.json`,
`documentation_obligations.json`, `_manifest.json`, `_run_manifest.json`,
`_facts/`, `_assessment/`, `_audit/`, `_investigation/` (including
`_investigation/_tasks/`), `_evidence_registry/`, `_evidence/`, `_topology/`,
`_agent_tasks/`, `_model_inputs/`, `_model_slices/`, `_plans/`, `_rag_support/`,
`_debug/`.

**Verdict on spec section 11 agent-2 item 2 ("keeps the ANALYSIS and deletes the
EVIDENCE -- inverted for a pack-based product"): accurate and slightly
understated.** The interpretation layer survives in reduced form as
`_rag_support/phase2_analysis/`. What is destroyed with no persisted copy is the
entire evidence-shaping layer: `_preprocess/{group}.md`, `_analysis/_tasks/`,
`_analysis/_claims/`, `_analysis/_context_packets/`, `_analysis/_doc_pack_*.md`,
`_analysis/_evidence_{doc}.md`, and the three `_analysis/_*_report.json`
validator reports (which the enterprise gate reads from `_analysis/` at
`harness.py:5305-5306` and which are then unrecoverable). One evidence layer
survives *by accident*: `_investigation/_tasks/`, simply because
`_investigation` is not in the rmtree tuple.

### 4.4 Atomic swap

`MCP/services/atomic_swap.py::swap_docs:30-74`. Callers: `api/routes/projects.py:2055`
(single run, inside `_run_generate_background` at `:1853`) and
`services/partition_orchestrator.py:246` (per partition).

`:50-51` clear `docs_prev/`; `:54-56` `os.rename(docs -> docs_prev)`; `:60`
`os.rename(docs_staging -> docs)`; `:62-70` rollback on failure; `:73-74`
`rmtree(docs_prev)`.

**The swap performs no filtering.** The whole post-cleanup staging tree,
including every `_`-prefixed private directory, becomes the published `docs/`.
Only the previous `docs/` is destroyed.

Post-swap: `projects.py:2060` `replace_analysis_cache_from_rag_support` rebuilds
`output/{pid}/analysis_cache/` from `docs/_rag_support/phase2_analysis/*.md`
(`rag_support_package.py:310-336`; an empty package removes the stale cache so
incremental regen falls back to full); `:2178` writes `docs/run_metrics.json`;
`:2104-2110` RAG push of top-level `*.md` only; `:2123` `sync_internal_layers`
selects the five internal layers at `rag_support_package.py:376-407`.

Non-swap outcomes: `projects.py:2002-2041` blocking pre-swap gate keeps staging
without swapping; `:2208-2211` success-with-zero-docs `rmtree`s staging;
`:2302-2309` failure preserves staging for resume;
`atomic_swap.py:113-144 gc_abandoned_staging` deletes staging older than
`config.py:229 max_age_days`; `api/app.py:104` runs startup lock/staging recovery.

### 4.5 Measured sizes

Byte caps (`MCP/config.py`): `phase2_task_evidence_bytes` 48,000 (`:269`);
`phase2_task_context_bytes` 140,000 (`:272`); `phase2_synthesis_context_bytes`
140,000 (`:273`); `phase2_filtered_map_bytes` 16,000 (`:274`);
`phase2_dense_unit_bytes` 24,000 (`:260`); `phase3_target_evidence_bytes`
120,000 (`:279`); `phase3_filtered_map_bytes` 24,000 (`:290`);
`phase3_noncode_evidence_bytes` 40,000 (`:296`); `phase3_noncode_project_bytes`
120,000 (`:299`); `phase3_change_impact_view_bytes` 48,000 (`:310`);
`phase3_unit_digest_bytes` 150,000 (`:418`); `phase3_shared_context_file_bytes`
16,000 (`:445`); `topology_context_evidence_bytes` 300,000 (`:287`);
`max_prompt_bundle_bytes` / `max_ref_bundle_bytes` 200,000 / 80,000 (`:234-235`);
`agent_max_tool_bytes` / `agent_max_tool_result_bytes` 600,000 / 50,000
(`:331-332`).

None of these caps bounds `_facts/engineering_facts.json`, `entity_index.csv`,
`usage_index.csv` or `_topology/evidence/*.jsonl` -- the four largest survivors
are uncapped.

Deleted classes, measured on `knowledge_base/output/werkorder/ETTM/docs_staging`
(94M total, pre-cleanup):

| Class | Size | Files |
|---|---|---|
| `_analysis/` total | 11M | 1,473 |
| `_analysis/_context_packets/` | 6.4M | -- |
| `_analysis/_tasks/` (Phase 2 task evidence) | 1.97M | 139 |
| `_analysis/_claims/` | 1.1M | -- |
| `_analysis/{group}.md` | 662,845 B | 20 |
| `_analysis/_coverage_units/` | 80K | -- |
| `_analysis/_{coverage,depth,guide_sections}_report.json` | 60K | 3 |
| `_preprocess/` total | 1.9M | 31 |
| `_preprocess/*.md` group bundles | 1,887,541 B | 28 |
| `_transient/context_slices/` | 316K | -- |
| **deleted by Phase 4** | **~13.2M of 94M (14%)** | |

Second sample (`werkorder/EnterpriseServiceBus/docs_staging`, 76M): `_analysis`
9.5M, `_preprocess` 1.3M, `_transient` 264K -> ~11.1M (15%).

Surviving classes, measured on `knowledge_base/output/werkorder/Assetcheck/docs`
(107M, post-swap, run with `MCP_PIPELINE_DEBUG=1`):

| Class | Size | Detail |
|---|---|---|
| `_facts/` | **48M** | `engineering_facts.json` 34M, `entity_index.csv` 6.1M, `usage_index.csv` 2.9M, `config_runtime_map.json` 2.2M, `dependency_map.md` 1.2M, `change_impact_matrix.md` 816K |
| `_debug/` | **48M** | `model_inputs` 34M, `agent_tasks` 5.9M, `provider_calls.json` 3.7M, `_audit` 3.7M. Zero when `MCP_PIPELINE_DEBUG=0` |
| `_topology/` | 2.8M | `evidence/` 2.7M |
| `_agent_tasks/` | 2.3M | 84 `output.md` = 1,920,356 B |
| `_model_slices/` | 1.4M | |
| `_model_inputs/` | 1.3M | 171 `manifest.json` = 281,771 B; rest is block overhead on 342 files |
| **top-level `*.md` (the product)** | **1,304,934 B** | 10 docs, 49,785-274,580 B each |
| `_investigation/` | 848K | |
| `_rag_support/` | 628K | `phase2_analysis/` 15 files = 361,868 B; `_doc_pack_manifest.json` 228K |
| `_assessment/` | 164K | |
| `facts_manifest.json` | 112K | |
| `documentation_obligations.json` | 64K | |

Headline ratio: **the 10 published documents are 1.2% of the 107M published
tree.** `_facts/` + `_debug/` are 90%. Cross-checks:
`output/alitalend/docs` 8.2M (older run, engineering facts still at top level);
`output/IntodataStreams/docs` 12M (plugin-pack run, `_evidence_registry` 1.2M
survived).

### 4.6 The existing fingerprint, and what a persisted evidence layer needs

Function: `compute_scan_fingerprint(scan_output_dir: Path) -> str` at
`MCP/services/business_topology/evidence.py:76` (body `:84-102`).

Inputs -- `_SCAN_ARTIFACTS` at `evidence.py:17-22`, in order: `scan_result.json`,
`scan/manifest.json`, `scan/lineage.json`, `scan/landscape.json`.

Algorithm: SHA-256 (`:84`); per existing+parseable artifact,
`update(relative_path) + NUL + update(canonical_json(payload)) + NUL`
(`:96-99`), so artifact *removal* changes the hash; `landscape.json` passes
through `_factual_landscape` (`:70-73, 93-94`) which strips `semantic_topology`
so semantic overlays are deliberately excluded; canonicalisation is
`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` at
`:57-58`; unparseable/missing artifacts are skipped (`:88-92`);
`FileNotFoundError` if none found (`:100-101`).

**It hashes only the intodoc scan cache. It is not keyed on the doc catalog,
pack manifest, prompt templates, model, or any config knob.**

Persistence and staleness (`MCP/services/partition_orchestrator.py`): wrapper
`_scan_fingerprint(scan_output_dir) -> str` at `:390-396` swallows all
exceptions and returns `""` ("fingerprint is only a resume optimization");
computed once per orchestration at `:185`; path from `_partition_state_path`
`:399-400` = `upload_topology_dir(project_id) / "partition_state.json"`;
read/write `_read_partition_state` `:403-404` / `_write_partition_state`
`:407-410`; record shape at `:279-284` is
`{partition_name: {"scan_fingerprint", "doc_count", "run_id"}}`, written only
after a successful `swap_docs` plus child-run completion.

`_partition_completed(state, partition_name, fingerprint, part_output) -> bool`
at `:413-426`, three conjunctive conditions: (1) `if not fingerprint: return
False` (`:421-422`); (2) recorded fingerprint equality (`:423-425`); (3)
`_count_public_docs(part_output / "docs") > 0` (`:426`, helper `:429-432`).
Call site `:196`: `if _partition_completed(...) and resume:` -> skip.

Related uses of the same value: `partition_orchestrator.py:566` writes it into
`output/{pid}/topology/validation_report.json`;
`phase37_business_topology.py:121` uses `bundle.scan_fingerprint[:16]` as an
agent-task `target_id` so a resume cannot accept a stale topology overlay;
`api/routes/projects.py:3941, 4021` recompute it to invalidate cached
topology/curation (`:4040, 4078, 4101`); and
`services/business_topology/evidence_merge.py:34-37` composes it with a catalog
version into a cache marker -- **the only place in the codebase where the
fingerprint is combined with a code-version input, and the correct precedent to
copy.**

What a persisted evidence layer must store (the classes destroyed today with no
copy):

| Layer | Staging path | Measured (ETTM) | Note |
|---|---|---|---|
| Group source bundles | `_preprocess/{group}.md` | 1.89M / 28 | Deterministic from scan + redaction; regenerable only if the redaction/preprocess code version is in the key |
| Phase 1 task evidence | `_investigation/_tasks/` | 1.69M / 44 | Survives today by accident |
| Phase 2 task evidence | `_analysis/_tasks/` | 1.97M / 139 | Bin-packing depends on `phase2_task_evidence_bytes`, so the cap must be in the key |
| Phase 2 context packets | `_analysis/_context_packets/` | 6.4M | Largest deleted class |
| Phase 2 claims | `_analysis/_claims/` | 1.1M | The interpretation ledger; pack-specific, so cache per (scan, pack) not per scan |
| Group analyses | `_analysis/{group}.md` | 663K / 20 | Already persisted via `_rag_support/phase2_analysis/` |
| Doc packs | `_analysis/_doc_pack_*.md` | manifest 228K | Only the manifest survives; the packs do not. Pack-specific. |
| Phase 3 target evidence | `_analysis/_evidence_{doc}.md` | capped 120K each | Doc-scoped, therefore pack-specific |
| Validator reports | `_analysis/_*_report.json` | 60K | Read by the enterprise gate at `harness.py:5305-5306`, then deleted |

**Recommended cache key.** Split the layer in two, because only the first half
is pack-independent:

- **Universal extraction cache**, keyed on
  `sha256(compute_scan_fingerprint(scan) + preprocess_code_version + redaction_code_version + partition_unit_allowlist_digest)`.
  Contents: `_preprocess/{group}.md` and the top-level engineering-fact
  artifacts (or a pointer to `_facts/`, since those already survive the swap).
  This is what makes "a second pack runs without re-scanning or re-extracting"
  true. Budget ~2M per project for the preprocess bundles; the fact artifacts
  are already retained.
- **Evidence-shaping cache**, keyed on the universal key **plus**
  `phase2_task_evidence_bytes` and the compiled section-contract digest.
  Contents: `_analysis/_tasks/`, `_investigation/_tasks/`. ~4M.
- Do **not** cache `_analysis/_context_packets/` (6.4M, the largest class): it is
  a deterministic function of the task evidence plus per-run byte caps and is
  cheap to rebuild. Caching it buys disk cost, not calls.
- `_analysis/_claims/`, `_doc_pack_*`, `_evidence_{doc}.md` are pack-scoped
  outputs, not extraction. Keep them under a `(scan_fingerprint, pack_id,
  pack_content_hash)` key if at all -- that also gives the spec's section-9
  audit record (`pack_id + version + content_hash` on `GenerateRun`) a natural
  storage location.

Practical note: the fingerprint wrapper returning `""` on any exception
(`partition_orchestrator.py:390-396`) is correct for a resume optimisation and
**wrong for an extraction cache** -- an empty key must mean "do not cache", never
"cache under the empty key". State that invariant explicitly when the layer is
built.

---

## RISKS TO THE PLAN

Blunt. Each item either contradicts the spec or makes a stated step harder than
section 7 implies. Ordered by how much they would hurt if discovered during
implementation instead of now.

### R1. `mode: replace` on Talend fails *silently as stubs*, not loudly

`harness.py:3019-3024` raises, `_contain_phase2_group_failures`
(`harness.py:2024-2068`) catches, and every group becomes a PARTIAL stub while
the run reports success and publishes documents. Salvage-first publishing (added
2026-08 for good reasons) has turned a hard contract violation into a
green-run-empty-product failure. **Acceptance test 1 will pass a naive "2
documents, 4 sections" assertion while producing garbage.** The acceptance test
must additionally assert that `state.partial_groups` is empty and that no
document contains the PARTIAL stub marker.

### R2. Section 7's HARD count is off by ~5x, and one row is actively wrong

26 HARD sites versus the 5 the spec plans for. Two spec rows are wrong in
opposite directions:

- `harness.py:3788` is marked "presentation-tolerant, no change". It is HARD --
  it iterates the module constant, not its `section_names` argument, and falls
  back to dumping the whole analysis file.
- `validators.py:620` is marked "enforces all 13 with NO platform check --
  inconsistent with the other three; also fixes an existing bug". There is no
  bug. Line 620 is inside the Talend branch guarded by the early return at
  `validators.py:580`. Budget for the extra 19 sites; delete the phantom bugfix
  from the plan.

### R3. There is no existing pack -> section seam to extend

Spec section 6 maps `lens.sections.declare` -> `extra_section_names` as if the
wiring exists with one change needed. It does not. `extra_section_names` is fed
only by `_guide_section_names` reading the **scanner's** ANALYSIS_GUIDE, and
`PluginPackManifest` (`plugin_schema.py:153-165`) has no `lens`, no `sections`,
and no `docs[].phase2_sections`. Agent 2 is building a new seam, not flipping a
flag. Also: `steps: list[PluginStep]` is a **required, non-empty** field
(`plugin_schema.py:162`, validator `:166-173`), so a v2 YAML-only user pack with
no steps cannot validate as a `PluginPackManifest` today.

### R4. The 16-section contract is asserted in prompt assets, five inside the scanner

`data/prompt_templates/phase2_platform_sections/{talend,workato,lobster,generic}.md`
carry the 15-16 `### SECTION` skeleton and remain server-owned.
`intodoc/src/intodoc/platforms/*/prompts/ANALYSIS_GUIDE.md` (5 files)
carry the same contract, are described to the model as "the authoritative
reference", and are injected into every Phase 2 call including `bounded` mode
(`harness.py:6316-6338`). **Superseding decision:** platform semantics are an
immutable Nexus foundation layer, not a plugin override seam. Always promote
the guide to system policy; allow only additive plugin guidance under
`prompts/extensions/platforms/`, with Nexus winning conflicts. Task-specific
schemas continue to control response transport so the legacy guide's markdown
wording cannot override a JSON claim task.

### R5. `_plugin_mode` is a filesystem predicate; user packs have no filesystem

Every legacy-contract off-switch -- `require_sections`,
`enforce_default_contract`, `require_group_summary`, the
`09_EXECUTIVE_SUMMARY` drop, the doc-set validator, the Phase 3 tool policy --
is keyed on `self._plugin_mode`, i.e. `plugin_pack_dir is not None`, and
`plugin_pack_dir` is `Path(PluginPack.path)` from a git clone
(`services/claude_service.py:1301`, `api/routes/plugins.py:268`). Spec section 9
says user packs are DB-stored and in-app authored with **no ZIP upload**. Those
two facts are incompatible. **Decide now:** either (a) materialise a DB pack to a
per-run temp directory and keep `_plugin_mode` as-is, or (b) replace
`_plugin_mode` with an explicit "active pack contract" object and audit all ~15
call sites. Option (a) is cheaper and reuses the six existing path-based pack
override seams (`prompts.py:69,339`, `phase1_investigation.py:62`,
`harness.py:3209`, `prompts.py:214,276`); option (b) is cleaner. Do not discover
this in agent 3.

### R6. Two of the program's own substitute controls are broken for packs today

Spec section 9 lists "preview-and-score before activation" and the
`harness_pack_activation_snapshots` audit record as the non-optional substitutes
for dropping Git review. Neither works on pack-named documents:

- `services/doc_quality_scorer.py:39` computes `_PUBLISHABLE_DOC_KEYS` from the
  **default catalog at module import time** and filters candidate files by it
  (`:409-411`), returning `overall=0.0, docs=[]` for a pack that produces
  `INJECTION_FINDINGS.md`. The preview gate would score every user pack zero.
- `harness_pack_activation_snapshots` exists but is not wired to doc runs (the
  spec says this itself at `:377-379`).

Fix the scorer before, not after, the scope ladder ships.

### R7. Living-document reingestion is legacy-shaped end to end

`true_patch_executor.py:302` (`require_sections=True` hardcoded),
`:1163-1170` (drops pack sections), `:630-634` + `:1156-1160` (default catalog),
and `patch_section_map.py:43-67, 97, 131-145` (component->section and
section->doc maps, both legacy-only). This is a whole subsystem, on an
independent call path from the generation arc, and it is not mentioned in section
7 at all. **Recommendation: gate it.** Reject webhook reingestion for
pack-scoped projects with an explicit error until it is ported, rather than
shipping it silently wrong. Otherwise the first customer running a pack plus a
git webhook gets corrupted documents.

### R8. Five RAG push paths silently drop pack-named documents

`services/rag_bridge.py:514-519` gates on `name[0].isdigit()`. The primary
generation path compensates (`api/routes/projects.py:2098-2103`,
`:2361-2364`) and the partition path bypasses the gate, but
`true_patch_executor.py:831`, `git_diff_patcher.py:540`,
`evidence_doc_update.py:147`, `evidence_doc_revert.py:107` and
`mcp/server.py:504` do not pass `extra_doc_names`. On those paths a pack document
is dropped before `filename_filter` is applied -- no error, no log, `doc_count`
just wrong. Fix inside `_read_md_files` (glob non-underscore `*.md`), not by
asking six callers to remember.

### R9. Deleting `packs/fixtures/*.json` empties the capability registry silently

`config.py:232-233` + `packs/runtime.py:31-33` + `registry.py:234-237`: the
fixture directory is the fallback pack source and gates every capability the
platform exposes. Deleting those two JSON files during Wave 2 produces no import
error and no import-time test failure -- just a platform with zero capabilities.
Add an explicit assertion that `default_capability_registry()` is non-empty to
the test suite **before** Wave 2 starts.

### R10. The cross-service hash contract will bite during the schema migration

`MCP/schemas.py:757-773` and `MCP/services/nexus_execution_contract.py:70-74`
independently recompute the canonical hash of what
`packs/composition.py:399` produced. There is no shared module, so no static
analysis catches a field rename -- it surfaces only on a real document generation
as a boundary failure. `NEXUS_EXECUTION_RECEIPT_SCHEMA_VERSION`
(`nexus_execution_contract.py:23`) is the intended lever; bump it deliberately
and add a round-trip test that constructs a snapshot on the RAG side and
validates it with the MCP schema in the same test process.

### R11. Phase 2 coverage, not sections, may be the real blocker for narrow packs

`pipeline/phases/phase2_analysis.py:130-133` raises when
`coverage.coverage_count == 0`, and coverage is measured by whether unit names
appear in the analysis text (`validators.py:515-521`, `:636`). A SQL-injection
pack that finds no sinks in a group legitimately produces a body that names no
units -> zero coverage -> hard raise. This is a *unit-coverage* contract, not a
section contract, so it is entirely outside section 7's scope, and it will bite
exactly the acceptance-test-1 scenario. The protected core arguably should keep a
unit-coverage floor, but it needs a "declared-and-found-nothing" expression --
e.g. require the pack's output to enumerate assigned units with an explicit
no-finding statement. Decide the semantics before agent 2 writes the compiler.

### R12. Smaller things that still cost a day each

- `harness.py:176-182` defines `PHASE2_SECTION_NAMES` a **second** time; a
  contract threaded only into `analysis_parser.py` leaves three stale uses.
- `doc_catalog.load_doc_catalog()` (`doc_catalog.py:87-100`) is a process-wide
  mutable singleton with no pack-override path. `knowledge_base/CLAUDE.md`'s
  claim that "plugin packs can override this with their own `doc_catalog.yaml` at
  the pack root" is **false** -- no such code exists. Fix the doc as well as the
  code.
- `phase2_claims.py:93-114` -- three dead section tuples
  (`_DESIGN_SECTIONS`, `_OPERATIONS_SECTIONS`, `_RELATIONSHIP_SECTIONS`) that a
  grep-driven implementer will waste time "fixing".
- `_phase2_group_agent_legacy` (`harness.py:2569-2826`) is unreachable
  (`_phase2_group_agent` at `:2106` delegates straight to `_phase2_group_claims`).
  It contains one of the two `_complete_phase2_sections` call sites, which makes
  the blast radius of S9 look larger than it is.
- `scripts/bundle_analysis.py` has its own hardcoded 15-name list and is
  `shutil.copy2`-ed into staging on **every** run
  (`services/claude_service.py:778-781`) while only the legacy `claude_cli`
  provider executes it. Looks dead to a static search; is not.
- `MCP_PHASE2_TASK_EVIDENCE_BYTES` is documented as 70000 in
  `knowledge_base/CLAUDE.md` and is actually 48000 (`config.py:269`). Any cache
  key or budget estimate copied from the docs will be wrong.
- `retrieval/search.py:831-853` `search_layered` (layered-RAG rung 2) has no
  production caller. Unrelated to packs, but worth knowing before anyone claims
  pack output is deep-searchable.

### What would make me confident

1. Convert the S9/S10 raises into contract-driven checks **first**, in isolation,
   with a test that a 4-section Talend Phase 2 body is accepted and
   `partial_groups` stays empty. That single change is the difference between
   "replace works" and "replace silently stubs".
2. Make the prompt-asset injection contract-conditional (R4) in the same slice.
   Validators and prompts must agree or the model output will not satisfy either.
3. Decide R5 (`_plugin_mode` vs. materialised directory) before agent 2 writes
   the compiler, because it determines the compiler's input type.
4. Gate living docs (R7) rather than porting it in this program.
