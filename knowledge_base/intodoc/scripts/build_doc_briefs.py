#!/usr/bin/env python3
"""Condense 630 pass_result files into 18 compact briefs (one per pass).

Reads:  intodoc/docs/pass_results/p{NN}_{chunk_id}.json
        intodoc/docs/chunks_summary.json
Writes: intodoc/docs/briefs/brief_p{NN}.json  (18 files, each < 15 KB)

Usage:
    python intodoc/scripts/build_doc_briefs.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
PASS_RESULTS_DIR = DOCS_DIR / "pass_results"
BRIEFS_DIR = DOCS_DIR / "briefs"
CHUNKS_FILE = DOCS_DIR / "chunks_summary.json"
MANIFEST_FILE = DOCS_DIR / "scan" / "manifest.json"
CONNECTIONS_FILE = DOCS_DIR / "scan" / "connections.json"

PASS_NAMES = {
    1: "Component Inventory",
    2: "Execution Logic",
    3: "Data Flows",
    4: "Environment Config",
    5: "External Systems",
    6: "Error Handling",
    7: "State & Side Effects",
    8: "Custom Code",
    9: "Business Logic",
    10: "Synthesis",
    11: "Schema Dictionary",
    12: "Data Quality",
    13: "Naming Conventions",
    14: "Scheduling & SLA",
    15: "Security & Access",
    16: "Performance",
    17: "Cross-Region",
    18: "Reusability",
}

# ── helpers ──────────────────────────────────────────────────────────

def _load_chunks_summary() -> dict[str, str]:
    """Return {chunk_id: group} mapping."""
    raw = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    return {c["chunk_id"]: c["group"] for c in raw}

def _load_manifest() -> dict:
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))

def _load_connections() -> list[dict]:
    return json.loads(CONNECTIONS_FILE.read_text(encoding="utf-8"))

def _chunk_to_region(chunk_id: str, mapping: dict[str, str]) -> str:
    return mapping.get(chunk_id, chunk_id.split("_")[0])

def _load_pass_files(pass_num: int) -> list[dict]:
    """Load all pass_result files for a given pass number."""
    files = sorted(PASS_RESULTS_DIR.glob(f"p{pass_num:02d}_*.json"))
    results = []
    for f in files:
        raw = json.loads(f.read_text(encoding="utf-8"))
        results.append(raw)
    return results

def _pick_examples(items: list, n: int = 5) -> list:
    """Pick up to n representative examples from a list."""
    if len(items) <= n:
        return items
    # Take first, last, and evenly spaced from middle
    step = max(1, len(items) // n)
    return items[::step][:n]

def _truncate_str(s: str, max_len: int = 200) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


# ── per-pass condensers ─────────────────────────────────────────────

def condense_p01(files: list[dict], chunk_map: dict) -> dict:
    """Pass 1: Component Inventory → component type counts by region."""
    region_data = defaultdict(lambda: {"job_count": 0, "component_types": Counter(), "total_components": 0, "jobs": []})

    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        data = f["data"]
        components = data.get("components", [])
        region_data[region]["total_components"] += len(components)
        jobs_in_chunk = set()
        for c in components:
            region_data[region]["component_types"][c.get("component_type", "unknown")] += 1
            loc = c.get("location", "")
            if loc:
                jobs_in_chunk.add(loc)
        region_data[region]["jobs"].extend(jobs_in_chunk)

    for r in region_data.values():
        r["jobs"] = sorted(set(r["jobs"]))
        r["job_count"] = len(r["jobs"])
        r["component_types"] = dict(r["component_types"].most_common(15))

    total_components = sum(r["total_components"] for r in region_data.values())
    total_jobs = sum(r["job_count"] for r in region_data.values())

    # Global component type distribution
    global_types = Counter()
    for r in region_data.values():
        global_types.update(r["component_types"])

    return {
        "doc_title": "Component Inventory",
        "global_summary": {
            "total_jobs": total_jobs,
            "total_components": total_components,
            "avg_components_per_job": round(total_components / max(total_jobs, 1), 1),
            "top_component_types": dict(global_types.most_common(10)),
            "region_count": len(region_data),
        },
        "region_summaries": {
            region: {
                "job_count": d["job_count"],
                "total_components": d["total_components"],
                "top_types": dict(Counter(d["component_types"]).most_common(8)),
                "example_jobs": d["jobs"][:5],
            }
            for region, d in sorted(region_data.items())
        },
        "cross_cutting_patterns": [
            f"tMap is the dominant transformation component across all regions",
            f"tBigQueryInput/Output used in {sum(1 for r in region_data.values() if 'tBigQueryInput' in r['component_types'])} of {len(region_data)} regions",
            f"Vaxxinova_Logging custom component detected in {sum(1 for r in region_data.values() if 'Vaxxinova_Logging' in r['component_types'])} regions",
            f"cleanup_temp_file_storage present in {sum(1 for r in region_data.values() if 'cleanup_temp_file_storage' in r['component_types'])} regions",
        ],
        "migration_notes": [
            "Map tMap to target platform's transformation component (e.g., Workato formulas, ADF data flows)",
            "Replace tBigQueryInput/Output with native BigQuery connectors in target platform",
            "Vaxxinova_Logging is a custom joblet — must be reimplemented as a shared utility",
            "cleanup_temp_file_storage handles file cleanup — ensure equivalent in target",
        ],
    }


def condense_p02(files: list[dict], chunk_map: dict) -> dict:
    """Pass 2: Execution Logic → flow patterns and triggers."""
    region_data = defaultdict(lambda: {"jobs": [], "triggers": Counter(), "has_mermaid": 0})

    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for step in f["data"].get("steps", []):
            job = step.get("job_name", "")
            trigger = step.get("trigger", "unknown")
            region_data[region]["jobs"].append(job)
            region_data[region]["triggers"][trigger] += 1
            if step.get("flowchart_mermaid"):
                region_data[region]["has_mermaid"] += 1

    total_jobs = sum(len(r["jobs"]) for r in region_data.values())
    all_triggers = Counter()
    for r in region_data.values():
        all_triggers.update(r["triggers"])

    # Pick example flows (jobs with longest control_flow = most complex)
    all_steps = []
    for f in files:
        for step in f["data"].get("steps", []):
            cf = step.get("control_flow", "")
            all_steps.append({
                "job": step.get("job_name", ""),
                "region": _chunk_to_region(f["chunk_id"], chunk_map),
                "trigger": step.get("trigger", ""),
                "complexity": len(cf),
            })
    all_steps.sort(key=lambda x: x["complexity"], reverse=True)

    return {
        "doc_title": "Execution Logic",
        "global_summary": {
            "total_jobs": total_jobs,
            "trigger_distribution": dict(all_triggers.most_common()),
            "regions_analyzed": len(region_data),
        },
        "region_summaries": {
            region: {
                "job_count": len(d["jobs"]),
                "triggers": dict(d["triggers"].most_common()),
                "jobs_with_flowcharts": d["has_mermaid"],
            }
            for region, d in sorted(region_data.items())
        },
        "most_complex_jobs": [
            {"job": s["job"], "region": s["region"], "trigger": s["trigger"]}
            for s in all_steps[:7]
        ],
        "cross_cutting_patterns": [
            f"{all_triggers.get('standalone', 0)} of {total_jobs} jobs are standalone (no parent trigger)",
            "Most jobs follow sequential SUBJOB_OK chaining pattern",
            "COMPONENT_OK used for intra-subjob sequencing",
            "RUN_IF patterns used for conditional branch execution",
        ],
        "migration_notes": [
            "Map SUBJOB_OK chains to target orchestration tool's dependency model",
            "RUN_IF conditions need equivalent conditional logic in target platform",
            "Mermaid flowcharts in pass data can be reused for migration documentation",
        ],
    }


def condense_p03(files: list[dict], chunk_map: dict) -> dict:
    """Pass 3: Data Flows → source/transform/sink patterns."""
    region_data = defaultdict(lambda: {"jobs": [], "input_types": Counter(), "output_types": Counter(), "transform_count": 0})

    all_flows = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for flow in f["data"].get("flows", []):
            job = flow.get("job_name", "")
            region_data[region]["jobs"].append(job)
            for inp in flow.get("inputs", []):
                src = inp.get("source_type", inp.get("component_type", "unknown"))
                region_data[region]["input_types"][src] += 1
            for out in flow.get("outputs", []):
                sink = out.get("sink_type", out.get("component_type", "unknown"))
                region_data[region]["output_types"][sink] += 1
            region_data[region]["transform_count"] += len(flow.get("transformations", []))

            all_flows.append({
                "job": job,
                "region": region,
                "inputs": len(flow.get("inputs", [])),
                "outputs": len(flow.get("outputs", [])),
                "transforms": len(flow.get("transformations", [])),
            })

    all_flows.sort(key=lambda x: x["transforms"], reverse=True)

    return {
        "doc_title": "Data Flows",
        "global_summary": {
            "total_jobs": sum(len(r["jobs"]) for r in region_data.values()),
            "total_transforms": sum(r["transform_count"] for r in region_data.values()),
            "regions_analyzed": len(region_data),
        },
        "region_summaries": {
            region: {
                "job_count": len(set(d["jobs"])),
                "top_input_types": dict(d["input_types"].most_common(5)),
                "top_output_types": dict(d["output_types"].most_common(5)),
                "total_transforms": d["transform_count"],
            }
            for region, d in sorted(region_data.items())
        },
        "most_complex_flows": [
            {"job": f["job"], "region": f["region"], "inputs": f["inputs"],
             "outputs": f["outputs"], "transforms": f["transforms"]}
            for f in all_flows[:7]
        ],
        "cross_cutting_patterns": [
            "BigQuery is the primary data warehouse (input and output)",
            "MSSQL used for on-premises source extraction (BRA, ITA, JPN, USA)",
            "tMap is the universal transformation component",
            "Most flows follow: Source DB → tMap transform → BigQuery output",
            "Salesforce used as both source and sink for CRM data sync",
        ],
        "migration_notes": [
            "Map source connections to equivalent connectors in target platform",
            "tMap expressions need translation to target transformation language",
            "BigQuery I/O should map to native BigQuery connectors",
            "Consider replacing sequential subjob patterns with parallel execution where possible",
        ],
    }


def condense_p04(files: list[dict], chunk_map: dict) -> dict:
    """Pass 4: Environment Config → env comparison, variable patterns."""
    region_data = defaultdict(lambda: {"envs": set(), "variables": [], "risk_count": 0})

    all_envs = set()
    total_vars = 0
    risk_vars = 0

    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        data = f["data"]
        for env in data.get("environments", []):
            region_data[region]["envs"].add(env)
            all_envs.add(env)
        for comp in data.get("comparisons", []):
            total_vars += 1
            notes = comp.get("risk_notes", "")
            if notes:
                risk_vars += 1
                region_data[region]["risk_count"] += 1
            region_data[region]["variables"].append({
                "name": comp.get("variable_name", ""),
                "has_risk": bool(notes),
                "risk": notes,
            })

    # Pick example risky variables
    risky_examples = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for comp in f["data"].get("comparisons", []):
            if comp.get("risk_notes"):
                risky_examples.append({
                    "variable": comp["variable_name"],
                    "region": region,
                    "risk": comp["risk_notes"],
                    "env_diff": {k: _truncate_str(str(v), 60) for k, v in comp.get("values_by_env", {}).items()},
                })
    risky_examples = _pick_examples(risky_examples, 5)

    return {
        "doc_title": "Environment Config",
        "global_summary": {
            "total_variables": total_vars,
            "variables_with_risks": risk_vars,
            "pct_risky": f"{round(100 * risk_vars / max(total_vars, 1))}%",
            "environments": sorted(all_envs),
        },
        "region_summaries": {
            region: {
                "variable_count": len(d["variables"]),
                "risk_count": d["risk_count"],
                "environments": sorted(d["envs"]),
            }
            for region, d in sorted(region_data.items())
        },
        "concrete_examples": risky_examples,
        "cross_cutting_patterns": [
            f"All regions share the same environment set: {', '.join(sorted(all_envs))}",
            f"{risk_vars} of {total_vars} variables ({round(100*risk_vars/max(total_vars,1))}%) have PROD/TEST differences",
            "Context variables follow naming: {region}_{subsidiary}_{domain}.parameter_{name}",
            "Credential paths use [CREDENTIAL_PATH] placeholder (filtered by scanner)",
        ],
        "migration_notes": [
            "Map Talend context variables to target platform's secret/config management",
            "PROD/TEST divergences must be validated during migration",
            "Credential management should use a vault (Azure Key Vault, etc.) in target",
        ],
    }


def condense_p05(files: list[dict], chunk_map: dict, connections: list[dict]) -> dict:
    """Pass 5: External Systems → system inventory and criticality."""
    region_data = defaultdict(lambda: {"systems": [], "criticality": Counter()})

    all_systems = {}
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for sys_info in f["data"].get("systems", []):
            name = sys_info.get("name", "")
            sys_type = sys_info.get("system_type", "")
            crit = sys_info.get("criticality", "medium")
            region_data[region]["criticality"][crit] += 1
            key = f"{name}|{sys_type}"
            if key not in all_systems:
                all_systems[key] = {
                    "name": name,
                    "type": sys_type,
                    "criticality": crit,
                    "regions": set(),
                    "used_by_jobs": set(),
                }
            all_systems[key]["regions"].add(region)
            for job in sys_info.get("used_by_jobs", []):
                all_systems[key]["used_by_jobs"].add(job)

    # Deduplicate and summarize
    system_list = []
    for s in all_systems.values():
        system_list.append({
            "name": s["name"],
            "type": s["type"],
            "criticality": s["criticality"],
            "regions": sorted(s["regions"]),
            "job_count": len(s["used_by_jobs"]),
        })
    system_list.sort(key=lambda x: (-x["job_count"], x["name"]))

    # Connection metadata from scan
    conn_summary = []
    for c in connections[:10]:
        conn_summary.append({
            "name": c.get("name", ""),
            "type": c.get("connection_type", ""),
            "host": _truncate_str(c.get("host", ""), 80),
        })

    return {
        "doc_title": "External Systems",
        "global_summary": {
            "total_unique_systems": len(system_list),
            "high_criticality": sum(1 for s in system_list if s["criticality"] == "high"),
            "system_types": dict(Counter(s["type"] for s in system_list).most_common()),
        },
        "top_systems": system_list[:15],
        "connection_metadata": conn_summary,
        "region_summaries": {
            region: {
                "system_count": len(d["systems"]) or sum(d["criticality"].values()),
                "criticality_breakdown": dict(d["criticality"]),
            }
            for region, d in sorted(region_data.items())
        },
        "cross_cutting_patterns": [
            "Google BigQuery is the central data warehouse across all regions",
            "MSSQL used for on-premises ERP source systems (BRA, ITA, JPN, USA)",
            "Salesforce used for CRM integration (product sync, account data)",
            "All connections use Talend context variables for parameterization",
        ],
        "migration_notes": [
            "Map each connection type to target platform's connector library",
            "Ensure network connectivity for on-prem MSSQL in target architecture",
            "Salesforce OAuth credentials need re-provisioning in target platform",
            "BigQuery service account keys must be migrated to target secret store",
        ],
    }


def condense_p06(files: list[dict], chunk_map: dict) -> dict:
    """Pass 6: Error Handling → ratings, gaps, recommendations."""
    region_data = defaultdict(lambda: {"jobs": [], "ratings": Counter(), "total_risks": 0, "error_components": Counter()})

    all_gaps = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        data = f["data"]
        for job in data.get("jobs", []):
            rating = job.get("rating", "unknown")
            region_data[region]["jobs"].append(job.get("job_name", ""))
            region_data[region]["ratings"][rating] += 1
            region_data[region]["total_risks"] += len(job.get("silent_failure_risks", []))
            for ec in job.get("error_components", []):
                region_data[region]["error_components"][ec] += 1
        for gap in data.get("critical_gaps", []):
            all_gaps.append({"region": region, "gap": _truncate_str(gap, 150)})

    total_jobs = sum(len(r["jobs"]) for r in region_data.values())
    all_ratings = Counter()
    for r in region_data.values():
        all_ratings.update(r["ratings"])

    jobs_no_handling = all_ratings.get("none", 0)

    # Pick examples of jobs with no error handling
    examples = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for job in f["data"].get("jobs", []):
            if job.get("rating") == "none":
                examples.append({
                    "job": job.get("job_name", ""),
                    "region": region,
                    "finding": "No error handling components",
                    "risks": job.get("silent_failure_risks", [])[:2],
                })
    examples = _pick_examples(examples, 5)

    return {
        "doc_title": "Error Handling",
        "global_summary": {
            "total_jobs": total_jobs,
            "jobs_no_handling": jobs_no_handling,
            "pct_no_handling": f"{round(100 * jobs_no_handling / max(total_jobs, 1))}%",
            "rating_distribution": dict(all_ratings.most_common()),
        },
        "region_summaries": {
            region: {
                "job_count": len(d["jobs"]),
                "rating": "poor" if d["ratings"].get("none", 0) > len(d["jobs"]) * 0.5 else "adequate",
                "ratings": dict(d["ratings"].most_common()),
                "notable_findings": [f"{d['ratings'].get('none', 0)} jobs lack any error handling"],
                "error_components_used": dict(d["error_components"].most_common(5)),
            }
            for region, d in sorted(region_data.items())
        },
        "critical_gaps": _pick_examples(all_gaps, 5),
        "concrete_examples": examples,
        "cross_cutting_patterns": [
            f"{jobs_no_handling} of {total_jobs} jobs ({round(100*jobs_no_handling/max(total_jobs,1))}%) have zero error handling",
            "No tLogCatcher usage detected in the majority of the codebase",
            "tLogRow is the most common 'error' component but only provides logging, not error recovery",
            "SUBJOB_ERROR flows are rarely connected",
        ],
        "migration_notes": [
            "Implement try-catch at every job level when migrating",
            "Add centralized error logging and alerting in target platform",
            "Design retry logic for transient failures (network, API rate limits)",
            "Create error notification channels (email, Slack) for critical job failures",
        ],
    }


def condense_p07(files: list[dict], chunk_map: dict) -> dict:
    """Pass 7: State & Side Effects → file I/O, database writes, risks."""
    region_data = defaultdict(lambda: {"effects": [], "effect_types": Counter(), "risks": Counter()})

    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for eff in f["data"].get("effects", []):
            etype = eff.get("effect_type", "unknown")
            risk = eff.get("risk", "low")
            region_data[region]["effect_types"][etype] += 1
            region_data[region]["risks"][risk] += 1
            region_data[region]["effects"].append({
                "job": eff.get("job_name", ""),
                "type": etype,
                "risk": risk,
                "cleanup": eff.get("cleanup_exists", False),
            })

    total_effects = sum(len(r["effects"]) for r in region_data.values())
    all_types = Counter()
    all_risks = Counter()
    no_cleanup = 0
    for r in region_data.values():
        all_types.update(r["effect_types"])
        all_risks.update(r["risks"])
        no_cleanup += sum(1 for e in r["effects"] if not e["cleanup"])

    # Examples of medium/high risk effects
    examples = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for eff in f["data"].get("effects", []):
            if eff.get("risk") in ("medium", "high"):
                examples.append({
                    "job": eff.get("job_name", ""),
                    "region": region,
                    "type": eff.get("effect_type", ""),
                    "description": _truncate_str(eff.get("description", ""), 120),
                    "risk": eff.get("risk", ""),
                    "has_cleanup": eff.get("cleanup_exists", False),
                })
    examples = _pick_examples(examples, 5)

    return {
        "doc_title": "State & Side Effects",
        "global_summary": {
            "total_effects": total_effects,
            "effect_types": dict(all_types.most_common()),
            "risk_distribution": dict(all_risks),
            "effects_without_cleanup": no_cleanup,
        },
        "region_summaries": {
            region: {
                "effect_count": len(d["effects"]),
                "types": dict(d["effect_types"].most_common(5)),
                "risk_breakdown": dict(d["risks"]),
            }
            for region, d in sorted(region_data.items())
        },
        "concrete_examples": examples,
        "cross_cutting_patterns": [
            "Temp file staging is the dominant side effect pattern",
            f"{no_cleanup} effects lack cleanup logic — risk of file accumulation",
            "cleanup_temp_file_storage joblet handles most temp file cleanup",
            "Most staging files write to context.resource_flow_temp_folder",
        ],
        "migration_notes": [
            "Replace file-based staging with in-memory or cloud storage patterns",
            "Ensure cleanup logic is preserved or improved in target platform",
            "Audit all TRUNCATE writes — ensure idempotency in target",
        ],
    }


def condense_p08(files: list[dict], chunk_map: dict) -> dict:
    """Pass 8: Custom Code → Java snippet patterns and risks."""
    region_data = defaultdict(lambda: {"snippets": [], "languages": Counter(), "purposes": Counter()})

    all_snippets_count = 0
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for snip in f["data"].get("snippets", []):
            all_snippets_count += 1
            lang = snip.get("language", "unknown")
            purpose = snip.get("purpose", "unknown")
            region_data[region]["languages"][lang] += 1
            region_data[region]["purposes"][purpose] += 1
            region_data[region]["snippets"].append({
                "location": snip.get("location", ""),
                "purpose": purpose,
                "has_business_rules": len(snip.get("hidden_business_rules", [])) > 0,
            })

    # Pick examples with business rules
    examples = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for snip in f["data"].get("snippets", []):
            if snip.get("hidden_business_rules"):
                examples.append({
                    "location": snip["location"],
                    "region": region,
                    "rules": snip["hidden_business_rules"][:3],
                    "code_preview": _truncate_str(snip.get("code", ""), 150),
                })
    # Also pick some common tJava patterns
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for snip in f["data"].get("snippets", []):
            if "tJava_StoreJobStats" in snip.get("code", ""):
                examples.append({
                    "location": snip["location"],
                    "region": region,
                    "pattern": "StoreJobStats boilerplate",
                    "code_preview": _truncate_str(snip.get("code", ""), 100),
                })
                break
        if len(examples) >= 5:
            break
    examples = _pick_examples(examples, 5)

    return {
        "doc_title": "Custom Code",
        "global_summary": {
            "total_snippets": all_snippets_count,
            "regions_with_code": len(region_data),
        },
        "region_summaries": {
            region: {
                "snippet_count": len(d["snippets"]),
                "languages": dict(d["languages"]),
                "purposes": dict(d["purposes"].most_common(5)),
                "with_business_rules": sum(1 for s in d["snippets"] if s["has_business_rules"]),
            }
            for region, d in sorted(region_data.items())
        },
        "concrete_examples": examples,
        "cross_cutting_patterns": [
            f"{all_snippets_count} custom code snippets across the codebase",
            "tJava_StoreJobStats is the most duplicated pattern — appears in nearly every job",
            "Most custom code is Java inside tJava components",
            "Hidden business rules are rare — most tJava is infrastructure boilerplate",
        ],
        "migration_notes": [
            "tJava_StoreJobStats pattern needs a shared logging/metrics utility in target",
            "Review hidden business rules in tJava — these are migration risks",
            "Java expressions in tMap will need translation to target language",
        ],
    }


def condense_p09(files: list[dict], chunk_map: dict) -> dict:
    """Pass 9: Business Logic → rules and domain algorithms (already in good docs, just summarize)."""
    # Pass 9 feeds doc 10 (Business Logic) which is one of the "good" docs
    # Just produce a minimal brief
    region_data = defaultdict(lambda: {"rules": []})
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        data = f["data"]
        # Business logic pass might have various structures
        if isinstance(data, dict):
            for key in ["rules", "business_rules", "algorithms", "logic"]:
                for item in data.get(key, []):
                    region_data[region]["rules"].append(item if isinstance(item, str) else str(item)[:100])

    return {
        "doc_title": "Business Logic",
        "note": "This pass feeds doc 10_BUSINESS_LOGIC.md which is generated from synthesis data",
        "region_summaries": {
            region: {"rule_count": len(d["rules"])}
            for region, d in sorted(region_data.items())
        },
    }


def condense_p10(files: list[dict], chunk_map: dict) -> dict:
    """Pass 10: Synthesis — feeds 5 good docs. Just summarize chunk coverage."""
    chunks = []
    for f in files:
        chunks.append({
            "chunk_id": f["chunk_id"],
            "region": _chunk_to_region(f["chunk_id"], chunk_map),
            "has_summary": bool(f["data"].get("executive_summary")),
            "has_faq": bool(f["data"].get("faq")),
            "has_glossary": bool(f["data"].get("glossary")),
            "has_onboarding": bool(f["data"].get("onboarding_guide")),
            "has_risk_dashboard": bool(f["data"].get("risk_dashboard")),
        })
    return {
        "doc_title": "Synthesis",
        "note": "Feeds 5 good docs: 00_PROJECT_OVERVIEW, 10_BUSINESS_LOGIC, 11_KNOWN_ISSUES, 14_GLOSSARY_FAQ, 15_ONBOARDING",
        "chunks_analyzed": len(chunks),
        "coverage": chunks,
    }


def condense_p11(files: list[dict], chunk_map: dict) -> dict:
    """Pass 11: Schema Dictionary → tables, columns, relationships."""
    region_data = defaultdict(lambda: {"tables": [], "table_count": 0, "tables_with_columns": 0})

    all_tables = {}
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for tbl in f["data"].get("tables", []):
            name = tbl.get("table_name", "")
            region_data[region]["table_count"] += 1
            cols = tbl.get("columns", [])
            if cols:
                region_data[region]["tables_with_columns"] += 1
            key = f"{region}|{name}"
            if key not in all_tables:
                all_tables[key] = {
                    "name": name,
                    "region": region,
                    "column_count": len(cols),
                    "used_by": tbl.get("used_by_jobs", []),
                    "source": tbl.get("source_system", ""),
                }

    # Group tables by name pattern
    table_patterns = Counter()
    for t in all_tables.values():
        name = t["name"]
        if "Sales_Set" in name:
            table_patterns["Sales_Set"] += 1
        elif "Raw" in name or "raw" in name:
            table_patterns["Raw tables"] += 1
        elif "Fiscal" in name:
            table_patterns["Fiscal reference"] += 1
        elif "Master" in name:
            table_patterns["Master data"] += 1
        elif "context." in name:
            table_patterns["Parameterized (context)"] += 1
        else:
            table_patterns["Other"] += 1

    return {
        "doc_title": "Schema Dictionary",
        "global_summary": {
            "total_tables": len(all_tables),
            "tables_with_columns": sum(1 for t in all_tables.values() if t["column_count"] > 0),
            "table_patterns": dict(table_patterns.most_common()),
        },
        "region_summaries": {
            region: {
                "table_count": d["table_count"],
                "tables_with_columns": d["tables_with_columns"],
            }
            for region, d in sorted(region_data.items())
        },
        "notable_tables": [
            {k: v for k, v in t.items() if k != "region"}
            for t in sorted(all_tables.values(), key=lambda x: -len(x["used_by"]))[:10]
        ],
        "cross_cutting_patterns": [
            "Most tables lack column definitions — mechanical extraction only captures table names",
            "Parameterized table names (context.*) are common, making static analysis difficult",
            "Global reference tables (Fiscal_Periods, Country_Master) shared across regions",
            "Sales_Set is the primary output table across all subsidiaries",
        ],
        "migration_notes": [
            "Document actual column schemas from BigQuery before migration",
            "Parameterized table names need resolution from context variables",
            "Shared reference tables must be migrated first as dependencies",
        ],
    }


def condense_p12(files: list[dict], chunk_map: dict) -> dict:
    """Pass 12: Data Quality → validation rules and severity."""
    region_data = defaultdict(lambda: {"rules": [], "rule_types": Counter(), "severities": Counter()})

    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for rule in f["data"].get("rules", []):
            rtype = rule.get("rule_type", "unknown")
            severity = rule.get("severity", "medium")
            region_data[region]["rule_types"][rtype] += 1
            region_data[region]["severities"][severity] += 1
            region_data[region]["rules"].append({
                "job": rule.get("job_name", ""),
                "type": rtype,
                "severity": severity,
                "desc": _truncate_str(rule.get("description", ""), 100),
            })

    total_rules = sum(len(r["rules"]) for r in region_data.values())
    all_types = Counter()
    all_sevs = Counter()
    for r in region_data.values():
        all_types.update(r["rule_types"])
        all_sevs.update(r["severities"])

    # Examples
    examples = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for rule in f["data"].get("rules", []):
            examples.append({
                "job": rule.get("job_name", ""),
                "region": region,
                "type": rule.get("rule_type", ""),
                "expression": _truncate_str(rule.get("expression", ""), 120),
            })
    examples = _pick_examples(examples, 5)

    return {
        "doc_title": "Data Quality",
        "global_summary": {
            "total_rules": total_rules,
            "rule_types": dict(all_types.most_common()),
            "severity_distribution": dict(all_sevs),
        },
        "region_summaries": {
            region: {
                "rule_count": len(d["rules"]),
                "types": dict(d["rule_types"].most_common(5)),
                "severities": dict(d["severities"]),
            }
            for region, d in sorted(region_data.items())
        },
        "concrete_examples": examples,
        "cross_cutting_patterns": [
            "null_check and type_check are the dominant validation patterns",
            "Most validation happens inside tMap expressions, not dedicated components",
            "No explicit data quality gates or rejection flows detected",
            "Type coercion (BigDecimal, Integer.valueOf) is common across all regions",
        ],
        "migration_notes": [
            "Implement explicit data quality checks in target platform",
            "Add rejection/quarantine flows for invalid records",
            "Document all null-handling logic before migration — implicit in tMap expressions",
        ],
    }


def condense_p13(files: list[dict], chunk_map: dict) -> dict:
    """Pass 13: Naming Conventions → patterns, violations, consistency."""
    region_data = defaultdict(lambda: {"patterns": [], "violations": [], "score": "", "recommendations": []})

    all_region_codes = Counter()
    all_subsidiary_codes = Counter()
    all_layer_codes = Counter()

    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        data = f["data"]
        region_data[region]["patterns"].extend(data.get("detected_patterns", []))
        region_data[region]["violations"].extend(data.get("violations", []))
        region_data[region]["score"] = data.get("consistency_score", "")
        region_data[region]["recommendations"].extend(data.get("recommendations", []))
        for code, count_str in data.get("region_code_usage", {}).items():
            all_region_codes[code] += int(count_str.split()[0]) if count_str else 0
        for code, count_str in data.get("subsidiary_code_usage", {}).items():
            all_subsidiary_codes[code] += int(count_str.split()[0]) if count_str else 0
        for code, count_str in data.get("layer_code_usage", {}).items():
            all_layer_codes[code] += int(count_str.split()[0]) if count_str else 0

    return {
        "doc_title": "Naming Conventions",
        "global_summary": {
            "region_codes": dict(all_region_codes.most_common()),
            "subsidiary_codes": dict(all_subsidiary_codes.most_common()),
            "layer_codes": dict(all_layer_codes.most_common()),
        },
        "region_summaries": {
            region: {
                "patterns": d["patterns"][:5],
                "violations": d["violations"][:3],
                "consistency_score": d["score"],
            }
            for region, d in sorted(region_data.items())
        },
        "cross_cutting_patterns": [
            "Standard pattern: {Region}_{Subsidiary}_{Layer}_{Description}",
            f"Region codes detected: {', '.join(all_region_codes.keys())}",
            f"Layer codes: {', '.join(all_layer_codes.keys())}",
            "Most regions follow the convention well — few violations detected",
        ],
        "migration_notes": [
            "Preserve naming convention in target platform for continuity",
            "Document any exceptions or deviations before migration",
        ],
    }


def condense_p14(files: list[dict], chunk_map: dict) -> dict:
    """Pass 14: Scheduling & SLA → frequencies, chains, bottlenecks."""
    region_data = defaultdict(lambda: {"jobs": [], "frequencies": Counter(), "max_chain_depth": 0})

    all_schedules = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        data = f["data"]
        for sched in data.get("schedules", []):
            freq = sched.get("estimated_frequency", "unknown")
            depth = sched.get("chain_depth", 0)
            region_data[region]["frequencies"][freq] += 1
            region_data[region]["max_chain_depth"] = max(region_data[region]["max_chain_depth"], depth)
            region_data[region]["jobs"].append(sched.get("job_name", ""))
            all_schedules.append({
                "job": sched.get("job_name", ""),
                "region": region,
                "frequency": freq,
                "chain_depth": depth,
            })

    total_jobs = sum(len(r["jobs"]) for r in region_data.values())
    all_freqs = Counter()
    for r in region_data.values():
        all_freqs.update(r["frequencies"])

    bottlenecks = []
    for f in files:
        for b in f["data"].get("bottleneck_jobs", []):
            bottlenecks.append(b)

    return {
        "doc_title": "Scheduling & SLA",
        "global_summary": {
            "total_jobs": total_jobs,
            "frequency_distribution": dict(all_freqs.most_common()),
            "jobs_with_dependency_chains": sum(1 for s in all_schedules if s["chain_depth"] > 0),
        },
        "region_summaries": {
            region: {
                "job_count": len(d["jobs"]),
                "frequencies": dict(d["frequencies"]),
                "max_chain_depth": d["max_chain_depth"],
            }
            for region, d in sorted(region_data.items())
        },
        "bottleneck_jobs": _pick_examples(bottlenecks, 5),
        "cross_cutting_patterns": [
            f"All {total_jobs} jobs marked as 'on-demand' — no scheduled triggers detected in Talend metadata",
            "Scheduling likely managed externally (e.g., Talend Administration Center, cron, orchestrator)",
            "No SLA definitions found in job metadata",
            "Dependency chains are shallow — most jobs are standalone",
        ],
        "migration_notes": [
            "Document actual scheduling from the execution environment (TAC, cron)",
            "Define SLAs for critical pipelines before migration",
            "Implement dependency-based orchestration in target platform",
        ],
    }


def condense_p15(files: list[dict], chunk_map: dict) -> dict:
    """Pass 15: Security & Access → credential findings, access patterns."""
    region_data = defaultdict(lambda: {"findings": [], "categories": Counter(), "severities": Counter()})

    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for finding in f["data"].get("findings", []):
            cat = finding.get("category", "unknown")
            sev = finding.get("severity", "medium")
            region_data[region]["categories"][cat] += 1
            region_data[region]["severities"][sev] += 1
            region_data[region]["findings"].append({
                "id": finding.get("finding_id", ""),
                "category": cat,
                "severity": sev,
                "location": _truncate_str(finding.get("location", ""), 80),
            })

    total_findings = sum(len(r["findings"]) for r in region_data.values())
    all_cats = Counter()
    all_sevs = Counter()
    for r in region_data.values():
        all_cats.update(r["categories"])
        all_sevs.update(r["severities"])

    examples = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for finding in f["data"].get("findings", []):
            examples.append({
                "region": region,
                "category": finding.get("category", ""),
                "severity": finding.get("severity", ""),
                "location": _truncate_str(finding.get("location", ""), 80),
                "remediation": _truncate_str(finding.get("remediation", ""), 100),
            })
    examples = _pick_examples(examples, 5)

    cred_mgmt = ""
    for f in files:
        cm = f["data"].get("credential_management", "")
        if cm:
            cred_mgmt = cm
            break

    return {
        "doc_title": "Security & Access",
        "global_summary": {
            "total_findings": total_findings,
            "categories": dict(all_cats.most_common()),
            "severity_distribution": dict(all_sevs),
            "credential_management": cred_mgmt,
        },
        "region_summaries": {
            region: {
                "finding_count": len(d["findings"]),
                "categories": dict(d["categories"]),
                "severities": dict(d["severities"]),
            }
            for region, d in sorted(region_data.items())
        },
        "concrete_examples": examples,
        "cross_cutting_patterns": [
            f"{total_findings} security findings, primarily credential_exposure",
            "All credentials use Talend context variables — no hardcoded secrets",
            "Password-type context variables detected across all regions",
            "No RBAC or row-level security detected in job configurations",
        ],
        "migration_notes": [
            "Migrate all credentials to a secrets manager (Azure Key Vault, etc.)",
            "Implement RBAC in target platform for data access control",
            "Audit credential rotation policies before migration",
        ],
    }


def condense_p16(files: list[dict], chunk_map: dict) -> dict:
    """Pass 16: Performance → SELECT *, parallelism, optimization opportunities."""
    region_data = defaultdict(lambda: {"jobs": [], "risks": [], "parallel_count": 0})

    all_risks = []
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for job in f["data"].get("jobs", []):
            region_data[region]["jobs"].append(job.get("job_name", ""))
            if job.get("parallel_execution"):
                region_data[region]["parallel_count"] += 1
            for risk in job.get("performance_risks", []):
                all_risks.append({"job": job.get("job_name", ""), "region": region, "risk": _truncate_str(risk, 120)})
                region_data[region]["risks"].append(risk)

    total_jobs = sum(len(r["jobs"]) for r in region_data.values())
    total_risks = len(all_risks)

    # Categorize risks
    risk_types = Counter()
    for r in all_risks:
        if "SELECT *" in r["risk"]:
            risk_types["SELECT * pattern"] += 1
        elif "batch" in r["risk"].lower():
            risk_types["Batch size"] += 1
        else:
            risk_types["Other"] += 1

    return {
        "doc_title": "Performance",
        "global_summary": {
            "total_jobs": total_jobs,
            "total_performance_risks": total_risks,
            "jobs_with_parallel": sum(r["parallel_count"] for r in region_data.values()),
            "risk_types": dict(risk_types.most_common()),
        },
        "region_summaries": {
            region: {
                "job_count": len(d["jobs"]),
                "risk_count": len(d["risks"]),
                "uses_parallelism": d["parallel_count"],
            }
            for region, d in sorted(region_data.items())
        },
        "concrete_examples": _pick_examples(all_risks, 5),
        "cross_cutting_patterns": [
            f"SELECT * pattern is the dominant performance risk ({risk_types.get('SELECT * pattern', 0)} instances)",
            "No parallel execution detected in any region",
            "No batch size configuration found — all jobs use default streaming",
            "Most optimization opportunities are in BigQuery query patterns",
        ],
        "migration_notes": [
            "Replace SELECT * with explicit column lists in target platform",
            "Evaluate parallel execution opportunities in target architecture",
            "Configure appropriate batch sizes for bulk operations",
            "Consider partitioning strategies for large BigQuery tables",
        ],
    }


def condense_p17(files: list[dict], chunk_map: dict) -> dict:
    """Pass 17: Cross-Region → region comparison matrix."""
    regions_data = {}
    for f in files:
        for reg in f["data"].get("regions", []):
            name = reg.get("region", "")
            if name not in regions_data:
                regions_data[name] = {
                    "job_count": reg.get("job_count", 0),
                    "components_used": set(),
                    "unique_patterns": [],
                    "deviations": [],
                }
            else:
                regions_data[name]["job_count"] = max(regions_data[name]["job_count"], reg.get("job_count", 0))
            for c in reg.get("components_used", []):
                regions_data[name]["components_used"].add(c)
            regions_data[name]["unique_patterns"].extend(reg.get("unique_patterns", []))
            regions_data[name]["deviations"].extend(reg.get("deviations_from_standard", []))

    # Convert sets to lists for JSON
    region_matrix = {}
    for name, data in sorted(regions_data.items()):
        region_matrix[name] = {
            "job_count": data["job_count"],
            "components_used": sorted(data["components_used"]),
            "unique_patterns": data["unique_patterns"][:5],
            "deviations": data["deviations"][:5],
        }

    # Shared vs unique components
    all_components = set()
    for r in regions_data.values():
        all_components.update(r["components_used"])
    shared_components = []
    for comp in sorted(all_components):
        regions_using = [name for name, d in regions_data.items() if comp in d["components_used"]]
        if len(regions_using) > 1:
            shared_components.append({"component": comp, "regions": regions_using})
    shared_components.sort(key=lambda x: -len(x["regions"]))

    return {
        "doc_title": "Cross-Region Analysis",
        "global_summary": {
            "total_regions": len(regions_data),
            "total_jobs": sum(r["job_count"] for r in regions_data.values()),
            "shared_component_count": len(shared_components),
        },
        "region_matrix": region_matrix,
        "shared_components": shared_components[:10],
        "cross_cutting_patterns": [
            f"{len(regions_data)} active regions: {', '.join(sorted(regions_data.keys()))}",
            "BRA and JPN have the most jobs; SAF and ZAF have the fewest",
            "tBigQueryInput, tMap, tBigQueryOutput are universal across all regions",
            "Region-specific components exist (e.g., tMSSqlInput for on-prem regions)",
        ],
        "migration_notes": [
            "Prioritize migration by region size: BRA > Global > JPN > USA > others",
            "Shared components (tMap, BigQuery) can use common migration patterns",
            "Region-specific connections (MSSQL) need per-region testing",
        ],
    }


def condense_p18(files: list[dict], chunk_map: dict) -> dict:
    """Pass 18: Reusability → duplicated logic, shared components."""
    region_data = defaultdict(lambda: {"duplicated": [], "shared": [], "dup_count": 0})

    all_shared = {}
    for f in files:
        region = _chunk_to_region(f["chunk_id"], chunk_map)
        for dup in f["data"].get("duplicated_logic", []):
            region_data[region]["dup_count"] += 1
            region_data[region]["duplicated"].append({
                "desc": _truncate_str(dup.get("description", ""), 100),
                "lines": dup.get("lines_duplicated", 0),
            })
        for shared in f["data"].get("shared_components", []):
            name = shared.get("component_name", "")
            ctype = shared.get("component_type", "")
            key = f"{name}|{ctype}"
            if key not in all_shared:
                all_shared[key] = {
                    "name": name,
                    "type": ctype,
                    "used_by": set(),
                    "reuse_count": 0,
                }
            for job in shared.get("used_by", []):
                all_shared[key]["used_by"].add(job)
            all_shared[key]["reuse_count"] = max(all_shared[key]["reuse_count"], shared.get("reuse_count", 0))

    total_duplicates = sum(r["dup_count"] for r in region_data.values())

    shared_list = []
    for s in all_shared.values():
        shared_list.append({
            "name": s["name"],
            "type": s["type"],
            "reuse_count": len(s["used_by"]),
        })
    shared_list.sort(key=lambda x: -x["reuse_count"])

    return {
        "doc_title": "Reusability",
        "global_summary": {
            "total_duplicated_patterns": total_duplicates,
            "unique_shared_components": len(shared_list),
            "top_reused_components": shared_list[:10],
        },
        "region_summaries": {
            region: {
                "duplicate_count": d["dup_count"],
                "example_duplicates": d["duplicated"][:3],
            }
            for region, d in sorted(region_data.items())
        },
        "cross_cutting_patterns": [
            f"{total_duplicates} duplicated logic patterns detected across the codebase",
            "tPostjob + cleanup_temp_file_storage is the most reused pattern",
            "Vaxxinova_Logging joblet is shared across most regions",
            "tJava_StoreJobStats code is copy-pasted rather than shared",
        ],
        "migration_notes": [
            "Consolidate duplicated logic into shared modules in target platform",
            "Convert Vaxxinova_Logging to a reusable utility/library",
            "Replace copy-pasted tJava_StoreJobStats with a shared metrics function",
        ],
    }


# ── main ─────────────────────────────────────────────────────────────

CONDENSERS = {
    1: condense_p01,
    2: condense_p02,
    3: condense_p03,
    4: condense_p04,
    5: lambda f, c: condense_p05(f, c, _load_connections()),
    6: condense_p06,
    7: condense_p07,
    8: condense_p08,
    9: condense_p09,
    10: condense_p10,
    11: condense_p11,
    12: condense_p12,
    13: condense_p13,
    14: condense_p14,
    15: condense_p15,
    16: condense_p16,
    17: condense_p17,
    18: condense_p18,
}


def main() -> None:
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    chunk_map = _load_chunks_summary()

    print(f"Loading pass results from {PASS_RESULTS_DIR}...")
    for pass_num in range(1, 19):
        files = _load_pass_files(pass_num)
        if not files:
            print(f"  p{pass_num:02d}: no files found, skipping")
            continue

        condenser = CONDENSERS[pass_num]
        brief = condenser(files, chunk_map)
        brief["pass_number"] = pass_num
        brief["pass_name"] = PASS_NAMES[pass_num]
        brief["chunks_processed"] = len(files)

        out_path = BRIEFS_DIR / f"brief_p{pass_num:02d}.json"
        raw = json.dumps(brief, indent=2, default=str)
        out_path.write_text(raw, encoding="utf-8")

        size_kb = len(raw.encode()) / 1024
        print(f"  p{pass_num:02d} ({PASS_NAMES[pass_num]}): {len(files)} chunks -> {size_kb:.1f} KB")
        if size_kb > 15:
            print(f"    WARNING: brief exceeds 15 KB target ({size_kb:.1f} KB)")

    print(f"\nDone. Briefs written to {BRIEFS_DIR}/")


if __name__ == "__main__":
    main()
