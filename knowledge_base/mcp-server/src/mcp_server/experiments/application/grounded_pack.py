"""Strict, provider-neutral Grounded orchestration contract compiled from recipe data."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from mcp_server.experiments.domain.recipes import AgenticRecipeManifest


@runtime_checkable
class _BatchLike(Protocol):
    """Minimal structural view of a planner batch (see analysis_batch_planner)."""

    @property
    def batch_id(self) -> str: ...

    @property
    def unit_names(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class GroundedRole:
    id: str
    agent: str
    stage: str
    owns: tuple[str, ...]
    depends_on: tuple[str, ...]
    source_globs: tuple[str, ...]
    max_context_bytes: int
    model_policy: str = "inherit"
    failure_policy: str = "fail"
    # Fan-out template marker. When non-empty this role is not run directly:
    # the runner expands it into concrete roles over the computed evidence
    # batches (see ``expand_grounded_fanout``). Only ``"evidence_batches"`` is
    # supported today. Two stages may declare a fan-out template:
    #   - ``analysis``: one role per batch, owning ``...${batch_id}...``.
    #   - ``writing``: one role per batch, owning one deep per-unit document per
    #     unit in the batch via a ``...${unit}...`` template (Tier-A docs).
    fanout: str = ""
    # Owned-artifact pattern for a fan-out template. Analysis templates use a
    # single ``${batch_id}`` placeholder (one file per batch); writing templates
    # use a single ``${unit}`` placeholder (one document per unit).
    owns_template: str = ""
    # Canonical unit names assigned to an expanded batch role (runtime only).
    units: tuple[str, ...] = ()
    # For an expanded writing (Tier-A) batch role: ``(unit_name, owned_doc_path)``
    # pairs, one per unit in the batch. Empty for every other role. Lets the
    # runner map each owned per-unit document back to its unit deterministically
    # without re-deriving filenames.
    unit_docs: tuple[tuple[str, str], ...] = ()


BATCH_ID_PLACEHOLDER = "${batch_id}"
UNIT_PLACEHOLDER = "${unit}"
SUPPORTED_FANOUT_KINDS = frozenset({"evidence_batches"})
SUPPORTED_FANOUT_STAGES = frozenset({"analysis", "writing"})
_FANOUT_PLACEHOLDER = {"analysis": BATCH_ID_PLACEHOLDER, "writing": UNIT_PLACEHOLDER}
# Role-id prefix for an expanded Tier-A per-unit writer (one per evidence batch).
WRITER_ROLE_PREFIX = "unitdoc_"


def writer_role_id(batch_id: str) -> str:
    return f"{WRITER_ROLE_PREFIX}{batch_id}"


def batch_id_from_writer_role(role_id: str) -> str:
    """Recover the evidence-batch id a Tier-A writer role fans out from."""

    return (
        role_id[len(WRITER_ROLE_PREFIX):]
        if role_id.startswith(WRITER_ROLE_PREFIX)
        else role_id
    )


def _safe_doc_component(unit_name: str) -> str:
    """Filesystem-safe component for a per-unit document filename."""

    component = "".join(
        ch if ch.isalnum() or ch in "._-" else "_" for ch in unit_name
    ).strip("._-")
    return component or "unit"


@dataclass(frozen=True)
class GroundedExecutionPack:
    recipe_id: str
    recipe_version: str
    roles: tuple[GroundedRole, ...]
    required_primary_artifacts: tuple[str, ...]
    validators: tuple[str, ...]
    workflow_mode: str
    max_parallelism: int
    repair_limit: int
    max_source_items: int
    max_source_bytes: int
    max_context_bytes: int
    max_input_tokens: int
    max_tool_calls: int

    @property
    def owned_artifacts(self) -> tuple[str, ...]:
        return tuple(path for role in self.roles for path in role.owns)

    @property
    def required_artifacts(self) -> tuple[str, ...]:
        return tuple(
            path
            for role in self.roles
            if role.failure_policy == "fail"
            for path in role.owns
        ) + self.required_primary_artifacts

    @property
    def optional_artifacts(self) -> tuple[str, ...]:
        return tuple(
            path
            for role in self.roles
            if role.failure_policy == "warn"
            for path in role.owns
        )

    def _fanout_template(self, stage: str) -> GroundedRole | None:
        templates = tuple(role for role in self.roles if role.fanout and role.stage == stage)
        if len(templates) > 1:
            raise ValueError(
                f"grounded execution supports at most one {stage} fan-out template"
            )
        return templates[0] if templates else None

    @property
    def analysis_fanout_template(self) -> GroundedRole | None:
        """Return the single analysis fan-out template role, if declared."""

        return self._fanout_template("analysis")

    @property
    def writing_fanout_template(self) -> GroundedRole | None:
        """Return the single writing (Tier-A per-unit) fan-out template, if declared."""

        return self._fanout_template("writing")


def load_grounded_execution_pack(
    manifest: AgenticRecipeManifest,
) -> GroundedExecutionPack:
    """Parse Grounded metadata with closed keys and graph/ownership validation."""

    raw = manifest.metadata.get("grounded_execution")
    data = _mapping(raw, "metadata.grounded_execution")
    _strict_keys(
        data,
        {
            "roles",
            "required_primary_artifacts",
            "validators",
            "max_parallelism",
            "repair_limit",
            "max_context_bytes",
            "max_input_tokens",
            "max_tool_calls",
            "workflow_mode",
        },
        "grounded execution",
    )
    roles = tuple(_role(item) for item in _object_list(data, "roles"))
    if not roles:
        raise ValueError("grounded execution must declare roles")
    role_ids = tuple(role.id for role in roles)
    if len(role_ids) != len(set(role_ids)):
        raise ValueError("grounded role ids must be unique")
    known_roles = set(role_ids)
    if any(not set(role.depends_on).issubset(known_roles) for role in roles):
        raise ValueError("grounded role dependency references an unknown role")
    if any(role.id in role.depends_on for role in roles):
        raise ValueError("grounded role cannot depend on itself")
    warn_role_ids = {role.id for role in roles if role.failure_policy == "warn"}
    if any(warn_role_ids.intersection(role.depends_on) for role in roles):
        raise ValueError("grounded warn roles must be leaves with no downstream dependencies")
    for stage in SUPPORTED_FANOUT_STAGES:
        if sum(1 for role in roles if role.fanout and role.stage == stage) > 1:
            raise ValueError(
                f"grounded execution supports at most one {stage} fan-out template"
            )
    _topological_roles(roles)

    primary = _strings(data.get("required_primary_artifacts"), "required_primary_artifacts")
    validators = _strings(data.get("validators"), "validators")
    if not validators:
        raise ValueError("grounded execution must declare validators")
    owned = tuple(path for role in roles for path in role.owns)
    all_artifacts = owned + primary
    if len(all_artifacts) != len(set(all_artifacts)):
        raise ValueError("grounded artifact ownership must be unique")

    declared_documents = {
        f"candidate/docs/{output.path}"
        for output in manifest.output_contracts
        if output.media_type == "text/markdown"
    }
    owned_documents = {path for path in owned if path.startswith("candidate/docs/")}
    if owned_documents != declared_documents:
        raise ValueError(
            "grounded document ownership must exactly match recipe Markdown outputs"
        )

    max_parallelism = _positive_int(data, "max_parallelism")
    if max_parallelism > manifest.budgets.max_concurrency:
        raise ValueError("grounded parallelism exceeds recipe concurrency budget")
    repair_limit = _nonnegative_int(data, "repair_limit")
    if repair_limit >= manifest.budgets.max_attempts:
        raise ValueError("grounded repair limit exceeds recipe attempt budget")
    max_context_bytes = _positive_int(data, "max_context_bytes")
    max_input_tokens = _positive_int(data, "max_input_tokens")
    max_tool_calls = _positive_int(data, "max_tool_calls")
    if max_context_bytes > manifest.default_scope.budgets.max_bytes:
        raise ValueError("grounded context budget exceeds recipe source budget")
    if max_input_tokens > manifest.default_scope.budgets.max_input_tokens:
        raise ValueError("grounded token budget exceeds recipe scope budget")
    if max_tool_calls > manifest.default_scope.budgets.max_tool_calls:
        raise ValueError("grounded tool budget exceeds recipe scope budget")
    if sum(role.max_context_bytes for role in roles) > max_context_bytes:
        raise ValueError("grounded role context budgets exceed aggregate context budget")

    return GroundedExecutionPack(
        recipe_id=manifest.id,
        recipe_version=manifest.version,
        roles=roles,
        required_primary_artifacts=primary,
        validators=validators,
        workflow_mode=_workflow_mode(data),
        max_parallelism=max_parallelism,
        repair_limit=repair_limit,
        max_source_items=manifest.default_scope.budgets.max_source_items,
        max_source_bytes=manifest.default_scope.budgets.max_bytes,
        max_context_bytes=max_context_bytes,
        max_input_tokens=max_input_tokens,
        max_tool_calls=max_tool_calls,
    )


def expand_grounded_fanout(
    pack: GroundedExecutionPack,
    batches: "Sequence[_BatchLike]",
) -> GroundedExecutionPack:
    """Expand the analysis and/or writing fan-out templates over the batches.

    One deterministic pass expands both stage templates the recipe declares:

    - The **analysis** template becomes one concrete role per evidence batch,
      each owning a generated intermediate filename (``${batch_id}``) and
      carrying its assigned unit names.
    - The **writing** (Tier-A) template becomes one concrete writer role per
      batch, owning one deep per-unit document (``${unit}``) for every unit in
      the batch. Each writer depends only on its OWN batch's analysis role (plus
      any non-analysis dependency such as the scorecard), so its packet stays
      bounded and coupled; the boundary manifest handles cross-batch awareness.

    Every dependent that referenced a template id is rewired to depend on all of
    that template's generated batch roles, so the summary writers and the
    business-topology role still see every batch analysis, and the reviewer sees
    every per-unit document.

    Deterministic and plan-driven: batch identities/units come from the planner,
    not free agent exploration. The returned pack is revalidated for
    artifact-ownership uniqueness and an acyclic role graph. The aggregate
    per-role context budget is intentionally not re-checked against the
    whole-pack budget: batch roles run in bounded waves (``max_parallelism``) and
    each role's physical packet is still capped at stage time, so summing every
    batch's ceiling would wrongly cap the batch count for large projects.
    """

    analysis = pack.analysis_fanout_template
    writing = pack.writing_fanout_template
    if analysis is None and writing is None:
        raise ValueError("grounded pack has no fan-out template to expand")
    if not batches:
        raise ValueError("grounded fan-out requires at least one batch")

    analysis_batch_ids: list[str] = []
    analysis_roles: dict[str, GroundedRole] = {}
    if analysis is not None:
        for batch in batches:
            owns = analysis.owns_template.replace(BATCH_ID_PLACEHOLDER, batch.batch_id)
            _safe_relative(owns, "grounded fan-out owned artifact")
            if not owns.startswith("candidate/"):
                raise ValueError("grounded fan-out owned artifact must be candidate-local")
            analysis_batch_ids.append(batch.batch_id)
            analysis_roles[batch.batch_id] = GroundedRole(
                id=batch.batch_id,
                agent=analysis.agent,
                stage=analysis.stage,
                owns=(owns,),
                depends_on=analysis.depends_on,
                source_globs=analysis.source_globs,
                max_context_bytes=analysis.max_context_bytes,
                model_policy=analysis.model_policy,
                failure_policy=analysis.failure_policy,
                units=tuple(batch.unit_names),
            )

    writer_role_ids: list[str] = []
    writer_roles: dict[str, GroundedRole] = {}
    if writing is not None:
        seen_docs: set[str] = set()
        for batch in batches:
            role_id = writer_role_id(batch.batch_id)
            unit_docs: list[tuple[str, str]] = []
            for unit in batch.unit_names:
                doc = writing.owns_template.replace(
                    UNIT_PLACEHOLDER, _safe_doc_component(unit)
                )
                _safe_relative(doc, "grounded unit-doc artifact")
                if not doc.startswith("candidate/docs/"):
                    raise ValueError("grounded unit-doc artifact must be a candidate document")
                if doc in seen_docs:
                    raise ValueError(
                        f"grounded per-unit document ownership collides at {doc!r} "
                        f"(unit {unit!r}); unit names must map to distinct filenames"
                    )
                seen_docs.add(doc)
                unit_docs.append((unit, doc))
            writer_deps: list[str] = []
            for dependency in writing.depends_on:
                # A per-unit writer depends only on its own batch's analysis.
                writer_deps.append(
                    batch.batch_id
                    if analysis is not None and dependency == analysis.id
                    else dependency
                )
            seen_dep: set[str] = set()
            writer_role_ids.append(role_id)
            writer_roles[role_id] = GroundedRole(
                id=role_id,
                agent=writing.agent,
                stage=writing.stage,
                owns=tuple(doc for _unit, doc in unit_docs),
                depends_on=tuple(
                    dep for dep in writer_deps if not (dep in seen_dep or seen_dep.add(dep))
                ),
                source_globs=writing.source_globs,
                max_context_bytes=writing.max_context_bytes,
                model_policy=writing.model_policy,
                failure_policy=writing.failure_policy,
                units=tuple(batch.unit_names),
                unit_docs=tuple(unit_docs),
            )

    template_ids = {
        template.id for template in (analysis, writing) if template is not None
    }

    def _rewire(depends_on: tuple[str, ...]) -> tuple[str, ...]:
        rewired: list[str] = []
        for dependency in depends_on:
            if analysis is not None and dependency == analysis.id:
                rewired.extend(analysis_batch_ids)
            elif writing is not None and dependency == writing.id:
                rewired.extend(writer_role_ids)
            else:
                rewired.append(dependency)
        seen: set[str] = set()
        return tuple(dep for dep in rewired if not (dep in seen or seen.add(dep)))

    expanded_roles: list[GroundedRole] = []
    for role in pack.roles:
        if analysis is not None and role.id == analysis.id:
            expanded_roles.extend(analysis_roles[bid] for bid in analysis_batch_ids)
            continue
        if writing is not None and role.id == writing.id:
            expanded_roles.extend(writer_roles[rid] for rid in writer_role_ids)
            continue
        expanded_roles.append(
            role if not template_ids.intersection(role.depends_on)
            else replace(role, depends_on=_rewire(role.depends_on))
        )

    roles = tuple(expanded_roles)
    _revalidate_roles(roles, pack.required_primary_artifacts)
    return replace(pack, roles=roles)


# Backwards-compatible name: callers/tests that only used the analysis fan-out
# keep working, and it now also expands a writing template when one is present.
expand_grounded_analysis_fanout = expand_grounded_fanout


def _revalidate_roles(
    roles: tuple[GroundedRole, ...],
    primary: tuple[str, ...],
) -> None:
    role_ids = tuple(role.id for role in roles)
    if len(role_ids) != len(set(role_ids)):
        raise ValueError("expanded grounded role ids must be unique")
    known = set(role_ids)
    if any(not set(role.depends_on).issubset(known) for role in roles):
        raise ValueError("expanded grounded role dependency references an unknown role")
    if any(role.id in role.depends_on for role in roles):
        raise ValueError("expanded grounded role cannot depend on itself")
    warn_ids = {role.id for role in roles if role.failure_policy == "warn"}
    if any(warn_ids.intersection(role.depends_on) for role in roles):
        raise ValueError("expanded grounded warn roles must remain leaves")
    owned = tuple(path for role in roles for path in role.owns)
    all_artifacts = owned + primary
    if len(all_artifacts) != len(set(all_artifacts)):
        raise ValueError("expanded grounded artifact ownership must be unique")
    _topological_roles(roles)


def grounded_pack_payload(pack: GroundedExecutionPack) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "recipe_id": pack.recipe_id,
        "recipe_version": pack.recipe_version,
        "max_parallelism": pack.max_parallelism,
        "repair_limit": pack.repair_limit,
        "budgets": {
            "max_source_items": pack.max_source_items,
            "max_source_bytes": pack.max_source_bytes,
            "max_context_bytes": pack.max_context_bytes,
            "max_input_tokens": pack.max_input_tokens,
            "max_tool_calls": pack.max_tool_calls,
        },
        "validators": list(pack.validators),
        "workflow_mode": pack.workflow_mode,
        "roles": [
            {
                "id": role.id,
                "agent": role.agent,
                "stage": role.stage,
                "owns": list(role.owns),
                "depends_on": list(role.depends_on),
                "source_globs": list(role.source_globs),
                "max_context_bytes": role.max_context_bytes,
                "model_policy": role.model_policy,
                "failure_policy": role.failure_policy,
                **({"fanout": role.fanout} if role.fanout else {}),
                **({"owns_template": role.owns_template} if role.owns_template else {}),
                **({"units": list(role.units)} if role.units else {}),
                **(
                    {"unit_docs": [[unit, doc] for unit, doc in role.unit_docs]}
                    if role.unit_docs
                    else {}
                ),
            }
            for role in pack.roles
        ],
        "required_primary_artifacts": list(pack.required_primary_artifacts),
    }


def _role(value: Mapping[str, object]) -> GroundedRole:
    data = dict(value)
    _strict_keys(
        data,
        {
            "id",
            "agent",
            "stage",
            "owns",
            "depends_on",
            "source_globs",
            "max_context_bytes",
            "model_policy",
            "failure_policy",
            "fanout",
            "owns_template",
        },
        "grounded role",
    )
    fanout = str(data.get("fanout") or "")
    role = GroundedRole(
        id=_string(data, "id"),
        agent=_string(data, "agent"),
        stage=_string(data, "stage"),
        owns=_strings(data.get("owns"), "owns") if data.get("owns") else (),
        depends_on=_strings(data.get("depends_on", []), "depends_on"),
        source_globs=_strings(data.get("source_globs"), "source_globs"),
        max_context_bytes=_positive_int(data, "max_context_bytes"),
        model_policy=str(data.get("model_policy") or "inherit"),
        failure_policy=str(data.get("failure_policy") or "fail"),
        fanout=fanout,
        owns_template=str(data.get("owns_template") or ""),
    )
    if not role.source_globs:
        raise ValueError("grounded role must declare source globs")
    if role.model_policy != "inherit":
        raise ValueError("grounded role model policy must be provider-neutral 'inherit'")
    if role.failure_policy not in {"fail", "warn"}:
        raise ValueError("grounded role failure policy must be 'fail' or 'warn'")
    if role.fanout:
        _validate_fanout_template(role)
    else:
        if not role.owns:
            raise ValueError("grounded role must own artifacts and declare source globs")
        if role.owns_template:
            raise ValueError("only a fan-out template may declare owns_template")
    for path in role.owns:
        _safe_relative(path, "grounded owned artifact")
        if not path.startswith("candidate/"):
            raise ValueError("grounded owned artifact must be candidate-local")
    for pattern in role.source_globs:
        _safe_glob(pattern)
    return role


def _validate_fanout_template(role: GroundedRole) -> None:
    if role.fanout not in SUPPORTED_FANOUT_KINDS:
        raise ValueError(
            "grounded fan-out template must declare a supported fanout kind"
        )
    if role.stage not in SUPPORTED_FANOUT_STAGES:
        raise ValueError(
            "grounded fan-out is only supported for the analysis or writing stage"
        )
    if role.owns:
        raise ValueError("grounded fan-out template must not declare fixed owns")
    if role.failure_policy != "fail":
        raise ValueError("grounded fan-out template must use the 'fail' failure policy")
    placeholder = _FANOUT_PLACEHOLDER[role.stage]
    template = role.owns_template
    if not template or template.count(placeholder) != 1:
        raise ValueError(
            f"grounded {role.stage} fan-out template requires exactly one "
            f"{placeholder} placeholder"
        )
    resolved = template.replace(placeholder, "sample")
    _safe_relative(resolved, "grounded fan-out owns_template")
    if not resolved.startswith("candidate/"):
        raise ValueError("grounded fan-out owns_template must be candidate-local")
    if role.stage == "writing" and not resolved.startswith("candidate/docs/"):
        raise ValueError(
            "grounded writing fan-out owns_template must own candidate/docs documents"
        )


def _topological_roles(roles: tuple[GroundedRole, ...]) -> None:
    remaining = {role.id: set(role.depends_on) for role in roles}
    while remaining:
        ready = [role_id for role_id, dependencies in remaining.items() if not dependencies]
        if not ready:
            raise ValueError("grounded role graph contains a cycle")
        for role_id in ready:
            remaining.pop(role_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)


def _safe_relative(value: str, field_name: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field_name} must be a contained POSIX path")


def _safe_glob(value: str) -> None:
    if value.startswith("/") or "\\" in value or ".." in PurePosixPath(value).parts:
        raise ValueError("grounded source glob must remain workspace-relative")


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _object_list(data: Mapping[str, object], field_name: str) -> tuple[dict[str, object], ...]:
    value = data.get(field_name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field_name} must be a list of objects")
    return tuple(value)


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field_name} must be a list of non-blank strings")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must be unique")
    return result


def _string(data: Mapping[str, object], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value


def _positive_int(data: Mapping[str, object], field_name: str) -> int:
    value = data.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_int(data: Mapping[str, object], field_name: str) -> int:
    value = data.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _workflow_mode(data: Mapping[str, object]) -> str:
    value = data.get("workflow_mode")
    if value not in {"legacy_replay", "recipe"}:
        raise ValueError(
            "grounded workflow_mode must be 'legacy_replay' or 'recipe'"
        )
    return str(value)


def _strict_keys(data: Mapping[str, object], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{context} contains unsupported keys: {unknown}")
