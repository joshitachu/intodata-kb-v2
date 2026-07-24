from src.harness.registry import base_capability_registry
from src.harness.workflow_family_inventory import (
    WORKFLOW_FAMILY_ADAPTERS,
    validate_workflow_family_inventory,
)


def test_all_six_slice5_workflow_families_have_registered_adapter_authority() -> None:
    validate_workflow_family_inventory(base_capability_registry())
    assert {item.family for item in WORKFLOW_FAMILY_ADAPTERS} == {
        "project_onboarding",
        "pack_builder",
        "evidence_investigation",
        "documentation_update",
        "generic_durable_capability",
        "documentation_generation_external_child",
    }
    for adapter in WORKFLOW_FAMILY_ADAPTERS:
        assert adapter.start and adapter.restart and adapter.terminal


def test_interrupt_resume_claims_are_explicit_per_family() -> None:
    by_name = {item.family: item for item in WORKFLOW_FAMILY_ADAPTERS}
    assert not by_name["evidence_investigation"].interrupt
    assert not by_name["evidence_investigation"].resume
    for family in set(by_name) - {"evidence_investigation"}:
        assert by_name[family].interrupt and by_name[family].resume
