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
        "postgresql+asyncpg://admin:@localhost:5432/intodata_kb"
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

    # Auth (shared with RAG backend)
    jwt_secret_key: str = "change-me-in-production-use-a-strong-random-key"
    ingest_api_key: str = ""
    jwt_algorithm: str = "HS256"
    allowed_origins: str = "http://localhost:3000,http://localhost:5173,http://localhost:8001"

    # Doc generation
    doc_generation_model: str = "claude-sonnet-4-6"
    doc_generation_write_model: str = "claude-opus-4-6"
    large_project_threshold: int = 200  # jobs; >=this splits Phase 3 into 4 agents

    # Ollama (for AI job summaries)
    ollama_base_url: str = "http://localhost:11434"
    ollama_summary_model: str = "gemma3:12b"
    ollama_summary_concurrency: int = 4
    auto_generate_summaries: bool = False

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
