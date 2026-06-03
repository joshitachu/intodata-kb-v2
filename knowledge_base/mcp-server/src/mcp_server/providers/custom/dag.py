"""Execution graph planning helpers for the custom harness."""

from __future__ import annotations

import hashlib
from collections import deque

from mcp_server.config import config
from mcp_server.providers.custom.models import (
    ExecutionGraph,
    ExecutionNode,
    ExecutionPlan,
    FactsManifest,
    GroupExecutionPlan,
)


FINAL_GATE_NODE_IDS = {"validate_docs", "enterprise_gates"}


def deterministic_execution_plan(
    facts: FactsManifest,
    *,
    required_docs: list[str],
    preprocess_groups: list[str],
    investigation_policy: str,
    analysis_policy: str,
    scorecard_policy: str,
    doc_bundles_policy: str,
    plugin_mode: bool,
    small_project_threshold: int,
) -> ExecutionPlan:
    use_direct = facts.unit_count <= small_project_threshold
    if investigation_policy == "skip":
        investigation_mode = "skip"
    elif use_direct:
        investigation_mode = "skip"
    else:
        investigation_mode = "full"

    if analysis_policy == "skip":
        analysis_mode = "skip"
    elif analysis_policy == "bounded" or (analysis_policy == "run" and use_direct):
        analysis_mode = "direct"
    else:
        analysis_mode = "full"

    groups = {
        group: GroupExecutionPlan(
            group=group,
            depth="shallow" if analysis_mode in {"direct", "skip"} else "deep",
            tool_budget="none",
            reasons=["deterministic fallback", f"{facts.groups.get(group, 0)} units"],
        )
        for group in preprocess_groups
    }
    plan = ExecutionPlan(
        mode="deterministic",
        planner_enabled=False,
        dag_enabled=config.agent_dag_enabled,
        investigation_mode=investigation_mode,
        analysis_mode=analysis_mode,
        required_docs=list(required_docs),
        scorecard=scorecard_policy != "skip",
        doc_bundles=doc_bundles_policy != "skip",
        critic=False,
        consistency=not plugin_mode,
        groups=groups,
        reasons=["deterministic graph mirrors current harness policies"],
    )
    plan.graph = build_execution_graph(plan, facts)
    return plan


def build_execution_graph(plan: ExecutionPlan, facts: FactsManifest) -> ExecutionGraph:
    nodes: list[ExecutionNode] = [
        _node("planner", "planner", outputs=["_plans/execution_plan.json"]),
        _node("facts", "facts", depends_on=["planner"], outputs=["facts_manifest.json"]),
    ]
    previous = "facts"
    if plan.investigation_mode != "skip":
        nodes.append(
            _node(
                "investigation",
                "investigation",
                depends_on=[previous],
                outputs=["_investigation/_summary.md"],
                validators=["investigation_coverage"],
                tool_policy="standard" if plan.investigation_mode == "manifest_agentic" else "none",
            )
        )
        previous = "investigation"

    if plan.analysis_mode == "skeleton_plus_deep_dive":
        skeleton_ids: list[str] = []
        deep_ids: list[str] = []
        for group, group_plan in sorted(plan.groups.items()):
            if group_plan.depth == "skip":
                continue
            safe = _safe_id(group)
            skeleton_id = f"phase2_skeleton:{safe}"
            nodes.append(
                _node(
                    skeleton_id,
                    "phase2_skeleton",
                    depends_on=[previous],
                    outputs=[f"_analysis/{group}.skeleton.md"],
                    target=group,
                )
            )
            skeleton_ids.append(skeleton_id)
            if group_plan.depth == "deep":
                deep_id = f"phase2_deep_dive:{safe}"
                nodes.append(
                    _node(
                        deep_id,
                        "phase2_deep_dive",
                        depends_on=[skeleton_id],
                        outputs=[f"_analysis/{group}.deep.md"],
                        validators=["analysis_depth"],
                        tool_policy=group_plan.tool_budget if group_plan.tool_budget != "none" else "standard",
                        target=group,
                    )
                )
                deep_ids.append(deep_id)
        merge_deps = deep_ids or skeleton_ids or [previous]
        nodes.append(
            _node(
                "phase2_merge",
                "phase2_merge",
                depends_on=merge_deps,
                outputs=["_analysis/*.md"],
                validators=["analysis_coverage", "analysis_depth", "analysis_guide_sections"],
            )
        )
        previous = "phase2_merge"
    elif plan.analysis_mode != "skip":
        nodes.append(
            _node(
                "phase2",
                "phase2_merge",
                depends_on=[previous],
                outputs=["_analysis/*.md"],
                validators=["analysis_coverage", "analysis_depth", "analysis_guide_sections"],
            )
        )
        previous = "phase2"
    else:
        nodes.append(
            _node(
                "phase2_pack_records",
                "phase2_merge",
                depends_on=[previous],
                outputs=["_analysis/_coverage_units/*.json"],
                validators=["analysis_coverage"],
            )
        )
        previous = "phase2_pack_records"

    if plan.scorecard:
        nodes.append(_node("scorecard", "scorecard", depends_on=[previous], outputs=["_assessment/scorecard.md"]))
        previous = "scorecard"
    if plan.doc_bundles:
        nodes.append(_node("doc_bundle", "doc_bundle", depends_on=[previous], outputs=["_analysis/_doc_pack_*.md"]))
        previous = "doc_bundle"

    doc_ids: list[str] = []
    for doc in plan.required_docs:
        doc_id = f"doc_write:{_safe_id(doc)}"
        nodes.append(
            _node(
                doc_id,
                "doc_write",
                depends_on=[previous],
                outputs=[f"{doc}.md"],
                validators=["doc_markdown"],
                target=doc,
                tool_policy="standard",
            )
        )
        doc_ids.append(doc_id)
    previous_docs = doc_ids or [previous]

    if plan.critic:
        nodes.append(_node("critic", "critic", depends_on=previous_docs, outputs=["_assessment/critic_report.json"], tool_policy="standard"))
        previous_docs = ["critic"]
    if plan.consistency:
        nodes.append(
            _node(
                "consistency",
                "consistency",
                depends_on=previous_docs,
                outputs=["_assessment/consistency_report.json"],
                validators=["consistency"],
                tool_policy="standard",
            )
        )
        previous_docs = ["consistency"]
    nodes.append(
        _node(
            "validate_docs",
            "validation_gate",
            depends_on=previous_docs,
            outputs=["_assessment/doc_validation_report.json", "_assessment/doc_depth_report.json"],
            validators=["validate_docs", "validate_doc_depth"],
        )
    )
    nodes.append(
        _node(
            "enterprise_gates",
            "validation_gate",
            depends_on=["validate_docs"],
            outputs=["_assessment/enterprise_gates.json"],
            validators=["enterprise_gates"],
        )
    )
    graph = ExecutionGraph(
        nodes=nodes,
        global_constraints={
            "unit_count": facts.unit_count,
            "required_docs": list(plan.required_docs),
            "final_gates_required": sorted(FINAL_GATE_NODE_IDS),
        },
    )
    for node in graph.nodes:
        node.cache_key = _cache_key(node, facts)
    return graph


