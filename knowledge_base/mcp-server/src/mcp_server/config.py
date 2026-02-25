"""Configuration for MCP server (Pydantic Settings)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPServerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MCP_",
        extra="ignore",
    )

    # PostgreSQL (same DB as RAG pipeline)
    database_url: str = (
        "postgresql+asyncpg://postgres:voetbal123@localhost:5432/intodata_kb"
    )

    # RAG pipeline base URL
    rag_base_url: str = "http://localhost:8000"

    # intodoc CLI path (defaults to system 'intodoc')
    intodoc_command: str = "intodoc"

    # claude CLI path (for doc generation)
    claude_command: str = "claude"

    # Base data directory for logs, uploads, repos
    data_dir: Path = Path(__file__).resolve().parent.parent.parent / "data"

    # intodoc default output directory
    intodoc_output_dir: Path = Path(__file__).resolve().parent.parent.parent.parent / "intodoc-output"

    # vaxxinova project root (parent of mcp-server)
    project_root: Path = Path(__file__).resolve().parent.parent.parent.parent

    # Hindsight memory
    hindsight_enabled: bool = False
    hindsight_url: str = "http://localhost:8888"

    # App
    log_level: str = "INFO"

    @property
    def logs_dir(self) -> Path:
        d = self.data_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def uploads_dir(self) -> Path:
        d = self.data_dir / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def repos_dir(self) -> Path:
        d = self.data_dir / "repos"
        d.mkdir(parents=True, exist_ok=True)
        return d


config = MCPServerConfig()
