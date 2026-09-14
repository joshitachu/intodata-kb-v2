"""Versioned, sanitized evidence pages derived only from a verified scan.

Storage is complete; page size bounds reads rather than deciding what survives.
The monolith manifest binds the index digest. Callers must hold the scan lock
while publishing its directory; this writer does not authorize publication.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from intodoc.models import ScanResult


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_evidence_package(result: ScanResult, scan_dir: Path) -> None:
    evidence_dir = scan_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    fragments: list[dict] = []
    for routine in result.routines:
        if routine.is_system or not routine.source_complete:
            continue
        # IDs hash sanitized owner + sanitized content, never raw secrets.
        lines = routine.source_code.splitlines(keepends=True)
        page_count = max(1, (len(lines) + 199) // 200)
        for page in range(page_count):
            content = "".join(lines[page * 200:(page + 1) * 200])
            fragment_id = _digest(f"{routine.routine_id}\n{page}\n{content}")
            record = {
                "schema_version": 1, "id": fragment_id,
                "owner": routine.routine_id, "kind": "routine_source",
                "source_root": routine.source_root, "path": routine.path,
                "version": routine.version, "line_start": page * 200 + 1,
                "line_end": min((page + 1) * 200, len(lines)),
                "page": page + 1, "page_count": page_count,
                "content": content, "sha256": _digest(content),
            }
            relative = f"evidence/{fragment_id}.json"
            (scan_dir / relative).write_text(json.dumps(record, indent=2), encoding="utf-8")
            fragments.append({k: v for k, v in record.items() if k != "content"} | {"artifact": relative})
    # TDM pages preserve every field; no first-N tree projection.
    for mapping in result.tdm_maps:
        for direction in ("input", "output"):
            tree = getattr(mapping, f"{direction}_tree")
            if tree is None:
                continue
            for start in range(0, len(tree.fields), 200):
                fields = [field.model_dump() for field in tree.fields[start:start + 200]]
                content = json.dumps(fields, sort_keys=True)
                owner = f"{mapping.source_root}::{mapping.path}"
                fragment_id = _digest(f"{owner}\n{direction}\n{start}\n{content}")
                relative = f"evidence/{fragment_id}.json"
                record = {"schema_version": 1, "id": fragment_id, "owner": owner,
                          "kind": "tdm_fields", "direction": direction,
                          "field_start": start, "total_fields": len(tree.fields),
                          "complete": not tree.truncated, "fields": fields,
                          "sha256": _digest(content)}
                (scan_dir / relative).write_text(json.dumps(record, indent=2), encoding="utf-8")
                fragments.append({k: v for k, v in record.items() if k != "fields"} | {"artifact": relative})
    payload = {"schema_version": 1, "capability_version": "talend-fidelity-1",
               "authority": "sanitized_export_design_not_deployed_state",
               "source_roots": result.manifest.source_roots,
               "source_inventory": result.source_inventory,
               "artifact_versions": result.manifest.artifact_versions,
               "fragments": fragments}
    encoded = json.dumps(payload, sort_keys=True, indent=2)
    (evidence_dir / "manifest.json").write_text(encoded, encoding="utf-8")
    result.manifest.evidence_package = {
        "schema_version": "1", "path": "scan/evidence/manifest.json",
        "sha256": _digest(encoded), "capability_version": "talend-fidelity-1",
    }


# ── Sanitized workspace ──────────────────────────────────────────────
#
# A complete, redacted MIRROR of the textual sources the walker
# inventoried. The parsed models are a projection: they carry what this
# scanner knows how to model, and a downstream reader that needs the exact
# serialized form of one component (or of a construct the parser does not
# model yet) has nowhere else to go.
#
# The gate is absolute: a file is copied only if the credential filter's own
# detectors find nothing in the sanitized text. A file that cannot be proven
# clean is NOT written, and is listed in the manifest with its finding
# CATEGORIES -- never the values, never a hash of the values.

_CAPABILITY_VERSION = "talend-capability-2"

# Extensions whose content is text this scanner can redact and verify.
# Everything else (jar, zip, xlsx, images) is never copied: redaction
# cannot be verified on bytes it cannot parse, so a "sanitized" binary
# would be an unchecked claim.
_TEXTUAL_SUFFIXES: frozenset[str] = frozenset({
    ".item", ".properties", ".xml", ".xsl", ".xslt", ".wsdl",
    ".txt", ".json", ".csv", ".prn", ".project",
})


def _source_root_for(path: str, roots: list[str], override: str) -> str:
    """Longest source root the inventory path falls under."""
    if override:
        return override
    matches = [
        root for root in roots
        if root and root != "." and (path == root or path.startswith(f"{root}/"))
    ]
    return max(matches, key=len) if matches else ""


def write_sanitized_workspace(result: ScanResult, scan_dir: Path, cred_filter) -> dict:
    """Write ``scan/sanitized/`` plus its manifest. Returns the manifest.

    ``files[].sha256`` is the digest of the SANITIZED bytes on disk, not of
    the original source (that digest is in ``files_index.json``): the two
    are deliberately different artifacts, and conflating them would let a
    consumer verify the wrong thing.
    """
    sanitized_dir = scan_dir / "sanitized"
    sanitized_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(result.repo_root)
    roots = [str(root.get("path", "")) for root in result.manifest.source_roots]
    source_root_by_path = {
        scanned.relative_path: str(scanned.platform_metadata.get("source_root", ""))
        for scanned in result.files
    }

    files: list[dict] = []
    excluded: list[dict] = []
    for entry in result.source_inventory:
        path = str(entry.get("path", ""))
        if not path:
            continue
        kind = str(entry.get("kind", "unknown"))
        outcome = str(entry.get("outcome", ""))
        source_root = _source_root_for(
            path, roots, source_root_by_path.get(path, ""),
        )
        record = {"path": path, "source_root": source_root, "kind": kind}
        if outcome == "excluded":
            excluded.append({
                **record, "reason": str(entry.get("reason", "inventory_excluded")),
            })
            continue
        if Path(path).suffix.lower() not in _TEXTUAL_SUFFIXES:
            excluded.append({**record, "reason": "non_textual_extension"})
            continue
        source_path = repo_root / path
        try:
            raw = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            excluded.append({
                **record, "reason": "source_file_unreadable",
                "error": type(exc).__name__,
            })
            continue
        sanitized = cred_filter.normalize_redaction_tokens(
            cred_filter.filter_text(raw, filename=str(source_path)),
        )
        # The text filter's key/value rule uses a fixed key list, while the
        # STRUCTURAL layer redacts by token-aware variable name -- which is
        # why a Talend context item's `name="DbPassword" value="<plaintext>"`
        # survives the former and is caught by the latter. Publishing raw
        # file text means applying that rule here too, not assuming the
        # parsed models' cleanliness transfers to their sources.
        sanitized = cred_filter.redact_named_value_pairs(sanitized)
        residual = cred_filter.detect_residual(sanitized, filename=path)
        if residual:
            excluded.append({
                **record, "reason": "residual_redaction_findings",
                "categories": sorted(residual),
            })
            continue
        target = sanitized_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = sanitized.encode("utf-8")
        target.write_bytes(encoded)
        files.append({
            **record,
            "sha256": _digest(sanitized),
            "bytes": len(encoded),
            "outcome": outcome,
        })

    manifest = {
        "schema_version": 1,
        "capability_version": _CAPABILITY_VERSION,
        "source_roots": result.manifest.source_roots,
        "files": files,
        "excluded": excluded,
        # True by construction: any file with a residual finding is in
        # `excluded` instead of on disk.
        "residual_free": True,
    }
    encoded_manifest = json.dumps(manifest, indent=2, sort_keys=True, default=str)
    (sanitized_dir / "manifest.json").write_text(encoded_manifest, encoding="utf-8")
    result.manifest.sanitized_workspace = {
        "schema_version": "1",
        "path": "scan/sanitized/manifest.json",
        "sha256": _digest(encoded_manifest),
        "file_count": str(len(files)),
        "excluded_count": str(len(excluded)),
        "capability_version": _CAPABILITY_VERSION,
    }
    return manifest
