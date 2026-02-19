#!/usr/bin/env python3
"""Generate all 630 pass_result JSON files from chunk data.

Reads the 35 chunk JSON files from intodoc/docs/chunks/ and the scan metadata
from intodoc/docs/scan/, then produces pass_result files for all 18 passes
at intodoc/docs/pass_results/p{NN}_{chunk_id}.json.

Usage:
    python intodoc/scripts/generate_pass_results.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
CHUNKS_DIR = DOCS_DIR / "chunks"
SCAN_DIR = DOCS_DIR / "scan"
OUTPUT_DIR = DOCS_DIR / "pass_results"

# ── Helpers ──────────────────────────────────────────────────────────


def load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def save_pass_result(pass_number: int, pass_name: str, chunk_id: str, data: dict) -> None:
    result = {
        "pass_number": pass_number,
        "pass_name": pass_name,
        "chunk_id": chunk_id,
        "model_used": "mechanical-extraction",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "data": data,
    }
    path = OUTPUT_DIR / f"p{pass_number:02d}_{chunk_id}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


def get_param(component: dict, param_name: str) -> str:
    for p in component.get("params", []):
        if p["name"] == param_name:
            return p["value"]
    return ""


def extract_table_from_query(query: str) -> str:
    """Extract table name from SQL-like query string."""
    # Remove Java string concatenation
    clean = query.replace('"', "").replace("+", " ").strip()
    # Match FROM clause
    m = re.search(r"FROM\s+([\w.]+)", clean, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def extract_tables_from_query(query: str) -> list[str]:
    """Extract all table names from a SQL query."""
    clean = query.replace('"', "").replace("+", " ").strip()
    tables = []
    for m in re.finditer(r"(?:FROM|JOIN)\s+([\w.]+)", clean, re.IGNORECASE):
        tables.append(m.group(1))
    return tables


def extract_columns_from_query(query: str) -> list[str]:
    """Extract column references from SELECT clause."""
    clean = query.replace('"', "").replace("+", " ").strip()
    m = re.search(r"SELECT\s+(.*?)\s+FROM", clean, re.IGNORECASE | re.DOTALL)
    if m:
        select_part = m.group(1).strip()
        if select_part == "*":
            return ["*"]
        cols = [c.strip().split(".")[-1].split(" ")[-1] for c in select_part.split(",")]
        return [c for c in cols if c and c != "*"]
    return []


def is_input_component(comp_type: str) -> bool:
    return any(
        kw in comp_type.lower()
        for kw in ["input", "mssqlinput", "bigqueryinput", "fileinput", "rest", "httprequest"]
    )


def is_output_component(comp_type: str) -> bool:
    return any(
        kw in comp_type.lower()
        for kw in ["output", "mssqloutput", "bigqueryoutput", "fileoutput"]
    )


def is_error_component(comp_type: str) -> bool:
    return comp_type.lower() in {
        "tdie", "twarn", "tlogrow", "tassert", "tlogcatcher",
        "tstatcatcher", "tflowmetercatcher",
    }


def is_custom_code_component(comp_type: str) -> bool:
    return any(
        kw in comp_type.lower()
        for kw in ["tjava", "tgroovy", "tjavarow", "tjavaflex"]
    )


def detect_component_system_type(comp_type: str) -> str:
    ct = comp_type.lower()
    if "bigquery" in ct:
        return "Google BigQuery"
    if "mssql" in ct or "sqlserver" in ct:
        return "Microsoft SQL Server"
    if "mysql" in ct:
        return "MySQL"
    if "postgres" in ct:
        return "PostgreSQL"
    if "oracle" in ct:
        return "Oracle"
    if "file" in ct or "csv" in ct:
        return "File System"
    if "rest" in ct or "http" in ct:
        return "REST API"
    if "ftp" in ct or "sftp" in ct:
        return "FTP/SFTP"
    if "s3" in ct or "aws" in ct:
        return "AWS S3"
    if "azure" in ct:
        return "Azure"
    return ""


def classify_region(group: str) -> str:
    return group if group else "Global"


# ── Pass 1: Component Inventory ─────────────────────────────────────


def pass_01_component_inventory(chunk: dict, manifest: dict) -> dict:
    components = []
    orphaned = []
    job_calls = manifest.get("job_calls", {})
    job_called_by = manifest.get("job_called_by", {})

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        for comp in job.get("components", []):
            deps = []
            # Add job-level dependencies
            if job_name in job_called_by:
                deps.extend(job_called_by[job_name])

            refs = job_calls.get(job_name, [])

            components.append({
                "name": comp["unique_name"],
                "component_type": comp["component_type"],
                "location": f"{job_name}",
                "dependencies": deps,
                "referenced_by": refs,
                "description": get_param(comp, "LABEL") or comp["component_type"],
            })

            has_conns = bool(comp.get("incoming_connections") or comp.get("outgoing_connections"))
            if not has_conns and comp["component_type"] not in {
                "tPrejob", "tPostjob", "cleanup_temp_file_storage", "Vaxxinova_Logging"
            }:
                orphaned.append(f"{comp['unique_name']} in {job_name}")

    # Build dependency graph mermaid
    mermaid_lines = ["graph LR"]
    job_names = [j["name"] for j in chunk.get("jobs", [])]
    for jn in job_names:
        safe_jn = jn.replace(" ", "_").replace("-", "_")
        calls = job_calls.get(jn, [])
        for callee in calls:
            safe_callee = callee.replace(" ", "_").replace("-", "_")
            mermaid_lines.append(f"    {safe_jn} --> {safe_callee}")
        called_by = job_called_by.get(jn, [])
        for caller in called_by:
            safe_caller = caller.replace(" ", "_").replace("-", "_")
            mermaid_lines.append(f"    {safe_caller} --> {safe_jn}")

    dep_mermaid = "\n".join(mermaid_lines) if len(mermaid_lines) > 1 else ""

    return {
        "components": components,
        "dependency_graph_mermaid": dep_mermaid,
        "orphaned_components": orphaned[:50],  # cap at 50
        "missing_references": [],
    }


# ── Pass 2: Execution Logic ─────────────────────────────────────────


def pass_02_execution_logic(chunk: dict, manifest: dict) -> dict:
    steps = []
    job_calls = manifest.get("job_calls", {})

    for job in chunk.get("jobs", []):
        job_name = job["name"]

        # Detect control flow from connections
        control_flows = []
        for conn in job.get("connections", []):
            cname = conn.get("connector_name", "")
            if cname in ("SUBJOB_OK", "SUBJOB_ERROR", "RUN_IF", "COMPONENT_OK", "COMPONENT_ERROR"):
                control_flows.append(f"{conn['source']} --{cname}--> {conn['target']}")

        # Detect tRunJob refs
        triggers = []
        child_calls = job_calls.get(job_name, [])
        if child_calls:
            triggers.append(f"Orchestrates: {', '.join(child_calls)}")

        # Build per-job flowchart
        flow_lines = ["graph TD"]
        for conn in job.get("connections", []):
            src = conn["source"].replace(" ", "_")
            tgt = conn["target"].replace(" ", "_")
            label = conn.get("connector_name", conn.get("name", ""))
            if label:
                flow_lines.append(f"    {src} -->|{label}| {tgt}")
            else:
                flow_lines.append(f"    {src} --> {tgt}")

        flowchart = "\n".join(flow_lines) if len(flow_lines) > 1 else ""

        steps.append({
            "job_name": job_name,
            "trigger": "; ".join(triggers) if triggers else "standalone",
            "control_flow": "; ".join(control_flows) if control_flows else "sequential",
            "flowchart_mermaid": flowchart,
            "notes": "",
        })

    # Orchestration mermaid for the chunk
    orch_lines = ["graph TD"]
    for job in chunk.get("jobs", []):
        jn = job["name"]
        safe_jn = jn.replace(" ", "_").replace("-", "_")
        calls = job_calls.get(jn, [])
        for callee in calls:
            safe_callee = callee.replace(" ", "_").replace("-", "_")
            orch_lines.append(f"    {safe_jn} --> {safe_callee}")

    orch_mermaid = "\n".join(orch_lines) if len(orch_lines) > 1 else ""

    return {
        "steps": steps,
        "orchestration_mermaid": orch_mermaid,
    }


# ── Pass 3: Data Flows ──────────────────────────────────────────────


def pass_03_data_flows(chunk: dict) -> dict:
    flows = []

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        inputs = []
        transformations = []
        outputs = []

        for comp in job.get("components", []):
            ct = comp["component_type"]

            if is_input_component(ct):
                query = get_param(comp, "QUERY")
                table = extract_table_from_query(query) if query else get_param(comp, "TABLE")
                label = get_param(comp, "LABEL") or comp["unique_name"]
                system = detect_component_system_type(ct) or ct
                cols = extract_columns_from_query(query) if query else []

                inputs.append({
                    "source_system": system,
                    "source_table": table,
                    "source_query": query.replace('"', "'")[:200] if query else "",
                    "columns": cols,
                })

            elif ct == "tMap":
                # Extract tMap transformation info
                for tmap in job.get("tmap_data", []):
                    for entry in tmap.get("entries", []):
                        transformations.append({
                            "component": comp["unique_name"],
                            "expression": entry.get("expression", "")[:200],
                            "human_explanation": f"Maps {entry.get('column_name', '?')} in {entry.get('table_name', '?')}",
                        })
                if not transformations:
                    transformations.append({
                        "component": comp["unique_name"],
                        "expression": "",
                        "human_explanation": "Data mapping/transformation",
                    })

            elif is_output_component(ct):
                table = get_param(comp, "TABLE") or get_param(comp, "DATASET")
                filename = get_param(comp, "FILENAME")
                system = detect_component_system_type(ct) or ct
                write_mode = get_param(comp, "ACTION_ON_TABLE") or get_param(comp, "WRITE_MODE") or ""

                outputs.append({
                    "target_system": system,
                    "target_table": table or filename[:100] if filename else "",
                    "write_mode": write_mode.strip('"').lower() if write_mode else "",
                    "columns": [],
                })

        # Build data lineage mermaid for this job
        lineage_lines = ["graph LR"]
        for i, inp in enumerate(inputs):
            src = (inp["source_table"] or inp["source_system"]).replace(" ", "_").replace(".", "_")
            lineage_lines.append(f"    src{i}[{src}] --> transform[tMap]")
        for i, out in enumerate(outputs):
            tgt = (out["target_table"] or out["target_system"]).replace(" ", "_").replace(".", "_")
            lineage_lines.append(f"    transform[tMap] --> tgt{i}[{tgt}]")

        lineage_mermaid = "\n".join(lineage_lines) if len(lineage_lines) > 1 else ""

        flows.append({
            "job_name": job_name,
            "inputs": inputs,
            "transformations": transformations[:20],  # cap
            "outputs": outputs,
            "data_lineage_mermaid": lineage_mermaid,
        })

    return {
        "flows": flows,
        "cross_job_lineage_mermaid": "",
    }


# ── Pass 4: Environment Config ──────────────────────────────────────


def pass_04_environment_config(chunk: dict) -> dict:
    contexts = chunk.get("related_contexts", [])
    environments = set()
    comparisons = []
    prod_risks = []

    for ctx_group in contexts:
        for var in ctx_group.get("variables", []):
            values = var.get("values", {})
            for env_name in values:
                environments.add(env_name)

            # Check for PROD vs TEST differences
            prod_val = values.get("PROD", "")
            test_val = values.get("TEST", "")
            risk = ""
            if prod_val and test_val and prod_val != test_val:
                risk = "PROD and TEST differ"
            elif prod_val and not test_val:
                risk = "PROD set but TEST empty"
            elif not prod_val and test_val:
                risk = "TEST set but PROD empty"

            if values and any(v for v in values.values()):
                comparisons.append({
                    "variable_name": f"{ctx_group['name']}.{var['name']}",
                    "values_by_env": values,
                    "risk_notes": risk,
                })

                # Flag credential-related prod risks
                vname = var["name"].lower()
                if risk and any(kw in vname for kw in ["password", "secret", "credential", "key"]):
                    prod_risks.append(
                        f"{var['name']}: {risk} — credential variable requires attention"
                    )

    return {
        "environments": sorted(environments),
        "comparisons": comparisons,
        "prod_risks": prod_risks,
    }


# ── Pass 5: External Systems ────────────────────────────────────────


def pass_05_external_systems(chunk: dict, manifest: dict) -> dict:
    systems = []
    seen = set()
    conn_used_by = manifest.get("connection_used_by", {})

    # From metadata connections in the chunk
    for conn in chunk.get("related_connections", []):
        name = conn["name"]
        if name in seen:
            continue
        seen.add(name)

        used_by = conn_used_by.get(name, [])
        systems.append({
            "name": name,
            "system_type": conn.get("connection_type", "database"),
            "connection_details": f"host={conn.get('host', '')}, db={conn.get('database', '')}",
            "used_by_jobs": used_by,
            "criticality": "high" if used_by else "medium",
        })

    # Detect from component types
    for job in chunk.get("jobs", []):
        for comp in job.get("components", []):
            sys_type = detect_component_system_type(comp["component_type"])
            if sys_type and sys_type not in seen:
                seen.add(sys_type)
                systems.append({
                    "name": sys_type,
                    "system_type": "database" if "SQL" in sys_type or "Query" in sys_type else "cloud",
                    "connection_details": f"Detected from {comp['component_type']}",
                    "used_by_jobs": [job["name"]],
                    "criticality": "medium",
                })

    # Build topology mermaid
    topo_lines = ["graph TB"]
    for sys in systems:
        safe_name = sys["name"].replace(" ", "_").replace("-", "_").replace(".", "_")
        for jn in sys.get("used_by_jobs", [])[:5]:
            safe_jn = jn.replace(" ", "_").replace("-", "_")
            topo_lines.append(f"    {safe_jn} --> {safe_name}")

    topo_mermaid = "\n".join(topo_lines) if len(topo_lines) > 1 else ""

    return {
        "systems": systems,
        "topology_mermaid": topo_mermaid,
    }


# ── Pass 6: Error Handling ──────────────────────────────────────────


def pass_06_error_handling(chunk: dict) -> dict:
    jobs = []
    critical_gaps = []

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        error_comps = []
        silent_risks = []
        recommendations = []

        has_die = False
        has_warn = False
        has_log = False
        has_catch = False

        for comp in job.get("components", []):
            ct = comp["component_type"].lower()
            if "die" in ct:
                error_comps.append(comp["unique_name"])
                has_die = True
            elif "warn" in ct:
                error_comps.append(comp["unique_name"])
                has_warn = True
            elif "logrow" in ct or "logcatcher" in ct:
                error_comps.append(comp["unique_name"])
                has_log = True
            elif "assert" in ct or "statcatcher" in ct:
                error_comps.append(comp["unique_name"])
                has_catch = True

        # Check connections for DIE_ON_CHILD_ERROR pattern
        has_error_flow = any(
            conn.get("connector_name") in ("SUBJOB_ERROR", "COMPONENT_ERROR")
            for conn in job.get("connections", [])
        )

        # Rate the job
        if has_die or (has_catch and has_error_flow):
            rating = "good"
        elif has_warn or has_log or has_error_flow:
            rating = "adequate"
        elif error_comps:
            rating = "adequate"
        else:
            rating = "none"
            silent_risks.append(f"{job_name} has no error handling components")
            critical_gaps.append(f"{job_name}: no error handling detected")

        if not has_error_flow and rating != "none":
            silent_risks.append(f"{job_name}: no error flow connections detected")

        if rating in ("none", "poor"):
            recommendations.append(f"Add tDie or tLogCatcher to {job_name}")
        if not has_error_flow:
            recommendations.append(f"Add SUBJOB_ERROR flow to {job_name}")

        jobs.append({
            "job_name": job_name,
            "rating": rating,
            "error_components": error_comps,
            "silent_failure_risks": silent_risks,
            "recommendations": recommendations,
        })

    # Overall rating
    ratings = [j["rating"] for j in jobs]
    if all(r == "good" for r in ratings):
        overall = "good"
    elif any(r == "none" for r in ratings):
        overall = "poor"
    else:
        overall = "adequate"

    return {
        "jobs": jobs,
        "overall_rating": overall,
        "critical_gaps": critical_gaps,
    }


# ── Pass 7: State & Side Effects ────────────────────────────────────


def pass_07_state_side_effects(chunk: dict) -> dict:
    effects = []
    cleanup_gaps = []

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        has_cleanup = False

        for comp in job.get("components", []):
            ct = comp["component_type"].lower()
            label = get_param(comp, "LABEL").lower()

            if "cleanup" in ct or "cleanup" in label:
                has_cleanup = True

            if "temp" in ct or "temp" in label or "staging" in label:
                filename = get_param(comp, "FILENAME")
                effects.append({
                    "job_name": job_name,
                    "effect_type": "temp file",
                    "description": f"{comp['unique_name']}: {filename[:100]}" if filename else comp["unique_name"],
                    "cleanup_exists": has_cleanup,
                    "risk": "low" if has_cleanup else "medium",
                })

            if "globalvar" in ct or "tsetglobalvar" in ct:
                effects.append({
                    "job_name": job_name,
                    "effect_type": "global variable",
                    "description": f"{comp['unique_name']} modifies global state",
                    "cleanup_exists": False,
                    "risk": "medium",
                })

        # Check for file output patterns (staging)
        for comp in job.get("components", []):
            filename = get_param(comp, "FILENAME")
            if filename and ("temp" in filename.lower() or "staging" in filename.lower()):
                effects.append({
                    "job_name": job_name,
                    "effect_type": "staging file",
                    "description": f"Writes to: {filename[:120]}",
                    "cleanup_exists": has_cleanup,
                    "risk": "low" if has_cleanup else "medium",
                })

        if not has_cleanup and any(e["job_name"] == job_name for e in effects):
            cleanup_gaps.append(f"{job_name}: has temp/staging artifacts but no cleanup component")

    return {
        "effects": effects,
        "cleanup_gaps": cleanup_gaps,
    }


# ── Pass 8: Custom Code ─────────────────────────────────────────────


def pass_08_custom_code(chunk: dict) -> dict:
    snippets = []
    routines_summary = []

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        for comp in job.get("components", []):
            if is_custom_code_component(comp["component_type"]):
                code = get_param(comp, "CODE") or get_param(comp, "CODE_MAIN") or ""
                snippets.append({
                    "location": f"{job_name}/{comp['unique_name']}",
                    "language": "java",
                    "code": code[:500] if code else "(embedded code)",
                    "purpose": get_param(comp, "LABEL") or comp["component_type"],
                    "hidden_business_rules": [],
                })

    # Include related routines
    for routine in chunk.get("related_routines", []):
        name = routine.get("name", "unknown")
        source = routine.get("source_code", "")
        routines_summary.append(f"{name}: {len(source)} chars")
        if source and not routine.get("is_system", False):
            snippets.append({
                "location": f"routines/{name}",
                "language": "java",
                "code": source[:800],
                "purpose": f"Shared routine: {name}",
                "hidden_business_rules": [],
            })

    return {
        "snippets": snippets,
        "routines_summary": routines_summary,
    }


# ── Pass 9: Business Logic (draft — enriched by agents) ─────────────


def pass_09_business_logic(chunk: dict) -> dict:
    domains = set()
    rules = []
    rule_counter = 0

    group = chunk.get("group", "Global")

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        name_lower = job_name.lower()

        # Detect domain from job name
        if any(kw in name_lower for kw in ["sales", "invoice", "billing", "revenue"]):
            domains.add("Sales & Revenue")
        if any(kw in name_lower for kw in ["inventory", "stock", "warehouse"]):
            domains.add("Inventory Management")
        if any(kw in name_lower for kw in ["customer", "client", "contact"]):
            domains.add("Customer Management")
        if any(kw in name_lower for kw in ["vacc", "dose", "batch", "vial"]):
            domains.add("Vaccination/Product")
        if any(kw in name_lower for kw in ["forecast", "predict", "plan"]):
            domains.add("Forecasting & Planning")
        if any(kw in name_lower for kw in ["raw", "etl", "extract", "load"]):
            domains.add("Data Integration/ETL")
        if any(kw in name_lower for kw in ["clean", "transform", "map"]):
            domains.add("Data Transformation")
        if any(kw in name_lower for kw in ["report", "export", "publish"]):
            domains.add("Reporting")

        # Extract business rules from tMap expressions and filters
        for tmap in job.get("tmap_data", []):
            for entry in tmap.get("entries", []):
                expr = entry.get("expression", "")
                if expr and len(expr) > 10:
                    rule_counter += 1
                    rules.append({
                        "rule_id": f"BR-{group}-{rule_counter:03d}",
                        "domain": "Data Transformation",
                        "description": f"tMap expression in {job_name}: {entry.get('column_name', '?')} = {expr[:150]}",
                        "implemented_in": [job_name],
                        "impact_if_wrong": "Incorrect data transformation",
                    })

            for var_entry in tmap.get("var_entries", []):
                expr = var_entry.get("expression", "")
                if expr and len(expr) > 10:
                    rule_counter += 1
                    rules.append({
                        "rule_id": f"BR-{group}-{rule_counter:03d}",
                        "domain": "Business Logic Variable",
                        "description": f"Variable in {job_name}: {var_entry.get('column_name', '?')} = {expr[:150]}",
                        "implemented_in": [job_name],
                        "impact_if_wrong": "Incorrect intermediate calculation",
                    })

        # Extract filter conditions from component params
        for comp in job.get("components", []):
            for p in comp.get("params", []):
                if p["name"] in ("FILTER", "WHERE", "CONDITION") and p["value"]:
                    rule_counter += 1
                    rules.append({
                        "rule_id": f"BR-{group}-{rule_counter:03d}",
                        "domain": "Data Filtering",
                        "description": f"Filter in {job_name}/{comp['unique_name']}: {p['value'][:150]}",
                        "implemented_in": [job_name],
                        "impact_if_wrong": "Incorrect data filtering may exclude/include wrong records",
                    })

    if not domains:
        domains.add("Data Integration/ETL")

    return {
        "domains": sorted(domains),
        "rules": rules[:100],  # cap
    }


# ── Pass 10: Synthesis (draft — enriched by agents) ─────────────────


def pass_10_synthesis(chunk: dict, all_chunk_data: dict) -> dict:
    group = chunk.get("group", "Global")
    chunk_id = chunk.get("chunk_id", "unknown")
    job_count = len(chunk.get("jobs", []))
    job_names = [j["name"] for j in chunk.get("jobs", [])]

    # Collect component type stats
    comp_types = Counter()
    for job in chunk.get("jobs", []):
        for comp in job.get("components", []):
            comp_types[comp["component_type"]] += 1

    top_comps = comp_types.most_common(10)
    top_comp_str = ", ".join(f"{ct} ({n})" for ct, n in top_comps)

    exec_summary = (
        f"The {group} region chunk '{chunk_id}' contains {job_count} jobs: "
        f"{', '.join(job_names[:10])}{'...' if len(job_names) > 10 else ''}. "
        f"Top component types: {top_comp_str}. "
        f"This region handles data integration for the {group} subsidiary of Vaxxinova."
    )

    key_findings = [
        f"{job_count} jobs in {group} region",
        f"{sum(comp_types.values())} total components across all jobs",
        f"Most used component: {top_comps[0][0]} ({top_comps[0][1]} instances)" if top_comps else "No components detected",
    ]

    # Glossary
    glossary = [
        {"term": "tMap", "definition": "Talend transformation component for data mapping and filtering"},
        {"term": "tBigQueryInput", "definition": "Component to read data from Google BigQuery"},
        {"term": "tBigQueryOutput", "definition": "Component to write data to Google BigQuery"},
        {"term": "tRunJob", "definition": "Component that calls a child job within the orchestration"},
        {"term": "SUBJOB_OK", "definition": "Trigger connection fired when a subjob completes successfully"},
        {"term": "context variable", "definition": "Environment-specific parameter (e.g., PROD vs TEST)"},
    ]

    # FAQ
    faq = [
        {
            "question": f"What does the {group} region cover?",
            "answer": f"The {group} region contains {job_count} Talend jobs handling data integration for the {group} Vaxxinova subsidiary.",
        },
        {
            "question": "How are environments managed?",
            "answer": "Environments (PROD, TEST, TEST_LINUX, TEST_MAC) are managed via Talend context variables, with different connection strings and paths per environment.",
        },
    ]

    # Risk dashboard
    risks = []
    if any("scratchpad" in j["name"].lower() or "test" in j["name"].lower() or "sandbox" in j["name"].lower() for j in chunk.get("jobs", [])):
        risks.append({
            "category": "Code Hygiene",
            "description": "Test/sandbox jobs found in the repository",
            "severity": "low",
            "affected_components": [j["name"] for j in chunk.get("jobs", []) if any(kw in j["name"].lower() for kw in ["test", "sandbox", "scratchpad"])],
        })

    onboarding = (
        f"## Getting Started with {group}\n\n"
        f"This region contains {job_count} Talend jobs. Key entry points:\n\n"
        + "\n".join(f"- **{jn}**" for jn in job_names[:10])
        + f"\n\nRefer to the component inventory and data flows sections for detailed information."
    )

    return {
        "executive_summary": exec_summary,
        "key_findings": key_findings,
        "faq": faq,
        "glossary": glossary,
        "onboarding_guide": onboarding,
        "risk_dashboard": risks,
    }


# ── Pass 11: Schema Dictionary ──────────────────────────────────────


def pass_11_schema_dictionary(chunk: dict) -> dict:
    tables = {}  # table_name -> {columns, used_by}

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        for comp in job.get("components", []):
            query = get_param(comp, "QUERY")
            if query:
                for table in extract_tables_from_query(query):
                    if table not in tables:
                        tables[table] = {"columns": [], "used_by": set()}
                    tables[table]["used_by"].add(job_name)

                    # Extract columns
                    cols = extract_columns_from_query(query)
                    for col in cols:
                        if col != "*" and col not in [c["column_name"] for c in tables[table]["columns"]]:
                            tables[table]["columns"].append({
                                "column_name": col,
                                "data_type": "",
                                "length": "",
                                "nullable": True,
                                "is_pk": False,
                                "is_fk": False,
                                "fk_references": "",
                                "description": "",
                            })

            # Check TABLE param for output components
            table_param = get_param(comp, "TABLE") or get_param(comp, "DATASET")
            if table_param and is_output_component(comp["component_type"]):
                clean_table = table_param.strip('"').strip("'")
                if clean_table and "context." not in clean_table:
                    if clean_table not in tables:
                        tables[clean_table] = {"columns": [], "used_by": set()}
                    tables[clean_table]["used_by"].add(job_name)

    table_list = []
    for tname, tdata in tables.items():
        table_list.append({
            "table_name": tname,
            "source_system": "",
            "columns": tdata["columns"],
            "used_by_jobs": sorted(tdata["used_by"]),
        })

    return {
        "tables": table_list,
        "unmapped_columns": [],
        "schema_diagram_mermaid": "",
    }


# ── Pass 12: Data Quality ───────────────────────────────────────────


def pass_12_data_quality(chunk: dict) -> dict:
    rules = []
    null_patterns = []
    coercion_risks = []
    rule_counter = 0

    for job in chunk.get("jobs", []):
        job_name = job["name"]

        for tmap in job.get("tmap_data", []):
            for entry in tmap.get("entries", []):
                expr = entry.get("expression", "")

                # Detect null checks
                if "null" in expr.lower() or "isnull" in expr.lower() or "nvl" in expr.lower():
                    rule_counter += 1
                    rules.append({
                        "rule_id": f"DQ-{rule_counter:03d}",
                        "job_name": job_name,
                        "component": "tMap",
                        "rule_type": "null_check",
                        "description": f"Null handling: {expr[:150]}",
                        "expression": expr[:200],
                        "severity": "medium",
                    })
                    null_patterns.append(f"{job_name}: {expr[:100]}")

                # Detect type coercions
                if any(kw in expr.lower() for kw in [
                    "parseint", "parsefloat", "parsedouble", "tostring",
                    "integer.valueof", "double.valueof", "bigdecimal",
                    "(int)", "(double)", "(float)", "(long)",
                ]):
                    rule_counter += 1
                    rules.append({
                        "rule_id": f"DQ-{rule_counter:03d}",
                        "job_name": job_name,
                        "component": "tMap",
                        "rule_type": "type_check",
                        "description": f"Type coercion: {expr[:150]}",
                        "expression": expr[:200],
                        "severity": "medium",
                    })
                    coercion_risks.append(f"{job_name}: {expr[:100]}")

                # Detect validation patterns
                if any(kw in expr.lower() for kw in [
                    ".equals(", ".contains(", ".matches(", ".startswith(",
                    ".endswith(", ".length()", "regex",
                ]):
                    rule_counter += 1
                    rules.append({
                        "rule_id": f"DQ-{rule_counter:03d}",
                        "job_name": job_name,
                        "component": "tMap",
                        "rule_type": "format_check",
                        "description": f"Format validation: {expr[:150]}",
                        "expression": expr[:200],
                        "severity": "low",
                    })

    # Overall score
    if len(rules) > 10:
        score = "good"
    elif len(rules) > 3:
        score = "adequate"
    else:
        score = "poor"

    recommendations = []
    if not null_patterns:
        recommendations.append("Consider adding explicit null checks in tMap expressions")
    if coercion_risks:
        recommendations.append("Review type coercions for potential data loss")

    return {
        "rules": rules[:50],
        "null_handling_patterns": null_patterns[:20],
        "type_coercion_risks": coercion_risks[:20],
        "overall_quality_score": score,
        "recommendations": recommendations,
    }


# ── Pass 13: Naming Conventions ──────────────────────────────────────


def pass_13_naming_conventions(chunk: dict) -> dict:
    group = chunk.get("group", "Global")

    # Collect all entity names
    job_names = [j["name"] for j in chunk.get("jobs", [])]
    comp_names = []
    conn_names = []

    for job in chunk.get("jobs", []):
        for comp in job.get("components", []):
            comp_names.append(comp["unique_name"])
        for conn in job.get("connections", []):
            conn_names.append(conn["name"])

    # Detect naming patterns
    detected_patterns = []
    region_codes = {}
    subsidiary_codes = {}
    layer_codes = {}

    # Region codes: BRA, USA, JPN, DEU, ITA, SAF, ZAF, Global
    region_pattern = re.compile(r"^(BRA|USA|JPN|DEU|ITA|SAF|ZAF|Global|NPL|VDA|Legacy)")
    for jn in job_names:
        m = region_pattern.match(jn)
        if m:
            code = m.group(1)
            region_codes[code] = region_codes.get(code, 0) + 1

    # Subsidiary codes: VBR, VIT, VJP, VUS, NPL, VDA, VSA, VDD
    sub_pattern = re.compile(r"_(VBR|VIT|VJP|VUS|NPL|VDA|VSA|VDD|BIO)_")
    for jn in job_names:
        m = sub_pattern.search(jn)
        if m:
            code = m.group(1)
            subsidiary_codes[code] = subsidiary_codes.get(code, 0) + 1

    # Layer codes: RAW, Clean, Raw, Forecast, etc.
    layer_pattern = re.compile(r"(RAW|Raw|Clean|Forecast|Report|Export|Staging|Global)")
    for jn in job_names:
        m = layer_pattern.search(jn)
        if m:
            code = m.group(1)
            layer_codes[code] = layer_codes.get(code, 0) + 1

    # Detect general patterns
    if region_codes:
        detected_patterns.append(f"Region prefix pattern: {', '.join(region_codes.keys())}")
    if subsidiary_codes:
        detected_patterns.append(f"Subsidiary code pattern: {', '.join(subsidiary_codes.keys())}")
    if layer_codes:
        detected_patterns.append(f"Layer naming pattern: {', '.join(layer_codes.keys())}")

    # Check for naming violations
    violations = []
    for jn in job_names:
        if " " in jn:
            violations.append({
                "entity_name": jn,
                "entity_type": "job",
                "expected_pattern": "No spaces in job names",
                "actual_pattern": "Contains spaces",
                "severity": "low",
            })
        if jn != jn.strip():
            violations.append({
                "entity_name": jn,
                "entity_type": "job",
                "expected_pattern": "No leading/trailing whitespace",
                "actual_pattern": "Has whitespace",
                "severity": "medium",
            })

    # Consistency score
    if violations:
        score = "inconsistent"
    elif detected_patterns:
        score = "good"
    else:
        score = "poor"

    return {
        "detected_patterns": detected_patterns,
        "region_code_usage": {k: f"{v} jobs" for k, v in region_codes.items()},
        "subsidiary_code_usage": {k: f"{v} jobs" for k, v in subsidiary_codes.items()},
        "layer_code_usage": {k: f"{v} jobs" for k, v in layer_codes.items()},
        "violations": violations,
        "consistency_score": score,
        "recommendations": [],
    }


# ── Pass 14: Scheduling & SLA ───────────────────────────────────────


def pass_14_scheduling_sla(chunk: dict, manifest: dict) -> dict:
    job_calls = manifest.get("job_calls", {})
    job_called_by = manifest.get("job_called_by", {})

    schedules = []
    critical_path = []
    bottleneck_jobs = []

    for job in chunk.get("jobs", []):
        job_name = job["name"]

        # Build dependency chain
        chain = []
        current = job_name
        visited = set()
        while current in job_called_by and current not in visited:
            visited.add(current)
            parents = job_called_by[current]
            if parents:
                chain.append(parents[0])
                current = parents[0]
            else:
                break

        # Estimate frequency from naming
        name_lower = job_name.lower()
        if "daily" in name_lower:
            freq = "daily"
        elif "hourly" in name_lower:
            freq = "hourly"
        elif "weekly" in name_lower:
            freq = "weekly"
        elif "monthly" in name_lower:
            freq = "monthly"
        else:
            freq = "on-demand"

        children = job_calls.get(job_name, [])
        chain_depth = len(chain)

        schedules.append({
            "job_name": job_name,
            "estimated_frequency": freq,
            "dependency_chain": list(reversed(chain)),
            "chain_depth": chain_depth,
            "estimated_duration": "unknown",
            "sla_recommendation": "",
        })

        # Detect bottleneck: jobs that call many children
        if len(children) > 3:
            bottleneck_jobs.append(job_name)

    # Build dependency diagram
    dep_lines = ["graph TD"]
    for job in chunk.get("jobs", []):
        jn = job["name"]
        safe_jn = jn.replace(" ", "_").replace("-", "_")
        for child in job_calls.get(jn, []):
            safe_child = child.replace(" ", "_").replace("-", "_")
            dep_lines.append(f"    {safe_jn} --> {safe_child}")

    dep_mermaid = "\n".join(dep_lines) if len(dep_lines) > 1 else ""

    return {
        "schedules": schedules,
        "critical_path": critical_path,
        "execution_windows": [],
        "bottleneck_jobs": bottleneck_jobs,
        "dependency_diagram_mermaid": dep_mermaid,
        "recommendations": [],
    }


# ── Pass 15: Security & Access Control ──────────────────────────────


def pass_15_security_access(chunk: dict) -> dict:
    findings = []
    pii_columns = []
    finding_counter = 0

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        for comp in job.get("components", []):
            for p in comp.get("params", []):
                val = p.get("value", "").lower()

                # Check for hardcoded credentials
                if p["name"].lower() in ("password", "secret", "api_key", "token"):
                    if val and "context." not in val and val not in ('""', "''", ""):
                        finding_counter += 1
                        findings.append({
                            "finding_id": f"SEC-{finding_counter:03d}",
                            "category": "hardcoded_secret",
                            "severity": "critical",
                            "location": f"{job_name}/{comp['unique_name']}",
                            "description": f"Potential hardcoded credential in param '{p['name']}'",
                            "remediation": "Use context variables for credential management",
                        })

    # Check context variables for credential patterns
    for ctx in chunk.get("related_contexts", []):
        for var in ctx.get("variables", []):
            vtype = var.get("type", "").lower()
            vname = var.get("name", "").lower()

            if "password" in vtype or "password" in vname:
                finding_counter += 1
                findings.append({
                    "finding_id": f"SEC-{finding_counter:03d}",
                    "category": "credential_exposure",
                    "severity": "medium",
                    "location": f"context/{ctx['name']}/{var['name']}",
                    "description": "Password-type context variable detected",
                    "remediation": "Ensure credential rotation and secure storage",
                })

            if any(kw in vname for kw in ["email", "phone", "address", "name", "ssn"]):
                pii_columns.append(f"{ctx['name']}.{var['name']}")

    # Credential management summary
    cred_mgmt = "Context variables are used for credential management across environments (PROD, TEST, etc.)"

    # Overall rating
    critical_count = sum(1 for f in findings if f["severity"] == "critical")
    if critical_count > 0:
        overall = "critical"
    elif findings:
        overall = "adequate"
    else:
        overall = "good"

    return {
        "findings": findings,
        "credential_management": cred_mgmt,
        "pii_columns": pii_columns,
        "data_classification": {},
        "encryption_usage": [],
        "overall_security_rating": overall,
        "recommendations": [
            "Rotate all credentials periodically",
            "Use a secrets manager instead of context variable files",
        ] if findings else [],
    }


# ── Pass 16: Performance ────────────────────────────────────────────


def pass_16_performance(chunk: dict) -> dict:
    jobs = []
    slow_candidates = []
    memory_intensive = []

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        heavy_ops = []
        perf_risks = []
        optimizations = []
        batch_sizes = {}

        comp_count = len(job.get("components", []))
        tmap_count = sum(1 for c in job.get("components", []) if c["component_type"] == "tMap")

        for comp in job.get("components", []):
            ct = comp["component_type"]

            # Detect heavy operations
            if ct == "tMap" and tmap_count > 3:
                heavy_ops.append(f"{comp['unique_name']}: complex transformation chain")

            if "join" in ct.lower() or "sort" in ct.lower():
                heavy_ops.append(f"{comp['unique_name']}: {ct} (memory-intensive)")
                memory_intensive.append(job_name)

            # Check for large query patterns
            query = get_param(comp, "QUERY")
            if query and "SELECT *" in query.upper():
                perf_risks.append(f"{comp['unique_name']}: SELECT * pattern — consider selecting specific columns")

            # Check batch sizes
            batch = get_param(comp, "BATCH_SIZE") or get_param(comp, "COMMIT_EVERY")
            if batch:
                batch_sizes[comp["unique_name"]] = batch

        if comp_count > 20:
            slow_candidates.append(job_name)
            perf_risks.append(f"Large job with {comp_count} components")

        if tmap_count > 5:
            perf_risks.append(f"{tmap_count} tMap components — consider consolidation")

        jobs.append({
            "job_name": job_name,
            "estimated_row_count": "",
            "heavy_operations": heavy_ops,
            "batch_sizes": batch_sizes,
            "parallel_execution": False,
            "performance_risks": perf_risks,
            "optimization_opportunities": optimizations,
        })

    # Overall rating
    if slow_candidates:
        overall = "adequate"
    elif memory_intensive:
        overall = "adequate"
    else:
        overall = "good"

    return {
        "jobs": jobs,
        "slow_job_candidates": slow_candidates,
        "memory_intensive_jobs": list(set(memory_intensive)),
        "overall_performance_rating": overall,
        "recommendations": [],
    }


# ── Pass 17: Cross-Region Analysis ──────────────────────────────────


def pass_17_cross_region(chunk: dict, all_chunks: list[dict]) -> dict:
    # Analyze across all chunks for this particular chunk
    current_group = chunk.get("group", "Global")

    # Collect per-region stats from all chunks
    region_stats = defaultdict(lambda: {"job_count": 0, "comp_types": Counter()})
    for c in all_chunks:
        g = c.get("group", "Global")
        region_stats[g]["job_count"] += len(c.get("jobs", []))
        for job in c.get("jobs", []):
            for comp in job.get("components", []):
                region_stats[g]["comp_types"][comp["component_type"]] += 1

    regions = []
    for region, stats in sorted(region_stats.items()):
        top_comps = [ct for ct, _ in stats["comp_types"].most_common(5)]
        regions.append({
            "region": region,
            "job_count": stats["job_count"],
            "components_used": top_comps,
            "unique_patterns": [],
            "deviations_from_standard": [],
        })

    # Find shared patterns (components used in 3+ regions)
    comp_by_region = defaultdict(set)
    for c in all_chunks:
        g = c.get("group", "Global")
        for job in c.get("jobs", []):
            for comp in job.get("components", []):
                comp_by_region[comp["component_type"]].add(g)

    shared = [ct for ct, regs in comp_by_region.items() if len(regs) >= 3]

    return {
        "regions": regions,
        "shared_patterns": shared[:20],
        "standardization_score": "medium",
        "region_comparison_mermaid": "",
        "recommendations": [],
    }


# ── Pass 18: Reusability ────────────────────────────────────────────


def pass_18_reusability(chunk: dict) -> dict:
    # Detect duplicate logic patterns
    duplicates = []
    shared_components = defaultdict(lambda: {"jobs": set(), "count": 0})
    code_smells = []

    # Track component usage patterns across jobs in this chunk
    comp_pattern_map = defaultdict(list)  # comp_type -> [job_names]

    for job in chunk.get("jobs", []):
        job_name = job["name"]
        for comp in job.get("components", []):
            ct = comp["component_type"]
            comp_pattern_map[ct].append(job_name)
            shared_components[ct]["jobs"].add(job_name)
            shared_components[ct]["count"] += 1

    # Find component types used by multiple jobs
    shared_list = []
    for ct, data in shared_components.items():
        if len(data["jobs"]) > 1:
            shared_list.append({
                "component_name": ct,
                "component_type": ct,
                "used_by": sorted(data["jobs"])[:10],
                "reuse_count": data["count"],
            })

    # Detect code smells
    for job in chunk.get("jobs", []):
        comp_count = len(job.get("components", []))
        if comp_count > 30:
            code_smells.append(f"{job['name']}: Large job with {comp_count} components — consider splitting")

        # Check for duplicate tMap patterns
        tmap_exprs = []
        for tmap in job.get("tmap_data", []):
            for entry in tmap.get("entries", []):
                tmap_exprs.append(entry.get("expression", ""))
        expr_counts = Counter(tmap_exprs)
        for expr, count in expr_counts.items():
            if count > 2 and expr:
                duplicates.append({
                    "description": f"Repeated expression in {job['name']}: {expr[:100]}",
                    "locations": [job["name"]],
                    "lines_duplicated": count,
                    "refactoring_suggestion": "Extract to a shared routine or variable",
                })

    # Joblet usage
    joblets = []
    for job in chunk.get("jobs", []):
        for comp in job.get("components", []):
            if "joblet" in comp["component_type"].lower():
                joblets.append(f"{comp['unique_name']} in {job['name']}")

    # Score
    if duplicates or code_smells:
        score = "poor"
    elif shared_list:
        score = "good"
    else:
        score = "adequate"

    return {
        "duplicated_logic": duplicates[:20],
        "shared_components": shared_list[:20],
        "joblet_usage": joblets,
        "code_smells": code_smells,
        "reusability_score": score,
        "refactoring_roadmap": [],
    }


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    print("Loading scan metadata...")
    manifest = load_json(SCAN_DIR / "manifest.json")

    print(f"Loading chunks from {CHUNKS_DIR}...")
    chunk_files = sorted(CHUNKS_DIR.glob("*.json"))
    chunks = []
    for cf in chunk_files:
        chunks.append(load_json(cf))
    print(f"  Loaded {len(chunks)} chunks")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pass_names = {
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

    total = 0
    for pass_num in range(1, 19):
        pname = pass_names[pass_num]
        print(f"\nPass {pass_num:2d}: {pname}")

        for chunk in chunks:
            chunk_id = chunk["chunk_id"]

            if pass_num == 1:
                data = pass_01_component_inventory(chunk, manifest)
            elif pass_num == 2:
                data = pass_02_execution_logic(chunk, manifest)
            elif pass_num == 3:
                data = pass_03_data_flows(chunk)
            elif pass_num == 4:
                data = pass_04_environment_config(chunk)
            elif pass_num == 5:
                data = pass_05_external_systems(chunk, manifest)
            elif pass_num == 6:
                data = pass_06_error_handling(chunk)
            elif pass_num == 7:
                data = pass_07_state_side_effects(chunk)
            elif pass_num == 8:
                data = pass_08_custom_code(chunk)
            elif pass_num == 9:
                data = pass_09_business_logic(chunk)
            elif pass_num == 10:
                data = pass_10_synthesis(chunk, {})
            elif pass_num == 11:
                data = pass_11_schema_dictionary(chunk)
            elif pass_num == 12:
                data = pass_12_data_quality(chunk)
            elif pass_num == 13:
                data = pass_13_naming_conventions(chunk)
            elif pass_num == 14:
                data = pass_14_scheduling_sla(chunk, manifest)
            elif pass_num == 15:
                data = pass_15_security_access(chunk)
            elif pass_num == 16:
                data = pass_16_performance(chunk)
            elif pass_num == 17:
                data = pass_17_cross_region(chunk, chunks)
            elif pass_num == 18:
                data = pass_18_reusability(chunk)
            else:
                data = {}

            save_pass_result(pass_num, pname, chunk_id, data)
            total += 1
            print(f"  {chunk_id} OK", end="", flush=True)

        print()

    print(f"\nDone! Generated {total} pass result files in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
