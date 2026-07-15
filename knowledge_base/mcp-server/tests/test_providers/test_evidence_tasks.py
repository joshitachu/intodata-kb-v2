"""Job-boundary evidence task planning tests (replaces char-based chunking)."""
from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.evidence_tasks import (
    EvidenceTask,
    job_section_texts,
    plan_evidence_tasks,
    split_job_sections,
)


def _job_section(name: str, body_bytes: int) -> str:
    filler = "evidence line for analysis\n" * max(1, body_bytes // 27)
    return f"\n---\n## Job: {name}\n\n### Components\n\n{filler}"


def _write_group_file(tmp_path: Path, jobs: dict[str, int], preamble: str = "# Group Evidence\n") -> Path:
    path = tmp_path / "GroupA.md"
    # Group-wide inventory naming EVERY job — this is exactly what fooled the
    # old substring-based visibility check.
    inventory = "Routines used by: " + ", ".join(jobs) + "\n"
    path.write_text(preamble + inventory + "".join(_job_section(n, size) for n, size in jobs.items()), encoding="utf-8")
    return path


def test_split_job_sections_extracts_preamble_and_jobs(tmp_path):
    path = _write_group_file(tmp_path, {"JobA": 100, "JobB": 100})
    preamble, sections = split_job_sections(path.read_text(encoding="utf-8"))
    assert "Group Evidence" in preamble
    assert "Routines used by" in preamble
    assert [s.name for s in sections] == ["JobA", "JobB"]
    assert sections[0].text.startswith("## Job: JobA")
    assert "## Job: JobB" not in sections[0].text


def test_small_group_is_single_task_with_original_file(tmp_path):
    path = _write_group_file(tmp_path, {"JobA": 100, "JobB": 100})
    tasks = plan_evidence_tasks("GroupA", path, ["JobA", "JobB"], tmp_path / "out", budget_bytes=70_000)
    assert len(tasks) == 1
    assert tasks[0].task_id == "GroupA"
    assert tasks[0].path == path
    assert tasks[0].units == ["JobA", "JobB"]
    assert tasks[0].missing_units == []


def test_bin_packing_respects_budget_and_never_splits_a_job(tmp_path):
    jobs = {"JobA": 30_000, "JobB": 30_000, "JobC": 30_000, "JobD": 5_000}
    path = _write_group_file(tmp_path, jobs)
    tasks = plan_evidence_tasks("GroupA", path, list(jobs), tmp_path / "out", budget_bytes=70_000)
    assert len(tasks) >= 2
    seen_units: list[str] = []
    for task in tasks:
        text = task.path.read_text(encoding="utf-8")
        # Every unit the task claims is physically present as a job section.
        for unit in task.units:
            assert f"## Job: {unit}" in text
        # No job section from another task leaks in (jobs are whole, disjoint).
        for other in set(jobs) - set(task.units):
            assert f"## Job: {other}\n" not in text
        seen_units.extend(task.units)
        assert len(text.encode("utf-8")) <= 70_000 + 2_000  # header allowance
    assert sorted(seen_units) == sorted(jobs)


def test_task_header_lists_exactly_contained_units(tmp_path):
    jobs = {"JobA": 40_000, "JobB": 40_000}
    path = _write_group_file(tmp_path, jobs)
    tasks = plan_evidence_tasks("GroupA", path, list(jobs), tmp_path / "out", budget_bytes=45_000)
    assert len(tasks) == 2
    first_header = tasks[0].path.read_text(encoding="utf-8").split("---", 1)[0]
    assert "- JobA" in first_header
    assert "- JobB" not in first_header
    second_header = tasks[1].path.read_text(encoding="utf-8").split("---", 1)[0]
    assert "- JobB" in second_header
    assert "- JobA" not in second_header


def test_oversized_job_gets_exactly_one_task(tmp_path):
    jobs = {"JobA": 100_000, "JobB": 1_000}
    path = _write_group_file(tmp_path, jobs)
    tasks = plan_evidence_tasks("GroupA", path, list(jobs), tmp_path / "out", budget_bytes=40_000)
    by_unit = {u: t for t in tasks for u in t.units}
    assert by_unit["JobA"].units == ["JobA"]  # oversized job alone in its task
    text = by_unit["JobA"].path.read_text(encoding="utf-8")
    assert text.count("## Job: JobA") == 1


def test_units_without_evidence_are_flagged_missing_on_first_task(tmp_path):
    jobs = {"JobA": 40_000, "JobB": 40_000}
    path = _write_group_file(tmp_path, jobs)
    tasks = plan_evidence_tasks(
        "GroupA", path, ["JobA", "JobB", "GhostJob"], tmp_path / "out", budget_bytes=45_000
    )
    assert tasks[0].missing_units == ["GhostJob"]
    assert all(t.missing_units == [] for t in tasks[1:])
    header = tasks[0].path.read_text(encoding="utf-8").split("\n---\n", 1)[0]
    assert "GhostJob" in header
    assert "NO evidence" in header
    # GhostJob must never be claimed as contained evidence.
    assert "GhostJob" not in tasks[0].units


def test_unstructured_oversized_file_stays_single_task(tmp_path):
    path = tmp_path / "GroupA.md"
    path.write_text("no job headings here\n" * 10_000, encoding="utf-8")
    tasks = plan_evidence_tasks("GroupA", path, ["JobA"], tmp_path / "out", budget_bytes=40_000)
    assert len(tasks) == 1
    assert tasks[0].path == path
    assert tasks[0].units == ["JobA"]


def test_missing_group_file_returns_no_tasks(tmp_path):
    tasks = plan_evidence_tasks("GroupA", tmp_path / "absent.md", ["JobA"], tmp_path / "out", 40_000)
    assert tasks == []


def test_task_ids_are_stable_and_deterministic(tmp_path):
    jobs = {"JobA": 40_000, "JobB": 40_000, "JobC": 40_000}
    path = _write_group_file(tmp_path, jobs)
    first = plan_evidence_tasks("GroupA", path, list(jobs), tmp_path / "out", budget_bytes=45_000)
    second = plan_evidence_tasks("GroupA", path, list(jobs), tmp_path / "out", budget_bytes=45_000)
    assert [t.task_id for t in first] == [t.task_id for t in second]
    assert [t.units for t in first] == [t.units for t in second]
    assert first[0].task_id == "GroupA__task_1"


def test_all_units_combines_evidence_and_missing():
    task = EvidenceTask(index=1, task_id="G__task_1", path=Path("x.md"), units=["A"], missing_units=["B"])
    assert task.all_units == ["A", "B"]


def test_job_section_texts_recovers_each_units_own_slice(tmp_path):
    # Used by job-scoped fabrication validation: a bin-packed task's own
    # evidence file must yield each job's text in isolation, not the whole
    # task's combined text -- this is what makes it possible to catch a
    # component real for JobA being misattributed to JobB in the same task.
    jobs = {"JobA": 30_000, "JobB": 30_000}
    path = _write_group_file(tmp_path, jobs)
    tasks = plan_evidence_tasks("GroupA", path, list(jobs), tmp_path / "out", budget_bytes=70_000)
    assert len(tasks) == 1  # both fit in one task -> single evidence file

    texts = job_section_texts(tasks[0].path, tasks[0].units)
    assert set(texts) == {"JobA", "JobB"}
    assert "## Job: JobA" in texts["JobA"]
    assert "## Job: JobB" not in texts["JobA"]
    assert "## Job: JobB" in texts["JobB"]
    assert "## Job: JobA" not in texts["JobB"]


def test_job_section_texts_omits_units_with_no_section(tmp_path):
    jobs = {"JobA": 10_000}
    path = _write_group_file(tmp_path, jobs)
    texts = job_section_texts(path, ["JobA", "GhostJob"])
    assert set(texts) == {"JobA"}


def test_job_section_texts_missing_file_returns_empty(tmp_path):
    assert job_section_texts(tmp_path / "absent.md", ["JobA"]) == {}
