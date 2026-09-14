"""Source-sized Talend requirements and bounded, addressable analysis packets."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from mcp_server.providers.custom.dependency_evidence import DependencyEvidenceRepository, canonical_job_id, load_scan
from mcp_server.providers.custom.knowledge_contracts import (
    LANDSCAPE_ROOT, KnowledgeEvidence, KnowledgeManifest, KnowledgePlan,
    KnowledgeRequirement, KnowledgeTask, digest, safe_id, write_json,
)
from mcp_server.providers.custom.models import FactsManifest


def _json_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _fragments(text: str, max_bytes: int) -> Iterator[str]:
    """Lossless UTF-8 fragments, including an individual overlong source line."""
    current: list[str] = []
    size = 0
    for char in text:
        width = len(char.encode("utf-8"))
        if current and size + width > max_bytes:
            yield "".join(current)
            current, size = [], 0
        current.append(char)
        size += width
    if current:
        yield "".join(current)


def _records(job: dict, prefix: str) -> Iterator[tuple[str, str, Any]]:
    """Enumerate serialized source structures, without reinterpretation/parsing."""
    yield "identity", prefix, {key: job.get(key) for key in ("job_id", "name", "path", "source_root", "version", "artifact_kind")}
    for category in ("components", "connections", "tmap_data", "txmlmap_data", "tdm_maps", "context_groups", "trunjob_refs"):
        for index, record in enumerate(job.get(category, []) or []):
            locator = f"{prefix}/{category}/{index}"
            if category == "tmap_data":
                for key, value in record.items():
                    if isinstance(value, list):
                        for subindex, entry in enumerate(value):
                            yield "transformation", f"{locator}/{_json_pointer(key)}/{subindex}", entry
                    elif value is not None:
                        yield "transformation", f"{locator}/{_json_pointer(key)}", value
            else:
                concern = {"components": "component_behavior", "connections": "execution_flow", "txmlmap_data": "transformation",
                           "tdm_maps": "transformation", "context_groups": "configuration", "trunjob_refs": "dependencies"}[category]
                yield concern, locator, record


def prepare_landscape(*, staging_dir: Path, scan_output_dir: Path, facts: FactsManifest,
                      max_requirements_per_task: int = 12, max_evidence_bytes: int = 60000) -> KnowledgePlan:
    """Build the v2 shared evidence/requirement contract from a sanitized scan.

    Caller owns profile selection. This function does not start scans or models.
    Partition scope follows the exact canonical IDs in the provided manifest.
    """
    if facts.platform != "talend":
        raise ValueError("Talend knowledge profile requires Talend scan facts")
    if max_requirements_per_task < 1 or max_evidence_bytes < 1024:
        raise ValueError("positive task count and at least 1024 evidence bytes required")
    if len(facts.unit_names()) != len(facts.units):
        raise ValueError("ambiguous canonical unit identities in facts manifest")
    scan, scan_manifest = load_scan(scan_output_dir)
    if scan.get("platform", "talend") != "talend":
        raise ValueError("scan platform disagrees with Talend profile")
    repository = DependencyEvidenceRepository(scan, scan_manifest)
    snapshot = digest({"scan": scan, "manifest": scan_manifest, "contract": "talend-knowledge-v2"})
    evidence: dict[str, KnowledgeEvidence] = {}
    requirements: list[KnowledgeRequirement] = []
    unit_facts = {unit.name: unit for unit in facts.units}
    fragment_bytes = min(12000, max_evidence_bytes // 2)

    def add(unit_id: str, concern: str, locator: str, value: Any, *, owner: str | None = None,
            complete: bool = True, gap_stage: str = "", gap_reason: str = "",
            source_path: str = "scan_result.json",
            authority: str = "implemented_design", mandatory: bool = True,
            exact_preservation: bool = True) -> None:
        source = value if isinstance(value, str) else json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True)
        parts = list(_fragments(source, fragment_bytes)) or [""]
        for index, part in enumerate(parts):
            pointer = locator + (f";fragment={index + 1}/{len(parts)}" if len(parts) > 1 else "")
            eid = "ev-" + digest({"snapshot": snapshot, "owner": owner or unit_id, "locator": pointer, "text": part})[:24]
            evidence[eid] = KnowledgeEvidence(evidence_id=eid, snapshot_id=snapshot, owner_id=owner or unit_id,
                source_path=source_path, locator=pointer, text=part, sha256=digest(part),
                complete=complete, authority=authority)
            rid = "req-" + digest({"unit": unit_id, "concern": concern, "locator": pointer, "owner": owner or unit_id})[:24]
            requirements.append(KnowledgeRequirement(requirement_id=rid, unit_id=unit_id, group=unit_facts[unit_id].group,
                concern=concern, description=f"Explain {concern} at {pointer}" + (" with adjacent fragments; this is not the full structure" if len(parts) > 1 else ""),
                evidence_ids=[eid], extraction_status="extracted" if complete else "partial",
                gap_stage=gap_stage, gap_reason=gap_reason,
                mandatory=mandatory, exact_preservation=exact_preservation,
                output_destinations=[f"_evidence_registry/doc_slices/{safe_id(unit_id)}.md#{rid}"]))

    def supporting_excerpt(path: Path, names: list[str]) -> str:
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected: set[int] = set()
        lowered = [name.lower() for name in names if name]
        for index, line in enumerate(lines):
            if any(name in line.lower() for name in lowered):
                selected.update(range(max(0, index - 2), min(len(lines), index + 3)))
        return "\n".join(lines[index] for index in sorted(selected))

    found: set[str] = set()
    for index, job in enumerate(scan.get("jobs", [])):
        unit_id = canonical_job_id(job)
        if unit_id not in unit_facts:
            continue
        found.add(unit_id)
        for concern, locator, value in _records(job, f"/jobs/{index}"):
            add(unit_id, concern, locator, value)
        routines, gaps = repository.routine_dependencies(unit_id)
        for routine_index, routine in routines:
            complete = bool(routine.get("source_complete"))
            add(unit_id, "routine_behavior", f"/routines/{routine_index}/source_code", str(routine.get("source_code") or ""),
                owner=str(routine["routine_id"]), complete=complete,
                gap_stage="" if complete else "extraction", gap_reason="" if complete else "Legacy or partial routine source; explicit refresh required.")
        for gap_index, reason in enumerate(gaps):
            rid = "req-" + digest({"unit": unit_id, "gap": reason})[:24]
            requirements.append(KnowledgeRequirement(requirement_id=rid, unit_id=unit_id, group=unit_facts[unit_id].group,
                concern="routine_resolution", description=reason, extraction_status="unresolved", gap_stage="resolution", gap_reason=reason))
        display_name = unit_facts[unit_id].display_name or unit_id
        for artifact, concern, authority in (
            (staging_dir / "_analysis" / "_noncode_project_context.md", "documented_context", "documented_intent"),
            (staging_dir / "_analysis" / "_runtime_profile.md", "runtime_behavior", "runtime_observation"),
        ):
            excerpt = supporting_excerpt(artifact, [unit_id, display_name])
            if excerpt:
                add(unit_id, concern, f"/{concern}/{safe_id(unit_id)}", excerpt,
                    source_path=artifact.relative_to(staging_dir).as_posix(), authority=authority,
                    mandatory=False, exact_preservation=False)
        # Exported code cannot establish these facts. Preserve unanswered needs.
        for topic in ("business_purpose", "business_owner", "deployed_version"):
            requirements.append(KnowledgeRequirement(requirement_id="req-" + digest({"unit": unit_id, "topic": topic})[:24],
                unit_id=unit_id, group=unit_facts[unit_id].group, concern=topic,
                description=f"Establish {topic} from supplied intent/runtime evidence.",
                extraction_status="unresolved", gap_stage="source", gap_reason="Requires supporting evidence beyond the exported design.",
                mandatory=False, exact_preservation=False))
    if set(unit_facts) - found:
        raise ValueError("canonical units absent from scan: " + ", ".join(sorted(set(unit_facts) - found)))
    knowledge = KnowledgeManifest(snapshot_id=snapshot, project_id=facts.project_id, requirements=requirements, evidence=evidence)
    tasks: list[KnowledgeTask] = []
    for unit_id in unit_facts:
        selected = [r for r in requirements if r.unit_id == unit_id and r.extraction_status == "extracted"]
        batches: list[list[KnowledgeRequirement]] = []
        batch: list[KnowledgeRequirement] = []
        size = 0
        for requirement in selected:
            requirement_bytes = sum(len(evidence[e].text.encode("utf-8")) for e in requirement.evidence_ids)
            if batch and (len(batch) >= max_requirements_per_task or size + requirement_bytes > max_evidence_bytes):
                batches.append(batch)
                batch, size = [], 0
            batch.append(requirement)
            size += requirement_bytes
        if batch:
            batches.append(batch)
        for ordinal, batch in enumerate(batches):
            task_id = f"knowledge_{safe_id(unit_id)}_{ordinal:04d}"
            task = KnowledgeTask(task_id=task_id, snapshot_id=snapshot, group=unit_facts[unit_id].group, unit_ids=[unit_id],
                requirement_ids=[r.requirement_id for r in batch], evidence_ids=sorted({eid for r in batch for eid in r.evidence_ids}),
                input_artifact=(LANDSCAPE_ROOT / "tasks" / f"{task_id}.json").as_posix(),
                output_artifact=(LANDSCAPE_ROOT / "claims" / f"{task_id}.json").as_posix())
            write_json(staging_dir / task.input_artifact, {"task": task.model_dump(), "requirements": [r.model_dump() for r in batch],
                "evidence": [evidence[eid].model_dump() for eid in task.evidence_ids]})
            tasks.append(task)
    plan = KnowledgePlan(snapshot_id=snapshot, tasks=tasks, manifest=knowledge)
    previous_path = staging_dir / LANDSCAPE_ROOT / "requirements.json"
    if previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        if previous.get("snapshot_id") != snapshot:
            raise ValueError("staging knowledge snapshot changed; start a new generation attempt")
    write_json(previous_path, knowledge)
    write_json(staging_dir / LANDSCAPE_ROOT / "plan.json", plan)
    write_json(
        staging_dir / LANDSCAPE_ROOT / "source_inventory.json",
        scan.get("source_inventory", []),
    )
    _write_reference_documents(staging_dir, knowledge)
    return plan


def _write_reference_documents(staging_dir: Path, manifest: KnowledgeManifest) -> None:
    """Publish lossless exact facts separately from authored explanations."""
    root = staging_dir / "_evidence_registry" / "doc_slices"
    root.mkdir(parents=True, exist_ok=True)
    units = sorted({requirement.unit_id for requirement in manifest.requirements})
    index = ["# Talend Landscape Source References", "",
             "Exact sanitized scan evidence. Authored documents summarize and link to these records.", ""]
    for unit_id in units:
        filename = f"{safe_id(unit_id)}.md"
        index.append(f"- [{unit_id}]({filename})")
        lines = [f"# Source Reference: {unit_id}", "",
                 f"Snapshot: `{manifest.snapshot_id}`", ""]
        for requirement in (r for r in manifest.requirements if r.unit_id == unit_id):
            lines.extend([f'<a id="{requirement.requirement_id}"></a>',
                          f"## {requirement.concern}: {requirement.requirement_id}", "",
                          f"Status: {requirement.extraction_status}; stage: {requirement.gap_stage or 'none'}", "",
                          requirement.description, ""])
            if requirement.gap_reason:
                lines.extend([f"Gap: {requirement.gap_reason}", ""])
            for evidence_id in requirement.evidence_ids:
                evidence = manifest.evidence[evidence_id]
                fence = "`" * max(3, 1 + max((len(run) for run in __import__("re").findall(r"`+", evidence.text)), default=0))
                lines.extend([f"Evidence `{evidence_id}` from `{evidence.source_path}` at `{evidence.locator}`:", "",
                              fence + "text", evidence.text, fence, ""])
        (root / filename).write_text("\n".join(lines), encoding="utf-8")
    (root / "landscape_index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
