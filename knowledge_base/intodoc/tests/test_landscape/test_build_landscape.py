"""Tests for `intodoc.landscape.build_landscape` -- system identity dedup,
control_flow vs data edges, provenance presence, the unread-channel
diagnostic, and credential safety.

All secret-shaped values in fixtures are FAKE (same convention as
test_lineage/test_build_lineage_matrix.py) -- never real credentials.
"""

from intodoc.landscape import build_landscape
from intodoc.models import (
    LineageConfidence,
    LineageEntry,
    LineageMatrix,
    LandscapeProvenance,
    MetadataConnection,
    ParsedJob,
    ProjectManifest,
    ScanResult,
)
from intodoc.scanner.credential_filter import CredentialFilter


def _empty_matrix() -> LineageMatrix:
    return LineageMatrix()


def _manifest(**kwargs) -> ProjectManifest:
    return ProjectManifest(**kwargs)


def _scan(jobs=None, metadata_connections=None, manifest=None) -> ScanResult:
    return ScanResult(
        platform="talend",
        repo_root="/fake",
        jobs=jobs or [],
        metadata_connections=metadata_connections or [],
        manifest=manifest or ProjectManifest(),
    )


def _build(scan_result, lineage_matrix=None, cred_filter=None):
    return build_landscape(scan_result, lineage_matrix or _empty_matrix(), cred_filter or CredentialFilter())


def test_l2_records_keep_safe_redacted_uri_descriptors_without_peer_guessing():
    manifest = _manifest(
        source_roots=[{"id": "root", "path": "root", "label": "Root"}],
        job_source_roots={"Route": "root"},
        artifact_channels=[{
            "artifact": "Route", "channel_type": "jms",
            "channel": "configured endpoint", "direction": "output",
            "component_name": "jmsOut",
            "channel_descriptor": {
                "scheme": "jms", "category": "jms", "auth_present": True,
                "auth_type": "basic", "tls": False, "host_present": True,
            },
        }],
        artifact_endpoints=[{
            "artifact": "Route", "endpoint_type": "rest", "direction": "output",
            "component_name": "restOut",
            "endpoint_descriptor": {
                "scheme": "https", "category": "rest", "auth_present": True,
                "auth_type": "api_key", "tls": True, "host_present": True,
            },
        }],
    )
    landscape = _build(_scan(
        jobs=[ParsedJob(name="Route", job_id="Route", source_root="root", path="routes/Route")],
        manifest=manifest,
    ))

    assert len(landscape.channels) == 1
    assert landscape.channels[0].channel == "configured endpoint"
    assert landscape.channels[0].redaction_descriptor is not None
    assert landscape.channels[0].redaction_descriptor.scheme == "jms"
    assert len(landscape.interfaces) == 1
    assert landscape.interfaces[0].redaction_descriptor is not None
    assert landscape.interfaces[0].redaction_descriptor.auth_type == "api_key"
    # Redacted names lack correlation identity, so no peer edge is invented.
    assert not landscape.edges


# ── System identity dedup ────────────────────────────────────────────────


