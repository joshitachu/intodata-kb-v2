from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.harness.packs.catalog import PackCatalog
from src.harness.packs.composition import PackStackCompiler, PackStackError
from src.harness.packs.models import (
    CommunicationOption,
    EvidenceRankingRule,
    FormattingOption,
    MinimumQualityThresholds,
    OrganizationGovernancePolicy,
    OutputOrganization,
    PackAuthority,
    PackGovernance,
    PackManifest,
    PackMutability,
    PackOwner,
    PackPhase,
    PackQuality,
    PackScope,
    PackStackSelection,
    PackTrust,
    PackType,
    PolicyHints,
    ProtectedInvariant,
    StyleAudience,
    StyleField,
    StyleGovernancePolicy,
    StylePresentation,
    StyleTone,
    TechnicalDepth,
    WorkflowView,
)


def governance(phase: PackPhase, *, quality: float = 0.95) -> PackGovernance:
    if phase is PackPhase.FOUNDATION:
        return PackGovernance(
            phase=phase,
            owner=PackOwner.INTODATA,
            authority=PackAuthority.INTODATA_CERTIFIED,
            scope=PackScope.GLOBAL,
            mutability=PackMutability.IMMUTABLE,
            trust=PackTrust.CERTIFIED,
            quality=PackQuality(quality_score=quality, evaluation_score=quality),
        )
    if phase is PackPhase.FUNCTIONAL:
        return PackGovernance(
            phase=phase,
            owner=PackOwner.ORGANIZATION,
            authority=PackAuthority.ORGANIZATION_CERTIFIED,
            scope=PackScope.ORGANIZATION,
            mutability=PackMutability.IMMUTABLE,
            trust=PackTrust.CERTIFIED,
            quality=PackQuality(quality_score=quality, evaluation_score=quality),
        )
    return PackGovernance(
        phase=phase,
        owner=PackOwner.PROJECT,
        authority=PackAuthority.PROJECT_MANAGED,
        scope=PackScope.PROJECT,
        mutability=PackMutability.EDITABLE,
        trust=PackTrust.UNREVIEWED,
    )


def pack(
    pack_id: str,
    phase: PackPhase,
    *,
    version: str = "1.0.0",
    includes: tuple[str, ...] = (),
    quality: float = 0.95,
    style: StylePresentation | None = None,
) -> PackManifest:
    return PackManifest(
        id=pack_id,
        name=pack_id,
        version=version,
        pack_type=PackType.STYLE if phase is PackPhase.STYLE else PackType.DOMAIN,
        governance=governance(phase, quality=quality),
        includes=includes,
        style=style,
    )


def policy(*, minimum: float = 0.9, allowed_functional: frozenset[str] | None = None):
    return OrganizationGovernancePolicy(
        id="org.default",
        required_certified_pack_ids=frozenset({"foundation.core"}),
        allowed_functional_pack_ids=allowed_functional or frozenset({"feature.operations"}),
        permitted_style=StyleGovernancePolicy(
            permitted_fields=frozenset(StyleField),
            allowed_audiences=frozenset(StyleAudience),
            allowed_tones=frozenset(StyleTone),
            allowed_technical_depths=frozenset(TechnicalDepth),
            allowed_output_organizations=frozenset(OutputOrganization),
            allowed_formatting=frozenset(FormattingOption),
            allowed_communication=frozenset(CommunicationOption),
        ),
        minimum_quality=MinimumQualityThresholds(
            quality_score=minimum,
            evaluation_score=minimum,
        ),
    )


def compiler(*manifests: PackManifest, organization_policy=None) -> PackStackCompiler:
    return PackStackCompiler(PackCatalog(manifests), organization_policy or policy())


def selection(*, functional: frozenset[str] = frozenset(), style: frozenset[str] = frozenset()):
    return PackStackSelection(
        foundation_pack_ids=frozenset({"foundation.core"}),
        functional_pack_ids=functional,
        style_pack_ids=style,
    )


def test_compiles_foundation_functional_and_style_layers_into_an_immutable_snapshot():
    foundation = pack("foundation.core", PackPhase.FOUNDATION)
    functional = pack(
        "feature.operations",
        PackPhase.FUNCTIONAL,
        includes=("foundation.core",),
    )
    style = pack(
        "style.engineering",
        PackPhase.STYLE,
        style=StylePresentation(
            audience=StyleAudience.ENGINEERING,
            tone=StyleTone.CONCISE,
            formatting=(FormattingOption.MARKDOWN,),
        ),
    )

    compiled = compiler(foundation, functional, style).compile(
        selection(functional=frozenset({"feature.operations"}), style=frozenset({"style.engineering"}))
    )

    assert [manifest.id for manifest in compiled.manifests] == [
        "feature.operations",
        "foundation.core",
        "style.engineering",
    ]
    assert compiled.presentation == StylePresentation(
        audience=StyleAudience.ENGINEERING,
        tone=StyleTone.CONCISE,
        formatting=(FormattingOption.MARKDOWN,),
    )
    assert compiled.snapshot.foundation_pack_id == "foundation.core"
    assert compiled.snapshot.functional_pack_ids == ("feature.operations",)
    assert compiled.snapshot.style_pack_ids == ("style.engineering",)
    assert len(compiled.snapshot.snapshot_hash) == 64


