This document is the deployable Azure Data Factory bundle. Every JSON block
must be complete and valid ADF resource JSON that deploys as-is; the reader
saves each block under its stated filename into an ADF Git repository (or
imports it via `az datafactory` / the ADF REST API) without editing bodies.

Deployment Instructions:
- Short numbered steps: where to place the files in an ADF Git repo layout
  (`linkedService/`, `dataset/`, `pipeline/`, `trigger/`, `dataflow/`),
  which Key Vault secrets must exist first (exact names), and the order to
  publish in.

Linked Services:
- One JSON artifact per evidenced connection, named `LS_<System>_<ConnectionName>`.
- All credentials via AzureKeyVaultSecret references using the
  `kv-<connection-name>-<field>` naming from the analysis. Include the Key
  Vault linked service itself as the first artifact.
- Reference the self-hosted integration runtime by parameterized name for
  evidenced on-premises sources.

Datasets:
- One JSON artifact per distinct source or target object, named
  `DS_<System>_<Object>`, with schema columns taken from the evidenced
  Talend flow schemas. Parameterize file paths and table names that came
  from Talend context variables.

Pipelines:
- One pipeline per Talend job, named `PL_<JobName>`, preserving the job's
  activity order, conditional links (OnSubjobOk as success dependencies,
  OnSubjobError as failure paths), and write semantics (translate
  truncate-load as a pre-copy script or Script activity, upserts via the
  evidenced keys).
- Orchestration jobs become pipelines of Execute Pipeline activities with
  the evidenced parameter passing.
- TRANSFORM jobs reference a Mapping Data Flow artifact `DF_<JobName>`
  whose script reproduces the evidenced tMap joins, expressions, filters,
  and reject flows; include the data flow JSON in this section.
- For untranslatable logic, insert a Wait or Fail activity named
  `TODO_<component>` whose description quotes the Talend component and
  reason, so the gap is visible inside ADF itself.
- Reproduce evidenced error handling: failure dependencies that write to
  the evidenced reject target or raise an alert, not generic try-catch
  prose.

Triggers:
- One trigger per evidenced schedule, named `TR_<JobName>`, translating the
  evidenced cron or TAC schedule exactly. Create triggers with
  `runtimeState: Stopped` so nothing runs on deployment. If a job has no
  scheduling evidence, list it here as manual-start and say so.

Parameter Reference:
- Table of every pipeline and global parameter: name, originating Talend
  context variable, example non-secret value per environment, and whether
  it resolves from Key Vault.

Evidence Notes:
- List every placeholder activity and every artifact affected by an
  evidence gap, by pipeline name.
