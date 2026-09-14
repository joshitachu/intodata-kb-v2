"""Compare semantic scope workload against a saved atomic plan, without models."""
import argparse
import json
import tempfile
from pathlib import Path

from mcp_server.providers.custom.knowledge_contracts import KnowledgePlan
from mcp_server.providers.custom.landscape_scopes import prepare_semantic_scopes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--scope-bytes", type=int, default=400000)
    parser.add_argument("--max-units", type=int, default=6)
    args = parser.parse_args()
    atomic = KnowledgePlan.model_validate_json(args.plan.read_text())
    with tempfile.TemporaryDirectory(prefix="nexus-scope-evaluation-") as temp:
        plan = prepare_semantic_scopes(staging_dir=Path(temp), atomic_plan=atomic,
                                       max_scope_evidence_bytes=args.scope_bytes,
                                       max_units_per_scope=args.max_units)
        assignments = [eid for task in plan.tasks for eid in task.evidence_ids]
        assigned = set(assignments)
        print(json.dumps({
            "atomic_tasks": len(atomic.tasks), "semantic_scopes": len(plan.tasks),
            "units": len({r.unit_id for r in atomic.manifest.requirements}),
            "scoped_units": len({u for task in plan.tasks for u in task.unit_ids}),
            "evidence_count": len(atomic.manifest.evidence),
            "evidence_assigned": len(assigned),
            "evidence_assignment_count": len(assignments),
            "repeated_evidence_assignments": len(assignments) - len(assigned),
            "evidence_unchanged": plan.manifest.evidence == atomic.manifest.evidence,
            "model_calls": 0, "cost_and_quality": "not_measured",
        }, indent=2))


if __name__ == "__main__":
    main()