def test_rejects_functional_selection_not_approved_by_organization_policy():
    foundation = pack("foundation.core", PackPhase.FOUNDATION)
    unapproved = pack("feature.unapproved", PackPhase.FUNCTIONAL, includes=("foundation.core",))

    with pytest.raises(PackStackError, match="not allowed"):
        compiler(foundation, unapproved).compile(selection(functional=frozenset({"feature.unapproved"})))


def test_rejects_conflicting_foundations_introduced_by_a_functional_dependency():
    foundation = pack("foundation.core", PackPhase.FOUNDATION)
    second_foundation = pack("foundation.other", PackPhase.FOUNDATION)
    functional = pack(
        "feature.operations",
        PackPhase.FUNCTIONAL,
        includes=("foundation.core", "foundation.other"),
    )

    with pytest.raises(PackStackError, match="conflicting foundation Packs"):
        compiler(foundation, second_foundation, functional).compile(
            selection(functional=frozenset({"feature.operations"}))
        )


@pytest.mark.parametrize(
    "unsafe_update",
    [
        {"evidence_ranking": (EvidenceRankingRule(evidence_type="document", weight=1),)},
        {"policy_hints": PolicyHints(evidence_required=False)},
        {
            "workflows": (
                WorkflowView(
                    id="style.analysis",
                    label="Analysis",
                    description="Unauthorized analysis.",
                ),
            )
        },
    ],
)
def test_compiler_defense_in_depth_rejects_style_changes_to_protected_content(unsafe_update):
    foundation = pack("foundation.core", PackPhase.FOUNDATION)
    safe_style = pack(
        "style.engineering",
        PackPhase.STYLE,
        style=StylePresentation(audience=StyleAudience.ENGINEERING),
    )
    # model_copy(update=...) simulates an unsafe in-memory caller that bypassed validation.
    unsafe_style = safe_style.model_copy(update=unsafe_update)

    with pytest.raises(PackStackError, match="protected semantic, policy, or capability content"):
        compiler(foundation, unsafe_style).compile(selection(style=frozenset({"style.engineering"})))


def test_style_contract_rejects_semantic_content_during_normal_validation():
    style = pack(
        "style.engineering",
        PackPhase.STYLE,
        style=StylePresentation(audience=StyleAudience.ENGINEERING),
    )
    payload = style.model_dump(mode="python")
    payload["policy_hints"] = PolicyHints(evidence_required=False)

    with pytest.raises(ValidationError, match="style Packs cannot declare protected"):
        PackManifest.model_validate(payload)


def test_stack_hash_is_deterministic_and_changes_when_pack_version_changes():
    foundation = pack("foundation.core", PackPhase.FOUNDATION)
    functional = pack("feature.operations", PackPhase.FUNCTIONAL, includes=("foundation.core",))
    first = compiler(functional, foundation).compile(selection(functional=frozenset({"feature.operations"})))
    second = compiler(foundation, functional).compile(selection(functional=frozenset({"feature.operations"})))
    upgraded = compiler(
        foundation,
        pack(
            "feature.operations",
            PackPhase.FUNCTIONAL,
            version="1.0.1",
            includes=("foundation.core",),
        ),
    ).compile(selection(functional=frozenset({"feature.operations"})))

    assert first.snapshot == second.snapshot
    assert first.snapshot.snapshot_hash != upgraded.snapshot.snapshot_hash


def test_rejects_pack_below_organization_minimum_quality():
    foundation = pack("foundation.core", PackPhase.FOUNDATION)
    functional = pack(
        "feature.operations",
        PackPhase.FUNCTIONAL,
        includes=("foundation.core",),
        quality=0.89,
    )

    with pytest.raises(PackStackError, match="quality thresholds"):
        compiler(foundation, functional).compile(selection(functional=frozenset({"feature.operations"})))


def test_functional_pack_cannot_weaken_a_foundation_protected_invariant():
    foundation = pack("foundation.core", PackPhase.FOUNDATION)
    functional = pack(
        "feature.operations",
        PackPhase.FUNCTIONAL,
        includes=("foundation.core",),
    ).model_copy(update={"policy_hints": PolicyHints(evidence_required=False)})

    with pytest.raises(PackStackError, match="weakens foundation evidence policy"):
        compiler(foundation, functional).compile(selection(functional=frozenset({"feature.operations"})))


def test_policy_cannot_drop_any_protected_invariant():
    with pytest.raises(ValidationError, match="cannot omit protected invariants"):
        OrganizationGovernancePolicy(
            id="org.invalid",
            minimum_quality=MinimumQualityThresholds(quality_score=0.9, evaluation_score=0.9),
            protected_invariants=frozenset({ProtectedInvariant.CAPABILITIES}),
        )


def test_legacy_manifest_is_not_granted_governance_authority():
    legacy = PackManifest(
        schema_version=1,
        id="legacy.domain",
        name="Legacy domain",
        version="1.0.0",
        pack_type=PackType.DOMAIN,
    )
    foundation = pack("foundation.core", PackPhase.FOUNDATION)

    with pytest.raises(PackStackError, match="lacks governance metadata"):
        compiler(foundation, legacy).compile(selection(functional=frozenset({"legacy.domain"})))
