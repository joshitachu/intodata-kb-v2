# Runtime Source Provider Contract

Runtime source providers implement `RuntimeSourceProvider` from `base.py`.

The contract has four mapper slots:

- Identity: `provider_key()` and `provider_label()`.
- Container: `container_key_field()` names the field used as the container key.
- Project map: framework-owned lookup from container key to Nexus project.
- Static ref: `static_ref_strategy()` returns `None` in v1.

Providers also expose `credential_schema()`, `validate_credentials()`, and
`sync()`. Credentials are accepted only on create/rotate/scheduler paths and are
never returned by read APIs or chat tools.

TMC maps `workspace_id` to the framework container key. The static-reference
slot is intentionally reserved for a later runtime-to-static cross-reference
epic.