class TestSystemDedup:
    def test_metadata_connections_merge_by_physical_identity_not_name(self):
        """Two differently-named connection objects pointing at the exact
        same (type, host, database) must merge into one system."""
        conns = [
            MetadataConnection(
                xmi_id="id1", name="LogConnection", connection_type="Microsoft SQL Server",
                host="sql-log.internal", database="logging",
                additional_params={"source_root": "root/A"},
            ),
            MetadataConnection(
                xmi_id="id2", name="LogDBConnection", connection_type="Microsoft SQL Server",
                host="sql-log.internal", database="logging",
                additional_params={"source_root": "root/B"},
            ),
        ]
        scan_result = _scan(metadata_connections=conns)
        landscape = _build(scan_result)
        assert len(landscape.systems) == 1
        system = landscape.systems[0]
        assert system.kind == "database"
        assert system.name == "Log"
        assert {e.label for e in system.evidence} == {"LogConnection", "LogDBConnection"}
        assert set(system.source_roots) == {"root/A", "root/B"}

    def test_metadata_connections_with_different_identity_stay_separate(self):
        conns = [
            MetadataConnection(
                xmi_id="id1", name="MaximoDBConnection", connection_type="Oracle with service name",
                host="[HOST_REDACTED]", database="context.connection_maximoDB_Sid",
            ),
            MetadataConnection(
                xmi_id="id2", name="QuintiqDBConnection", connection_type="Microsoft SQL Server",
                host="[HOST_REDACTED]", database="context.connection_quintiqDB_Database",
            ),
        ]
        scan_result = _scan(metadata_connections=conns)
        landscape = _build(scan_result)
        assert len(landscape.systems) == 2
        names = {s.name for s in landscape.systems}
        assert names == {"Maximo", "Quintiq"}

    def test_redacted_or_empty_physical_discriminators_never_merge_across_roots(self):
        """Filtering is not physical-identity evidence: unrelated endpoint
        details must not collapse into one system just because both hosts were
        replaced with the same redaction marker."""
        conns = [
            MetadataConnection(
                xmi_id="a", name="OrdersConnection", connection_type="Oracle",
                host="[HOST_REDACTED]", database="",
                additional_params={"source_root": "root/A"},
            ),
            MetadataConnection(
                xmi_id="b", name="InventoryConnection", connection_type="Oracle",
                host="[HOST_REDACTED]", database="context.inventory_database",
                additional_params={"source_root": "root/B"},
            ),
        ]
        landscape = _build(_scan(metadata_connections=conns))
        assert len(landscape.systems) == 2
        assert {system.name for system in landscape.systems} == {"Orders", "Inventory"}
        assert {tuple(system.source_roots) for system in landscape.systems} == {
            ("root/A",), ("root/B",),
        }

    def test_context_group_core_recurring_across_projects_merges_into_connection_system(self):
        """A context-group name whose suffix-stripped core matches a
        metadata-connection system's core, recurring across >= 2
        source_roots, is a rich business-name signal that should win over
        the connection's own display name."""
        conns = [
            MetadataConnection(
                xmi_id="id1", name="MaximoDBConnection", connection_type="Oracle with service name",
                host="[HOST_REDACTED]", database="context.maximo_sid",
            ),
        ]
        manifest = _manifest(context_metadata={
            "maximoContext": {
                "name": "maximoContext", "context_id": "c1", "source_root": "root/A",
                "source_project": "A", "source_role": "execution", "purpose": "", "description": "",
                "environments": ["PROD"], "variable_count": 2,
            },
            "Maximo": {
                "name": "Maximo", "context_id": "c2", "source_root": "root/B",
                "source_project": "B", "source_role": "execution", "purpose": "", "description": "",
                "environments": ["PROD"], "variable_count": 1,
            },
        })
        scan_result = _scan(metadata_connections=conns, manifest=manifest)
        landscape = _build(scan_result)
        assert len(landscape.systems) == 1
        system = landscape.systems[0]
        # Richest signal (context group) wins the canonical name.
        assert system.name == "Maximo"
        assert system.kind == "database"  # kind still comes from the connection evidence
        signal_types = {e.signal_type for e in system.evidence}
        assert signal_types == {"metadata_connection", "context_group"}

    def test_single_project_context_group_is_not_promoted_to_a_system(self):
        """No cross-project recurrence and no matching connection: a bare
        context-group name is project-local flow config, not evidence of a
        shared external system -- never fabricated into one."""
        manifest = _manifest(context_metadata={
            "SomeFlow_Context": {
                "name": "SomeFlow_Context", "context_id": "c1", "source_root": "root/A",
                "source_project": "A", "source_role": "execution", "purpose": "", "description": "",
                "environments": ["PROD"], "variable_count": 2,
            },
        })
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        assert landscape.systems == []

    def test_context_group_matching_application_label_is_excluded(self):
        """A context group whose core equals the owning application's own
        label names the application's config, not an external system."""
        job = ParsedJob(name="J1", path="p", source_root="root/A")
        manifest = _manifest(
            source_roots=[{"id": "root/A", "path": "root/A", "label": "AssetCheck", "role": "execution"}],
            job_source_roots={"J1": "root/A"},
            context_metadata={
                "AssetCheckInterface_Context": {
                    "name": "AssetCheckInterface_Context", "context_id": "c1", "source_root": "root/A",
                    "source_project": "AssetCheck", "source_role": "execution", "purpose": "", "description": "",
                    "environments": ["PROD"], "variable_count": 2,
                },
                "AssetCheckOther_Context": {
                    "name": "AssetCheckOther_Context", "context_id": "c2", "source_root": "root/B",
                    "source_project": "AssetCheck", "source_role": "execution", "purpose": "", "description": "",
                    "environments": ["PROD"], "variable_count": 2,
                },
            },
        )
        scan_result = _scan(jobs=[job], manifest=manifest)
        landscape = _build(scan_result)
        # Both context groups reduce to core "AssetCheck", which matches the
        # application's own label -- must not spawn a self-referential system.
        assert landscape.systems == []

    def test_generic_transport_labels_are_not_promoted_to_systems(self):
        """A transport category is evidence of an integration surface, not
        evidence that a peer system called 'REST API' exists."""
        manifest = _manifest(
            job_source_roots={"J1": "root/A", "J2": "root/B"},
            job_external_systems={"J1": ["REST API"], "J2": ["REST API"]},
        )
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        assert landscape.systems == []
        assert landscape.edges == []

    def test_fallback_skips_database_labels_already_covered_by_connections(self):
        manifest = _manifest(
            job_source_roots={"J1": "root/A"},
            job_external_systems={"J1": ["MSSQL"]},
        )
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        assert landscape.systems == []


