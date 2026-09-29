from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: The fallback ``MC_API_KEY`` in the root ``docker-compose.yml``. It is public
#: (in the repo), so a Mission Control that holds the signing key refuses to
#: issue while it is in use.
REPO_DEFAULT_API_KEY = "mc-dev-local-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MC_",
        env_file=".env",
        extra="ignore",
    )

    api_key: str
    snapshots_dir: Path = Path("snapshots")
    host: str = "0.0.0.0"
    port: int = 9100

    #: ``MC_CLIENTS_CONFIG``: per-client control keys. Default: ``clients.json``
    #: next to ``snapshots_dir`` (``/app/clients.json`` in the container,
    #: ``mission_control/clients.json`` when run from the package directory).
    clients_config: Path | None = None

    #: ``MC_LICENCE_SIGNING_KEY_FILE`` / ``MC_LICENCE_SIGNING_KID``. Only the
    #: central, operator-side Mission Control sets these. The key file is read
    #: lazily, when a licence is issued, and its content is never logged.
    licence_signing_key_file: Path | None = None
    licence_signing_kid: str | None = None

    #: ``MC_LICENCE_LEDGER_DIR``: where ``issued.jsonl`` lives. Default:
    #: ``licences/`` next to ``snapshots_dir``.
    licence_ledger_dir: Path | None = None

    @property
    def data_dir(self) -> Path:
        """The directory that holds ``snapshots_dir``; defaults are relative to it."""
        return self.snapshots_dir.parent

    @property
    def clients_config_path(self) -> Path:
        return self.clients_config or self.data_dir / "clients.json"

    @property
    def licence_ledger_path(self) -> Path:
        return self.licence_ledger_dir or self.data_dir / "licences"


settings = Settings()
