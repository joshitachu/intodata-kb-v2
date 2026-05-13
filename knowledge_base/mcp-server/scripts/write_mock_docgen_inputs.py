from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import FactsManifest, HarnessRunState, ModelOptions, ModelResult, UnitFact


class FakeProvider:
    model_name = "fake/no-ai"
    supports_tools = False

    async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
        return ModelResult(
            success=True,
            text="# Fake Output\n\nThis response is deterministic and no AI provider was called.",
            model_name=self.model_name,
            input_tokens=0,
            output_tokens=0,
        )


async def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    staging = repo_root / "tmp" / "mock_docgen_inputs" / "docs_staging"
    if staging.exists():
        shutil.rmtree(staging)

    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_analysis").mkdir()
    (staging / "_assessment").mkdir()
    (staging / "_investigation").mkdir()

    facts = FactsManifest(
        project_id="mock-docgen",
        platform="generic",
        canonical_unit_label="code units",
        unit_count=3,
        groups={"Orders": 2, "Billing": 1},
        key_systems=["Stripe API", "Postgres", "S3"],
        units=[
            UnitFact(
                name="OrderImportJob",
                path="src/jobs/order_import.py",
                unit_type="job",
                group="Orders",
                component_count=4,
                key_systems=["Postgres", "S3"],
            ),
            UnitFact(
                name="OrderValidationService",
                path="src/services/order_validation.py",
                unit_type="service",
                group="Orders",
                component_count=6,
                key_systems=["Postgres"],
            ),
            UnitFact(
                name="StripeBillingSync",
                path="src/integrations/stripe_billing.py",
                unit_type="integration",
                group="Billing",
                component_count=5,
                key_systems=["Stripe API", "Postgres"],
            ),
        ],
    )
    facts.write(staging / "facts_manifest.json")

    (staging / "_preprocess" / "Orders.md").write_text(
        """# Preprocessed Group: Orders

Canonical units:
- OrderImportJob | source=src/jobs/order_import.py | reads CSV files from S3 and writes orders to Postgres
- OrderValidationService | source=src/services/order_validation.py | validates required fields, duplicate order IDs, and customer status

Evidence excerpts:
- OrderImportJob calls S3ObjectReader and OrderRepository.bulk_insert.
- OrderValidationService rejects orders with missing customer_id or invalid currency.
- Both units depend on the orders table and share retry/error handling helpers.
""",
        encoding="utf-8",
    )
    (staging / "_preprocess" / "_analysis_prompt_pack.md").write_text(
        """# Phase 2 Analysis Instructions

Required sections:
- COMPONENTS
- EXECUTION_FLOW
- DATA_FLOWS
- EXTERNAL_SYSTEMS
- ENVIRONMENT_CONFIG
- ERROR_HANDLING
- RISK_FLAGS
- GROUP_SUMMARY
""",
        encoding="utf-8",
    )
    (staging / "_investigation" / "_summary.md").write_text(
        """# Investigation Summary

- Orders group evidence was found in source files and scanner output.
- Billing group has an external Stripe dependency and redacted credentials.
- Scanner limits: runtime schedules are inferred from config names, not live scheduler state.
""",
        encoding="utf-8",
    )
    (staging / "_assessment" / "scorecard.md").write_text(
        """# Scorecard

Overall score: 3/5
Confidence: HIGH for inventory and source artifacts, MEDIUM for runtime scheduling.
Red flags:
- StripeBillingSync has external API retry risk.
- OrderImportJob can duplicate rows if idempotency keys are missing.
Quick wins:
- Add fixture tests for duplicate order IDs.
- Document rollback for failed billing sync.
""",
        encoding="utf-8",
    )
    (staging / "documentation_obligations.json").write_text(
        """{
  "platform": "generic",
  "shared": {
    "identity": ["name", "group", "source artifact", "type"],
    "runtime_config": ["config", "environment", "external system"],
    "debug_testing": ["debug", "test", "verify"]
  },
  "doc_targets": {
    "identity": ["02_COMPONENT_INVENTORY"],
    "runtime_config": ["05_RUNTIME_CONFIG_AND_EXTERNALS"],
    "debug_testing": ["06_OPERATIONS_TESTING_AND_DEBUGGING", "08_CHANGE_PLAYBOOK"]
  }
}
""",
        encoding="utf-8",
    )
    (staging / "_analysis" / "_doc_pack_02_COMPONENT_INVENTORY.md").write_text(
        """# Document Evidence Pack: 02_COMPONENT_INVENTORY

## Shared Project Scope
- Platform: generic
- Canonical inventory: 3 code units
- Groups: Orders=2, Billing=1

## All Groups And Units
- OrderImportJob | group=Orders | path=src/jobs/order_import.py | type=job | systems=Postgres, S3
- OrderValidationService | group=Orders | path=src/services/order_validation.py | type=service | systems=Postgres
- StripeBillingSync | group=Billing | path=src/integrations/stripe_billing.py | type=integration | systems=Stripe API, Postgres

## Documentation Obligations
- shared.identity: name, group, source artifact, type
- units.canonical_inventory: name, group, source artifact, type, key systems

## Doc-Specific Phase 2 Evidence
### COMPONENTS
OrderImportJob imports CSV orders from S3 and writes to Postgres.
OrderValidationService validates order fields and duplicate IDs.
StripeBillingSync sends billing updates to Stripe API and persists sync state.

### RISK_FLAGS
StripeBillingSync depends on external API availability.
OrderImportJob needs idempotency checks to avoid duplicate imports.
""",
        encoding="utf-8",
    )

    ctx = DocGenContext(
        project_id="mock-docgen",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(repo_root / "tmp" / "mock_docgen_inputs" / "logs"),
        preprocess_groups=["Orders", "Billing"],
        total_jobs=3,
        total_groups=2,
        effective_model="fake/no-ai",
        write_model="fake/no-ai",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_prompt_pack.md",
    )
    state = HarnessRunState(project_id="mock-docgen", facts_manifest=facts)
    provider = FakeProvider()
    harness = CustomHarnessProvider(provider)

    async def log_cb(pid: str, message: str, level: str = "info") -> None:
        print(f"{level.upper()} {message}")

    phase2_context = [
        staging / "facts_manifest.json",
        staging / "_preprocess" / "_analysis_prompt_pack.md",
        staging / "_investigation" / "_summary.md",
        staging / "_preprocess" / "Orders.md",
    ]
    await harness._complete_core_generation(
        ctx,
        state,
        provider=provider,
        phase="phase2",
        target="Orders",
        prompt=(
            "Write Phase 2 analysis for group Orders. Preserve exact canonical unit names. "
            "Use the supplied preprocessed evidence and investigation summary."
        ),
        context_files=phase2_context,
        opts=ModelOptions(max_tokens=32_000),
        log_cb=log_cb,
    )

    phase3_context = [
        staging / "documentation_obligations.json",
        staging / "_analysis" / "_doc_pack_02_COMPONENT_INVENTORY.md",
        staging / "_assessment" / "scorecard.md",
        staging / "facts_manifest.json",
    ]
    await harness._complete_core_generation(
        ctx,
        state,
        provider=provider,
        phase="phase3",
        target="02_COMPONENT_INVENTORY",
        prompt=(
            "Write 02_COMPONENT_INVENTORY.md. The output must include every canonical unit, "
            "its group, source artifact, type, key systems, risks, and scanner-limited caveats."
        ),
        context_files=phase3_context,
        opts=ModelOptions(max_tokens=64_000),
        log_cb=log_cb,
    )

    print("\nMock input snapshots written to:")
    print(staging / "_model_inputs")
    print("\nStart with:")
    print(staging / "_model_inputs" / "phase3" / "002_02_COMPONENT_INVENTORY" / "context_bundle.md")


if __name__ == "__main__":
    asyncio.run(main())