# ── Applications ─────────────────────────────────────────────────────────


class TestApplications:
    def test_one_application_per_source_root_with_counts(self):
        jobs = [
            ParsedJob(name="J1", path="p", source_root="root/A", artifact_kind="job"),
            ParsedJob(name="J2", path="p", source_root="root/A", artifact_kind="route"),
            ParsedJob(name="J3", path="p", source_root="root/B", artifact_kind="job"),
        ]
        manifest = _manifest(source_roots=[
            {"id": "root/A", "path": "root/A", "label": "AppA", "role": "execution"},
            {"id": "root/B", "path": "root/B", "label": "AppB", "role": "execution"},
        ])
        scan_result = _scan(jobs=jobs, manifest=manifest)
        landscape = _build(scan_result)
        assert len(landscape.applications) == 2
        app_a = next(a for a in landscape.applications if a.id == "root/A")
        assert app_a.label == "AppA"
        assert app_a.artifact_count == 2
        assert app_a.kinds_breakdown == {"job": 1, "route": 1}

    def test_duplicate_imported_project_roots_share_stable_application_identity(self):
        jobs = [
            ParsedJob(
                name="SharedJob", path="process/SharedJob.item",
                version="1.0", source_root="zip-a/REFERENCES",
            ),
            ParsedJob(
                name="SharedJob", path="process/SharedJob.item",
                version="1.0", source_root="zip-b/REFERENCES",
            ),
        ]
        manifest = _manifest(source_roots=[
            {
                "id": "zip-a/REFERENCES", "path": "zip-a/REFERENCES",
                "label": "References", "role": "support",
                "project_id": "_stable-talend-project-id",
            },
            {
                "id": "zip-b/REFERENCES", "path": "zip-b/REFERENCES",
                "label": "References", "role": "support",
                "project_id": "_stable-talend-project-id",
            },
        ])
        landscape = _build(_scan(jobs=jobs, manifest=manifest))

        assert len(landscape.applications) == 1
        application = landscape.applications[0]
        assert application.project_id == "_stable-talend-project-id"
        assert application.role == "shared_project"
        assert application.source_roots == [
            "zip-a/REFERENCES", "zip-b/REFERENCES",
        ]
        assert application.artifact_count == 1


# ── Control-flow vs data edges ────────────────────────────────────────────


