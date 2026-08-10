"""Feature-flagged API for isolated, non-publishing generation experiments."""

from __future__ import annotations

import asyncio
import datetime
from decimal import Decimal
import json
import os
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from starlette.responses import StreamingResponse
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mcp_server.api.permissions import require_project_access
from mcp_server.config import config
from mcp_server.services.log_manager import experiment_log_manager
from mcp_server.db.models import (
    GenerateRun,
    GenerationExperiment,
    GenerationRecipeSnapshot,
    GenerationSourceSnapshot,
    GenerationSourceSnapshotItem,
    GenerationTaskAttempt,
    GenerationTaskEvent,
    GenerationTaskRun,
    Project,
)
from mcp_server.db.session import async_session_factory
from mcp_server.experiments.application.snapshot_baseline import (
    BaselineFreezeRequest,
    BaselineFreezer,
)
from mcp_server.experiments.application.snapshot_project_evidence import (
    CreateProjectEvidenceSnapshotRequest,
    ProjectEvidenceSnapshotService,
)
from mcp_server.experiments.application.prepare_supporting_evidence import (
    SupportingEvidenceLimits,
    SupportingEvidencePreparationService,
)
from mcp_server.experiments.application.scanner_evidence_receipt import (
    ensure_grounded_scanner_receipt,
)
from mcp_server.experiments.application.recover_experiment import (
    ExperimentRecoverySnapshot,
    SanitizedLeaseSnapshot,
    SanitizedTaskSnapshot,
    SanitizedTaskStatus,
    derive_recovery,
)
from mcp_server.experiments.application.run_agentic_legacy_candidate import (
    AgenticLegacyOpenCodeRunner,
)
from mcp_server.experiments.application.grounded_pack import (
    load_grounded_execution_pack,
)
from mcp_server.experiments.application.recover_orchestrated_candidate import (
    OrchestratedCandidateRecovery,
)
from mcp_server.experiments.application.run_orchestrated_candidate import (
    OrchestratedOpenCodeRunner,
)
from mcp_server.experiments.application.run_staged_grounded_candidate import (
    GroundedRuntimeRunner,
)
from mcp_server.experiments.application.snapshot_sources import (
    CreateSourceSnapshotRequest,
    SourceSnapshotService,
)
from mcp_server.experiments.application.snapshot_recipe import (
    RecipeSnapshotArtifactSink,
)
from mcp_server.experiments.domain.models import (
    BaselineSupportingArtifact,
    LegacyBaselineRun,
    require_safe_identifier,
)
from mcp_server.experiments.domain.project_evidence import (
    PROJECT_EVIDENCE_SCHEMA_VERSION,
    ProjectEvidenceSnapshot,
)
from mcp_server.experiments.domain.sources import (
    SourceDescriptor,
    SourceLimits,
)
from mcp_server.experiments.infrastructure.opencode.client import OpenCodeHttpClient
from mcp_server.experiments.infrastructure.opencode.runtime import (
    OpenCodeAgentRuntime,
    OpenCodeGroundedTaskBuilder,
)
from mcp_server.experiments.infrastructure.openai_agents.composition import (
    OPENAI_AGENTS_GROUNDED_RUNTIME_ID,
    OPENCODE_GROUNDED_RUNTIME_ID,
    compose_openai_agents_grounded_runtime,
    configured_openai_agents_routes,
)
from mcp_server.experiments.infrastructure.persistence.repositories import (
    InMemoryExperimentRepository,
)
from mcp_server.experiments.infrastructure.persistence.sqlalchemy_repository import (
    SqlAlchemyExperimentRepository,
)
from mcp_server.experiments.infrastructure.persistence.task_scheduler_repository import (
    TaskSchedulerRepository,
)
from mcp_server.experiments.infrastructure.recipes.compiler import compile_recipe
from mcp_server.experiments.infrastructure.recipes.loader import (
    load_recipe,
    parse_scope,
)
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore
from mcp_server.experiments.infrastructure.storage.experiment_retention import (
    ExperimentRetention,
    ExperimentRetentionError,
    RetentionCandidate,
)
from mcp_server.experiments.infrastructure.sources.governed_directory import (
    DirectoryEvidenceAdapter,
)
from mcp_server.experiments.infrastructure.sources.intodoc_scan import (
    IntodocScanSourceAdapter,
)
from mcp_server.experiments.infrastructure.sources.registry import (
    SourceAdapterError,
    SourceAdapterRegistry,
)
from mcp_server.experiments.infrastructure.sources.runtime_export import (
    RuntimeExportAdapter,
)
from mcp_server.experiments.infrastructure.sources.prepared_directory import (
    PreparedDirectoryEvidenceAdapter,
)
from mcp_server.experiments.infrastructure.sources.tabular import TabularFileAdapter
from mcp_server.experiments.infrastructure.storage.source_snapshot_store import (
    SourceSnapshotArtifactSink,
)
from mcp_server.experiments.infrastructure.storage.project_evidence_snapshot_store import (
    ProjectEvidenceSnapshotArtifactSink,
    ProjectEvidenceSnapshotPersistence,
)

router = APIRouter(prefix="/agentic-experiments", tags=["agentic-experiments"])


class CreateExperimentRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    baseline_run_id: int | None = Field(default=None, gt=0)
    recipe_id: str | None = Field(default=None, max_length=200)
    recipe_version: str | None = Field(default=None, max_length=100)
    runtime_id: str = Field(default="fake", min_length=1, max_length=100)
    provider_id: str | None = Field(default=None, max_length=100)
    model_id: str | None = Field(default=None, max_length=200)
    scope: dict[str, object] | None = None
    acknowledge_non_publishing: Literal[True]
    idempotency_key: str = Field(min_length=1, max_length=200)


