This document provisions the Azure infrastructure the ADF bundle in
03_ADF_PIPELINES depends on. All templates must be complete and deployable
as-is; environment-specific values live only in parameter files and script
variables at the top of each block.

Prerequisites:
- Bullet list: required Azure CLI version, required RBAC roles for the
  deploying identity, and the resource providers to register.
- Name the evidenced on-premises systems that require a self-hosted
  integration runtime VM or host, without inventing network details.

Bicep Templates:
- Emit `main.bicep` plus modules as separate artifacts with bold filename
  lines. Provision only what the evidenced workload needs: the Data
  Factory (system-assigned identity), Key Vault with an access policy or
  RBAC grant for the factory identity, storage accounts or ADLS only if
  file sources or landing zones appear in evidence, Log Analytics with ADF
  diagnostic settings, and budget-friendly defaults (no premium SKUs
  without an evidenced reason).
- Resource names take an `environmentCode` and `projectCode` parameter so
  the same templates deploy to dev, test, and prod.

Parameter Files:
- One `parameters.<env>.json` per environment found in the Talend context
  evidence, carrying the non-secret context values for that environment.
  Secrets appear only as Key Vault secret names to be set, never as values.

Deployment Script:
- A single `deploy.sh` artifact: idempotent Azure CLI script that creates
  the resource group, deploys the Bicep, sets placeholder Key Vault secrets
  with `az keyvault secret set` lines the operator fills in (echo a clear
  warning listing every secret still holding a placeholder), and publishes
  the ADF artifacts from the repo layout defined in 03_ADF_PIPELINES.
- The script must fail fast with `set -euo pipefail` and print what it will
  create before creating it.

Post-Deployment Configuration:
- Numbered manual steps that cannot be scripted: installing and registering
  the self-hosted integration runtime, granting the factory identity access
  to evidenced databases, entering real secret values, and starting the
  triggers (which deploy stopped).

Evidence Notes:
- Name infrastructure choices that rest on missing evidence, such as
  unknown data volumes, unknown network topology, or unconfirmed target
  database tiers.
