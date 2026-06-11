"""Evidence agent resolution and registry artifact writing.

This module keeps pack-specific evidence behavior declarative. Plugin packs can
declare which evidence agents they want; the harness resolves those declarations
and writes normalized evidence claims for downstream phases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from mcp_server.providers.custom.debug_artifacts import debug_dir, debug_write
from mcp_server.providers.custom.evidence_bundles import (
    CATEGORY_PATTERNS,
    SUPPORTED_EVIDENCE_CATEGORIES,
    EvidenceBundleBuilder,
)
from mcp_server.providers.custom.models import ModelOptions, ModelResult
from mcp_server.providers.custom.providers import ModelProvider


@dataclass(frozen=True)
class ResolvedEvidenceAgent:
    id: str
    source: str
    prompt: str
    evidence_profile: str | None
    categories: list[str]
    inputs: list[str]
    output: str = "evidence_registry"
    model: str | None = None
    concurrency: int | None = None
    prompt_path: Path | None = None


# Maps evidence category → doc names that benefit from those claims.
# Used to write per-doc slices in _evidence_registry/doc_slices/ so Phase 3
# receives only relevant evidence instead of the full registry or brief.
_CATEGORY_TO_DOCS: dict[str, list[str]] = {
    "source_summary": ["00_AGENT_BRIEF", "01_ARCHITECTURE_MAP", "02_COMPONENT_INVENTORY"],
    "routes": ["00_AGENT_BRIEF", "01_ARCHITECTURE_MAP", "02_COMPONENT_INVENTORY"],
    "handlers": ["00_AGENT_BRIEF", "01_ARCHITECTURE_MAP", "02_COMPONENT_INVENTORY"],
    "sql_queries": [
        "03_DATA_FLOWS_AND_LINEAGE",
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
        "07_QUALITY_SECURITY_RISK_SCORECARD",
        "08_CHANGE_PLAYBOOK",
    ],
    "orm_entities": [
        "03_DATA_FLOWS_AND_LINEAGE",
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
        "08_CHANGE_PLAYBOOK",
    ],
    "ddl_schema": [
        "03_DATA_FLOWS_AND_LINEAGE",
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
        "08_CHANGE_PLAYBOOK",
    ],
    "db_connections": [
        "01_ARCHITECTURE_MAP",
        "03_DATA_FLOWS_AND_LINEAGE",
        "05_RUNTIME_CONFIG_AND_EXTERNALS",
    ],
    "config_references": [
        "00_AGENT_BRIEF",
        "05_RUNTIME_CONFIG_AND_EXTERNALS",
        "06_OPERATIONS_TESTING_AND_DEBUGGING",
        "07_QUALITY_SECURITY_RISK_SCORECARD",
        "08_CHANGE_PLAYBOOK",
    ],
    "auth": [
        "05_RUNTIME_CONFIG_AND_EXTERNALS",
        "06_OPERATIONS_TESTING_AND_DEBUGGING",
        "07_QUALITY_SECURITY_RISK_SCORECARD",
    ],
}


BUILTIN_AGENT_PROMPTS: dict[str, str] = {
    "builtin/core_architecture_dependency": """You are the core architecture and dependency evidence agent.

Goal:
Find concrete evidence about application structure, entrypoints, components, handlers, routes, orchestration, dependencies, and cross-component calls.

Evidence to capture:
- Named components/modules/classes/jobs/services and what they do.
- Entry points, routes, controllers, handlers, listeners, callbacks, or scheduled jobs.
- Internal dependencies between components.
- External libraries, services, APIs, queues, files, or systems referenced by the code.
- Architectural gaps that are directly evidenced, such as unclear ownership or missing entrypoint coverage.

Rules:
- Only write evidence that is visible in the supplied files.
- Include source file or chunk references for every claim.
- Prefer precise component names over generic summaries.
- Mark anything uncertain as UNKNOWN instead of guessing.
""",
    "builtin/core_data_flow": """You are the core data-flow evidence agent.

Goal:
Find concrete evidence about how data enters, moves through, transforms inside, and leaves the system.

