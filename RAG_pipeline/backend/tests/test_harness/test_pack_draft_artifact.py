"""Structural validation of a chat-authored plugin.yaml draft.

The security-relevant direction is one-way: local validation may be more
permissive than MCP's loader, but it must never ACCEPT something MCP would
reject on authority grounds. The foundation-key tests below pin that.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.harness.packs.draft_artifact import (
    FOUNDATION_ONLY_KEYS,
    PackDraftArtifact,
    PackDraftInvalid,
)


VALID = """\
name: sql-injection-review
version: 0.3.0
description: Find every place user input reaches a query.
lens:
  focus:
    include: [sql_queries, orm_entities]
    exclude: [binary]
  sections:
    mode: replace
    declare:
      - id: INJECTION_SINKS
        question: where does user data reach a query
audience:
  roles: [security reviewer]
  tone: neutral
  technical_depth: deep
docs:
  - name: INJECTION_FINDINGS
    phase2_sections: [INJECTION_SINKS]
"""


def _artifact(manifest: str = VALID, **kwargs) -> PackDraftArtifact:
    return PackDraftArtifact(manifest_yaml=manifest, **kwargs)


def test_a_complete_manifest_has_no_structural_errors():
    assert _artifact().structural_errors() == []


def test_derived_views_read_from_the_manifest_text():
    artifact = _artifact()
    assert artifact.name == "sql-injection-review"
    assert artifact.version == "0.3.0"
    assert artifact.doc_names == ("INJECTION_FINDINGS",)
    # roles is bounded free text and must NOT be coerced into a presentation enum
    assert artifact.audience_render() == {"tone": "neutral", "technical_depth": "deep"}


@pytest.mark.parametrize("key", sorted(FOUNDATION_ONLY_KEYS))
def test_every_foundation_only_key_is_refused(key):
    """The authority boundary. A user pack can never carry these."""
    manifest = VALID + f"{key}: whatever\n"
    errors = _artifact(manifest).structural_errors()
    assert any("server-owned field" in error and key in error for error in errors), (
        f"{key} was not refused: {errors}"
    )


def test_authority_violation_is_reported_before_mere_incompleteness():
    manifest = "layer: foundation\nname: x\n"
    errors = _artifact(manifest).structural_errors()
    assert "server-owned field" in errors[0]


def test_unparseable_yaml_reports_a_line_number_and_does_not_raise():
    artifact = _artifact("name: [unclosed\n")
    errors = artifact.structural_errors()
    assert len(errors) == 1
    assert "not valid YAML" in errors[0]
    # Derived views degrade instead of exploding, so a broken draft still stores.
    assert artifact.name == ""
    assert artifact.doc_names == ()
    assert artifact.audience_render() == {}


def test_non_mapping_manifest_is_rejected():
    assert "must be a YAML mapping" in _artifact("- a\n- b\n").structural_errors()[0]
    with pytest.raises(PackDraftInvalid):
        _artifact("- a\n").manifest()


def test_a_pack_must_declare_at_least_one_document():
    manifest = VALID.replace(
        "docs:\n  - name: INJECTION_FINDINGS\n    phase2_sections: [INJECTION_SINKS]\n", ""
    )
    assert any("at least one output document" in e for e in _artifact(manifest).structural_errors())


def test_document_names_must_be_upper_snake_and_unique():
    lower = VALID.replace("INJECTION_FINDINGS", "Injection_Findings")
    assert any("UPPER_SNAKE_CASE" in e for e in _artifact(lower).structural_errors())

    duplicated = VALID + "  - name: INJECTION_FINDINGS\n"
    assert any("duplicate document name" in e for e in _artifact(duplicated).structural_errors())


def test_document_count_is_not_artificially_bounded():
    docs = "docs:\n" + "".join(f"  - name: DOC_{i}\n" for i in range(100))
    manifest = VALID.split("docs:")[0] + docs
    assert _artifact(manifest).structural_errors() == []


def test_name_and_version_shape_is_enforced():
    assert any("kebab-case" in e for e in _artifact(VALID.replace("sql-injection-review", "SQL Review")).structural_errors())
    assert any("1.0.0" in e for e in _artifact(VALID.replace("version: 0.3.0", "version: v3")).structural_errors())


def test_thin_description_is_refused():
    manifest = VALID.replace(
        "description: Find every place user input reaches a query.", "description: docs"
    )
    assert any("one-line summary" in e for e in _artifact(manifest).structural_errors())


def test_absent_lens_is_legitimate_not_an_error():
    """A pack with no lens behaves as packs did before the block existed."""
    manifest = "name: plain\nversion: 1.0.0\ndescription: a plain pack with no lens at all\ndocs:\n  - name: REPORT\n"
    assert _artifact(manifest).structural_errors() == []


def test_malformed_lens_is_an_error():
    assert any("mode must be extend or replace" in e for e in _artifact(VALID.replace("mode: replace", "mode: obliterate")).structural_errors())


def test_declared_section_count_is_not_artificially_bounded():
    manifest = VALID.replace(
        "      - id: INJECTION_SINKS\n        question: where does user data reach a query\n",
        "".join(f"      - id: SECTION_{i}\n" for i in range(50)),
    )
    assert _artifact(manifest).structural_errors() == []


def test_file_paths_are_bounded_and_traversal_free():
    for bad in ("../escape.md", "prompts/../../etc/passwd.md", "/abs.md", "run.sh", "img.png"):
        with pytest.raises(ValidationError):
            _artifact(files={bad: "x"})


def test_file_count_is_not_artificially_bounded():
    artifact = _artifact(files={f"prompts/docs/D{i}.md": "x" for i in range(100)})
    assert len(artifact.files) == 100


def test_legal_pack_files_are_accepted():
    artifact = _artifact(files={"prompts/phase2.md": "body", "examples/output-style.md": "x"})
    assert set(artifact.files) == {"prompts/phase2.md", "examples/output-style.md"}


def test_artifact_is_frozen_and_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        PackDraftArtifact(manifest_yaml=VALID, schema_version=3)
