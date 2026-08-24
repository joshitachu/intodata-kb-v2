"""Talend Management Console as a catalogue entry.

TMC is a **runtime sync** provider, not a live-evidence one: a scheduler in the
RAG backend mirrors executions, plans and failures into the runtime store every
60 seconds, and documentation reads bounded aggregates back out of that mirror
(`providers/custom/runtime_evidence.py`). Nothing queries TMC during a run.

So this module registers a manifest and nothing else. The instance -- its
credential, health, auto-disable threshold and sync interval -- stays in the RAG
backend's `runtime_sources` tables, which is where the scheduler that uses it
lives. Moving the secret here would mean migrating a live credential across two
databases and rewiring a working poller to fetch it back cross-service, for no
behaviour a user can see.

`test` and `execute` therefore refuse rather than pretend. The catalogue's
`register` contract requires both to exist; a provider that cannot be queried
in-band should say so loudly instead of returning an empty envelope that reads
as "TMC had nothing to report".
"""

from __future__ import annotations

from ..catalog import connector_catalog
from ..domain import (
    ConnectorBuildState,
    ConnectorInstanceConfig,
    ConnectorKind,
    ConnectorProviderManifest,
    ConnectorSourceProfile,
    EvidenceEnvelope,
    EvidenceQuestion,
)
from ..errors import ConnectorError

TALEND_TMC_MANIFEST = ConnectorProviderManifest(
    provider_id="talend_tmc",
    label="Talend Management Console",
    version="1",
    authentication_types=("api_key",),
    configuration_schema={
        "region": "TMC region host, e.g. eu.cloud.talend.com",
        "environment": "optional environment filter",
    },
    secret_fields=("credential",),
    # No gather capabilities: TMC contributes runtime aggregates through the
    # mirror, so it is never offered as an in-run evidence source.
    capabilities=(),
    kind=ConnectorKind.RUNTIME_SYNC,
    build_state=ConnectorBuildState.AVAILABLE,
    description=(
        "Mirror Talend job executions, plans and failures for runtime evidence "
        "in generated documentation."
    ),
    # One connection per TMC region/tenant.
    account_key_field="region",
    setup_capabilities=(),
)


class TalendTmcProvider:
    """Catalogue presence only; the sync path owns every TMC API call."""

    manifest = TALEND_TMC_MANIFEST

    async def test(self, instance: ConnectorInstanceConfig, credential: str) -> dict[str, object]:
        raise ConnectorError(
            "unsupported_operation",
            "Talend Management Console connections are validated by the runtime "
            "source that owns their credential, not through the connector API.",
        )

    async def execute(
        self,
        *,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str,
        capability_id: str,
        question: EvidenceQuestion,
        run_id: int,
        project_id: str,
    ) -> EvidenceEnvelope:
        raise ConnectorError(
            "unsupported_operation",
            "Talend Management Console exposes no in-run gather capability; its "
            "evidence reaches documentation through the runtime mirror.",
        )


def register_talend_tmc_provider() -> None:
    if TALEND_TMC_MANIFEST.provider_id not in {
        manifest.provider_id for manifest in connector_catalog.manifests()
    }:
        connector_catalog.register(TALEND_TMC_MANIFEST, TalendTmcProvider())
