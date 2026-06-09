SECTION-BY-SECTION EXTRACTION REQUIREMENTS

### COMPONENTS
For EVERY recipe and callable function:
- Name, purpose, folder/group membership
- All steps: action type, connector, operation name
- Trigger type and configuration: event / schedule / webhook / callable
- Callable dependencies: which recipes call this recipe (referenced-by)
- Connector accounts used per step

### EXECUTION_FLOW
- Step sequence in full execution order
- Every conditional branch (if/unless/case): condition expression and both paths
- Repeat/loop blocks: what they iterate over and bounds
- Error monitor blocks: scope, actions on error, stop vs resume behavior
- Dead branches: conditions that evaluate to the same value always (unreachable paths)

### DATA_FLOWS
- Every data mapping: source datapill -> target field with full formula (never truncate)
- Lookup table references: table name, match key, return field, fallback when no match
- Type conversions and format transformations: full formula shown
- Null / absent-field handling: default values, conditional checks

### TRANSFORMATION_DETAIL
- Field-by-field mapping tables per recipe step
- Formula catalog: every non-trivial formula with its purpose explained
- String, date, and numeric transformation patterns

### BUSINESS_RULES
- BR-{GROUP}-NNN for every conditional, formula, or routing rule that encodes business logic
- Full formula or condition expression -- never truncate
- Lookup table entries acting as business-rule tables: document all rows if available
- Parameter catalog: hardcoded values, static lists, date constants (table with location and risk)

### EXTERNAL_SYSTEMS
- Every connector and account: type, purpose, which recipes use it
- REST endpoint patterns (sanitize credentials with ***)
- Webhook sources and their triggering conditions

### ERROR_HANDLING
- Error handlers per recipe: scope, on-error action, stop / resume / custom
- Retry configuration per action
- Notification / alerting hooks
- Recipes with no error handling on critical paths -- flag each

### ENVIRONMENT_CONFIG
- Properties and their values (note environment-specific differences)
- Lookup tables acting as configuration stores
- Connector accounts that differ per environment

### RISK_FLAGS
CRITICAL / HIGH / MEDIUM / LOW with affected recipes and specific remediation:
- Hardcoded credentials in connector configs
- Unvalidated external input used in routing or output
- Recipes with no error handling
- Concurrency risks (recipe callable from multiple triggers simultaneously)
- Rate limit exposure on high-frequency triggers

WORKATO EXPORT CAVEATS (include in each section where applicable):
- This analysis is based on the JSON export, which may not reflect the live VQL (Visual Query Language) state.
  If the export is more than 30 days old relative to the scan date, flag affected recipes as [EXPORT_DRIFT_RISK].
- Recipe steps that appear unused may be called dynamically via callable recipe references that are not
  visible in the export. Flag such steps as [POSSIBLY_DYNAMIC] rather than removing them from analysis.
- CSRF token and export authentication quirks may cause recipe parameters to be absent or masked in the export.
  Do not conclude a field is empty if only its export representation is missing.
