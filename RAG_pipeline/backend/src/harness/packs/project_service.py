"""Governed Pack catalog, Style Pack persistence, and stack activation."""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Document, RuntimeSourceRow
from src.db.models.harness_pack import (
    HarnessOrganizationGovernancePolicyRow,
    HarnessPackActivationSnapshotRow,
    HarnessPrivateAnalysisPackRow,
    HarnessPrivateAnalysisPackVersionRow,
    HarnessProjectPackActivationRow,
    HarnessStylePackVersionRow,
)
from src.harness.packs.catalog import PackCatalog, PackCatalogError, manifest_hash
from src.harness.packs.composition import PackStackCompiler, PackStackError
from src.harness.packs.models import (
    CommunicationOption,
    CompiledPackStack,
    FormattingOption,
    MinimumQualityThresholds,
    OrganizationGovernancePolicy,
    OutputOrganization,
    PackActivation,
    PackArtifactV3,
    PackAuthority,
    PackGovernance,
    PackManifest,
    PackMutability,
    PackOwner,
    PackPhase,
    PackRecommendation,
    PackScope,
    PackSnapshot,
    PackStackSelection,
    PackStackSnapshot,
    PackTrust,
    PackType,
    ProtectedInvariant,
    ResolvedPackSet,
    StyleAudience,
    StyleField,
    StyleGovernancePolicy,
    StylePresentation,
    StyleTone,
    TechnicalDepth,
)
from src.harness.packs.runtime import build_pack_catalog, build_pack_reference_sets
from src.harness.registry import CapabilityRegistry

DEFAULT_WORKFLOW_KEY = "default"
DEFAULT_ORGANIZATION_ID = "default"
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][a-zA-Z0-9.-]+)?$")
_WORKFLOW_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_STYLE_PACK_ID = re.compile(r"^style\.[a-z][a-z0-9_.-]{0,73}$")
_STYLE_SLUG = re.compile(r"^[a-z][a-z0-9.-]{0,47}$")
_STYLE_FIELDS = frozenset({"id", "name", "version", "presentation"})


class PackServiceError(ValueError):
    """A safe client-facing governance or persistence error."""

    status_code = 409


class PackValidationError(PackServiceError):
    """A malformed Style Pack request rather than an unsafe state conflict."""

    status_code = 422


@dataclass(frozen=True)
class StylePackRecord:
    manifest: PackManifest
    scope: PackScope
    project_id: str | None
    owner_user_id: int
    quality_score: float | None
    evaluation_score: float | None
    evaluation_metadata: dict[str, Any] | None
    evaluated_by_user_id: int | None
    review_status: str
    promotion_eligible: bool


@dataclass(frozen=True)
class GovernedProjectPackActivation:
    project_id: str
    workflow_key: str
    active_pack_ids: tuple[str, ...]
    selection: PackStackSelection
    snapshot: PackSnapshot
    compiled_snapshot: PackStackSnapshot
    presentation: StylePresentation | None
    manifests: tuple[PackManifest, ...]


@dataclass(frozen=True)
class PrivateAnalysisPackExecution:
    """Verified, non-authority metadata for one private Analysis Pack version."""

    receipt: dict[str, Any]
    artifact: PackArtifactV3
    missing_optional_capability_ids: tuple[str, ...]

    def provider_payload(self) -> dict[str, Any]:
        return {
            "receipt": self.receipt,
            "artifact": self.artifact.model_dump(mode="json"),
            "missing_optional_capability_ids": list(self.missing_optional_capability_ids),
        }


def default_organization_policy() -> OrganizationGovernancePolicy:
    """The persisted single-organization default until tenancy is introduced."""
    return OrganizationGovernancePolicy(
        id="org.default",
        required_certified_pack_ids=frozenset({"nexus.documentation"}),
        allowed_functional_pack_ids=frozenset({"nexus.data-integration"}),
        permitted_style=StyleGovernancePolicy(
            permitted_fields=frozenset(StyleField),
            allowed_audiences=frozenset(StyleAudience),
            allowed_tones=frozenset(StyleTone),
            allowed_technical_depths=frozenset(TechnicalDepth),
            allowed_output_organizations=frozenset(OutputOrganization),
            allowed_formatting=frozenset(FormattingOption),
            allowed_communication=frozenset(CommunicationOption),
        ),
        minimum_quality=MinimumQualityThresholds(quality_score=0.8, evaluation_score=0.8),
        protected_invariants=frozenset(ProtectedInvariant),
    )


