"""Adapter protocol, deterministic registry, and safe file extraction primitives."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceDescriptor,
    SourceLimits,
    SourceSnapshotContent,
    SourceSnapshotItem,
)


class SourceAdapterError(RuntimeError):
    pass


class SourceBudgetError(SourceAdapterError):
    """A source exceeded an explicit file or byte boundary."""


@dataclass(frozen=True)
class CapturedRawSource:
    """One actual raw file consumed by an adapter, before record expansion."""

    logical_identity: str
    raw_sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.logical_identity.strip():
            raise ValueError("captured raw source identity must not be blank")
        if len(self.raw_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.raw_sha256
        ):
            raise ValueError("captured raw source hash must be a lowercase SHA-256 digest")
        if self.size_bytes < 0:
            raise ValueError("captured raw source size must not be negative")


@dataclass(frozen=True)
class AdapterSnapshotResult:
    items: tuple[SourceSnapshotItem, ...]
    contents: tuple[SourceSnapshotContent, ...]
    raw_sources: tuple[CapturedRawSource, ...] = ()


class SourceAdapter(Protocol):
    id: str
    version: str

    def supports(self, descriptor: SourceDescriptor) -> bool: ...

    def snapshot(
        self, descriptor: SourceDescriptor, limits: SourceLimits
    ) -> AdapterSnapshotResult: ...


class SourceAdapterRegistry:
    """Explicitly registered trusted adapters; no source content chooses executable code."""

    def __init__(self, adapters: Iterable[SourceAdapter] = ()) -> None:
        self._adapters: dict[str, SourceAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: SourceAdapter) -> None:
        if not adapter.id or adapter.id in self._adapters:
            raise ValueError("source adapter ids must be non-empty and unique")
        self._adapters[adapter.id] = adapter

    def resolve(self, descriptor: SourceDescriptor) -> SourceAdapter:
        matches = self.matching_adapters(descriptor)
        if len(matches) != 1:
            raise SourceAdapterError(
                f"expected exactly one trusted adapter for {descriptor.id}, found {len(matches)}"
            )
        return matches[0]

    def matching_adapters(self, descriptor: SourceDescriptor) -> tuple[SourceAdapter, ...]:
        """Return the deterministic adapter candidates without attempting extraction.

        Aggregate evidence inventory needs to disclose an unsupported descriptor
        in its frozen manifest instead of treating it as an exception that can be
        silently lost.  Existing callers should continue using ``resolve`` when
        exactly one adapter is required.
        """
        return tuple(
            sorted(
                (adapter for adapter in self._adapters.values() if adapter.supports(descriptor)),
                key=lambda adapter: adapter.id,
            )
        )


def regular_files(root: Path, *, limits: SourceLimits) -> tuple[Path, ...]:
    """Enumerate a non-symlink file/tree deterministically under explicit caps."""

    root = Path(root)
    _require_directory(root) if root.is_dir() else _require_regular(root)
    if root.is_file():
        return (root,)
    files: list[Path] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise SourceAdapterError(f"source tree contains symlink: {path}")
        if path.is_dir():
            continue
        _require_regular(path)
        size = path.stat().st_size
        total += size
        if len(files) >= limits.max_files or total > limits.max_bytes:
            raise SourceBudgetError("source file or byte limit exceeded")
        files.append(path)
    return tuple(files)


def read_regular(path: Path, *, max_bytes: int) -> bytes:
    _require_regular(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SourceAdapterError(f"cannot safely open source item: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SourceAdapterError("source item is not a regular file")
        with os.fdopen(fd, "rb") as handle:
            content = handle.read(max_bytes + 1)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    if len(content) > max_bytes:
        raise SourceBudgetError("source item exceeds byte limit")
    return content


def capture_snapshot_item(
    *,
    descriptor: SourceDescriptor,
    path: Path,
    root: Path,
    adapter_id: str,
    adapter_version: str,
    redaction_state: RedactionState,
) -> tuple[SourceSnapshotItem, SourceSnapshotContent]:
    content = read_regular(path, max_bytes=path.stat().st_size)
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    raw_sha = hashlib.sha256(content).hexdigest()
    logical_identity = f"{descriptor.id}:{relative}"
    item = SourceSnapshotItem(
        id=hashlib.sha256(logical_identity.encode("utf-8")).hexdigest(),
        source_descriptor_id=descriptor.id,
        logical_identity=logical_identity,
        source_kind=descriptor.source_kind,
        source_role=descriptor.source_role,
        capabilities=descriptor.capabilities,
        locator=f"{descriptor.provenance_locator.rstrip('/')}/{relative}",
        raw_sha256=raw_sha,
        extracted_sha256=raw_sha,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        size_bytes=len(content),
        extracted_size_bytes=len(content),
        trust_level=descriptor.trust_level,
        redaction_state=redaction_state,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
    )
    return item, SourceSnapshotContent(
        item_id=item.id,
        extracted_bytes=content,
        extracted_sha256=raw_sha,
        media_type=item.media_type,
        size_bytes=len(content),
    )


def ensure_within(path: Path, roots: tuple[Path, ...]) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceAdapterError(f"cannot resolve governed source path: {path}") from exc
    for root in roots:
        try:
            resolved.relative_to(root.resolve(strict=True))
            return resolved
        except (ValueError, OSError):
            continue
    raise SourceAdapterError("source path is outside configured governed roots")


def _require_regular(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise SourceAdapterError(f"cannot inspect source item: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise SourceAdapterError("source item must be a regular non-symlink file")


def _require_directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise SourceAdapterError(f"cannot inspect source directory: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise SourceAdapterError("source directory must be a non-symlink directory")
