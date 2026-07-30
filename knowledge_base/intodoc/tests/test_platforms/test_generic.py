"""Tests for the Generic platform plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from intodoc.models import FileType, ScanResult
from intodoc.platforms.generic import GenericPlugin
from intodoc.platforms.registry import get_platform, list_platforms
from intodoc.scanner.credential_filter import CredentialFilter


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "generic_sample"


# ── Registry ────────────────────────────────────────────────────────


def test_generic_in_registry():
    """Generic should be a registered platform."""
    assert "generic" in list_platforms()


def test_get_generic_plugin():
    """get_platform('generic') should return GenericPlugin."""
    plugin = get_platform("generic")
    assert isinstance(plugin, GenericPlugin)
    assert plugin.name == "generic"
    assert plugin.display_name == "Generic Integration Project"


# ── File Classification ─────────────────────────────────────────────


class TestClassifyFile:
    @pytest.fixture
    def plugin(self):
        return GenericPlugin()

    @pytest.mark.parametrize("ext", [
        ".py", ".java", ".js", ".ts", ".go", ".rb", ".sh", ".groovy",
        ".cs", ".php", ".cpp", ".swift", ".rs",
    ])
    def test_code_files_are_routine(self, plugin, tmp_path, ext):
        f = tmp_path / f"main{ext}"
        f.write_text("hello world\n")
        assert plugin.classify_file(f) == FileType.ROUTINE

    def test_unknown_text_extension_is_routine(self, plugin, tmp_path):
        """Any readable text file with an unknown extension is scanned as ROUTINE."""
        f = tmp_path / "source.zig"
        f.write_text("const std = @import(\"std\");\n")
        assert plugin.classify_file(f) == FileType.ROUTINE

    @pytest.mark.parametrize("ext,expected", [
        (".sql", FileType.SQL_PATTERN),
        (".ddl", FileType.SQL_PATTERN),
    ])
    def test_sql_files(self, plugin, ext, expected):
        assert plugin.classify_file(Path(f"scripts/query{ext}")) == expected

    @pytest.mark.parametrize("ext,expected", [
        (".json", FileType.CONFIG),
        (".yaml", FileType.CONFIG),
        (".yml", FileType.CONFIG),
        (".xml", FileType.CONFIG),
        (".toml", FileType.CONFIG),
    ])
    def test_config_files(self, plugin, ext, expected):
        assert plugin.classify_file(Path(f"config/app{ext}")) == expected

    @pytest.mark.parametrize("ext,expected", [
        (".env", FileType.CONTEXT),
        (".properties", FileType.CONTEXT),
        (".cfg", FileType.CONTEXT),
        (".ini", FileType.CONTEXT),
    ])
    def test_context_files(self, plugin, ext, expected):
        assert plugin.classify_file(Path(f"env/prod{ext}")) == expected

    @pytest.mark.parametrize("ext,expected", [
        (".csv", FileType.SCHEMA),
        (".xsd", FileType.SCHEMA),
        (".proto", FileType.SCHEMA),
    ])
    def test_schema_files(self, plugin, ext, expected):
        assert plugin.classify_file(Path(f"data/schema{ext}")) == expected

    @pytest.mark.parametrize("ext,expected", [
        (".md", FileType.DOCUMENTATION),
        (".rst", FileType.DOCUMENTATION),
        (".txt", FileType.DOCUMENTATION),
    ])
    def test_doc_files(self, plugin, ext, expected):
        assert plugin.classify_file(Path(f"docs/readme{ext}")) == expected

    @pytest.mark.parametrize("ext", [
        ".pyc", ".class", ".jar", ".exe", ".dll", ".png", ".jpg", ".zip",
    ])
    def test_skip_binary(self, plugin, tmp_path, ext):
        f = tmp_path / f"file{ext}"
        f.write_bytes(b"\x00\x01\x02\x03binary content here")
        assert plugin.classify_file(f) == FileType.UNKNOWN

    def test_skip_dirs(self, plugin):
        assert plugin.classify_file(Path("node_modules/pkg/index.js")) == FileType.UNKNOWN
        assert plugin.classify_file(Path("__pycache__/mod.py")) == FileType.UNKNOWN
        assert plugin.classify_file(Path(".venv/lib/site.py")) == FileType.UNKNOWN


# ── Parsing ──────────────────────────────────────────────────────────


class TestGenericParser:
    @pytest.fixture
    def cred_filter(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return CredentialFilter()

    def test_parse_fixture(self, cred_filter):
        """Parse the generic_sample fixture and verify results."""
        if not FIXTURES_DIR.exists():
            pytest.skip("generic_sample fixture not found")

        plugin = GenericPlugin()
        result = plugin.parse_project(FIXTURES_DIR, cred_filter)

        assert isinstance(result, ScanResult)
        assert result.platform == "generic"
        assert len(result.jobs) > 0
        assert len(result.files) > 0

    def test_parse_creates_jobs_from_directories(self, cred_filter):
        """Jobs should be created from directory groupings."""
        if not FIXTURES_DIR.exists():
            pytest.skip("generic_sample fixture not found")

        plugin = GenericPlugin()
        result = plugin.parse_project(FIXTURES_DIR, cred_filter)

        # Should have jobs for src, config, sql sub-groups
        job_groups = {j.group for j in result.jobs}
        assert len(job_groups) > 0

    def test_parse_extracts_context(self, cred_filter, tmp_path):
        """Context variables should be extracted from .properties files."""
        # .env files are hidden (start with .) and get skipped by walker.
        # Use a .properties file instead.
        props_dir = tmp_path / "config"
        props_dir.mkdir()
        (props_dir / "app.properties").write_text("DB_HOST=localhost\nDB_PORT=5432\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')\n")

        plugin = GenericPlugin()
        result = plugin.parse_project(tmp_path, cred_filter)

        assert len(result.context_groups) > 0

    def test_parse_detects_connections(self, cred_filter):
        """Connections should be detected from config files."""
        if not FIXTURES_DIR.exists():
            pytest.skip("generic_sample fixture not found")

        plugin = GenericPlugin()
        result = plugin.parse_project(FIXTURES_DIR, cred_filter)

        # database.json has host/port/database keys
        assert len(result.metadata_connections) > 0

    def test_parse_detects_config_xml_connections(self, cred_filter, tmp_path):
        """Web.config/App.config style files should be treated as XML connection sources."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')\n")
        (tmp_path / "Web.config").write_text(
            """<configuration>
  <connectionStrings>
    <add name="MainDb" connectionString="Server=sql01;Database=Main;Password=[INLINE_CREDENTIAL]" providerName="System.Data.SqlClient" />
  </connectionStrings>
</configuration>
""",
            encoding="utf-8",
        )

        plugin = GenericPlugin()
        result = plugin.parse_project(tmp_path, cred_filter)

        assert any(conn.name == "MainDb" and conn.connection_type == "SQL Server" for conn in result.metadata_connections)

    def test_parse_detects_all_config_xml_connections_and_appsettings(self, cred_filter, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')\n")
        (tmp_path / "Web.config").write_text(
            """<configuration>
  <appSettings>
    <add key="ClientSecret" value="plaintext-secret" />
    <add key="FeatureFlag" value="enabled" />
  </appSettings>
  <connectionStrings>
    <add name="MainDb" connectionString="Server=sql01,1433;Database=Main;User ID=app;Password=secret" providerName="System.Data.SqlClient" />
    <add name="AuditDb" connectionString="Data Source=sql02;Initial Catalog=Audit;Pwd=secret" providerName="System.Data.SqlClient" />
  </connectionStrings>
</configuration>
""",
            encoding="utf-8",
        )

        plugin = GenericPlugin()
        result = plugin.parse_project(tmp_path, cred_filter)

        assert {conn.name for conn in result.metadata_connections} >= {"MainDb", "AuditDb"}
        main = next(conn for conn in result.metadata_connections if conn.name == "MainDb")
        assert main.host == "sql01"
        assert main.port == "1433"
        assert main.database == "Main"
        assert main.additional_params["password"] == "[REDACTED]"
        ctx = next(group for group in result.context_groups if group.name == "Web.config_appSettings")
        values = {var.name: var.values["default"] for var in ctx.variables}
        assert values["ClientSecret"] == "[REDACTED]"
        assert values["FeatureFlag"] == "enabled"
        web_comp = next(
            comp for job in result.jobs for comp in job.components
            if comp.compact_params().get("file_path") == "Web.config"
        )
        assert web_comp.metadata_connection_ref == "MainDb,AuditDb"

    def test_parse_extracts_routines(self, cred_filter):
        """Code files should be extracted as routines."""
        if not FIXTURES_DIR.exists():
            pytest.skip("generic_sample fixture not found")

        plugin = GenericPlugin()
        result = plugin.parse_project(FIXTURES_DIR, cred_filter)

        assert len(result.routines) > 0

    def test_parse_extracts_sql_server_ddl_schema_definitions(self, cred_filter, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')\n")
        (tmp_path / "ddl").mkdir()
        (tmp_path / "ddl" / "tables.sql").write_text(
            """
CREATE TABLE [dbo].[SALESTABLE](
    [SALESID] [nvarchar](20) NOT NULL,
    [CUSTACCOUNT] [nvarchar](20) NOT NULL,
    [AMOUNT] [numeric](32, 16) NULL,
    [RECID] [bigint] IDENTITY(1,1) NOT NULL,
    CONSTRAINT [PK_SALESTABLE] PRIMARY KEY CLUSTERED ([RECID] ASC)
)
GO
ALTER TABLE [dbo].[SALESTABLE] ADD DEFAULT ('') FOR [CUSTACCOUNT]
GO
CREATE NONCLUSTERED INDEX [I_SALESTABLE_CUST] ON [dbo].[SALESTABLE] ([CUSTACCOUNT] ASC)
""",
            encoding="utf-8",
        )

        plugin = GenericPlugin()
        result = plugin.parse_project(tmp_path, cred_filter)

        assert result.stats["schema_definitions"] == 1
        table = result.schema_definitions[0]
        assert table["name"] == "dbo.SALESTABLE"
        columns = {col["name"]: col for col in table["columns"]}
        assert columns["CUSTACCOUNT"]["data_type"] == "nvarchar(20)"
        assert columns["CUSTACCOUNT"]["max_length"] == "20"
        assert columns["CUSTACCOUNT"]["nullable"] is False
        assert columns["CUSTACCOUNT"]["default"] == "('')"
        assert columns["AMOUNT"]["precision"] == "32"
        assert columns["AMOUNT"]["scale"] == "16"
        assert columns["RECID"]["identity"] == {"seed": "1", "increment": "1"}
        assert any(c["kind"] == "primary_key" and c["columns"] == ["RECID"] for c in table["constraints"])
        assert any(i["name"] == "I_SALESTABLE_CUST" for i in table["indexes"])

    def test_parse_deduplicates_duplicate_ddl_sources(self, cred_filter, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')\n")
        ddl = "CREATE TABLE [dbo].[Products]([Id] [int] NOT NULL)\n"
        (tmp_path / "a.sql").write_text(ddl, encoding="utf-8")
        (tmp_path / "b.sql").write_text(ddl, encoding="utf-8")

        plugin = GenericPlugin()
        result = plugin.parse_project(tmp_path, cred_filter)

        assert len(result.schema_definitions) == 1
        assert result.schema_definitions[0]["name"] == "dbo.Products"
        assert result.schema_definitions[0]["duplicate_sources"] == ["b.sql"]

    def test_parse_extracts_utf16_sql_server_ddl_schema_definitions(self, cred_filter, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')\n")
        (tmp_path / "axorderservice-prd.sql").write_text(
            """
CREATE TABLE [dbo].[OrderHeaders](
    [Id] [int] IDENTITY(1,1) NOT NULL,
    [CustomerNumber] [nvarchar](20) NOT NULL,
    CONSTRAINT [PK_OrderHeaders] PRIMARY KEY CLUSTERED ([Id] ASC)
)
GO
CREATE TABLE [dbo].[OrderLines](
    [Id] [int] IDENTITY(1,1) NOT NULL,
    [OrderId] [int] NOT NULL,
    [ProductNumber] [nvarchar](30) NOT NULL,
    CONSTRAINT [PK_OrderLines] PRIMARY KEY CLUSTERED ([Id] ASC),
    CONSTRAINT [FK_OrderLines_OrderHeaders_OrderId] FOREIGN KEY([OrderId])
        REFERENCES [dbo].[OrderHeaders] ([Id])
)
""",
            encoding="utf-16",
        )

        plugin = GenericPlugin()
        result = plugin.parse_project(tmp_path, cred_filter)

        names = {schema["name"] for schema in result.schema_definitions}
        assert names == {"dbo.OrderHeaders", "dbo.OrderLines"}
        order_lines = next(schema for schema in result.schema_definitions if schema["name"] == "dbo.OrderLines")
        columns = {col["name"]: col for col in order_lines["columns"]}
        assert columns["ProductNumber"]["data_type"] == "nvarchar(30)"
        assert any(c["kind"] == "foreign_key" for c in order_lines["constraints"])

    def test_manifest_has_job_groups(self, cred_filter):
        """Manifest should contain job groups."""
        if not FIXTURES_DIR.exists():
            pytest.skip("generic_sample fixture not found")

        plugin = GenericPlugin()
        result = plugin.parse_project(FIXTURES_DIR, cred_filter)

        assert len(result.manifest.job_groups) > 0


# ── Universal extraction unit tests ─────────────────────────────────


def test_go_block_imports_extracted():
    """Block-style Go imports should be extracted by the two-pass importer."""
    from intodoc.platforms.generic.parser import GenericParser

    go_content = '''\
package main

import (
\t"fmt"
\t"database/sql"
\t_ "github.com/lib/pq"
\talias "net/http"
)

func main() {}
'''
    imports = GenericParser._extract_imports_universal(go_content)
    pkg_paths = [i.replace('import "', '').rstrip('"') for i in imports if i.startswith('import "')]
    assert "fmt" in pkg_paths
    assert "database/sql" in pkg_paths
    assert "github.com/lib/pq" in pkg_paths
    assert "net/http" in pkg_paths


def test_c_function_names_extracted():
    """C-style functions (no keyword prefix) should be found by the brace fallback."""
    from intodoc.platforms.generic.parser import GenericParser

    c_content = '''\
#include <stdio.h>

int processPayment(int amount) {
    return amount * 2;
}

void handleError(const char* msg) {
    printf("%s\\n", msg);
}

static bool validateInput(int x) {
    return x > 0;
}
'''
    funcs = GenericParser._extract_function_names_universal(c_content)
    assert "processPayment" in funcs
    assert "handleError" in funcs
    assert "validateInput" in funcs
    # Control-flow keywords must not appear
    assert "if" not in funcs
    assert "for" not in funcs


# ── Topology facts (artifact_kinds / relations / endpoints / resources) ──


class TestGenericTopologyFacts:
    """Generic-scanner population of the cross-platform manifest topology
    fields Talend already populates: artifact_kinds/relations/endpoints/
    resources. Uses a small synthetic project so every signal (entry point,
    resolved import call, HTTP route, Web.config connection, external-system
    import) is deliberate and traceable to a specific assertion."""

    @pytest.fixture
    def cred_filter(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return CredentialFilter()

    @pytest.fixture
    def scan_result(self, cred_filter, tmp_path) -> ScanResult:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text(
            "from .helper import do_thing\n\n"
            "@app.get(\"/api/orders\")\n"
            "def list_orders():\n"
            "    return do_thing()\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "helper.py").write_text(
            "import boto3\n\n"
            "def do_thing():\n"
            "    return boto3.client('s3')\n",
            encoding="utf-8",
        )
        (tmp_path / "Web.config").write_text(
            """<configuration>
  <connectionStrings>
    <add name="MainDb" connectionString="Server=sql01;Database=Main;Password=secret" providerName="System.Data.SqlClient" />
  </connectionStrings>
</configuration>
""",
            encoding="utf-8",
        )

        plugin = GenericPlugin()
        return plugin.parse_project(tmp_path, cred_filter)

    def test_entry_point_and_module_kinds(self, scan_result):
        """Entry-point files and directory-grouped files get distinct,
        structurally-derived kinds (never a fabricated Talend job/route
        label)."""
        kinds = scan_result.manifest.artifact_kinds
        assert kinds["main"] == "entry_point"
        assert kinds["helper"] == "module"
        assert kinds["Web"] == "module"

    def test_resolved_import_becomes_relation(self, scan_result):
        """main.py's relative import of helper.py is a resolvable call --
        mirrors Talend's tRunJob shape (job_calls_job/resolved)."""
        assert {
            "source": "main", "target": "helper",
            "relation_type": "job_calls_job", "resolution": "resolved",
            "provenance": "import_reference",
        } in scan_result.manifest.artifact_relations

    def test_http_route_becomes_inbound_endpoint(self, scan_result):
        """A regex-detected route decorator is a real inbound HTTP surface;
        the literal path is not disclosed, same redaction posture as
        Talend's route endpoint records."""
        endpoints = [
            e for e in scan_result.manifest.artifact_endpoints
            if e["artifact"] == "main"
        ]
        assert len(endpoints) == 1
        assert endpoints[0]["endpoint_type"] == "http_route"
        assert endpoints[0]["direction"] == "input"
        for endpoint in scan_result.manifest.artifact_endpoints:
            assert "/api/orders" not in str(endpoint.values())

    def test_config_connection_becomes_resource(self, scan_result):
        """The Web.config connection string is attributed as a resource to
        the component that actually carries the file_path match."""
        resources = [
            r for r in scan_result.manifest.artifact_resources
            if r["artifact"] == "Web"
        ]
        assert len(resources) == 1
        assert resources[0]["resource"] == "MainDb"
        assert resources[0]["resource_type"] == "SQL Server"
        assert resources[0]["direction"] == "unknown"

    def test_import_detected_external_system_becomes_resource(self, scan_result):
        """boto3 usage is a real, already-parsed signal -- surfaced as an
        external_system resource, direction 'unknown' since Generic has no
        input/output naming convention to key off."""
        resources = [
            r for r in scan_result.manifest.artifact_resources
            if r["artifact"] == "helper" and r["resource_type"] == "external_system"
        ]
        assert len(resources) == 1
        assert resources[0]["resource"] == "AWS"
        assert resources[0]["direction"] == "unknown"

    def test_duplicate_display_names_get_distinct_source_relative_ids(self, tmp_path):
        for folder in ("service_a", "service_b"):
            target = tmp_path / folder
            target.mkdir()
            (target / "main.py").write_text(
                "def main():\n    return 1\n", encoding="utf-8",
            )

        result = GenericPlugin().parse_project(tmp_path, CredentialFilter())

        main_jobs = [job for job in result.jobs if job.name == "main"]
        assert len(main_jobs) == 2
        assert {job.job_id for job in main_jobs} == {
            ".::service_a/main.py", ".::service_b/main.py",
        }
        assert set(result.manifest.artifact_kinds) == {
            ".::service_a/main.py", ".::service_b/main.py",
        }
        assert set(result.manifest.job_source_roots) == set(result.manifest.artifact_kinds)


# ── Best Practices ───────────────────────────────────────────────────


def test_best_practices_loads():
    """best_practices.yaml should load successfully."""
    plugin = GenericPlugin()
    bp = plugin.get_best_practices()
    assert bp["platform_name"] == "Generic Integration Project"
    assert "concepts" in bp
    assert "anti_patterns" in bp