class TestEdges:
    def test_job_calls_job_is_direct_synchronous_control_flow(self):
        manifest = _manifest(
            job_source_roots={"Parent": "root/A", "Child": "root/B"},
            artifact_relations=[{
                "source": "Parent", "target": "Child",
                "relation_type": "job_calls_job", "resolution": "resolved", "provenance": "tRunJob",
            }],
        )
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        assert len(landscape.edges) == 1
        edge = landscape.edges[0]
        assert edge.dependency_type == "control_flow"
        assert edge.source == "root/A" and edge.target == "root/B"
        assert edge.source_kind == "application" and edge.target_kind == "application"
        assert edge.mechanism == "direct"
        assert edge.reliability == "synchronous"
        assert edge.confidence == "certain"
        assert edge.provenance  # never empty
        assert all(isinstance(record, LandscapeProvenance) for record in edge.provenance)
        assert {record.fact_type for record in edge.provenance} == {
            "artifact_relation_source", "artifact_relation_target",
        }
        assert {record.source_root for record in edge.provenance} == {"root/A", "root/B"}

    def test_route_invocation_through_jms_channel_is_buffered(self):
        manifest = _manifest(
            job_source_roots={"Route1": "root/A", "Job1": "root/B"},
            artifact_relations=[{
                "source": "Route1", "target": "Job1", "relation_type": "route_invokes_job",
                "resolution": "same_root", "provenance": "cTalendJob", "component_name": "cTalendJob_1",
            }],
            artifact_channels=[{
                "artifact": "Route1", "channel_type": "jms", "channel": "context.SomeQueue",
                "direction": "input", "component_name": "cTalendJob_1", "provenance": "cJMS",
            }],
        )
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        edge = next(e for e in landscape.edges if e.dependency_type == "control_flow")
        assert edge.mechanism == "jms"
        assert edge.reliability == "buffered"

    def test_same_root_call_is_not_an_l1_self_loop(self):
        manifest = _manifest(
            job_source_roots={"Parent": "root/A", "Child": "root/A"},
            artifact_relations=[{
                "source": "Parent", "target": "Child", "relation_type": "job_calls_job",
                "resolution": "resolved", "provenance": "tRunJob",
            }],
        )
        landscape = _build(_scan(manifest=manifest))
        assert landscape.edges == []
        assert landscape.unmapped == []

    def test_unresolved_relation_never_becomes_an_edge_but_is_diagnosed(self):
        manifest = _manifest(artifact_relations=[{
            "source": "Route1", "target": "", "target_name": "SomeMissingJob",
            "relation_type": "route_invokes_job", "resolution": "unresolved", "provenance": "cTalendJob",
        }])
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        assert landscape.edges == []
        unresolved = [d for d in landscape.diagnostics if d.kind == "unresolved_reference"]
        assert len(unresolved) == 1
        assert "SomeMissingJob" in unresolved[0].description
        assert "Route1" in unresolved[0].evidence

    def test_resource_read_write_becomes_data_edge_to_system(self):
        conns = [MetadataConnection(
            xmi_id="id1", name="RHMDBConnection", connection_type="Microsoft SQL Server",
            host="[HOST_REDACTED]", database="context.RHMDB_Database",
        )]
        manifest = _manifest(
            job_source_roots={"J1": "root/A"},
            artifact_resources=[{
                "artifact": "J1", "resource_type": "repository_connection", "resource": "RHMDBConnection",
                "direction": "output", "component_name": "tMSSqlOutput_1", "provenance": "tMSSqlOutput",
            }],
        )
        scan_result = _scan(metadata_connections=conns, manifest=manifest)
        landscape = _build(scan_result)
        data_edges = [e for e in landscape.edges if e.dependency_type == "data"]
        assert len(data_edges) == 1
        edge = data_edges[0]
        assert edge.source == "root/A"
        assert edge.source_kind == "application"
        assert edge.target_kind == "system"
        assert edge.mechanism == "db"
        assert edge.direction == "out"
        assert edge.confidence == "certain"
        assert edge.provenance

    def test_unresolvable_resource_reference_is_recorded_not_dropped(self):
        manifest = _manifest(
            job_source_roots={"J1": "root/A"},
            artifact_resources=[{
                "artifact": "J1", "resource_type": "repository_connection", "resource": "UnknownConn",
                "direction": "input", "component_name": "c1", "provenance": "tMSSqlInput",
            }],
        )
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        assert landscape.edges == []
        assert len(landscape.unmapped) == 1
        assert landscape.unmapped[0]["resource"]["resource"] == "UnknownConn"

    def test_inferred_lineage_cross_job_entry_becomes_data_edge(self):
        manifest = _manifest(job_source_roots={"ParentJob": "root/A", "ChildJob": "root/B"})
        scan_result = _scan(manifest=manifest)
        lineage_matrix = LineageMatrix(entries=[LineageEntry(
            source_table="Orders", source_column="", target_table="Orders", target_column="",
            job_name="ChildJob", source_job_name="ParentJob",
            confidence=LineageConfidence.INFERRED, resolved=True,
        )])
        landscape = _build(scan_result, lineage_matrix)
        data_edges = [e for e in landscape.edges if e.dependency_type == "data"]
        assert len(data_edges) == 1
        edge = data_edges[0]
        assert edge.source == "root/A" and edge.target == "root/B"
        assert edge.confidence == "inferred"
        assert edge.provenance

    def test_every_edge_carries_provenance(self):
        """Cross-cutting invariant: an edge with empty provenance is a
        builder bug, never emitted intentionally."""
        manifest = _manifest(
            job_source_roots={"P": "root/A", "C": "root/B", "J1": "root/A"},
            artifact_relations=[{
                "source": "P", "target": "C", "relation_type": "job_calls_job",
                "resolution": "resolved", "provenance": "tRunJob",
            }],
            artifact_resources=[{
                "artifact": "J1", "resource_type": "repository_connection", "resource": "Conn1",
                "direction": "input", "component_name": "c1", "provenance": "tMSSqlInput",
            }],
        )
        conns = [MetadataConnection(
            xmi_id="id1", name="Conn1", connection_type="Microsoft SQL Server",
            host="[HOST_REDACTED]", database="context.db1",
        )]
        scan_result = _scan(metadata_connections=conns, manifest=manifest)
        landscape = _build(scan_result)
        assert landscape.edges
        assert all(e.provenance for e in landscape.edges)

    def test_non_repository_resource_is_likely_integration_not_data(self):
        manifest = _manifest(
            job_source_roots={"J1": "root/A"},
            artifact_resources=[{
                "artifact": "J1", "resource_type": "connector_action", "resource": "Salesforce",
                "direction": "output", "component_name": "upsert_contact", "provenance": "workato_action",
            }],
        )
        landscape = _build(_scan(manifest=manifest))
        assert len(landscape.edges) == 1
        edge = landscape.edges[0]
        assert edge.dependency_type == "integration"
        assert edge.confidence == "likely"
        assert edge.provenance[0].fact_type == "artifact_resource"

    def test_generic_transport_fallback_does_not_create_integration_edges(self):
        manifest = _manifest(
            job_source_roots={"A": "root/A", "B": "root/B"},
            job_external_systems={"A": ["REST API"], "B": ["REST API"]},
        )
        landscape = _build(_scan(manifest=manifest))
        assert landscape.systems == []
        assert landscape.edges == []

    def test_concrete_resource_subsumes_matching_fallback_participation(self):
        manifest = _manifest(
            job_source_roots={"J1": "root/A"},
            job_external_systems={"J1": ["Salesforce"]},
            artifact_resources=[{
                "artifact": "J1", "resource_type": "connector_action", "resource": "Salesforce",
                "direction": "output", "component_name": "upsert", "provenance": "workato_action",
            }],
        )
        landscape = _build(_scan(manifest=manifest))
        assert len(landscape.edges) == 1
        assert landscape.edges[0].provenance[0].fact_type == "artifact_resource"


