"""Central certification and provenance policy for generation-authoritative Packs."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select

from mcp_server.config import config
from mcp_server.db.models import PluginPack
from mcp_server.services.git_remote_service import canonical_remote_key, run_git
from mcp_server.services.plugin_schema import PluginPackManifest, load_plugin_manifest

_CERTIFIED = "certified"
_LEGACY = "legacy_untrusted"
_UNTRUSTED = "untrusted"
_SHA256_HEX_LENGTH = 64


class PackGovernanceDenied(Exception):
    """Pack selection is prohibited by the certification boundary."""


@dataclass(frozen=True)
class TrustedPackSource:
    kind: Literal["git", "bundled"]
    provenance: str
    repo_url: str | None = None
    refs: tuple[str, ...] = ()
    manifest_name: str | None = None
    allow_legacy_migration: bool = False


@dataclass(frozen=True)
class PackGovernanceDecision:
    certified: bool
    origin: str
    mutable: bool
    blocked_reason: str | None = None
    provenance: str | None = None
    content_sha256: str | None = None


def _trusted_sources() -> tuple[TrustedPackSource, ...]:
    """Load strict JSON configuration. Invalid entries fail closed individually."""
    raw = config.pack_governance_trusted_sources_json.strip()
    if not raw:
        return ()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()

    sources: list[TrustedPackSource] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        provenance = item.get("provenance")
        if kind not in {"git", "bundled"} or not isinstance(provenance, str) or not provenance:
            continue
        if kind == "git":
            repo_url = item.get("repo_url")
            refs = item.get("refs")
            if not isinstance(repo_url, str) or not repo_url or not isinstance(refs, list):
                continue
            clean_refs = tuple(ref for ref in refs if isinstance(ref, str) and ref)
            if not clean_refs:
                continue
            sources.append(
                TrustedPackSource(
                    kind="git",
                    provenance=provenance,
                    repo_url=repo_url,
                    refs=clean_refs,
                    allow_legacy_migration=bool(item.get("allow_legacy_migration", False)),
                )
            )
        else:
            manifest_name = item.get("manifest_name")
            if not isinstance(manifest_name, str) or not manifest_name:
                continue
            sources.append(
                TrustedPackSource(
                    kind="bundled",
                    provenance=provenance,
                    manifest_name=manifest_name,
                    allow_legacy_migration=bool(item.get("allow_legacy_migration", False)),
                )
            )
    return tuple(sources)


def _tree_sha256(pack_dir: Path) -> str:
    """Hash Pack content without Git metadata; never use the path as provenance."""
    root = pack_dir.resolve()
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise OSError("Pack content must not contain symlinks")
        if not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _safe_manifest(pack: PluginPack) -> PluginPackManifest | None:
    try:
        return load_plugin_manifest(Path(pack.path))
    except (OSError, ValueError, TypeError):
        return None


def _git_source(repo_url: str, repo_branch: str) -> TrustedPackSource | None:
    repo_key = canonical_remote_key(repo_url)
    for source in _trusted_sources():
        if source.kind != "git" or not source.repo_url:
            continue
        if canonical_remote_key(source.repo_url) != repo_key:
            continue
        if any(fnmatch.fnmatchcase(repo_branch, pattern) for pattern in source.refs):
            return source
    return None


def _bundled_source(manifest_name: str) -> TrustedPackSource | None:
    return next(
        (
            source
            for source in _trusted_sources()
            if source.kind == "bundled" and source.manifest_name == manifest_name
        ),
        None,
    )


def _in_bundled_root(pack: PluginPack) -> bool:
    try:
        return Path(pack.path).resolve().is_relative_to((config.project_root / "plugin_packs").resolve())
    except (OSError, ValueError):
        return False


class PackGovernanceService:
    """The only service allowed to approve a Pack for governed generation."""

    async def evaluate(self, pack: PluginPack, *, verify_integrity: bool = True) -> PackGovernanceDecision:
        manifest = _safe_manifest(pack)
        if manifest is None:
            return PackGovernanceDecision(False, pack.origin, False, "Pack manifest is invalid or unavailable.")

        source: TrustedPackSource | None
        expected_origin: str
        if pack.repo_url and pack.repo_branch:
            source = _git_source(pack.repo_url, pack.repo_branch)
            expected_origin = "git"
        else:
            source = _bundled_source(manifest.name) if _in_bundled_root(pack) else None
            expected_origin = "bundled"

        if source is None:
            return PackGovernanceDecision(
                False, pack.origin, pack.origin not in {"git", "bundled"},
                "Pack is not certified by configured Git/PR provenance.",
            )

        declared_provenance = manifest.certification.provenance if manifest.certification else None
        legacy_migration = (
            pack.origin == "legacy"
            and pack.certification_status == _LEGACY
            and source.allow_legacy_migration
        )
        # Backfill persists the source provenance, digest, and (for Git) commit
        # on the Pack row. Once persisted, that baseline no longer has a
        # legacy origin/status, so recognize it only when the configured
        # migration authority and stored provenance still agree. The normal
        # digest and checkout-integrity checks below remain mandatory.
        persisted_legacy_baseline = (
            source.allow_legacy_migration
            and pack.certification_status == _CERTIFIED
            and pack.certification_provenance == source.provenance
            and pack.content_sha256 is not None
            and declared_provenance is None
        )
        if declared_provenance != source.provenance and not (
            legacy_migration or persisted_legacy_baseline
        ):
            return PackGovernanceDecision(False, pack.origin, False, "Pack manifest provenance is not certified.")

        if pack.origin not in {expected_origin, "legacy"}:
            return PackGovernanceDecision(False, pack.origin, False, "Pack origin is not eligible for certification.")

        if expected_origin == "git":
            if not pack.last_commit_sha or len(pack.last_commit_sha) != 40:
                return PackGovernanceDecision(False, pack.origin, False, "Pack does not pin a reviewed Git commit.")
            if verify_integrity:
                try:
                    head = await run_git(pack.path, "rev-parse", "HEAD", strip=True)
                    dirty = await run_git(pack.path, "status", "--porcelain", strip=True)
                except Exception:
                    return PackGovernanceDecision(False, pack.origin, False, "Pack Git checkout cannot be verified.")
                if head != pack.last_commit_sha or dirty:
                    return PackGovernanceDecision(False, pack.origin, False, "Pack checkout changed outside its reviewed Git commit.")

        try:
            actual_digest = _tree_sha256(Path(pack.path))
        except OSError:
            return PackGovernanceDecision(False, pack.origin, False, "Pack content is unavailable.")

        if pack.content_sha256 and pack.content_sha256 != actual_digest:
            return PackGovernanceDecision(False, pack.origin, False, "Pack content changed outside certified Git/PR control.")
        if not pack.content_sha256 and not legacy_migration:
            return PackGovernanceDecision(False, pack.origin, False, "Pack has no certified content digest.")

        return PackGovernanceDecision(
            True,
            expected_origin,
            False,
            provenance=source.provenance,
            content_sha256=actual_digest,
        )

    async def require_generation_authority(self, pack: PluginPack) -> PackGovernanceDecision:
        decision = await self.evaluate(pack)
        if not decision.certified:
            raise PackGovernanceDenied(
                f"Pack '{pack.name}' cannot be used for governed document generation: "
                f"{decision.blocked_reason or 'certification is required'} Submit changes through Git/PR."
            )
        return decision

    async def certify_new_git_pack(
        self,
        *,
        repo_url: str,
        repo_branch: str,
        pack_dir: Path,
        commit_sha: str,
        manifest: PluginPackManifest,
    ) -> PackGovernanceDecision:
        source = self.require_configured_git_source(repo_url, repo_branch)
        if manifest.certification is None or manifest.certification.provenance != source.provenance:
            raise PackGovernanceDenied("Pack manifest provenance is not certified for this trusted Git source. Submit changes through Git/PR.")
        candidate = PluginPack(
            name=manifest.name,
            manifest_name=manifest.name,
            version=manifest.version,
            path=str(pack_dir),
            repo_url=repo_url,
            repo_branch=repo_branch,
            last_commit_sha=commit_sha,
            auto_update=True,
            origin="git",
            certification_status=_CERTIFIED,
            certification_provenance=source.provenance,
            content_sha256=_tree_sha256(pack_dir),
        )
        return await self.require_generation_authority(candidate)

    def require_configured_git_source(self, repo_url: str, repo_branch: str) -> TrustedPackSource:
        source = _git_source(repo_url, repo_branch)
        if source is None:
            raise PackGovernanceDenied(
                "Git repository/ref is not configured as a trusted Pack source. Submit changes through Git/PR."
            )
        return source

    def require_configured_git_repository(self, repo_url: str) -> None:
        repo_key = canonical_remote_key(repo_url)
        if any(
            source.kind == "git"
            and source.repo_url
            and canonical_remote_key(source.repo_url) == repo_key
            for source in _trusted_sources()
        ):
            return
        raise PackGovernanceDenied(
            "Git repository is not configured as a trusted Pack source. Submit changes through Git/PR."
        )

    async def backfill_explicit_legacy_packs(self, session) -> int:
        """Establish a digest only for config-named legacy Packs, once."""
        rows = (await session.execute(select(PluginPack).where(PluginPack.origin == "legacy"))).scalars().all()
        updated = 0
        for pack in rows:
            decision = await self.evaluate(pack)
            if not decision.certified or not decision.content_sha256:
                continue
            pack.origin = decision.origin
            pack.certification_status = _CERTIFIED
            pack.certification_provenance = decision.provenance
            pack.content_sha256 = decision.content_sha256
            updated += 1
        if updated:
            await session.commit()
        return updated


pack_governance = PackGovernanceService()
