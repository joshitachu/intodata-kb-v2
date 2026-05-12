from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import ModelResult
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES


# ---------- scan / job helpers (copied from test_custom_provider.py) ----------

def _write_scan(out_dir: Path, *, platform: str, jobs: list[dict], files: list[dict] | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scan_result.json").write_text(json.dumps({
        "platform": platform,
        "repo_root": "/repo",
        "files": files or [],
        "jobs": jobs,
        "manifest": {},
        "warnings": [],
    }), encoding="utf-8")


def _job(name: str, group: str = "A", component_type: str = "tMap") -> dict:
    return {
        "name": name,
        "path": f"{name}.item",
        "group": group,
        "components": [{"unique_name": f"{component_type}_1", "component_type": component_type}],
    }


# ---------- fake provider (copied from test_custom_provider.py) ----------

class _FakeProvider:
    model_name = "fake/model"
    calls: list[dict] | None = None

    async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
        if self.calls is not None:
            self.calls.append({
                "prompt": prompt,
                "context_names": [p.name for p in context_files or []],
                "options": options,
            })

        if "Allowed actions:" in prompt and '"action":"finish"' in prompt:
            if "Latest tool observation JSON" not in prompt:
                match = re.search(r"Primary group file: ([^\n]+)", prompt)
                path = match.group(1).strip() if match else "_preprocess/Recipes.md"
                return ModelResult(
                    success=True,
                    text=json.dumps({"action": "read_file", "path": path, "offset": 0, "max_bytes": 4000}),
                    model_name=self.model_name,
                    input_tokens=10,
                    output_tokens=20,
                )
            units_block = prompt.split("Allowed canonical units:", 1)[1].split("\n\n", 1)[0]
            units = [line[2:].strip() for line in units_block.splitlines() if line.startswith("- ")]
            source_match = re.search(r"Primary group file: ([^\n]+)", prompt)
            source = source_match.group(1).strip() if source_match else "_preprocess/Recipes.md"
            evidence = []
            for unit in units:
                evidence.append({
                    "unit_name": unit,
                    "category": "inventory",
                    "source_file": source,
                    "line_range": "1-3",
                    "snippet": f"{unit} scanner inventory",
                    "observation": f"{unit} appears in the preprocessed scanner inventory for this group.",
                })
            return ModelResult(
                success=True,
                text=json.dumps({"action": "finish", "evidence": evidence, "skipped_units": {}}),
                model_name=self.model_name,
                input_tokens=20,
                output_tokens=40,
            )

        if "Return ONLY a valid JSON object" in prompt and '"evidence"' in prompt:
            units_block = prompt.split("Units to cover:", 1)[1].split("\n\n", 1)[0]
            units = [line[2:].strip() for line in units_block.splitlines() if line.startswith("- ")]
            source_match = re.search(r"Source file: ([^\n]+)", prompt)
            source = source_match.group(1).strip() if source_match else "_preprocess/Recipes.md"
            evidence = []
            for unit in units:
                evidence.append({
                    "unit_name": unit,
                    "category": "inventory",
                    "source_file": source,
                    "line_range": "1-3",
                    "snippet": unit,
                    "observation": f"{unit} appears in the supplied scan data with concrete inventory detail.",
                })
            return ModelResult(
                success=True,
                text=json.dumps({"evidence": evidence, "skipped_units": {}}),
                model_name=self.model_name,
                input_tokens=12,
                output_tokens=24,
            )

        if "You are a documentation critic" in prompt:
            return ModelResult(
                success=True,
                text='{"expansions":[]}',
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=2,
            )

        deep_text = (
            "# Deep Generated Analysis\n\n"
            "Canonical inventory: 2 recipes/functions. Source artifacts: Recipe One.item, Function One.item.\n"
            "Platform evidence includes recipe, callable function, folder, export artifact, trigger, step, action, block, "
            "datapill, formula, lookup table, property, connection, monitor, job history.\n"
            "Score: 3/5. Confidence: HIGH. Evidence: facts_manifest.json. Red Flags: HIGH risk. Quick Wins: add tests. Unknowns: runtime SLA.\n"
            "Change Impact: inspect source artifacts, Test changed flows, Verify outputs, Rollback if validation fails.\n"
            "Documentation Map, Onboarding, Common Tasks, Known Traps, Glossary. Debug and Recovery guidance with Testing evidence.\n"
            "Topology, Boundaries, Dependencies, Entry Points, External Systems. Source, Target, Transformation, Schema, Validation, Scanner-Limited.\n"
            "Expression, Unit, Domain, Impact. Config, Redacted, Scanner-Limited.\n"
            "## Inventory and purpose\n"
            "Recipe One and Function One are covered as canonical units in the Recipes and Functions groups.\n"
            "| Unit | Group | Source Artifact | Type | Purpose | Dependencies | Confidence |\n"
            "|------|-------|-----------------|------|---------|--------------|------------|\n"
            "| Recipe One | Recipes | Recipe One.item | recipe | purpose | dependency | HIGH |\n"
            "| Function One | Functions | Function One.item | function | purpose | dependency | HIGH |\n"
            "## Components, actions, or implementation units\n"
            "Components and actions are described with component evidence and connector behavior.\n"
            "## Data flows and lineage\n"
            "Data flow details describe source systems, transformation, and target systems for each group.\n"
            "## Transformation and business logic\n"
            "Business rule evidence, transformation logic, and implementation rules are documented.\n"
            "## External systems and context/configuration\n"
            "External systems, context values, configuration, and properties are documented.\n"
            "## Operations, monitoring, and error handling\n"
            "Operation, execution, monitoring, retry, and error handling details are documented.\n"
            "## Security risks\n"
            "Security, credential, access, risk, severity, and mitigation details are documented.\n"
            "## Migration risks and recommendations\n"
            "Migration readiness, impact, testing, recovery, runbook, troubleshooting, FAQ, glossary, "
            "pattern, standard, anti-pattern, architecture, connectivity, component, environment, "
            "key findings, recommendations, scope, groups, lineage, inventory, scanner limitations, "
            "executive overview, access, credential, severity, mitigation, onboarding, plan.\n"
            "## Scanner limitations\n"
            "Scanner limitations are called out where source detail is incomplete.\n"
        )
        for section in ANALYSIS_SECTION_NAMES:
            deep_text += f"\n### {section}\n"
            deep_text += (
                f"{section} evidence for Recipe One and Function One includes component, data flow, "
                "business, external, operation, security, migration, and scanner detail.\n"
            )
        deep_text += "\n### GROUP_SUMMARY\nUnits covered: Recipe One, Function One. Top risks and flows summarized.\n"
        deep_text = deep_text + ("\nDetailed evidence paragraph for Recipe One and Function One. " * 260)
        return ModelResult(
            success=True,
            text=deep_text,
            model_name=self.model_name,
            input_tokens=10,
            output_tokens=2500,
            metadata={
                "estimated_input_tokens": 10,
                "context_bytes": sum(p.stat().st_size for p in context_files or [] if p.exists()),
                "provider_options": (options.provider_options if options else {}),
            },
        )