def validate_execution_plan(
    plan: ExecutionPlan,
    facts: FactsManifest,
    *,
    allowed_docs: list[str],
    max_nodes: int | None = None,
    max_depth: int | None = None,
) -> list[str]:
    errors: list[str] = []
    allowed = set(allowed_docs)
    undeclared = sorted(set(plan.required_docs) - allowed)
    if undeclared:
        errors.append("Plan requested undeclared docs: " + ", ".join(undeclared))
    if not plan.required_docs:
        errors.append("Plan has no required docs")
    manifest_groups = set(facts.groups)
    outside_groups = sorted(set(plan.groups) - manifest_groups)
    if outside_groups:
        errors.append("Plan referenced unknown groups: " + ", ".join(outside_groups))
    graph = plan.graph
    if len(graph.nodes) > (max_nodes or config.agent_dag_max_nodes):
        errors.append(f"Graph has too many nodes: {len(graph.nodes)}")
    ids = [node.id for node in graph.nodes]
    duplicate_ids = sorted({node_id for node_id in ids if ids.count(node_id) > 1})
    if duplicate_ids:
        errors.append("Graph has duplicate node ids: " + ", ".join(duplicate_ids))
    id_set = set(ids)
    for node in graph.nodes:
        missing = sorted(set(node.depends_on) - id_set)
        if missing:
            errors.append(f"Node {node.id} depends on missing nodes: {', '.join(missing)}")
    if FINAL_GATE_NODE_IDS - id_set:
        errors.append("Graph is missing required final gates: " + ", ".join(sorted(FINAL_GATE_NODE_IDS - id_set)))
    cycle = _cycle_errors(graph)
    errors.extend(cycle)
    if not cycle:
        depth = _graph_depth(graph)
        if depth > (max_depth or config.agent_dag_max_depth):
            errors.append(f"Graph depth {depth} exceeds limit")
    return errors


def _node(
    node_id: str,
    node_type: str,
    *,
    depends_on: list[str] | None = None,
    outputs: list[str] | None = None,
    validators: list[str] | None = None,
    tool_policy: str = "none",
    target: str | None = None,
) -> ExecutionNode:
    return ExecutionNode(
        id=node_id,
        type=node_type,  # type: ignore[arg-type]
        depends_on=depends_on or [],
        outputs=outputs or [],
        validators=validators or [],
        tool_policy=tool_policy,  # type: ignore[arg-type]
        target=target,
    )


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def _cache_key(node: ExecutionNode, facts: FactsManifest) -> str:
    payload = "|".join([
        node.id,
        node.type,
        ",".join(node.depends_on),
        ",".join(node.inputs),
        ",".join(node.outputs),
        facts.project_id,
        str(facts.unit_count),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cycle_errors(graph: ExecutionGraph) -> list[str]:
    ids = {node.id for node in graph.nodes}
    indegree = {node_id: 0 for node_id in ids}
    children = {node_id: [] for node_id in ids}
    for node in graph.nodes:
        for dep in node.depends_on:
            if dep in ids:
                indegree[node.id] += 1
                children[dep].append(node.id)
    queue = deque([node_id for node_id, degree in indegree.items() if degree == 0])
    seen = 0
    while queue:
        node_id = queue.popleft()
        seen += 1
        for child in children[node_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if seen != len(ids):
        return ["Graph contains a cycle"]
    return []


def _graph_depth(graph: ExecutionGraph) -> int:
    by_id = {node.id: node for node in graph.nodes}
    memo: dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        node = by_id[node_id]
        if not node.depends_on:
            memo[node_id] = 1
        else:
            memo[node_id] = 1 + max(depth(dep) for dep in node.depends_on if dep in by_id)
        return memo[node_id]

    return max((depth(node.id) for node in graph.nodes), default=0)