# ── Diagnostics ──────────────────────────────────────────────────────────


class TestDiagnostics:
    def test_write_only_channel_is_flagged_unread(self):
        manifest = _manifest(artifact_channels=[{
            "artifact": "Route1", "channel_type": "jms", "channel": "context.SomeQueue",
            "direction": "output", "component_name": "cJMS_1", "provenance": "cJMS",
        }])
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        unread = [d for d in landscape.diagnostics if d.kind == "unread_channel"]
        assert len(unread) == 1
        assert unread[0].severity == "info"
        assert "Route1" in unread[0].evidence

    def test_dead_letter_named_write_only_channel_is_higher_severity(self):
        manifest = _manifest(artifact_channels=[{
            "artifact": "Route1", "channel_type": "jms",
            "channel": '"DLQ." + context.ActiveMQ_ErrorReport',
            "direction": "output", "component_name": "cJMS_1", "provenance": "cJMS",
        }])
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        unread = [d for d in landscape.diagnostics if d.kind == "unread_channel"]
        assert len(unread) == 1
        assert unread[0].severity == "warning"

    def test_channel_with_both_writer_and_reader_is_not_flagged(self):
        manifest = _manifest(artifact_channels=[
            {
                "artifact": "Route1", "channel_type": "jms", "channel": "context.SomeQueue",
                "direction": "output", "component_name": "cJMS_1", "provenance": "cJMS",
            },
            {
                "artifact": "Route2", "channel_type": "jms", "channel": "context.SomeQueue",
                "direction": "input", "component_name": "cJMS_2", "provenance": "cJMS",
            },
        ])
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        assert [d for d in landscape.diagnostics if d.kind == "unread_channel"] == []

    def test_placeholder_channel_value_is_not_flagged(self):
        """'configured channel' carries no distinguishing identity to
        correlate readers on -- never a false-positive diagnostic."""
        manifest = _manifest(artifact_channels=[{
            "artifact": "Route1", "channel_type": "direct", "channel": "configured channel",
            "direction": "output", "component_name": "cDirect_1", "provenance": "cDirect",
        }])
        scan_result = _scan(manifest=manifest)
        landscape = _build(scan_result)
        assert [d for d in landscape.diagnostics if d.kind == "unread_channel"] == []
        assert landscape.channels == []
        assert [edge for edge in landscape.edges if edge.dependency_type == "integration"] == []

    def test_exact_channel_producer_consumer_creates_typed_integration_edge(self):
        manifest = _manifest(
            job_source_roots={"Producer": "root/A", "Consumer": "root/B"},
            artifact_channels=[
                {
                    "artifact": "Producer", "channel_type": "jms", "channel": "orders.queue",
                    "direction": "output", "component_name": "send", "provenance": "cJMS",
                },
                {
                    "artifact": "Consumer", "channel_type": "jms", "channel": "orders.queue",
                    "direction": "input", "component_name": "receive", "provenance": "cJMS",
                },
            ],
        )
        landscape = _build(_scan(manifest=manifest))
        assert len(landscape.channels) == 2
        edge = next(edge for edge in landscape.edges if edge.dependency_type == "integration")
        assert (edge.source, edge.target, edge.mechanism) == ("root/A", "root/B", "jms")
        assert {record.fact_type for record in edge.provenance} == {
            "artifact_channel_producer", "artifact_channel_consumer",
        }

    def test_same_root_channel_is_retained_for_l2_without_l1_self_loop(self):
        manifest = _manifest(
            job_source_roots={"Producer": "root/A", "Consumer": "root/A"},
            artifact_channels=[
                {
                    "artifact": "Producer", "channel_type": "direct", "channel": "internal.flow",
                    "direction": "output", "component_name": "send", "provenance": "cDirect",
                },
                {
                    "artifact": "Consumer", "channel_type": "direct", "channel": "internal.flow",
                    "direction": "input", "component_name": "receive", "provenance": "cDirect",
                },
            ],
        )
        landscape = _build(_scan(manifest=manifest))
        assert len(landscape.channels) == 2
        assert [edge for edge in landscape.edges if edge.dependency_type == "integration"] == []

    def test_near_name_or_case_mismatch_does_not_create_channel_edge(self):
        manifest = _manifest(
            job_source_roots={"Producer": "root/A", "Consumer": "root/B"},
            artifact_channels=[
                {
                    "artifact": "Producer", "channel_type": "jms", "channel": "orders.queue",
                    "direction": "output", "component_name": "send", "provenance": "cJMS",
                },
                {
                    "artifact": "Consumer", "channel_type": "jms", "channel": "Orders.Queue",
                    "direction": "input", "component_name": "receive", "provenance": "cJMS",
                },
            ],
        )
        landscape = _build(_scan(manifest=manifest))
        assert len(landscape.channels) == 2
        assert [edge for edge in landscape.edges if edge.dependency_type == "integration"] == []
        assert len([diagnostic for diagnostic in landscape.diagnostics if diagnostic.kind == "unread_channel"]) == 1