Evidence to capture:
- Source systems, target systems, intermediate stores, tables, files, topics, queues, and APIs.
- SQL queries, joins, filters, table names, field names, ORM entities, and DDL references.
- Transformations, mappings, validations, enrichment, filtering, aggregation, and routing rules.
- Lineage gaps where the source or destination is not evidenced.

Rules:
- Only write evidence that is visible in the supplied files.
- Preserve table, field, query, and function names exactly.
- Include source file or chunk references for every claim.
- Do not infer lineage that is not directly evidenced.
""",
    "builtin/core_runtime_config": """You are the core runtime and configuration evidence agent.

Goal:
Find concrete evidence about how the system is configured, deployed, connected, and operated at runtime.

Evidence to capture:
- Environment variables, config files, property keys, secrets references, connection strings, database endpoints, API URLs, and runtime flags.
- Scheduling, retry, timeout, batch size, concurrency, logging, and error-handling settings.
- Environment differences such as dev/test/acc/prd when directly evidenced.
- Missing or unresolved runtime assumptions that affect documentation.

Rules:
- Only write evidence that is visible in the supplied files.
- Never expose secret values; describe secret references by key/name only.
- Include source file or chunk references for every claim.
- Mark absent runtime facts as UNKNOWN instead of filling them in.
""",
    "builtin/core_risk_security": """You are the core risk and security evidence agent.

Goal:
Find concrete evidence about security-sensitive behavior, operational risks, quality risks, and migration risks.

Evidence to capture:
- Authentication, authorization, roles, permissions, tokens, credentials references, and secret handling.
- PII/sensitive-data handling, logging of sensitive data, unsafe defaults, disabled validation, or hardcoded endpoints.
- Error handling, retry behavior, idempotency, observability, operational failure modes, and missing tests.
- Risk statements that are tied to concrete source evidence.

Rules:
- Only write evidence that is visible in the supplied files.
- Do not invent vulnerabilities; report directly evidenced risk indicators.
- Include source file or chunk references for every claim.
- Separate confirmed risks from unresolved questions.
""",
    "builtin/sql_usage_agent": """You are the table inventory SQL usage evidence agent.

Goal:
Find every evidenced SQL usage and table/field reference needed for table inventory documentation.

Evidence to capture:
- SELECT, INSERT, UPDATE, DELETE, MERGE, JOIN, WHERE, GROUP BY, ORDER BY, and stored procedure usage.
- Table names, aliases, field names, join keys, filters, and write targets.
- Source file/chunk references for each query or query fragment.
- Whether a table appears read-only, write-only, read/write, or unclear based on evidence.

Rules:
- Preserve SQL snippets and identifiers exactly where possible.
- Do not create tables or fields that are not present in evidence.
- Mark ambiguous aliases or dynamic SQL as unresolved.
""",
    "builtin/ddl_alignment_agent": """You are the table inventory DDL alignment evidence agent.

Goal:
Compare evidenced runtime/table usage with evidenced DDL/schema definitions.

Evidence to capture:
- CREATE TABLE, ALTER TABLE, constraints, keys, indexes, datatypes, nullability, and schema names.
- Tables/fields used by SQL or ORM evidence that are missing from DDL evidence.
- DDL tables/fields that appear unused by supplied source evidence.
- Environment caveats where DDL may describe only one environment.

Rules:
- Only compare evidence that is supplied.
- Do not generate final DDL unless the evidence supports it.
- Mark missing schema facts as UNKNOWN or unresolved blockers.
""",
    "builtin/db_config_agent": """You are the database configuration evidence agent.

Goal:
Find database connection and environment evidence needed for table inventory and DDL documentation.

Evidence to capture:
- Database connection names, drivers, hosts, schemas, catalogs, connection pools, datasource names, and config keys.
- Environment-specific database references such as ACC, PRD, test, local, or migration targets.
- ORM/database framework configuration and implicit table/entity mapping.
- Missing environment or connection facts that block confident table documentation.

