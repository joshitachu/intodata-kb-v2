from __future__ import annotations

import pytest

from mcp_server.platforms import (
    get_platform_strategy,
    list_platform_strategies,
    supported_platform_ids,
)


def test_strategy_registry_resolves_current_platforms():
    assert supported_platform_ids() == frozenset({"talend", "workato", "lobster", "generic"})
    assert [strategy.id for strategy in list_platform_strategies()] == [
        "talend",
        "workato",
        "lobster",
        "generic",
    ]


def test_strategy_registry_rejects_unknown_platform():
    with pytest.raises(ValueError, match="Unknown platform"):
        get_platform_strategy("jitterbit")


@pytest.mark.parametrize(
    ("platform", "label", "unit_type", "component_type"),
    [
        ("talend", "jobs", "job", "tMap"),
        ("workato", "recipes/functions", "recipe", "WorkatoRecipe"),
        ("workato", "recipes/functions", "function", "WorkatoFunction"),
        ("lobster", "profiles", "profile", "Profile"),
        ("generic", "code units", "code_unit", "Module"),
    ],
)
def test_strategy_exposes_current_unit_terms(platform, label, unit_type, component_type):
    strategy = get_platform_strategy(platform)
    job = {"components": [{"component_type": component_type}]}

    assert strategy.descriptor.id == platform
    assert strategy.canonical_unit_label() == label
    assert strategy.unit_type_for(job) == unit_type


def test_strategy_intodoc_plugin_names_match_intodoc_registry():
    from intodoc.platforms.registry import list_platforms

    intodoc_platforms = set(list_platforms())

    for strategy in list_platform_strategies():
        assert strategy.intodoc_plugin_name in intodoc_platforms


@pytest.mark.parametrize(
    ("platform", "files", "expected_label", "expected_count"),
    [
        ("talend", [{"relative_path": "jobs/a.item", "file_type": "unknown"}], "item_files_fallback", 1),
        (
            "workato",
            [{"relative_path": "recipes/a.recipe.json", "file_type": "unknown"}],
            "recipe_or_function_files",
            1,
        ),
        ("lobster", [{"relative_path": "profiles/a.xml", "file_type": "unknown"}], "profile_xml_files", 1),
        ("generic", [{"relative_path": "src/a.py", "file_type": "unknown"}], "parsed_units", 1),
    ],
)
def test_strategy_raw_source_audit_preserves_fallback_counts(
    platform,
    files,
    expected_label,
    expected_count,
):
    strategy = get_platform_strategy(platform)
    jobs = [{"name": "a", "components": []}]

    audit = strategy.raw_source_audit({"files": files}, jobs)

    assert audit["detected_source_label"] == expected_label
    assert audit["detected_source_units"] == expected_count
    assert audit["parsed_units"] == 1


def test_raw_source_audit_prefers_file_type_job_count():
    strategy = get_platform_strategy("talend")
    files = [
        {"relative_path": "jobs/a.item", "file_type": "job"},
        {"relative_path": "jobs/b.item", "file_type": "job"},
    ]

    audit = strategy.raw_source_audit({"files": files}, [{"name": "a"}])

    assert audit["detected_source_label"] == "job_files"
    assert audit["detected_source_units"] == 2


def test_talend_external_system_fallback_matches_existing_behavior():
    strategy = get_platform_strategy("talend")
    job = {
        "components": [
            {"component_type": "tSalesforceInput"},
            {"component_type": "tMap"},
            {"component_type": "tRESTClient"},
            {"component_type": "tRunJob"},
        ]
    }

    assert strategy.derive_external_systems(job) == ["REST API", "Salesforce"]


def test_workato_external_system_fallback_matches_existing_behavior():
    strategy = get_platform_strategy("workato")
    job = {
        "components": [{
            "params": [{
                "value": '{"provider": "salesforce", "action_applications": ["workato", "jira"]}',
            }]
        }]
    }

    assert strategy.derive_external_systems(job) == ["Jira", "Salesforce"]
