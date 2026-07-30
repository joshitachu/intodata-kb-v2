"""Evidence and anti-fabrication tests for the semantic L1/L2 projection."""

from intodoc.landscape import build_landscape
from intodoc.models import (
    Component,
    ComponentParam,
    LineageMatrix,
    ParsedJob,
    ProjectManifest,
    ScanResult,
)
from intodoc.scanner.credential_filter import CredentialFilter


def _job(
    name: str,
    *,
    root: str = "assetcheck-root",
    label: str = "",
    component_type: str = "tRESTClient",
) -> ParsedJob:
    components = []
    if label:
        components.append(Component(
            unique_name="component_1",
            component_type=component_type,
            params=[ComponentParam(name="LABEL", value=label)],
        ))
    return ParsedJob(
        name=name,
        job_id=name,
        source_root=root,
        path=f"process/{name}",
        components=components,
    )


def _build(jobs: list[ParsedJob], roots: list[dict[str, str]]):
    manifest = ProjectManifest(
        source_roots=roots,
        job_source_roots={job.name: job.source_root for job in jobs},
    )
    scan = ScanResult(
        platform="talend",
        repo_root="/fake",
        jobs=jobs,
        manifest=manifest,
    )
    return build_landscape(scan, LineageMatrix(), CredentialFilter())


def _system_names(landscape) -> set[str]:
    return {system.name for system in landscape.semantic_topology.systems}


def _exchange_names(landscape) -> list[tuple[str, str, str]]:
    systems = {
        system.id: system.name
        for system in landscape.semantic_topology.systems
    }
    return [
        (
            systems[exchange.source_system_id],
            systems[exchange.target_system_id],
            exchange.label,
        )
        for exchange in landscape.semantic_topology.exchanges
    ]


def test_directional_artifact_name_proposes_system_exchange_with_provenance():
    landscape = _build(
        [_job("Maximo2AssetCheck_SendWorkOrder")],
        [{
            "id": "assetcheck-root",
            "path": "assetcheck-root",
            "label": "AssetCheck",
            "role": "execution",
        }],
    )

    assert _system_names(landscape) == {"Maximo", "AssetCheck"}
    assert _exchange_names(landscape) == [
        ("Maximo", "AssetCheck", "Work Order"),
    ]
    exchange = landscape.semantic_topology.exchanges[0]
    assert exchange.status == "proposed"
    assert exchange.confidence == "likely"
    assert exchange.implementation_solution_ids == [
        landscape.semantic_topology.integration_solutions[0].id,
    ]
    assert exchange.evidence[0].signal_type == "directional_artifact_name"
    assert exchange.evidence[0].qualified_artifact_id == (
        "assetcheck-root::Maximo2AssetCheck_SendWorkOrder"
    )


def test_component_authored_label_can_propose_direction_from_solution_to_peer():
    landscape = _build(
        [_job(
            "AssetCheck_SendWorkOrderUpdate",
            label="Send work order update to Maximo",
        )],
        [
            {
                "id": "assetcheck-root",
                "path": "assetcheck-root",
                "label": "AssetCheck",
                "role": "execution",
            },
            {
                "id": "maximo-root",
                "path": "maximo-root",
                "label": "Maximo",
                "role": "execution",
            },
        ],
    )

    assert ("AssetCheck", "Maximo", "Work Order Update") in (
        _exchange_names(landscape)
    )
    exchange = landscape.semantic_topology.exchanges[0]
    assert exchange.mechanisms == ["rest"]
    assert exchange.evidence[0].component_name == "component_1"
    assert exchange.evidence[0].label == "Send work order update to Maximo"


def test_monitoring_is_not_misread_as_camel_case_to_direction():
    landscape = _build(
        [_job("fourpsObjectsReader_errorMonitoringOnly")],
        [{
            "id": "erp-root",
            "path": "erp-root",
            "label": "ERP",
            "role": "execution",
        }],
    )

    assert landscape.semantic_topology.systems == []
    assert landscape.semantic_topology.exchanges == []


def test_known_base_identity_wins_over_job_family_suffix():
    landscape = _build(
        [_job("Vise2AssetCheckBgProcess_MainJob")],
        [{
            "id": "assetcheck-root",
            "path": "assetcheck-root",
            "label": "AssetCheck",
            "role": "execution",
        }],
    )

    assert _system_names(landscape) == {"Vise", "AssetCheck"}
    assert _exchange_names(landscape) == [
        ("Vise", "AssetCheck", "Vise to AssetCheck"),
    ]


def test_test_and_copy_artifacts_do_not_create_semantic_systems():
    landscape = _build(
        [
            _job("testQuintiq2AssetCheck"),
            _job("Copy_of_Maximo2AssetCheck_WorkOrder"),
        ],
        [{
            "id": "assetcheck-root",
            "path": "assetcheck-root",
            "label": "AssetCheck",
            "role": "execution",
        }],
    )

    assert landscape.semantic_topology.systems == []
    assert landscape.semantic_topology.exchanges == []


def test_unknown_component_and_file_names_never_create_semantic_peers():
    landscape = _build(
        [_job(
            "HashRecords",
            label="Calculate hash for local comparison",
            component_type="tHashOutput",
        )],
        [{
            "id": "root",
            "path": "root",
            "label": "Orders",
            "role": "execution",
        }],
    )

    assert landscape.semantic_topology.systems == []
    assert landscape.semantic_topology.exchanges == []


def test_data_format_converter_names_are_not_system_exchanges():
    landscape = _build(
        [_job("json2xml"), _job("xml_to_Csv")],
        [{
            "id": "root",
            "path": "root",
            "label": "Transformations",
            "role": "execution",
        }],
    )

    assert landscape.semantic_topology.systems == []
    assert landscape.semantic_topology.exchanges == []
