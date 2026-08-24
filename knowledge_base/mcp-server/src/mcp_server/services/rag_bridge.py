"""RAG bridge -- POSTs generated docs to the RAG pipeline's /ingest/md endpoint."""

from __future__ import annotations

import hashlib
import logging
from contextlib import nullcontext
from pathlib import Path
from collections.abc import Mapping, Sequence

import httpx

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.repositories.doc_rag_hash_repository import DocRagHashRepository
from mcp_server.services.doc_quality_scorer import is_published_doc_filename
from mcp_server.services.log_manager import LogManager
from mcp_server.services.rag_support_package import (
    build_entity_aliases,
    collect_internal_artifacts,
)
from mcp_server.services.living_docs.rag_revisions import (
    PreparedRagRevision,
    RagArtifact,
    RagActivationReceipt,
    RagMutation,
    RagMutationKind,
    RagValidationReceipt,
    RevisionedRagPort,
    RevisionNotPreparedError,
    RevisionValidationError,
)

logger = logging.getLogger(__name__)


def _sha256_hex(text: str) -> str:
    """Return the SHA-256 hex digest of text encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rag_push_succeeded(result: dict, *, expected_count: int | None = None) -> bool:
    """Return True only when a required RAG push completed without errors.

    When expected_count is given, skipped (unchanged) docs count towards the
    effective total so an all-skip second push is not falsely reported as failed.
    """
    if result.get("errors"):
        return False
    if expected_count is not None:
        effective = int(result.get("pushed") or 0) + int(result.get("skipped") or 0)
        if effective < expected_count:
            return False
    return True


class RagBridge:
    """Pushes markdown documents to the RAG pipeline for indexing."""

    def __init__(
        self,
        log_manager: LogManager | None = None,
        *,
        revisioned_rag: RevisionedRagPort | None = None,
    ) -> None:
        self.base_url = config.rag_base_url
        self.log_manager = log_manager
        self._api_key = config.ingest_api_key
        # Incremental patch publication injects a revision-aware adapter.  The
        # legacy bridge remains unchanged for first ingestion/manual recovery,
        # so an ordinary caller never accidentally stages or activates a
        # revision through its existing push methods.
        self._revisioned_rag = revisioned_rag

    def _require_revisioned_rag(self) -> RevisionedRagPort:
        if self._revisioned_rag is None:
            raise RuntimeError(
                "Revision-aware RAG is not configured; use the explicit "
                "prepare/validate/activate patch path"
            )
        return self._revisioned_rag

    async def prepare_revision(
        self,
        project_id: str,
        knowledge_revision_id: str,
        mutations: Sequence[RagMutation],
        *,
        base_knowledge_revision_id: str | None,
    ) -> PreparedRagRevision:
        """Prepare targeted canonical/internal mutations while reads stay active.

        This is the S2 entry point for the incremental coordinator.  It has no
        fallback to ``sync_internal_layers`` or ``push_docs`` because either
        legacy method can expose a partial update before a revision activates.
        """
        return await self._require_revisioned_rag().prepare(
            project_id,
            knowledge_revision_id,
            mutations,
            base_knowledge_revision_id=base_knowledge_revision_id,
        )

    async def validate_revision(
        self,
        project_id: str,
        knowledge_revision_id: str,
        *,
        required_artifact_hashes: Mapping[str, str] | None = None,
        required_deletes: Sequence[str] = (),
        required_aliases: Mapping[str, str] | None = None,
    ) -> RagValidationReceipt:
        """Validate exact membership/hashes before making a revision visible."""
        return await self._require_revisioned_rag().validate(
            project_id,
            knowledge_revision_id,
            required_artifact_hashes=required_artifact_hashes,
            required_deletes=required_deletes,
            required_aliases=required_aliases,
        )

    async def activate_revision(
        self, project_id: str, knowledge_revision_id: str
    ) -> RagActivationReceipt:
        """Atomically expose a previously validated RAG revision."""
        return await self._require_revisioned_rag().activate(
            project_id,
            knowledge_revision_id,
        )

    async def active_knowledge_revision(self, project_id: str) -> str | None:
        """Return the revision that retrieval must filter to for this project."""
        return await self._require_revisioned_rag().active_revision(project_id)

    async def rollback_revision(
        self,
        project_id: str,
        knowledge_revision_id: str,
        *,
        expected_active_revision_id: str,
    ) -> RagActivationReceipt:
        """CAS-restore a retained revision; this never calls an embedding provider."""

        return await self._require_revisioned_rag().rollback(
            project_id,
            knowledge_revision_id,
            expected_active_revision_id=expected_active_revision_id,
        )

    def http_revisioned_adapter(self) -> "HttpRevisionedRagAdapter":
        """Build the authenticated HTTP port used by the enabled patch route."""
        return HttpRevisionedRagAdapter(self)

    @staticmethod
    def push_succeeded(result: dict, *, expected_count: int | None = None) -> bool:
        """Return True only when a required RAG push completed without errors."""
        return rag_push_succeeded(result, expected_count=expected_count)

    async def delete_project_documents(self, project_id: str) -> dict:
        """Delete all documents for a project from the RAG pipeline.

        Returns the response dict from the RAG API.
        """
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=config.rag_delete_timeout_s) as client:
            resp = await client.delete(
                f"{self.base_url}/api/v1/ingest/by-project/{project_id}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_document(self, project_id: str, filename: str) -> dict:
        """Delete one generated document from the RAG pipeline."""
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=config.rag_delete_timeout_s) as client:
            resp = await client.delete(
                f"{self.base_url}/api/v1/ingest/by-project/{project_id}/document/{filename}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_internal_documents(self, project_id: str) -> dict:
        """Delete all internal support-layer docs (and aliases) for a project."""
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=config.rag_delete_timeout_s) as client:
            resp = await client.delete(
                f"{self.base_url}/api/v1/ingest/by-project/{project_id}/internal",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def post_entity_aliases(self, project_id: str, aliases: list[dict]) -> dict:
        """Replace the project's entity alias set on the RAG backend."""
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=config.rag_push_timeout_s) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/ingest/entity-aliases",
                json={"project_id": project_id, "aliases": aliases},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def sync_internal_layers(
        self,
        project_id: str,
        docs_dir: Path,
        run_id: int | None = None,
    ) -> dict:
        """Replace the project's internal support layers and entity aliases.

        Removed groups are invisible to the canonical-doc diff, so the
        internal set is always deleted wholesale and re-pushed from the
        current `docs/` tree. Canonical doc push behavior is unaffected.

        After a successful wholesale delete, all hash rows for the project are
        invalidated so the subsequent push_docs call re-establishes baselines.
        The internal push loop itself does NOT use the skip predicate -- internal
        layers are always repushed unconditionally.

        Returns: {pushed: int, aliases: int, errors: list[str]}
        """
        result: dict = {"pushed": 0, "aliases": 0, "errors": []}

        artifacts = collect_internal_artifacts(docs_dir, project_id=project_id, run_id=run_id)
        aliases = build_entity_aliases(docs_dir)

        try:
            await self.delete_internal_documents(project_id)
        except Exception as e:
            # Without a clean delete, a re-push could leave stale internal
            # docs from removed groups -- stop rather than mix old and new.
            result["errors"].append(f"Internal-layer delete failed: {e}")
            logger.error("Internal-layer delete failed for %s: %s", project_id, e)
            return result

        # Invalidate all project hash rows: the wholesale internal delete means
        # any stored hashes are potentially stale.  The next push_docs call will
        # re-populate them.  Errors here are non-fatal (hash rows are advisory).
        if config.incremental_hash_skip_enabled:
            try:
                async with async_session_factory() as _db:
                    await DocRagHashRepository(_db).delete_all_for_project(project_id)
                    await _db.commit()
            except Exception as e:
                logger.warning(
                    "Hash invalidation failed for %s after internal delete: %s",
                    project_id,
                    e,
                )

        for artifact in artifacts:
            try:
                await self.push_markdown(
                    project_id=project_id,
                    filename=artifact["filename"],
                    content=artifact["content"],
                    metadata=artifact["metadata"],
                )
                result["pushed"] += 1
            except Exception as e:
                error_msg = f"Failed to push internal {artifact['filename']}: {e}"
                result["errors"].append(error_msg)
                logger.error(error_msg)

        if aliases:
            try:
                alias_result = await self.post_entity_aliases(project_id, aliases)
                result["aliases"] = alias_result.get("inserted", len(aliases))
            except Exception as e:
                result["errors"].append(f"Entity alias ingest failed: {e}")
                logger.error("Entity alias ingest failed for %s: %s", project_id, e)

        if self.log_manager:
            await self.log_manager.emit(
                project_id,
                f"Internal RAG sync: {result['pushed']} docs, "
                f"{result['aliases']} aliases, {len(result['errors'])} errors",
                level="info",
            )
        return result

    async def push_markdown(
        self,
        project_id: str,
        filename: str,
        content: str,
        source: str = "",
        metadata: dict | None = None,
    ) -> dict:
        """Push a single markdown document to RAG pipeline.

        Returns the response dict from the RAG API.
        """
        payload = {
            "text": content,
            "filename": filename,
            "source": source or f"intodoc:{project_id}",
            "project_id": project_id,
            "metadata": metadata or {},
        }

        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=config.rag_push_timeout_s) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/ingest/md",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def push_docs(
        self,
        project_id: str,
        docs: list[dict] | None = None,
        output_dir: str | None = None,
        filenames: set[str] | list[str] | None = None,
        extra_doc_names: set[str] | list[str] | None = None,
        force: bool = False,
    ) -> dict:
        """Push multiple docs to RAG.

        If `docs` list provided, push those directly.
        If `output_dir` provided, read .md files from that directory.
        If `filenames` provided, only push docs whose filename is in that set.
        `extra_doc_names` lists non-numbered pack doc filenames to include alongside
        standard numbered enterprise docs (e.g. TABLE_INVENTORY.md).

        When MCP_INCREMENTAL_HASH_SKIP_ENABLED is True, docs whose SHA-256 matches
        the last successfully pushed hash are skipped.  Pass `force=True` to bypass
        the skip and re-embed every doc unconditionally (operator recovery path for
        out-of-band vector-store drift); fresh hashes are still recorded afterwards.
        The result dict always includes a `skipped` counter (0 when skipping is off).

        Returns: {pushed: int, skipped: int, errors: list[str]}
        """
        result: dict = {"pushed": 0, "skipped": 0, "errors": []}
        filename_filter = set(filenames) if filenames is not None else None
        extra_set = set(extra_doc_names) if extra_doc_names else None

        if docs:
            items = docs
        elif output_dir:
            items = self._read_md_files(output_dir, extra_filenames=extra_set)
        else:
            result["errors"].append("No docs or output_dir provided")
            return result

        hash_enabled = config.incremental_hash_skip_enabled
        # Hash-skip is bypassed on a forced push: a forced push always re-embeds
        # every doc so out-of-band vector-store drift can be repaired, while still
        # refreshing the stored hashes.
        skip_enabled = hash_enabled and not force
        # Open a DB session only when hash tracking is active; otherwise use a
        # no-op context so the loop body stays identical for both paths.
        db_ctx = async_session_factory() if hash_enabled else nullcontext(None)

        async with db_ctx as _db:
            repo = DocRagHashRepository(_db) if _db is not None else None
            hashes_staged = False

            for doc in items:
                filename = doc.get("filename", "")
                content = doc.get("content", "")
                if filename_filter is not None and filename not in filename_filter:
                    continue
                if not filename or not content:
                    result["errors"].append(f"Skipped empty doc: {filename}")
                    continue

                # Compute hash once; reused for both skip check and post-push upsert.
                doc_hash = _sha256_hex(content) if hash_enabled else None

                if repo is not None and doc_hash is not None and skip_enabled:
                    stored = await repo.get_hash(project_id, filename)
                    if stored == doc_hash:
                        result["skipped"] += 1
                        if self.log_manager:
                            await self.log_manager.emit(
                                project_id,
                                f"Hash skip (unchanged): {filename}",
                                level="info",
                            )
                        continue

                try:
                    await self.push_markdown(
                        project_id=project_id,
                        filename=filename,
                        content=content,
                    )
                except Exception as e:
                    error_msg = f"Failed to push {filename}: {e}"
                    result["errors"].append(error_msg)
                    logger.error(error_msg)
                    if self.log_manager:
                        await self.log_manager.emit(project_id, error_msg, level="error")
                    continue

                # Push confirmed successful.
                result["pushed"] += 1
                if self.log_manager:
                    await self.log_manager.emit(
                        project_id,
                        f"Pushed to RAG: {filename}",
                        level="info",
                    )

                # Stage the hash in the session (committed once after the loop).
                # A hash-tracking failure must NOT mark the doc as a failed push --
                # the doc is already in the vector store.
                if repo is not None and doc_hash is not None:
                    await repo.upsert_hash(project_id, filename, doc_hash)
                    hashes_staged = True

            # Single commit for all staged hashes.  A commit failure means only
            # that hash tracking lagged; every counted doc is already in RAG, so
            # we log a warning rather than corrupting the push result.
            if repo is not None and hashes_staged:
                try:
                    await _db.commit()
                except Exception as e:
                    logger.warning(
                        "RAG hash commit failed for %s (docs pushed OK): %s",
                        project_id, e,
                    )

        if self.log_manager:
            await self.log_manager.emit(
                project_id,
                f"RAG push complete: {result['pushed']} docs pushed, "
                f"{result.get('skipped', 0)} skipped (unchanged), "
                f"{len(result['errors'])} errors",
                level="info",
            )

        return result

    async def post_usage(
        self,
        interface: str,
        project_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        latency_ms: int | None = None,
        model_name: str | None = None,
        provider: str | None = None,
        query_text: str | None = None,
    ) -> None:
        """POST usage record to RAG backend (fire-and-forget with warning on failure)."""
        payload = {
            "interface": interface,
            "project_id": project_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "model_name": model_name,
            "provider": provider,
            "query_text": query_text,
        }

        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with httpx.AsyncClient(timeout=config.rag_usage_timeout_s) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v1/usage/record",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                logger.info("Posted usage record: interface=%s project=%s", interface, project_id)
        except Exception as e:
            logger.warning("Failed to post usage record: %s", e)

    def _read_md_files(
        self,
        output_dir: str,
        extra_filenames: set[str] | None = None,
    ) -> list[dict]:
        """Read the published doc set from the top-level docs/ directory.

        Selection is name-agnostic: every top-level ``*.md`` that
        ``is_published_doc_filename`` accepts is read, whatever the active pack
        named it (One Pack decisions D45 / R8). The former ``NN_`` digit-prefix
        gate silently dropped pack-declared documents such as
        ``INJECTION_FINDINGS.md`` on every push path that did not pass them in
        ``extra_filenames`` -- five secondary paths (true patch executor, git
        diff patcher, evidence doc update/revert, the ``insert_to_rag`` MCP
        tool) did not, so those pushes were no-ops with no error and no log.
        The ``NN_`` prefix is a filename convention; the pack contract is the
        authority.

        Excluded: ``_``-prefixed internal artifacts (pushed separately by
        ``sync_internal_layers``) and lowercase snake_case engineering-fact
        support artifacts (``engineering_facts.md``, ``dependency_map.md``,
        ...). ``extra_filenames`` still forces a file in, so callers that
        already enumerate the docs root (the primary generation push and the
        manual re-push) keep pushing exactly what they push today, support
        artifacts included; the parameter is now an additive superset rather
        than the only way a pack doc can be seen.
        """
        docs: list[dict] = []
        base = Path(output_dir)
        if not base.exists():
            return docs

        forced = extra_filenames or set()
        for md_file in sorted(base.glob("*.md")):
            name = md_file.name
            if not is_published_doc_filename(name) and name not in forced:
                # Never silent: a dropped top-level document is exactly the
                # class of defect this predicate replaced.
                logger.info(
                    "RAG push: skipping non-published top-level markdown %s "
                    "(internal or engineering-fact support artifact)",
                    md_file,
                )
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, ValueError) as e:
                logger.error("Failed to read %s: %s", md_file, e)
                continue
            docs.append({
                "filename": name,
                "content": content,
            })

        return docs


