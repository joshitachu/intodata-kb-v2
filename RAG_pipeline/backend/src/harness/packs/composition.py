"""Governed, deterministic composition of Nexus Pack layers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from pydantic import ValidationError

from src.harness.packs.catalog import PackCatalog, PackCatalogError, manifest_hash
from src.harness.packs.models import (
    CompiledPackStack,
    FormattingOption,
    OrganizationGovernancePolicy,
    PackActivation,
    PackIdentity,
    PackManifest,
    PackPhase,
    PackStackSelection,
    PackStackSnapshot,
    PackTrust,
    StyleField,
    StylePresentation,
)


class PackStackError(ValueError):
    """Raised when a Pack selection cannot satisfy governance constraints."""


def governance_policy_hash(policy: OrganizationGovernancePolicy) -> str:
    return _canonical_hash(policy.model_dump(mode="json"))


class PackStackCompiler:
    """Compile immutable Pack layers without allowing lower-layer overrides."""

    def __init__(self, catalog: PackCatalog, policy: OrganizationGovernancePolicy) -> None:
        self._catalog = catalog
        self._policy = policy

    def compile(self, selection: PackStackSelection) -> CompiledPackStack:
        self._validate_disjoint_selection(selection)
        selected_ids = frozenset().union(
            selection.foundation_pack_ids,
            selection.functional_pack_ids,
            selection.style_pack_ids,
        )
        try:
            resolved = self._catalog.resolve(PackActivation(enabled_pack_ids=selected_ids))
        except (PackCatalogError, ValidationError) as exc:
            raise PackStackError(str(exc)) from exc

        root_manifests = {manifest.id: manifest for manifest in self._catalog.definitions()}
        self._validate_selected_layer(root_manifests, selection.foundation_pack_ids, PackPhase.FOUNDATION)
        self._validate_selected_layer(root_manifests, selection.functional_pack_ids, PackPhase.FUNCTIONAL)
        self._validate_selected_layer(root_manifests, selection.style_pack_ids, PackPhase.STYLE)

        manifests = resolved.manifests
        self._validate_declared_governance(manifests)
        self._validate_dependencies(manifests)
        foundation_id = self._validate_foundation(manifests, selection)
        self._validate_lower_layers_only_add(manifests)
        self._validate_functional_packs(manifests)
        self._validate_required_certified_packs(manifests)
        self._validate_minimum_quality(manifests)
        presentation = self._compile_presentation(manifests)

        identities = tuple(
            PackIdentity(id=manifest.id, version=manifest.version, content_hash=manifest_hash(manifest))
            for manifest in manifests
        )
        functional_ids = tuple(
            manifest.id
            for manifest in manifests
            if manifest.governance and manifest.governance.phase is PackPhase.FUNCTIONAL
        )
        style_ids = tuple(
            manifest.id
            for manifest in manifests
            if manifest.governance and manifest.governance.phase is PackPhase.STYLE
        )
        policy_hash = governance_policy_hash(self._policy)
        snapshot_payload = {
            "policy_id": self._policy.id,
            "policy_hash": policy_hash,
            "foundation_pack_id": foundation_id,
            "functional_pack_ids": functional_ids,
            "style_pack_ids": style_ids,
            "resolved_packs": [identity.model_dump(mode="json") for identity in identities],
            "presentation": presentation.model_dump(mode="json") if presentation else None,
        }
        snapshot = PackStackSnapshot(
            policy_id=self._policy.id,
            policy_hash=policy_hash,
            foundation_pack_id=foundation_id,
            functional_pack_ids=functional_ids,
            style_pack_ids=style_ids,
            resolved_packs=identities,
            snapshot_hash=_canonical_hash(snapshot_payload),
        )
        return CompiledPackStack(
            manifests=manifests,
            presentation=presentation,
            snapshot=snapshot,
        )

    @staticmethod
    def _validate_disjoint_selection(selection: PackStackSelection) -> None:
        layers = (
            selection.foundation_pack_ids,
            selection.functional_pack_ids,
            selection.style_pack_ids,
        )
        all_ids = set().union(*layers)
        if len(all_ids) != sum(len(layer) for layer in layers):
            raise PackStackError("a Pack cannot be selected in more than one governance layer")

    @staticmethod
    def _governance(manifest: PackManifest):
        if manifest.governance is None:
            raise PackStackError(
                f"pack {manifest.id} lacks governance metadata and must be migrated before use"
            )
        return manifest.governance

    def _validate_selected_layer(
        self,
        manifests: dict[str, PackManifest],
        selected_ids: Iterable[str],
        expected_phase: PackPhase,
    ) -> None:
        for pack_id in selected_ids:
            manifest = manifests.get(pack_id)
            if manifest is None:
                continue  # resolve() emits the safe unknown-pack error.
            phase = self._governance(manifest).phase
            if phase is not expected_phase:
                raise PackStackError(
                    f"pack {pack_id} is {phase.value}; it cannot be selected as {expected_phase.value}"
                )

    def _validate_declared_governance(self, manifests: Iterable[PackManifest]) -> None:
        for manifest in manifests:
            governance = self._governance(manifest)
            if governance.phase is PackPhase.STYLE:
                self._validate_style_namespace(manifest)

    def _validate_dependencies(self, manifests: Iterable[PackManifest]) -> None:
        by_id = {manifest.id: manifest for manifest in manifests}
        phase_order = {
            PackPhase.FOUNDATION: 1,
            PackPhase.FUNCTIONAL: 2,
            PackPhase.STYLE: 3,
        }
        for manifest in manifests:
            phase = self._governance(manifest).phase
            for included_id in manifest.includes:
                included = by_id.get(included_id)
                if included is None:
                    raise PackStackError(
                        f"pack {manifest.id} depends on {included_id}, which is absent from the stack"
                    )
                included_phase = self._governance(included).phase
                if phase_order[included_phase] > phase_order[phase]:
                    raise PackStackError(
                        f"pack {manifest.id} cannot depend on lower-authority {included_phase.value} "
                        f"pack {included_id}"
                    )

    def _validate_foundation(
        self, manifests: Iterable[PackManifest], selection: PackStackSelection
    ) -> str:
        foundation_ids = sorted(
            manifest.id
            for manifest in manifests
            if self._governance(manifest).phase is PackPhase.FOUNDATION
        )
        if len(foundation_ids) != 1:
            raise PackStackError(
                "conflicting foundation Packs: expected exactly one, got " + ", ".join(foundation_ids)
            )
        foundation_id = foundation_ids[0]
        if foundation_id not in selection.foundation_pack_ids:
            raise PackStackError("selected functional Pack introduced an unselected foundation Pack")
        return foundation_id

    def _validate_functional_packs(self, manifests: Iterable[PackManifest]) -> None:
        allowed = self._policy.allowed_functional_pack_ids
        for manifest in manifests:
            governance = self._governance(manifest)
            if governance.phase is PackPhase.FUNCTIONAL and manifest.id not in allowed:
                raise PackStackError(f"functional Pack {manifest.id} is not allowed by organization policy")

    def _validate_lower_layers_only_add(self, manifests: Iterable[PackManifest]) -> None:
        """Reject Phase 2 declarations that would weaken or replace foundation content."""
        foundations = [
            manifest
            for manifest in manifests
            if self._governance(manifest).phase is PackPhase.FOUNDATION
        ]
        functional = [
            manifest
            for manifest in manifests
            if self._governance(manifest).phase is PackPhase.FUNCTIONAL
        ]
        for feature in functional:
            for foundation in foundations:
                self._validate_foundation_compatibility(foundation, feature)

    @staticmethod
    def _validate_foundation_compatibility(
        foundation: PackManifest, feature: PackManifest
    ) -> None:
        if (
            foundation.documentation_provider_ref is not None
            and feature.documentation_provider_ref not in {None, foundation.documentation_provider_ref}
        ):
            raise PackStackError(
                f"functional Pack {feature.id} conflicts with foundation documentation provider"
            )
        foundation_terms = {term.term.casefold() for term in foundation.vocabulary}
        overlapping_terms = sorted(
            foundation_terms & {term.term.casefold() for term in feature.vocabulary}
        )
        if overlapping_terms:
            raise PackStackError(
                f"functional Pack {feature.id} attempts to replace foundation vocabulary: "
                + ", ".join(overlapping_terms)
            )
        foundation_evidence = {
            (rule.evidence_type, rule.source_kind) for rule in foundation.evidence_ranking
        }
        feature_evidence = {(rule.evidence_type, rule.source_kind) for rule in feature.evidence_ranking}
        if conflicts := sorted(foundation_evidence & feature_evidence):
            rendered = ", ".join(
                f"{evidence_type}:{source_kind or 'any'}"
                for evidence_type, source_kind in conflicts
            )
            raise PackStackError(
                f"functional Pack {feature.id} attempts to replace foundation evidence ranking: {rendered}"
            )
        if foundation.policy_hints.evidence_required and not feature.policy_hints.evidence_required:
            raise PackStackError(f"functional Pack {feature.id} weakens foundation evidence policy")
        if foundation.policy_hints.read_only and not feature.policy_hints.read_only:
            raise PackStackError(f"functional Pack {feature.id} weakens foundation read-only policy")
        if foundation.default_scopes.project_required and not feature.default_scopes.project_required:
            raise PackStackError(f"functional Pack {feature.id} weakens foundation project scope")
        if foundation.output_rules.include_confidence and not feature.output_rules.include_confidence:
            raise PackStackError(f"functional Pack {feature.id} weakens foundation confidence requirement")
        if (
            foundation.output_rules.include_artifact_links
            and not feature.output_rules.include_artifact_links
        ):
            raise PackStackError(f"functional Pack {feature.id} weakens foundation artifact-link requirement")

    def _validate_required_certified_packs(self, manifests: Iterable[PackManifest]) -> None:
        by_id = {manifest.id: manifest for manifest in manifests}
        missing = sorted(self._policy.required_certified_pack_ids - by_id.keys())
        if missing:
            raise PackStackError("missing required certified Pack(s): " + ", ".join(missing))
        for pack_id in self._policy.required_certified_pack_ids:
            governance = self._governance(by_id[pack_id])
            if governance.phase is PackPhase.STYLE or governance.trust is not PackTrust.CERTIFIED:
                raise PackStackError(f"required Pack {pack_id} is not certified")

    def _validate_minimum_quality(self, manifests: Iterable[PackManifest]) -> None:
        minimum = self._policy.minimum_quality
        for manifest in manifests:
            governance = self._governance(manifest)
            if governance.phase is PackPhase.STYLE:
                continue
            quality = governance.quality
            if quality is None or (
                quality.quality_score < minimum.quality_score
                or quality.evaluation_score < minimum.evaluation_score
            ):
                raise PackStackError(f"pack {manifest.id} does not meet organization quality thresholds")

    def _compile_presentation(self, manifests: Iterable[PackManifest]) -> StylePresentation | None:
        values: dict[StyleField, object] = {}
        formatting: set[FormattingOption] = set()
        communication: set = set()
        style_manifests = sorted(
            (
                manifest
                for manifest in manifests
                if self._governance(manifest).phase is PackPhase.STYLE
            ),
            key=lambda manifest: manifest.id,
        )
        for manifest in style_manifests:
            presentation = manifest.style
            if presentation is None:  # Guarded by _validate_style_namespace.
                raise PackStackError(f"style Pack {manifest.id} has no presentation overlay")
            self._validate_permitted_presentation(manifest.id, presentation)
            self._merge_scalar(values, StyleField.AUDIENCE, presentation.audience, manifest.id)
            self._merge_scalar(values, StyleField.TONE, presentation.tone, manifest.id)
            self._merge_scalar(
                values, StyleField.TECHNICAL_DEPTH, presentation.technical_depth, manifest.id
            )
            self._merge_scalar(
                values, StyleField.OUTPUT_ORGANIZATION, presentation.output_organization, manifest.id
            )
            formatting.update(presentation.formatting)
            communication.update(presentation.communication)
        if not style_manifests:
            return None
        return StylePresentation(
            audience=values.get(StyleField.AUDIENCE),
            tone=values.get(StyleField.TONE),
            technical_depth=values.get(StyleField.TECHNICAL_DEPTH),
            output_organization=values.get(StyleField.OUTPUT_ORGANIZATION),
            formatting=tuple(sorted(formatting, key=lambda item: item.value)),
            communication=tuple(sorted(communication, key=lambda item: item.value)),
        )

    def _validate_permitted_presentation(
        self, pack_id: str, presentation: StylePresentation
    ) -> None:
        policy = self._policy.permitted_style
        scalar_values = (
            (StyleField.AUDIENCE, presentation.audience, policy.allowed_audiences),
            (StyleField.TONE, presentation.tone, policy.allowed_tones),
            (
                StyleField.TECHNICAL_DEPTH,
                presentation.technical_depth,
                policy.allowed_technical_depths,
            ),
            (
                StyleField.OUTPUT_ORGANIZATION,
                presentation.output_organization,
                policy.allowed_output_organizations,
            ),
        )
        for field, value, allowed in scalar_values:
            if value is not None:
                self._validate_style_value(pack_id, field, value, allowed)
        if presentation.formatting:
            self._validate_style_values(
                pack_id, StyleField.FORMATTING, presentation.formatting, policy.allowed_formatting
            )
        if presentation.communication:
            self._validate_style_values(
                pack_id,
                StyleField.COMMUNICATION,
                presentation.communication,
                policy.allowed_communication,
            )

    def _validate_style_value(
        self, pack_id: str, field: StyleField, value: object, allowed: frozenset
    ) -> None:
        if field not in self._policy.permitted_style.permitted_fields or value not in allowed:
            raise PackStackError(f"style Pack {pack_id} is not permitted to set {field.value}")

    def _validate_style_values(
        self, pack_id: str, field: StyleField, values: Iterable[object], allowed: frozenset
    ) -> None:
        if field not in self._policy.permitted_style.permitted_fields or not set(values) <= allowed:
            raise PackStackError(f"style Pack {pack_id} is not permitted to set {field.value}")

    @staticmethod
    def _merge_scalar(
        values: dict[StyleField, object], field: StyleField, value: object, pack_id: str
    ) -> None:
        if value is None:
            return
        existing = values.get(field)
        if existing is not None and existing != value:
            raise PackStackError(f"conflicting style {field.value} in Pack {pack_id}")
        values[field] = value

    @staticmethod
    def _validate_style_namespace(manifest: PackManifest) -> None:
        """Defence in depth for objects built without Pydantic validation."""
        if manifest.pack_type.value != "style" or manifest.style is None or manifest.includes:
            raise PackStackError(f"style Pack {manifest.id} has unauthorized composition content")
        if any(
            (
                manifest.documentation_provider_ref is not None,
                manifest.vocabulary,
                manifest.entities,
                manifest.workflows,
                manifest.evidence_ranking,
                manifest.artifact_templates,
                manifest.connector_requirements,
                manifest.default_scopes != type(manifest.default_scopes)(),
                manifest.policy_hints != type(manifest.policy_hints)(),
                manifest.output_rules != type(manifest.output_rules)(),
            )
        ):
            raise PackStackError(
                f"style Pack {manifest.id} attempts to change protected analysis, evidence, policy, or capabilities"
            )


def _canonical_hash(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
