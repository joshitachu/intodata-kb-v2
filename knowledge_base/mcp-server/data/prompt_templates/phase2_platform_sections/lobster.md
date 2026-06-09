SECTION-BY-SECTION EXTRACTION REQUIREMENTS

### COMPONENTS
For EVERY profile and agent:
- Name, purpose, type (input/output/mapping/route)
- Parser and format definitions
- Forward/exception profile references

### EXECUTION_FLOW
- Profile execution chain in order
- Route conditions and targets
- Exception paths

### DATA_FLOWS
- Mapping definitions: source field -> transformation -> target field (full expressions)
- Format conversions: input format -> output format
- Conditional routing: which conditions route where

### TRANSFORMATION_DETAIL
- Field mapping tables per profile
- Transformation expressions in full

### BUSINESS_RULES
- BR-{GROUP}-NNN for every routing rule, filter, or transformation condition
- Full expression or condition -- never truncate
- Parameter catalog: hardcoded values table

### EXTERNAL_SYSTEMS
- All partner connections: type, protocol, direction (inbound/outbound)
- Sanitized host/port details

### ERROR_HANDLING
- Exception profiles per route
- Forward profiles on error
- Profiles with no exception handling -- flag each

### ENVIRONMENT_CONFIG
- Variables and environment-specific values
- Schedule and trigger configuration

### RISK_FLAGS
CRITICAL / HIGH / MEDIUM / LOW with specific profiles and remediation.
