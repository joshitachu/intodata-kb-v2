"""What a project's material compile is allowed to return, and to whom.

Three properties are load-bearing here and each has a test that fails loudly if
it regresses: the access boundary (naming a document the caller may not read is
a disclosure), the fail-soft contract (a manifest must never cost an answer),
and the cache key (an unchanged key promises an unchanged prompt prefix).
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.db.models.document import Document
from src.db.models.document_text import DocumentTextRow
from src.harness.guidance.material import MANIFEST_MAX_ENTRIES, MaterialKind
from src.harness.guidance.material_repository import (
    compile_project_material,
    load_material_texts,
)

PROJECT = "reverse_engineer_demo"
OTHER_PROJECT = "another_client"
#: The caller's full authorization group set: role names plus assigned project
#: ids, the same union ``_build_search_filter`` composes internally.
GROUPS = ["developer", PROJECT]


async def _document(
    db,
    *,
    filename: str,
    role: str,
    project_id: str | None = PROJECT,
    deleted: bool = False,
    text: str | None = None,
) -> Document:
    """Seed one document, optionally with its stored text."""

    document = Document(
        filename=filename,
        file_type=filename.rsplit(".", 1)[-1],
        document_role=role,
        project_id=project_id,
        deleted_at=(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC) if deleted else None),
    )
    db.add(document)
    await db.flush()
    if text is not None:
        db.add(
            DocumentTextRow(
                document_id=document.id,
                text=text,
                char_count=len(text),
                sha256="0" * 64,
            )
        )
        await db.flush()
    return document


def _failing_execute(session) -> None:
    """Make every query on ``session`` raise the error the module must absorb."""

    async def execute(*args: object, **kwargs: object) -> object:
        raise SQLAlchemyError("connection reset")

    session.execute = execute  # type: ignore[method-assign]


# -- scope ------------------------------------------------------------------


async def test_source_documents_are_never_listed(db_session) -> None:
    await _document(db_session, filename="talend-export.pdf", role="source", text="body")
    await _document(db_session, filename="house-policy.pdf", role="policy", text="body")

    material = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=GROUPS
    )

    assert [entry.filename for entry in material.entries] == ["house-policy.pdf"]
    assert material.of_kind(MaterialKind.EVIDENCE) == ()


async def test_deleted_documents_are_never_listed(db_session) -> None:
    await _document(db_session, filename="retired-policy.pdf", role="policy", deleted=True)
    await _document(db_session, filename="current-policy.pdf", role="policy")

    material = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=GROUPS
    )

    assert [entry.filename for entry in material.entries] == ["current-policy.pdf"]


async def test_documents_of_another_project_are_never_listed(db_session) -> None:
    await _document(
        db_session, filename="their-policy.pdf", role="policy", project_id=OTHER_PROJECT
    )
    await _document(db_session, filename="our-policy.pdf", role="policy")

    material = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=GROUPS
    )

    assert [entry.filename for entry in material.entries] == ["our-policy.pdf"]


async def test_generated_internal_artifacts_are_never_listed(db_session) -> None:
    await _document(
        db_session,
        filename=f"{PROJECT}/_internal/technical_analysis/job-a.md",
        role="reference",
    )
    await _document(db_session, filename="uploaded-reference.pdf", role="reference")

    material = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=GROUPS
    )

    assert [entry.filename for entry in material.entries] == ["uploaded-reference.pdf"]


async def test_no_project_binding_compiles_nothing(db_session) -> None:
    await _document(db_session, filename="house-policy.pdf", role="policy")

    material = await compile_project_material(db_session, project_id=None, user_groups=GROUPS)

    assert material.is_empty
    assert material.project_id is None


# -- access boundary --------------------------------------------------------


async def test_caller_without_the_project_group_sees_nothing(db_session) -> None:
    await _document(db_session, filename="house-policy.pdf", role="policy", text="body")

    material = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=["developer"]
    )

    assert material.is_empty
    # Not even the cache key leaks the fact that material exists.
    assert material.cache_key == ""


async def test_admin_group_bypasses_project_scoping(db_session) -> None:
    await _document(db_session, filename="house-policy.pdf", role="policy", text="body")

    material = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=["admin"]
    )

    assert [entry.filename for entry in material.entries] == ["house-policy.pdf"]


# -- materialization --------------------------------------------------------


async def test_document_without_a_text_row_is_not_materialized(db_session) -> None:
    await _document(db_session, filename="small-policy.pdf", role="policy", text="short body")
    await _document(db_session, filename="huge-policy.pdf", role="policy")

    material = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=GROUPS
    )
    by_name = {entry.filename: entry for entry in material.entries}

    assert by_name["small-policy.pdf"].materialized is True
    assert by_name["small-policy.pdf"].char_count == len("short body")
    assert by_name["huge-policy.pdf"].materialized is False
    assert by_name["huge-policy.pdf"].char_count == 0
    assert material.mountable == (by_name["small-policy.pdf"],)


# -- ordering and the entry cap ---------------------------------------------


async def test_constraints_sort_ahead_of_methods_and_references(db_session) -> None:
    # Inserted worst-first so insertion order cannot be what produces the result.
    await _document(db_session, filename="background.pdf", role="reference")
    await _document(db_session, filename="how-to.pdf", role="guidance")
    await _document(db_session, filename="report-shape.pdf", role="template")
    await _document(db_session, filename="house-standard.pdf", role="standard")
    await _document(db_session, filename="house-policy.pdf", role="policy")

    material = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=GROUPS
    )

    assert [entry.kind for entry in material.entries] == [
        MaterialKind.CONSTRAINT,
        MaterialKind.CONSTRAINT,
        MaterialKind.METHOD,
        MaterialKind.METHOD,
        MaterialKind.REFERENCE,
    ]
    # Within a kind, the document id is the tiebreaker, so the order is total.
    constraints = material.of_kind(MaterialKind.CONSTRAINT)
    assert [entry.document_id for entry in constraints] == sorted(
        entry.document_id for entry in constraints
    )


async def test_entries_beyond_the_cap_become_overflow_not_silence(db_session) -> None:
    for index in range(MANIFEST_MAX_ENTRIES + 3):
        await _document(db_session, filename=f"reference-{index:03d}.pdf", role="reference")
    await _document(db_session, filename="house-policy.pdf", role="policy")

    material = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=GROUPS
    )

    assert len(material.entries) == MANIFEST_MAX_ENTRIES
    assert material.overflow_count == 4
    # The constraint is the one entry that must survive the cap.
    assert material.entries[0].filename == "house-policy.pdf"


# -- cache key --------------------------------------------------------------


async def test_cache_key_is_stable_across_identical_compiles(db_session) -> None:
    await _document(db_session, filename="house-policy.pdf", role="policy", text="body")
    await _document(db_session, filename="background.pdf", role="reference")

    first = await compile_project_material(db_session, project_id=PROJECT, user_groups=GROUPS)
    second = await compile_project_material(db_session, project_id=PROJECT, user_groups=GROUPS)

    assert first.cache_key == second.cache_key
    assert first.cache_key != ""


async def test_cache_key_changes_when_a_document_changes(db_session) -> None:
    document = await _document(db_session, filename="house-policy.pdf", role="policy")
    before = await compile_project_material(db_session, project_id=PROJECT, user_groups=GROUPS)

    # Materializing the document changes the rendered line from "search it" to a
    # path, so the key has to move with it.
    db_session.add(
        DocumentTextRow(
            document_id=document.id, text="body", char_count=4, sha256="1" * 64
        )
    )
    await db_session.flush()
    after = await compile_project_material(db_session, project_id=PROJECT, user_groups=GROUPS)

    assert before.cache_key != after.cache_key


async def test_cache_key_changes_when_an_overflow_document_changes(db_session) -> None:
    for index in range(MANIFEST_MAX_ENTRIES + 1):
        await _document(db_session, filename=f"reference-{index:03d}.pdf", role="reference")
    before = await compile_project_material(db_session, project_id=PROJECT, user_groups=GROUPS)

    # A document past the cap still moves ``overflow_count``, which renders.
    await _document(db_session, filename="reference-999.pdf", role="reference")
    after = await compile_project_material(db_session, project_id=PROJECT, user_groups=GROUPS)

    assert before.overflow_count == 1
    assert after.overflow_count == 2
    assert before.cache_key != after.cache_key


async def test_cache_key_is_scoped_to_the_project(db_session) -> None:
    empty_here = await compile_project_material(
        db_session, project_id=PROJECT, user_groups=GROUPS
    )
    empty_there = await compile_project_material(
        db_session, project_id=OTHER_PROJECT, user_groups=["admin"]
    )

    assert empty_here.entries == () == empty_there.entries
    assert empty_here.cache_key != empty_there.cache_key


# -- fail-soft --------------------------------------------------------------


async def test_database_failure_returns_empty_material_rather_than_raising(
    db_session, caplog: pytest.LogCaptureFixture
) -> None:
    await _document(db_session, filename="house-policy.pdf", role="policy", text="body")
    _failing_execute(db_session)

    with caplog.at_level("ERROR"):
        material = await compile_project_material(
            db_session, project_id=PROJECT, user_groups=GROUPS
        )

    assert material.is_empty
    assert material.project_id == PROJECT
    assert material.cache_key == ""
    assert "Project material compile failed" in caplog.text


# -- text loading -----------------------------------------------------------


async def test_load_material_texts_returns_only_the_requested_ids(db_session) -> None:
    wanted = await _document(
        db_session, filename="house-policy.pdf", role="policy", text="policy body"
    )
    other = await _document(
        db_session, filename="background.pdf", role="reference", text="reference body"
    )
    missing = await _document(db_session, filename="huge-policy.pdf", role="policy")

    texts = await load_material_texts(db_session, [wanted.id, missing.id])

    assert texts == {wanted.id: "policy body"}
    assert other.id not in texts


async def test_load_material_texts_short_circuits_on_an_empty_id_list(db_session) -> None:
    await _document(db_session, filename="house-policy.pdf", role="policy", text="body")
    # No query is issued at all, so a broken session cannot matter.
    _failing_execute(db_session)

    assert await load_material_texts(db_session, []) == {}


async def test_load_material_texts_caps_the_number_of_ids(db_session) -> None:
    documents = [
        await _document(
            db_session, filename=f"reference-{index:03d}.pdf", role="reference", text=f"body {index}"
        )
        for index in range(MANIFEST_MAX_ENTRIES + 5)
    ]

    texts = await load_material_texts(db_session, [document.id for document in documents])

    assert len(texts) == MANIFEST_MAX_ENTRIES
    assert set(texts) == {document.id for document in documents[:MANIFEST_MAX_ENTRIES]}


async def test_load_material_texts_returns_empty_on_a_database_failure(
    db_session, caplog: pytest.LogCaptureFixture
) -> None:
    document = await _document(
        db_session, filename="house-policy.pdf", role="policy", text="body"
    )
    _failing_execute(db_session)

    with caplog.at_level("ERROR"):
        texts = await load_material_texts(db_session, [document.id])

    assert texts == {}
    assert "Loading material texts failed" in caplog.text