class HttpRevisionedRagAdapter:
    """RevisionedRagPort backed by the authenticated RAG HTTP surface.

    Only changed upserts are sent through ``/ingest/md``.  The prepare endpoint
    receives the complete membership and clones unchanged base vectors under
    the target revision, so activation can switch one coherent snapshot
    without re-embedding inherited artifacts.
    """

    def __init__(self, bridge: RagBridge) -> None:
        self._bridge = bridge

    def _headers(self) -> dict[str, str]:
        return ({"Authorization": f"Bearer {self._bridge._api_key}"}
                if self._bridge._api_key else {})

    async def _request(self, method: str, path: str, *, payload: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=config.rag_push_timeout_s) as client:
            response = await client.request(
                method,
                f"{self._bridge.base_url}{path}",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def _revision_detail(self, project_id: str, knowledge_revision_id: str) -> dict:
        try:
            return await self._request(
                "GET", f"/api/v1/ingest/revisions/{project_id}/{knowledge_revision_id}"
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RevisionNotPreparedError(
                    f"Base revision {knowledge_revision_id!r} is not prepared"
                ) from exc
            raise

    async def prepare(
        self,
        project_id: str,
        knowledge_revision_id: str,
        mutations: Sequence[RagMutation],
        *,
        base_knowledge_revision_id: str | None,
    ) -> PreparedRagRevision:
        base_membership: dict[str, str] = {}
        base_aliases: dict[str, str] = {}
        if base_knowledge_revision_id is not None:
            base = await self._revision_detail(project_id, base_knowledge_revision_id)
            base_membership = dict(base.get("membership_hashes") or {})
            base_aliases = dict(base.get("aliases") or {})

        membership = dict(base_membership)
        aliases = dict(base_aliases)
        deleted: set[str] = set()
        embedded: set[str] = set()
        reused: set[str] = set(base_membership.values())
        seen: set[str] = set()
        for mutation in mutations:
            if mutation.logical_id in seen:
                raise RevisionValidationError("A revision may mutate each logical_id only once")
            seen.add(mutation.logical_id)
            if mutation.kind is RagMutationKind.UPSERT:
                assert mutation.artifact is not None
                artifact: RagArtifact = mutation.artifact
                artifact_hash = artifact.content_hash
                if base_membership.get(artifact.logical_id) != artifact_hash:
                    await self._bridge.push_markdown(
                        project_id=project_id,
                        filename=artifact.logical_id,
                        content=artifact.content,
                        metadata={
                            **dict(artifact.metadata),
                            "source_layer": artifact.source_layer,
                            "artifact_type": artifact.artifact_type,
                            "artifact_id": artifact.logical_id,
                            "content_sha256": artifact_hash,
                            "knowledge_revision_id": knowledge_revision_id,
                        },
                    )
                    embedded.add(artifact_hash)
                else:
                    reused.add(artifact_hash)
                membership[artifact.logical_id] = artifact_hash
                aliases.pop(artifact.logical_id, None)
            elif mutation.kind is RagMutationKind.DELETE:
                membership.pop(mutation.logical_id, None)
                aliases.pop(mutation.logical_id, None)
                deleted.add(mutation.logical_id)
            else:
                assert mutation.alias_target is not None
                aliases[mutation.logical_id] = mutation.alias_target

        await self._request(
            "POST",
            "/api/v1/ingest/revisions/prepare",
            payload={
                "project_id": project_id,
                "knowledge_revision_id": knowledge_revision_id,
                "base_knowledge_revision_id": base_knowledge_revision_id,
                "membership_hashes": membership,
                "deleted_logical_ids": sorted(deleted),
                "aliases": aliases,
            },
        )
        return PreparedRagRevision(
            project_id=project_id,
            knowledge_revision_id=knowledge_revision_id,
            base_knowledge_revision_id=base_knowledge_revision_id,
            membership_hashes=membership,
            deleted_logical_ids=frozenset(deleted),
            aliases=aliases,
            embedded_content_hashes=frozenset(embedded),
            reused_content_hashes=frozenset(reused),
        )

    async def validate(
        self,
        project_id: str,
        knowledge_revision_id: str,
        *,
        required_artifact_hashes: Mapping[str, str] | None = None,
        required_deletes: Sequence[str] = (),
        required_aliases: Mapping[str, str] | None = None,
    ) -> RagValidationReceipt:
        detail = await self._revision_detail(project_id, knowledge_revision_id)
        membership = dict(detail.get("membership_hashes") or {})
        deletes = frozenset(detail.get("deleted_logical_ids") or [])
        aliases = dict(detail.get("aliases") or {})
        if any(membership.get(key) != value for key, value in (required_artifact_hashes or {}).items()):
            raise RevisionValidationError("Artifact membership mismatch")
        if not set(required_deletes).issubset(deletes):
            raise RevisionValidationError("Delete membership mismatch")
        if any(aliases.get(key) != value for key, value in (required_aliases or {}).items()):
            raise RevisionValidationError("Alias membership mismatch")
        return RagValidationReceipt(
            project_id=project_id,
            knowledge_revision_id=knowledge_revision_id,
            membership_hashes=membership,
            deleted_logical_ids=deletes,
            aliases=aliases,
        )

    async def activate(self, project_id: str, knowledge_revision_id: str) -> RagActivationReceipt:
        payload = await self._request(
            "POST", f"/api/v1/ingest/revisions/{project_id}/{knowledge_revision_id}/activate"
        )
        return RagActivationReceipt(
            project_id=project_id,
            knowledge_revision_id=knowledge_revision_id,
            previous_knowledge_revision_id=payload.get("previous_knowledge_revision_id"),
        )

    async def active_revision(self, project_id: str) -> str | None:
        payload = await self._request("GET", f"/api/v1/ingest/revisions/{project_id}/active")
        return payload.get("knowledge_revision_id")

    async def rollback(
        self,
        project_id: str,
        knowledge_revision_id: str,
        *,
        expected_active_revision_id: str,
    ) -> RagActivationReceipt:
        payload = await self._request(
            "POST",
            f"/api/v1/ingest/revisions/{project_id}/{knowledge_revision_id}/rollback",
            payload={"expected_active_revision_id": expected_active_revision_id},
        )
        return RagActivationReceipt(
            project_id=project_id,
            knowledge_revision_id=knowledge_revision_id,
            previous_knowledge_revision_id=payload.get("previous_knowledge_revision_id"),
        )
