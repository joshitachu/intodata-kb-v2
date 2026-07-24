"""Job-boundary evidence task planning for Phase 1/2 group processing.

Replaces the former character-based group chunker. The old chunker cut group
evidence at a fixed byte offset (splitting jobs mid-section, even mid-line) and
stamped every chunk with a header claiming ALL group units were "visible in
this chunk" (substring match against group-wide inventories). Downstream
prompts then required every listed unit to be analyzed, forcing models to
fabricate analyses for jobs whose evidence they never received.

This module plans tasks on typed executable-design section boundaries instead:

- A job's evidence section is NEVER split across tasks.
- Multiple whole jobs are bin-packed into one task up to a byte budget
  (``MCP_PHASE2_TASK_EVIDENCE_BYTES``) so small jobs share a call.
- Each task knows exactly which canonical units its evidence contains; the
  written evidence file header lists exactly those units and nothing else.
- Canonical units with no evidence section in the group file are assigned to
  the first task as ``missing_units`` so the model is explicitly told to state
  what evidence is missing instead of silently omitting (or inventing) them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

TASKS_DIRNAME = "_tasks"

_JOB_HEADING_RE = re.compile(
    r"^## (?:(?:Talend )?(?:Job|Route)|Artifact):\s*(.+?)\s*$",
    re.MULTILINE,
)
_TRAILING_SEPARATOR_RE = re.compile(r"(?:\n---\s*)+\Z")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "group"


@dataclass(frozen=True)
class JobSection:
    """One typed executable-design section extracted from group evidence."""

    name: str
    text: str  # includes the typed heading line

    @property
    def size_bytes(self) -> int:
        return len(self.text.encode("utf-8"))


@dataclass(frozen=True)
class EvidenceTask:
    """One planned analysis/investigation task over whole-job evidence.

    ``units`` are canonical units whose evidence sections are physically inside
    ``path``. ``missing_units`` are canonical units assigned to this task that
    have NO evidence in the scan (first task only) — prompts must instruct the
    model to state what is missing for those, never to reconstruct them.
    """

    index: int  # 1-based
    task_id: str
    path: Path
    units: list[str] = field(default_factory=list)
    missing_units: list[str] = field(default_factory=list)

    @property
    def all_units(self) -> list[str]:
        return [*self.units, *self.missing_units]


def split_job_sections(text: str) -> tuple[str, list[JobSection]]:
    """Split group evidence markdown into (preamble, job sections).

    Sections start at each supported Job/Route/Artifact heading and run to the
    next one.
    The ``---`` horizontal-rule separators the preprocess writer emits between
    jobs are stripped from section tails (they are cosmetic separators, not
    content). Returns the text before the first heading as the preamble.
    """
    matches = list(_JOB_HEADING_RE.finditer(text))
    if not matches:
        return text, []

    preamble = _TRAILING_SEPARATOR_RE.sub("", text[: matches[0].start()]).rstrip()
    sections: list[JobSection] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = _TRAILING_SEPARATOR_RE.sub("", text[match.start():end]).rstrip()
        sections.append(JobSection(name=match.group(1), text=body))
    return preamble, sections


def _match_section_names_to_units(
    sections: list[JobSection], group_units: list[str]
) -> dict[str, str]:
    """Map job-section names to canonical unit names (exact, then case-insensitive).

    Shared low-level matcher: used both to plan tasks and, later, to recover a
    single job's own evidence text out of a multi-job task file for
    job-scoped fabrication validation (see ``job_section_texts`` below).
    """
    exact = {u: u for u in group_units}
    lowered = {u.lower(): u for u in group_units}
    mapping: dict[str, str] = {}
    matched: set[str] = set()
    for section in sections:
        unit = exact.get(section.name) or lowered.get(section.name.lower())
        if unit is not None and unit not in matched:
            mapping[section.name] = unit
            matched.add(unit)
    return mapping


def _match_units(
    sections: list[JobSection], group_units: list[str]
) -> tuple[dict[str, str], list[str]]:
    """Map job-section names to canonical units, plus canonical units with no
    matching section. Section names that match no canonical unit are simply
    carried along as evidence without a unit claim.
    """
    mapping = _match_section_names_to_units(sections, group_units)
    matched = set(mapping.values())
    missing = [u for u in group_units if u not in matched]
    return mapping, missing


def job_section_texts(task_path: Path, units: list[str]) -> dict[str, str]:
    """Return each unit's own typed evidence-section text from a task file.

    A bin-packed task can hold several whole jobs' evidence in one file.
    Verified failure mode: an agent analyzing several sibling jobs at once
    can borrow one job's real component name for another job in the same
    batch (e.g. attributing 'tMSSqlOutput_1', real for job A, to job B, which
    actually uses 'tDBOutput_1'). Checking the model's output against the
    *combined* evidence of the whole task lets that pass, since the name is
    real somewhere in the batch. This function recovers each unit's own
    section text so a validator can check attributed content against only
    that job's evidence. Units with no matching section (e.g. a task's
    ``missing_units``, which have no evidence anywhere) are simply absent
    from the returned mapping.
    """
    try:
        text = task_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    _, sections = split_job_sections(text)
    mapping = _match_section_names_to_units(sections, units)
    text_by_section_name = {section.name: section.text for section in sections}
    return {
        unit: text_by_section_name[section_name]
        for section_name, unit in mapping.items()
        if section_name in text_by_section_name
    }


def _render_task_evidence(
    group: str,
    index: int,
    total: int,
    units: list[str],
    extra_sections: list[str],
    missing_units: list[str],
    preamble: str,
    sections: list[JobSection],
) -> str:
    lines: list[str] = [
        f"# Evidence task {index} of {total} for group {group}",
        "",
        "Canonical units whose evidence sections are contained in THIS file:",
        *(f"- {name}" for name in units),
    ]
    if extra_sections:
        lines += [
            "",
            "Additional evidence sections in this file (not canonical units):",
            *(f"- {name}" for name in extra_sections),
        ]
    if missing_units:
        lines += [
            "",
            "Assigned units with NO evidence sections anywhere in the scan. For each,",
            "state exactly what evidence is missing. Do NOT infer or reconstruct their",
            "components, code, or configuration:",
            *(f"- {name}" for name in missing_units),
        ]
    lines += ["", "---", ""]
    if preamble:
        lines += [preamble, ""]
    for section in sections:
        lines += [section.text, ""]
    return "\n".join(lines)


def plan_evidence_tasks(
    group: str,
    group_file: Path,
    group_units: list[str],
    out_dir: Path,
    budget_bytes: int,
) -> list[EvidenceTask]:
    """Plan whole-job evidence tasks for one group.

    Single-task groups (whole file within budget, or no job structure) reuse the
    original group file untouched. Multi-task groups get per-task evidence files
    under ``{out_dir}/_tasks/`` whose headers list exactly the contained units.
    """
    if not group_file.exists():
        return []

    text = group_file.read_text(encoding="utf-8", errors="replace")
    file_bytes = len(text.encode("utf-8"))
    preamble, sections = split_job_sections(text)

    if file_bytes <= budget_bytes:
        return [EvidenceTask(index=1, task_id=group, path=group_file, units=list(group_units))]

    if not sections:
        # No job boundaries to split on. Splitting blindly would reintroduce the
        # partial-evidence fabrication problem, so run one oversized task and
        # surface the condition instead.
        logger.warning(
            "group %s evidence is %d bytes (> budget %d) but has no typed "
            "Job/Route sections; running a single oversized task",
            group, file_bytes, budget_bytes,
        )
        return [EvidenceTask(index=1, task_id=group, path=group_file, units=list(group_units))]

    section_to_unit, missing_units = _match_units(sections, group_units)

    # First-fit in file order: whole sections only, never split a job.
    preamble_bytes = len(preamble.encode("utf-8"))
    bins: list[list[JobSection]] = []
    current: list[JobSection] = []
    current_bytes = preamble_bytes
    for section in sections:
        if current and current_bytes + section.size_bytes > budget_bytes:
            bins.append(current)
            current = []
            current_bytes = preamble_bytes
        current.append(section)
        current_bytes += section.size_bytes
    if current:
        bins.append(current)

    if len(bins) == 1:
        return [EvidenceTask(index=1, task_id=group, path=group_file, units=list(group_units))]

    tasks_dir = out_dir / TASKS_DIRNAME
    tasks_dir.mkdir(parents=True, exist_ok=True)
    total = len(bins)
    tasks: list[EvidenceTask] = []
    for idx, bin_sections in enumerate(bins, start=1):
        units = [section_to_unit[s.name] for s in bin_sections if s.name in section_to_unit]
        extra = [s.name for s in bin_sections if s.name not in section_to_unit]
        assigned_missing = missing_units if idx == 1 else []
        task_id = f"{group}__task_{idx}"
        path = tasks_dir / f"{_safe_name(group)}__task_{idx}.md"
        path.write_text(
            _render_task_evidence(
                group, idx, total, units, extra, assigned_missing, preamble, bin_sections
            ),
            encoding="utf-8",
        )
        tasks.append(
            EvidenceTask(
                index=idx,
                task_id=task_id,
                path=path,
                units=units,
                missing_units=assigned_missing,
            )
        )
    return tasks
