# Layered RAG — Slice 6 Evaluation Report

- Generated: 2026-06-13
- Git commit: 41028fd
- Project: `phase2`
- Fixture: `/tmp/ev/eval_fixtures/layered_rag_phase2.json`
- Access level: `['admin']` (admin-level eval)
- Mode: both
- Cases: 10

## Config values at run time

| Setting | Value |
|---|---|
| `default_threshold` | 0.3 |
| `rerank_top_n` | 30 |
| `rung2_prefetch_limit` | 150 |
| `rung2_rerank_top_n` | 75 |
| `rung2_entity_match_limit` | 25 |
| `rung2_section_expansion_limit` | 40 |

## Retrieval metrics

- recall@30 (pre-rerank): 100.0% (3/3)
- recall@8 (post-rerank): 100.0% (3/3)
- source-layer accuracy: 100.0% (5/5)

## Ladder metrics by category

| Category | Pass | Total | must_pass | Avg ms |
|---|---|---|---|---|
| final_doc_answerable | 2 | 3 | 2/2 | 16608 |
| phase2_only | 1 | 1 | 0/0 | 3760 |
| unsupported | 2 | 2 | 2/2 | 6466 |
| entity_lookup | 2 | 2 | 1/1 | 9001 |
| risk_config_dependency | 2 | 2 | 0/0 | 5305 |

### Case detail

| ID | Category | Result | Layer | Fallback | DocGap | ms | Sources |
|---|---|---|---|---|---|---|---|
| fd-external-systems | final_doc_answerable | PASS | canonical_docs | False | False | 10702 | 02_COMPONENT_INVENTORY.md (Project: phase2), 08_CHANGE_PLAYBOOK.md (Project: phase2), 09_EXECUTIVE_SUMMARY.md (Project: phase2) |
| fd-architecture | final_doc_answerable | PASS | canonical_docs | False | False | 25332 | 03_DATA_FLOWS_AND_LINEAGE.md (Project: phase2), 01_ARCHITECTURE_MAP.md (Project: phase2), 00_AGENT_BRIEF.md (Project: phase2) |
| fd-security-risks | final_doc_answerable | FAIL | — | False | False | 13789 | 07_QUALITY_SECURITY_RISK_SCORECARD.md (Project: phase2), 09_EXECUTIVE_SUMMARY.md (Project: phase2), 00_AGENT_BRIEF.md (Project: phase2) |
|  | ↳ primary layer None != expected 'canonical_docs' ||||||| 
| entity-logdir | entity_lookup | PASS | support_maps | True | True | 9824 | Support Map: config_runtime_map (Project: phase2), Support Map: change_impact_matrix (Project: phase2), Support Map: dependency_map (Project: phase2) |
| entity-setglobalvar | entity_lookup | PASS | canonical_docs | False | False | 8178 | 02_COMPONENT_INVENTORY.md (Project: phase2), 01_ARCHITECTURE_MAP.md (Project: phase2), 03_DATA_FLOWS_AND_LINEAGE.md (Project: phase2) |
| p2-extract-props | phase2_only | PASS | canonical_docs | False | False | 3760 | 03_DATA_FLOWS_AND_LINEAGE.md (Project: phase2), 04_BUSINESS_RULES_AND_TRANSFORMATIONS.md (Project: phase2), 01_ARCHITECTURE_MAP.md (Project: phase2) |
| rcd-connection-params | risk_config_dependency | PASS | canonical_docs | False | False | 4214 | 01_ARCHITECTURE_MAP.md (Project: phase2), 02_COMPONENT_INVENTORY.md (Project: phase2), 00_AGENT_BRIEF.md (Project: phase2) |
| rcd-die-on-error | risk_config_dependency | PASS | canonical_docs | False | False | 6396 | 04_BUSINESS_RULES_AND_TRANSFORMATIONS.md (Project: phase2), 03_DATA_FLOWS_AND_LINEAGE.md (Project: phase2), 06_OPERATIONS_TESTING_AND_DEBUGGING.md (Project: phase2) |
| unsupported-kafka | unsupported | PASS | — | False | False | 6992 | 05_RUNTIME_CONFIG_AND_EXTERNALS.md (Project: phase2), 04_BUSINESS_RULES_AND_TRANSFORMATIONS.md (Project: phase2), 08_CHANGE_PLAYBOOK.md (Project: phase2) |
| unsupported-salesforce | unsupported | PASS | — | True | False | 5939 | — |

## Slice 5 inspection metrics

- Alias rows for project: 567
- Alias-match avg time: 8.1 ms
- Ladder latency — rung1 avg 10339 ms | rung2 answer avg 9824 ms | refusal avg 6466 ms | worst 25332 ms

## Verdicts

- PASS: recall@8 100.0% (gate 90%)
- PASS: source-layer accuracy 100.0% (gate 90%)
- PASS: operator must-pass set 5/5
- PASS: unsupported refusals with zero factual leak (2/2)

## Notes

- Eval project is phase2, not Koopmans: phase2 is the only project with internal layers and entity aliases synced to RAG; Koopmans predates Slice 2 and has no internal layers on disk.
- VERDICT (LLM verifier): WARRANTED for unsupported/refusal scoring. Substring forbidden-matching cannot distinguish 'X is configured' (fabrication) from 'no X is configured' (refusal) — a correct Kafka refusal tripped the literal check. Retrieval + layer + answer-substring scoring is reliable; only the refusal-fabrication gate needs an LLM judge. Recommended as a Slice 7 follow-up, off by default.
- VERDICT (_doc_pack_* indexing): NOT WARRANTED. recall@8 and source-layer accuracy are 100% on the golden set without indexing _doc_pack_/_bundle_ artifacts; the four internal layers already cover entity, config, risk, and dependency questions. Revisit only if a larger multi-group project shows misses.
- VERDICT (config tuning): NONE. Default threshold 0.3 and rung-2 recall settings yield 100% recall@8 and layer accuracy; no tuning applied. Worst-case ladder latency 24s is the architecture map (large canonical synthesis), not a rung-2 cost.
