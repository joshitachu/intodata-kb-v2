"""RAG bridge — POSTs generated docs to the RAG pipeline's /ingest/md endpoint."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from mcp_server.config import config
from mcp_server.services.log_manager import LogManager

logger = logging.getLogger(__name__)


class RagBridge:
    """Pushes markdown documents to the RAG pipeline for indexing."""

    def __init__(self, log_manager: LogManager | None = None) -> None:
        self.base_url = config.rag_base_url
        self.log_manager = log_manager

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

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/ingest/md",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def push_docs(
        self,
        project_id: str,
        docs: list[dict] | None = None,
        output_dir: str | None = None,
    ) -> dict:
        """Push multiple docs to RAG.

        If `docs` list provided, push those directly.
        If `output_dir` provided, read .md files from that directory.

        Returns: {pushed: int, errors: list[str]}
        """
        result = {"pushed": 0, "errors": []}

        if docs:
            items = docs
        elif output_dir:
            items = self._read_md_files(output_dir)
        else:
            result["errors"].append("No docs or output_dir provided")
            return result

        for doc in items:
            filename = doc.get("filename", "")
            content = doc.get("content", "")
            if not filename or not content:
                result["errors"].append(f"Skipped empty doc: {filename}")
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
                f"{len(result['errors'])} errors",
                level="info",
            )

        return result

    def _read_md_files(self, output_dir: str) -> list[dict]:
        """Read all .md files from a directory tree."""
        docs: list[dict] = []
        base = Path(output_dir)
        if not base.exists():
            return docs

        for md_file in sorted(base.rglob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                # Use relative path as filename
                rel = md_file.relative_to(base)
                docs.append({
                    "filename": str(rel).replace("\\", "/"),
                    "content": content,
                })
            except Exception as e:
                logger.error(f"Failed to read {md_file}: {e}")

        return docs