# ---------- concurrency-tracking provider ----------

class _TrackedFakeProvider(_FakeProvider):
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        # Yield to the event loop so concurrently-dispatched coroutines can start
        # before any one of them finishes.  Without this yield, tasks complete
        # without interleaving and max_active stays 1 even with asyncio.gather.
        await asyncio.sleep(0.01)
        result = await super().complete(prompt, context_files, options)
        self.active -= 1
        return result


# ---------- staging setup ----------

_PREPROCESS_REF_FILES = [
    "_scoring_rubric.md",
    "_anti_patterns.md",
    "_red_flags.md",
    "_security_checklist.md",
    "_pattern_library.md",
    "_ref_core.md",
    "_ref_business_ops.md",
    "_ref_patterns.md",
    "_ref_user.md",
]


def _make_staging(tmp_path: Path, project_id: str, jobs: list[dict], platform: str) -> DocGenContext:
    staging = tmp_path / "output" / project_id / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_analysis").mkdir()
    (staging / "_assessment").mkdir()

    groups = sorted({j["group"] for j in jobs})
    for group in groups:
        group_jobs = [j["name"] for j in jobs if j["group"] == group]
        (staging / "_preprocess" / f"{group}.md").write_text("\n".join(group_jobs), encoding="utf-8")

    (staging / "_preprocess" / "_analysis_guide.md").write_text("Workato analysis guide", encoding="utf-8")
    (staging / "_preprocess" / "_phase3_guide.md").write_text("Workato phase 3 guide", encoding="utf-8")
    for name in _PREPROCESS_REF_FILES:
        (staging / "_preprocess" / name).write_text(f"{name} content", encoding="utf-8")
    (staging / "_assessment" / "quick_scan.md").write_text("quick scan facts", encoding="utf-8")

    return DocGenContext(
        project_id=project_id,
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=groups,
        total_jobs=len(jobs),
        total_groups=len(groups),
        effective_model="fake/model",
        write_model="fake/model",
        template_snapshot={},
        platform=platform,
        analysis_guide_file="_analysis_guide.md",
    )


# ---------- tests ----------

_WORKATO_JOBS = [
    _job("Recipe One", "Recipes", "WorkatoRecipe"),
    _job("Function One", "Functions", "WorkatoRecipeFunction"),
]


@pytest.mark.asyncio
async def test_phase2_groups_run_concurrently(tmp_path, monkeypatch):
    """Phase 2 group analysis tasks dispatch in parallel, not one-at-a-time."""
    from mcp_server.config import config

    project_id = "perf-concurrent"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="workato", jobs=_WORKATO_JOBS)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    monkeypatch.setattr(config, "model_pricing_json", '{"fake/model":{"input":1,"output":2}}')
    monkeypatch.setattr(config, "agent_phase2_concurrency", 4)

    ctx = _make_staging(tmp_path, project_id, _WORKATO_JOBS, "workato")
    logs: list = []

    async def log_cb(pid: str, message: str, level: str = "info") -> None:
        logs.append((pid, message, level))

    provider = _TrackedFakeProvider()
    result = await CustomHarnessProvider(provider).run(ctx, log_cb)

    assert result.success
    assert provider.max_active > 1      # phases actually ran in parallel
    assert provider.max_active <= 4     # bounded by the concurrency semaphore


@pytest.mark.asyncio
async def test_full_harness_completes_within_time_budget(tmp_path, monkeypatch):
    """No phase blocks the event loop; full run with instant provider stays well under 5 s."""
    from mcp_server.config import config

    project_id = "perf-budget"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="workato", jobs=_WORKATO_JOBS)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    monkeypatch.setattr(config, "model_pricing_json", '{"fake/model":{"input":1,"output":2}}')

    ctx = _make_staging(tmp_path, project_id, _WORKATO_JOBS, "workato")
    logs: list = []

    async def log_cb(pid: str, message: str, level: str = "info") -> None:
        logs.append((pid, message, level))

    result = await CustomHarnessProvider(_FakeProvider()).run(ctx, log_cb)

    assert result.success
    assert result.duration_ms is not None
    assert result.duration_ms < 5_000   # 5 s cap — instant provider finishes in <1 s
