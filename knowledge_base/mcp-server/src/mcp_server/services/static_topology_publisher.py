"""Publish a bounded, redaction-approved static topology snapshot to Runtime.

This deliberately does *not* read a repository or derive facts from names.  It
only translates four completed scanner artifacts that are hash-bound by a final
scanner approval receipt into the provider-neutral Runtime contract.  A failed
publication is safe to treat as advisory by the scan caller: the scan has
already passed its own scanner/redaction gates.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from mcp_server.config import config

_MAX_JOBS = 5_000
_MAX_COMPONENTS_PER_JOB = 2_000
_MAX_DEPENDENCIES = 30_000
_MAX_LINEAGE = 100_000
_MAX_JOB_ID = 255
_MAX_SOURCE_ROOT = 1_000
_MAX_COMPONENT_TYPE = 120
_MAX_EVIDENCE_ID = 512
_MAX_LINEAGE_FIELD = 255
_MAX_TRANSFORMS = 10
_MAX_TRANSFORM_TYPE = 50
_MAX_EXPRESSION = 1_000
_MAX_SOURCE_EXPRESSION = 100_000

_RESOLVED_RELATIONS = frozenset({"resolved", "same_root", "global"})
_INVOCATION_RELATION_TYPES = frozenset({"job_calls_job", "route_invokes_job"})
_PUBLICATION_APPROVAL_FILENAME = "publication_approval.json"
_APPROVED_ARTIFACTS = (
    "scan_result.json",
    "scan/manifest.json",
    "scan/lineage.json",
    "scan/landscape.json",
)


class StaticTopologyPublicationError(RuntimeError):
    """The completed scan cannot safely be represented by the Runtime contract."""


class StaticTopologyRedactionRefusal(StaticTopologyPublicationError):
    """The scanner's redaction receipt did not explicitly approve publication."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StaticTopologyPublicationError(
            f"required scanner artifact is unavailable or invalid: {path.name}"
        ) from exc
    if not isinstance(value, dict):
        raise StaticTopologyPublicationError(f"scanner artifact must be a JSON object: {path.name}")
    return value


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _read_approved_json(path: Path, expected_sha256: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise StaticTopologyPublicationError(
            f"required scanner artifact is unavailable or invalid: {path.name}"
        ) from exc
    if _sha256_bytes(raw) != expected_sha256:
        raise StaticTopologyRedactionRefusal(
            f"static topology publication refused: approved hash does not match {path.name}"
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaticTopologyPublicationError(
            f"required scanner artifact is unavailable or invalid: {path.name}"
        ) from exc
    if not isinstance(value, dict):
        raise StaticTopologyPublicationError(f"scanner artifact must be a JSON object: {path.name}")
    return value


def _approved_artifact_hashes(root: Path) -> dict[str, str]:
    """Return a receipt's verified artifact hashes or fail closed."""

    try:
        receipt = _read_json(root / _PUBLICATION_APPROVAL_FILENAME)
    except StaticTopologyPublicationError as exc:
        raise StaticTopologyRedactionRefusal(
            "static topology publication refused: final scanner approval is missing or invalid"
        ) from exc
    if receipt.get("schema_version") != 1 or receipt.get("approved") is not True:
        raise StaticTopologyRedactionRefusal(
            "static topology publication refused: final scanner approval is missing"
        )
    if receipt.get("allow_redaction_failures") is not False:
        raise StaticTopologyRedactionRefusal(
            "static topology publication refused: redaction escape-hatch scans cannot publish"
        )
    artifacts = receipt.get("artifacts")
    verifications = receipt.get("verifications")
    if not isinstance(artifacts, dict) or not isinstance(verifications, dict):
        raise StaticTopologyRedactionRefusal(
            "static topology publication refused: final scanner approval is malformed"
        )

    hashes: dict[str, str] = {}
    for relative_path in _APPROVED_ARTIFACTS:
        digest = artifacts.get(relative_path)
        verification = verifications.get(relative_path)
        if (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or not isinstance(verification, dict)
            or verification.get("passed") is not True
        ):
            raise StaticTopologyRedactionRefusal(
                f"static topology publication refused: {relative_path} lacks final approval"
            )
        hashes[relative_path] = digest
    return hashes


def _string(value: object, *, field: str, limit: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise StaticTopologyPublicationError(f"{field} must be a string")
    if required and not value:
        raise StaticTopologyPublicationError(f"{field} must not be empty")
    if len(value) > limit:
        raise StaticTopologyPublicationError(f"{field} exceeds the Runtime contract bound")
    return value


def _list(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise StaticTopologyPublicationError(f"{field} must be a list")
    return value


def _stable_evidence_id(kind: str, fact: dict[str, Any]) -> str:
    """Return a deterministic opaque ID without exposing raw fact text in IDs."""
    canonical = json.dumps(fact, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{kind}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _canonical_snapshot_content(
    platform: str,
    jobs: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    lineage: list[dict[str, Any]],
) -> dict[str, Any]:
    """Stable ordering for both payload and revision hashing.

    The scanner's natural file traversal order must not create a new runtime
    revision when the published facts are unchanged.
    """
    canonical_jobs = []
    for job in jobs:
        canonical_jobs.append({
            **job,
            "components": sorted(job["components"], key=lambda item: item["unique_name"]),
        })
    return {
        "platform": platform,
        "jobs": sorted(canonical_jobs, key=lambda item: item["static_job_id"]),
        "dependencies": sorted(
            dependencies,
            key=lambda item: (
                item["dependency_type"], item["source_job_id"], item["target_job_id"], item["evidence_id"],
            ),
        ),
        "lineage": sorted(
            lineage,
            key=lambda item: (
                item["job_id"], item["source_job_id"], item["target_component"],
                item["target_table"], item["target_column"], item["evidence_id"],
            ),
        ),
    }


class StaticTopologyPublisher:
    """Translate and post a static scanner snapshot to the Runtime ingest API."""

    def __init__(self, *, base_url: str | None = None, ingest_api_key: str | None = None) -> None:
        self._base_url = (base_url if base_url is not None else config.rag_base_url).rstrip("/")
        self._api_key = ingest_api_key if ingest_api_key is not None else config.ingest_api_key

    def compile_snapshot(
        self, *, project_id: str, output_dir: str | Path, source_sequence: int
    ) -> dict[str, Any]:
        """Compile an approved scan into the exact bounded Runtime payload.

        Every relationship reference is validated against the published static
        job IDs.  Unresolved scanner relations are intentionally absent; a
        relation that claims to be resolved but cannot reference a scanned job
        is a malformed completed scan and refuses publication.
        """
        if isinstance(source_sequence, bool) or not isinstance(source_sequence, int) or source_sequence < 1:
            raise StaticTopologyPublicationError("source_sequence must be a positive scan generation")
        root = Path(output_dir)
        artifact_hashes = _approved_artifact_hashes(root)
        scan_result = _read_approved_json(
            root / "scan_result.json", artifact_hashes["scan_result.json"]
        )
        manifest = _read_approved_json(
            root / "scan" / "manifest.json", artifact_hashes["scan/manifest.json"]
        )
        lineage_matrix = _read_approved_json(
            root / "scan" / "lineage.json", artifact_hashes["scan/lineage.json"]
        )
        # Landscape is not yet translated into the Runtime payload, but it is
        # a completed-scanner artifact and must be hash-approved as part of
        # the all-or-nothing publication boundary.
        _read_approved_json(
            root / "scan" / "landscape.json", artifact_hashes["scan/landscape.json"]
        )

        platform = _string(scan_result.get("platform"), field="platform", limit=80, required=True)
        raw_jobs = _list(scan_result.get("jobs"), field="scan_result.jobs")
        if len(raw_jobs) > _MAX_JOBS:
            raise StaticTopologyPublicationError("scan_result.jobs exceeds the Runtime contract bound")

        jobs: list[dict[str, Any]] = []
        job_ids: set[str] = set()
        for index, raw_job in enumerate(raw_jobs):
            if not isinstance(raw_job, dict):
                raise StaticTopologyPublicationError(f"scan_result.jobs[{index}] must be an object")
            # `job_id` is the scanner's composite-safe identity. Historic
            # single-root scans encode the same stable scanner ID as `name`.
            static_job_id = _string(
                raw_job.get("job_id") or raw_job.get("name"),
                field=f"scan_result.jobs[{index}].job_id",
                limit=_MAX_JOB_ID,
                required=True,
            )
            if static_job_id in job_ids:
                raise StaticTopologyPublicationError(
                    f"duplicate scanner static job ID: {static_job_id}"
                )
            job_ids.add(static_job_id)
            name = _string(raw_job.get("name"), field=f"scan_result.jobs[{index}].name", limit=_MAX_JOB_ID, required=True)
            source_root = _string(
                raw_job.get("source_root"), field=f"scan_result.jobs[{index}].source_root", limit=_MAX_SOURCE_ROOT
            )
            raw_components = _list(raw_job.get("components", []), field=f"scan_result.jobs[{index}].components")
            if len(raw_components) > _MAX_COMPONENTS_PER_JOB:
                raise StaticTopologyPublicationError("job components exceed the Runtime contract bound")
            components: list[dict[str, str]] = []
            component_ids: set[str] = set()
            for component_index, raw_component in enumerate(raw_components):
                if not isinstance(raw_component, dict):
                    raise StaticTopologyPublicationError(
                        f"scan_result.jobs[{index}].components[{component_index}] must be an object"
                    )
                unique_name = _string(
                    raw_component.get("unique_name"),
                    field=f"scan_result.jobs[{index}].components[{component_index}].unique_name",
                    limit=_MAX_JOB_ID,
                    required=True,
                )
                if unique_name in component_ids:
                    raise StaticTopologyPublicationError(
                        f"duplicate component ID in scanner job {static_job_id}: {unique_name}"
                    )
                component_ids.add(unique_name)
                components.append({
                    "unique_name": unique_name,
                    "component_type": _string(
                        raw_component.get("component_type"),
                        field=f"scan_result.jobs[{index}].components[{component_index}].component_type",
                        limit=_MAX_COMPONENT_TYPE,
                    ),
                })
            jobs.append({
                "static_job_id": static_job_id,
                "name": name,
                "source_root": source_root,
                "components": components,
            })

        dependencies = self._compile_dependencies(manifest, lineage_matrix, job_ids)
        lineage = self._compile_lineage(lineage_matrix, job_ids)
        canonical = _canonical_snapshot_content(platform, jobs, dependencies, lineage)
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return {
            "project_id": _string(project_id, field="project_id", limit=_MAX_JOB_ID, required=True),
            "source_sequence": source_sequence,
            "scan_revision": f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}",
            **canonical,
        }

    def _compile_dependencies(
        self,
        manifest: dict[str, Any],
        lineage_matrix: dict[str, Any],
        job_ids: set[str],
    ) -> list[dict[str, str]]:
        dependencies: dict[tuple[str, str, str, str], dict[str, str]] = {}
        raw_relations = _list(manifest.get("artifact_relations", []), field="manifest.artifact_relations")
        for index, relation in enumerate(raw_relations):
            if not isinstance(relation, dict):
                raise StaticTopologyPublicationError(f"manifest.artifact_relations[{index}] must be an object")
            resolution = relation.get("resolution", "")
            target = relation.get("target", "")
            # Unresolved/ambiguous scanner facts are deliberately not runtime
            # dependency facts. Do not try to recover them by name.
            if resolution not in _RESOLVED_RELATIONS or not target:
                continue
            relation_type = _string(
                relation.get("relation_type"), field="relation type", limit=120
            )
            if relation_type not in _INVOCATION_RELATION_TYPES:
                continue
            source_job_id = _string(relation.get("source"), field="resolved relation source", limit=_MAX_JOB_ID, required=True)
            target_job_id = _string(target, field="resolved relation target", limit=_MAX_JOB_ID, required=True)
            if source_job_id not in job_ids or target_job_id not in job_ids:
                raise StaticTopologyPublicationError("resolved control-flow relation references an unknown scanner job")
            evidence_id = _stable_evidence_id("control_flow", {
                "source": source_job_id,
                "target": target_job_id,
                "relation_type": relation_type,
                "component_name": _string(relation.get("component_name"), field="relation component", limit=_MAX_JOB_ID),
                "provenance": _string(relation.get("provenance"), field="relation provenance", limit=120),
            })
            if len(evidence_id) > _MAX_EVIDENCE_ID:
                raise StaticTopologyPublicationError("control-flow evidence ID exceeds the Runtime contract bound")
            fact = {
                "source_job_id": source_job_id,
                "target_job_id": target_job_id,
                "dependency_type": "control_flow",
                "evidence_id": evidence_id,
            }
            dependencies[("control_flow", source_job_id, target_job_id, evidence_id)] = fact

        raw_entries = _list(lineage_matrix.get("entries", []), field="lineage.entries")
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, dict):
                raise StaticTopologyPublicationError(f"lineage.entries[{index}] must be an object")
            source_job_id = _string(entry.get("source_job_name"), field="lineage source_job_name", limit=_MAX_JOB_ID)
            if not source_job_id:
                continue
            target_job_id = _string(entry.get("job_name"), field="lineage job_name", limit=_MAX_JOB_ID, required=True)
            if source_job_id not in job_ids or target_job_id not in job_ids:
                raise StaticTopologyPublicationError("cross-job lineage references an unknown scanner job")
            evidence_id = self._lineage_evidence_id(entry)
            fact = {
                "source_job_id": source_job_id,
                "target_job_id": target_job_id,
                "dependency_type": "data_lineage",
                "evidence_id": evidence_id,
            }
            dependencies[("data_lineage", source_job_id, target_job_id, evidence_id)] = fact

        if len(dependencies) > _MAX_DEPENDENCIES:
            raise StaticTopologyPublicationError("dependencies exceed the Runtime contract bound")
        return list(dependencies.values())

    def _compile_lineage(self, lineage_matrix: dict[str, Any], job_ids: set[str]) -> list[dict[str, Any]]:
        raw_entries = _list(lineage_matrix.get("entries", []), field="lineage.entries")
        if len(raw_entries) > _MAX_LINEAGE:
            raise StaticTopologyPublicationError("lineage entries exceed the Runtime contract bound")
        result: list[dict[str, Any]] = []
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, dict):
                raise StaticTopologyPublicationError(f"lineage.entries[{index}] must be an object")
            job_id = _string(entry.get("job_name"), field="lineage job_name", limit=_MAX_JOB_ID, required=True)
            source_job_id = _string(entry.get("source_job_name"), field="lineage source_job_name", limit=_MAX_JOB_ID)
            if job_id not in job_ids or (source_job_id and source_job_id not in job_ids):
                raise StaticTopologyPublicationError("lineage fact references an unknown scanner job")
            transformations = _list(entry.get("transformations", []), field="lineage transformations")
            # Runtime intentionally caps mapping detail. Keep the scanner's
            # deterministic order for the first bounded set of transformation
            # types, but never truncate an expression into a misleading new
            # expression. Oversized text is simply not published.
            transformations = transformations[:_MAX_TRANSFORMS]
            transformation_types: list[str] = []
            expressions: list[str] = []
            for transform_index, transformation in enumerate(transformations):
                if not isinstance(transformation, dict):
                    raise StaticTopologyPublicationError(
                        f"lineage transformations[{transform_index}] must be an object"
                    )
                transformation_type = _string(
                    transformation.get("transformation_type"),
                    field="lineage transformation type",
                    limit=_MAX_TRANSFORM_TYPE,
                )
                if not transformation_type:
                    raise StaticTopologyPublicationError("lineage transformation type must not be empty")
                transformation_types.append(transformation_type)
                expression = _string(
                    transformation.get("expression"), field="lineage expression", limit=_MAX_SOURCE_EXPRESSION
                )
                if expression and len(expression) <= _MAX_EXPRESSION:
                    expressions.append(expression)
            fact = {
                "job_id": job_id,
                "source_job_id": source_job_id,
                "source_component": _string(entry.get("source_component"), field="lineage source_component", limit=_MAX_JOB_ID),
                "target_component": _string(entry.get("target_component"), field="lineage target_component", limit=_MAX_JOB_ID),
                "source_table": _string(entry.get("source_table"), field="lineage source_table", limit=_MAX_LINEAGE_FIELD),
                "source_column": _string(entry.get("source_column"), field="lineage source_column", limit=_MAX_LINEAGE_FIELD),
                "target_table": _string(entry.get("target_table"), field="lineage target_table", limit=_MAX_LINEAGE_FIELD),
                "target_column": _string(entry.get("target_column"), field="lineage target_column", limit=_MAX_LINEAGE_FIELD),
                "confidence": _string(entry.get("confidence"), field="lineage confidence", limit=32) or "opaque",
                "transformation_types": transformation_types,
                "expressions": expressions,
            }
            fact["evidence_id"] = self._lineage_evidence_id(entry)
            result.append(fact)
        return result

    def _lineage_evidence_id(self, entry: dict[str, Any]) -> str:
        safe_fact = {
            "job_name": entry.get("job_name", ""),
            "source_job_name": entry.get("source_job_name", ""),
            "source_component": entry.get("source_component", ""),
            "target_component": entry.get("target_component", ""),
            "source_table": entry.get("source_table", ""),
            "source_column": entry.get("source_column", ""),
            "target_table": entry.get("target_table", ""),
            "target_column": entry.get("target_column", ""),
            "confidence": entry.get("confidence", ""),
            "transformations": entry.get("transformations", []),
        }
        evidence_id = _stable_evidence_id("lineage", safe_fact)
        if len(evidence_id) > _MAX_EVIDENCE_ID:
            raise StaticTopologyPublicationError("lineage evidence ID exceeds the Runtime contract bound")
        return evidence_id

    async def publish(
        self, *, project_id: str, output_dir: str | Path, source_sequence: int
    ) -> dict[str, Any]:
        """Compile then POST an approved snapshot to Runtime's dedicated endpoint."""
        snapshot = self.compile_snapshot(
            project_id=project_id,
            output_dir=output_dir,
            source_sequence=source_sequence,
        )
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(timeout=config.rag_push_timeout_s) as client:
            response = await client.post(
                f"{self._base_url}/api/v1/ingest/runtime-static-topology",
                json=snapshot,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