class TestL2Evidence:
    def test_endpoint_becomes_interface_without_inventing_peer_edge(self):
        manifest = _manifest(
            job_source_roots={"Api": "root/A"},
            artifact_endpoints=[{
                "artifact": "Api", "endpoint_type": "rest", "direction": "input",
                "component_name": "cREST_1", "provenance": "cREST",
            }],
        )
        landscape = _build(_scan(manifest=manifest))
        assert len(landscape.interfaces) == 1
        interface = landscape.interfaces[0]
        assert interface.qualified_artifact_id == "root/A::Api"
        assert interface.endpoint_type == "rest"
        assert landscape.edges == []

    def test_only_promoted_shared_contexts_become_config_edges(self):
        manifest = _manifest(
            job_source_roots={"A": "root/A", "B": "root/B"},
            context_metadata={
                "maximo_a": {"name": "MaximoContext", "source_root": "root/A"},
                "maximo_b": {"name": "Maximo", "source_root": "root/B"},
                "local": {"name": "LocalConfig", "source_root": "root/A"},
            },
            context_used_by={"maximo_a": ["A"], "maximo_b": ["B"], "local": ["A"]},
        )
        landscape = _build(_scan(manifest=manifest))
        config_edges = [edge for edge in landscape.edges if edge.dependency_type == "config"]
        assert len(config_edges) == 2
        assert {edge.target for edge in config_edges} == {"system:context:maximo"}
        assert {record.fact_type for edge in config_edges for record in edge.provenance} == {"context_reference"}