class SourceInputRequest(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    source_kind: str = Field(min_length=1, max_length=200)
    source_role: str = Field(min_length=1, max_length=200)
    capabilities: set[str] = Field(min_length=1, max_length=32)
    governed_root_index: int | None = Field(default=None, ge=0)
    relative_path: str | None = Field(default=None, max_length=1000)
    redaction_receipt_relative_path: str | None = Field(default=None, max_length=1000)


class QuickOpenCodeRunRequest(BaseModel):
    """Normal Generate-dialog request for one isolated OpenCode candidate."""

    project_id: str = Field(min_length=1, max_length=100)
    baseline_run_id: int | None = Field(default=None, gt=0)
    recipe_id: str = Field(
        default="alitalend-legacy-equivalence",
        min_length=1,
        max_length=200,
    )
    recipe_version: str = Field(default="1.0.0", min_length=1, max_length=100)
    provider_id: str | None = Field(default=None, min_length=1, max_length=100)
    model_id: str | None = Field(default=None, min_length=1, max_length=200)
    candidate_mode: Literal["single_agent", "orchestrated", "grounded"] = "single_agent"
    grounded_runtime_id: Literal["opencode", "openai-agents"] | None = None
    acknowledge_paid_execution: Literal[True] | None = None
    additional_sources: tuple[SourceInputRequest, ...] = Field(default=(), max_length=31)
    scope: dict[str, object] | None = None
    acknowledge_non_publishing: Literal[True]
    idempotency_key: str = Field(min_length=1, max_length=200)
    resume_from_experiment_id: str | None = Field(default=None, min_length=1, max_length=36)


class RecoverOpenCodeRunRequest(BaseModel):
    """Explicitly index one completed failed-run workspace without dispatch."""

    parent_session_id: str = Field(min_length=1, max_length=200)
    acknowledge_no_provider_call: Literal[True]


class ArtifactResponse(BaseModel):
    branch: str
    kind: str
    logical_name: str
    relative_path: str
    sha256: str
    media_type: str
    size_bytes: int


class ExperimentBranchMetricsResponse(BaseModel):
    document_count: int
    duration_ms: int | None
    provider_latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    billed_cost_usd: str | None
    quality_score: float | None
    model_id: str | None
    attempt_count: int | None
    retry_count: int | None


class GroundedRoleProgressResponse(BaseModel):
    role_id: str
    stage: str
    status: Literal["pending", "completed"]
    accepted_artifact_count: int
    owned_artifact_count: int


class GroundedProgressResponse(BaseModel):
    stage: str
    accepted_artifact_count: int
    required_artifact_count: int
    roles: tuple[GroundedRoleProgressResponse, ...]


class ProjectEvidenceSourceStatusResponse(BaseModel):
    descriptor_id: str
    source_kind: str
    source_role: str
    status: Literal["readable", "unreadable", "unsupported"]
    status_reason: str | None
    prepared_records: int


class ProjectEvidenceCoverageResponse(BaseModel):
    declared_sources: int
    readable_sources: int
    unreadable_sources: int
    unsupported_sources: int
    prepared_records: int
    prepared_bytes: int
    raw_files: int
    raw_bytes: int


class ExperimentResponse(BaseModel):
    experiment_id: str
    project_id: str
    baseline_run_id: int | None
    status: str
    comparison_status: str
    candidate_verdict: str
    runtime_id: str
    provider_id: str | None
    model_id: str | None
    source_snapshot_id: str | None
    source_snapshot_frozen: bool
    source_snapshot_fingerprint_sha256: str | None = None
    source_coverage: ProjectEvidenceCoverageResponse | None = None
    source_inventory: tuple[ProjectEvidenceSourceStatusResponse, ...] = ()
    recipe_snapshot_id: str | None
    recipe_id: str | None
    recipe_version: str | None
    baseline_manifest_sha256: str | None
    baseline_document_count: int
    artifact_count: int
    publishes: Literal[False] = False
    candidate_not_started: bool
    error_message: str | None
    baseline_metrics: ExperimentBranchMetricsResponse
    candidate_metrics: ExperimentBranchMetricsResponse
    safety_warnings: tuple[dict[str, str], ...] = ()
    grounded_progress: GroundedProgressResponse | None = None
    artifacts: tuple[ArtifactResponse, ...] = ()


class CreateSourceSnapshotBody(BaseModel):
    sources: tuple[SourceInputRequest, ...] = Field(min_length=1, max_length=32)


class SourceSnapshotResponse(BaseModel):
    experiment_id: str
    snapshot_id: str
    manifest_sha256: str
    security_state: Literal["approved"]
    manifest_relative_path: str
    item_count: int
    source_kinds: tuple[str, ...]
    source_roles: tuple[str, ...]
    fingerprint_sha256: str | None = None
    coverage: ProjectEvidenceCoverageResponse | None = None
    inventory: tuple[ProjectEvidenceSourceStatusResponse, ...] = ()
    publishes: Literal[False] = False


class CompileRecipeBody(BaseModel):
    recipe_id: str = Field(min_length=1, max_length=200)
    recipe_version: str = Field(min_length=1, max_length=100)
    model_capabilities: set[str] = Field(min_length=1, max_length=32)
    scope: dict[str, object] | None = None


class RecipeSnapshotResponse(BaseModel):
    experiment_id: str
    snapshot_id: str
    recipe_id: str
    recipe_version: str
    graph_sha256: str
    effective_scope_sha256: str
    manifest_relative_path: str
    task_count: int
    document_output_count: int
    preflight_required: Literal[True] = True
    publishes: Literal[False] = False


class TaskAttemptResponse(BaseModel):
    attempt_id: str
    attempt_number: int
    status: str
    started_at: str
    completed_at: str | None
    wall_duration_ms: int | None
    provider_latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    provider_cost_usd: str | None


class TaskEventResponse(BaseModel):
    event_id: int
    attempt_id: str | None
    sequence_number: int
    event_type: str
    occurred_at: str


class ExperimentTaskResponse(BaseModel):
    task_run_id: str
    node_id: str
    task_type: str
    role: str
    dependencies: tuple[str, ...]
    output_ids: tuple[str, ...]
    status: str
    attempt: int
    max_attempts: int
    runtime_id: str | None
    provider_id: str | None
    model_id: str | None
    started_at: str | None
    completed_at: str | None
    attempts: tuple[TaskAttemptResponse, ...]
    events: tuple[TaskEventResponse, ...]
    publishes: Literal[False] = False


class ExperimentTaskListResponse(BaseModel):
    experiment_id: str
    tasks: tuple[ExperimentTaskResponse, ...]
    total: int
    publishes: Literal[False] = False


class RetryTaskBody(BaseModel):
    acknowledge_explicit_retry: Literal[True]


class ReconcileExperimentBody(BaseModel):
    acknowledge_no_dispatch: Literal[True]


class TaskRecoveryResponse(BaseModel):
    task_run_id: str
    state: str
    manual_retry_required: bool
    reason: str | None


class ReconcileExperimentResponse(BaseModel):
    experiment_id: str
    persisted_status: str
    derived_status: str
    recovered_expired_task_ids: tuple[str, ...]
    manual_retry_task_ids: tuple[str, ...]
    missing_required_outputs: tuple[str, ...]
    duplicate_required_outputs: tuple[str, ...]
    tasks: tuple[TaskRecoveryResponse, ...]
    dispatch_performed: Literal[False] = False
    publishes: Literal[False] = False


class RetentionActionBody(BaseModel):
    acknowledge_recoverable_move: Literal[True]


class RetentionActionResponse(BaseModel):
    experiment_id: str
    project_id: str
    state: Literal["quarantined", "restored"]
    receipt_sha256: str
    source_relative_path: str
    trash_relative_path: str
    hard_deleted: Literal[False] = False
    publishes: Literal[False] = False


class RecipeSourceRequirementResponse(BaseModel):
    source_role: str
    required_capabilities: tuple[str, ...]
    optional: bool


class RecipeCapabilityResponse(BaseModel):
    recipe_id: str
    recipe_version: str
    source_requirements: tuple[RecipeSourceRequirementResponse, ...]
    required_model_capabilities: tuple[str, ...]
    output_ids: tuple[str, ...]
    document_output_count: int


class GroundedRuntimeCapabilityResponse(BaseModel):
    runtime_id: Literal["opencode", "openai-agents"]
    available: bool
    provider_id: str
    model_ids: tuple[str, ...]
    paid_execution_confirmation_required: bool
    routes: tuple["GroundedModelRouteCapabilityResponse", ...] = ()
    blockers: tuple[str, ...]


class GroundedModelRouteCapabilityResponse(BaseModel):
    provider_id: str
    model_id: str
    display_name: str


class ExperimentCapabilitiesResponse(BaseModel):
    feature_enabled: Literal[True] = True
    provider_neutral: Literal[True] = True
    model_selection: Literal["runtime_discovered"] = "runtime_discovered"
    publishes: Literal[False] = False
    candidate_dispatch_available: bool
    paid_execution_enabled: bool
    opencode_version: str
    supported_source_kinds: tuple[str, ...]
    recipes: tuple[RecipeCapabilityResponse, ...]
    grounded_runtimes: tuple[GroundedRuntimeCapabilityResponse, ...]
    execution_blockers: tuple[str, ...]


def _require_feature() -> None:
    if not config.agentic_experiments_enabled:
        raise HTTPException(status_code=404, detail="Agentic experiments are disabled")


def _json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _governed_roots() -> tuple[Path, ...]:
    try:
        raw = json.loads(config.agentic_governed_source_roots_json or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("governed source roots configuration is invalid") from exc
    if not isinstance(raw, list) or any(not isinstance(value, str) or not value for value in raw):
        raise ValueError("governed source roots must be a JSON string array")
    return tuple(Path(value) for value in raw)


def _governed_source_path(
    source: SourceInputRequest,
    roots: tuple[Path, ...],
    *,
    allow_missing: bool = False,
) -> Path:
    if source.governed_root_index is None or source.relative_path is None:
        raise ValueError("non-scanner sources require a governed root and relative path")
    return _governed_relative_path(
        root_index=source.governed_root_index,
        relative_path=source.relative_path,
        roots=roots,
        allow_missing=allow_missing,
    )


def _governed_relative_path(
    *,
    root_index: int,
    relative_path: str,
    roots: tuple[Path, ...],
    allow_missing: bool,
) -> Path:
    try:
        root = roots[root_index].resolve(strict=True)
    except (IndexError, OSError) as exc:
        raise ValueError("governed source root is unavailable") from exc
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative_path
        or "\\" in relative_path
        or "\x00" in relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("governed source path must be a safe relative POSIX path")
    try:
        candidate = root.joinpath(*relative.parts).resolve(strict=not allow_missing)
        candidate.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("governed source path escapes its configured root") from exc
    return candidate


def _source_composition(
    *,
    project_id: str,
    sources: tuple[SourceInputRequest, ...],
    allow_missing: bool = False,
    managed_workspace_root: Path | None = None,
    require_bound_scanner_receipt: bool = False,
) -> tuple[SourceAdapterRegistry, tuple[SourceDescriptor, ...]]:
    require_safe_identifier(project_id, field_name="project_id")
    roots = _governed_roots()
    descriptors: list[SourceDescriptor] = []
    for source in sources:
        capabilities = frozenset(source.capabilities)
        if any(not capability.strip() for capability in capabilities):
            raise ValueError("source capabilities must be non-blank")
        if source.source_kind == "intodoc_scan":
            if (
                source.governed_root_index is not None
                or source.relative_path is not None
                or source.redaction_receipt_relative_path is not None
            ):
                raise ValueError("IntoDoc source location is project-owned and cannot be overridden")
            source_path = config.intodoc_output_dir / project_id
            if require_bound_scanner_receipt:
                # A normal scanner report records that redaction ran.
                # Project-evidence snapshots additionally need a closed,
                # hash-bound receipt for the exact files they freeze.  Create
                # that companion only from already-approved scanner output;
                # no uploaded source files are read here.
                receipt = ensure_grounded_scanner_receipt(source_path)
            else:
                report = source_path / "redaction_report.json"
                fallback = source_path / "redaction.json"
                receipt = report if report.is_file() else fallback
            descriptor = SourceDescriptor(
                id=source.id,
                source_kind=source.source_kind,
                source_role=source.source_role,
                source_path=source_path,
                provenance_locator=f"intodoc://{project_id}/current",
                capabilities=capabilities,
                redaction_receipt_path=receipt,
                trust_level="scanner_redacted",
            )
        else:
            source_path = _governed_source_path(
                source,
                roots,
                allow_missing=allow_missing,
            )
            receipt_path = (
                _governed_relative_path(
                    root_index=source.governed_root_index,
                    relative_path=source.redaction_receipt_relative_path,
                    roots=roots,
                    allow_missing=allow_missing,
                )
                if source.governed_root_index is not None
                and source.redaction_receipt_relative_path is not None
                else None
            )
            descriptor = SourceDescriptor(
                id=source.id,
                source_kind=source.source_kind,
                source_role=source.source_role,
                source_path=source_path,
                provenance_locator=(
                    f"governed://{source.governed_root_index}/{source.relative_path}"
                ),
                capabilities=capabilities,
                redaction_receipt_path=receipt_path,
                trust_level="governed",
            )
        descriptors.append(descriptor)
    adapter_roots = roots
    if managed_workspace_root is not None:
        managed_root = Path(managed_workspace_root)
        if managed_root.is_symlink():
            raise ValueError("managed project workspace cannot be a symlink")
        # Managed Git/ZIP projects are rooted under the server-owned repository
        # directory.  Do not turn the persisted project path into a caller-
        # controlled filesystem capability.
        expected_root = (config.repos_dir / project_id).resolve(strict=False)
        try:
            managed_root.resolve(strict=False).relative_to(expected_root.parent)
        except ValueError as exc:
            raise ValueError("managed project workspace is outside the server repository root") from exc
        descriptor = SourceDescriptor(
            id="managed-project-workspace",
            source_kind="directory_evidence",
            source_role="supporting_reference",
            source_path=managed_root,
            provenance_locator=f"project-workspace://{project_id}",
            capabilities=frozenset({"searchable_text", "structured_records"}),
            # Uploaded workspaces are not implicitly trusted as redacted input.
            # A missing or invalid receipt is recorded as unavailable by the
            # project-evidence snapshot service; never bypass this boundary.
            redaction_receipt_path=None,
            trust_level="managed_workspace_unredacted",
            metadata={
                "automatic": True,
                "authority": "supporting_only",
                # Scanner-owned inputs are implementation evidence, already
                # represented through the IntoDoc source snapshot and fact
                # ledger.  The supporting-evidence stage must never expose a
                # duplicate raw code copy to an agent.
                "exclude_relative_prefixes": _scanner_owned_relative_prefixes(
                    project_id
                ),
            },
        )
        if any(item.id == descriptor.id for item in descriptors):
            raise ValueError("managed project workspace descriptor id collides with an explicit source")
        descriptors.append(descriptor)
        adapter_roots = (*roots, managed_root)

    registry = SourceAdapterRegistry(
        (
            IntodocScanSourceAdapter(),
            DirectoryEvidenceAdapter(allowed_roots=adapter_roots),
            TabularFileAdapter(),
            RuntimeExportAdapter(),
        )
    )
    return registry, tuple(descriptors)


def _scanner_owned_relative_prefixes(project_id: str) -> tuple[str, ...]:
    """Read scanner-declared source roots without platform filename heuristics.

    The scanner is the authority for which project paths implement the scanned
    system.  Its file index may contain records outside a platform execution
    root; only explicitly declared ``source_root`` values are excluded from
    generic supporting-evidence preparation.  Missing or malformed metadata is
    intentionally treated as no exclusion instead of guessing from a folder
    name such as ``Talend exports``.
    """

    path = config.intodoc_output_dir / project_id / "scan" / "files_index.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    if not isinstance(value, list):
        return ()
    prefixes: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        metadata = item.get("platform_metadata")
        if not isinstance(metadata, dict):
            continue
        root = metadata.get("source_root")
        if not isinstance(root, str) or not root.strip():
            continue
        candidate = PurePosixPath(root.strip())
        if (
            candidate.is_absolute()
            or "\\" in root
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            continue
        prefixes.add(candidate.as_posix().rstrip("/"))
    return tuple(sorted(prefixes))


async def _managed_project_workspace(project_id: str) -> Path | None:
    """Return the server-owned workspace for an uploaded/managed project.

    Local-path projects are intentionally excluded: their path was supplied by
    an operator and is not an implicit Generate Docs source.  Git/ZIP projects
    are materialized beneath ``config.repos_dir`` and can be declared as
    supporting evidence without asking the caller for a filesystem path.
    """

    async with async_session_factory() as db:
        project = await db.get(Project, project_id)
    if project is None or project.source_type not in {"git", "zip"}:
        return None
    return config.repos_dir / project_id


def _bundled_recipe_root() -> Path:
    configured = Path(config.agentic_recipe_root)
    if not configured.is_absolute():
        configured = Path.cwd() / configured
    if configured.is_dir() and not configured.is_symlink():
        return configured
    return Path(__file__).resolve().parents[4] / "data" / "agentic_recipes"


def _resolve_bundled_recipe(recipe_id: str, recipe_version: str):
    root = _bundled_recipe_root()
    matches = []
    if root.is_dir() and not root.is_symlink():
        for child in sorted(root.iterdir(), key=lambda path: path.name):
            source = child / "recipe.yaml"
            if child.is_symlink() or not child.is_dir() or not source.is_file():
                continue
            manifest = load_recipe(source)
            if manifest.id == recipe_id and manifest.version == recipe_version:
                matches.append((child, manifest))
    if len(matches) != 1:
        raise ValueError("exactly one certified bundled recipe must match the requested identity")
    recipe_root, manifest = matches[0]
    if not manifest.certification_id or not manifest.certification_id.startswith("bundled:nexus:"):
        raise ValueError("agentic recipe is not certified for execution")
    return recipe_root, manifest


def _bundled_recipe_capabilities() -> tuple[RecipeCapabilityResponse, ...]:
    root = _bundled_recipe_root()
    recipes: list[RecipeCapabilityResponse] = []
    if not root.is_dir() or root.is_symlink():
        return ()
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        source = child / "recipe.yaml"
        if child.is_symlink() or not child.is_dir() or not source.is_file():
            continue
        manifest = load_recipe(source)
        if not manifest.certification_id or not manifest.certification_id.startswith(
            "bundled:nexus:"
        ):
            continue
        recipes.append(
            RecipeCapabilityResponse(
                recipe_id=manifest.id,
                recipe_version=manifest.version,
                source_requirements=tuple(
                    RecipeSourceRequirementResponse(
                        source_role=requirement.source_role,
                        required_capabilities=tuple(
                            sorted(requirement.required_capabilities)
                        ),
                        optional=requirement.optional,
                    )
                    for requirement in manifest.source_requirements
                ),
                required_model_capabilities=tuple(
                    sorted(manifest.required_model_capabilities)
                ),
                output_ids=tuple(output.id for output in manifest.output_contracts),
                document_output_count=sum(
                    output.media_type == "text/markdown"
                    for output in manifest.output_contracts
                ),
            )
        )
    return tuple(recipes)


def _supporting_artifacts(project_id: str, run_id: int) -> tuple[BaselineSupportingArtifact, ...]:
    project_output = config.project_root / "output" / project_id
    candidates: list[BaselineSupportingArtifact] = []
    metrics_path = project_output / "logs" / f"run_metrics_{run_id}.json"
    if metrics_path.is_file() and not metrics_path.is_symlink():
        candidates.append(
            BaselineSupportingArtifact(
                logical_name=f"run_metrics_{run_id}.json",
                source_path=metrics_path,
                kind="metrics",
            )
        )
    manifest_path = project_output / "docs" / "_run_manifest.json"
    manifest = _json_object(manifest_path)
    if (
        manifest_path.is_file()
        and not manifest_path.is_symlink()
        and manifest.get("run_id") == run_id
    ):
        candidates.append(
            BaselineSupportingArtifact(
                logical_name=f"run_manifest_{run_id}.json",
                source_path=manifest_path,
                kind="manifest",
            )
        )
    return tuple(candidates)


async def _select_baseline(
    *,
    project_id: str,
    requested_run_id: int | None,
) -> tuple[Project, GenerateRun | None]:
    async with async_session_factory() as db:
        project = await db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        query = select(GenerateRun).where(
            GenerateRun.project_id == project_id,
            GenerateRun.status.in_(("completed", "completed_warnings")),
        )
        if requested_run_id is not None:
            query = query.where(GenerateRun.id == requested_run_id)
        else:
            query = query.order_by(
                GenerateRun.completed_at.desc(),
                GenerateRun.started_at.desc(),
                GenerateRun.id.desc(),
            ).limit(1)
        baseline = (await db.execute(query)).scalar_one_or_none()
        if baseline is None and requested_run_id is not None:
            raise HTTPException(status_code=409, detail="No completed legacy baseline is available")
        return project, baseline


async def _response(experiment_id: str, *, include_artifacts: bool = True) -> ExperimentResponse:
    async with async_session_factory() as db:
        repository = SqlAlchemyExperimentRepository(db)
        row = await repository.get(experiment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        baseline = (
            await db.get(GenerateRun, row.baseline_run_id)
            if row.baseline_run_id is not None
            else None
        )
        attempts = tuple(
            (
                await db.execute(
                    select(GenerationTaskAttempt)
                    .join(
                        GenerationTaskRun,
                        GenerationTaskRun.id == GenerationTaskAttempt.task_run_id,
                    )
                    .where(GenerationTaskRun.experiment_id == experiment_id)
                    .order_by(
                        GenerationTaskAttempt.started_at,
                        GenerationTaskAttempt.id,
                    )
                )
            ).scalars()
        )
        recipe_snapshot = (
            await db.get(GenerationRecipeSnapshot, row.recipe_snapshot_id)
            if row.recipe_snapshot_id is not None
            else None
        )
        source_snapshot = (
            await db.get(GenerationSourceSnapshot, row.source_snapshot_id)
            if row.source_snapshot_id is not None
            else None
        )
        source_fingerprint, source_coverage, source_inventory = (
            _project_evidence_response_metadata(source_snapshot)
        )
        artifacts = await repository.list_artifacts(experiment_id)
        candidate_metrics_json = (
            row.metrics_json.get("candidate")
            if isinstance(row.metrics_json, dict)
            and isinstance(row.metrics_json.get("candidate"), dict)
            else {}
        )
        artifact_responses = tuple(
            ArtifactResponse(
                branch=artifact.branch,
                kind=artifact.kind,
                logical_name=artifact.logical_name,
                relative_path=artifact.relative_path,
                sha256=artifact.sha256,
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes,
            )
            for artifact in artifacts
        ) if include_artifacts else ()
        grounded_progress = (
            _grounded_workspace_progress(row.id)
            if row.runtime_id == "opencode-grounded"
            else None
        )
        return ExperimentResponse(
            experiment_id=row.id,
            project_id=row.project_id,
            baseline_run_id=row.baseline_run_id,
            status=row.status,
            comparison_status=row.comparison_status,
            candidate_verdict=row.candidate_verdict,
            runtime_id=row.runtime_id,
            provider_id=row.provider_id,
            model_id=row.model_id,
            source_snapshot_id=row.source_snapshot_id,
            source_snapshot_frozen=row.source_snapshot_id is not None,
            source_snapshot_fingerprint_sha256=source_fingerprint,
            source_coverage=source_coverage,
            source_inventory=source_inventory,
            recipe_snapshot_id=row.recipe_snapshot_id,
            recipe_id=recipe_snapshot.recipe_id if recipe_snapshot is not None else None,
            recipe_version=(
                recipe_snapshot.recipe_version if recipe_snapshot is not None else None
            ),
            baseline_manifest_sha256=row.baseline_manifest_sha256,
            baseline_document_count=sum(
                artifact.branch == "baseline" and artifact.kind == "document"
                for artifact in artifacts
            ),
            artifact_count=len(artifacts),
            publishes=False,
            candidate_not_started=row.started_at is None,
            error_message=row.error_message,
            baseline_metrics=ExperimentBranchMetricsResponse(
                document_count=baseline.doc_count if baseline is not None else 0,
                duration_ms=baseline.duration_ms if baseline is not None else None,
                provider_latency_ms=(
                    baseline.duration_api_ms if baseline is not None else None
                ),
                input_tokens=baseline.input_tokens if baseline is not None else None,
                output_tokens=baseline.output_tokens if baseline is not None else None,
                billed_cost_usd=(
                    str(Decimal(str(baseline.cost_usd)))
                    if baseline is not None and baseline.cost_usd is not None
                    else None
                ),
                quality_score=(
                    baseline.quality_score if baseline is not None else None
                ),
                model_id=baseline.model_name if baseline is not None else None,
                attempt_count=None,
                retry_count=None,
            ),
            candidate_metrics=ExperimentBranchMetricsResponse(
                document_count=sum(
                    artifact.branch == "candidate" and artifact.kind == "document"
                    for artifact in artifacts
                ),
                duration_ms=row.wall_duration_ms,
                provider_latency_ms=_sum_known_attempt_field(
                    attempts,
                    "provider_latency_ms",
                ) or _metric_int(candidate_metrics_json, "provider_latency_ms"),
                input_tokens=(
                    _sum_known_attempt_field(attempts, "input_tokens")
                    if attempts
                    else _metric_int(candidate_metrics_json, "input_tokens")
                ),
                output_tokens=(
                    _sum_known_attempt_field(attempts, "output_tokens")
                    if attempts
                    else _metric_int(candidate_metrics_json, "output_tokens")
                ),
                billed_cost_usd=(
                    _sum_known_attempt_cost(attempts)
                    if attempts
                    else _metric_str(candidate_metrics_json, "billed_cost_usd")
                ),
                quality_score=None,
                model_id=row.model_id,
                attempt_count=len(attempts),
                retry_count=max(0, len(attempts) - len({item.task_run_id for item in attempts})),
            ),
            safety_warnings=tuple(
                item
                for item in candidate_metrics_json.get("safety_warnings", ())
                if isinstance(item, dict)
                and isinstance(item.get("path"), str)
                and isinstance(item.get("category"), str)
                and isinstance(item.get("severity"), str)
            ),
            grounded_progress=grounded_progress,
            artifacts=artifact_responses,
        )


def _grounded_workspace_progress(
    experiment_id: str,
) -> GroundedProgressResponse | None:
    workspace = Path(config.agentic_workspace_root) / experiment_id
    pack = _json_object(workspace / "GROUNDED_EXECUTION_PACK.json")
    raw_roles = pack.get("roles")
    raw_primary = pack.get("required_primary_artifacts")
    if not isinstance(raw_roles, list) or not isinstance(raw_primary, list):
        return None
    roles: list[GroundedRoleProgressResponse] = []
    accepted_total = 0
    required_total = 0
    stage = "preflight"
    for raw_role in raw_roles:
        if not isinstance(raw_role, dict):
            return None
        role_id = raw_role.get("id")
        role_stage = raw_role.get("stage")
        owns = raw_role.get("owns")
        if (
            not isinstance(role_id, str)
            or not isinstance(role_stage, str)
            or not isinstance(owns, list)
            or any(not isinstance(path, str) for path in owns)
        ):
            return None
        accepted = sum(
            _safe_workspace_artifact_exists(workspace, path)
            for path in owns
        )
        accepted_total += accepted
        required_total += len(owns)
        status: Literal["pending", "completed"] = (
            "completed" if accepted == len(owns) else "pending"
        )
        roles.append(
            GroundedRoleProgressResponse(
                role_id=role_id,
                stage=role_stage,
                status=status,
                accepted_artifact_count=accepted,
                owned_artifact_count=len(owns),
            )
        )
        if status == "pending" and stage == "preflight":
            stage = role_stage
    primary_accepted = sum(
        _safe_workspace_artifact_exists(workspace, path)
        for path in raw_primary
        if isinstance(path, str)
    )
    accepted_total += primary_accepted
    required_total += len(raw_primary)
    if roles and all(role.status == "completed" for role in roles):
        stage = (
            "completed"
            if primary_accepted == len(raw_primary)
            else "finalizing"
        )
    return GroundedProgressResponse(
        stage=stage,
        accepted_artifact_count=accepted_total,
        required_artifact_count=required_total,
        roles=tuple(roles),
    )


def _safe_workspace_artifact_exists(workspace: Path, relative_path: str) -> bool:
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return False
    path = workspace.joinpath(*relative.parts)
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    except OSError:
        return False


def _metric_int(metrics: dict[str, object], key: str) -> int | None:
    value = metrics.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _metric_str(metrics: dict[str, object], key: str) -> str | None:
    value = metrics.get(key)
    return str(value) if isinstance(value, str | int | float) else None


def _sum_known_attempt_field(
    attempts: tuple[GenerationTaskAttempt, ...],
    field_name: Literal["provider_latency_ms", "input_tokens", "output_tokens"],
) -> int | None:
    if not attempts:
        return None
    values = tuple(getattr(attempt, field_name) for attempt in attempts)
    if any(value is None for value in values):
        return None
    return sum(int(value) for value in values)


def _sum_known_attempt_cost(
    attempts: tuple[GenerationTaskAttempt, ...],
) -> str | None:
    if not attempts or any(attempt.provider_cost_usd is None for attempt in attempts):
        return None
    return str(
        sum(
            (Decimal(str(attempt.provider_cost_usd)) for attempt in attempts),
            Decimal("0"),
        )
    )


def _project_evidence_response_metadata(
    snapshot: GenerationSourceSnapshot | None,
) -> tuple[
    str | None,
    ProjectEvidenceCoverageResponse | None,
    tuple[ProjectEvidenceSourceStatusResponse, ...],
]:
    metadata = snapshot.snapshot_metadata if snapshot is not None else None
    if not isinstance(metadata, dict) or metadata.get("contract") != "project_evidence":
        return None, None, ()
    fingerprint = metadata.get("fingerprint_sha256")
    coverage = metadata.get("coverage")
    inventory = metadata.get("inventory")
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or not isinstance(coverage, dict)
        or not isinstance(inventory, list)
    ):
        raise ValueError("project evidence snapshot metadata is invalid")
    return (
        fingerprint,
        ProjectEvidenceCoverageResponse.model_validate(coverage),
        tuple(
            ProjectEvidenceSourceStatusResponse.model_validate(value)
            for value in inventory
        ),
    )


async def _source_snapshot_response(
    db,
    *,
    experiment_id: str,
    snapshot: GenerationSourceSnapshot,
) -> SourceSnapshotResponse:
    items = tuple(
        (
            await db.execute(
                select(GenerationSourceSnapshotItem).where(
                    GenerationSourceSnapshotItem.snapshot_id == snapshot.id
                )
            )
        ).scalars()
    )
    fingerprint, coverage, inventory = _project_evidence_response_metadata(snapshot)
    source_kinds = {item.source_kind for item in items}
    source_roles = {item.source_role for item in items}
    source_kinds.update(value.source_kind for value in inventory)
    source_roles.update(value.source_role for value in inventory)
    return SourceSnapshotResponse(
        experiment_id=experiment_id,
        snapshot_id=snapshot.id,
        manifest_sha256=snapshot.manifest_sha256,
        security_state="approved",
        manifest_relative_path=snapshot.manifest_relative_path,
        item_count=len(items),
        source_kinds=tuple(sorted(source_kinds)),
        source_roles=tuple(sorted(source_roles)),
        fingerprint_sha256=fingerprint,
        coverage=coverage,
        inventory=inventory,
        publishes=False,
    )


async def _recipe_snapshot_response(
    db,
    *,
    experiment_id: str,
    snapshot: GenerationRecipeSnapshot,
) -> RecipeSnapshotResponse:
    tasks = tuple(
        (
            await db.execute(
                select(GenerationTaskRun).where(
                    GenerationTaskRun.recipe_snapshot_id == snapshot.id
                )
            )
        ).scalars()
    )
    document_outputs = {
        output_id
        for task in tasks
        if task.task_type == "synthesis"
        for output_id in task.output_ids_json
    }
    return RecipeSnapshotResponse(
        experiment_id=experiment_id,
        snapshot_id=snapshot.id,
        recipe_id=snapshot.recipe_id,
        recipe_version=snapshot.recipe_version,
        graph_sha256=snapshot.graph_sha256,
        effective_scope_sha256=snapshot.effective_scope_sha256,
        manifest_relative_path=snapshot.manifest_relative_path,
        task_count=len(tasks),
        document_output_count=len(document_outputs),
        preflight_required=True,
        publishes=False,
    )


def _timestamp(value) -> str | None:
    return None if value is None else value.isoformat()


def _database_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def _accepted_output_claims(task: GenerationTaskRun) -> tuple[str, ...]:
    result = task.result_json
    if not isinstance(result, dict):
        return ()
    claims = result.get("accepted_output_ids")
    if not isinstance(claims, list | tuple) or any(
        not isinstance(item, str) or not item.strip() for item in claims
    ):
        return ()
    return tuple(claims)


async def _task_response(db, task: GenerationTaskRun) -> ExperimentTaskResponse:
    attempts = tuple(
        (
            await db.execute(
                select(GenerationTaskAttempt)
                .where(GenerationTaskAttempt.task_run_id == task.id)
                .order_by(GenerationTaskAttempt.attempt_number)
            )
        ).scalars()
    )
    events = tuple(
        (
            await db.execute(
                select(GenerationTaskEvent)
                .where(GenerationTaskEvent.task_run_id == task.id)
                .order_by(GenerationTaskEvent.sequence_number)
            )
        ).scalars()
    )
    return ExperimentTaskResponse(
        task_run_id=task.id,
        node_id=task.node_id,
        task_type=task.task_type,
        role=task.role,
        dependencies=tuple(task.dependencies_json),
        output_ids=tuple(task.output_ids_json),
        status=task.status,
        attempt=task.attempt,
        max_attempts=task.max_attempts,
        runtime_id=task.runtime_id,
        provider_id=task.provider_id,
        model_id=task.model_id,
        started_at=_timestamp(task.started_at),
        completed_at=_timestamp(task.completed_at),
        attempts=tuple(
            TaskAttemptResponse(
                attempt_id=attempt.id,
                attempt_number=attempt.attempt_number,
                status=attempt.status,
                started_at=attempt.started_at.isoformat(),
                completed_at=_timestamp(attempt.completed_at),
                wall_duration_ms=attempt.wall_duration_ms,
                provider_latency_ms=attempt.provider_latency_ms,
                input_tokens=attempt.input_tokens,
                output_tokens=attempt.output_tokens,
                cache_read_tokens=attempt.cache_read_tokens,
                provider_cost_usd=attempt.provider_cost_usd,
            )
            for attempt in attempts
        ),
        events=tuple(
            TaskEventResponse(
                event_id=event.id,
                attempt_id=event.attempt_id,
                sequence_number=event.sequence_number,
                event_type=event.event_type,
                occurred_at=event.occurred_at.isoformat(),
            )
            for event in events
        ),
        publishes=False,
    )


@router.post("", response_model=ExperimentResponse, status_code=201)
async def create_experiment(
    body: CreateExperimentRequest,
    request: Request,
) -> ExperimentResponse:
    """Reserve and freeze a legacy baseline without starting candidate execution."""

    _require_feature()
    await require_project_access(request, body.project_id)
    project, baseline_row = await _select_baseline(
        project_id=body.project_id,
        requested_run_id=body.baseline_run_id,
    )
    settings: dict[str, object] = {
        "acknowledge_non_publishing": True,
        "recipe_id": body.recipe_id,
        "recipe_version": body.recipe_version,
        "scope": body.scope,
    }
    experiment_id = str(uuid4())

    async with async_session_factory() as db:
        repository = SqlAlchemyExperimentRepository(db)
        existing = await repository.find_by_idempotency_key(
            project_id=body.project_id,
            idempotency_key=body.idempotency_key,
        )
        if existing is not None:
            return await _response(existing.id)
        reserved = repository.reserve(
            experiment_id=experiment_id,
            project_id=body.project_id,
            baseline_run_id=baseline_row.id if baseline_row is not None else None,
            idempotency_key=body.idempotency_key,
            runtime_id=body.runtime_id,
            provider_id=body.provider_id,
            model_id=body.model_id,
            settings=settings,
        )
        if baseline_row is None:
            reserved.comparison_status = "not_available"
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            winner = await repository.find_by_idempotency_key(
                project_id=body.project_id,
                idempotency_key=body.idempotency_key,
            )
            if winner is None:
                raise
            return await _response(winner.id)

    if baseline_row is None:
        # A legacy baseline is useful for A/B comparison, but it is not required
        # to run an isolated OpenCode candidate against a frozen scanner input.
        return await _response(experiment_id)

    documents_directory = config.project_root / "output" / body.project_id / "docs"
    metrics_path = config.project_root / "output" / body.project_id / "logs" / (
        f"run_metrics_{baseline_row.id}.json"
    )
    baseline = LegacyBaselineRun(
        id=baseline_row.id,
        project_id=body.project_id,
        completed_at=baseline_row.completed_at or baseline_row.started_at,
        documents_directory=documents_directory,
        supporting_artifacts=_supporting_artifacts(body.project_id, baseline_row.id),
        metrics=_json_object(metrics_path),
    )
    memory_repository = InMemoryExperimentRepository((baseline,))
    freezer = BaselineFreezer(
        repository=memory_repository,
        artifact_store_factory=lambda project_id, current_experiment_id: ArtifactStore(
            output_root=config.project_root / "output",
            project_id=project_id,
            experiment_id=current_experiment_id,
        ),
    )
    try:
        frozen = await asyncio.to_thread(
            freezer.freeze,
            BaselineFreezeRequest(
                project_id=project.id,
                baseline_run_id=baseline_row.id,
                idempotency_key=body.idempotency_key,
                experiment_id=experiment_id,
            ),
        )
        async with async_session_factory() as db:
            repository = SqlAlchemyExperimentRepository(db)
            await repository.finalize_baseline(
                experiment=frozen.experiment,
                artifacts=frozen.artifacts,
            )
            await db.commit()
    except Exception as exc:
        async with async_session_factory() as db:
            repository = SqlAlchemyExperimentRepository(db)
            await repository.mark_failed(experiment_id, f"baseline snapshot failed: {type(exc).__name__}")
            await db.commit()
        raise HTTPException(status_code=500, detail="Baseline snapshot failed") from exc
    return await _response(experiment_id)


@router.post(
    "/{experiment_id}/source-snapshot",
    response_model=SourceSnapshotResponse,
    status_code=201,
)
async def create_source_snapshot(
    experiment_id: str,
    body: CreateSourceSnapshotBody,
    request: Request,
    *,
    managed_workspace_root: Path | None = None,
) -> SourceSnapshotResponse:
    """Freeze approved scanner and governed inputs without exposing raw paths."""

    _require_feature()
    async with async_session_factory() as db:
        experiment = await db.get(GenerationExperiment, experiment_id)
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        project_id = experiment.project_id
        use_project_evidence = experiment.runtime_id in {
            "opencode-grounded",
            "openai-agents-grounded",
        }
    await require_project_access(request, project_id)

    try:
        registry, descriptors = _source_composition(
            project_id=project_id,
            sources=body.sources,
            allow_missing=use_project_evidence,
            managed_workspace_root=managed_workspace_root,
            require_bound_scanner_receipt=use_project_evidence,
        )
        if use_project_evidence:
            descriptors = _prepare_managed_supporting_evidence(
                project_id=project_id,
                descriptors=descriptors,
                registry=registry,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with async_session_factory() as db:
        locked = (
            await db.execute(
                select(GenerationExperiment)
                .where(GenerationExperiment.id == experiment_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        if locked.source_snapshot_id is not None:
            existing = await db.get(
                GenerationSourceSnapshot,
                locked.source_snapshot_id,
            )
            if existing is None:
                raise HTTPException(
                    status_code=409,
                    detail="Experiment source snapshot index is inconsistent",
                )
            return await _source_snapshot_response(
                db,
                experiment_id=experiment_id,
                snapshot=existing,
            )
        store = ArtifactStore(
            output_root=config.project_root / "output",
            project_id=project_id,
            experiment_id=experiment_id,
        )
        try:
            repository = SqlAlchemyExperimentRepository(db)
            if use_project_evidence:
                project_snapshot = await asyncio.to_thread(
                    ProjectEvidenceSnapshotService(registry=registry).create,
                    CreateProjectEvidenceSnapshotRequest(
                        descriptors=descriptors,
                        limits=SourceLimits(),
                        snapshot_id=f"project-evidence-{uuid4().hex[:16]}",
                    ),
                )
                project_persistence = await asyncio.to_thread(
                    ProjectEvidenceSnapshotArtifactSink(store).save_snapshot,
                    project_snapshot,
                )
                snapshot_row = await repository.finalize_project_evidence_snapshot(
                    experiment_id=experiment_id,
                    snapshot=project_snapshot,
                    persistence=project_persistence,
                )
            else:
                result = await asyncio.to_thread(
                    SourceSnapshotService(
                        registry=registry,
                        sink=SourceSnapshotArtifactSink(store),
                    ).create,
                    CreateSourceSnapshotRequest(
                        descriptors=descriptors,
                        limits=SourceLimits(),
                        snapshot_id=str(uuid4()),
                    ),
                )
                if result.persistence is None:
                    raise RuntimeError(
                        "source snapshot storage did not return a durable receipt"
                    )
                snapshot_row = await repository.finalize_source_snapshot(
                    experiment_id=experiment_id,
                    snapshot=result.snapshot,
                    persistence=result.persistence,
                )
            await db.commit()
        except (SourceAdapterError, ValueError) as exc:
            await db.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            await db.rollback()
            async with async_session_factory() as failure_db:
                await SqlAlchemyExperimentRepository(failure_db).mark_failed(
                    experiment_id,
                    f"source snapshot failed: {type(exc).__name__}",
                )
                await failure_db.commit()
            raise HTTPException(status_code=500, detail="Source snapshot failed") from exc
    return await _source_snapshot_response(
        db,
        experiment_id=experiment_id,
        snapshot=snapshot_row,
    )


def _prepare_managed_supporting_evidence(
    *,
    project_id: str,
    descriptors: tuple[SourceDescriptor, ...],
    registry: SourceAdapterRegistry,
) -> tuple[SourceDescriptor, ...]:
    """Replace an automatic raw workspace with its redacted derivative.

    Supporting material is additive.  A project containing only implementation
    source, only unsupported binaries, or material that cannot be safely
    prepared must still run the existing scanner-grounded workflow.  In that
    case the original automatic descriptor remains explicitly unreadable in
    the immutable inventory; it is never silently treated as evidence.
    """

    managed = next(
        (
            descriptor
            for descriptor in descriptors
            if descriptor.id == "managed-project-workspace"
        ),
        None,
    )
    if managed is None:
        return descriptors
    preparation_root = (
        config.project_root / "output" / project_id / "supporting-evidence"
    )
    try:
        prepared = SupportingEvidencePreparationService().prepare(
            managed,
            output_root=preparation_root,
            limits=SupportingEvidenceLimits(
                max_file_bytes=config.supporting_evidence_max_file_bytes,
                max_raw_bytes=config.supporting_evidence_max_raw_bytes,
            ),
        )
    except SourceAdapterError:
        return descriptors
    registry.register(
        PreparedDirectoryEvidenceAdapter(allowed_roots=(preparation_root,))
    )
    return tuple(
        prepared.descriptor if descriptor.id == managed.id else descriptor
        for descriptor in descriptors
    )


@router.post(
    "/{experiment_id}/recipe-snapshot",
    response_model=RecipeSnapshotResponse,
    status_code=201,
)
async def create_recipe_snapshot(
    experiment_id: str,
    body: CompileRecipeBody,
    request: Request,
) -> RecipeSnapshotResponse:
    """Compile and freeze one certified generic recipe without executing it."""

    _require_feature()
    async with async_session_factory() as db:
        experiment = await db.get(GenerationExperiment, experiment_id)
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        project_id = experiment.project_id
    await require_project_access(request, project_id)
    try:
        recipe_root, manifest = _resolve_bundled_recipe(
            body.recipe_id,
            body.recipe_version,
        )
        caller_scope = parse_scope(body.scope) if body.scope is not None else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with async_session_factory() as db:
        locked = (
            await db.execute(
                select(GenerationExperiment)
                .where(GenerationExperiment.id == experiment_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        if locked.recipe_snapshot_id is not None:
            existing = await db.get(
                GenerationRecipeSnapshot,
                locked.recipe_snapshot_id,
            )
            if existing is None:
                raise HTTPException(
                    status_code=409,
                    detail="Experiment recipe snapshot index is inconsistent",
                )
            return await _recipe_snapshot_response(
                db,
                experiment_id=experiment_id,
                snapshot=existing,
            )
        if locked.source_snapshot_id is None:
            raise HTTPException(
                status_code=409,
                detail="A finalized source snapshot is required before compiling a recipe",
            )
        source_items = tuple(
            (
                await db.execute(
                    select(GenerationSourceSnapshotItem).where(
                        GenerationSourceSnapshotItem.snapshot_id
                        == locked.source_snapshot_id
                    )
                )
            ).scalars()
        )
        available_source_capabilities: dict[str, frozenset[str]] = {}
        for item in source_items:
            current = available_source_capabilities.get(item.source_role, frozenset())
            available_source_capabilities[item.source_role] = (
                current | frozenset(item.capabilities_json)
            )
        try:
            graph = compile_recipe(
                manifest,
                recipe_root=recipe_root,
                model_capabilities=frozenset(body.model_capabilities),
                available_source_capabilities=available_source_capabilities,
                caller_scope=caller_scope,
            )
            snapshot_id = str(uuid4())
            store = ArtifactStore(
                output_root=config.project_root / "output",
                project_id=project_id,
                experiment_id=experiment_id,
            )
            persistence = await asyncio.to_thread(
                RecipeSnapshotArtifactSink(store).save,
                experiment_id=experiment_id,
                snapshot_id=snapshot_id,
                manifest=manifest,
                graph=graph,
            )
            row = await SqlAlchemyExperimentRepository(db).finalize_recipe_snapshot(
                experiment_id=experiment_id,
                snapshot_id=snapshot_id,
                manifest=manifest,
                graph=graph,
                persistence=persistence,
            )
            await db.commit()
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            await db.rollback()
            async with async_session_factory() as failure_db:
                await SqlAlchemyExperimentRepository(failure_db).mark_failed(
                    experiment_id,
                    f"recipe snapshot failed: {type(exc).__name__}",
                )
                await failure_db.commit()
            raise HTTPException(status_code=500, detail="Recipe snapshot failed") from exc
        return RecipeSnapshotResponse(
            experiment_id=experiment_id,
            snapshot_id=row.id,
            recipe_id=row.recipe_id,
            recipe_version=row.recipe_version,
            graph_sha256=row.graph_sha256,
            effective_scope_sha256=row.effective_scope_sha256,
            manifest_relative_path=row.manifest_relative_path,
            task_count=len(graph.nodes),
            document_output_count=sum(
                node.task_type == "synthesis" for node in graph.nodes
            ),
            preflight_required=True,
            publishes=False,
        )


@router.get(
    "/{experiment_id}/tasks",
    response_model=ExperimentTaskListResponse,
)
async def list_experiment_tasks(
    experiment_id: str,
    request: Request,
) -> ExperimentTaskListResponse:
    """Expose sanitized scheduler progress without runtime payloads or prompts."""

    _require_feature()
    async with async_session_factory() as db:
        experiment = await db.get(GenerationExperiment, experiment_id)
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        project_id = experiment.project_id
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        tasks = tuple(
            (
                await db.execute(
                    select(GenerationTaskRun)
                    .where(GenerationTaskRun.experiment_id == experiment_id)
                    .order_by(GenerationTaskRun.created_at, GenerationTaskRun.id)
                )
            ).scalars()
        )
        responses = tuple(
            [await _task_response(db, task) for task in tasks]
        )
    return ExperimentTaskListResponse(
        experiment_id=experiment_id,
        tasks=responses,
        total=len(responses),
        publishes=False,
    )


@router.post(
    "/{experiment_id}/tasks/{task_run_id}/retry",
    response_model=ExperimentTaskResponse,
)
async def retry_experiment_task(
    experiment_id: str,
    task_run_id: str,
    body: RetryTaskBody,
    request: Request,
) -> ExperimentTaskResponse:
    """Authorize one explicit retry; this endpoint never dispatches a provider."""

    _require_feature()
    del body
    async with async_session_factory() as db:
        task = await db.get(GenerationTaskRun, task_run_id)
        if task is None or task.experiment_id != experiment_id:
            raise HTTPException(status_code=404, detail="Experiment task not found")
        experiment = await db.get(GenerationExperiment, experiment_id)
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        project_id = experiment.project_id
    await require_project_access(request, project_id)
    payload = getattr(request.state, "auth_payload", {}) or {}
    requested_by = f"user:{payload.get('sub', 'unknown')}"
    async with async_session_factory() as db:
        scheduler = TaskSchedulerRepository(db)
        try:
            task = await scheduler.request_retry(
                task_run_id=task_run_id,
                requested_by=requested_by,
            )
            await db.commit()
        except (LookupError, ValueError) as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return await _task_response(db, task)


@router.post(
    "/{experiment_id}/reconcile",
    response_model=ReconcileExperimentResponse,
)
async def reconcile_experiment(
    experiment_id: str,
    body: ReconcileExperimentBody,
    request: Request,
) -> ReconcileExperimentResponse:
    """Reconcile expired leases without dispatching, retrying, or publishing."""

    _require_feature()
    del body
    async with async_session_factory() as db:
        experiment = await db.get(GenerationExperiment, experiment_id)
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        project_id = experiment.project_id
    await require_project_access(request, project_id)

    now = datetime.datetime.now(datetime.timezone.utc)
    async with async_session_factory() as db:
        experiment = await db.get(GenerationExperiment, experiment_id)
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        tasks = tuple(
            (
                await db.execute(
                    select(GenerationTaskRun)
                    .where(GenerationTaskRun.experiment_id == experiment_id)
                    .order_by(GenerationTaskRun.created_at, GenerationTaskRun.id)
                    .with_for_update()
                )
            ).scalars()
        )
        if not tasks:
            raise HTTPException(
                status_code=409,
                detail="A compiled recipe task graph is required before reconciliation",
            )
        try:
            snapshot = ExperimentRecoverySnapshot(
                experiment_id=experiment_id,
                tasks=tuple(
                    SanitizedTaskSnapshot(
                        task_id=task.id,
                        status=SanitizedTaskStatus(task.status),
                        declared_outputs=tuple(task.output_ids_json),
                        accepted_output_claims=_accepted_output_claims(task),
                        lease=(
                            SanitizedLeaseSnapshot(
                                expires_at=_database_utc(task.lease_expires_at),
                                # Once a worker owns a lease, absence of a
                                # persisted provider ID cannot prove no request
                                # escaped before a crash.
                                external_call_may_have_started=True,
                            )
                            if task.status in {"leased", "running"}
                            and task.lease_expires_at is not None
                            else None
                        ),
                    )
                    for task in tasks
                ),
            )
            decision = derive_recovery(snapshot=snapshot, now=now)
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Experiment task ledger is not recoverable: {exc}",
            ) from exc

        scheduler = TaskSchedulerRepository(db)
        recovered = await scheduler.recover_expired_leases(
            experiment_id=experiment_id,
            now=now,
        )
        await scheduler.skip_blocked_tasks(experiment_id=experiment_id, now=now)
        await db.commit()
        return ReconcileExperimentResponse(
            experiment_id=experiment_id,
            persisted_status=experiment.status,
            derived_status=decision.state.value,
            recovered_expired_task_ids=recovered,
            manual_retry_task_ids=decision.manual_retry_task_ids,
            missing_required_outputs=decision.missing_required_outputs,
            duplicate_required_outputs=decision.duplicate_required_outputs,
            tasks=tuple(
                TaskRecoveryResponse(
                    task_run_id=task.task_id,
                    state=task.state.value,
                    manual_retry_required=task.manual_retry_required,
                    reason=task.reason,
                )
                for task in decision.task_recovery
            ),
            dispatch_performed=False,
            publishes=False,
        )


@router.post(
    "/{experiment_id}/quarantine",
    response_model=RetentionActionResponse,
)
async def quarantine_experiment(
    experiment_id: str,
    body: RetentionActionBody,
    request: Request,
) -> RetentionActionResponse:
    """Move one old terminal experiment into recoverable project-local trash."""

    _require_feature()
    del body
    now = datetime.datetime.now(datetime.timezone.utc)
    async with async_session_factory() as db:
        experiment = (
            await db.execute(
                select(GenerationExperiment)
                .where(GenerationExperiment.id == experiment_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        project_id = experiment.project_id
        await require_project_access(request, project_id)
        live_lease = (
            await db.scalar(
                select(GenerationTaskRun.id)
                .where(
                    GenerationTaskRun.experiment_id == experiment_id,
                    GenerationTaskRun.status.in_(("leased", "running")),
                    GenerationTaskRun.lease_expires_at.is_not(None),
                    GenerationTaskRun.lease_expires_at > now,
                )
                .limit(1)
            )
        ) is not None
        candidate = RetentionCandidate(
            project_id=project_id,
            experiment_id=experiment_id,
            status=experiment.status,
            completed_at=(
                _database_utc(experiment.completed_at)
                if experiment.completed_at is not None
                else None
            ),
            has_live_lease=live_lease,
        )
        cutoff = now - datetime.timedelta(days=config.agentic_retention_days)
        try:
            receipt = await asyncio.to_thread(
                ExperimentRetention(
                    output_root=config.project_root / "output"
                ).quarantine,
                candidate=candidate,
                cutoff=cutoff,
                acknowledged=True,
                now=now,
            )
        except (ExperimentRetentionError, ValueError) as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await db.commit()
    return RetentionActionResponse(
        experiment_id=experiment_id,
        project_id=project_id,
        state="quarantined",
        receipt_sha256=receipt.receipt_sha256,
        source_relative_path=receipt.source_relative_path,
        trash_relative_path=receipt.trash_relative_path,
        hard_deleted=False,
        publishes=False,
    )


@router.post(
    "/{experiment_id}/restore",
    response_model=RetentionActionResponse,
)
async def restore_experiment(
    experiment_id: str,
    body: RetentionActionBody,
    request: Request,
) -> RetentionActionResponse:
    """Restore one quarantined experiment without changing its immutable receipt."""

    _require_feature()
    del body
    async with async_session_factory() as db:
        experiment = await db.get(GenerationExperiment, experiment_id)
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        project_id = experiment.project_id
    await require_project_access(request, project_id)
    try:
        restored = await asyncio.to_thread(
            ExperimentRetention(output_root=config.project_root / "output").restore,
            project_id=project_id,
            experiment_id=experiment_id,
            acknowledged=True,
        )
    except (ExperimentRetentionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RetentionActionResponse(
        experiment_id=experiment_id,
        project_id=project_id,
        state="restored",
        receipt_sha256=restored.retention.receipt_sha256,
        source_relative_path=restored.retention.source_relative_path,
        trash_relative_path=restored.retention.trash_relative_path,
        hard_deleted=False,
        publishes=False,
    )


def _quick_candidate_sources(
    *,
    project_id: str,
    experiment_id: str,
    relative_paths: tuple[str, ...],
) -> tuple[Path, ...]:
    experiment_root = (
        config.project_root / "output" / project_id / "experiments" / experiment_id
    ).resolve()
    resolved: list[Path] = []
    for value in relative_paths:
        relative = PurePosixPath(value)
        if (
            relative.is_absolute()
            or not relative.parts
            or relative.parts[0] != "shared"
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("frozen source path is outside the shared experiment branch")
        candidate = experiment_root.joinpath(*relative.parts).resolve(strict=True)
        candidate.relative_to(experiment_root)
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("frozen source content is not a regular file")
        resolved.append(candidate)
    if not resolved:
        raise ValueError("experiment has no frozen source content")
    return tuple(resolved)


def _project_evidence_persistence(
    snapshot: GenerationSourceSnapshot,
) -> ProjectEvidenceSnapshotPersistence:
    metadata = snapshot.snapshot_metadata
    if not isinstance(metadata, dict) or metadata.get("contract") != "project_evidence":
        raise ValueError("Grounded experiment has no project evidence snapshot")
    fingerprint = metadata.get("fingerprint_sha256")
    content_paths = metadata.get("content_relative_paths")
    manifest_size = metadata.get("manifest_size_bytes")
    if (
        not isinstance(fingerprint, str)
        or not isinstance(content_paths, dict)
        or any(
            not isinstance(item_id, str) or not isinstance(relative_path, str)
            for item_id, relative_path in content_paths.items()
        )
        or not isinstance(manifest_size, int)
        or isinstance(manifest_size, bool)
    ):
        raise ValueError("project evidence persistence metadata is invalid")
    return ProjectEvidenceSnapshotPersistence(
        snapshot_id=snapshot.id,
        fingerprint_sha256=fingerprint,
        content_relative_paths=content_paths,
        manifest_relative_path=snapshot.manifest_relative_path,
        manifest_sha256=snapshot.manifest_sha256,
        manifest_size_bytes=manifest_size,
    )


def _load_verified_project_evidence_snapshot(
    *,
    project_id: str,
    experiment_id: str,
    snapshot_row: GenerationSourceSnapshot,
    indexed_items: tuple[GenerationSourceSnapshotItem, ...],
) -> ProjectEvidenceSnapshot:
    if (
        snapshot_row.experiment_id != experiment_id
        or snapshot_row.schema_version != PROJECT_EVIDENCE_SCHEMA_VERSION
        or snapshot_row.security_state != "approved"
        or any(item.snapshot_id != snapshot_row.id for item in indexed_items)
    ):
        raise ValueError("project evidence database linkage is invalid")
    persistence = _project_evidence_persistence(snapshot_row)
    snapshot = ProjectEvidenceSnapshotArtifactSink(
        ArtifactStore(
            output_root=config.project_root / "output",
            project_id=project_id,
            experiment_id=experiment_id,
        )
    ).load_snapshot(persistence)
    _verify_project_evidence_database_index(
        snapshot=snapshot,
        persistence=persistence,
        indexed_items=indexed_items,
    )
    return snapshot


def _verify_project_evidence_database_index(
    *,
    snapshot: ProjectEvidenceSnapshot,
    persistence: ProjectEvidenceSnapshotPersistence,
    indexed_items: tuple[GenerationSourceSnapshotItem, ...],
) -> None:
    indexed_by_id = {item.id: item for item in indexed_items}
    if len(indexed_by_id) != len(indexed_items):
        raise ValueError("project evidence database index has duplicate item ids")
    expected_ids = {
        str(prepared["item_id"])
        for source in snapshot.sources
        for prepared in source.prepared_representations
    }
    if set(indexed_by_id) != expected_ids:
        raise ValueError("project evidence database index differs from persisted snapshot")

    for source in snapshot.sources:
        raw_by_id = {
            str(reference["item_id"]): reference
            for reference in source.raw_evidence_references
        }
        for prepared in source.prepared_representations:
            item_id = str(prepared["item_id"])
            raw = raw_by_id.get(item_id)
            if raw is None:
                raise ValueError("project evidence database index has no raw reference")
            row = indexed_by_id[item_id]
            expected_metadata = {
                "opaque_logical_identity": str(prepared["logical_identity"]),
                "project_evidence_fingerprint_sha256": snapshot.fingerprint_sha256,
                "source_status": source.status.value,
            }
            if (
                row.snapshot_id != snapshot.id
                or row.source_descriptor_id != source.descriptor_id
                or row.logical_identity != prepared["logical_path"]
                or row.source_kind != source.source_kind
                or row.source_role != source.source_role
                or tuple(row.capabilities_json) != source.capabilities
                or row.locator != raw["locator"]
                or row.raw_sha256 != raw["raw_sha256"]
                or row.extracted_sha256 != prepared["extracted_sha256"]
                or row.media_type != prepared["media_type"]
                or row.size_bytes != raw["size_bytes"]
                or row.extracted_size_bytes != prepared["size_bytes"]
                or row.content_relative_path
                != persistence.content_relative_paths[item_id]
                or row.trust_level != source.trust_level
                or row.redaction_state != "approved"
                or row.adapter_id != "project-evidence"
                or row.adapter_version != str(snapshot.schema_version)
                or row.item_metadata != expected_metadata
            ):
                raise ValueError(
                    "project evidence database index differs from persisted snapshot"
                )


async def _resolve_experiment_recipe(db, experiment: GenerationExperiment):
    """Resolve the certified recipe through the experiment's immutable snapshot."""

    if experiment.recipe_snapshot_id is None:
        raise LookupError("prepared experiment has no recipe snapshot")
    snapshot = await db.get(
        GenerationRecipeSnapshot,
        experiment.recipe_snapshot_id,
    )
    if snapshot is None or snapshot.experiment_id != experiment.id:
        raise LookupError("prepared experiment recipe snapshot is unavailable")
    return _resolve_bundled_recipe(
        snapshot.recipe_id,
        snapshot.recipe_version,
    )


async def _run_quick_opencode_candidate(
    *,
    experiment_id: str,
    project_id: str,
    provider_id: str,
    model_id: str,
    output_names: tuple[str, ...],
    candidate_mode: Literal["single_agent", "orchestrated", "grounded"] = "single_agent",
    grounded_runtime_id: str = OPENCODE_GROUNDED_RUNTIME_ID,
    resume_from_experiment_id: str | None = None,
) -> None:
    """Run after the HTTP receipt is returned; failures remain visible in the ledger."""

    try:
        if candidate_mode == "grounded" and grounded_runtime_id not in {
            OPENCODE_GROUNDED_RUNTIME_ID,
            OPENAI_AGENTS_GROUNDED_RUNTIME_ID,
        }:
            raise ValueError("configured Grounded runtime is not supported")
        async with async_session_factory() as db:
            experiment = await db.get(GenerationExperiment, experiment_id)
            if experiment is None or experiment.source_snapshot_id is None:
                raise LookupError("prepared experiment is unavailable")
            source_snapshot = await db.get(
                GenerationSourceSnapshot,
                experiment.source_snapshot_id,
            )
            if source_snapshot is None:
                raise LookupError("prepared source snapshot is unavailable")
            source_items = tuple(
                (
                    await db.execute(
                        select(GenerationSourceSnapshotItem)
                        .where(
                            GenerationSourceSnapshotItem.snapshot_id
                            == experiment.source_snapshot_id
                        )
                        .order_by(GenerationSourceSnapshotItem.logical_identity)
                    )
                ).scalars()
            )
            project_evidence_snapshot = None
            if candidate_mode == "grounded":
                project_evidence_snapshot = await asyncio.to_thread(
                    _load_verified_project_evidence_snapshot,
                    project_id=project_id,
                    experiment_id=experiment_id,
                    snapshot_row=source_snapshot,
                    indexed_items=source_items,
                )
            execution_source_items = (
                tuple(
                    item
                    for item in source_items
                    if item.source_kind == "intodoc_scan"
                )
                if candidate_mode == "grounded"
                else source_items
            )
            source_paths = _quick_candidate_sources(
                project_id=project_id,
                experiment_id=experiment_id,
                relative_paths=tuple(
                    item.content_relative_path for item in execution_source_items
                ),
            )
            frozen_sources = tuple(
                (item.logical_identity, path)
                for item, path in zip(execution_source_items, source_paths, strict=True)
            )
            execution_pack = None
            if candidate_mode == "grounded":
                _, recipe_manifest = await _resolve_experiment_recipe(db, experiment)
                execution_pack = load_grounded_execution_pack(recipe_manifest)
            await SqlAlchemyExperimentRepository(db).mark_candidate_running(experiment_id)
            await db.commit()

        timeout = httpx.Timeout(
            connect=10.0,
            read=float(config.agentic_max_wall_seconds),
            write=60.0,
            pool=10.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            runner_class = {
                "single_agent": AgenticLegacyOpenCodeRunner,
                "orchestrated": OrchestratedOpenCodeRunner,
                "grounded": GroundedRuntimeRunner,
            }[candidate_mode]
            opencode_client = OpenCodeHttpClient(
                base_url=config.agentic_opencode_base_url,
                username=config.agentic_opencode_username,
                password=config.agentic_opencode_password,
                client=http_client,
            )
            runner_kwargs = {
                "output_root": config.project_root / "output",
                "project_root": config.project_root,
                "workspace_root": config.agentic_workspace_root,
                "timeout_seconds": config.agentic_max_wall_seconds,
            }
            if candidate_mode == "grounded":
                if grounded_runtime_id == OPENAI_AGENTS_GROUNDED_RUNTIME_ID:
                    composed = await compose_openai_agents_grounded_runtime(
                        settings=config,
                        provider_id=provider_id,
                        model_id=model_id,
                        workspace_root=config.agentic_workspace_root,
                    )
                    runner_kwargs["runtime"] = composed.runtime
                    runner_kwargs["task_builder"] = composed.task_builder
                    runner_kwargs["max_billed_cost_usd"] = (
                        config.agentic_max_cost_usd
                        if config.agentic_max_cost_usd > 0
                        else None
                    )
                else:
                    runner_kwargs["runtime"] = OpenCodeAgentRuntime(opencode_client)
                    runner_kwargs["task_builder"] = OpenCodeGroundedTaskBuilder()
            else:
                runner_kwargs["client"] = opencode_client
            if execution_pack is not None:
                runner_kwargs["execution_pack"] = execution_pack
            run_kwargs = {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "source_items": frozen_sources,
                "output_names": output_names,
                "provider_id": provider_id,
                "model_id": model_id,
            }
            if candidate_mode == "grounded":
                run_kwargs["legacy_baseline_available"] = (
                    experiment.baseline_run_id is not None
                )
                run_kwargs["project_evidence_snapshot"] = project_evidence_snapshot
                if resume_from_experiment_id is not None:
                    run_kwargs["resume_from_experiment_id"] = resume_from_experiment_id
            result = await runner_class(**runner_kwargs).run(**run_kwargs)

        async with async_session_factory() as db:
            await SqlAlchemyExperimentRepository(db).finalize_quick_candidate(
                experiment_id=experiment_id,
                artifacts=result.artifacts,
                manifest_sha256=result.manifest_sha256,
                elapsed_ms=result.elapsed_ms,
                session_id=result.session_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_tokens=result.cache_read_tokens,
                billed_cost_usd=result.billed_cost_usd,
                safety_warnings=result.safety_warnings,
            )
            await db.commit()
    except Exception as exc:
        detail = str(exc).strip()
        runtime_label = (
            "OpenAI Agents"
            if grounded_runtime_id == OPENAI_AGENTS_GROUNDED_RUNTIME_ID
            else "OpenCode"
        )
        failure = f"{runtime_label} candidate failed: {type(exc).__name__}"
        if detail:
            failure = f"{failure}: {detail}"
        async with async_session_factory() as db:
            await SqlAlchemyExperimentRepository(db).mark_failed(
                experiment_id,
                failure[:2000],
            )
            await db.commit()
        await experiment_log_manager.emit(experiment_id, failure[:2000], level="error")
        await experiment_log_manager.emit_done(experiment_id)


@router.post("/quick-run", response_model=ExperimentResponse, status_code=202)
async def start_quick_opencode_candidate(
    body: QuickOpenCodeRunRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ExperimentResponse:
    """Freeze inputs and queue a non-publishing OpenCode candidate from Generate Docs."""

    _require_feature()
    if body.candidate_mode != "grounded" and body.grounded_runtime_id is not None:
        raise HTTPException(
            status_code=400,
            detail="A Grounded runtime can be selected only for Grounded execution",
        )
    if body.candidate_mode != "grounded" and body.acknowledge_paid_execution is not None:
        raise HTTPException(
            status_code=400,
            detail="Paid execution acknowledgement applies only to Grounded execution",
        )
    if body.candidate_mode != "grounded" and body.resume_from_experiment_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Resuming a prior attempt applies only to Grounded execution",
        )
    grounded_runtime_id = (
        body.grounded_runtime_id or config.agentic_grounded_runtime_id
        if body.candidate_mode == "grounded"
        else OPENCODE_GROUNDED_RUNTIME_ID
    )
    if grounded_runtime_id not in {
        OPENCODE_GROUNDED_RUNTIME_ID,
        OPENAI_AGENTS_GROUNDED_RUNTIME_ID,
    }:
        raise HTTPException(status_code=503, detail="Grounded runtime is not configured")
    if (
        grounded_runtime_id != OPENAI_AGENTS_GROUNDED_RUNTIME_ID
        and body.acknowledge_paid_execution is not None
    ):
        raise HTTPException(
            status_code=400,
            detail="Paid execution acknowledgement applies only to OpenAI Agents",
        )
    if grounded_runtime_id == OPENCODE_GROUNDED_RUNTIME_ID:
        if config.agentic_opencode_version != "1.18.4":
            raise HTTPException(status_code=503, detail="OpenCode runtime pin is not configured")
        if not config.agentic_opencode_password:
            raise HTTPException(
                status_code=503,
                detail="OpenCode runtime authentication is unavailable",
            )
    elif body.candidate_mode == "grounded":
        if not config.agentic_paid_execution_enabled:
            raise HTTPException(
                status_code=503,
                detail="OpenAI Agents paid execution is disabled by the operator",
            )
        if body.acknowledge_paid_execution is not True:
            raise HTTPException(
                status_code=400,
                detail="OpenAI Agents requires explicit paid execution acknowledgement",
            )
    if body.additional_sources and body.candidate_mode != "grounded":
        raise HTTPException(
            status_code=400,
            detail="Additional project evidence is available only to Grounded execution",
        )
    if any(
        source.source_kind == "intodoc_scan" for source in body.additional_sources
    ):
        raise HTTPException(
            status_code=400,
            detail="The project-owned IntoDoc scanner source cannot be duplicated",
        )
    if any(
        source.source_role.casefold() == "implementation"
        for source in body.additional_sources
    ):
        raise HTTPException(
            status_code=400,
            detail="Additional project evidence cannot claim implementation authority",
        )
    # Server-side default resolution. Grounded runs no longer require the caller
    # to pin a provider/model: when omitted, the platform default (config knob,
    # ``deepseek-v4-pro`` out of the box) is used. An explicit caller value
    # always wins. Non-grounded modes fall back to the same default.
    provider_id = body.provider_id or config.agentic_grounded_default_provider_id
    model_id = body.model_id or config.agentic_grounded_default_model_id
    if grounded_runtime_id == OPENAI_AGENTS_GROUNDED_RUNTIME_ID:
        try:
            await compose_openai_agents_grounded_runtime(
                settings=config,
                provider_id=provider_id,
                model_id=model_id,
                workspace_root=config.agentic_workspace_root,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail="OpenAI Agents Grounded runtime preflight failed",
            ) from exc
    try:
        _, manifest = _resolve_bundled_recipe(body.recipe_id, body.recipe_version)
        if body.candidate_mode == "grounded":
            load_grounded_execution_pack(manifest)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Certified OpenCode documentation recipe is unavailable",
        ) from exc
    output_names = tuple(
        output.path
        for output in manifest.output_contracts
        if output.media_type == "text/markdown"
    )
    if not output_names or any(PurePosixPath(name).name != name for name in output_names):
        raise HTTPException(
            status_code=400,
            detail="Quick OpenCode recipes require flat Markdown document outputs",
        )

    experiment = await create_experiment(
        CreateExperimentRequest(
            project_id=body.project_id,
            baseline_run_id=body.baseline_run_id,
            recipe_id=body.recipe_id,
            recipe_version=body.recipe_version,
            runtime_id={
                "single_agent": "opencode",
                "orchestrated": "opencode-orchestrated",
                "grounded": (
                    "openai-agents-grounded"
                    if grounded_runtime_id == OPENAI_AGENTS_GROUNDED_RUNTIME_ID
                    else "opencode-grounded"
                ),
            }[body.candidate_mode],
            provider_id=provider_id,
            model_id=model_id,
            scope=body.scope,
            acknowledge_non_publishing=True,
            idempotency_key=body.idempotency_key,
        ),
        request,
    )
    await create_source_snapshot(
        experiment.experiment_id,
        CreateSourceSnapshotBody(
            sources=(
                SourceInputRequest(
                    id="current-intodoc-scan",
                    source_kind="intodoc_scan",
                    source_role="implementation",
                    capabilities={"searchable_text", "structured_records"},
                ),
                *body.additional_sources,
            )
        ),
        request,
        managed_workspace_root=(
            await _managed_project_workspace(body.project_id)
            if body.candidate_mode == "grounded"
            else None
        ),
    )
    await create_recipe_snapshot(
        experiment.experiment_id,
        CompileRecipeBody(
            recipe_id=body.recipe_id,
            recipe_version=body.recipe_version,
            model_capabilities={"tools"},
            scope=body.scope,
        ),
        request,
    )
    prepared = await _response(experiment.experiment_id)
    if prepared.status == "queued":
        background_tasks.add_task(
            _run_quick_opencode_candidate,
            experiment_id=prepared.experiment_id,
            project_id=prepared.project_id,
            provider_id=provider_id,
            model_id=model_id,
            output_names=output_names,
            candidate_mode=body.candidate_mode,
            grounded_runtime_id=grounded_runtime_id,
            resume_from_experiment_id=body.resume_from_experiment_id,
        )
    return prepared


@router.post(
    "/{experiment_id}/recover-opencode",
    response_model=ExperimentResponse,
)
async def recover_failed_opencode_candidate(
    experiment_id: str,
    body: RecoverOpenCodeRunRequest,
    request: Request,
) -> ExperimentResponse:
    """Finalize a proven complete orchestrated workspace without model traffic."""

    _require_feature()
    if config.agentic_opencode_version != "1.18.4":
        raise HTTPException(
            status_code=503,
            detail="OpenCode runtime pin is not configured",
        )
    if not config.agentic_opencode_password:
        raise HTTPException(
            status_code=503,
            detail="OpenCode runtime authentication is unavailable",
        )

    async with async_session_factory() as db:
        experiment = await db.get(GenerationExperiment, experiment_id)
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        await require_project_access(request, experiment.project_id)
        if experiment.status != "failed":
            raise HTTPException(
                status_code=409,
                detail="Only a failed OpenCode experiment can be recovered",
            )
        if experiment.runtime_id != "opencode-orchestrated":
            raise HTTPException(
                status_code=409,
                detail="Recovery supports orchestrated OpenCode experiments only",
            )
        if not experiment.provider_id or not experiment.model_id:
            raise HTTPException(
                status_code=409,
                detail="Experiment has no recoverable provider/model identity",
            )
        if experiment.source_snapshot_id is None:
            raise HTTPException(
                status_code=409,
                detail="Experiment has no frozen source snapshot",
            )
        source_items = tuple(
            (
                await db.execute(
                    select(GenerationSourceSnapshotItem)
                    .where(
                        GenerationSourceSnapshotItem.snapshot_id
                        == experiment.source_snapshot_id
                    )
                    .order_by(GenerationSourceSnapshotItem.logical_identity)
                )
            ).scalars()
        )
        source_paths = _quick_candidate_sources(
            project_id=experiment.project_id,
            experiment_id=experiment_id,
            relative_paths=tuple(
                item.content_relative_path for item in source_items
            ),
        )
        frozen_sources = tuple(
            (item.logical_identity, path)
            for item, path in zip(source_items, source_paths, strict=True)
        )
        try:
            _, recipe = await _resolve_experiment_recipe(db, experiment)
        except (LookupError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="Experiment recipe is no longer available",
            ) from exc
        output_names = tuple(
            output.path
            for output in recipe.output_contracts
            if output.media_type == "text/markdown"
        )
        project_id = experiment.project_id
        provider_id = experiment.provider_id
        model_id = experiment.model_id
        await SqlAlchemyExperimentRepository(db).mark_candidate_recovering(
            experiment_id
        )
        await db.commit()

    try:
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            result = await OrchestratedCandidateRecovery(
                client=OpenCodeHttpClient(
                    base_url=config.agentic_opencode_base_url,
                    username=config.agentic_opencode_username,
                    password=config.agentic_opencode_password,
                    client=http_client,
                ),
                output_root=config.project_root / "output",
                workspace_root=config.agentic_workspace_root,
            ).recover(
                project_id=project_id,
                experiment_id=experiment_id,
                session_id=body.parent_session_id,
                source_items=frozen_sources,
                output_names=output_names,
                provider_id=provider_id,
                model_id=model_id,
            )
        async with async_session_factory() as db:
            await SqlAlchemyExperimentRepository(db).finalize_quick_candidate(
                experiment_id=experiment_id,
                artifacts=result.artifacts,
                manifest_sha256=result.manifest_sha256,
                elapsed_ms=result.elapsed_ms,
                session_id=result.session_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_tokens=result.cache_read_tokens,
                billed_cost_usd=result.billed_cost_usd,
                safety_warnings=result.safety_warnings,
            )
            await db.commit()
    except Exception as exc:
        detail = str(exc).strip()
        failure = f"OpenCode recovery failed: {type(exc).__name__}"
        if detail:
            failure = f"{failure}: {detail}"
        async with async_session_factory() as db:
            await SqlAlchemyExperimentRepository(db).mark_failed(
                experiment_id,
                failure[:2000],
            )
            await db.commit()
        raise HTTPException(status_code=409, detail=failure[:2000]) from exc

    return await _response(experiment_id)


@router.get("/capabilities", response_model=ExperimentCapabilitiesResponse)
async def get_experiment_capabilities() -> ExperimentCapabilitiesResponse:
    """Describe the safe experiment surface without probing providers or secrets."""

    _require_feature()
    blockers = []
    if not config.agentic_opencode_password:
        blockers.append("OpenCode runtime authentication is unavailable")
    if not config.doc_generation_deepseek_api_key:
        blockers.append("DeepSeek credentials are unavailable")
    opencode_blockers: list[str] = []
    if config.agentic_opencode_version != "1.18.4":
        opencode_blockers.append("OpenCode runtime pin is not configured")
    if not config.agentic_opencode_password:
        opencode_blockers.append("OpenCode runtime authentication is unavailable")
    openai_blockers: list[str] = []
    route_config_error: str | None = None
    try:
        configured_routes = configured_openai_agents_routes(config)
    except ValueError:
        configured_routes = ()
        route_config_error = "OpenAI Agents provider route allowlist is invalid"
    ready_routes = tuple(
        route for route in configured_routes if os.environ.get(route.api_key_env_var)
    )
    openai_models = tuple(route.model_id for route in configured_routes)
    if not config.agentic_openai_agents_enabled:
        openai_blockers.append("OpenAI Agents Grounded runtime is disabled")
    if not config.agentic_paid_execution_enabled:
        openai_blockers.append("OpenAI Agents paid execution is disabled")
    if route_config_error:
        openai_blockers.append(route_config_error)
    elif not configured_routes:
        openai_blockers.append("OpenAI Agents model allowlist is empty")
    elif not ready_routes:
        openai_blockers.append("OpenAI Agents credentials are unavailable")
    return ExperimentCapabilitiesResponse(
        candidate_dispatch_available=(not opencode_blockers or not openai_blockers),
        paid_execution_enabled=config.agentic_paid_execution_enabled,
        opencode_version=config.agentic_opencode_version,
        supported_source_kinds=(
            "intodoc_scan",
            "directory_evidence",
            "tabular_file",
            "runtime_export",
        ),
        recipes=_bundled_recipe_capabilities(),
        grounded_runtimes=(
            GroundedRuntimeCapabilityResponse(
                runtime_id="opencode",
                available=not opencode_blockers,
                provider_id="",
                model_ids=(),
                paid_execution_confirmation_required=False,
                routes=(),
                blockers=tuple(opencode_blockers),
            ),
            GroundedRuntimeCapabilityResponse(
                runtime_id="openai-agents",
                available=not openai_blockers,
                provider_id=(configured_routes[0].provider_id if configured_routes else ""),
                model_ids=openai_models,
                paid_execution_confirmation_required=True,
                routes=tuple(
                    GroundedModelRouteCapabilityResponse(
                        provider_id=route.provider_id,
                        model_id=route.model_id,
                        display_name=route.display_name,
                    )
                    for route in ready_routes
                ),
                blockers=tuple(openai_blockers),
            ),
        ),
        execution_blockers=tuple(blockers),
    )


@router.get("", response_model=tuple[ExperimentResponse, ...])
async def list_experiments(
    request: Request,
    project_id: str,
    limit: int = Query(default=50, ge=1, le=100),
) -> tuple[ExperimentResponse, ...]:
    _require_feature()
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        rows = await SqlAlchemyExperimentRepository(db).list_for_project(project_id, limit=limit)
    return tuple(
        [await _response(row.id, include_artifacts=False) for row in rows]
    )


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment(experiment_id: str, request: Request) -> ExperimentResponse:
    _require_feature()
    async with async_session_factory() as db:
        row = await SqlAlchemyExperimentRepository(db).get(experiment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        project_id = row.project_id
    await require_project_access(request, project_id)
    return await _response(experiment_id)


@router.get("/{experiment_id}/events/stream")
async def stream_experiment_events(experiment_id: str, request: Request):
    """SSE endpoint for live Grounded/OpenCode role progress and tool activity.

    Mirrors GET /logs/stream, keyed by experiment_id instead of project_id --
    see mcp_server.services.log_manager.experiment_log_manager.
    """

    _require_feature()
    async with async_session_factory() as db:
        row = await SqlAlchemyExperimentRepository(db).get(experiment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        project_id = row.project_id
    await require_project_access(request, project_id)
    queue = experiment_log_manager.subscribe(experiment_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if event.get("project_id") != experiment_id:
                        continue
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            experiment_log_manager.unsubscribe(experiment_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
