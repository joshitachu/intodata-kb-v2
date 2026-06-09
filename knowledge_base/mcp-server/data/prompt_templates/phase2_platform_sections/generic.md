SECTION-BY-SECTION EXTRACTION REQUIREMENTS

### COMPONENTS
- Unit name, purpose, type (module / service / script / config)
- All dependencies: imports, called modules, external APIs
- Entry points and exposed interfaces

### EXECUTION_FLOW
- Call chains and execution order
- Every conditional branch with condition expression
- Loops and iteration patterns
- Error propagation paths

### DATA_FLOWS
- Input -> transformation -> output for every data-handling unit
- Full transformation expressions / code -- never truncate
- Type conversions, null handling, validation present or absent

### TRANSFORMATION_DETAIL
- Field-by-field mapping per unit with full expressions

### BUSINESS_RULES
- BR-{GROUP}-NNN for every conditional, calculation, or filter (full expression)
- Parameter catalog: hardcoded value | location | purpose | staleness risk

### EXTERNAL_SYSTEMS
- All APIs, databases, file systems, queues accessed
- Auth method and connection mode per system
- Which units use each system and for what purpose

### ERROR_HANDLING
- Rate each unit: GOOD / ADEQUATE / POOR / NONE
- Error propagation paths, retry logic
- Silent failures: errors swallowed without logging -- list each

### ENVIRONMENT_CONFIG
- Config files, env vars, feature flags and their values
- Environment-specific differences

### RISK_FLAGS
CRITICAL / HIGH / MEDIUM / LOW with specific unit references and remediation.
