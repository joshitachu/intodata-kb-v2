# Project Memory

## Architecture Decisions

| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| 1 | Platform-explicit (--platform flag) | Auto-detect is fragile; platform-specific prompts are far more accurate | 2026-02-16 |
| 2 | Credential filter before parsing | Raw text redaction prevents accidental credential exposure at any stage | 2026-02-16 |
| 3 | Shared Pydantic models in models.py | Single source of truth for data shapes across all platforms | 2026-02-16 |
| 4 | hatchling build system | Simple, modern Python packaging with pyproject.toml | 2026-02-16 |
| 5 | Multi-layer distiller filtering | Name + prefix + substring + value + field_type filters catch noise at every level | 2026-02-17 |
| 6 | ProjectManifest in every chunk | LLM needs cross-job context (~675 tokens) for accurate analysis | 2026-02-17 |
| 7 | Hand-written recursive descent parser (no grammar library) | Grammar is small (~15 productions), 85% handled by regex fast path, avoids dependency | 2026-02-18 |
| 8 | IR models in separate ir/models.py (not in shared models.py) | IR is platform-agnostic and decoupled from Talend-specific scan models | 2026-02-18 |
| 9 | MCP-based doc generation replaces CLI analyzer for documentation | Claude Code agent via MCP is more flexible, produces higher quality docs, eliminates API key management and budget tracking overhead. CLI analyzer + Jinja2 templates retained as legacy path. | 2026-02-19 |
| 10 | Landscape facts are deterministic, provenance-backed scanner output | L1/L2 rollup must preserve exact source-root/artifact/component evidence and separate control-flow, data, integration, and config dependencies. Redacted/dynamic identities never prove that two systems or channels are the same. | 2026-07-29 |
| 11 | Redaction captures safe structure without retaining sensitive values | URI filtering may retain scheme/category/auth mechanism/TLS/host-presence only. Opaque transport markers are parser-internal, must be consumed before persistence, and the verifier rejects any residual marker or sensitive value. | 2026-07-29 |

## Lessons Learned / Gotchas

| Date | Lesson | Context |
|------|--------|---------|
| 2026-02-16 | Use hatchling not setuptools | pyproject.toml build system |
| 2026-02-16 | Replace Unicode chars with ASCII for Windows | Click help text with em-dashes/arrows breaks on cp1252 |
| 2026-02-17 | tMap distiller must strip noise BEFORE chunking | Token bloat caused LLM budget overruns |
| 2026-02-17 | ENDPOINT should NOT be stripped — REST/OData URLs are semantically important | Was accidentally removed by STRIP_SUBSTRINGS |
| 2026-02-17 | Connection matching must use component REPOSITORY_PROPERTY_TYPE refs | Region-name matching was unreliable |
| 2026-02-18 | intodoc .agent/ dirs were empty scaffolding | README.md listed files that didn't exist — always verify |
| 2026-02-18 | Root .gitignore was missing | Only RAG_pipeline had its own; root tracked everything |
| 2026-02-18 | Prompt files named pNN_name.txt but get_prompt() expected pNN.txt | Glob matching `pNN_*.txt` fixes this -- descriptive names are better |
| 2026-02-18 | tMap internal flows don't use Talend connection model | JOIN steps from tMap get orphaned_step warnings -- expected, not a bug |
| 2026-02-18 | ConnectionDefinition uses connection_id not name | Test fixtures must use the actual field name -- always check model definitions |
| 2026-02-19 | Always update .agent/ and .claude/ docs after architecture changes | Docs had drifted significantly -- still described old LiteLLM/Jinja2 pipeline after MCP agent was in use |
| 2026-07-29 | A `str`-backed Enum must be handled before `str` in recursive Pydantic walks | Otherwise `FileType` is silently coerced to plain strings and Pydantic serialization emits one warning per scanned file |
| 2026-07-29 | Context-driven endpoint descriptors require parser-time marker consumption | Post-filter normalization is too late to recover URI structure; attach descriptors per environment while parsing and propagate them only through exact `context.Variable` references with agreeing facts |

## Completed Tasks

| Task | Milestone | Date |
|------|-----------|------|
| Project scaffold (directories, pyproject.toml, docs) | M1 | 2026-02-16 |
| Talend plugin + scanner | M2 | 2026-02-16 |
| LLM client + chunker | M3 | 2026-02-16 |
| Pass 1 end-to-end | M4 | 2026-02-16 |
| Pass 3 data flows | M5 | 2026-02-16 |
| Passes 2, 4-9 | M6 | 2026-02-16 |
| Synthesis + 16 templates | M7 | 2026-02-16 |
| PDF + Qdrant export | M8 | 2026-02-16 |
| Git analyzer (M9 partial) | M9 | 2026-02-17 |
| Distiller token reduction | Post-M9 | 2026-02-17 |
| Cross-job connections (6 fixes) | Post-M9 | 2026-02-17 |
| Passes p11-p18 (8 additional) | Post-M9 | 2026-02-17 |
| Phase 0: Cleanup + knowledge base | P0 | 2026-02-18 |
| Phase 1: Docs quality + migration readiness | P1 | 2026-02-18 |
| Phase 2: Universal IR (9 modules, 62 tests, 99.4% parse rate) | P2 | 2026-02-18 |
| Document 25: Preventive Analysis + architecture doc updates | Post-P2 | 2026-02-19 |
### Talend topology identity invariants (2026-07-30)

- Use the XMI project ID in `talend.project` as the stable imported-project
  identity. A ZIP/source-root path is provenance and drill-down scope, not an
  application identity.
- Talend `LABEL` is human presentation; `UNIQUE_NAME` remains the stable
  component identity for connections, lineage, and drill-down.
- A component category or transport such as Hash, REST API, JMS Broker, or File
  endpoint does not prove a peer system identity. Keep it as L2 mechanism or
  interface evidence until an endpoint, repository connection, context identity,
  supplied business registry, or other explicit fact establishes the peer.
- Direct and direct-vm channel names are route-local implementation addresses,
  never cross-project business-exchange identity. Only shared transports with
  cross-root identity evidence, such as an exact JMS destination, may create a
  cross-application technical edge.
- Semantic systems/exchanges are additive proposals over raw scan evidence.
  They must carry exact artifact/component provenance and explicit status and
  confidence. They never replace technical facts or become `confirmed` without
  supplied business evidence or durable human curation.