def _semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if not match:
        raise PackValidationError("Pack version must use semantic versioning")
    return tuple(int(part) for part in match.groups())


def _style_manifest(
    *, pack_id: str, name: str, version: str, presentation: StylePresentation, scope: PackScope
) -> PackManifest:
    if not _STYLE_PACK_ID.fullmatch(pack_id):
        raise PackValidationError("Style Pack ids must start with style. and use lowercase Pack ids")
    owner = PackOwner.USER if scope is PackScope.USER else PackOwner.PROJECT
    authority = PackAuthority.USER_MANAGED if scope is PackScope.USER else PackAuthority.PROJECT_MANAGED
    return PackManifest(
        id=pack_id,
        name=name,
        version=version,
        pack_type=PackType.STYLE,
        governance=PackGovernance(
            phase=PackPhase.STYLE,
            owner=owner,
            authority=authority,
            scope=scope,
            mutability=PackMutability.EDITABLE,
            trust=PackTrust.UNREVIEWED,
        ),
        style=presentation,
    )


def _canonical_style_pack_id(
    value: str, *, scope: PackScope, owner_user_id: int, project_id: str | None
) -> str:
    expected_prefix = (
        f"style.user.{owner_user_id}."
        if scope is PackScope.USER
        else f"style.project.{hashlib.sha256((project_id or '').encode('utf-8')).hexdigest()[:12]}."
    )
    if value.startswith(("style.user.", "style.project.")) and not value.startswith(expected_prefix):
        raise PackValidationError("Style Pack scope namespace is server-owned")
    slug = value.removeprefix(expected_prefix).removeprefix("style.")
    if not _STYLE_SLUG.fullmatch(slug):
        raise PackValidationError("Style Pack id must contain a lowercase logical slug")
    canonical = expected_prefix + slug
    if not _STYLE_PACK_ID.fullmatch(canonical):
        raise PackValidationError("Style Pack canonical id is too long")
    return canonical


def parse_style_manifest(
    payload: dict[str, Any], *, scope: PackScope, owner_user_id: int, project_id: str | None
) -> PackManifest:
    """Accept only the bounded presentation namespace, never semantic Pack content."""
    unexpected = sorted(set(payload) - _STYLE_FIELDS)
    if unexpected:
        raise PackValidationError(
            "Style Packs accept only id, name, version, and presentation; rejected: "
            + ", ".join(unexpected)
        )
    missing = sorted(_STYLE_FIELDS - set(payload))
    if missing:
        raise PackValidationError("Style Pack fields are required: " + ", ".join(missing))
    try:
        requested_id = payload["id"]
        name = payload["name"]
        version = payload["version"]
        if not isinstance(requested_id, str) or not isinstance(name, str) or not isinstance(version, str):
            raise TypeError("id, name, and version must be strings")
        pack_id = _canonical_style_pack_id(
            requested_id, scope=scope, owner_user_id=owner_user_id, project_id=project_id
        )
        _semver(version)
        presentation = StylePresentation.model_validate_json(
            json.dumps(payload["presentation"]), strict=True
        )
        return _style_manifest(
            pack_id=pack_id, name=name, version=version, presentation=presentation, scope=scope
        )
    except (TypeError, ValueError, ValidationError) as exc:
        if isinstance(exc, PackServiceError):
            raise
        raise PackValidationError(f"Invalid Style Pack: {exc}") from exc


