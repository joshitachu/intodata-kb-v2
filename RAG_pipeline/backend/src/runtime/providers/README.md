# Runtime Source Provider Contract

Runtime source providers implement `RuntimeSourceProvider` from `base.py`.

The contract has five mapper slots:

- Identity: `provider_key()` and `provider_label()`.
- Container: `container_key_field()` names the field used as the container key.
- Project map: framework-owned lookup from container key to Nexus project.
- Static ref: `static_ref_strategy()` declares which of the provider's own
  `RuntimeJob` fields names the scanned design, and is exact-match only.
  `None` means the provider exposes no static reference at all.
- Executable semantics: `executable_semantics()` says whether one runtime row of
  a given `RuntimeExecutableKind` is an `execution` (one run) or a `lifecycle`
  (a deployment that stays up).

Providers also expose `credential_schema()`, `validate_credentials()`, and
`sync()`. Credentials are accepted only on create/rotate/scheduler paths and are
never returned by read APIs or chat tools.

TMC maps `workspace_id` to the framework container key and declares
`artifact_name` as its static reference -- the DESIGN name Studio published,
not the operator-facing task name. Elasticsearch declares `None`: a log document
carries no reference to a published design, so the mapping stays a human
decision there. `src/runtime/topology/binding.py` resolves the declared
strategies into `runtime_job_scan_maps` at the end of each sync cycle.
