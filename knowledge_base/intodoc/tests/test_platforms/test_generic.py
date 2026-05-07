"""Tests for the Generic platform plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from intodoc.models import FileType, ScanResult
from intodoc.platforms.generic import GenericPlugin
from intodoc.platforms.registry import get_platform, list_platforms


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

    def test_parse_extracts_routines(self, cred_filter):
        """Code files should be extracted as routines."""
        if not FIXTURES_DIR.exists():
            pytest.skip("generic_sample fixture not found")

        plugin = GenericPlugin()
        result = plugin.parse_project(FIXTURES_DIR, cred_filter)

        assert len(result.routines) > 0

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


# ── Best Practices ───────────────────────────────────────────────────


def test_best_practices_loads():
    """best_practices.yaml should load successfully."""
    plugin = GenericPlugin()
    bp = plugin.get_best_practices()
    assert bp["platform_name"] == "Generic Integration Project"
    assert "concepts" in bp
    assert "anti_patterns" in bp
