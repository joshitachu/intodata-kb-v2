"""Tests for Talend parser against real vaxxinova-talend project."""

import json

import pytest

from intodoc.scanner.credential_filter import CredentialFilter
from intodoc.scanner.post_filter import filter_scan_result
from intodoc.platforms.talend.parser import (
    TalendParser,
    _extract_talend_external_system,
)
from intodoc.models import (
    Component,
    ComponentParam,
    MetadataConnection,
    ParsedJob,
    ScanResult,
)


@pytest.fixture
def parser():
    return TalendParser(CredentialFilter())


class TestTalendParserIntegration:
    """Integration tests using the real vaxxinova-talend project."""

    def test_parse_finds_jobs(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        assert result.platform == "talend"
        assert len(result.jobs) > 0, "Should find at least one job"

    def test_parse_finds_connections(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        assert len(result.metadata_connections) > 0, "Should find metadata connections"

    def test_parse_finds_contexts(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        assert len(result.context_groups) > 0, "Should find context groups"

    def test_jobs_have_components(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        jobs_with_components = [j for j in result.jobs if j.components]
        assert len(jobs_with_components) > 0, "At least some jobs should have components"

    def test_jobs_have_connections(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        jobs_with_connections = [j for j in result.jobs if j.connections]
        assert len(jobs_with_connections) > 0, "At least some jobs should have connections"

    def test_no_credentials_in_output(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        cred_filter = CredentialFilter()
        # Check that no raw credentials appear in job data
        for job in result.jobs:
            for comp in job.components:
                for param in comp.params:
                    assert not cred_filter.has_credentials(param.value), (
                        f"Credential found in {job.name}/{comp.unique_name}: {param.name}"
                    )

    def test_bra_vbr_raw_job_exists(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        bra_jobs = [j for j in result.jobs if "BRA_VBR_RAW" in j.name]
        assert len(bra_jobs) > 0, "BRA_VBR_RAW job should exist"
        job = bra_jobs[0]
        assert job.group == "BRA"


def test_unknown_utility_components_are_not_external_systems():
    """Component names such as Hash or ContextDump are implementation
    mechanics; unknown names must never be turned into landscape peers."""
    assert _extract_talend_external_system("tHashOutput") is None
    assert _extract_talend_external_system("tContextDump") is None
    assert _extract_talend_external_system("tAdvancedFileOutputXML") is None

    def test_stats_populated(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        assert result.stats["total_jobs"] > 0
        assert result.stats["total_files"] > 0


def test_composite_connections_with_redacted_hosts_never_merge():
    """Layer-1 hostname placeholders are not physical identity evidence."""
    jobs = [
        ParsedJob(
            name="A", path="process/A", source_root="project_a",
            components=[Component(unique_name="dbA", component_type="tMSSqlInput", metadata_connection_ref="Shared")],
        ),
        ParsedJob(
            name="B", path="process/B", source_root="project_b",
            components=[Component(unique_name="dbB", component_type="tMSSqlInput", metadata_connection_ref="Shared")],
        ),
    ]
    connections = [
        MetadataConnection(
            xmi_id="a", name="Shared", connection_type="MSSQL",
            host="[HOSTNAME]", port="1433", database="logging",
            additional_params={"source_root": "project_a"},
        ),
        MetadataConnection(
            xmi_id="b", name="Shared", connection_type="MSSQL",
            host="[HOSTNAME]", port="1433", database="logging",
            additional_params={"source_root": "project_b"},
        ),
    ]

    retained = TalendParser._qualify_composite_connections(jobs, connections, composite=True)

    assert [connection.name for connection in retained] == [
        "project_a::Shared", "project_b::Shared",
    ]
    assert [job.components[0].metadata_connection_ref for job in jobs] == [
        "project_a::Shared", "project_b::Shared",
    ]


# ── Synthetic project helpers ───────────────────────────────────────

_ITEM_XMLNS = (
    'xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" '
    'xmlns:TalendMapper="http://www.talend.org/mapper" '
    'xmlns:xmi="http://www.omg.org/XMI" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


def _write_job(project, rel_dir, name, version, body=""):
    """Write a minimal .item + .properties pair for a job version."""
    job_dir = project / "process" / rel_dir
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{name}_{version}.item").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        f'<talendfile:ProcessType {_ITEM_XMLNS} defaultContext="DEV" jobType="Standard">'
        f"{body}</talendfile:ProcessType>",
        encoding="utf-8",
    )
    (job_dir / f"{name}_{version}.properties").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xmi:XMI xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Property label="{name}" version="{version}"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


def _synthetic_project(root):
    project = root / "SYNTH"
    (project / "process").mkdir(parents=True)
    (project / "metadata" / "connections").mkdir(parents=True)
    (project / "context").mkdir(parents=True)
    return project


_TMAP_NODE = (
    '<node componentName="tMap" componentVersion="0.1" posX="100" posY="100">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tMap_1"/>'
    '<nodeData xsi:type="TalendMapper:MapperData">'
    '<inputTables name="row1"/>'
    '<outputTables name="out1">'
    '<mapperTableEntries name="wonum" expression="row1.wonum" type="id_String"/>'
    "</outputTables>"
    "</nodeData>"
    "</node>"
)


class TestVersionDedup:
    """Talend exports keep every saved item version; only the latest is current."""

    def test_only_latest_version_kept(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "WorkOrderJob", "0.9")
        _write_job(project, "Iface", "WorkOrderJob", "0.10")
        _write_job(project, "Iface", "WorkOrderJob", "3.2")
        result = parser.parse(tmp_path)
        wo_jobs = [j for j in result.jobs if j.name == "WorkOrderJob"]
        assert len(wo_jobs) == 1
        assert wo_jobs[0].version == "3.2"
        assert result.stats["superseded_job_versions"] == 2

    def test_numeric_version_ordering(self, parser, tmp_path):
        """'0.10' is newer than '0.9' -- versions compare numerically."""
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "NumJob", "0.9")
        _write_job(project, "Iface", "NumJob", "0.10")
        result = parser.parse(tmp_path)
        num_jobs = [j for j in result.jobs if j.name == "NumJob"]
        assert len(num_jobs) == 1
        assert num_jobs[0].version == "0.10"

    def test_same_name_in_different_folders_both_kept(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "GroupA", "SharedName", "0.1")
        _write_job(project, "GroupB", "SharedName", "0.1")
        result = parser.parse(tmp_path)
        shared = [j for j in result.jobs if j.name == "SharedName"]
        assert len(shared) == 2

    def test_version_sort_key_ordering(self, parser):
        assert parser._version_sort_key("0.10") > parser._version_sort_key("0.9")
        assert parser._version_sort_key("3.3") > parser._version_sort_key("0.1")
        assert parser._version_sort_key("") == ()


class TestCompositeTalendBundles:
    def test_route_ctalendjob_resolves_a_same_root_di_job(self, parser, tmp_path):
        root = _synthetic_project(tmp_path)
        _write_job(root, "Jobs", "Worker", "0.1")
        routes = root / "routes" / "Service"
        routes.mkdir(parents=True)
        body = (
            '<node componentName="cTalendJob"><elementParameter name="UNIQUE_NAME" value="invoke"/>'
            '<elementParameter name="SELECTED_JOB_NAME" value="Worker"/></node>'
        )
        (routes / "Inbound_0.1.item").write_text(
            '<?xml version="1.0"?><talendfile:ProcessType ' + _ITEM_XMLNS + '>' + body + '</talendfile:ProcessType>',
            encoding="utf-8",
        )
        (routes / "Inbound_0.1.properties").write_text(
            '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" xmlns:TalendProperties="http://www.talend.org/properties">'
            '<TalendProperties:Property label="Inbound" version="0.1"/></xmi:XMI>', encoding="utf-8",
        )

        result = parser.parse(tmp_path)
        route = next(job for job in result.jobs if job.name == "Inbound")
        assert route.artifact_kind == "route"
        assert route.artifact_refs[0].resolution == "same_root"
        assert route.artifact_refs[0].resolved_artifact_id == "Worker"
        assert result.manifest.artifact_relations == [{
            "source": "Inbound", "target": "Worker", "relation_type": "route_invokes_job",
            "resolution": "same_root", "provenance": "cTalendJob", "component_name": "invoke",
        }]

    def test_route_channels_become_shared_topology_dependencies(self, parser, tmp_path):
        root = _synthetic_project(tmp_path)
        routes = root / "routes" / "Service"
        routes.mkdir(parents=True)
        body = (
            '<node componentName="cJMS">'
            '<elementParameter name="UNIQUE_NAME" value="queue"/>'
            '<elementParameter name="DESTINATION" value="&quot;esb.orders&quot;"/>'
            "</node>"
            '<node componentName="cDirect">'
            '<elementParameter name="UNIQUE_NAME" value="direct"/>'
            '<elementParameter name="NAME" value="&quot;validate&quot;"/>'
            "</node>"
        )
        (routes / "Inbound_0.1.item").write_text(
            '<?xml version="1.0"?><talendfile:ProcessType '
            + _ITEM_XMLNS
            + ">"
            + body
            + "</talendfile:ProcessType>",
            encoding="utf-8",
        )
        (routes / "Inbound_0.1.properties").write_text(
            '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:TalendProperties="http://www.talend.org/properties">'
            '<TalendProperties:Property label="Inbound" version="0.1"/>'
            "</xmi:XMI>",
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)

        assert result.manifest.job_connections_used["Inbound"] == [
            "JMS · esb.orders",
            "DIRECT · validate",
        ]
        assert result.manifest.connection_used_by["JMS · esb.orders"] == ["Inbound"]
        assert result.manifest.connection_used_by["DIRECT · validate"] == ["Inbound"]

    def test_route_channel_direction_comes_from_component_flow(self, parser, tmp_path):
        root = _synthetic_project(tmp_path)
        routes = root / "routes" / "Service"
        routes.mkdir(parents=True)
        body = (
            '<node componentName="cJMS">'
            '<elementParameter name="UNIQUE_NAME" value="queueIn"/>'
            '<elementParameter name="DESTINATION" value="&quot;orders.in&quot;"/>'
            "</node>"
            '<node componentName="cProcessor">'
            '<elementParameter name="UNIQUE_NAME" value="processor"/>'
            "</node>"
            '<node componentName="cJMS">'
            '<elementParameter name="UNIQUE_NAME" value="queueOut"/>'
            '<elementParameter name="DESTINATION" value="&quot;orders.out&quot;"/>'
            "</node>"
            '<connection source="queueIn" target="processor" connectorName="ROUTE"/>'
            '<connection source="processor" target="queueOut" connectorName="ROUTE"/>'
        )
        (routes / "Directional_0.1.item").write_text(
            '<?xml version="1.0"?><talendfile:ProcessType '
            + _ITEM_XMLNS
            + ">"
            + body
            + "</talendfile:ProcessType>",
            encoding="utf-8",
        )
        (routes / "Directional_0.1.properties").write_text(
            '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:TalendProperties="http://www.talend.org/properties">'
            '<TalendProperties:Property label="Directional" version="0.1"/>'
            "</xmi:XMI>",
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)
        channels = {
            channel["component_name"]: channel
            for channel in result.manifest.artifact_channels
        }

        assert channels["queueIn"]["direction"] == "input"
        assert channels["queueOut"]["direction"] == "output"

    def test_route_channel_carries_additive_redaction_descriptor(self, parser, tmp_path):
        """`channel` stays the existing collapsed placeholder string (no
        shape/meaning change for existing artifact_channels consumers), but
        a new additive `channel_descriptor` key captures safe structural
        facts (scheme/auth/tls/category) from the same raw endpoint value
        before it was collapsed.

        Uses a single-label host (no recognized TLD) and query-param-style
        auth so the value is representative of what actually reaches the
        manifest builder: layer-1 (text-level) credential filtering already
        ran on the raw file before parsing, and would otherwise collapse a
        dotted hostname or a user:pass@ literal before this point.
        """
        root = _synthetic_project(tmp_path)
        routes = root / "routes" / "Service"
        routes.mkdir(parents=True)
        secret = "S3cr3tBrokerPass"
        body = (
            '<node componentName="cJMS">'
            '<elementParameter name="UNIQUE_NAME" value="secureQueue"/>'
            f'<elementParameter name="DESTINATION" '
            f'value="jms://brokerhost01:61616/orders?password={secret}"/>'
            "</node>"
            '<node componentName="cDirect">'
            '<elementParameter name="UNIQUE_NAME" value="plainDirect"/>'
            '<elementParameter name="NAME" value="&quot;validate&quot;"/>'
            "</node>"
        )
        (routes / "Secure_0.1.item").write_text(
            '<?xml version="1.0"?><talendfile:ProcessType '
            + _ITEM_XMLNS
            + ">"
            + body
            + "</talendfile:ProcessType>",
            encoding="utf-8",
        )
        (routes / "Secure_0.1.properties").write_text(
            '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:TalendProperties="http://www.talend.org/properties">'
            '<TalendProperties:Property label="Secure" version="0.1"/>'
            "</xmi:XMI>",
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)
        channels = {
            channel["component_name"]: channel
            for channel in result.manifest.artifact_channels
        }

        secure = channels["secureQueue"]
        # Existing collapsed placeholder is unchanged in shape/meaning --
        # the raw endpoint (host, credentials) never reaches `channel`.
        assert secure["channel"] == "configured endpoint"
        assert secret not in secure["channel"]
        assert "brokerhost01" not in secure["channel"]
        # The whole parsed result, not merely the derived manifest channel,
        # must be clean: JMS URI query credentials used to survive in the
        # component parameter because this scheme was absent from Layer 1.
        assert secret not in result.model_dump_json()
        assert "brokerhost01" not in result.model_dump_json()
        # New additive descriptor recovers structural facts safely
        descriptor = secure["channel_descriptor"]
        assert descriptor is not None
        assert descriptor["scheme"] == "jms"
        assert descriptor["category"] == "jms"
        assert descriptor["auth_present"] is True
        assert descriptor["auth_type"] == "basic"
        assert descriptor["host_present"] is True
        assert secret not in json.dumps(descriptor)
        assert "brokerhost01" not in json.dumps(descriptor)

        # A plain named channel (no URI shape) keeps its non-collapsed
        # string and yields no descriptor -- nothing to safely derive.
        plain = channels["plainDirect"]
        assert plain["channel"] == '"validate"'
        assert plain["channel_descriptor"] is None

    def test_normal_https_endpoint_keeps_descriptor_after_layer_one_redaction(self, parser, tmp_path):
        """A dotted real-world host is redacted before XML parsing, but its
        safe protocol facts survive to the component and L2 endpoint record."""
        root = _synthetic_project(tmp_path)
        routes = root / "routes" / "Service"
        routes.mkdir(parents=True)
        secret = "FakeApiSecret42"
        body = (
            '<node componentName="cREST">'
            '<elementParameter name="UNIQUE_NAME" value="restOut"/>'
            f'<elementParameter name="URI" value="https://api.example.com/orders?api_key={secret}"/>'
            "</node>"
        )
        (routes / "SecureRest_0.1.item").write_text(
            '<?xml version="1.0"?><talendfile:ProcessType ' + _ITEM_XMLNS + ">"
            + body + "</talendfile:ProcessType>", encoding="utf-8",
        )
        (routes / "SecureRest_0.1.properties").write_text(
            '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:TalendProperties="http://www.talend.org/properties">'
            '<TalendProperties:Property label="SecureRest" version="0.1"/>'
            "</xmi:XMI>", encoding="utf-8",
        )

        result = parser.parse(tmp_path)
        param = result.jobs[0].components[0].params[0]
        assert param.value == "[URL_REDACTED]"
        assert secret not in result.model_dump_json()
        assert "api.example.com" not in result.model_dump_json()
        assert param.redaction_descriptor is not None
        assert param.redaction_descriptor.scheme == "https"
        assert param.redaction_descriptor.auth_type == "api_key"
        endpoint = result.manifest.artifact_endpoints[0]
        assert endpoint["endpoint_descriptor"] == {
            "scheme": "https", "category": "rest", "auth_present": True,
            "auth_type": "api_key", "tls": True, "host_present": True,
        }

    def test_talend_routlet_spelling_is_classified_as_routelet_wrapper(
        self, parser, tmp_path,
    ):
        root = _synthetic_project(tmp_path)
        routes = root / "routes"
        routes.mkdir()
        (routes / "Trigger_0.1.item").write_text(
            '<?xml version="1.0"?><talendfile:ProcessType '
            + _ITEM_XMLNS
            + '><node componentName="trigger_OnTime_routlet">'
            '<elementParameter name="UNIQUE_NAME" value="trigger"/>'
            "</node></talendfile:ProcessType>",
            encoding="utf-8",
        )
        (routes / "Trigger_0.1.properties").write_text(
            '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:TalendProperties="http://www.talend.org/properties">'
            '<TalendProperties:Property label="Trigger" version="0.1"/>'
            "</xmi:XMI>",
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)

        route = next(job for job in result.jobs if job.name == "Trigger")
        assert route.artifact_kind == "route"
        assert route.artifact_role == "routelet_wrapper"

    def test_ambiguous_route_ctalendjob_never_creates_an_edge(self, parser, tmp_path):
        caller = _synthetic_project(tmp_path / "caller")
        one = _synthetic_project(tmp_path / "one")
        two = _synthetic_project(tmp_path / "two")
        _write_job(one, "Jobs", "Worker", "0.1")
        _write_job(two, "Jobs", "Worker", "0.1")
        routes = caller / "routes"
        routes.mkdir()
        (routes / "Inbound_0.1.item").write_text(
            '<?xml version="1.0"?><talendfile:ProcessType ' + _ITEM_XMLNS + '><node componentName="cTalendJob">'
            '<elementParameter name="SELECTED_JOB_NAME" value="Worker"/></node></talendfile:ProcessType>', encoding="utf-8",
        )
        (routes / "Inbound_0.1.properties").write_text(
            '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" xmlns:TalendProperties="http://www.talend.org/properties">'
            '<TalendProperties:Property label="Inbound" version="0.1"/></xmi:XMI>', encoding="utf-8",
        )
        result = parser.parse(tmp_path)
        route = next(job for job in result.jobs if job.name == "Inbound")
        assert route.artifact_refs[0].resolution == "ambiguous"
        assert route.artifact_refs[0].resolved_artifact_id == ""
        assert result.manifest.artifact_relations[0]["target"] == ""
    def test_multiple_roots_are_one_scan_with_qualified_duplicate_ids(self, parser, tmp_path):
        first = _synthetic_project(tmp_path / "exports" / "first")
        second = _synthetic_project(tmp_path / "exports" / "second")
        _write_job(first, "Jobs", "Shared", "0.1")
        _write_job(second, "Jobs", "Shared", "0.1")
        _write_job(second, "Jobs", "SecondOnly", "0.1")

        result = parser.parse(tmp_path)

        assert result.stats["total_roots"] == 2
        assert {job.source_root for job in result.jobs} == {
            "exports/first/SYNTH", "exports/second/SYNTH",
        }
        shared = [job for job in result.jobs if job.name == "Shared"]
        assert all(job.job_id != job.name and "/" not in job.job_id and ".." not in job.job_id for job in shared)
        assert len({job.job_id for job in shared}) == 2
        assert len(result.manifest.source_roots) == 2
        assert len(result.manifest.job_groups) == 3
        assert {
            result.manifest.job_groups[job.job_id]
            for job in result.jobs
        } == {
            "exports/first/SYNTH/Jobs",
            "exports/second/SYNTH/Jobs",
        }

    def test_cross_root_runjob_resolves_unique_target(self, parser, tmp_path):
        first = _synthetic_project(tmp_path / "one")
        second = _synthetic_project(tmp_path / "two")
        run_job = (
            '<node componentName="tRunJob"><elementParameter name="UNIQUE_NAME" value="run"/>'
            '<elementParameter name="PROCESS" value="Child"/></node>'
        )
        _write_job(first, "Jobs", "Parent", "0.1", run_job)
        _write_job(second, "Jobs", "Child", "0.1")

        result = parser.parse(tmp_path)
        parent = next(job for job in result.jobs if job.name == "Parent")
        assert parent.trunjob_refs[0].resolution == "global"
        assert result.manifest.job_calls[parent.job_id] == ["Child"]

    def test_ambiguous_cross_root_runjob_is_not_invented(self, parser, tmp_path):
        caller = _synthetic_project(tmp_path / "caller")
        one = _synthetic_project(tmp_path / "one")
        two = _synthetic_project(tmp_path / "two")
        run_job = (
            '<node componentName="tRunJob"><elementParameter name="UNIQUE_NAME" value="run"/>'
            '<elementParameter name="PROCESS" value="Child"/></node>'
        )
        _write_job(caller, "Jobs", "Parent", "0.1", run_job)
        _write_job(one, "Jobs", "Child", "0.1")
        _write_job(two, "Jobs", "Child", "0.1")

        result = parser.parse(tmp_path)
        parent = next(job for job in result.jobs if job.name == "Parent")
        assert parent.trunjob_refs[0].resolution == "ambiguous"
        assert parent.trunjob_refs[0].resolved_job_id == ""
        assert parent.job_id not in result.manifest.job_calls
        assert result.manifest.unresolved_job_calls[parent.job_id][0]["child_job"] == "Child"

    def test_same_root_duplicate_names_have_distinct_ids_and_ambiguous_call(self, parser, tmp_path):
        root = _synthetic_project(tmp_path / "bundle")
        other = _synthetic_project(tmp_path / "other")
        _write_job(root, "A", "Child", "0.1")
        _write_job(root, "B", "Child", "0.1")
        run_job = ('<node componentName="tRunJob"><elementParameter name="UNIQUE_NAME" value="run"/>'
                   '<elementParameter name="PROCESS" value="Child"/></node>')
        _write_job(root, "A", "Parent", "0.1", run_job)
        _write_job(other, "Jobs", "Other", "0.1")

        result = parser.parse(tmp_path)
        children = [job for job in result.jobs if job.name == "Child"]
        assert len({job.job_id for job in children}) == 2
        parent = next(job for job in result.jobs if job.name == "Parent")
        assert parent.trunjob_refs[0].resolution == "ambiguous"
        assert parent.job_id not in result.manifest.job_calls

    def test_process_and_routes_same_relative_path_do_not_collide(self, parser, tmp_path):
        root = _synthetic_project(tmp_path)
        (root / "routes").mkdir()
        _write_job(root, "A", "Shared", "0.1")
        # _write_job is process-only; copy the minimal design pair to routes.
        source = root / "process" / "A"
        target = root / "routes" / "A"
        target.mkdir(parents=True)
        for file in source.iterdir():
            (target / file.name).write_text(file.read_text())

        result = parser.parse(tmp_path)
        shared = [job for job in result.jobs if job.name == "Shared"]
        assert len(shared) == 2
        assert {job.source_design for job in shared} == {"process", "routes"}
        assert len({job.job_id for job in shared}) == 2

    def test_macos_noise_root_is_not_discovered(self, parser, tmp_path):
        real = _synthetic_project(tmp_path / "real")
        noise = _synthetic_project(tmp_path / "__MACOSX" / "noise")
        _write_job(real, "Jobs", "Real", "0.1")
        _write_job(noise, "Jobs", "Noise", "0.1")
        result = parser.parse(tmp_path)
        assert result.stats["total_roots"] == 1
        assert [job.name for job in result.jobs] == ["Real"]

    def test_generated_process_trees_are_not_independent_roots(self, parser, tmp_path):
        root = _synthetic_project(tmp_path)
        _write_job(root, "Jobs", "Real", "0.1")
        generated = root / "poms" / "jobs"
        _write_job(generated, "Jobs", "GeneratedCopy", "0.1")

        result = parser.parse(tmp_path)

        assert result.stats["total_roots"] == 1
        assert [job.name for job in result.jobs] == ["Real"]

    def test_marked_project_nested_below_project_remains_independent(self, parser, tmp_path):
        outer = _synthetic_project(tmp_path)
        nested = _synthetic_project(outer / "nested")
        (nested / "talend.project").write_text("", encoding="utf-8")
        _write_job(outer, "Jobs", "Outer", "0.1")
        _write_job(nested, "Jobs", "Nested", "0.1")

        result = parser.parse(tmp_path)

        assert result.stats["total_roots"] == 2
        assert {job.name for job in result.jobs} == {"Outer", "Nested"}

    def test_segmented_and_monolithic_manifest_share_job_file_index(self, parser, tmp_path):
        from intodoc.cli import _save_scan_result

        one = _synthetic_project(tmp_path / "one")
        two = _synthetic_project(tmp_path / "two")
        _write_job(one, "Jobs", "Shared", "0.1")
        _write_job(two, "Jobs", "Shared", "0.1")
        result = parser.parse(tmp_path)
        output = tmp_path / "out"
        _save_scan_result(result, output, CredentialFilter())

        segmented = json.loads((output / "scan" / "manifest.json").read_text())
        monolithic = json.loads((output / "scan_result.json").read_text())["manifest"]
        assert segmented["job_files"] == monolithic["job_files"]
        assert set(segmented["job_files"]) == {job.job_id for job in result.jobs}

    def test_segmented_paths_digest_qualified_ids_without_collision(self, tmp_path):
        from intodoc.cli import _save_scan_result

        jobs = [
            ParsedJob(name="one", path="one", job_id="a::b", source_root="root-a", group="Jobs"),
            ParsedJob(name="two", path="two", job_id="a__b", source_root="root-b", group="Jobs"),
        ]
        result = ScanResult(
            platform="talend", repo_root="/synthetic", jobs=jobs,
            manifest={"source_roots": [{"id": "root-a", "path": "root-a"}, {"id": "root-b", "path": "root-b"}]},
        )
        _save_scan_result(result, tmp_path, CredentialFilter())
        paths = result.manifest.job_files
        assert len(set(paths.values())) == 2
        assert all(len(part) <= 80 for path in paths.values() for part in path.split("/"))

    def test_empty_same_name_connections_are_not_merged_across_roots(self):
        first = ParsedJob(name="One", path="one", source_root="one", components=[Component(unique_name="c1", component_type="tDBInput", metadata_connection_ref="Shared")])
        second = ParsedJob(name="Two", path="two", source_root="two", components=[Component(unique_name="c2", component_type="tDBInput", metadata_connection_ref="Shared")])
        connections = [
            MetadataConnection(xmi_id="1", name="Shared", connection_type="DB", additional_params={"source_root": "one"}),
            MetadataConnection(xmi_id="2", name="Shared", connection_type="DB", additional_params={"source_root": "two"}),
        ]
        retained = TalendParser._qualify_composite_connections([first, second], connections, True)
        assert {connection.name for connection in retained} == {"one::Shared", "two::Shared"}
        assert first.components[0].metadata_connection_ref == "one::Shared"
        assert second.components[0].metadata_connection_ref == "two::Shared"

    def test_repository_connections_preserve_input_output_direction(self):
        job = ParsedJob(
            name="Transfer",
            path="process/Transfer.item",
            job_id="Transfer",
            components=[
                Component(
                    unique_name="read",
                    component_type="tMSSqlInput",
                    metadata_connection_ref="SourceDB",
                ),
                Component(
                    unique_name="write",
                    component_type="tPostgresqlOutput",
                    metadata_connection_ref="TargetDB",
                ),
            ],
        )

        manifest = TalendParser._build_manifest([job], [], [])

        assert manifest.artifact_resources == [
            {
                "artifact": "Transfer",
                "resource_type": "repository_connection",
                "resource": "SourceDB",
                "direction": "input",
                "component_name": "read",
                "provenance": "tMSSqlInput",
            },
            {
                "artifact": "Transfer",
                "resource_type": "repository_connection",
                "resource": "TargetDB",
                "direction": "output",
                "component_name": "write",
                "provenance": "tPostgresqlOutput",
            },
        ]

    def test_existing_connection_reference_inherits_repository_identity_and_direction(self):
        job = ParsedJob(
            name="Transfer",
            path="process/Transfer.item",
            job_id="Transfer",
            components=[
                Component(
                    unique_name="tMSSqlConnection_1",
                    component_type="tMSSqlConnection",
                    metadata_connection_ref="SharedDB",
                ),
                Component(
                    unique_name="tMSSqlInput_1",
                    component_type="tMSSqlInput",
                    params=[
                        ComponentParam(name="USE_EXISTING_CONNECTION", value="true"),
                        ComponentParam(name="CONNECTION", value="tMSSqlConnection_1"),
                    ],
                ),
                Component(
                    unique_name="tMSSqlOutput_1",
                    component_type="tMSSqlOutput",
                    params=[
                        ComponentParam(name="USE_EXISTING_CONNECTION", value="true"),
                        ComponentParam(name="CONNECTION", value="tMSSqlConnection_1"),
                    ],
                ),
            ],
        )

        manifest = TalendParser._build_manifest([job], [], [])

        assert job.components[1].metadata_connection_ref == "SharedDB"
        assert job.components[2].metadata_connection_ref == "SharedDB"
        assert manifest.artifact_resources == [
            {
                "artifact": "Transfer",
                "resource_type": "repository_connection",
                "resource": "SharedDB",
                "direction": "input",
                "component_name": "tMSSqlInput_1",
                "provenance": "tMSSqlInput",
            },
            {
                "artifact": "Transfer",
                "resource_type": "repository_connection",
                "resource": "SharedDB",
                "direction": "output",
                "component_name": "tMSSqlOutput_1",
                "provenance": "tMSSqlOutput",
            },
        ]

    def test_unresolved_existing_connection_reference_does_not_invent_resource(self):
        job = ParsedJob(
            name="BrokenReference",
            path="process/BrokenReference.item",
            job_id="BrokenReference",
            components=[
                Component(
                    unique_name="tMSSqlInput_1",
                    component_type="tMSSqlInput",
                    params=[
                        ComponentParam(name="USE_EXISTING_CONNECTION", value="true"),
                        ComponentParam(name="CONNECTION", value="missingConnection"),
                    ],
                ),
            ],
        )

        manifest = TalendParser._build_manifest([job], [], [])

        assert job.components[0].metadata_connection_ref == ""
        assert manifest.artifact_resources == []


class TestTMapExtraction:
    """tMap MapperData must survive credential filtering end-to-end.

    Regression: URL redaction used to rewrite xmlns declaration URIs,
    which broke the namespaced xsi:type lookup and silently dropped
    every tMap mapping from the scan.
    """

    def test_mapper_data_extracted_through_filter(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "MapJob", "1.0", body=_TMAP_NODE)
        result = parser.parse(tmp_path)
        map_job = next(j for j in result.jobs if j.name == "MapJob")
        assert len(map_job.tmap_data) == 1
        tmap = map_job.tmap_data[0]
        assert tmap.input_tables == ["row1"]
        assert tmap.output_tables == ["out1"]
        assert len(tmap.entries) == 1
        assert tmap.entries[0].column_name == "wonum"
        assert tmap.entries[0].expression == "row1.wonum"


def _write_context(project, rel_dir, name, version, var_xml):
    """Write a context group .item at context/<rel_dir>/ (empty rel_dir = flat)."""
    ctx_dir = project / "context" / rel_dir if rel_dir else project / "context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    (ctx_dir / f"{name}_{version}.item").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        '<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" '
        'xmi:version="2.0">'
        f'<talendfile:ContextType name="DEV">{var_xml}</talendfile:ContextType>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


def _write_talend_descriptor(project, label, technical_label):
    project.mkdir(parents=True, exist_ok=True)
    (project / "talend.project").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Project label="{label}" technicalLabel="{technical_label}"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


def _write_repository_context(project, name, context_id, variables):
    var_xml = "".join(
        f'<contextParameter name="{var_name}" type="id_String" value="{value}"/>'
        for var_name, value in variables
    )
    _write_context(project, name, name, "0.1", var_xml)
    props = project / "context" / name / f"{name}_0.1.properties"
    props.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Property id="{context_id}" label="{name}" '
        'purpose="Shared runtime configuration"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


class TestContextDiscovery:
    """Context items occur flat AND nested in per-group subfolders, and are
    version-duplicated like jobs -- both layouts must yield latest-only groups."""

    def test_nested_context_groups_found(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_context(project, "MAS", "MAS", "0.1",
                       '<contextParameter name="MAS_URL" type="id_String" value="x"/>')
        result = parser.parse(tmp_path)
        names = [g.name for g in result.context_groups]
        assert "MAS" in names

    def test_flat_context_groups_still_found(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_context(project, "", "FlatGroup", "0.2",
                       '<contextParameter name="Host" type="id_String" value="h"/>')
        result = parser.parse(tmp_path)
        assert "FlatGroup" in [g.name for g in result.context_groups]

    def test_context_versions_deduped_to_latest(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_context(project, "Grp", "Grp", "0.1",
                       '<contextParameter name="V" type="id_String" value="old"/>')
        _write_context(project, "Grp", "Grp", "0.7",
                       '<contextParameter name="V" type="id_String" value="new"/>')
        result = parser.parse(tmp_path)
        grps = [g for g in result.context_groups if g.name == "Grp"]
        assert len(grps) == 1
        assert grps[0].variables[0].values.get("DEV") == "new"

    def test_version_suffix_stripped_beyond_zero_versions(self, parser, tmp_path):
        """Group names strip any numeric version suffix, not only '_0.x'."""
        project = _synthetic_project(tmp_path)
        _write_context(project, "Big", "BigGroup", "3.4",
                       '<contextParameter name="V" type="id_String" value="v"/>')
        result = parser.parse(tmp_path)
        assert "BigGroup" in [g.name for g in result.context_groups]

    def test_descriptor_backed_support_project_links_by_repository_id(self, parser, tmp_path):
        execution = _synthetic_project(tmp_path / "client")
        support = tmp_path / "bundle" / "shared_config"
        _write_talend_descriptor(support, "Shared Configuration", "SHARED_CONFIG")
        _write_repository_context(
            support,
            "Runtime",
            "_runtime_context",
            [("Host", "example.internal"), ("API_SECRET", "example-sensitive-value")],
        )
        inline_context = (
            '<context name="DEV">'
            '<contextParameter name="Host" type="id_String" value="" '
            'repositoryContextId="_runtime_context"/>'
            '<contextParameter name="API_SECRET" type="id_String" value="" '
            'repositoryContextId="_runtime_context"/>'
            "</context>"
        )
        _write_job(execution, "Jobs", "Consumer", "0.1", inline_context)

        result = parser.parse(tmp_path)

        assert result.stats["execution_roots"] == 1
        assert result.stats["support_roots"] == 1
        support_descriptor = next(
            root for root in result.manifest.source_roots if root["role"] == "support"
        )
        assert support_descriptor["path"] == "bundle/shared_config"
        assert support_descriptor["label"] == "Shared Configuration"

        context = next(group for group in result.context_groups if group.name == "Runtime")
        assert context.context_id == "_runtime_context"
        assert context.source_role == "support"
        assert context.source_project == "Shared Configuration"
        assert result.manifest.job_contexts_used["Consumer"] == ["Runtime"]
        assert result.manifest.context_used_by["Runtime"] == ["Consumer"]
        assert result.manifest.job_support_roots["Consumer"] == ["bundle/shared_config"]
        assert result.manifest.job_context_links["Consumer"][0]["resolution"] == "repository_id"

        filter_scan_result(result, CredentialFilter())
        api_secret = next(
            variable for variable in context.variables if variable.name == "API_SECRET"
        )
        assert set(api_secret.values.values()) == {"[REDACTED]"}

    def test_ambiguous_repository_id_is_reported_without_inventing_edge(self, parser, tmp_path):
        execution = _synthetic_project(tmp_path / "client")
        inline_context = (
            '<context name="DEV"><contextParameter name="Host" type="id_String" '
            'value="" repositoryContextId="_duplicate"/></context>'
        )
        _write_job(execution, "Jobs", "Consumer", "0.1", inline_context)
        for folder, name in (("one", "First"), ("two", "Second")):
            support = tmp_path / folder
            _write_talend_descriptor(support, name, name.upper())
            _write_repository_context(support, name, "_duplicate", [("Host", folder)])

        result = parser.parse(tmp_path)

        assert "Consumer" not in result.manifest.job_contexts_used
        assert result.manifest.unresolved_context_refs["Consumer"] == [{
            "context_id": "_duplicate",
            "resolution": "ambiguous",
        }]


# ── Joblets / Routelets / tXMLMap / TDM Maps ────────────────────────
#
# Ground-truth verification against a real 6-project Talend export found
# three artifact categories the scanner skipped silently: joblets/routelets
# (directory never walked), tXMLMap (unparsed XML-tree mapper, a separate
# component from classic tMap), and TDM Maps (unparsed Maps/*.xml). These
# tests cover the fix plus the ScanWarning mechanism added alongside it.


_JOBLET_XMLNS = (
    'xmlns:xmi="http://www.omg.org/XMI" '
    'xmlns:TalendMapper="http://www.talend.org/mapper" '
    'xmlns:model="http://www.talend.com/joblet.ecore" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


def _write_joblet(project, name, version, body=""):
    """Write a minimal joblet .item + .properties pair.

    Real Talend joblet .item files wrap the process body one level deeper
    than jobs/routes/routelets: <xmi:XMI><model:JobletProcess>...
    </model:JobletProcess></xmi:XMI>, rather than a bare <ProcessType> root.
    """
    joblet_dir = project / "joblets"
    joblet_dir.mkdir(parents=True, exist_ok=True)
    (joblet_dir / f"{name}_{version}.item").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        f'<xmi:XMI {_JOBLET_XMLNS} xmi:version="2.0">'
        f'<model:JobletProcess defaultContext="DEV" jobType="Standard">{body}</model:JobletProcess>'
        "</xmi:XMI>",
        encoding="utf-8",
    )
    (joblet_dir / f"{name}_{version}.properties").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xmi:XMI xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Property label="{name}" version="{version}"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


def _write_routelet(project, name, version, body=""):
    """Write a routelet .item + .properties pair. Unlike joblets, routelets
    share the plain <ProcessType> root shape used by jobs/routes."""
    routelet_dir = project / "routelets"
    routelet_dir.mkdir(parents=True, exist_ok=True)
    (routelet_dir / f"{name}_{version}.item").write_text(
        '<?xml version="1.0"?><talendfile:ProcessType ' + _ITEM_XMLNS + '>' + body + '</talendfile:ProcessType>',
        encoding="utf-8",
    )
    (routelet_dir / f"{name}_{version}.properties").write_text(
        '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Property label="{name}" version="{version}"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


class TestJobletRoutletDiscovery:
    """Regression coverage for gap #1: joblets/routelets directories were
    never walked at all (joblet_count/routelet_count always 0)."""

    def test_joblet_is_discovered_and_classified(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_joblet(project, "SharedMapping", "0.1", body=_TMAP_NODE)

        result = parser.parse(tmp_path)

        joblet = next(j for j in result.jobs if j.name == "SharedMapping")
        assert joblet.artifact_kind == "joblet"
        assert joblet.artifact_role == "reusable"
        assert result.stats["joblet_count"] == 1
        assert result.manifest.artifact_kinds[joblet.job_id or joblet.name] == "joblet"

    def test_joblet_nested_body_components_are_parsed(self, parser, tmp_path):
        """A joblet's node/connection/context live one level deeper
        (<model:JobletProcess>) than a job/route/routelet's bare
        <ProcessType> root -- the parser must descend into that wrapper
        rather than finding zero components."""
        project = _synthetic_project(tmp_path)
        _write_joblet(project, "MappingJoblet", "0.1", body=_TMAP_NODE)

        result = parser.parse(tmp_path)

        joblet = next(j for j in result.jobs if j.name == "MappingJoblet")
        assert len(joblet.components) == 1
        assert joblet.components[0].component_type == "tMap"
        assert joblet.scan_quality == "complete"

    def test_parent_job_resolves_explicit_joblet_family_reference(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_joblet(project, "SharedMapping", "0.1", body=_TMAP_NODE)
        _write_job(
            project, "Iface", "Parent", "0.1",
            body=(
                '<node componentName="SharedMapping">'
                '<elementParameter field="TEXT" name="UNIQUE_NAME" value="SharedMapping_1"/>'
                '<elementParameter field="TEXT" name="FAMILY" value="Joblets"/>'
                "</node>"
            ),
        )

        result = parser.parse(tmp_path)
        parent = next(job for job in result.jobs if job.name == "Parent")
        joblet = next(job for job in result.jobs if job.name == "SharedMapping")
        assert len(parent.artifact_refs) == 1
        ref = parent.artifact_refs[0]
        assert ref.relation_type == "job_uses_joblet"
        assert ref.component_name == "SharedMapping_1"
        assert ref.resolution == "same_root"
        assert ref.resolved_artifact_id == (joblet.job_id or joblet.name)
        assert {
            "source": parent.job_id or parent.name,
            "target": joblet.job_id or joblet.name,
            "relation_type": "job_uses_joblet",
            "component_name": "SharedMapping_1",
            "provenance": "joblet_family",
            "resolution": "same_root",
        } in result.manifest.artifact_relations

    def test_routelet_is_discovered_and_classified(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        body = '<node componentName="cProcessor"><elementParameter name="UNIQUE_NAME" value="proc"/></node>'
        _write_routelet(project, "SharedRouting", "0.1", body=body)

        result = parser.parse(tmp_path)

        routelet = next(j for j in result.jobs if j.name == "SharedRouting")
        assert routelet.artifact_kind == "routelet"
        assert routelet.artifact_role == "reusable"
        assert len(routelet.components) == 1
        assert result.stats["routelet_count"] == 1

    def test_stats_are_zero_when_no_joblets_or_routelets_present(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Jobs", "Plain", "0.1")
        result = parser.parse(tmp_path)
        assert result.stats["joblet_count"] == 0
        assert result.stats["routelet_count"] == 0


_TXMLMAP_NODE = (
    '<node componentName="tXMLMap" componentVersion="2.1">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tXMLMap_1"/>'
    '<nodeData xsi:type="TalendXMLMap:XmlMapData">'
    '<inputTrees name="row1">'
    '<nodes name="GUID" xpath="row1/GUID" type="id_String"/>'
    "</inputTrees>"
    '<outputTrees name="OutputBody">'
    '<nodes name="body" xpath="OutputBody/body" type="id_Document">'
    '<children name="GUIDElement" xpath="OutputBody.body:/GUIDElement" '
    'type="id_String" expression="row1.GUID"/>'
    "</nodes>"
    "</outputTrees>"
    '<connections source="//@node.0/@nodeData/@inputTrees.0/@nodes.0" '
    'target="//@node.0/@nodeData/@outputTrees.0/@nodes.0/@children.0"/>'
    "</nodeData>"
    "</node>"
)


class TestTXMLMapExtraction:
    """Regression coverage for gap #2: tXMLMap (TalendXMLMap) is a separate
    mapper from classic tMap and had zero model representation or parsing."""

    def test_xml_map_data_extracted_with_expressions(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "XmlMapJob", "1.0", body=_TXMLMAP_NODE)

        result = parser.parse(tmp_path)

        job = next(j for j in result.jobs if j.name == "XmlMapJob")
        assert len(job.txmlmap_data) == 1
        txmap = job.txmlmap_data[0]
        assert txmap.component_name == "tXMLMap_1"
        assert txmap.input_trees[0].name == "row1"
        assert txmap.input_trees[0].fields[0].name == "GUID"
        assert txmap.output_trees[0].name == "OutputBody"
        # Depth-first flatten: intermediate "body" node, then nested "GUIDElement".
        output_fields = {f.name: f for f in txmap.output_trees[0].fields}
        assert output_fields["GUIDElement"].expression == "row1.GUID"
        assert len(txmap.raw_connections) == 1
        assert "nodes.0" in txmap.raw_connections[0]
        assert txmap.unresolved_connections == []
        assert txmap.resolved_connections[0]["source_field"] == "GUID"
        assert txmap.resolved_connections[0]["target_field"] == "GUIDElement"

    def test_xml_map_data_does_not_call_resolved_wiring_partial(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "XmlMapJob", "1.0", body=_TXMLMAP_NODE)

        result = parser.parse(tmp_path)

        txmlmap_warnings = [w for w in result.warnings if w.phase == "txmlmap"]
        assert txmlmap_warnings == []

    def test_xml_map_data_preserves_unsupported_reference_as_gap(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        body = _TXMLMAP_NODE.replace(
            'source="//@node.0/@nodeData/@inputTrees.0/@nodes.0"',
            'source="other.resource#//@inputTrees.0/@nodes.0"',
        )
        _write_job(project, "Iface", "XmlMapJob", "1.0", body=body)
        result = parser.parse(tmp_path)
        txmap = result.jobs[0].txmlmap_data[0]
        assert txmap.resolved_connections == []
        assert txmap.unresolved_connections[0]["reason"] == "unsupported_or_out_of_range_emf_reference"
        assert len([w for w in result.warnings if w.phase == "txmlmap"]) == 1

    def test_classic_tmap_unaffected_by_xml_map_support(self, parser, tmp_path):
        """Regression guard: adding tXMLMap parsing must not break tMap."""
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "MapJob", "1.0", body=_TMAP_NODE)
        result = parser.parse(tmp_path)
        job = next(j for j in result.jobs if j.name == "MapJob")
        assert len(job.tmap_data) == 1
        assert len(job.txmlmap_data) == 0
        assert not [w for w in result.warnings if w.phase == "txmlmap"]


class TestRoutineFidelity:
    def test_routine_tail_is_preserved_and_root_qualified(self, parser, tmp_path):
        root = tmp_path / "SYNTH"
        routines = root / "code" / "routines"
        routines.mkdir(parents=True)
        lines = [f"// line {index}" for index in range(250)]
        tail_secret = "FakeRoutineTailSecret42"
        lines.append(
            "public static String decisiveRule() { "
            f"String endpoint = \"https://example.invalid?a=1&password={tail_secret}\"; "
            "return \"tail-rule\"; }"
        )
        (routines / "Rules_1.2.item").write_text("\n".join(lines), encoding="utf-8")

        parsed = parser._parse_routines(root, source_root="exports/SYNTH")

        assert len(parsed) == 1
        routine = parsed[0]
        assert routine.source_complete is True
        assert "tail-rule" in routine.source_code
        assert tail_secret not in routine.source_code
        assert "[URL_REDACTED]" in routine.source_code
        assert "truncated" not in routine.source_code
        assert routine.routine_id.startswith("exports/SYNTH::")
        assert routine.version == "1.2"
        assert len(routine.source_sha256) == 64


def _tdm_map_xml(name, in_struct, out_struct, output_children):
    """Build a minimal TDM Maps/*.xml document.

    ``output_children`` is a list of (name, value_expression) pairs rendered
    as direct children of the outputMapElem tree.
    """
    children_xml = "".join(
        f'<child name="{cname}"><value>{cvalue}</value></child>'
        for cname, cvalue in output_children
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<root name="{name}" inStruct="{in_struct}" outStruct="{out_struct}">'
        '<inputMapElem name="In"><child name="Field1"/></inputMapElem>'
        f'<outputMapElem name="Out">{children_xml}</outputMapElem>'
        "</root>"
    )


class TestTdmMapDiscovery:
    """Regression coverage for gap #3: Talend Data Mapper Maps/*.xml files
    (a third, separate mapping format used in ESB/API Services projects)
    were never discovered or parsed."""

    def test_tdm_map_under_maps_jobs_associates_with_matching_job(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "TargetJob", "0.1")
        maps_dir = project / "Maps" / "Jobs" / "TargetJob"
        maps_dir.mkdir(parents=True)
        (maps_dir / "map1.xml").write_text(
            _tdm_map_xml("map1", "Structures/In.xml", "Structures/Out.xml",
                         [("Field1", "in$/Field1")]),
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)

        assert len(result.tdm_maps) == 1
        tdm = result.tdm_maps[0]
        assert tdm.association == "directory_jobs"
        assert tdm.owning_artifact == "TargetJob"
        assert tdm.input_struct == "Structures/In.xml"
        assert tdm.output_struct == "Structures/Out.xml"
        assert tdm.output_tree.fields[0].name == "Field1"
        assert tdm.output_tree.fields[0].expression == "in$/Field1"
        assert tdm.input_tree.fields[0].name == "Field1"
        assert result.stats["tdm_map_count"] == 1
        assert result.stats["tdm_map_unresolved_count"] == 0

    def test_tdm_map_under_maps_routes_associates_with_matching_route(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        routes = project / "routes" / "Group"
        routes.mkdir(parents=True)
        (routes / "TargetRoute_0.1.item").write_text(
            '<?xml version="1.0"?><talendfile:ProcessType ' + _ITEM_XMLNS + '></talendfile:ProcessType>',
            encoding="utf-8",
        )
        (routes / "TargetRoute_0.1.properties").write_text(
            '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:TalendProperties="http://www.talend.org/properties">'
            '<TalendProperties:Property label="TargetRoute" version="0.1"/></xmi:XMI>',
            encoding="utf-8",
        )
        maps_dir = project / "Maps" / "Routes" / "TargetRoute"
        maps_dir.mkdir(parents=True)
        (maps_dir / "map1.xml").write_text(
            _tdm_map_xml("map1", "Structures/In.xml", "Structures/Out.xml", []),
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)

        tdm = result.tdm_maps[0]
        assert tdm.association == "directory_routes"
        assert tdm.owning_artifact == "TargetRoute"

    def test_top_level_tdm_map_is_unresolved_with_warning(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        maps_dir = project / "Maps"
        maps_dir.mkdir(parents=True)
        (maps_dir / "flat_map.xml").write_text(
            _tdm_map_xml("flat_map", "Structures/In.xml", "Structures/Out.xml", []),
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)

        tdm = result.tdm_maps[0]
        assert tdm.association == "unresolved"
        assert tdm.owning_artifact == ""
        tdm_warnings = [w for w in result.warnings if w.phase == "tdm_map"]
        assert any("ownership could not be resolved" in w.message for w in tdm_warnings)

    def test_tdm_map_field_tree_truncates_with_warning(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        maps_dir = project / "Maps"
        maps_dir.mkdir(parents=True)
        many_fields = [(f"F{i}", f"in$/F{i}") for i in range(5)]
        (maps_dir / "big_map.xml").write_text(
            _tdm_map_xml("big_map", "Structures/In.xml", "Structures/Out.xml", many_fields),
            encoding="utf-8",
        )
        parser._MAX_TDM_FIELDS = 2

        result = parser.parse(tmp_path)

        tdm = result.tdm_maps[0]
        assert tdm.output_tree.truncated is True
        assert len(tdm.output_tree.fields) == 2
        truncation_warnings = [
            w for w in result.warnings
            if w.phase == "tdm_map" and "truncated" in w.message
        ]
        assert len(truncation_warnings) == 1

    def test_tdm_map_preserves_more_than_legacy_field_limit_by_default(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        maps_dir = project / "Maps"
        maps_dir.mkdir(parents=True)
        many_fields = [(f"F{i}", f"in$/F{i}") for i in range(501)]
        (maps_dir / "big_map.xml").write_text(
            _tdm_map_xml("big_map", "Structures/In.xml", "Structures/Out.xml", many_fields),
            encoding="utf-8",
        )
        result = parser.parse(tmp_path)
        assert result.tdm_maps[0].output_tree.truncated is False
        assert len(result.tdm_maps[0].output_tree.fields) == 501


class TestUnscannedTalendCategories:
    """The ScanWarning mechanism generalizes beyond the three named gaps: any
    top-level Talend repository directory this scanner doesn't recognize
    (e.g. resources/, services/, tests/ seen in real ESB exports) must be
    flagged rather than silently ignored."""

    def test_unrecognized_directory_emits_warning(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Jobs", "Plain", "0.1")
        resources_dir = project / "resources"
        resources_dir.mkdir()
        (resources_dir / "some.jar").write_text("binary-ish", encoding="utf-8")

        result = parser.parse(tmp_path)

        unscanned = [w for w in result.warnings if w.kind == "unscanned_category"]
        assert len(unscanned) == 1
        assert "resources" in unscanned[0].message

    def test_known_directories_do_not_emit_warning(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Jobs", "Plain", "0.1")
        (project / "sqlPatterns").mkdir()
        (project / "sqlPatterns" / "pattern.txt").write_text("select 1", encoding="utf-8")

        result = parser.parse(tmp_path)

        assert not [w for w in result.warnings if w.kind == "unscanned_category"]

    def test_empty_unknown_directory_is_not_flagged(self, parser, tmp_path):
        """An empty directory hides nothing -- no warning needed."""
        project = _synthetic_project(tmp_path)
        _write_job(project, "Jobs", "Plain", "0.1")
        (project / "services").mkdir()

        result = parser.parse(tmp_path)

        assert not [w for w in result.warnings if w.kind == "unscanned_category"]

    def test_nested_talend_root_is_not_flagged_as_unscanned(self, parser, tmp_path):
        """A nested project directory that is itself a recognized Talend
        root (has its own talend.project) is scanned under its own root --
        it must not also be flagged as an unscanned category of the outer
        root that happens to contain it."""
        outer = _synthetic_project(tmp_path)
        nested = _synthetic_project(outer / "nested")
        (nested / "talend.project").write_text("", encoding="utf-8")
        _write_job(outer, "Jobs", "Outer", "0.1")
        _write_job(nested, "Jobs", "Nested", "0.1")

        result = parser.parse(tmp_path)

        assert result.stats["total_roots"] == 2
        assert not [w for w in result.warnings if w.kind == "unscanned_category"]
