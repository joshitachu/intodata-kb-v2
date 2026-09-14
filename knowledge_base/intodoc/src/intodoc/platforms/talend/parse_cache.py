"""Content-addressed cache for deterministic Talend design parsing."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intodoc.models import ParsedJob, ScanWarning

LOGGER = logging.getLogger(__name__)
_CHUNK = 65_536
CACHE_SCHEMA_VERSION = "talend-job-cache-v1"
# Bump whenever the parsed design representation changes, including changes
# to the pre-parse credential projection. v5 guarantees that private URI
# descriptor tokens are normalized before a parsed design enters the cache.
PARSER_CONTRACT_VERSION = "talend-parser-v5"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.is_file():
        digest.update(b"<missing>")
        return digest.hexdigest()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def design_cache_key(
    item_file: Path,
    *,
    redaction_fingerprint: str,
    resolution_fingerprint: str,
    parser_version: str = PARSER_CONTRACT_VERSION,
) -> str:
    """Hash every deterministic input, never the design's location."""
    payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "parser": parser_version,
        "item_sha256": _hash_file(item_file),
        "properties_present": item_file.with_suffix(".properties").is_file(),
        "properties_sha256": _hash_file(item_file.with_suffix(".properties")),
        "redaction": redaction_fingerprint,
        "resolution": resolution_fingerprint,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedDesign:
    job: ParsedJob
    warnings: list[ScanWarning]
    source_item: str
    name_from_path: bool
    redaction_audit: dict[str, list[str]]


class TalendDesignCache:
    """One JSON file per key: O(1) reads and atomic, index-free writes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.hits = 0
        self.misses = 0
        self.invalid_entries = 0
        self.writes = 0

    def reset_metrics(self) -> None:
        self.hits = self.misses = self.invalid_entries = self.writes = 0

    def read(self, key: str) -> CachedDesign | None:
        path = self.root / f"{key}.json"
        if not path.is_file():
            self.misses += 1
            LOGGER.info("Talend parse cache miss %s", key[:12])
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
                raise ValueError("cache schema mismatch")
            result = CachedDesign(
                job=ParsedJob.model_validate(payload["job"]),
                warnings=[ScanWarning.model_validate(item) for item in payload.get("warnings", [])],
                source_item=str(payload["source_item"]),
                name_from_path=bool(payload.get("name_from_path")),
                redaction_audit={
                    str(category): [str(item) for item in hashes]
                    for category, hashes in dict(payload.get("redaction_audit", {})).items()
                },
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            self.misses += 1
            self.invalid_entries += 1
            LOGGER.info("Talend parse cache invalid entry %s; treating as miss", key[:12])
            return None
        self.hits += 1
        LOGGER.info("Talend parse cache hit %s", key[:12])
        return result

    def write(
        self,
        key: str,
        *,
        job: ParsedJob,
        warnings: list[ScanWarning],
        source_item: str,
        name_from_path: bool,
        redaction_audit: dict[str, list[str]],
    ) -> None:
        payload: dict[str, Any] = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "job": job.model_dump(mode="json"),
            "warnings": [warning.model_dump(mode="json") for warning in warnings],
            "source_item": source_item,
            "name_from_path": name_from_path,
            "redaction_audit": redaction_audit,
        }
        temporary: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / f"{key}.json"
            temporary = self.root / f".{key}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
            self.writes += 1
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.warning("Could not write Talend parse cache %s: %s", key[:12], exc)
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def metrics(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "invalid_entries": self.invalid_entries,
            "writes": self.writes,
        }