Rules:
- Never expose secret values; keep only key names and source references.
- Do not assume one environment's schema applies to another.
- Include source file or chunk references for every claim.
""",
}


@dataclass(frozen=True)
class EvidenceAgentResolution:
    agents: list[ResolvedEvidenceAgent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class EvidenceAgentClaim(BaseModel):
    category: str
    claim: str
    source: str
    snippet: str
    confidence: float = Field(ge=0.0, le=1.0)
    unknown: bool = False

    @field_validator("category", "claim", "source", "snippet")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class EvidenceAgentRunResult(BaseModel):
    agent_id: str
    result: ModelResult
    claims: list[EvidenceAgentClaim] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


DEFAULT_HARNESS_AGENTS: tuple[ResolvedEvidenceAgent, ...] = (
    ResolvedEvidenceAgent(
        id="core_architecture_dependency",
        source="builtin/core_architecture_dependency",
        prompt=BUILTIN_AGENT_PROMPTS["builtin/core_architecture_dependency"],
        evidence_profile=None,
        categories=["source_summary", "routes", "handlers"],
        inputs=["facts_manifest", "preprocess_chunks", "engineering_facts"],
    ),
    ResolvedEvidenceAgent(
        id="core_data_flow",
        source="builtin/core_data_flow",
        prompt=BUILTIN_AGENT_PROMPTS["builtin/core_data_flow"],
        evidence_profile=None,
        categories=["sql_queries", "orm_entities", "ddl_schema"],
        inputs=["facts_manifest", "preprocess_chunks", "engineering_facts"],
    ),
    ResolvedEvidenceAgent(
        id="core_runtime_config",
        source="builtin/core_runtime_config",
        prompt=BUILTIN_AGENT_PROMPTS["builtin/core_runtime_config"],
        evidence_profile=None,
        categories=["db_connections", "config_references"],
        inputs=["facts_manifest", "preprocess_chunks", "engineering_facts"],
    ),
    ResolvedEvidenceAgent(
        id="core_risk_security",
        source="builtin/core_risk_security",
        prompt=BUILTIN_AGENT_PROMPTS["builtin/core_risk_security"],
        evidence_profile=None,
        categories=["auth", "config_references"],
        inputs=["facts_manifest", "preprocess_chunks", "engineering_facts"],
    ),
)


def resolve_evidence_agents(manifest: Any | None, pack_dir: Path | None) -> EvidenceAgentResolution:
    """Resolve the evidence agents that should run for this harness invocation."""
    if manifest is None:
        return EvidenceAgentResolution(agents=list(DEFAULT_HARNESS_AGENTS))

    declared = list(getattr(manifest, "evidence_agents", []) or [])
    if not declared:
        return EvidenceAgentResolution()

    agents: list[ResolvedEvidenceAgent] = []
    errors: list[str] = []
    profiles = getattr(manifest, "evidence_profiles", {}) or {}

    for agent in declared:
        agent_id = getattr(agent, "id", "")
        profile_name = getattr(agent, "evidence_profile", None)
        categories = [
            category
            for category in list(getattr(agent, "categories", []) or [])
            if category in SUPPORTED_EVIDENCE_CATEGORIES
        ]
        if profile_name and profile_name in profiles:
            include = list(getattr(profiles[profile_name], "include", []) or [])
            categories = categories or [
                category for category in include if category in SUPPORTED_EVIDENCE_CATEGORIES
            ]
        if not categories:
            errors.append(f"evidence agent {agent_id!r} has no supported categories")
            continue

        uses = getattr(agent, "uses", None)
        prompt_file = getattr(agent, "prompt_file", None)
        prompt_path: Path | None = None
        source = str(uses or "")
        prompt = ""
        if uses:
            prompt = BUILTIN_AGENT_PROMPTS.get(str(uses), "")
            if str(uses).startswith("builtin/") and not prompt:
                errors.append(f"evidence agent {agent_id!r} unknown builtin: {uses}")
                continue
        if prompt_file:
            if pack_dir is None:
                errors.append(f"evidence agent {agent_id!r} uses prompt_file without a pack directory")
                continue
            prompt_path = pack_dir / str(prompt_file)
            source = prompt_path.relative_to(pack_dir).as_posix()
            if not prompt_path.is_file():
                errors.append(f"evidence agent {agent_id!r} prompt_file not found: {source}")
                continue
            prompt = prompt_path.read_text(encoding="utf-8", errors="replace").strip()
            if not prompt:
                errors.append(f"evidence agent {agent_id!r} prompt_file is empty: {source}")
                continue

        agents.append(
            ResolvedEvidenceAgent(
                id=agent_id,
                source=source,
                prompt=prompt,
                evidence_profile=profile_name,
                categories=categories,
                inputs=list(getattr(agent, "inputs", []) or []),
                output=getattr(agent, "output", "evidence_registry"),
                model=getattr(agent, "model", None),
                concurrency=getattr(agent, "concurrency", None),
                prompt_path=prompt_path,
            )
        )

    return EvidenceAgentResolution(agents=agents, errors=errors)


class EvidenceAgentRunner:
    """Run prompt-backed evidence agents and validate their structured claims."""

    def __init__(self, staging_dir: Path) -> None:
        self.staging_dir = staging_dir
        self._bundle_builder = EvidenceBundleBuilder(staging_dir, manifest=None)

    async def run_agent(
        self,
        agent: ResolvedEvidenceAgent,
        provider: ModelProvider,
        *,
        max_context_bytes: int = 120_000,
    ) -> EvidenceAgentRunResult:
        context_files = self._context_files(agent, max_context_bytes=max_context_bytes)
        result = await provider.complete(
            _agent_prompt(agent),
            context_files=context_files,
            options=ModelOptions(
                max_tokens=4096,
                temperature=0.0,
                timeout_s=240.0,
                retries=1,
                json_mode=True,
            ),
        )
        if not result.success:
            return EvidenceAgentRunResult(
                agent_id=agent.id,
                result=result,
                errors=[result.raw_error or "model call failed"],
            )
        claims, errors = parse_agent_claims(result.text, agent, self.staging_dir)
        return EvidenceAgentRunResult(agent_id=agent.id, result=result, claims=claims, errors=errors)

    def _context_files(self, agent: ResolvedEvidenceAgent, *, max_context_bytes: int) -> list[Path]:
        selected: list[Path] = []
        total = 0

        def _add(path: Path) -> None:
            nonlocal total
            if not path.is_file() or path in selected:
                return
            try:
                size = path.stat().st_size
            except OSError:
                return
            if selected and total + size > max_context_bytes:
                return
            selected.append(path)
            total += size

        for path in self._bundle_builder._base_files():
            _add(path)

        source_files = self._bundle_builder._source_files([])
        for path in source_files:
            if self._file_matches_agent(path, agent):
                _add(path)
        if not any(path.parent.name == "_preprocess" and not path.name.startswith("_") for path in selected):
            for path in source_files[:2]:
                _add(path)
        return selected

    def _file_matches_agent(self, path: Path, agent: ResolvedEvidenceAgent) -> bool:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return any(CATEGORY_PATTERNS[category].search(text) for category in agent.categories)


class EvidenceRegistryBuilder:
    """Write normalized evidence registry files from resolved agents."""

    def __init__(self, staging_dir: Path) -> None:
        self.staging_dir = staging_dir
        self._bundle_builder = EvidenceBundleBuilder(staging_dir, manifest=None)

    def build(
        self,
        agents: list[ResolvedEvidenceAgent],
        agent_results: list[EvidenceAgentRunResult] | None = None,
    ) -> dict[str, str]:
        """Build evidence registry artifacts and return a dict of relative paths.

        Writes three output tiers:
        - evidence_registry.json  — full raw store (never sent to models)
        - evidence_registry.md    — full markdown reference (operator use)
        - evidence_brief.md       — compact one-line-per-claim for model context
        """
        registry_dir = self.staging_dir / "_evidence_registry"
        registry_dir.mkdir(parents=True, exist_ok=True)

        claims: list[dict[str, Any]] = []
        for run_result in agent_results or []:
            agent = next((candidate for candidate in agents if candidate.id == run_result.agent_id), None)
            if agent is None:
                continue
            for claim in run_result.claims:
                claims.append(
                    {
                        "id": _claim_id(agent.id, claim.category, claim.source, claim.snippet),
                        "agent_id": agent.id,
                        "agent_source": agent.source,
                        "evidence_profile": agent.evidence_profile,
                        "category": claim.category,
                        "claim": claim.claim,
                        "source": claim.source,
                        "snippet": claim.snippet,
                        "confidence": claim.confidence,
                        "unknown": claim.unknown,
                        "output": agent.output,
                        "found_by": "llm_agent",
                    }
                )
        for agent in agents:
            for category in agent.categories:
                for source_path in [*self._bundle_builder._base_files(), *self._bundle_builder._source_files([])]:
                    for snippet in self._bundle_builder._snippets_for(source_path, category):
                        rel = source_path.relative_to(self.staging_dir).as_posix()
                        claims.append(
                            {
                                "id": _claim_id(agent.id, category, rel, snippet),
                                "agent_id": agent.id,
                                "agent_source": agent.source,
                                "evidence_profile": agent.evidence_profile,
                                "category": category,
                                "claim": "",
                                "source": rel,
                                "snippet": snippet,
                                "confidence": 0.75 if CATEGORY_PATTERNS[category].search(snippet) else 0.5,
                                "unknown": False,
                                "output": agent.output,
                                "found_by": "deterministic",
                            }
                        )

        deduped = _dedupe_claims(claims)
        payload = {
            "version": 1,
            "agents": [
                {
                    "id": agent.id,
                    "source": agent.source,
                    "prompt": agent.prompt,
                    "evidence_profile": agent.evidence_profile,
                    "categories": agent.categories,
                    "inputs": agent.inputs,
                    "output": agent.output,
                    "model": agent.model,
                    "concurrency": agent.concurrency,
                }
                for agent in agents
            ],
            "claim_count": len(deduped),
            "agent_runs": [
                {
                    "agent_id": run_result.agent_id,
                    "success": run_result.result.success,
                    "model_name": run_result.result.model_name,
                    "input_tokens": run_result.result.input_tokens,
                    "output_tokens": run_result.result.output_tokens,
                    "latency_ms": run_result.result.latency_ms,
                    "claim_count": len(run_result.claims),
                    "errors": run_result.errors,
                }
                for run_result in agent_results or []
            ],
            "claims": deduped,
        }

        registry_json = registry_dir / "evidence_registry.json"
        evidence_brief = registry_dir / "evidence_brief.md"
        doc_slices_dir = registry_dir / "doc_slices"
        registry_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        debug_write(
            debug_dir(self.staging_dir, "_evidence_registry", "evidence_registry.md"),
            _registry_markdown(payload),
        )
        evidence_brief.write_text(_brief_markdown(deduped, payload["agents"]), encoding="utf-8")
        debug_write(
            debug_dir(self.staging_dir, "_evidence_registry", "survival_report.json"),
            json.dumps(_survival_report(payload), indent=2),
        )
        _build_doc_slices(deduped, payload["agents"], doc_slices_dir)
        return {
            "json": registry_json.relative_to(self.staging_dir).as_posix(),
            "brief": evidence_brief.relative_to(self.staging_dir).as_posix(),
            "doc_slices_dir": doc_slices_dir.relative_to(self.staging_dir).as_posix(),
        }

    def build_phase2_slice(self, chunk_id: str, allowed_units: list[str]) -> Path | None:
        """Generate _evidence_registry/phase2_slices/{chunk_id}.md filtered to this chunk.

        Filters claims whose source path or stem contains any unit name visible in this chunk.
        Called per-chunk during Phase 2 dispatch. Returns the slice path, or None if no
        claims match (caller should fall back to evidence_brief.md).
        """
        registry_json = self.staging_dir / "_evidence_registry" / "evidence_registry.json"
        if not registry_json.exists():
            return None
        try:
            payload = json.loads(registry_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        all_claims: list[dict[str, Any]] = payload.get("claims", [])
        agents: list[dict[str, Any]] = payload.get("agents", [])
        unit_names_lower = {u.lower() for u in allowed_units}

        def _matches(claim: dict[str, Any]) -> bool:
            source = str(claim["source"])
            source_lower = source.lower()
            source_stem = Path(source).stem.lower()
            snippet_lower = str(claim.get("snippet", "")).lower()
            claim_text_lower = str(claim.get("claim", "")).lower()
            return (
                source_stem in unit_names_lower
                or any(unit in source_lower for unit in unit_names_lower)
                or any(unit in snippet_lower for unit in unit_names_lower)
                or any(unit in claim_text_lower for unit in unit_names_lower)
            )

        matching = [c for c in all_claims if _matches(c)]
        if not matching:
            return None

        slices_dir = self.staging_dir / "_evidence_registry" / "phase2_slices"
        slices_dir.mkdir(parents=True, exist_ok=True)
        slice_path = slices_dir / f"{chunk_id}.md"
        slice_path.write_text(_brief_markdown(matching, agents), encoding="utf-8")
        return slice_path


def _claim_id(agent_id: str, category: str, source: str, snippet: str) -> str:
    import hashlib

    digest = hashlib.sha1(f"{agent_id}\0{category}\0{source}\0{snippet}".encode("utf-8")).hexdigest()
    return digest[:16]


def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for claim in claims:
        key = (
            str(claim["category"]),
            str(claim["source"]),
            str(claim["snippet"]),
            str(claim.get("claim") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(claim)
    return deduped


def _registry_markdown(payload: dict[str, Any]) -> str:
    parts = [
        "# Evidence Registry",
        "",
        f"Claims: {payload['claim_count']}",
        "",
        "## Agents",
    ]
    for agent in payload["agents"]:
        parts.append(f"- `{agent['id']}` from `{agent['source']}`: {', '.join(agent['categories'])}")
    parts.append("")
    # Agent prompts are in evidence_registry.json for debugging; not forwarded to models.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for claim in payload["claims"]:
        grouped.setdefault(str(claim["category"]), []).append(claim)
    for category, claims in sorted(grouped.items()):
        parts.append(f"## {category}")
        for claim in claims:
            parts.append(f"### {claim['source']}")
            parts.append(f"Agent: `{claim['agent_id']}` ({claim.get('found_by', 'unknown')})")
            parts.append("")
            if claim.get("claim"):
                parts.append(str(claim["claim"]))
                parts.append("")
            parts.append(str(claim["snippet"]))
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _brief_markdown(claims: list[dict[str, Any]], agents: list[dict[str, Any]]) -> str:
    """Compact one-line-per-claim format for model context.

    No raw code blocks, no agent prompts. Deterministic claims use a snippet
    summary (first 120 chars) instead of the full raw snippet.
    """
    parts = [
        "# Evidence Brief",
        "",
        f"Claims: {len(claims)}",
        "",
        "## Agents",
    ]
    for agent in agents:
        parts.append(f"- `{agent['id']}` ({', '.join(agent['categories'])})")
    parts.append("")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        grouped.setdefault(str(claim["category"]), []).append(claim)

    for category, cat_claims in sorted(grouped.items()):
        parts.append(f"## {category}")
        for claim in cat_claims:
            source_name = Path(str(claim["source"])).name
            if claim.get("claim"):
                text = str(claim["claim"]).strip()
            else:
                raw = str(claim.get("snippet", "")).strip()
                flat = " ".join(raw.split())
                text = (flat[:120] + "...") if len(flat) > 120 else flat
            found_by = claim.get("found_by", "unknown")
            parts.append(f"- [{found_by}] {text} ({source_name})")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _build_doc_slices(
    claims: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    slices_dir: Path,
) -> None:
    """Write _evidence_registry/doc_slices/{doc_name}.md for each doc with matching claims."""
    slices_dir.mkdir(parents=True, exist_ok=True)
    doc_claims: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        category = str(claim.get("category", ""))
        for doc_name in _CATEGORY_TO_DOCS.get(category, []):
            doc_claims.setdefault(doc_name, []).append(claim)
    for doc_name, doc_slice_claims in doc_claims.items():
        (slices_dir / f"{doc_name}.md").write_text(
            _brief_markdown(doc_slice_claims, agents), encoding="utf-8"
        )


def _survival_report(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "mode": "advisory",
        "claim_count": payload["claim_count"],
        "warnings": [
            {
                "code": "survival_not_yet_checked",
                "message": (
                    "Evidence registry was written. Downstream survival comparison is advisory "
                    "and not yet blocking in this implementation."
                ),
            }
        ],
    }


def _agent_prompt(agent: ResolvedEvidenceAgent) -> str:
    example_category = agent.categories[0] if agent.categories else "source_summary"
    schema = {
        "claims": [
            {
                "category": example_category,
                "claim": "One factual evidence claim derived only from supplied context.",
                "source": "_preprocess/example.md",
                "snippet": "Exact or near-exact supporting snippet from the source context.",
                "confidence": 0.8,
                "unknown": False,
            }
        ]
    }
    return (
        f"{agent.prompt.strip()}\n\n"
        "Return JSON only. Do not wrap it in markdown.\n"
        f"Allowed categories: {', '.join(agent.categories)}\n"
        "Output schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        "Validation rules:\n"
        "- Every claim.category must be one of the allowed categories.\n"
        "- Every source must name one supplied context file or a path visible inside it.\n"
        "- Every snippet must quote or closely preserve concrete source evidence.\n"
        "- If nothing relevant is evidenced, return {\"claims\": []}."
    )


def parse_agent_claims(
    text: str,
    agent: ResolvedEvidenceAgent,
    staging_dir: Path,
) -> tuple[list[EvidenceAgentClaim], list[str]]:
    errors: list[str] = []
    try:
        payload = _parse_json_object(text)
    except ValueError as exc:
        return [], [str(exc)]

    raw_claims = payload.get("claims", [])
    if not isinstance(raw_claims, list):
        return [], ["claims must be a list"]

    claims: list[EvidenceAgentClaim] = []
    known_sources = _known_source_names(staging_dir)
    for idx, raw in enumerate(raw_claims):
        try:
            claim = EvidenceAgentClaim.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {"msg": str(exc)}
            errors.append(f"claim {idx}: {first['msg']}")
            continue
        if claim.category not in agent.categories:
            errors.append(f"claim {idx}: category {claim.category!r} not allowed for agent {agent.id!r}")
            continue
        if not _source_is_known(claim.source, known_sources):
            errors.append(f"claim {idx}: source {claim.source!r} was not supplied")
            continue
        claims.append(claim)
    return claims, errors


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty model output")
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model output did not contain a JSON object")
        candidate = stripped[start : end + 1]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json  # type: ignore[import-untyped]

                parsed = repair_json(candidate, return_objects=True)
            except Exception as exc:
                raise ValueError(f"model output was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model output JSON must be an object")
    return parsed


_SOURCE_CODE_EXTENSIONS: frozenset[str] = frozenset({
    ".cs", ".vb",
    ".java", ".kt", ".scala", ".groovy",
    ".py", ".rb", ".go", ".ts", ".js", ".php",
    ".sql", ".xsl", ".xslt", ".dtsx",
    ".config", ".properties",
})


def _known_source_names(staging_dir: Path) -> set[str]:
    names: set[str] = set()
    for path in staging_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(staging_dir).as_posix()
        names.add(rel)
        names.add(path.name)
    return names


def _source_is_known(source: str, known_sources: set[str]) -> bool:
    source = source.strip()
    if source in known_sources:
        return True
    if any(source.endswith(f"/{known}") or known.endswith(f"/{source}") for known in known_sources):
        return True
    source_basename = Path(source).name
    if any(Path(k).name == source_basename for k in known_sources):
        return True
    # Accept citations of original source files embedded inside preprocess chunks.
    # Those files don't exist in staging_dir; the preprocess step inlined their content.
    ext = Path(source).suffix.lower()
    return bool(ext and ext in _SOURCE_CODE_EXTENSIONS)
