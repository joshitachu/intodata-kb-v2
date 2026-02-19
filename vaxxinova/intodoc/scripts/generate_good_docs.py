#!/usr/bin/env python3
"""Generate the 5 'good' docs from synthesis (p10) and business logic (p09) data.

Produces:
  - 00_PROJECT_OVERVIEW.md  (from p10 executive summaries + scan metadata)
  - 10_BUSINESS_LOGIC.md    (from p09 business rules)
  - 11_KNOWN_ISSUES.md      (from p10 risk dashboard)
  - 14_GLOSSARY_FAQ.md      (from p10 glossary + FAQ)
  - 15_ONBOARDING.md        (from p10 onboarding guides)

Usage:
    python intodoc/scripts/generate_good_docs.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
PASS_RESULTS_DIR = DOCS_DIR / "pass_results"
CHUNKS_FILE = DOCS_DIR / "chunks_summary.json"
MANIFEST_FILE = DOCS_DIR / "scan" / "manifest.json"
CONNECTIONS_FILE = DOCS_DIR / "scan" / "connections.json"


def _load_pass(pass_num: int) -> list[dict]:
    files = sorted(PASS_RESULTS_DIR.glob(f"p{pass_num:02d}_*.json"))
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def _load_chunks_summary() -> list[dict]:
    return json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))


def _chunk_to_region(chunk_id: str) -> str:
    return chunk_id.split("_")[0]


def _region_label(region: str) -> str:
    labels = {
        "BRA": "Brazil (BRA)",
        "DEU": "Germany (DEU)",
        "ITA": "Italy (ITA)",
        "JPN": "Japan (JPN)",
        "USA": "United States (USA)",
        "SAF": "South Africa (SAF)",
        "ZAF": "South Africa (ZAF)",
        "Global": "Global",
        "GENERIC": "Generic / Shared",
        "scratchpad": "Scratchpad / Development",
    }
    return labels.get(region, region)


# ── 00_PROJECT_OVERVIEW ─────────────────────────────────────────────

def generate_00(p10_files: list[dict], chunks: list[dict]) -> str:
    lines = [
        "# Project Overview",
        "",
        "> Vaxxinova Talend Integration Platform -- Comprehensive Documentation",
        "",
        "## Executive Summary",
        "",
        "This documentation covers Vaxxinova's Talend-based data integration platform, which manages the ETL pipelines connecting on-premises ERP systems, Google BigQuery (the central data warehouse), and Salesforce CRM across multiple global subsidiaries. The platform processes sales, product, customer, and financial data for veterinary vaccine operations spanning Brazil, Germany, Italy, Japan, the United States, and South Africa.",
        "",
    ]

    # Platform stats from chunks
    total_jobs = sum(c["job_count"] for c in chunks)
    regions = sorted(set(c["group"] for c in chunks))
    lines.append(f"The platform comprises **{total_jobs} Talend jobs** organized across **{len(regions)} regions**: {', '.join(regions)}.")
    lines.append("")

    # Region job count table
    from collections import Counter as Ctr
    region_jobs = Ctr()
    for c in chunks:
        region_jobs[c["group"]] += c["job_count"]
    lines.append("| Region | Jobs | Description |")
    lines.append("|--------|------|-------------|")
    region_descs = {
        "BRA": "Brazil -- Biovet/VBR subsidiaries, legacy MSSQL + Business Central",
        "DEU": "Germany -- VDA subsidiary",
        "ITA": "Italy -- VIT subsidiary",
        "JPN": "Japan -- VJP subsidiary, largest APAC presence",
        "USA": "United States -- NPL + VUS subsidiaries",
        "SAF": "South Africa -- VSA subsidiary",
        "ZAF": "South Africa -- additional ZAF entity",
        "Global": "Cross-region consolidation, budget, forecast pipelines",
        "GENERIC": "Shared utilities, reference data loaders",
        "scratchpad": "Development/test jobs",
    }
    for r in sorted(region_jobs.keys()):
        lines.append(f"| {r} | {region_jobs[r]} | {region_descs.get(r, '')} |")
    lines.append("")

    # Architecture overview
    lines.append("## Platform Architecture")
    lines.append("")
    lines.append("The Vaxxinova integration platform follows a three-layer architecture:")
    lines.append("")
    lines.append("1. **Raw Layer**: Source system data is extracted from on-premises MSSQL databases, Business Central APIs, Salesforce, and flat files, then landed in BigQuery datasets with a `raw` prefix (e.g., `bra_vbr_raw`). Jobs in this layer perform minimal transformation -- mostly column mapping and CSV staging.")
    lines.append("")
    lines.append("2. **Clean Layer**: Raw data is enriched with reference data (fiscal periods, country master, currency conversion rates), deduplicated, and joined into business-ready datasets. The primary output is the `Sales_Set` table, which consolidates invoice and credit memo data across all subsidiaries.")
    lines.append("")
    lines.append("3. **Global Layer**: Regional clean datasets are consolidated into global reporting tables. Budget, forecast, and prior-year data are processed separately and merged with actuals for management dashboards. Salesforce CRM is synced with product and account master data.")
    lines.append("")
    lines.append("### Key Technology Stack")
    lines.append("")
    lines.append("- **ETL Engine**: Talend Open Studio / Data Integration")
    lines.append("- **Data Warehouse**: Google BigQuery")
    lines.append("- **Source Systems**: Microsoft SQL Server (on-prem ERPs), Microsoft Dynamics 365 Business Central, Salesforce CRM")
    lines.append("- **Staging**: Google Cloud Storage (temp CSV files)")
    lines.append("- **Orchestration**: Talend context variables for environment management (PROD, TEST, TEST_LINUX, TEST_MAC)")
    lines.append("")

    # Group summaries by region
    region_summaries = defaultdict(list)
    for f in p10_files:
        region = _chunk_to_region(f["chunk_id"])
        summary = f["data"].get("executive_summary", "")
        if summary:
            region_summaries[region].append(summary)

    lines.append("## Regional Overview")
    lines.append("")

    for region in sorted(region_summaries.keys()):
        summaries = region_summaries[region]
        lines.append(f"### {_region_label(region)}")
        lines.append("")
        # Use the first (most representative) summary, truncated
        combined = summaries[0]
        if len(combined) > 1500:
            combined = combined[:1500] + "..."
        lines.append(combined)
        lines.append("")
        if len(summaries) > 1:
            lines.append(f"*{len(summaries)} analysis chunks cover this region's full pipeline.*")
            lines.append("")

    # Key findings across all chunks
    lines.append("## Platform-Wide Key Findings")
    lines.append("")
    seen = set()
    count = 0
    for f in sorted(p10_files, key=lambda x: x["chunk_id"]):
        for finding in f["data"].get("key_findings", []):
            # Deduplicate similar findings
            key = finding[:80].lower()
            if key not in seen and count < 25:
                seen.add(key)
                lines.append(f"- {finding}")
                count += 1
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Generated by intodoc -- documentation automation for integration platforms.*")
    return "\n".join(lines)


# ── 10_BUSINESS_LOGIC ───────────────────────────────────────────────

def generate_10(p09_files: list[dict]) -> str:
    lines = [
        "# Business Logic",
        "",
        "This document catalogs the business rules embedded in Vaxxinova's Talend ETL pipelines. These rules govern how sales data is transformed, validated, and enriched as it flows from regional ERP systems through to BigQuery and Salesforce. Understanding these rules is critical for migration planning -- each rule must be faithfully replicated in any target platform.",
        "",
    ]

    # Group rules by domain, then by region
    domain_rules = defaultdict(list)
    for f in p09_files:
        region = _chunk_to_region(f["chunk_id"])
        for rule in f["data"].get("rules", []):
            domain = rule.get("domain", "Other")
            domain_rules[domain].append({
                "rule_id": rule.get("rule_id", ""),
                "region": region,
                "description": rule.get("description", ""),
                "implemented_in": rule.get("implemented_in", []),
                "impact": rule.get("impact_if_wrong", ""),
            })

    for domain in sorted(domain_rules.keys()):
        rules = domain_rules[domain]
        lines.append(f"## {domain}")
        lines.append("")
        lines.append(f"*{len(rules)} business rules identified across the platform.*")
        lines.append("")

        # Group by region within domain
        by_region = defaultdict(list)
        for r in rules:
            by_region[r["region"]].append(r)

        for region in sorted(by_region.keys()):
            region_rules = by_region[region]
            lines.append(f"### {_region_label(region)}")
            lines.append("")

            cap = 3  # Cap per region to keep doc under 800 lines
            for rule in region_rules[:cap]:
                lines.append(f"**{rule['rule_id']}**: {rule['description']}")
                if rule["implemented_in"]:
                    lines.append(f"  - *Implemented in*: {', '.join(rule['implemented_in'])}")
                if rule["impact"]:
                    lines.append(f"  - *Impact if wrong*: {rule['impact']}")
                lines.append("")

            if len(region_rules) > cap:
                lines.append(f"*... and {len(region_rules) - cap} additional rules in this region.*")
                lines.append("")

    # Migration takeaway
    lines.append("## Migration Considerations")
    lines.append("")
    lines.append("Each business rule above represents logic that must be faithfully replicated in the target platform. Key considerations:")
    lines.append("")
    lines.append("- **Sales consolidation logic** (return negation, invoice/credit memo merging) is the highest-risk area for migration errors")
    lines.append("- **Fiscal period mapping** depends on the Global_Raw.Fiscal_Periods reference table and must be migrated first")
    lines.append("- **Currency conversion** rules differ by region and data category (actuals vs. budget vs. forecast)")
    lines.append("- **Salesforce sync rules** include specific field mappings and external ID conventions that must be preserved")
    lines.append("- **Entity assignment** logic varies by subsidiary and must be validated per-region during migration testing")
    lines.append("")

    return "\n".join(lines)


# ── 11_KNOWN_ISSUES ─────────────────────────────────────────────────

def generate_11(p10_files: list[dict]) -> str:
    lines = [
        "# Known Issues & Risk Dashboard",
        "",
        "This document aggregates all identified risks, issues, and technical debt across the Vaxxinova Talend platform. Issues are categorized by severity and type to support prioritized remediation and migration planning.",
        "",
    ]

    # Collect all risks
    all_risks = []
    for f in p10_files:
        region = _chunk_to_region(f["chunk_id"])
        for risk in f["data"].get("risk_dashboard", []):
            all_risks.append({
                "region": region,
                "chunk": f["chunk_id"],
                "category": risk.get("category", "Other"),
                "description": risk.get("description", ""),
                "severity": risk.get("severity", "medium"),
                "affected": risk.get("affected_components", []),
            })

    # Summary stats
    from collections import Counter
    sev_counts = Counter(r["severity"] for r in all_risks)
    cat_counts = Counter(r["category"] for r in all_risks)

    lines.append("## Summary")
    lines.append("")
    lines.append(f"**{len(all_risks)} issues identified** across the platform:")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|----------|-------|")
    for sev in ["critical", "high", "medium", "low"]:
        if sev_counts.get(sev, 0) > 0:
            lines.append(f"| {sev.title()} | {sev_counts[sev]} |")
    lines.append("")

    lines.append("| Category | Count |")
    lines.append("|----------|-------|")
    for cat, count in cat_counts.most_common():
        lines.append(f"| {cat} | {count} |")
    lines.append("")

    # Critical and High issues in detail; medium/low summarized
    for severity in ["critical", "high"]:
        issues = [r for r in all_risks if r["severity"] == severity]
        if not issues:
            continue
        lines.append(f"## {severity.title()} Severity Issues ({len(issues)})")
        lines.append("")

        for issue in issues:
            lines.append(f"### [{issue['category']}] {issue['region']} / {issue['chunk']}")
            lines.append("")
            lines.append(issue["description"])
            if issue["affected"]:
                lines.append("")
                lines.append(f"**Affected components**: {', '.join(issue['affected'][:5])}")
            lines.append("")

    # Medium and low: summarize by category
    for severity in ["medium", "low"]:
        issues = [r for r in all_risks if r["severity"] == severity]
        if not issues:
            continue
        lines.append(f"## {severity.title()} Severity Issues ({len(issues)})")
        lines.append("")

        by_cat = defaultdict(list)
        for issue in issues:
            by_cat[issue["category"]].append(issue)
        for cat in sorted(by_cat.keys()):
            cat_issues = by_cat[cat]
            regions = sorted(set(i["region"] for i in cat_issues))
            lines.append(f"- **{cat}** ({len(cat_issues)} issues): Affects {', '.join(regions)}")
            # Show first 2 examples
            for ex in cat_issues[:2]:
                lines.append(f"  - {ex['description'][:150]}{'...' if len(ex['description']) > 150 else ''}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*This risk dashboard should be reviewed regularly and updated as issues are remediated or new risks are identified during migration.*")
    return "\n".join(lines)


# ── 14_GLOSSARY_FAQ ─────────────────────────────────────────────────

def generate_14(p10_files: list[dict]) -> str:
    lines = [
        "# Glossary & FAQ",
        "",
        "This document provides definitions for key terms and answers to common questions about the Vaxxinova Talend integration platform. It is intended for onboarding new team members and as a quick reference for anyone working with the platform.",
        "",
    ]

    # Collect and deduplicate glossary terms
    terms = {}
    for f in p10_files:
        for entry in f["data"].get("glossary", []):
            term = entry.get("term", "")
            defn = entry.get("definition", "")
            if term and term not in terms:
                terms[term] = defn
            elif term and len(defn) > len(terms.get(term, "")):
                terms[term] = defn  # Keep longer definition

    lines.append("## Glossary")
    lines.append("")
    # Cap at 80 most important terms
    sorted_terms = sorted(terms.keys(), key=str.lower)[:80]
    for term in sorted_terms:
        lines.append(f"**{term}**")
        lines.append(f": {terms[term]}")
        lines.append("")
    if len(terms) > 80:
        lines.append(f"*... and {len(terms) - 80} additional terms.*")
        lines.append("")

    # Collect and deduplicate FAQ
    faqs = {}
    for f in p10_files:
        region = _chunk_to_region(f["chunk_id"])
        for entry in f["data"].get("faq", []):
            q = entry.get("question", "")
            a = entry.get("answer", "")
            if q and q not in faqs:
                faqs[q] = {"answer": a, "region": region}

    lines.append("## Frequently Asked Questions")
    lines.append("")
    # Cap at 40 FAQ entries
    for i, (q, info) in enumerate(list(faqs.items())[:40], 1):
        lines.append(f"### {i}. {q}")
        lines.append("")
        lines.append(info["answer"])
        lines.append("")
    if len(faqs) > 40:
        lines.append(f"*... and {len(faqs) - 40} additional questions.*")
        lines.append("")

    return "\n".join(lines)


# ── 15_ONBOARDING ───────────────────────────────────────────────────

def generate_15(p10_files: list[dict]) -> str:
    lines = [
        "# Onboarding Guide",
        "",
        "This guide helps new team members get up to speed with the Vaxxinova Talend integration platform. Each section covers a specific region's pipeline, including architecture, key jobs, environment setup, and common debugging steps.",
        "",
    ]

    # Group onboarding guides by region
    region_guides = defaultdict(list)
    for f in p10_files:
        region = _chunk_to_region(f["chunk_id"])
        guide = f["data"].get("onboarding_guide", "")
        if guide:
            region_guides[region].append({
                "chunk": f["chunk_id"],
                "guide": guide,
            })

    for region in sorted(region_guides.keys()):
        guides = region_guides[region]
        lines.append(f"## {_region_label(region)}")
        lines.append("")

        # Use only the first 2 guides per region to keep doc size manageable
        for g in guides[:2]:
            lines.append(f"### {g['chunk']}")
            lines.append("")
            # Truncate very long guides
            guide_text = g["guide"]
            guide_lines = guide_text.split("\n")
            if len(guide_lines) > 40:
                guide_text = "\n".join(guide_lines[:40]) + "\n\n*[Guide truncated for brevity]*"
            lines.append(guide_text)
            lines.append("")

        if len(guides) > 2:
            remaining_chunks = [g["chunk"] for g in guides[2:]]
            lines.append(f"*Additional onboarding guides available for chunks: {', '.join(remaining_chunks)}*")
            lines.append("")

    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────

def main() -> None:
    print("Loading synthesis (p10) and business logic (p09) data...")
    p10 = _load_pass(10)
    p09 = _load_pass(9)
    chunks = _load_chunks_summary()
    print(f"  p10: {len(p10)} chunks, p09: {len(p09)} chunks")

    docs = {
        "00_PROJECT_OVERVIEW.md": generate_00(p10, chunks),
        "10_BUSINESS_LOGIC.md": generate_10(p09),
        "11_KNOWN_ISSUES.md": generate_11(p10),
        "14_GLOSSARY_FAQ.md": generate_14(p10),
        "15_ONBOARDING.md": generate_15(p10),
    }

    for name, content in docs.items():
        path = DOCS_DIR / name
        path.write_text(content, encoding="utf-8")
        line_count = content.count("\n") + 1
        size_kb = len(content.encode()) / 1024
        print(f"  {name}: {line_count} lines, {size_kb:.1f} KB")

    print("Done.")


if __name__ == "__main__":
    main()
