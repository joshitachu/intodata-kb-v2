"""RAG bridge -- POSTs generated docs to the RAG pipeline's /ingest/md endpoint."""

from __future__ import annotations

import hashlib
import logging
from contextlib import nullcontext
from pathlib import Path

import httpx

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.repositories.doc_rag_hash_repository import DocRagHashRepository
from mcp_server.services.log_manager import LogManager
from mcp_server.services.rag_support_package import (
    build_entity_aliases,
    collect_internal_artifacts,
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

    def __init__(self, log_manager: LogManager | None = None) -> None:
        self.base_url = config.rag_base_url
        self.log_manager = log_manager
        self._api_key = config.ingest_api_key

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
    ) -> dict:
        """Push multiple docs to RAG.

        If `docs` list provided, push those directly.
        If `output_dir` provided, read .md files from that directory.
        If `filenames` provided, only push docs whose filename is in that set.
        `extra_doc_names` lists non-numbered pack doc filenames to include alongside
        standard numbered enterprise docs (e.g. TABLE_INVENTORY.md).

        When MCP_INCREMENTAL_HASH_SKIP_ENABLED is True, docs whose SHA-256 matches
        the last successfully pushed hash are silently skipped.  The result dict
        always includes a `skipped` counter (0 when the flag is off).

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
        # Open a DB session only when hash skip is active; otherwise use a
        # no-op context so the loop body stays identical for both paths.
        db_ctx = async_session_factory() if hash_enabled else nullcontext(None)

        async with db_ctx as _db:
            repo = DocRagHashRepository(_db) if _db is not None else None

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

                if repo is not None and doc_hash is not None:
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
                    result["pushed"] += 1
                    if self.log_manager:
                        await self.log_manager.emit(
                            project_id,
                            f"Pushed to RAG: {filename}",
                            level="info",
                        )
                    # Record hash only after a confirmed successful push.
                    if repo is not None and doc_hash is not None:
                        await repo.upsert_hash(project_id, filename, doc_hash)
                        await _db.commit()
                except Exception as e:
                    error_msg = f"Failed to push {filename}: {e}"
                    result["errors"].append(error_msg)
                    logger.error(error_msg)
                    if self.log_manager:
                        await self.log_manager.emit(project_id, error_msg, level="error")

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
        """Read user-facing doc .md files from the top-level docs/ directory.

        Includes numbered enterprise docs (NN_*.md) plus any filenames in
        extra_filenames (for plugin-pack docs that don't use numeric prefixes).
        Internal artifacts starting with '_' are always excluded.
        """
        docs: list[dict] = []
        base = Path(output_dir)
        if not base.exists():
            return docs

        for md_file in sorted(base.glob("*.md")):
            name = md_file.name
            is_numbered = name[0].isdigit()
            is_pack_doc = extra_filenames is not None and name in extra_filenames
            if not is_numbered and not is_pack_doc:
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                docs.append({
                    "filename": name,
                    "content": content,
                })
            except Exception as e:
                logger.error(f"Failed to read {md_file}: {e}")

        return docs