class ProjectPackService:
    def __init__(self, db: AsyncSession, registry: CapabilityRegistry) -> None:
        self._db = db
        self._registry = registry

    async def organization_policy(
        self, organization_id: str = DEFAULT_ORGANIZATION_ID
    ) -> OrganizationGovernancePolicy:
        row = await self._db.scalar(
            select(HarnessOrganizationGovernancePolicyRow).where(
                HarnessOrganizationGovernancePolicyRow.organization_id == organization_id
            )
        )
        if row is None and organization_id == DEFAULT_ORGANIZATION_ID:
            # SQLite-focused tests do not run Alembic. Production receives this row from 061.
            policy = default_organization_policy()
            row = HarnessOrganizationGovernancePolicyRow(
                organization_id=organization_id,
                policy=policy.model_dump(mode="json"),
            )
            self._db.add(row)
            await self._db.flush()
            return policy
        if row is None:
            raise PackServiceError("Organization governance policy is unavailable")
        try:
            return OrganizationGovernancePolicy.model_validate_json(json.dumps(row.policy), strict=True)
        except ValidationError as exc:
            raise PackServiceError("Persisted organization governance policy is invalid") from exc

    async def catalog(self, project_id: str | None = None, user_id: int | None = None) -> PackCatalog:
        library = list(build_pack_catalog(self._registry).definitions())
        for manifest in library:
            governance = manifest.governance
            if governance is None or governance.phase is PackPhase.STYLE:
                raise PackServiceError(f"library Pack {manifest.id} is not certified governance content")
            if governance.trust is not PackTrust.CERTIFIED or governance.mutability is not PackMutability.IMMUTABLE:
                raise PackServiceError(f"library Pack {manifest.id} is not immutable certified content")
        styles = await self._latest_style_records(project_id=project_id, user_id=user_id)
        try:
            return PackCatalog(
                [*library, *(record.manifest for record in styles)],
                allowed_references=build_pack_reference_sets(self._registry),
            )
        except PackCatalogError as exc:
            raise PackServiceError(str(exc)) from exc

    async def available(
        self, project_id: str | None = None, user_id: int | None = None
    ) -> tuple[PackManifest, ...]:
        return (await self.catalog(project_id, user_id)).definitions()

    async def style_packs(
        self, *, scope: PackScope, user_id: int, project_id: str | None = None
    ) -> tuple[StylePackRecord, ...]:
        if scope is PackScope.USER:
            return tuple(await self._latest_style_records(user_id=user_id, only_scope=scope))
        if scope is PackScope.PROJECT and project_id:
            return tuple(await self._latest_style_records(project_id=project_id, only_scope=scope))
        raise PackValidationError("Style Pack scope must be user or project")

    async def create_style(
        self,
        *,
        scope: PackScope,
        payload: dict[str, Any],
        user_id: int,
        project_id: str | None = None,
    ) -> StylePackRecord:
        self._validate_style_scope(scope, project_id)
        manifest = parse_style_manifest(
            payload, scope=scope, owner_user_id=user_id, project_id=project_id
        )
        duplicate = await self._latest_style_row(
            scope=scope, pack_id=manifest.id, user_id=user_id, project_id=project_id
        )
        if duplicate is not None:
            raise PackServiceError("Style Pack already exists; save a new version instead")
        row = self._style_row(manifest, scope=scope, project_id=project_id, user_id=user_id)
        self._db.add(row)
        await self._db.commit()
        return self._style_record(row)

    async def save_style_version(
        self,
        *,
        scope: PackScope,
        pack_id: str,
        payload: dict[str, Any],
        user_id: int,
        project_id: str | None = None,
    ) -> StylePackRecord:
        self._validate_style_scope(scope, project_id)
        manifest = parse_style_manifest(
            payload, scope=scope, owner_user_id=user_id, project_id=project_id
        )
        if manifest.id != pack_id:
            raise PackValidationError("Style Pack id cannot change between versions")
        latest = await self._latest_style_row(
            scope=scope, pack_id=pack_id, user_id=user_id, project_id=project_id
        )
        if latest is None:
            raise PackServiceError("Style Pack was not found")
        if _semver(manifest.version) <= _semver(latest.version):
            raise PackValidationError("New Style Pack version must be greater than the current version")
        row = self._style_row(manifest, scope=scope, project_id=project_id, user_id=user_id)
        self._db.add(row)
        await self._db.commit()
        return self._style_record(row)

    async def record_style_quality(
        self,
        *,
        scope: PackScope,
        pack_id: str,
        quality_score: float,
        evaluation_score: float,
        evaluation_metadata: dict[str, Any],
        evaluator_user_id: int,
        owner_user_id: int | None = None,
        project_id: str | None = None,
    ) -> StylePackRecord:
        self._validate_style_scope(scope, project_id)
        if not 0 <= quality_score <= 1 or not 0 <= evaluation_score <= 1:
            raise PackValidationError("Quality and evaluation scores must be between 0 and 1")
        try:
            json.dumps(evaluation_metadata)
        except (TypeError, ValueError) as exc:
            raise PackValidationError("Evaluation metadata must be JSON-compatible") from exc
        row = await self._latest_style_row(
            scope=scope,
            pack_id=pack_id,
            user_id=owner_user_id if scope is PackScope.USER and owner_user_id is not None else evaluator_user_id,
            project_id=project_id,
        )
        if row is None:
            raise PackServiceError("Style Pack was not found")
        policy = await self.organization_policy()
        eligible = (
            quality_score >= policy.minimum_quality.quality_score
            and evaluation_score >= policy.minimum_quality.evaluation_score
        )
        row.quality_score = quality_score
        row.evaluation_score = evaluation_score
        row.evaluation_metadata = evaluation_metadata
        row.evaluated_by_user_id = evaluator_user_id
        row.promotion_eligible = eligible
        row.review_status = "promotion_eligible" if eligible else "draft"
        await self._db.commit()
        return self._style_record(row)

    async def recommendations(
        self, project_id: str, user_id: int | None = None
    ) -> tuple[PackRecommendation, ...]:
        catalog = await self.catalog(project_id, user_id)
        has_documents = bool(
            (await self._db.execute(
                select(Document.id).where(Document.project_id == project_id).limit(1)
            )).scalar_one_or_none()
        )
        runtime_sources = list(
            (await self._db.execute(
                select(RuntimeSourceRow.source_type).where(
                    RuntimeSourceRow.project_id == project_id,
                    RuntimeSourceRow.enabled.is_(True),
                )
            )).scalars()
        )
        results: list[PackRecommendation] = []
        for manifest in catalog.definitions():
            governance = manifest.governance
            if governance is None:
                continue
            reasons: list[str] = []
            confidence = 0.45
            domains = set(manifest.default_scopes.domains)
            if governance.phase is PackPhase.STYLE:
                confidence += 0.2
                reasons.append("Presentation-only Style Pack available for this project")
            if "documentation" in domains:
                confidence += 0.24
                reasons.append("Supports project documentation workflows")
                if has_documents:
                    confidence += 0.14
                    reasons.append("Project already has indexed documents")
            if runtime_sources and ({"operations", "data-integration"} & domains):
                confidence += 0.27
                reasons.append("Enabled runtime source metadata matches operational workflows")
            if not reasons:
                reasons.append("Available for this project")
            results.append(
                PackRecommendation(
                    pack_id=manifest.id,
                    confidence=round(min(confidence, 0.98), 2),
                    reasons=tuple(reasons[:3]),
                )
            )
        return tuple(sorted(results, key=lambda item: (-item.confidence, item.pack_id)))

    async def current_activation(
        self, project_id: str, workflow_key: str = DEFAULT_WORKFLOW_KEY
    ) -> GovernedProjectPackActivation | None:
        """Return the UI-ready governed stack, including its compiled presentation."""
        return await self.current_governed_activation(project_id, workflow_key)

    async def current_governed_activation(
        self, project_id: str, workflow_key: str = DEFAULT_WORKFLOW_KEY
    ) -> GovernedProjectPackActivation | None:
        _validate_workflow_key(workflow_key)
        row = await self._activation_row(project_id, workflow_key)
        if row is None:
            return None
        snapshot_row = await self._db.get(HarnessPackActivationSnapshotRow, row.snapshot_id)
        if snapshot_row is None:
            raise PackServiceError("Pack activation snapshot is unavailable")
        if (
            snapshot_row.stack_selection is None
            or snapshot_row.compiled_stack_snapshot is None
            or snapshot_row.compiled_stack_hash is None
        ):
            raise PackServiceError("Legacy Pack activation is quarantined and cannot be used")
        compiled_snapshot = _parse_compiled_snapshot(snapshot_row.compiled_stack_snapshot)
        if compiled_snapshot.snapshot_hash != snapshot_row.compiled_stack_hash:
            raise PackServiceError("Governed Pack activation snapshot hash is invalid")
        manifests = tuple(
            PackManifest.model_validate_json(json.dumps(item), strict=True)
            for item in snapshot_row.resolved_manifests
        )
        return GovernedProjectPackActivation(
            project_id=project_id,
            workflow_key=workflow_key,
            active_pack_ids=tuple(snapshot_row.active_pack_ids),
            selection=_parse_stack_selection(snapshot_row.stack_selection),
            snapshot=_parse_snapshot(snapshot_row.resolved_snapshot),
            compiled_snapshot=compiled_snapshot,
            presentation=(
                _parse_presentation(snapshot_row.compiled_presentation)
                if snapshot_row.compiled_presentation is not None
                else _presentation_from_manifests(snapshot_row.resolved_manifests)
            ),
            manifests=manifests,
        )

    async def activate(
        self,
        *,
        project_id: str,
        workflow_key: str,
        user_id: int,
        selection: PackStackSelection | None = None,
        active_pack_ids: Iterable[str] | None = None,
    ) -> GovernedProjectPackActivation:
        _validate_workflow_key(workflow_key)
        catalog = await self.catalog(project_id, user_id)
        if selection is None:
            selection = await self._selection_from_legacy_ids(catalog, active_pack_ids or ())
        roots = frozenset().union(
            selection.foundation_pack_ids,
            selection.functional_pack_ids,
            selection.style_pack_ids,
        )
        try:
            legacy_resolved = catalog.resolve(PackActivation(enabled_pack_ids=roots))
            compiled = PackStackCompiler(catalog, await self.organization_policy()).compile(selection)
        except (PackCatalogError, PackStackError, ValidationError) as exc:
            raise PackServiceError(str(exc)) from exc
        snapshot_row = await self._persist_snapshot(
            project_id=project_id,
            workflow_key=workflow_key,
            resolved=legacy_resolved,
            selection=selection,
            compiled=compiled,
            user_id=user_id,
        )
        current = await self._activation_row(project_id, workflow_key)
        now = datetime.datetime.now(datetime.UTC)
        if current is None:
            self._db.add(HarnessProjectPackActivationRow(
                project_id=project_id,
                workflow_key=workflow_key,
                snapshot_id=snapshot_row.id,
                updated_by_user_id=user_id,
                updated_at=now,
            ))
        else:
            current.snapshot_id = snapshot_row.id
            current.updated_by_user_id = user_id
            current.updated_at = now
        await self._db.commit()
        return GovernedProjectPackActivation(
            project_id=project_id,
            workflow_key=workflow_key,
            active_pack_ids=tuple(snapshot_row.active_pack_ids),
            selection=selection,
            snapshot=legacy_resolved.snapshot,
            compiled_snapshot=compiled.snapshot,
            presentation=compiled.presentation,
            manifests=compiled.manifests,
        )

    async def resolve_for_execution(
        self, project_id: str, workflow_key: str = DEFAULT_WORKFLOW_KEY
    ) -> ResolvedPackSet | None:
        """Compatibility resolver backed exclusively by the persisted governed stack."""
        compiled = await self.resolve_governed_for_execution(project_id, workflow_key)
        if compiled is None:
            return None
        active_ids = tuple(sorted({
            compiled.snapshot.foundation_pack_id,
            *compiled.snapshot.functional_pack_ids,
            *compiled.snapshot.style_pack_ids,
        }))
        compatibility_snapshot = PackSnapshot(
            active_pack_ids=active_ids,
            resolved_packs=compiled.snapshot.resolved_packs,
            snapshot_hash=_legacy_snapshot_hash(active_ids, compiled.snapshot.resolved_packs),
        )
        return ResolvedPackSet(manifests=compiled.manifests, snapshot=compatibility_snapshot)

    async def resolve_governed_for_execution(
        self, project_id: str, workflow_key: str = DEFAULT_WORKFLOW_KEY
    ) -> CompiledPackStack | None:
        """Hydrate the exact immutable stack and presentation selected at activation time."""
        active = await self.current_governed_activation(project_id, workflow_key)
        if active is None and workflow_key != DEFAULT_WORKFLOW_KEY:
            active = await self.current_governed_activation(project_id, DEFAULT_WORKFLOW_KEY)
        if active is None:
            return None
        return CompiledPackStack(
            manifests=active.manifests,
            presentation=active.presentation,
            snapshot=active.compiled_snapshot,
        )

    async def resolve_private_analysis_for_execution(
        self, project_id: str, workflow_key: str = DEFAULT_WORKFLOW_KEY
    ) -> tuple[PrivateAnalysisPackExecution, ...]:
        """Hydrate exact private versions pinned by the current activation.

        Private artifacts are verified data-only metadata. They never enter Pack
        compilation and therefore cannot grant capabilities or alter governance.
        """
        snapshot_row = await self._execution_snapshot_row(project_id, workflow_key)
        if snapshot_row is None or not snapshot_row.private_version_receipts:
            return ()
        receipts = snapshot_row.private_version_receipts
        if not isinstance(receipts, list) or len(receipts) > 20:
            raise PackServiceError("Private Analysis Pack activation receipt is invalid")

        available = {item.id for item in self._registry.list(enabled_only=True)}
        resolved: list[PrivateAnalysisPackExecution] = []
        seen_versions: set[int] = set()
        seen_hashes: set[str] = set()
        for raw_receipt in receipts:
            receipt = _private_receipt(raw_receipt)
            version_id = receipt["version_id"]
            content_hash = receipt["content_hash"]
            if version_id in seen_versions or content_hash in seen_hashes:
                raise PackServiceError("Private Analysis Pack activation contains duplicate exact versions")
            seen_versions.add(version_id)
            seen_hashes.add(content_hash)
            version_row = await self._db.get(HarnessPrivateAnalysisPackVersionRow, version_id)
            if version_row is None:
                raise PackServiceError("Private Analysis Pack version is unavailable")
            pack_row = await self._db.get(HarnessPrivateAnalysisPackRow, version_row.private_pack_id)
            if (
                pack_row is None
                or pack_row.pack_id != receipt["pack_id"]
                or version_row.version != receipt["version"]
                or version_row.content_hash != content_hash
            ):
                raise PackServiceError("Private Analysis Pack receipt does not match its immutable version")
            artifact = PackArtifactV3.model_validate_json(json.dumps(version_row.artifact), strict=True)
            if _private_artifact_hash(artifact) != content_hash:
                raise PackServiceError("Private Analysis Pack artifact hash verification failed")
            required_missing = sorted(
                requirement.capability_id
                for requirement in artifact.capability_requirements
                if requirement.required and requirement.capability_id not in available
            )
            if required_missing:
                raise PackServiceError(
                    "Private Analysis Pack required capabilities are unavailable: "
                    + ", ".join(required_missing)
                )
            optional_missing = tuple(sorted(
                requirement.capability_id
                for requirement in artifact.capability_requirements
                if not requirement.required and requirement.capability_id not in available
            ))
            resolved.append(
                PrivateAnalysisPackExecution(
                    receipt=receipt,
                    artifact=artifact,
                    missing_optional_capability_ids=optional_missing,
                )
            )
        return tuple(resolved)

    async def create_custom(self, **_: Any) -> PackManifest:
        raise PackServiceError("Custom semantic Packs are legacy; create a presentation-only Style Pack")

    async def save_custom_version(self, **_: Any) -> PackManifest:
        raise PackServiceError("Custom semantic Packs are legacy; create a presentation-only Style Pack")

    async def _selection_from_legacy_ids(
        self, catalog: PackCatalog, active_pack_ids: Iterable[str]
    ) -> PackStackSelection:
        by_id = {manifest.id: manifest for manifest in catalog.definitions()}
        layers: dict[PackPhase, set[str]] = {
            PackPhase.FOUNDATION: set(), PackPhase.FUNCTIONAL: set(), PackPhase.STYLE: set()
        }
        for pack_id in active_pack_ids:
            manifest = by_id.get(pack_id)
            if manifest is None or manifest.governance is None:
                raise PackServiceError(f"Pack {pack_id} is legacy or unknown and cannot be activated")
            layers[manifest.governance.phase].add(pack_id)
        if not layers[PackPhase.FOUNDATION]:
            selected_ids = frozenset().union(
                layers[PackPhase.FUNCTIONAL], layers[PackPhase.STYLE]
            )
            try:
                closure = catalog.resolve(PackActivation(enabled_pack_ids=selected_ids)).manifests
            except PackCatalogError as exc:
                raise PackServiceError(str(exc)) from exc
            policy = await self.organization_policy()
            by_id = {manifest.id: manifest for manifest in catalog.definitions()}
            derived = {
                manifest.id
                for manifest in closure
                if manifest.governance and manifest.governance.phase is PackPhase.FOUNDATION
            }
            derived.update(
                pack_id
                for pack_id in policy.required_certified_pack_ids
                if (
                    (manifest := by_id.get(pack_id)) is not None
                    and manifest.governance is not None
                    and manifest.governance.phase is PackPhase.FOUNDATION
                )
            )
            layers[PackPhase.FOUNDATION].update(derived)
        try:
            return PackStackSelection(
                foundation_pack_ids=frozenset(layers[PackPhase.FOUNDATION]),
                functional_pack_ids=frozenset(layers[PackPhase.FUNCTIONAL]),
                style_pack_ids=frozenset(layers[PackPhase.STYLE]),
            )
        except ValidationError as exc:
            raise PackValidationError("Activation requires explicit selection of exactly one foundation Pack") from exc

    async def _persist_snapshot(
        self,
        *,
        project_id: str,
        workflow_key: str,
        resolved: ResolvedPackSet,
        selection: PackStackSelection,
        compiled: CompiledPackStack,
        user_id: int,
    ) -> HarnessPackActivationSnapshotRow:
        existing = await self._snapshot_for(resolved.snapshot.snapshot_hash)
        if existing is not None:
            return existing
        row = HarnessPackActivationSnapshotRow(
            project_id=project_id,
            workflow_key=workflow_key,
            active_pack_ids=list(resolved.snapshot.active_pack_ids),
            resolved_snapshot=resolved.snapshot.model_dump(mode="json"),
            resolved_manifests=[item.model_dump(mode="json") for item in compiled.manifests],
            snapshot_hash=resolved.snapshot.snapshot_hash,
            stack_selection=selection.model_dump(mode="json"),
            compiled_stack_snapshot=compiled.snapshot.model_dump(mode="json"),
            compiled_stack_hash=compiled.snapshot.snapshot_hash,
            compiled_presentation=(
                compiled.presentation.model_dump(mode="json") if compiled.presentation else None
            ),
            created_by_user_id=user_id,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self._db.add(row)
        await self._db.flush()
        return row

    async def _latest_style_records(
        self,
        *,
        project_id: str | None = None,
        user_id: int | None = None,
        only_scope: PackScope | None = None,
    ) -> list[StylePackRecord]:
        conditions = []
        if only_scope is PackScope.USER:
            if user_id is None:
                return []
            conditions.append(and_(HarnessStylePackVersionRow.scope == PackScope.USER.value, HarnessStylePackVersionRow.owner_user_id == user_id))
        elif only_scope is PackScope.PROJECT:
            if project_id is None:
                return []
            conditions.append(and_(HarnessStylePackVersionRow.scope == PackScope.PROJECT.value, HarnessStylePackVersionRow.project_id == project_id))
        else:
            if user_id is not None:
                conditions.append(and_(HarnessStylePackVersionRow.scope == PackScope.USER.value, HarnessStylePackVersionRow.owner_user_id == user_id))
            if project_id is not None:
                conditions.append(and_(HarnessStylePackVersionRow.scope == PackScope.PROJECT.value, HarnessStylePackVersionRow.project_id == project_id))
        if not conditions:
            return []
        rows = list((await self._db.execute(
            select(HarnessStylePackVersionRow).where(or_(*conditions)).order_by(
                HarnessStylePackVersionRow.pack_id, desc(HarnessStylePackVersionRow.id)
            )
        )).scalars())
        latest: dict[str, HarnessStylePackVersionRow] = {}
        for row in rows:
            latest.setdefault(row.pack_id, row)
        return [self._style_record(row) for row in latest.values()]

    async def _latest_style_row(
        self, *, scope: PackScope, pack_id: str, user_id: int, project_id: str | None
    ) -> HarnessStylePackVersionRow | None:
        clauses = [HarnessStylePackVersionRow.scope == scope.value, HarnessStylePackVersionRow.pack_id == pack_id]
        if scope is PackScope.USER:
            clauses.append(HarnessStylePackVersionRow.owner_user_id == user_id)
        else:
            clauses.append(HarnessStylePackVersionRow.project_id == project_id)
        return await self._db.scalar(
            select(HarnessStylePackVersionRow).where(*clauses).order_by(
                desc(HarnessStylePackVersionRow.id)
            ).limit(1)
        )

    @staticmethod
    def _style_row(
        manifest: PackManifest, *, scope: PackScope, project_id: str | None, user_id: int
    ) -> HarnessStylePackVersionRow:
        assert manifest.style is not None
        return HarnessStylePackVersionRow(
            pack_id=manifest.id,
            version=manifest.version,
            scope=scope.value,
            project_id=project_id,
            owner_user_id=user_id,
            name=manifest.name,
            presentation=manifest.style.model_dump(mode="json"),
            content_hash=manifest_hash(manifest),
            review_status="draft",
            promotion_eligible=False,
        )

    @staticmethod
    def _style_record(row: HarnessStylePackVersionRow) -> StylePackRecord:
        scope = PackScope(row.scope)
        presentation = StylePresentation.model_validate_json(json.dumps(row.presentation), strict=True)
        return StylePackRecord(
            manifest=_style_manifest(
                pack_id=row.pack_id, name=row.name, version=row.version,
                presentation=presentation, scope=scope,
            ),
            scope=scope,
            project_id=row.project_id,
            owner_user_id=row.owner_user_id,
            quality_score=row.quality_score,
            evaluation_score=row.evaluation_score,
            evaluation_metadata=row.evaluation_metadata,
            evaluated_by_user_id=row.evaluated_by_user_id,
            review_status=row.review_status,
            promotion_eligible=row.promotion_eligible,
        )

    @staticmethod
    def _validate_style_scope(scope: PackScope, project_id: str | None) -> None:
        if scope is PackScope.USER and project_id is None:
            return
        if scope is PackScope.PROJECT and project_id:
            return
        raise PackValidationError("Style Pack scope and project binding are inconsistent")

    async def _activation_row(
        self, project_id: str, workflow_key: str
    ) -> HarnessProjectPackActivationRow | None:
        return await self._db.scalar(
            select(HarnessProjectPackActivationRow).where(
                HarnessProjectPackActivationRow.project_id == project_id,
                HarnessProjectPackActivationRow.workflow_key == workflow_key,
            ).limit(1)
        )

    async def _snapshot_for(self, snapshot_hash: str) -> HarnessPackActivationSnapshotRow | None:
        return await self._db.scalar(
            select(HarnessPackActivationSnapshotRow).where(
                HarnessPackActivationSnapshotRow.snapshot_hash == snapshot_hash
            ).limit(1)
        )

    async def _execution_snapshot_row(
        self, project_id: str, workflow_key: str
    ) -> HarnessPackActivationSnapshotRow | None:
        active = await self._activation_row(project_id, workflow_key)
        if active is None and workflow_key != DEFAULT_WORKFLOW_KEY:
            active = await self._activation_row(project_id, DEFAULT_WORKFLOW_KEY)
        if active is None:
            return None
        snapshot = await self._db.get(HarnessPackActivationSnapshotRow, active.snapshot_id)
        if snapshot is None:
            raise PackServiceError("Pack activation snapshot is unavailable")
        return snapshot


def _validate_workflow_key(value: str) -> None:
    if not _WORKFLOW_KEY.fullmatch(value):
        raise PackValidationError(
            "Workflow key must use lowercase letters, numbers, dots, underscores, or hyphens"
        )


def _parse_snapshot(value: dict[str, Any]) -> PackSnapshot:
    return PackSnapshot.model_validate_json(json.dumps(value), strict=True)


def _parse_stack_selection(value: dict[str, Any]) -> PackStackSelection:
    return PackStackSelection.model_validate_json(json.dumps(value), strict=True)


def _parse_compiled_snapshot(value: dict[str, Any]) -> PackStackSnapshot:
    return PackStackSnapshot.model_validate_json(json.dumps(value), strict=True)


def _parse_presentation(value: dict[str, Any]) -> StylePresentation:
    return StylePresentation.model_validate_json(json.dumps(value), strict=True)


def _private_artifact_hash(artifact: PackArtifactV3) -> str:
    canonical = json.dumps(
        artifact.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _private_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "version_id", "pack_id", "version", "content_hash", "audience_render"
    }:
        raise PackServiceError("Private Analysis Pack activation receipt is invalid")
    version_id = value.get("version_id")
    pack_id = value.get("pack_id")
    version = value.get("version")
    content_hash = value.get("content_hash")
    audience = value.get("audience_render")
    if (
        not isinstance(version_id, int)
        or version_id < 1
        or not isinstance(pack_id, str)
        or not re.fullmatch(r"^[a-z][a-z0-9_.-]{1,79}$", pack_id)
        or not isinstance(version, str)
        or not version
        or not isinstance(content_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", content_hash)
        or not isinstance(audience, dict)
        or set(audience) - {"seniority", "domain_familiarity", "decision_horizon", "detail_level"}
    ):
        raise PackServiceError("Private Analysis Pack activation receipt is invalid")
    allowed = {
        "seniority": {"executive", "director", "manager", "practitioner"},
        "domain_familiarity": {"introductory", "working", "expert"},
        "decision_horizon": {"immediate", "near_term", "strategic"},
        "detail_level": {"brief", "standard", "detailed"},
    }
    if any(not isinstance(item, str) or item not in allowed[key] for key, item in audience.items()):
        raise PackServiceError("Private Analysis Pack activation receipt is invalid")
    return {
        "version_id": version_id,
        "pack_id": pack_id,
        "version": version,
        "content_hash": content_hash,
        "audience_render": dict(audience),
    }


def _legacy_snapshot_hash(active_pack_ids: tuple[str, ...], identities: tuple[Any, ...]) -> str:
    payload = {
        "active_pack_ids": active_pack_ids,
        "resolved_packs": [identity.model_dump(mode="json") for identity in identities],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _presentation_from_manifests(value: list[dict[str, Any]]) -> StylePresentation | None:
    styles = [
        PackManifest.model_validate_json(json.dumps(item), strict=True).style
        for item in value
    ]
    return next((style for style in styles if style is not None), None)