# ── Credential safety ──────────────────────────────────────────────────


class TestCredentialSafety:
    def test_connection_string_shaped_name_is_redacted_in_system_output(self):
        conns = [MetadataConnection(
            xmi_id="id1",
            name='jdbc:sqlserver://admin:hunter2fake@myserver.example.com:1433/mydbConnection',
            connection_type="Microsoft SQL Server",
            host="[HOST_REDACTED]", database="context.db1",
        )]
        scan_result = _scan(metadata_connections=conns)
        landscape = _build(scan_result, cred_filter=CredentialFilter())
        dumped = landscape.model_dump_json()
        assert "hunter2fake" not in dumped

    def test_no_raw_host_or_secret_leaves_landscape_output(self):
        """Full-shape sanity sweep: nothing in the serialized landscape
        should ever carry a raw host/credential -- only names/kinds."""
        conns = [MetadataConnection(
            xmi_id="id1", name="MaximoDBConnection", connection_type="Oracle with service name",
            host="[HOST_REDACTED]", database="context.maximo_sid",
        )]
        manifest = _manifest(
            job_source_roots={"J1": "root/A"},
            artifact_resources=[{
                "artifact": "J1", "resource_type": "repository_connection", "resource": "MaximoDBConnection",
                "direction": "input", "component_name": "c1", "provenance": "tOracleInput",
            }],
        )
        scan_result = _scan(metadata_connections=conns, manifest=manifest)
        landscape = _build(scan_result)
        dumped = landscape.model_dump_json()
        assert "hunter2" not in dumped
        assert "[HOST_REDACTED]" not in dumped  # host itself was never carried into the graph
