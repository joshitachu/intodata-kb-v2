"""Slice 3: publication, and the only four checks that remain.

The regression that matters most is at the bottom of the "publishes clean"
group: a run whose agent called the project ``ACME-2`` -- the name the staged
workspace itself taught it -- while the internal id was ``ACME_ZIP_TEST`` was
failed by ``"candidate project_id does not match parser foundation"`` AFTER it
had already written a valid 22,723-byte deliverable. Nothing in this module
compares prose to an internal identity, and these tests are what keeps it that
way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.publish import (
    finalize_publication,
    publish_agent_output,
)
from tests.test_agent_workspace.workspace_fixture import make_workspace

_CLEAN = "# Orders\n\nOrders move from the ERP to the field service app nightly.\n"


def _staged(tmp_path: Path, documents: dict[str, str]) -> Path:
    staging = make_workspace(tmp_path / "docs_staging")
    for relative, text in documents.items():
        path = staging / contract.OUTPUT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return staging


# -- what publishes ------------------------------------------------------


def test_nested_output_reaches_the_docs_root_with_its_relative_path(tmp_path):
    staging = _staged(
        tmp_path,
        {
            "00_OVERVIEW.md": _CLEAN,
            "integrations/orders.md": _CLEAN,
            "integrations/deep/invoices.md": _CLEAN,
        },
    )

    outcome = publish_agent_output(staging)
    assert outcome.ok, outcome.blocking
    assert outcome.published == (
        "00_OVERVIEW.md",
        "integrations/deep/invoices.md",
        "integrations/orders.md",
    )

    finalize_publication(staging, outcome)
    assert (staging / "integrations" / "orders.md").read_text(encoding="utf-8") == _CLEAN
    assert (staging / "integrations" / "deep" / "invoices.md").is_file()


def test_a_human_friendly_name_and_a_foreign_project_id_publish_clean(tmp_path):
    """The exact mismatch that killed the last paid pilot.

    ``_goal_driven/repair_state.json``: "candidate project_id does not match
    parser foundation". The agent is handed a workspace whose staged root is
    named ``ACME-2``; the internal id is ``ACME_ZIP_TEST``. Both names are
    correct for their own audience and neither is a defect.
    """

    document = (
        "# ACME-2 integration overview\n\n"
        "The ACME-2 estate exchanges tickets between the ERP and the field "
        "service platform. No parser label, no scan identifier and no internal "
        "project id appears in this document, which is the point.\n"
    )
    staging = _staged(tmp_path, {"00_OVERVIEW.md": document})

    outcome = publish_agent_output(staging)

    assert outcome.ok, outcome.blocking
    assert outcome.published == ("00_OVERVIEW.md",)


def test_an_absent_parser_label_is_not_a_finding(tmp_path):
    """Nothing here requires a document to restate scanner evidence."""

    staging = _staged(
        tmp_path,
        {"01_FLOWS.md": "# Flows\n\nThe nightly batch reconciles two systems.\n"},
    )
    outcome = publish_agent_output(staging)
    assert outcome.ok, outcome.blocking


def test_a_lowercase_document_name_is_not_treated_as_a_support_artifact(tmp_path):
    staging = _staged(tmp_path, {"integrations/order_flow.md": _CLEAN})
    outcome = publish_agent_output(staging)
    assert outcome.published == ("integrations/order_flow.md",)


# -- credential leak: the one check that did not exist before ------------


@pytest.mark.parametrize(
    ("label", "line"),
    [
        ("assignment", "Connection uses password=Sup3rSecret99 for the batch user."),
        ("connection url", "jdbc:mysql://svc_user:s3cr3tpw@erp-db:3306/orders"),
        ("bearer", "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abcdefghij"),
        (
            "private key",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n-----END RSA PRIVATE KEY-----",
        ),
        ("aws key", "The job authenticates with AKIAIOSFODNN7EXAMPLE."),
        (
            "jwt",
            (
                "Token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"
            ),
        ),
    ],
)
def test_a_planted_credential_blocks_publication(tmp_path, label, line):
    staging = _staged(tmp_path, {"05_RUNTIME.md": f"# Runtime\n\n{line}\n"})

    outcome = publish_agent_output(staging)

    assert not outcome.ok, label
    assert outcome.published == ()
    assert any("05_RUNTIME.md" in finding for finding in outcome.blocking)
    # Nothing is written when a document is blocked.
    assert not (staging / "05_RUNTIME.md").exists()


def test_a_blocked_publication_cannot_be_finalized(tmp_path):
    staging = _staged(tmp_path, {"05_RUNTIME.md": "password=Sup3rSecret99\n"})
    outcome = publish_agent_output(staging)
    with pytest.raises(ValueError):
        finalize_publication(staging, outcome)


def test_a_credential_finding_never_quotes_the_secret(tmp_path):
    """The finding lands in a log, an error message and a database row."""

    staging = _staged(tmp_path, {"05_RUNTIME.md": "api_key=AbCd1234EfGh5678\n"})
    outcome = publish_agent_output(staging)
    assert not outcome.ok
    assert not any("AbCd1234EfGh5678" in finding for finding in outcome.blocking)
    assert any("api_key" in finding for finding in outcome.blocking)


@pytest.mark.parametrize(
    "line",
    [
        "The password is configured on the connection, not stored in the job.",
        "password = context.erp_password",
        "Set the api_key from ${ERP_API_KEY} at deploy time.",
        "| password | [REDACTED-CREDENTIAL] |",
        "A client_secret is required but was not captured by the scan.",
    ],
)
def test_naming_a_credential_is_not_leaking_one(tmp_path, line):
    """Precision matters: this check BLOCKS a paid run.

    Documentation that says a password exists is doing its job. Blocking on
    the WORD would be the over-gating this lane exists to remove.
    """

    staging = _staged(tmp_path, {"05_RUNTIME.md": f"# Runtime\n\n{line}\n"})
    outcome = publish_agent_output(staging)
    assert outcome.ok, outcome.blocking


# -- internal leak -------------------------------------------------------


@pytest.mark.parametrize(
    "prose",
    [
        "See instructions/writing_the_docs.md for the required sections.",
        "Derived from scan/jobs/Orders/Orders.json.",
        "Read AGENT_GUIDE.md first.",
        "The estate map is in INDEX.md.",
        "Row 12 of facts/navigation/units/part_001.jsonl.",
        "Written under docs_staging before the swap.",
        "Recorded in the Evidence Register.",
        "scanner detail is limited for this design.",
    ],
)
def test_an_internal_phrase_warns_but_never_blocks_publication(tmp_path, prose):
    """Naming is advisory. Only safety blocks.

    Run 241 wrote four usable documents and was refused over the phrase
    "Scanner-Limited" -- which `writing_the_docs.md` had told it to use as a
    heading, because `doc_catalog.yaml` names that section and
    `client_language.py` forbids it. Failing a finished run over a word is the
    behaviour this lane exists to remove; the operator gets a warning and the
    documentation ships.
    """
    output = tmp_path / "output"
    output.mkdir()
    (output / "01_DOC.md").write_text(f"# Doc\n\n{prose}\n", encoding="utf-8")

    outcome = publish_agent_output(tmp_path)

    assert outcome.ok
    assert outcome.published == ("01_DOC.md",)
    assert outcome.warnings, "the operator still needs to be told"
    assert all(w.startswith("01_DOC.md: ") for w in outcome.warnings)
    assert not outcome.blocking


def test_an_absent_output_directory_blocks(tmp_path):
    staging = make_workspace(tmp_path / "docs_staging")
    (staging / contract.OUTPUT).rmdir()
    outcome = publish_agent_output(staging)
    assert not outcome.ok
    assert "no output/ directory" in outcome.blocking[0]


def test_an_output_with_only_empty_markdown_blocks(tmp_path):
    staging = _staged(tmp_path, {"00_OVERVIEW.md": "   \n\n"})
    outcome = publish_agent_output(staging)
    assert not outcome.ok
    assert any("no non-empty readable Markdown" in item for item in outcome.blocking)
    assert any("empty, not published" in item for item in outcome.warnings)


def test_a_symlink_out_of_the_workspace_blocks(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# Not ours\n", encoding="utf-8")
    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN})
    (staging / contract.OUTPUT / "escape.md").symlink_to(outside)

    outcome = publish_agent_output(staging)

    assert not outcome.ok
    assert any("escape.md: symbolic link" in item for item in outcome.blocking)


def test_a_symlink_pointing_INSIDE_output_also_blocks(tmp_path):
    """The old rule only caught the external case, and that is the weaker half.

    A link is re-pointable between the moment it is validated and the moment
    it is read, so "it currently resolves inside output/" is not a property
    worth publishing on. There is no case in which the agent needs one.
    """

    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN})
    (staging / contract.OUTPUT / "alias.md").symlink_to(staging / contract.OUTPUT / "00_OVERVIEW.md")

    outcome = publish_agent_output(staging)

    assert not outcome.ok
    assert any("alias.md: symbolic link" in item for item in outcome.blocking)


def test_a_symlinked_directory_inside_output_blocks(tmp_path):
    """Version-dependent behaviour, pinned to one answer.

    ``Path.rglob('*')`` descended into a symlinked directory up to Python 3.12
    and stopped in 3.13 (measured here on 3.14.6: the link is yielded, its
    contents are not). This project supports 3.11+, so the old walk published
    a linked tree on one interpreter and silently ignored it on another, with
    no finding either way.
    """

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "smuggled.md").write_text(_CLEAN, encoding="utf-8")
    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN})
    (staging / contract.OUTPUT / "linked").symlink_to(outside, target_is_directory=True)

    outcome = publish_agent_output(staging)

    assert not outcome.ok
    assert any("linked: symbolic link" in item for item in outcome.blocking)
    assert not any("smuggled" in item for item in outcome.published)


def test_non_markdown_output_is_not_published(tmp_path):
    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN})
    (staging / contract.TOPOLOGY_FILE).write_text("{}", encoding="utf-8")
    outcome = publish_agent_output(staging)
    assert outcome.published == ("00_OVERVIEW.md",)


# -- finalization --------------------------------------------------------


def test_finalization_builds_a_clean_room_and_keeps_nexus_state(tmp_path):
    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN})
    (staging / "_audit").mkdir()
    (staging / "_audit" / "generation_profile.json").write_text("{}", encoding="utf-8")
    (staging / "_topology").mkdir()
    (staging / "_topology" / "business_topology.json").write_text("{}", encoding="utf-8")

    outcome = publish_agent_output(staging)
    assembly = finalize_publication(staging, outcome)

    assert assembly.ok, assembly.blocking
    # Every loose evidence file the contract declares, not a remembered pair.
    # `INDEX.md` is the third and it is not underscore-prefixed, so one left
    # behind ships the estate's evidence map to the client and is pushed to
    # RAG as a document. Nothing carries it now, so nothing has to remember to
    # delete it -- which is the whole difference between this and a cleanup.
    for name in contract.EVIDENCE_FILES:
        assert not (staging / name).exists(), name
        assert name.as_posix() in assembly.excluded, name
    for root in (*contract.EVIDENCE_ROOTS, *contract.WRITABLE_ROOTS):
        assert not (staging / root).exists(), root
    assert "scan" in assembly.excluded and "output" in assembly.excluded
    # Nexus' own state survives; the publication path downstream reads it.
    assert (staging / "_audit" / "generation_profile.json").is_file()
    assert (staging / "_topology" / "business_topology.json").is_file()
    assert "_audit/generation_profile.json" in assembly.carried
    assert "_topology/business_topology.json" in assembly.carried
    assert (staging / "00_OVERVIEW.md").is_file()
    # The scratch directories used to build the clean room are gone.
    assert not (staging.parent / f"{staging.name}.clean").exists()
    assert not (staging.parent / f"{staging.name}.discarded").exists()


def test_a_file_the_cleanup_list_never_named_does_not_publish(tmp_path):
    """The verified hole, reproduced: publication by survival.

    Finalization used to remove ``contract.EVIDENCE_ROOTS`` and
    ``contract.WRITABLE_ROOTS`` from the staging directory and write the
    accepted documents into what was left -- and ``services/atomic_swap.py``
    renames that same directory to ``docs/`` verbatim. So a file the list did
    not name shipped, unvalidated. Both of these were confirmed present in the
    published tree, and ``rogue.md`` was also accepted by
    ``is_published_doc_filename`` and pushed to RAG as a client document.
    """

    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN})
    (staging / "rogue.md").write_text("# Rogue\n\nNothing validated this.\n", encoding="utf-8")
    (staging / "rogue-visible").mkdir()
    (staging / "rogue-visible" / "extra.md").write_text("# Extra\n", encoding="utf-8")

    outcome = publish_agent_output(staging)
    assembly = finalize_publication(staging, outcome)

    assert assembly.ok, assembly.blocking
    assert assembly.published == ("00_OVERVIEW.md",)
    assert not (staging / "rogue.md").exists()
    assert not (staging / "rogue-visible").exists()
    # Named, not silently dropped.
    assert "rogue.md" in assembly.excluded
    assert "rogue-visible" in assembly.excluded


def test_nexus_restart_state_does_not_reach_the_publication(tmp_path):
    """``_agent_control/`` describes an in-flight run; a published tree is not one.

    It used to survive for the only reason anything used to survive -- it was
    not on a removal list. ``checkpoint.md`` shipped to the client's disk that
    way, and the run-bound ``resume_identity.json`` joined it. Underscore-
    prefixing keeps both out of RAG and out of every client listing, which is a
    necessary condition for being CARRIED and never a sufficient one: a root
    travels because something reads it back, or it does not travel.
    """

    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN})
    control = staging / contract.CONTROL
    control.mkdir(parents=True, exist_ok=True)
    (control / "checkpoint.md").write_text("## Checkpoint after attempt 1\n", encoding="utf-8")
    (control / "resume_identity.json").write_text('{"goal": "..."}', encoding="utf-8")
    (control / "planted.md").write_text("# Planted\n", encoding="utf-8")

    outcome = publish_agent_output(staging)
    assembly = finalize_publication(staging, outcome)

    assert assembly.ok, assembly.blocking
    assert not (staging / contract.CONTROL).exists()
    assert not any(item.startswith("_agent_control") for item in assembly.carried)
    assert contract.CONTROL.name in assembly.excluded


def test_a_rogue_file_inside_a_carried_nexus_root_is_not_a_client_document(tmp_path):
    """``_audit`` is carried, so say plainly what that does and does not admit.

    A file under an underscore-prefixed root is invisible to every client
    surface -- ``is_published_doc_filename``, ``_count_staged_docs``, the PDF
    export and the dashboard listing all reject an underscore segment -- so it
    is operator diagnostics, not published documentation.
    """

    from mcp_server.services.doc_quality_scorer import is_published_doc_filename

    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN})
    (staging / "_audit").mkdir()
    (staging / "_audit" / "smuggled.md").write_text("# Smuggled\n", encoding="utf-8")

    outcome = publish_agent_output(staging)
    assembly = finalize_publication(staging, outcome)

    assert assembly.ok
    assert assembly.published == ("00_OVERVIEW.md",)
    assert (staging / "_audit" / "smuggled.md").is_file()
    assert not is_published_doc_filename("_audit/smuggled.md")

    from mcp_server.services.rag_bridge import RagBridge

    names = {item["filename"] for item in RagBridge()._read_md_files(str(staging))}
    assert names == {"00_OVERVIEW.md"}


def test_a_credential_in_a_carried_internal_artifact_warns_and_still_publishes(tmp_path):
    """Internal artifacts are operator-only, so a finding there is advisory.

    ``_audit/opencode_errors.json`` records raw provider error events, and a
    provider message legitimately quotes part of the request. Blocking on that
    would hold a finished paid run over a string no client can see -- the exact
    failure mode this lane removes. The scan still runs and still reports.
    """

    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN})
    (staging / "_audit").mkdir()
    (staging / "_audit" / "opencode_errors.json").write_text(
        '{"message": "connect failed for password=Tr0ub4dor3"}\n', encoding="utf-8"
    )

    outcome = publish_agent_output(staging)
    assembly = finalize_publication(staging, outcome)

    assert assembly.ok, assembly.blocking
    assert any("internal artifact" in item and "password" in item for item in assembly.warnings)
    # The value itself is never quoted into a warning that reaches a log.
    assert not any("Tr0ub4dor3" in item for item in assembly.warnings)


def test_the_publication_is_exactly_the_accepted_set_plus_the_declared_roots(tmp_path):
    """The clean-room invariant, stated once and checkable.

    Publication content is a function of two trusted inputs -- the in-memory
    accepted snapshot and :data:`NEXUS_OWNED_ROOTS` -- and of nothing else that
    happens to be lying around in staging.
    """

    from mcp_server.providers.agent_workspace.publish import NEXUS_OWNED_ROOTS

    staging = _staged(
        tmp_path,
        {"00_OVERVIEW.md": _CLEAN, "integrations/orders.md": _CLEAN, "reference/units.md": _CLEAN},
    )
    # Everything else an agent with workspace-wide write access can produce.
    (staging / "rogue.md").write_text("# Rogue\n", encoding="utf-8")
    (staging / "_facts").mkdir()
    (staging / "_facts" / "smuggled.md").write_text("# Internal RAG layer\n", encoding="utf-8")
    (staging / "_topology").mkdir()
    (staging / "_topology" / "business_topology.json").write_text("{}", encoding="utf-8")
    (staging / contract.NOTES / "scratch.md").write_text("# Scratch\n", encoding="utf-8")

    outcome = publish_agent_output(staging)
    assembly = finalize_publication(staging, outcome)

    assert assembly.ok, assembly.blocking
    present = {path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file()}
    assert present == {
        "00_OVERVIEW.md",
        "integrations/orders.md",
        "reference/units.md",
        "_topology/business_topology.json",
    }
    # `_facts/` is how an agent would reach the internal RAG layers
    # (`rag_support_package.collect_internal_artifacts` reads `docs/_facts/*.md`).
    # It is not a declared Nexus root, so it does not travel.
    assert not (staging / "_facts").exists()
    assert {root.as_posix() for root in NEXUS_OWNED_ROOTS} == {
        "_audit",
        "_topology",
        "_transient",
    }
    assert contract.CONTROL not in NEXUS_OWNED_ROOTS


def test_the_clean_room_is_never_built_from_a_blocked_outcome(tmp_path):
    staging = _staged(tmp_path, {"05_RUNTIME.md": "password=Sup3rSecret99\n"})
    outcome = publish_agent_output(staging)
    with pytest.raises(ValueError):
        finalize_publication(staging, outcome)
    # The draft survives for the operator to read.
    assert (staging / contract.OUTPUT / "05_RUNTIME.md").is_file()
    assert (staging / contract.SCAN).is_dir()


def test_a_document_written_under_a_reserved_name_survives_cleanup(tmp_path):
    """Ordering regression: clear the workspace, THEN write.

    ``output/notes/handover.md`` publishes to ``notes/handover.md``, and
    ``notes/`` is one of the directories cleanup removes. Writing first would
    delete the document it had just published.
    """

    staging = _staged(tmp_path, {"notes/handover.md": _CLEAN})
    outcome = publish_agent_output(staging)
    finalize_publication(staging, outcome)

    assert (staging / "notes" / "handover.md").read_text(encoding="utf-8") == _CLEAN


def test_an_internal_segment_inside_output_is_skipped_with_a_warning(tmp_path):
    staging = _staged(tmp_path, {"00_OVERVIEW.md": _CLEAN, "_scratch/draft.md": _CLEAN})
    outcome = publish_agent_output(staging)
    assert outcome.published == ("00_OVERVIEW.md",)
    assert any("_scratch/draft.md" in item for item in outcome.warnings)


# -- the RAG push half of "reaches docs/ and RAG" ------------------------


def test_the_rag_push_reads_nested_documents_by_relative_path(tmp_path):
    """``docs/integrations/orders.md`` must be pushed as that exact filename.

    Additive to the legacy lane: no docs tree produced by the legacy or
    composite lanes has a subdirectory that is not underscore-prefixed, so
    they see no change. Without this, an agent-chosen structure lands on disk
    and is silently absent from RAG.
    """

    from mcp_server.services.rag_bridge import RagBridge

    docs = tmp_path / "docs"
    (docs / "integrations").mkdir(parents=True)
    (docs / "_assessment").mkdir()
    (docs / "00_OVERVIEW.md").write_text("# Overview\n", encoding="utf-8")
    (docs / "integrations" / "orders.md").write_text("# Orders\n", encoding="utf-8")
    (docs / "_assessment" / "scorecard.md").write_text("# Internal\n", encoding="utf-8")
    (docs / "engineering_facts.md").write_text("# Support artifact\n", encoding="utf-8")

    items = RagBridge()._read_md_files(str(docs))
    names = {item["filename"] for item in items}

    assert names == {"00_OVERVIEW.md", "integrations/orders.md"}
    # The two exclusions the top-level predicate already owned still hold.
    assert "engineering_facts.md" not in names
    assert "_assessment/scorecard.md" not in names


def test_a_legacy_docs_tree_pushes_byte_identically(tmp_path):
    """The regression guard for the shared call site.

    Every subdirectory a legacy or composite run writes is
    underscore-prefixed, so the nested pass must contribute nothing.
    """

    from mcp_server.services.rag_bridge import RagBridge

    docs = tmp_path / "docs"
    for internal in ("_assessment", "_audit", "_rag_support", "_topology", "_facts"):
        (docs / internal).mkdir(parents=True)
        (docs / internal / "note.md").write_text("# Internal\n", encoding="utf-8")
    (docs / "00_AGENT_BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    (docs / "09_EXECUTIVE_SUMMARY.md").write_text("# Summary\n", encoding="utf-8")

    names = {item["filename"] for item in RagBridge()._read_md_files(str(docs))}

    assert names == {"00_AGENT_BRIEF.md", "09_EXECUTIVE_SUMMARY.md"}


def test_compound_credential_keys_are_caught():
    """Regression: the blocking gate missed every compound key.

    The original lookbehind excluded ``_`` as well as letters and digits, so
    ``password = ...`` fired but ``sftp_password = ...`` did not. Compound
    names are how real estates spell these -- the ACME fixture's context
    groups are ``sFTP``, ``RHM_DB``, ``LogDB``, ``Azure`` -- so the one
    blocking safety check in this lane was blind to the common case while
    passing its own tests on the uncommon one.
    """
    from mcp_server.providers.agent_workspace.publish import credential_findings

    for line in (
        "password = Tr0ub4dor3",
        "sftp_password = Tr0ub4dor3",
        "mssql_password: Wint3r2024",
        "ftpPassword = Tr0ub4dor3",
        "db_secret: 8f14e45fcee",
        "client_token = abc123def456",
        "context.sftp_password = Tr0ub4dor&3",
        "ASSETHUB_API_KEY=8f14e45fceea167a",
    ):
        assert credential_findings(line), f"missed a credential in: {line}"

    # Prose about credentials, and every form of reference, must still publish:
    # holding a paid estate's documentation over the word "password" is its own
    # failure mode.
    for line in (
        "The password is stored in the ActiveMQ context group and never logged.",
        "sftp_password is read from the sFTP context group per environment.",
        "Each job resolves mssql_password from context, not from a literal.",
        "password: [PASSWORD_REDACTED]",
        "password = context.mssql_password",
        "API_KEY: ${ASSETHUB_API_KEY}",
        'password: routines.Vault.get("mssql")',
        "token: refreshed hourly by the scheduler",
    ):
        assert not credential_findings(line), f"false positive on: {line}"
