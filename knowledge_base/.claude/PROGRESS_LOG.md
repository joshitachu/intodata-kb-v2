# Progress Log

**Invariant**: newest-first. Every entry is a `## Session: YYYY-MM-DD -- <title>` heading, and the newest entry is always at the top. Entries older than ~30 days are rotated verbatim to `archive/PROGRESS_LOG-pre-2026-08-05.md`. Last rotation: 2026-09-04 (cutoff 2026-08-05).

## Session: 2026-09-10 -- VW POC: the ERP koppelvlak's six missing exchanges analysed and documented

- **Files changed by this work**: all 16 files in `output/VW_FULL_LANDSCAPE/poc_werkorder/` (6.599 -> ~10.100 lines) and the generator in `output/VW_FULL_LANDSCAPE/topology/` (new `werkorder_streams_d.py`; edits to `build_werkorder_topology.py`, `werkorder_{constants,fields,findings,questions,rules}.py`, `README.md`; regenerated `business_topology.curated.json`, `werkorder_field_mappings.json`, `validation_report.json`). **Both trees are gitignored** (`knowledge_base/.gitignore` line 2 covers `output/`), so `git diff` shows nothing for this work -- the paths above are the record. No tracked file was changed.
- **Problem**: the client identified four Maximo <-> 4PS exchanges (invoice, material usage, purchase order, time spent) as belonging to the WerkOrder. The doc set covered exactly one exchange for that koppelvlak -- the work order itself -- so the ERP interface was understated by a factor of six and the "what does the work order cost" question had no answer.
- **Root cause**: the first pass scoped the ERP koppelvlak to the work-order object. The accounting exchanges carry the work order too (three provably), and one of them -- the settlement invoice -- carries the richest field contract in the estate. None of that was in scope, so none of it was read.
- **Change**: analysed seven exchanges field by field from the same six evidence tiers as the first pass, plus two new tiers (the client's own error-report automation, and the production task registry). Added 122 field rows across seven tables to doc 05, a new chain analysis (doc 04 keten 1b), and folded the results into all 16 documents. Extended the topology generator by seven streams: 41 -> 48 interfaces, 323 -> 445 field mappings, 20 -> 27 field tables, 29 -> 36 transformations.
- **Why this approach**: doc 05 is parsed straight out of the markdown by the generator, so the documents stayed the source of record and the generator's strict assertions caught every registration gap. Mappings were **executed** against the real captured production messages rather than only read -- that produced findings no reading gives, including the settlement invoice sending the originating record id where the work order number was available four times over, and a whitespace-padded value in one of four branches of a known, three-quarters-fixed bug. Delegated the doc propagation to writers with disjoint file ownership against one verified findings ledger; kept the field tables, the chain analysis and the runtime folding.
- **The mappings are now verified against the production population, not one sample each.** Technique: empty elements are self-closing so `match_phrase("NAME NAME", slop 25)` separates filled from empty, and `slop 4` hops the XML attributes that sit between tag and value; validated locally against a real message before running, with a per-lane calibration pair. Results closed **blocking question 7e** (`REFWO` filled on 40,5%/47,1% of purchase orders -- inkoop IS a work-order consumer), **disproved a stated Aanname** (the five unmapped statuses do not occur on the 4PS feed at all), and produced several findings that change the redesign picture: the WBS fallback is the dominant path (80,8%/90,7%), `PARENT` is NEVER filled so the invoice almost always names the melding, `REIS`/`NAARHUIS` never reach the 4PS hour feed, 56,5% of material messages are inventory mutations carrying no work order, `LOCATION` is 100% filled and unmapped while `ASSETNUM` is only 7,2% filled, and ~1.700 telephone numbers went to 4PS on invoices. Docs 03, 04, 05, 06, 08 (§1n, §1o), 09 and 11 updated. First attempt at this was WRONG (assumed `<X></X>` for empties) and its fill-rate column was discarded rather than published.
- **Corrected a standing claim**: the set said the 4PS chain uniquely does not log its message content. Measured per step, that holds only for the OUTBOUND write (315 bytes of envelope); the inbound Maximo message (4.465 bytes avg over 460.938 events) and the 4PS response (3.595) ARE stored, on all seven lanes. Narrowed in doc 08 §1b/§5, doc 04 keten 1 and the README; doc 08 gained §1m. Two consequences: 37,5M events carry content including personal data (doc 11 §18, with a retention/access list), and the doc 05 field mappings -- each resting on one captured message -- are now testable against hundreds of thousands of real messages, which would move them from Vastgesteld to Gemeten (doc 09 q20s).
- **Also measured**: per-lane error causes (material usage = 253 errors, ONE cause, 1 retried; work-order failures by Maximo status; status return failing on the Maximo side plus 15 `401`s), end-to-end latency (`_v2` purchase order 3x slower than the lane it replaced), monthly trend (status return and material usage both degrading since summer), and business rhythm (no month-end invoice peak; 22.216 weekend hour-bookings). Doc 08 gained §1h-§1l; three measured risks added to the topology.
- **Verification**: `python3 build_werkorder_topology.py` -> `validated: true`, 0 errors, 50 warnings (48 of the pre-existing "direction asserted by documentation" class -- 41 old + the 7 new streams -- plus 2 pre-existing). Frontend contract guard `npx vitest run tests/lib/curatedTopologyContract.test.ts` -> 5 passed, so the enriched overlay still parses against the `.strict()` browser schema. Doc-set sweeps written for this pass: 205 markdown tables column-consistent, 0 dangling cross-references, all 16 files ASCII-only, and a value-leak scan clean (the only hits are five Smartflow GUIDs that doc 10 already documents deliberately). One self-correction inside this pass is worth recording as a method note: an intermediate reading concluded the ERP routes ran ahead of the export, based on the route bundle number. That number is a publication counter and does not track the design version -- the child-job bundle lines (`<route>_<job>/<X.Y.0>`) do. Re-verified against the engine `mvn:` lines: **four of six chains run exactly the delivered version**; material usage runs 11.3 against a delivered 11.2; the invoice has no deployed bundle at all. The stored memory `tmc-mirror-what-it-holds` already recorded this trap and is what caught it.
- **The single most valuable measurement, and it was nearly missed**: the registry's business key has a DIFFERENT shape in every lane -- work order `999999`, time spent `9999999` (labour line), material usage transaction ids, purchase order `AA999999`, status return a 4PS GUID, invoice `AAA999999` on 0,7% of its messages. **Not one of the five accounting lanes correlates on the work order**, so "what did this work order cost" is unanswerable from 37,5M events. That turns the mapping-derived identity finding into a measured one and is the strongest redesign argument in the set (doc 08 §1h, doc 03 §D2a, doc 09).
- **Volumes: closed.** The Elastic estate is reachable after all -- not via `runtime_sources` but via the `elastic_stack` **connector** (`connector_instances`, Elastic Cloud, API key, `alitalend` bound). Measured over 2026-01-16..09-11 and folded into doc 08 §1e, doc 04 keten 1b §2e and the seven topology runtime slots. Two findings reversed earlier conclusions: **the invoice route does run** (9.594 events to 09-10, after its bundle teardown on 09-07 -- the task registry simply never recorded the reinstall), and **the purchase-order lane was cut over to `_v2` on 2026-09-07** after four months of overlap. The purchase order is the least reliable lane (4,70% falling to 2,27%), material usage fails six times as often as the work order (1,40%), and the status return's 0,02% is an artefact of its filters discarding 96% of what it reads (134.272 reads -> 4.923 writes). **Still open**: the outbound purchase-order header mapping is not in the delivered material (19 fields known, 16 sources unrecoverable); whether an XSLT 2.0 processor is registered on the integration server; and the correlation key is unusable as a document count on the invoice (18 keys / 9.594 events) and material usage (transaction ids, unset for goods receipts).


## Session: 2026-09-10 -- Independent goal-driven integration-intelligence pipeline

- **Files changed by this work**: `.plans/{goal-driven-agent-architecture,inspection}.md`; `.agent/decisions/031-goal-driven-investigation-is-independent-and-provider-neutral.md`; `mcp-server/data/methodology_packs/{base-integration,talend}/pack.json`; `mcp-server/src/mcp_server/providers/coding_agent/{__init__,base,factory,models,opencode,registry}.py`; `mcp-server/src/mcp_server/providers/goal_driven/{business_topology,domain,fact_graph,knowledge,mapping_rows,packs,projections,prompting,publication,refs,reingestion,repair,runner,scope,semantic_validation}.py`; `mcp-server/src/mcp_server/providers/opencode_cli.py`; `mcp-server/src/mcp_server/services/{claude_service,generation_profile,partition_orchestrator,scanner_service}.py`; `mcp-server/src/mcp_server/{config,schemas}.py`; `mcp-server/src/mcp_server/providers/base.py`; `mcp-server/tests/test_goal_driven/*.py`; `mcp-server/tests/{test_api/test_projects.py,test_services/test_generation_profile.py,test_services/test_partition_orchestrator.py,test_services/test_scanner_service.py}`; `dashboard/src/{api/client.ts,api/schemas.ts,components/GenerateDialog.tsx,types.ts}`. The wider dirty tree predates this work and is not claimed here; Talend scanner files are logged separately in `intodoc/.claude/PROGRESS_LOG.md`.
- **Problem**: the prior enterprise runner transformed complete source coverage into fixed reasoning batches, replayed the same context through many sessions, fragmented integration understanding and allowed structurally valid but shallow outputs.
- **Root cause**: deterministic fact extraction, scope selection, model scheduling, analysis products and document production were coupled. Unit/key counts determined model topology, while exact facts were reduced or repeated before writing.
- **Change**: added the separate `integration-intelligence-v1` route: complete parser-authoritative fact graph; goal classification and advisory scope seed; one coherent resumable provider-neutral investigation; naturally discovered OpenCode delegation only; methodology-only packs; canonical Integration/Journey/Interface knowledge; semantic, scope and publication gates; deterministic catalogue/dossier/topology/RAG projections; explicit cancellation and usage receipts; dependency-aware reingestion and fail-closed publication. Goal-driven runs bypass legacy preprocessing, phases and partitioned reasoning.
- **Why this approach**: complete deterministic parsing is cheap and trustworthy; deep reasoning is expensive and should follow the user goal. Keeping OpenCode behind an adapter permits later Codex/Claude Code/Hermes transports without rebuilding domain logic. Keeping exact facts outside the model-authored lane prevents truth drift and removes repeated evidence registries from client prose.
- **Verification**: goal-driven `44 passed`; OpenCode/goal-driven `116 passed`; affected MCP integration `252 passed`; intodoc full `669 passed, 7 skipped`; dashboard `tsc` clean and `159 passed`; Ruff and changed-scope invisible-character scan clean. Full MCP stopped at the pre-existing malformed GitHub App PEM after 88 passes. Default invisible scan found the same 46 pre-existing variation selectors under `knowledge_base/output/poly`. Paid ETTM/human semantic acceptance remains intentionally unrun.

## Session: 2026-09-10 -- Business topology carries field kinds, business rules, conditions and measured runtime (VW-first, plan slice 8)

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/services/business_topology/{models.py,validation.py,pruning.py,composite.py}`; `knowledge_base/mcp-server/tests/test_services/test_business_topology_structured_facts.py` (new); `knowledge_base/mcp-server/tests/test_api/test_vw_werkorder_curated_topology.py`; `RAG_pipeline/backend/src/topology/{models.py,tools.py}`; `RAG_pipeline/frontend/{types/landscape.ts,lib/architectureViewModel.ts,lib/integrationDetailTransform.ts,components/graph/ArchitectureTopologyCanvas.tsx,components/graph/IntegrationDetailCanvas.tsx}`; `RAG_pipeline/frontend/tests/{lib/businessTopologySchema.test.ts,lib/architectureViewModel.test.ts,lib/integrationDetailTransform.test.ts,lib/curatedTopologyContract.test.ts (new),components/ArchitectureFlowPanel.test.tsx (new),components/IntegrationDetailTabs.test.tsx (new)}`; `.plans/plan-method-enrichment.md` (slice 8 build status); gitignored client data `knowledge_base/output/VW_FULL_LANDSCAPE/topology/{build_werkorder_topology.py,werkorder_constants.py,werkorder_runtime.py (new),werkorder_rules.py (new),README.md,business_topology.curated.json,werkorder_field_mappings.json,validation_report.json}`.
- **Problem**: the topology viewer flat-listed every transformation mapping as one undifferentiated `source -> target` list and never rendered `mapping_type`, so a status value table (`GGK -> In Process`) read as a business decision. Underneath it, the schema had nowhere to put contract widths, conditions, verdicts, deployment agreement or measured runtime, so the builder concatenated all of them into one `description` string per stream (median 1,386 chars, max 2,715).
- **Root cause**: `business-topology-v1` modelled transformations as untyped mapping lists and had no `business_rules` collection at all; three strict schema mirrors meant nobody could add a field cheaply; `RuntimeObservation` required typed `observed_runtime` evidence that only a pipeline run mints, so an analyst could not publish a measurement; and the copilot's own mirror typed transformations as a bare claim, dropping `mappings` entirely.
- **Change**: additive OPTIONAL slots in all three mirrors (schema version unchanged): `ContractWidth`, `StreamCondition`, `DeploymentAgreement`, `RuntimeMetric`, `BusinessRule`, nine stream fields, `kind` + `target_field` on transformations, `expression`/`category`/`reaches_target`/`note` on mapping rows, `kind`/`metrics`/`source` on runtime observations. Validator gained rule-reference and statement errors plus three warnings (non-narrowing contract width, unnested value domain, unowned rule); pruner and composite merge carry rules through. Viewer gained a rebuilt flow panel and two L2 tabs (`velden`, `regels`), under the rule that a section renders nothing when its data is absent, and unclassified transformations stay under the old heading. `MAX_VISIBLE_RELATIONSHIPS` became an option with 18 still default plus a "N relationships hidden -- show all" button, because `hiddenRelationshipIds` had no consumer and the omission was invisible. The VW builder unpacked the description strings, split transformations by kind, and authored 16 business rules, 48 runtime observations (22 of kind `absence`), 3 conflicts and 13 artifact annotations.
- **Why this approach**: every field optional so the 22 other topology files on disk keep validating and no other customer's screen changes; the runtime tier is annotative by construction (verified: the minted evidence ids appear in zero claims outside `runtime_observations`, all 41 streams stay `inferred`, all 41 documentation-tier direction warnings remain), so measurements can be published without grounding a direction or promoting a system. Rejected: raising the relationship cap globally (changes every project) and inflating claim statuses to defeat the cap.
- **Follow-up the same day, after looking at it**: the first panel rendered every section expanded, which put ~4,500 characters in a 390px column and repeated the same fact up to four times (a 14-hour delay appeared in the purpose, the strip, the conditions and the risks). Redesigned to a short fact strip (contract funnel as one line, the headline measurement, key form, deployment verdict) plus `Disclosure` rows that are COLLAPSED by default and carry a count, so nothing is hidden without saying how much. Measured on the real overlay: default view is now 836 characters. Two regressions the tests caught while doing it: moving `protocols` / `integration_solutions` into a new "Techniek" section gave every LEGACY stream a section it never had (reverted to badges -- only genuinely new prose lives there), and making Delivery path conditional removed a pre-existing section plus its empty state from legacy streams (made unconditional again).
- **Second follow-up, same day**: removed the `Bronnen` row from the flow panel by request (the `document_refs` DATA stays on the projection and in the artifact -- it is provenance, and dropping it would make a claim unverifiable; it is simply not what a reader of that panel is after). Then fixed the implementing-design panel (`IntegrationDetailCanvas.tsx::DesignExplanationPanel`), which rendered ONE bordered card per mapping, each repeating the group label and a `type . origin` footer: a 28-mapping design printed the same two labels 28 times. New `MappingGroups` renders one compact two-column table per kind, with the label and origin stated once per group, a per-row annotation only where the row's type differs from its group's or the value does not arrive, the verbatim `expression` preferred over the bare source identity, and a `value_domain` group headed by the field it constrains. Also fixed a real mislabel: `topologyTerm(mapping.mappingType)` mapped `direct` to "Direct call" from the dependency-MECHANISM vocabulary, where that phrase means a direct call between systems, not a value copied unchanged. `break-all` became `truncate` + `title`, so tokens no longer split mid-word ("to4psJson/descriptio n"). New `tests/components/ImplementingDesignMappings.test.tsx` (3 cases) pins the grouping, the value-table nesting and the absence of "Direct call".
- **Third follow-up, same day -- the implementing-design panel could not explain itself.** Asked what "redacted" meant, how the mappings worked and what scanner field lineage was for, the screen answered none of the three. Two were rendering, one was a real bug. (1) `[REDACTED -- inloggegeven]` sat in the SOURCE column where a field name belongs, with its reason truncated mid-phrase, so it read as a field called REDACTED rather than "this mapping writes a hardcoded credential into the message and the value was withheld"; now an amber "value withheld" with the reason in full, never truncated. (2) The two groups (analysed transformation vs scanner-read wiring) sat in one undifferentiated list; each now states what it is and where it was read from, ONCE, via `MAPPING_GROUP_BLURB`. (3) **`lib/integrationDetailTransform.ts` joined `source_table` + `source_column` although the scanner already qualifies the column** (`source_column: "row5/WONUM"`, `source_table: "row5"`), producing `row5.row5/WONUM` on 734 of 2,574 lineage records -- fixed with `qualifiedColumn`. (4) `source_component` / `target_component` are populated on ALL 2,574 lineage records and were dropped by the transform, so a row had no answer to "where in the job does this happen"; now carried as `component` and shown. (5) A source that fans out (one timestamp into YEAR/MONTH/DAY/HOUR/MINUTE/SECOND) was six rows of the same fact; now one row stating how many fields it feeds. (6) `BC -> BC` printed a name twice with an arrow between; now "unchanged", said once. (7) Scanner lineage starts collapsed with its count visible -- it is the technical layer, not the reason the panel was opened. `tests/components/ImplementingDesignMappings.test.tsx` grew to 9 cases pinning all of it, including that the placeholder string never reaches the screen and that the duplicated table prefix cannot return. Frontend suite 1,174 tests / 1,168 pass (same 6 pre-existing), tsc clean, image rebuilt and verified in the container.
- **Fourth follow-up: two of my own "improvements" were wrong.** (a) Collapsing a same-name row to one column ("BC unchanged") broke the two-column alignment for the whole table, and scanning source against target down two columns is exactly why the table reads; every row is side by side again, including a field that crosses under its own name, and the per-row type moved from its own line onto the end of the mapping line so the grid keeps four aligned columns. (b) **The fan-out merge fused an interface's username and password into a single row** -- both curated rows carry the same redaction placeholder as their source, and my merge key was (source, type, note, component), so it read as one source feeding two fields. Merging is now restricted to `origin === "scanner_lineage"`: an analyst wrote each curated row deliberately, so two of them sharing a source label are still two facts; machine-read wiring is the only place a fan-out is genuinely one fact. Tests grew to 10, two of them pinning exactly these two regressions.
- **Fifth follow-up: the columns were unreadable.** Four columns in a 390px panel left ~110px per side, so `truncate` hid the end of nearly every real field path (`max:PLUSPCUS...` into `to4psJson/cu...`) -- a name you cannot read is worse than a taller row. New shared `components/graph/FieldPath.tsx` wraps a path at its OWN separators: CSS will not break after `/` or `:` unaided, and `break-all` splits mid-word ("descriptio n"), so the break opportunities are inserted explicitly as `<wbr>` after `/ . : _ >`; the full value stays in `title`. Applied in 8 places across both panels (the flow panel's Velden rows and value tables, the design panel's mapping rows, the value-table heading, and the wide L2 field table, which had the same `break-all`). The per-row type and the reach flag moved from a fourth column onto the annotation line beneath, returning that width to the two paths. Test added asserting a path is never truncated and carries break points.
- **Verification**: mcp-server `pytest -k "topology or landscape or composite"` -> 141 passed, ruff clean. RAG backend `pytest -k topology` -> 399 passed. Frontend `tsc --noEmit` clean, `vitest run` -> 1,165 tests, 1,159 passed; the 6 failures are pre-existing and proven unrelated (`TopologyPageCopilot.test.tsx` stubs both canvases at lines 118-121, so component edits cannot reach it; its cause is uncommitted work in `app/topology/page.tsx`). VW topology rebuilt twice: byte-identical (md5) and idempotent against the agent's final state; `validation_report.json` validated true / 0 errors / 43 warnings; 616,747 bytes, ASCII-only; independent credential sweep found 0 sensitive dict keys carrying a value and all 6 credential-suffixed mapping rows masked in BOTH `source` and `expression`. New `tests/lib/curatedTopologyContract.test.ts` parses all four curated overlays on this machine against the strict browser zod -> 5 passed, which is the only check a fixture cannot make.

## Session: 2026-09-09 -- VW WerkOrder doc set corrected from the TMC mirror and Elastic second pass (gitignored output; no tracked-file diff)

- **Files changed by this session** (all under gitignored `output/`, so absent from `git diff`; the surrounding dirty tree predates this session and is not claimed here): `output/VW_FULL_LANDSCAPE/poc_werkorder/{README,02_LANDSCHAP_EN_INTERFACEREGISTER,03_INFORMATIEMODEL_WERKORDER,04_KETENANALYSES,05_VELDMAPPINGS_COMPLEET,07_BUSINESSREGELS,08_GEGEVENSSTROMEN_EN_UITVOERING,09_RATIONALISATIE_EN_OPEN_VRAGEN,10_BRONVERWIJZINGEN,11_BEVINDINGEN_BEHEER_EN_SECURITY,12_ONTWERP_VERSUS_IMPLEMENTATIE,15_LANDSCHAPSOVERZICHT}.md`; agent memory `~/.claude/projects/.../memory/{vw-werkorder-poc-redirect,vw-elastic-esb-monitoring,tmc-mirror-what-it-holds,MEMORY}.md`. A background Opus subagent is rewriting `.plans/plan-business-object-method.md` into `.plans/plan-method-enrichment.md` (verdict PENDING) and archiving the old file; not finished at logging time.
- **Problem**: the 16-document set stated several things as unmeasurable or unknown (records per run, deployed versions, distinct work orders, ProRail storingen "probably stopped", FO 4.7 "active") while the runtime mirror (`project_id='alitalend'`) and the VW Elastic connector held the answers.
- **Root cause**: the first pass read only `runtime_job_runs` and aggregated Elastic on `ApplicationName`; it never opened `runtime_jobs.artifact_version`, `runtime_component_metrics`, the engine-log `mvn:` bundle lines, the `ClientReferenceID` business key, the `(none)` ApplicationName bucket (727k events keyed by `ProcessName`), or the `.properties` builder descriptions (2,054 of 5,942 filled). Absence claims were written without evidence of absence.
- **Change**: README scope limits rewritten (records per run and distinct work orders now measured; chain 7 outbound leg documented from v1.4 while PROD runs 3.1 since 2023-09-07); 02 par. 3d ProRail = deployed and active (route rebuilt 2026-03-05), par. 2 note 5 + 04 chain 4 + 05 + 09 + 15: FO 4.7 STOPPED (zero events in 8 months; builder note "moved to EttM/4PS"), successor `maximoFourpsTijdbesteding`; 02 par. 6 + 09 20b: 4.6_2 is a pure copy of 4.6 (union cardinality 17,589 == 4.6 alone); 08 new par. 1c (distinct work orders per chain and per month, key forms 6-digit WONUM vs 7-digit ASSIGNMENTID, 4.1 request/response reconciliation 9,026 of 9,048/9,060) and par. 1d (rows per run from component metrics, 8-week window); 12 new par. 9 (builder change-history tier, incl. the contradiction that five routes marked "DO NOT USE ANYMORE" carry all FO 4.2-4.5 traffic) and par. 10 (export-vs-deployment table: 6 equal / 6 export-ahead / 1 behind); 03 D1, 07 3.7, 10, 11 par. 1 and 8 updated accordingly; 09 new questions 20a, 20f-20i and next steps 6-7.
- **Why this approach**: correct wrong claims before adding; every number carries its window and label; builder notes are a separate labelled tier because they proved to be intent, not liveness. Rejected: attributing engine-log bodies to a route (99.9% of the 4.57M lines are `scope=engine`, `attribution_safe=false`; lines under one route's execution include other routes' calls) and comparing route artifact versions from `runtime_jobs` (a kar counter, not the Studio version).
- **Verification**: all 16 docs `str.isascii()` true; table pipe counts consistent in every inserted section; every new cross-reference (`paragraaf 1c/1d`, `paragraaf 9/10`, vragen 20a-20i) resolves; hostname/PII sweep found only the two pre-existing location references. Source queries: `psql` against `intodata-kb-v2-postgres-1` (runtime_jobs, runtime_component_metrics, runtime_log_events) and Elastic aggregations via the decrypted connector inside `intodata-kb-v2-mcp-server-1` (cardinality, date_histogram, terms; no values dumped).

## Session: 2026-09-09 -- Charge every model turn to the usage budget (UNCOMMITTED)

- **Files changed**: `mcp-server/src/mcp_server/providers/opencode_cli.py`; `mcp-server/tests/test_providers/test_opencode_cli.py`.
- **Problem**: reconciling the first enterprise run's token figure against its own log showed the two disagreed. `phase_state.json` recorded 1,586,524 processed tokens for the session; summing the 19 `model turn complete` lines the same session wrote gives 3,157,666. The first 11 turns sum to exactly the recorded figure.
- **Root cause**: OpenCode emits two shapes for the same fact, a top-level `type: "step_finish"` and a nested `part.type: "step-finish"`. `_live_event_summary` accepted both; the token, cost and turn accounting in `_execute_step` accepted only the first. On `opencode-go/gpt-5.6-luna` roughly half the turns arrive in the second shape, so every usage figure and every ceiling built on it -- `max_processed_tokens` per session, `total_processed_token_limit` per run, the reported cost, the turn ceiling -- read about half the truth. A resource limit that under-counts is not a limit, and plan decision 7 requires these ceilings to actually stop new work.
- **Change**: one `_is_turn_finished(event)` predicate, used by both the live summary and the accounting. Both shapes carry the same `part.tokens` payload and each turn appears once (verified: the 19 logged turns have strictly increasing, distinct `cache_read` values), so accepting both cannot double-count.
- **Verification**: `pytest -q tests/test_providers/test_opencode_cli.py tests/test_providers/test_opencode_analysis_bounds.py` -> `81 passed`, then `73 passed` on the extended file; `ruff` clean. New tests pin that both shapes are charged, that non-turn events are not, and that usage is read from either shape.
- **What the measured tokens actually were** (one deep-analysis session over 12 designs): 82% `cache_read`, 17% `cache_write`, 0.8% output, 0.1% reasoning. New context ever sent was 539,393 tokens; the model wrote 26,400. The metric is dominated by re-reading the same prompt prefix across 19 turns, so it bounds context churn rather than spend -- the charged half cost USD 0.118.
- **Budget consequence, from that one data point**: a full session is ~3.16M processed tokens against a 6M per-session default, only 1.9x headroom. `ETTM_ZIP_TEST` plans three partitions (50, 52, 40 designs); at 12 designs per session and four analysis phases that is roughly 63M, 63M and 50M processed tokens, so two of the three would trip the 60M per-run ceiling before writing, review or repair. The defaults need recalibrating against a real pilot; they are not yet evidence-based.

## Session: 2026-09-09 -- Enterprise lane: partitioned publication gate and unique session step names (UNCOMMITTED)

- **Files changed**: `mcp-server/src/mcp_server/services/partition_orchestrator.py`; `mcp-server/src/mcp_server/providers/custom/enterprise/phase_runner.py`; `mcp-server/tests/test_services/test_partition_orchestrator.py`; `mcp-server/tests/test_providers/test_enterprise_phase_runner.py`.
- **Problem**: the first real enterprise run (`ETTM_ZIP_TEST`, contract recorded in staging, model `opencode-go/gpt-5.6-luna` pinned by request) accepted one chunk of 12 designs, then died with `OpenCode target 'step:enterprise_deep_analyze_estate_initial_001' exceeds max_attempts=1` and 38 keys outstanding. Reading the state also showed that this project publishes through the partition orchestrator, which swaps unconditionally.
- **Root cause**: two defects in the enterprise work. (1) `EnterpriseRunner._session` derived its step name from `len(record.attempts) + 1`, but chunks inside one phase round run concurrently and every one of them reads that length before any of them appends its attempt, so all chunks in a round produced the SAME step name; `OpenCodeAttemptLedger.dispatch` permits exactly one dispatch per name and rejected every chunk after the first. The existing runner tests passed `ledger=None`, so the ledger boundary was never exercised. (2) `_run_generate_background` returns before the client-readiness gate when `plan_composite_generation` yields a plan, and `partition_orchestrator` called `swap_docs` on any successful sub-project run without consulting the verdict, so the fail-closed publication rule from plan slice 9 silently did not apply to composite uploads -- which is the class of defect the enterprise contract exists to remove. The single-run path was covered by a test; the partitioned path was not, because the synthetic fixture is single-root.
- **Change**: the runner now takes its session number from a lock-guarded monotonic counter, seeded on resume from the attempts already recorded, so every session has a unique dispatchable step name. The partition orchestrator loads `_assessment/publication_verdict.json` from each sub-project's staging before swapping; a failing verdict skips the swap and the RAG push for that sub-project only, preserves its draft, completes the child run with `run_verdict="held_client_readiness"` and a `manual_review:` reason naming the blocking findings, and leaves that sub-project's previously published documents live while healthy siblings still publish.
- **Why this approach**: publication is decided over the final staged product, so the check belongs at each swap site rather than inside the provider; duplicating the predicate was the alternative and it is what let the two paths diverge in the first place. A monotonic counter is used instead of making the step name include a chunk index because resume must not reissue a name the ledger has already counted.
- **Verification**: `pytest -q tests` -> `2563 passed, 10 failed`, the 10 being the same pre-existing set (GitHub App PEM, `opencode-go` catalogue drift, intodoc import, component_description fixtures); `ruff check` clean on every changed file. The new concurrency test was first confirmed to be worthless: it passed with the fix reverted, because the fake session never yields and the chunks serialised. After making the fake `await asyncio.sleep(0)` like a real subprocess, reverting the fix reproduces the exact production error (`OpenCodeAttemptError`) and restoring it passes. The new partition test proves a failing verdict holds one sub-project (no swap, no push, draft kept, `held_client_readiness`, parent run `failed`) while its sibling publishes over its previous documents. Rebuilt and restarted the container; both fixes confirmed live by inspecting the running source.
- **Measured cost signal from the failed run**: one deep-analysis session over 12 designs consumed 1,586,524 processed tokens for USD 0.118 on `opencode-go/gpt-5.6-luna`. `part1` holds 50 designs, so that phase alone is about 5 sessions per sub-project.

## Session: 2026-09-09 -- Enterprise Talend documentation method (talend-enterprise-v1) (UNCOMMITTED)

- **Files changed by this session** (the surrounding dirty tree predates it and is not claimed here): `.plans/{plan.md,inspection.md}`; `CLAUDE.md`; `.agent/README.md`; `.agent/system/mcp-server.md`; new `.agent/decisions/030-enterprise-talend-method-is-fail-closed.md`; `mcp-server/src/mcp_server/services/generation_profile.py`; new `mcp-server/src/mcp_server/services/investigation_method.py`; `mcp-server/src/mcp_server/services/rag_support_package.py`; `mcp-server/src/mcp_server/providers/opencode_cli.py`; `mcp-server/src/mcp_server/providers/opencode_artifact_mcp.py`; new `mcp-server/src/mcp_server/providers/custom/runtime_authority.py`; new package `mcp-server/src/mcp_server/providers/custom/enterprise/{__init__,layout,workspace,knowledge_model,knowledge_questions,phase_contracts,phase_runner,integration_projection,client_language,publication_policy,evaluation}.py`; `mcp-server/src/mcp_server/api/routes/projects.py`; new `mcp-server/data/prompt_templates/enterprise/*.md` (10 templates); new `mcp-server/data/investigation_methods/talend/{method.yaml,nexus-adaptation.md,profile.md,parsing-core.md,parsing-di-jobs.md,parsing-esb-routes.md,platform-behaviour.md}`; new `mcp-server/scripts/evaluate_enterprise_talend.py`; new tests `mcp-server/tests/test_providers/{enterprise_fixture,test_enterprise_workspace_model,test_enterprise_contracts_projection,test_enterprise_phase_runner,test_runtime_authority}.py`, `mcp-server/tests/test_api/test_enterprise_publication_gate.py`, `mcp-server/tests/test_services/test_investigation_method.py`, `mcp-server/tests/fixtures/talend_enterprise/expected.json`; `dashboard/src/components/GenerateDialog.tsx`; `dashboard/src/tests/{ep000-ui.test.tsx,api-client.test.ts}`. Scanner side logged in `intodoc/.claude/PROGRESS_LOG.md`.
- **Problem**: the Talend/OpenCode pipeline produced shallow documents despite high spend: per-job sessions, evidence-file-only reads, truncated mappings before writing, docs written from summaries, presence-only coverage gates, advisory quality gates, and internal registry paths leaking into client prose.
- **Root cause**: analysis was scheduled and bounded by evidence packets rather than by a durable knowledge model; access boundaries and scheduling records were the same object; publication never evaluated the final staged product.
- **Change**: new profile contract `talend-enterprise-v1` (`EnterpriseBudgets`, model pin or inherited route). `providers/custom/enterprise/` runs DEEP ANALYZE ESTATE -> RECONSTRUCT INTEGRATIONS -> BUILD KNOWLEDGE MODEL -> RECONCILE AUTHORITIES -> WRITE CATALOGUE/DOSSIERS -> REVIEW -> TARGETED REPAIR as durable per-phase ledgers with typed submissions through the artifact facade; every OpenCode session reads the complete digest-verified sanitized workspace (`_workspace/`) plus `_analysis/_exact/` tables; assignments are scheduling records only. Python assembles and validates the knowledge model (grounding into the workspace, full-estate completeness, exact-table interpretation, explicit unknowns), answers a frozen four-audience question set, projects `INTEGRATION_CATALOGUE.md` and top-level `INTEGRATION__<slug>.md` dossiers with exact mapping tables embedded verbatim, keeps provenance in `_evidence_registry/enterprise/` sidecars, and writes `_assessment/publication_verdict.json`; `_run_generate_background` holds the swap and RAG push on a failing verdict (`held_client_readiness`). The pinned Integration Suite Talend method (`0c68b22633a6d067fcc30e29dd18e86d9ea6d24f`) is staged read-only with sha256 verification; TMC plus optional Elastic aggregates fuse into `_analysis/_runtime_authority.json` where absence is `unavailable`. Dashboard gains the enterprise toggle. RAG gets an internal `knowledge_model` layer.
- **Why this approach**: extend the scanner and the OpenCode provider rather than add an orchestrator (plan decision 1); deterministic code owns exact facts and gates, the model owns interpretation (decisions 2, 6, 9, 12). Dossiers are top-level files instead of the plan's `integrations/` directory because seven publication consumers and the viewer route only enumerate top-level `docs/*.md` (ADR-030).
- **Verification**: `mcp-server .venv/bin/python -m pytest -q tests` -> `2560 passed, 10 failed` (the 10 are pre-existing: GitHub App PEM, `opencode-go` catalogue drift, intodoc import, component_description fixture; none import a changed module); enterprise suites `28 passed`, method+runtime `62 passed`, OpenCode/profile/landscape `143 passed`, `tests/test_api/test_projects.py` `51 passed`; `ruff check` on all changed files -> `All checks passed!`; dashboard `vitest` `29 passed`, `tsc --noEmit` clean; `git diff --check` clean. End to end: real `intodoc scan` of a synthetic two-root export staged through the enterprise runner with a fake model -> run complete, verdict `ok` (11/11 checks), catalogue + 1 dossier with both exact tables, 20 questions (business-owner ownership explicit unknown). The first real-scan grep caught `Password="..."` surviving in a sanitized connection item; fixed in `credential_filter.py` and covered by a test. NOT RUN: the paid pilot (plan slice 10).

## Session: 2026-09-08 -- Economical lossless OpenCode semantic scopes (UNCOMMITTED)

- **Files changed by this revision**: `.plans/{plan.md,inspection.md}`; `.agent/decisions/029-talend-analysis-uses-lossless-semantic-scopes.md`; `.claude/{context.md,PROGRESS_LOG.md}`; `mcp-server/scripts/plan_landscape_scopes.py`; `mcp-server/src/mcp_server/providers/{opencode_cli.py,opencode_landscape.py,custom/knowledge_contracts.py,custom/landscape_scopes.py,custom/document_reconciliation.py,custom/flow_analysis.py}`; `mcp-server/src/mcp_server/services/generation_profile.py`; focused tests under `mcp-server/tests/test_providers/` and `tests/test_services/test_generation_profile.py`. The large surrounding dirty tree predates this revision and is not claimed here.
- **Problem**: the canceled TEST_V2 run scheduled hundreds of fresh agent sessions over tiny atomic chunks. It had started 93 analysis jobs in roughly an hour and the 200-task cap would have silently excluded 444 of 644 tasks. Legacy model Gather also consumed time despite not feeding the new typed analysis.
- **Root cause**: source preservation granularity had incorrectly become model scheduling granularity. There was no coherent semantic packing layer, complete preflight, strict declared-read boundary, aggregate run budget, or honest distinction between retained evidence, grounded explanation coverage and semantic correctness.
- **Change**: retained the immutable atomic ledger and grouped it into bounded unit/concern semantic scopes ordered only by scanner-resolved same-group dependencies. Added paged declared evidence, complete evidence-union checks, review attestations, cumulative repair/resume, first-pass fairness, fail-closed scope preflight, session turn/time/all-token limits, run-wide time/token/reported-cost dispatch ceilings, interrupted-usage receipts, scoped source journeys and separate reconciliation/coverage semantics. New empty profiles normalize to analysis v3; persisted profiles without a version remain v2. V3 skips only the redundant model Gather pass.
- **Offline demonstration**: TEST_V2 part1 changes from 644 atomic tasks to 41 semantic scopes (93.6% fewer) for all 105 units. All 6,605 unique evidence records remain assigned and the evidence map is unchanged. There are 7,163 total assignments, including 558 disclosed repeats where a record supports multiple concerns/scopes. Zero model calls were made; quality and actual cost are unmeasured.
- **Verification**: changed-path Ruff and Python compilation pass; final focused suite `112 passed in 5.96s`; final scope regression `4 passed in 0.15s`; `git diff --check` passes; changed paths contain no invisible control/variation characters. Full MCP fail-fast suite stops at the existing GitHub App test (`1 failed, 87 passed`) because the configured PEM is malformed (`MalformedFraming`). Docker rebuilt/recreated MCP plus its backend dependency; both are healthy, port 8002 reports `status=ok`, and the running image reports analysis v3 with `semantic-scopes-v2`. Inspection verdict WARN: implementation is coherent and lossless, but a fresh paid pilot plus human semantic review is still required.

## Session: 2026-09-08 -- Dashboard opt-in for Talend landscape v2 (UNCOMMITTED)

- **Files changed by this session**: `dashboard/src/{api/client.ts,hooks/useProjects.ts,components/GenerateDialog.tsx,tests/api-client.test.ts,tests/ep000-ui.test.tsx}`; `.agent/system/dashboard.md`; `.claude/{context.md,PROGRESS_LOG.md}`; `.plans/inspection.md`. Other dirty-tree files predated this dashboard integration and are not claimed here.
- **Problem**: port 8001 could select OpenCode but could not activate the new versioned Talend contract, so a dashboard test silently submitted a NULL profile and exercised the legacy-compatible workflow.
- **Root cause**: the MCP request schema and generation service supported `generation_profile`, but the dashboard client/hook/dialog never represented or sent that field.
- **Change**: added an explicit Talend + OpenCode-only `Use Talend landscape v2 (experimental)` checkbox. Checked runs send `generation_profile: {}`, which the server normalizes and pins as `talend-landscape-v2`; unchecked and non-Talend runs omit the field and remain legacy. Added API serialization and dialog activation tests.
- **Why this approach**: explicit opt-in makes live comparison possible without prematurely changing the default before semantic/cost acceptance. The choice is carried through the existing typed client/hook boundary instead of bypassing it with a special request.
- **Verification**: dashboard `npx tsc --noEmit` passed; full Vitest suite `131 passed` across 11 files; focused client/dialog suite `27 passed`; `git diff --check` passed. Docker rebuilt/recreated dashboard plus dependencies; dashboard, MCP and backend are healthy, ports 8001/8002 return HTTP 200, and the served dashboard bundle contains the v2 control. Repository invisible-character scan still fails on the same 46 pre-existing variation selectors under `knowledge_base/output/poly`; changed dashboard files are clean.

## Session: 2026-09-08 -- Versioned Talend landscape evidence and grounded document generation (UNCOMMITTED)

- **Files changed by this session**: `.plans/{plan.md,inspection.md}`; `.agent/decisions/028-talend-landscape-generation-is-profile-pinned.md`; `intodoc/src/intodoc/{cli.py,models.py,platforms/talend/parser.py,scanner/walker.py,scanner/evidence_store.py}`; `intodoc/tests/{test_platforms/test_talend_parser.py,test_scanner/test_evidence_store.py}`; `mcp-server/alembic/versions/032_generation_profile.py`; `mcp-server/data/{doc_catalog.yaml,prompt_templates/phase2_analysis.md,prompt_templates/phase3_doc.md,prompt_templates/phase3_unit_doc.md}`; `mcp-server/src/mcp_server/{api/app.py,api/routes/generate_runs.py,api/routes/projects.py,db/models.py,providers/base.py,providers/custom/harness.py,providers/custom/partition_fingerprint.py,providers/custom/phase2_context.py,providers/custom/pipeline/phases/phase3_docs.py,providers/custom/target_evidence.py,providers/custom/validators.py,providers/custom/dependency_evidence.py,providers/custom/document_reconciliation.py,providers/custom/flow_analysis.py,providers/custom/knowledge_contracts.py,providers/custom/knowledge_requirements.py,providers/custom/talend_landscape_eval.py,providers/opencode_artifact_mcp.py,providers/opencode_cli.py,providers/opencode_evidence.py,providers/opencode_landscape.py,services/claude_service.py,services/generation_profile.py,services/generation_run_state.py,services/git_reingestion.py,services/partition_orchestrator.py,services/project_registry.py,services/scanner_service.py}`; `mcp-server/scripts/evaluate_talend_landscape.py`; fixture/tests under `mcp-server/tests/fixtures/talend_landscape/`, `tests/test_providers/{test_custom_provider.py,test_document_reconciliation.py,test_knowledge_requirements.py,test_opencode_evidence.py,test_partition_fingerprint.py,test_perf_regression.py,test_phase2_context.py,test_talend_landscape_eval.py,test_target_evidence.py}`, and `tests/test_services/{test_generation_profile.py,test_scanner_service.py}`. Other dirty-tree files predated this implementation and are not claimed here.
- **Problem**: generated Talend documents could lose mapping entries and routine behavior, treat bounded packet omissions as scanner failures, count skipped units as covered, conflate exported and deployed versions, and resume/reuse artifacts across implicit engine contracts.
- **Root cause**: the scan cache truncated or weakly identified some evidence; both engines consumed group-sized prose rather than independently addressable requirements; exact evidence, interpretation and document retention shared one vague completeness signal; generation configuration was not immutable run state.
- **Change**: added complete sanitized evidence pages and source accounting, root-qualified routine/dependency resolution, supported tXMLMap links, unbounded production TDM preservation, source-sized v2 requirements and exact-citation claims, durable OpenCode repair/resume, pre-writing source journeys, accessible reference documents, reconciliation and honest partial receipts. The same full caller routine source now reaches custom Phase 2. Added a strict `talend-landscape-v2` profile persisted through API, resume, partition children and Git reingestion, plus an offline acceptance evaluator.
- **Why this approach**: deterministic code preserves exact source structures and locators; models explain bounded obligations. OpenCode is the v2 execution profile, while scanner/reference/legacy evidence fixes are shared. NULL stays legacy and no default changes before measured acceptance.
- **Verification**: intodoc full suite `603 passed, 7 skipped`; MCP plan-scoped suites `275 passed`, `268 passed`, and final `232 passed`; MCP excluding three unrelated pre-existing/environment-dependent test files `2419 passed, 5 warnings`; compilation, reference-pack check and `git diff --check` passed. Full MCP `-x` attempts exposed the existing malformed GitHub PEM, unsupported `opencode-go/grok-4.5` catalogue entry and unavailable real component-description fixture. The mandatory invisible-character scan failed on 46 pre-existing variation selectors under `knowledge_base/output/poly`; the 130-file implementation scope is clean. No live scan, paid model call or deployment.

## Session: 2026-08-27 -- Multi-repo projects + per-repo incremental regeneration (UNCOMMITTED)

**Goal**: extend the single-repo GitHub / Azure DevOps paths to N repos under one
project, with re-ingestion incremental at the repo level (a push to repo-b
regenerates repo-b only).

**Key finding that shaped the design**: the expensive machinery already existed.
`services/partition_orchestrator.py` already runs one independent scoped
generation per sub-project into `output/{project_id}/{subproject}/docs` plus a
deterministic (zero-LLM) cross-project topology layer. Sub-project identity was
the only ZIP-specific part: `partition_planner.subproject_key` matched the
`.__intodoc_zip__` marker written by `_extract_zip_bundle`. Multi-repo therefore
needed a repo-origin marker, not a new pipeline.

### Files changed

- **NEW** `src/mcp_server/utils/source_origin.py` -- single home for origin
  markers (`.__intodoc_zip__`, `.__intodoc_repo__`), stem extraction, and
  `subproject_key`. `ARCHIVE_MARKERS` is separate from `ORIGIN_MARKERS` because
  only a zip has a raw archive sibling to exclude.
- `src/mcp_server/providers/custom/partition_planner.py` -- `subproject_key`
  delegates to the shared helper; `ZIP_MARKER`/`REPO_MARKER` re-exported.
- `src/mcp_server/services/lineage_service.py` -- `_strip_zip_marker` delegates.
- `src/mcp_server/providers/custom/noncode_evidence.py` --
  `derive_code_exclusions` excludes any origin subtree, but emits the raw
  `X.zip` prefix only for archive origins.
- **NEW** `src/mcp_server/providers/custom/partition_fingerprint.py` --
  per-partition scan digests.
- `src/mcp_server/services/partition_orchestrator.py` -- skip decision now reads
  the per-partition digest (`partition_fingerprint` in `partition_state.json`);
  whole-scan digest retained for diagnostics only.
- `src/mcp_server/db/models.py` -- **NEW** `ProjectRepoSource`.
- **NEW** `alembic/versions/031_add_project_repo_sources.py`.
- **NEW** `src/mcp_server/services/repo_sources.py` -- `RepoSourceRepository`
  (Repository pattern) + `MultiRepoSyncService`.
- **NEW** `src/mcp_server/api/routes/repo_sources.py` -- 5 endpoints under
  `/mcp/multi-repo/...`; registered in `api/app.py`.
- `src/mcp_server/schemas.py` -- multi-repo request/response models.
- **NEW** tests: `tests/test_providers/test_partition_fingerprint.py` (15),
  `tests/test_services/test_repo_sources.py` (15); 2 added to
  `tests/test_services/test_partition_orchestrator.py`.

### Problem -> root cause -> fix

1. **"Every run reprocesses everything"** (the stated core requirement).
   *Root cause*: `partition_orchestrator` computed ONE
   `compute_scan_fingerprint(scan_output_dir)` and compared every partition
   against it, so any change anywhere mismatched all of them.
   *Fix*: `compute_partition_fingerprints` hashes, per partition, only that
   partition's job artifacts -- located via the scanner's authoritative
   `manifest.job_files` index -- plus its `source_root`-scoped contexts.
   `scan/manifest.json` is deliberately EXCLUDED (it holds the project-wide call
   graph, so including it would re-couple every partition); `scan/landscape.json`
   too (derived from the job artifacts, so already covered).

2. **In-file edits would have been missed.** `UnitFact` carries no content hash,
   so a fingerprint built from the facts manifest alone would not move when a
   tMap expression changed -> stale docs published. *Fix*: hash the job JSON
   BYTES. Covered by
   `test_in_file_edit_is_detected_even_though_unit_facts_are_identical`.

3. **Collision-suffixed markers were mis-attributed** (pre-existing bug, found
   while generalizing). `_extract_zip_bundle` writes
   `X.__intodoc_zip__-<digest>` when two inner zips share a name, but
   `partition_planner.subproject_key` and `lineage_service._strip_zip_marker`
   both matched with `endswith(MARKER)`, which that form fails -- so those units
   fell into the `__shared__` partition and lineage showed the raw directory
   name. `noncode_evidence` used `in`/`startswith` and was unaffected.
   *Fix*: containment matching in the shared helper, documented as a constraint.

4. **A push to a member repo is silently ignored.** `_find_project_by_clone_url`
   (`api/routes/webhook.py:296`) skips projects with no `repo_url`, and a
   multi-repo project has `repo_url = NULL`. Not fixed -- see Known gaps.

### Known gaps (NOT implemented)

- **Webhook -> member routing.** `AutoDocUpdateService` does
  `fetch_remote(ctx.local_path)` and `git diff before..after` at
  `ctx.local_path`, which for a multi-repo project is the container directory
  with no `.git`. Needs `ctx.local_path` to become the member clone path. This
  is a CLAUDE.md critical area (MCP API routes) and requires `/inspect`.
  Until then, `POST /mcp/multi-repo/projects/{id}/sync?repo_identity=...` is the
  manual equivalent and is tested.
- **Dashboard multi-select UI.** Backend only. Both repo listings are already
  org-wide (`utils/azure_devops_api.list_repositories` spans all ADO projects;
  `GET /mcp/github/app/installations/{id}/repos`).
- **Shared routines cannot be repo-scoped.** intodoc's `Routine` model has no
  `source_root`, so a changed shared routine invalidates every partition
  (`UNATTRIBUTABLE_ARTIFACTS`). Over-invalidation is the safe direction; fixing
  it means adding provenance in the scanner.
- **`/inspect` not run.**

### Verification

- `pytest tests/ -q` -> **2177 passed / 12 failed**. All 12 reproduce
  identically on the stashed tree (`git stash push -u` baseline): missing
  `intodoc` module in the mcp-server venv, plus reference-pack and
  model-catalogue fixtures. Baseline before this session was 2162 passed / same
  12 failed.
- Migration 031 exercised directly against SQLite in both orders: `create_all`
  then `upgrade` (idempotent no-op, run twice), and cold `upgrade` on a bare
  `projects` table, then `downgrade`. First attempt FAILED with
  `NotImplementedError: No support for ALTER of constraints in SQLite`; unique
  constraints were moved inline into `create_table` as a result.
- `MultiRepoSyncService` tests drive real `git` against local `file://` origins:
  clone, no-op re-sync, `only=` filter leaving a sibling's working tree at old
  content, per-repo SHA recording, one failing member not stopping the others,
  and `--branch` pinning.

## Session: 2026-08-27 -- Multi-repo picker + Azure DevOps tab in the dashboard (UNCOMMITTED)

Follow-on to the multi-repo backend entry above. Brings the dashboard's Add
Repository dialog to the same UI as the RAG frontend's `/settings/sources`.

### Files changed

- **NEW** `dashboard/src/lib/repoSelection.ts` -- copy of the RAG frontend's
  module, with a header stating the two MUST stay identical. The dashboard and
  the RAG frontend are separate packages with no shared workspace, and the
  `repo_identity` rules must match the server on both surfaces; a divergence
  would send an identity no push event can resolve.
- **NEW** `dashboard/src/components/RepoMultiSelect.tsx` -- same picker,
  dashboard dark styling.
- `dashboard/src/components/AddRepoDialog.tsx` -- rewritten. Was 3 tabs
  (URL/Path, ZIP, GitHub App) with a single-select `<select>`; now 4 tabs with
  **Azure DevOps** added, and both connection tabs share the multi-select.
- `dashboard/src/api/client.ts` -- Azure DevOps connection methods (list,
  connect, test, delete, repos) + `createMultiRepoProject`.
- `dashboard/src/types.ts` -- `AzureDevOpsConnection`, `AzureDevOpsRepo`,
  `MultiRepoMember`, `MultiRepoSyncOutcome`, `MultiRepoProject`.
- **NEW** `dashboard/src/tests/add-repo-multi-select.test.tsx` (9 tests).

### Bugs found and fixed while building

1. **`api.addRepo` dropped the Azure DevOps connection.** Its parameter type had
   no `azure_devops_connection_id`, though `/mcp/add-repo` accepts one. Caught by
   `tsc`. Left as-is, a single-repo Azure DevOps add from the dashboard would
   have fallen back to an unauthenticated clone and failed as "Authentication
   failed" -- a credential error for a missing field.
2. **Refresh was a no-op.** The GitHub repo list loaded in an effect keyed on
   `appInstallationId`; Refresh re-set the same value, which does not re-run the
   effect. Extracted `loadAppRepos` and call it directly.

### Verification

- `npx tsc -b --noEmit` clean.
- `npx vitest run` -> **100 passed / 0 failed** (8 files). The pre-existing
  `add-repo-dialog.test.tsx` suite still passes unchanged, so the URL/Path and
  ZIP tabs behave exactly as before.
- Not verified in a browser; no live GitHub or Azure DevOps call was made.

## Session: 2026-08-27 -- Multi-repo membership panel in the dashboard (UI polish) (UNCOMMITTED)

Follow-on to the dashboard picker entry above, same date.

**Problem**: `ProjectMetaGrid.tsx:47` renders the Repo URL row only when
`project.repo_url` is set, and a multi-repo project has none, so its members were
invisible in Project Settings.

### Files changed

- **NEW** `dashboard/src/components/MultiRepoMembersPanel.tsx` -- mirrors the RAG
  frontend panel (per-member SHA + last-ingested time, per-member Sync, Sync all,
  Remove behind a confirm step), dashboard dark styling.
- `dashboard/src/components/ProjectSettings.tsx` -- renders it below
  `ProjectMetaGrid` when `source_type === "git"` and `repo_url` is null.
  Invalidates `["projects"]` on change via `useQueryClient`, matching how the
  project hooks already invalidate (there is no `onRefresh` prop to thread).
- `dashboard/src/api/client.ts` -- `listMultiRepoMembers`,
  `removeMultiRepoMember`, `syncMultiRepoProject`.
- **NEW** `dashboard/src/tests/multi-repo-members-panel.test.tsx` (10 tests).

### Verification

- `npx tsc -b --noEmit` clean.
- `npx vitest run` -> **110 passed / 0 failed** (9 files). 100 -> 110 passing.
- Not verified in a browser; no live provider call was made.

### Known UI gap (deliberately not fixed)

The pickers mark a repository "already connected" from `project.repo_url` only,
so a repo that is already a MEMBER of a multi-repo project is not flagged. This
is not a defect: the server's unique constraint is per project, and
`find_by_identity` returns a list precisely because one repo may belong to
several projects. Flagging it would need either an N+1 member fetch in the
picker or member clone URLs added to the project-list response.

## Session: 2026-08-27 -- Branch pinning + "already connected" was wrong to block (UNCOMMITTED)

Two operator-reported problems with the multi-repo picker, plus a hazard found
while fixing them.

### 1. "already connected" blocked re-adding a repository -- wrong

`Talend8` was disabled in the picker because it already belonged to a project,
so it could not also join a wider domain project. That block was inherited from
the pre-multi-repo `<select>` and is wrong now: the server's uniqueness is
per project (`uq_repo_source_identity` is `(project_id, repo_identity)`), and
`RepoSourceRepository.find_by_identity` returns a LIST precisely because one repo
may belong to several projects.

`PickableRepo` now separates the two ideas:

- `unavailableReason` -- a hard block, checkbox disabled. Only "no clone URL"
  and "disabled in Azure DevOps".
- `note` -- informational, still selectable. "already in a project" moved here.

### 2. Branch selection was missing from the UI

The backend had carried `branch` since the first multi-repo commit
(`ProjectRepoSource.branch`, `--branch --single-branch` on clone, ff-only against
`refs/remotes/origin/<branch>`), but no UI ever set it. Added a per-repo branch
field, shown only once a repo is selected so a ten-repo list stays scannable.

**Empty means unset, not "the current default".** `branchForMember` returns
`undefined` for a blank field, so the clone keeps following whatever the
provider's default becomes; typing a name freezes it. The placeholder shows the
default branch so the operator can see what they would get.

**`/mcp/add-repo` gained `branch`.** Without it, pinning a branch and selecting
exactly ONE repo would have silently cloned the default -- the single-repo path
is kept deliberately (it sets `repo_url`, which the push webhook matches on), so
it had to learn the same option. `schemas.AddRepoRequest.branch` +
`--branch --single-branch` in the clone call.

### 3. Hazard found: reusing a project id silently hijacks a project

`ProjectRegistry.add_project` UPDATES a project whose id already exists,
repointing `local_path`, `repo_url`, `platform` and `source_type`. Nothing
warned. Reusing a REPOSITORY is legitimate; reusing an ID is destructive, and the
picker was guarding the wrong one. Both surfaces now block an existing id with
an explicit message (`collidesWithExistingProject`).

### Files changed

- `mcp-server`: `schemas.py` (`AddRepoRequest.branch`),
  `api/routes/projects.py` (clone honours `--branch`).
- RAG frontend: `repoSelection.ts` (`note`, `branchForMember`,
  `collidesWithExistingProject`), `RepoMultiSelect.tsx` (note styling + branch
  field), both connect panels, `lib/mcpClient.ts`, `types/index.ts`,
  `app/api/source/github/route.ts`.
- Dashboard: `lib/repoSelection.ts` (re-synced), `RepoMultiSelect.tsx`,
  `AddRepoDialog.tsx` (branch state, branch on both paths, collision guard via
  `useProjects`), `api/client.ts`.
- Tests updated from "blocks" to "notes but allows"; new tests for branch
  pinning (multi and single path), branch field visibility, and id collision.

### Verification

- mcp-server `pytest`: 2178 passed / 12 failed (all 12 pre-existing).
- RAG frontend: `tsc` clean, 955 passed / 5 failed (the 5 pre-existing).
- Dashboard: `tsc -b` clean, **114 passed / 0 failed**.
- Rebuilt and redeployed `mcp-server`, `dashboard`, `frontend`; all 8 services
  healthy. Live OpenAPI confirms `branch` on both `AddRepoRequest` and
  `MultiRepoMemberRequest`. No errors in any container log.
- Still not exercised end to end against a live provider.
## Session: 2026-08-26 -- Deployment runbook re-synced (MCP-relevant parts)

- No `knowledge_base/` code changed. Logged here because two runbook corrections concern the MCP service directly. Files changed: `deploy/RUNBOOK.md`, `deploy/deploy-client.sh`, `docker-compose.yml`, `.env.production.template` (full detail in `RAG_pipeline/.claude/PROGRESS_LOG.md`).
- **MCP env changes need `docker compose up -d mcp-server`.** Step 8 of the runbook told operators to `systemctl restart mcp-server@<client>.service` after setting `MCP_PHASE2/3_MAX_TOKENS`. That unit (`deploy/mcp-server@.service`) is the retired host-virtualenv path (`$REPO_ROOT/venv/bin/python`), is never installed by `deploy-client.sh`, and contradicts the runbook's own statement that no host virtualenv is required -- so the documented step silently did nothing. Also note `restart` alone is not enough for an env change; it reuses the existing container environment.
- **New runbook section 15 documents doc-generation provider config**, which was previously undocumented for deployment: `MCP_DOC_GENERATION_PROVIDER` (`custom` | `<provider>/<model>` | `claude-cli` | `opencode-cli`), the hosted-model defaults (`anthropic/claude-sonnet-4-6` for analysis/write, `google/gemini-2.5-flash` for investigation, `openai/gpt-5.4` fallback, `repo_agent` deep-dive) and the fact that **no API key ships with the stack** -- a default deployment starts clean, passes health checks, then fails at the first generation run. Includes the local-only `ollama/<model>` alternative and the client-consent point about source code leaving the VPS. An unrecognised provider value raises at generation time, not at startup.
- Also recorded: packs are read from `$REPO_ROOT/knowledge_base/packs` (mounted at `/app/workspace/packs`, created by `deploy-client.sh`), `plugin.yaml` is the only creatable pack format, and `MCP_PACK_AUTHOR_MODEL` defaults to `kimi/kimi-k2.5` (needs `KIMI_API_KEY`).
- Verification: docs and script only; `docker compose config` passes. Nothing run against a VPS.

## Session: 2026-08-24 -- Direct interactive Pack Architect, UNCOMMITTED

### [Fix/Refactor]: Replace Pack-specific OpenCode with compact design plus deterministic rendering

- **Files changed**: `mcp-server/src/mcp_server/services/{pack_architect.py,instance_settings_service.py}`; `mcp-server/src/mcp_server/api/routes/plugins.py`; `mcp-server/src/mcp_server/config.py`; `mcp-server/tests/test_services/test_pack_architect.py`; `dashboard/src/{hooks/useInstanceSettings.ts,components/GenerateDialog.tsx,api/client.ts,api/schemas.ts}`; `CLAUDE.md`; Compose/environment settings.
- **Problem**: the Pack-specific OpenCode agent took roughly nine minutes and ten sequential Kimi calls for one Pack. The frontend proxy timed out first, even when MCP eventually produced a valid draft. Port 8001 separately failed Pack selection against a deleted RAG route.
- **Root cause**: OpenCode was oversized for a bounded declarative artifact and generated complete files through a tool loop. The caller used a synchronous transport contract and retained legacy organization/private workspace semantics instead of one Plugin Pack ID.
- **Change**: a turn is now saved immediately and run in the background. One direct configured Pack-model request returns a compact, validated complete design or one clarification question. MCP renders `plugin.yaml`, README, phase/document/platform guidance, and output example deterministically, swaps the workspace atomically, and runs canonical validation. Sessions expose stage/detail/timing/error state, cancellation, latest-draft recovery, and restart interruption handling. Legacy Pack OpenCode metadata/runtime state is retired; saving and document generation use the central personal/shared Plugin Pack store.
- **Why this approach**: the LLM owns semantic design while Nexus owns deterministic structure, safety, persistence, and validation. It eliminates repeated paid calls and boilerplate output latency without imposing arbitrary document or section counts.
- **Verification**: scoped MCP tests **64 passed**; RAG Pack integration **19 passed**; frontend Pack proxy tests **35 passed**; Python compilation, Ruff, frontend TypeScript, frontend production build, and dashboard production build passed. A deployed `kimi/kimi-k2.5` turn was accepted in **0.005s**, completed in **38.1s**, rendered **11 files**, and produced **0 validation errors**. All Compose services are healthy.

### [Feature]: Save AI refinements back to an existing personal Pack

- **Files changed**: `mcp-server/src/mcp_server/services/pack_architect.py`; `mcp-server/src/mcp_server/api/routes/plugins.py`; `mcp-server/tests/test_api/test_personal_packs.py`; Pack Workbench client/UI tests.
- **Change**: Architect sessions may be seeded from an owned Pack and retain `source_pack_id`. First-turn context includes the bounded current guidance files as well as the manifest. Saving stages the complete refined tree with its existing `.git`, validates and commits it, atomically swaps it into place, updates manifest metadata, and returns the same registry row.
- **Verification**: scoped MCP suite **66 passed**, including in-place identity and cross-owner privacy tests; frontend refinement/proxy suites **37 passed**; production build and deployed health checks passed.

## Session: 2026-08-24 -- Repo sync: every UNCOMMITTED entry above is now committed

### [Chore]: Commit and push the whole accumulated working tree to origin/master

- **Files changed (authored this session)**: `.gitignore` (repo root, +16 lines). NOTHING else was edited. The 681-file commit this session produced is *pre-existing* working-tree state authored by earlier sessions; it was staged and committed, not written here. knowledge_base accounts for 242 of those 681 paths -- `mcp-server/src/mcp_server` 55M/35A, `mcp-server/data/plugin_packs` 45D, `mcp-server/tests/test_connectors` 9A, `tests/test_providers` 5M/8A, `tests/test_services` 8M/6A, `tests/test_api` 6M/3A, `tests/test_utils` 2M, `tests/test_model_strategies` 1M, `tests/conftest.py` 1M, `mcp-server/alembic/versions` 5A (026-030), `mcp-server/data/prompt_templates` 1M/6A, `mcp-server/data/model_catalogue.json` 1M, `mcp-server/data/swap_pack_starter` 2M, five `mcp-server/plugin_packs/*` manifests 1M each, `dashboard/src/components` 7M/2A/1D, `dashboard/src/tests` 2M/3A, `dashboard/src/{hooks,api}` 2M each, `dashboard/src/{types.ts,lib,App.tsx}` 1M each, `OPENCODE_OPERATOR.md`, `CLAUDE.md`, `.agent/system/mcp-server.md`, `.agent/SOP/add-new-project.md`, and the three `.claude/` docs. Full verbatim list: `git show --stat 457622c`.
- **Problem**: the working tree held 741 uncommitted VS Code entries (586 porcelain entries / 681 commitable files) spanning at least six in-flight workstreams. Every PROGRESS_LOG session header from 2026-08-07 onward was suffixed `UNCOMMITTED`. Nothing was on origin, so there was no clean baseline to branch from or to split the repo from.
- **Root cause**: work accumulated across many sessions without a commit gate. Not a code defect.
- **Change**: staged all 681 files onto branch `chore/sync-local-working-state`, committed as `457622c`, pushed, then fast-forwarded `master` to it and pushed `master` (`a481cf3..457622c`, +96,619/-20,465). Working tree is now empty; `master` and `origin/master` are 0 ahead / 0 behind. **Every `UNCOMMITTED` marker in the session headers above is now stale -- all of that work is contained in `457622c`.** Note the 45 deletions under `mcp-server/data/plugin_packs` are the One Pack collapse retiring the governed pack stack, not accidental loss. Four classes of local artifact were deliberately excluded and added to `.gitignore` instead: `deploy/backups/` (Postgres pre-migration dump; the existing `.backups/` rule did not match this path), `mcp-server/data/opencode_cache/` (4MB regenerated model catalogue), `mcp-server/data/pack_architect_sessions/` (57 per-run session files), and three cloned pack working copies carrying their own `.git` (`pack-repos/nexus-baseline/`, `mcp-server/data/plugin_packs/swap-pack-starter__main__a4308c2e2a/`, `mcp-server/data/plugin_packs/users/`).
- **Why this approach**: one squashed consolidating commit rather than an attempt to reconstruct per-workstream commits -- the changes are interleaved across the same files, so a faithful split was not recoverable from the working tree. A branch plus fast-forward keeps the remote history linear and made the merge conflict-free. The nested-`.git` exclusions are mandatory, not stylistic: `git add` would have recorded those three directories as mode-160000 gitlinks with no `.gitmodules`, i.e. permanently broken references. `data/plugin_packs` itself stays TRACKED -- only the cloned working copies inside it are ignored.
- **Verification**: **no tests were run.** This is a state-capture commit and its correctness is unverified -- the commit message says so explicitly. What WAS verified: no secrets (diff and all 343 untracked files scanned for `sk-*`, `sk-ant-`, `ghp_*`, `github_pat_`, `AKIA*`, `xoxb-`, `AIza*`, PEM private-key headers -- zero hits); `.env.production.template` diff is comments and empty keys only; zero mode-160000 gitlinks staged; zero excluded paths staged; zero files left untracked; largest committed blob 220KB. Prior sessions' own verification results are recorded in their entries above and were not re-run.
- **Confidentiality note**: three new `.docx` files under `knowledge_base/deliverables/vw_werkorder_vwi/` (client integration analysis) are now committed and pushed. The repo is PRIVATE and 8 comparable deliverables were already tracked, so this matches existing practice -- but `.gitignore` lines 107-113 declare client POC material must never be committed. These sit outside the paths that rule names (`/POC_Werkorder/`, `/Assetcheck/`, `/*.zip`). Resolve before any repo split or visibility change.

## Session: 2026-08-24 -- TMC runtime evidence reaches generated documentation (UNCOMMITTED)

### [Feature]: Make Talend Management Console runtime data a first-class doc-generation input in BOTH engines

- **Files changed**: `mcp-server/src/mcp_server/providers/custom/runtime_evidence.py` (rewritten); `providers/custom/harness.py`; `providers/custom/target_evidence.py`; `providers/custom/phase3_slices.py`; `providers/custom/pipeline/helpers.py`; `pipeline/phases/phase3_docs.py`; `pipeline/phases/phase4_cleanup.py`; `providers/opencode_cli.py`; `config.py`; `connectors/domain.py`; `connectors/providers/talend_tmc.py`; `api/routes/connectors.py`; `api/routes/generate_runs.py`; `schemas.py`; dashboard `ConnectorsPage.tsx`, `GenerateDialog.tsx`, `RunHistory.tsx`, `api/client.ts`, `api/schemas.ts`, `types.ts`, `hooks/useProjects.ts`; new tests `test_runtime_evidence_wiring.py`, `test_run_connector_evidence.py`, `run-evidence-ui.test.tsx`; plan `docs/plans/TMC_RUNTIME_EVIDENCE_BUILD_PLAN.md`; `CLAUDE.md`.
- **Problem**: the runtime-evidence lane existed but was dead. `_load_runtime_evidence` early-returned unless `_per_job_doc_units` was non-empty, and that map is only populated when `MCP_DOCGEN_UNIT_DOCS_ENABLED=1` (default False) -- so a standard run shipped zero runtime facts, silently. OpenCode never fetched at all. Group/summary docs and Phase 2 saw nothing. Pressing Connect on the Talend Management Console row opened the Connect Elastic Stack form. `GET /mcp/generate-runs/{id}/connector-evidence` was called by the dashboard and did not exist.
- **Root cause**: runtime evidence was the ONLY deterministic evidence tier held as in-memory provider state instead of a staging artifact, populated inside one engine's phase driver. OpenCode reuses the CONSUMER (`_phase3_context_files`) and structurally could not reuse the producer. Separately, `editing` in `ConnectorsPage` was typed `ConnectorInstance | null | undefined` -- it carried the instance but not the provider, so the render site had nothing to dispatch on.
- **Change**: one producer (`_ensure_runtime_evidence`, exposed as `HarnessHelpersImpl.ensure_runtime_evidence`) writes three artifacts -- cache in `_transient/`, `_analysis/_runtime_profile.md`, `_assessment/runtime_evidence_coverage.json` -- and both engines call it (custom from `Phase3Docs.run`, OpenCode from a now-async `_prepare_run`). Units come from the facts manifest, sending BOTH `unit.name` (job_id) and `unit.display_name`; batched at 200 units and by URL byte length, orphans merged by INTERSECTION with failed batches excluded. Six-section project rollup attached to group/summary docs only. Per-unit table now leads with days-since-last-run, gates durations at n>=30, classifies cadence from server-supplied median gaps, drops plans. Dialog registry keyed on `provider_id` with typed `{provider, connector}` state; `managed_by` marks TMC as managed in Runtime sources; the missing run-evidence route implemented, partition-aware.
- **Why this approach**: producer-writes-files/consumer-reads-files is the pattern every other evidence tier already uses (`_cross_cutting_facts.md`, `_noncode_project_context.md`), so parity stops depending on discipline. Rollup is group-only because a project-wide failure census names other units' components and Phase 3 component-echo validates a unit doc against its own bundle -- attaching it there manufactures fabrication rejections and costs ~1.3M extra input tokens on werkorder.
- **Verification**: MCP suite **2090 passed, 10 failed** (all pre-existing: github-app x3, model-catalogue x2, intodoc import, claude-service, component-description x3). Dashboard **60 passed**, `tsc --noEmit` clean. Wiring tests A/B'd: 21 of 22 fail before the change. LIVE against werkorder with the rebuilt backend: two-key fetch **4 -> 6 matched** (recovers `Maximo2Quintiq_Workorder_Route`), rollup 11,982 bytes under the 12,000 ceiling and ASCII-clean, cadence 30 min / 1 h / 15 min, `RHM2Smartflow_Workorders` flagged 53 days silent, failure census **699/789 = 88.6% one `tMSSqlConnection_2` timeout**, zero customer identifiers, zero redaction flags, `scope_mismatch` correctly FALSE on a healthy 1.4%-coverage project.
- **NOT done**: `/inspect` was not run (rule 13 mandates it for harness phases + MCP API routes; the user directed that the LFE lifecycle be skipped this session, and a red-team design pass was substituted). No end-to-end paid generation run -- it destructively swaps a real `docs/` directory and awaits approval. Nothing committed.
- **Known limits recorded in CLAUDE.md**: the living-docs patch lane never reads `_phase3_context_files` so patched docs gain no runtime prose; the tool-enabled Phase 3 first pass discards context files; scoped runs keep a project-wide coverage denominator with a `run_scope` block noting the discrepancy.

### [Finding]: The runtime<->scan join is an unvalidated foreign key, and it is wrong for one live project

- **Problem**: scan dir `alitalend` holds the AssetCheck estate, whose 10 units match set-for-set the runtime jobs filed under `reverse_engineer_VW`; runtime `alitalend` holds a different 28-job workspace with no scan on disk. A doc run for that project attaches ~10%-correct evidence.
- **Change**: none -- deciding the correct binding is an operator call. The build makes it visible: `orphan_runtime_jobs` is reported and a `scope_mismatch` flag is raised from the runtime-side orphan ratio, stated as an observation and never as an accusation (a match-ratio rule would flag healthy werkorder at 1.4% and MISS alitalend at 10%).
- **Verification**: exact name matching resolves 100% where the binding is correct. Do NOT add a normaliser -- `AssetCheck_GetLocation` and `AssetCheck_GetLocations` are both real, separately-scheduled jobs.

## Session: 2026-08-23 -- Adaptive OpenCode session planning, UNCOMMITTED

> Scope note: the six source/test/operator files below already contained substantial user-owned uncommitted work. This entry covers only the adaptive batching additions and narrowly updated topology comments; it does not claim the full diff against HEAD.

### [Feature]: Size Gather and Analyze sessions from contract evidence cost

- **Files changed**: `mcp-server/src/mcp_server/providers/opencode_cli.py`; `mcp-server/src/mcp_server/config.py`; `mcp-server/tests/test_providers/test_opencode_cli.py`; `mcp-server/OPENCODE_OPERATOR.md`; `mcp-server/src/mcp_server/providers/__init__.py`; `mcp-server/src/mcp_server/services/claude_service.py`.
- **Problem**: a 105-unit/16-group partition scheduled 42 fresh Gather sessions and 17 fresh Analyze sessions because every internal contract or group was mapped one-to-one to an autonomous OpenCode session. Session count followed task count rather than actual context capacity, producing heavy startup overhead and long agent loops.
- **Root cause**: contracts correctly defined independent validation/resume obligations, but the executor also treated them as session boundaries. Concurrency was bounded, while session planning itself was not adaptive. Analyze additionally needed an explicit dependency barrier because its scorecard consumes the complete group-analysis set.
- **Change**: added deterministic contract input estimation from the serialized envelope, prompt, and declared evidence; stable ordered packing under `MCP_OPENCODE_BATCH_TARGET_INPUT_TOKENS` (80K default); over-target singleton batches; bounded Gather and Analyze batch concurrency; contract-level resume filtering before replanning; concise plan logs; and a separate scorecard session after the group-batch barrier. Produce is unchanged. Updated stale one-session operator and code descriptions.
- **Why this approach**: contracts remain the durable correctness unit while sessions become replaceable capacity containers. Project evidence volume now determines batch count, provider limits determine concurrency, and a later resume may repack unfinished work without depending on conversation history.
- **Verification**: `python3 -m py_compile` passed for changed Python modules; `tests/test_providers/test_opencode_cli.py` **59 passed**; owned `git diff --check` passed. Full MCP regression: **1,863 passed, 10 failed**; all ten failures are unrelated to this feature (GitHub App PEM/configuration, model-catalogue drift, missing `intodoc`, and unavailable real component-description fixtures). No live/paid generation was started.
## Session: 2026-08-23 -- Interactive Pack Architect, UNCOMMITTED

### [Feature/Refactor]: Replace one-shot Pack drafting with a persistent OpenCode agent

- **Problem**: Pack creation used a two-call one-shot model workflow that discarded incomplete drafts, imposed arbitrary document/file-count ceilings, and could not continue interactively. The Workbench therefore surfaced opaque failures such as “did not return a pack draft.”
- **Root cause**: `/mcp/plugins/draft` coupled generation, hidden repair, and strict final validation into one request. It used a separate Anthropic path and treated any missing canonical file as total failure.
- **Change**: Added owner-scoped, filesystem-persistent Pack Architect sessions backed by a workspace-restricted OpenCode agent using the configured RAG chat model (`kimi/kimi-k2.5` by default). Each turn returns the current files plus deterministic validation errors; the same OpenCode session resumes across turns and MCP restarts. Saving reuses the canonical personal Plugin Pack creation path. Removed `pack_author.py`, `/mcp/plugins/draft`, its schemas/tests, and the obsolete max-token setting. Extracted deterministic validation and removed arbitrary document, file, section, and audience-count caps while retaining technical payload/path/content safety limits.
- **Files changed**: `mcp-server/src/mcp_server/services/{pack_architect.py,pack_validation.py,plugin_schema.py}`; `mcp-server/src/mcp_server/api/routes/plugins.py`; `mcp-server/src/mcp_server/{config.py,models/instance_settings.py}`; Pack Architect tests and settings documentation.
- **Verification**: focused MCP suites **49 passed** and **47 passed**; Ruff clean. Rebuilt/restarted MCP. Authenticated create/reload returned 201/200. A real Kimi/OpenCode session continued through the same Pack and OpenCode session after restart and reached `complete=true`, revision 3, 8 files, and zero validation errors.

## Session: 2026-08-07 -- Agentic grounded doc pipeline repair (werkorder root cause), 7 slices, UNCOMMITTED

> Scope note: this branch carries extensive pre-existing uncommitted work (RAG_pipeline, dashboard, intodoc, `providers/custom`, business-topology service, openai_agents infra) that PREDATES this session and is NOT covered by this entry. The full 89-file `git diff --stat` is not all from this session. The work below is the agentic grounded doc-generation repair only, all under `knowledge_base/mcp-server/`. Nothing was committed.

### [Fix/Refactor]: Why werkorder produced near-empty docs, and the 7-slice repair

- **Problem**: The agentic/"grounded" doc run for `werkorder` (429 Talend designs) produced ~10 thin, count-only docs covering just 2 of 61 groups, with mappings/Excel ignored. Diagnosed two root causes: (1) staged evidence (full scan JSON + prepared Excel text) was written as `.bin` and the external OpenCode `read` tool silently REFUSED it as binary, so no phase ever saw a line of code; (2) the recipe `alitalend-legacy-equivalence` HARD-CODED exactly two analysis outputs, so only 2 groups were ever scheduled. Not a model or size limit (budgets huge and barely used). Legacy `providers/custom` is deep because Python deterministically PUSHES verbatim per-job evidence into the prompt; the agentic path made the model PULL via a tool that failed.
- **Root cause**: `.bin` suffix on decodable text staged for the OpenCode reader (Slice 1); fixed analysis role `owns` filenames in the recipe (Slice 3); evidence delivered as a structural FACT_LEDGER only, never verbatim code (Slice 2); silent topology failure + claim-checker matching numbers against the wrong subject (Slice 6); default model `deepseek-v4-flash` vs legacy `deepseek-v4-pro`, and resume keyed on fixed role names incompatible with dynamic fan-out (Slice 7).
- **Change** (7 sequential slices, each planned+built+verified in a dedicated subagent; orchestrated with handoff verification between each):
  - S1 reader: stage evidence with real text extensions via new `evidence_relative_suffix(media_type)`; never `.bin`; re-indent large JSON to dodge the reader's 2000-char line truncation.
  - S2 Phase 1: new `unit_evidence.py::UnitEvidenceExtractor` (adapted from legacy `target_evidence.py`, no cross-import) emits verbatim per-unit code/SQL/tMap/context; staged into the investigation packet; evidence-hierarchy prompt (CODE=truth, RUNTIME unavailable->unknown, mappings=Tier-3 context that never overrides code).
  - S3 Phase 2: new `analysis_batch_planner.py` (union-find coupling graph over same-group|caller->callee|shared-connection, byte-budget bin-pack, boundary manifests, coverage gate); recipe analysis stage de-hardcoded to a `fanout: evidence_batches` template expanded by `expand_grounded_fanout`.
  - S4 Phase 3: two-tier docs -- exhaustive per-unit docs (`unit-doc-writer` `${unit}` template) + the 10 summaries; new `unit_tiering.py` prod/test classifier (errs toward production); `BatchPlan.assert_production_covered` hard gate.
  - S5 mappings: new `PdfProbe`/`XlsProbe` (redaction-safe, through existing `_redact`), loud+configurable size caps (`skipped_files`), new `mapping_association.py` routing mappings to matched units + summary-tier fallback (never dropped).
  - S6 topology+review: `_safe_role_failure_detail` surfaces the real reason for optional-role failures (no more silent topology); claim-checker subject-scoping (`_exact_subject` case-sensitive, `_call_clause_end`, anchored project-total patterns) removes ~681 false warnings while keeping real ones; topology role budget 16->64 MiB.
  - S7 cross-cutting: default `deepseek-v4-pro` via `MCP_AGENTIC_GROUNDED_DEFAULT_MODEL_ID` (grounded runs no longer 400 on omitted model); resume keyed on per-role batch-content hash (`RESUME_STATE.json`); transient-only bounded retry (`_is_transient_runtime_error`) + loud required-role failures.
- **Files changed (this session only)**:
  - New source modules: `mcp-server/src/mcp_server/experiments/application/{unit_evidence.py, analysis_batch_planner.py, unit_tiering.py, mapping_association.py}`
  - Edited source (several are large pre-existing dirty files where this session added the specific capabilities above, not the whole diff): `mcp-server/src/mcp_server/experiments/application/{grounded_evidence_enrichment.py, prepare_supporting_evidence.py, run_staged_grounded_candidate.py, fact_ledger.py, grounded_pack.py}`; `mcp-server/src/mcp_server/api/routes/agentic_experiments.py`; `mcp-server/src/mcp_server/config.py`; `mcp-server/pyproject.toml` (added `pypdf`, `xlrd`, dev `xlwt`); `mcp-server/data/agentic_recipes/alitalend_legacy_equivalence/recipe.yaml`
  - New tests: `mcp-server/tests/agentic_experiments/{test_grounded_evidence_enrichment.py, test_unit_evidence.py, test_s3_analysis_fanout.py, test_s4_unit_docs_fanout.py, test_s5_mapping_association.py, test_prepare_supporting_evidence.py}`
  - Edited tests: `mcp-server/tests/agentic_experiments/{test_fact_ledger.py, test_staged_grounded_candidate.py, test_grounded_pack.py, test_agentic_config.py}`; `mcp-server/tests/test_api/test_agentic_experiments.py`
- **Why this approach**: keep the agentic "orchestra" (roles, topology, review, recipes, mappings) but adopt legacy's proven strength -- deterministic Python extraction of verbatim evidence PUSHED into the prompt -- rather than rebuild on legacy (which lacks topology/review/mappings). Depth is evidence-engineering + coverage, not model spend (legacy and agentic both run deepseek).
- **Verification**: per-slice unit/integration tests, each verified independently by the orchestrator (files present + targeted pytest). Final cumulative `tests/agentic_experiments/ + tests/test_api/test_agentic_experiments.py` = **479 passed, 1 failed** (the failure is the known pre-existing live-OpenCode-500 preflight test, confirmed against base HEAD). Full repo sweep during S7 = **1850 passed, 4 failed** (all pre-listed known-unrelated: live-OpenCode-500, model-catalogue drift, two `intodoc`-not-installed). NOT YET validated by a live end-to-end run -- the true proof (werkorder through the live OpenCode/deepseek stack on `pro`) needs the running services and is the outstanding step.

## Session: 2026-08-05

### [Feature]: Connect the grounded agent workflow to project supporting evidence

- **Files changed**: `mcp-server/src/mcp_server/api/routes/agentic_experiments.py`; `mcp-server/src/mcp_server/experiments/application/{prepare_supporting_evidence.py,run_staged_grounded_candidate.py}`; `mcp-server/src/mcp_server/experiments/infrastructure/sources/prepared_directory.py`; `mcp-server/src/mcp_server/experiments/infrastructure/openai_agents/{grounded.py,scoped_evidence.py}`; focused agentic/API tests.
- **Problem**: Grounded candidates froze scanner facts but treated the managed project workspace as unreadable. Scope sheets, XML samples, mappings, and notes could therefore not safely enrich an agent run.
- **Change**: Added a generic evidence-preparation pipeline that inventories the workspace, dynamically excludes scanner-owned source roots from `files_index.json`, redacts safe supporting derivatives before persistence, writes an immutable receipt tied to the raw-file hashes, and exposes only role-scoped `list/search/read/traverse` tools to OpenAI Agents. Safe UTF text and hardened OOXML containers are prepared; unsupported formats are receipted as unsupported rather than silently ignored. Reviewer execution now waits for the topology role, so it can validate accepted topology evidence.
- **Invariant**: `scan_result.json` and `lineage.json` remain implementation truth. Supporting files and runtime exports are additive evidence only; they cannot overwrite scanner facts. Legacy generation is unchanged. Grounded runs remain non-publishing candidates until an explicit review/promotion action is added.
- **Verification**: focused/full selected MCP suite **428 passed**; Python compile, Ruff, and `git diff --check` passed. A real `werkorder` preparation produced **85** redacted supporting records, excluded **9,503** scanner-owned source files via dynamic roots, and receipted unsupported/over-limit files. Dashboard tests/build passed (**24 tests**). No paid provider run, Docker rebuild, or runtime connector call was made.
- **Remaining**: configure an approved OpenAI-compatible route and run a live candidate; add an explicit candidate-promotion path for published docs/topology; add governed TMC/Elastic runtime connectors separately. The `cast` project-tracking CLI was unavailable in this shell, so this record is the durable implementation log.

### [Fix]: Install the OpenAI Agents SDK in the MCP production image

- **Files changed**: `mcp-server/Dockerfile`.
- **Problem**: Selecting Nexus grounded — OpenAI Agents returned HTTP 500 with `ModuleNotFoundError: No module named 'agents'`.
- **Root cause**: `openai-agents==0.19.3` was declared as an optional project dependency, but the Dockerfile installed only `.` rather than `.[openai-agents]`. The UI could therefore advertise a configured route that the running MCP image could not compose.
- **Change**: Build the MCP image with its declared `openai-agents` extra and assert `import agents` during the image build.
- **Verification**: focused OpenAI runtime/API suite **41 passed**; lint and diff checks passed. Rebuilt/recreated MCP is healthy and the live container imports `agents` version **0.19.3**. No provider generation was triggered.

### [Fix]: Freeze actual IntoDoc scanner outputs for grounded runs

- **Files changed**: `mcp-server/src/mcp_server/experiments/application/scanner_evidence_receipt.py`; `mcp-server/src/mcp_server/api/routes/agentic_experiments.py`; focused API tests.
- **Problem**: A normal IntoDoc `redaction_report.json` proves that scanner redaction ran, but it is an audit summary rather than the closed file-hash receipt required by the project-evidence snapshot. Grounded OpenAI startup therefore marked the required `implementation` source unreadable and recipe compilation returned `required source role is unavailable: implementation` before any provider call.
- **Change**: Grounded snapshots now derive a companion immutable receipt from the already-approved redacted scan output. It binds the exact scanner artifacts the run freezes and is excluded from the artifacts it authorizes. Legacy/non-grounded source snapshots continue to use their existing report path unchanged.
- **Verification**: API regression tests passed for both the real IntoDoc report and explicit OpenAI grounded route; lint and compilation passed. The real `werkorder` scan freezes as a readable `implementation` source with **441** scanner artifacts. MCP was rebuilt/recreated and `/health` is healthy. No provider generation was triggered.

### [Fix]: Distinguish credential references from unredacted secret material in grounded preflight

- **Files changed**: `mcp-server/src/mcp_server/experiments/application/grounded_preflight.py`; `mcp-server/tests/agentic_experiments/test_fact_ledger.py`.
- **Problem**: The first real `werkorder` grounded candidate stopped before the provider was contacted. The source gate treated every sensitive-looking field name in scanner/prepared JSON as a credential leak, including already-redacted or explanatory Talend context evidence.
- **Change**: The gate now blocks only concrete secret-like assignments, user-info URLs, and private-key material. Ambiguous sensitive references are retained as value-free preflight warnings (`secret_warning_count` and categories), so the audit trail remains visible without rejecting a valid redacted scanner snapshot.
- **Invariant**: Opaque secret values still fail closed and error messages continue to expose only categories and logical paths, never the secret itself.
- **Verification**: full fact-ledger/preflight suite **21 passed**; selected grounded API snapshot suite **2 passed**; Ruff, Python compilation, and diff checks passed. Rebuilt/recreated only the MCP service; its health endpoint returned OK. No provider generation was triggered.

### [Policy]: Treat grounded source credential findings as warnings

- **Files changed**: `mcp-server/src/mcp_server/experiments/application/grounded_preflight.py`; `mcp-server/tests/agentic_experiments/test_fact_ledger.py`.
- **Decision**: The project owner requested that credential findings in frozen source evidence no longer hard-fail a grounded candidate. The preflight now preserves value-free warning counts/categories and continues; it does not echo credential values. The generated-output safety gate remains unchanged.
- **Verification**: preflight tests **3 passed**; full fact-ledger suite **21 passed**; Ruff, compilation, and diff checks passed. MCP rebuilt/recreated with the new policy and `/health` returned OK. No provider generation was triggered.

### [Fix]: Reconcile composite Talend artifact kinds and label grounded runtime failures correctly

- **Files changed**: `mcp-server/src/mcp_server/experiments/application/fact_ledger.py`; `mcp-server/src/mcp_server/api/routes/agentic_experiments.py`.
- **Problem**: The first post-credential-policy run stopped before any model call with three reconciliation errors. The ledger treated 26 joblets and 8 routelets as ordinary jobs/routes and compared duplicate display names as if they were stable identities. The API also reported every candidate exception as an “OpenCode” failure, including OpenAI Agents runs.
- **Change**: Prefer the scanner’s explicit `artifact_kind`, reconcile the 429 composite designs as 209 jobs + 186 routes + 26 joblets + 8 routelets, compare composite manifest identity indexes by count instead of unstable display names, and label failures using the selected runtime (`OpenAI Agents` or `OpenCode`).
- **Verification**: Full fact-ledger suite **21 passed**; focused grounded API suite **2 passed**; the frozen real `werkorder` snapshot now passes ledger preflight with the exact scanner totals and all four artifact kinds. MCP rebuilt/recreated and `/health` returned OK. No provider generation was triggered.

### [Fix]: Raise grounded role packet capacity for large composite projects

- **Files changed**: `mcp-server/data/agentic_recipes/alitalend_legacy_equivalence/recipe.yaml`.
- **Problem**: The first real 429-design grounded run stopped during deterministic workspace preparation with `grounded physical role packet budget exceeded`. The evidence role received a 52,867,936-byte packet while its recipe limit was only 8,388,608 bytes. No model/provider request was made.
- **Change**: Raised the practical packet ceiling to 1 GiB per role and 8 GiB for the aggregate recipe/source budget. This removes the artificial physical-packet blocker for large projects while retaining the independent model input-token, tool-call, elapsed-time, and cost safeguards.
- **Verification**: Recipe compilation succeeds; selected grounded suite **51 passed**. The failed project's measured evidence packet is **52,867,936 bytes**, well below the new practical ceiling. A Docker rebuild and fresh run are pending.
