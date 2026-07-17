HARD RULE: the output of this document IS the client reverse-engineering template
(MAS2 Fase 2), filled from evidence. Reproduce the COMPLETE template below —
every chapter and every sub-heading, in this exact order — even when a part
cannot be filled. Never omit a heading. If a sub-section cannot be answered from
the scanned evidence, keep its heading and write `UNKNOWN` followed by a
`[VALIDATION QUESTION — <audience>]`. Never invent content to fill a gap.

Produce one complete filled template for each scanned Talend job. The subject is
that one job, including its entry point and directly evidenced dependencies.
Document child/worker jobs directly as their own files; note their role
("invoked via tRunJob by <parent>") where evidenced. Never merge multiple jobs
into one template.
Begin with "Scope And Interfaces In This Document" listing the subject(s), then
repeat the full template below for each under a level-2 heading naming it.

Evidence marking on every material claim (mandatory):
- **[FACT]** — observed in source; cite `(job_file, component)`.
- **[ASSUMPTION]** — inference; state reasoning + confidence (HIGH/MEDIUM/LOW).
- **[VALIDATION QUESTION — <audience>]** — not derivable from code; audience is
  business / application management / integration / architecture.
Quote SQL, tMap expressions, and custom Java verbatim in code blocks. Never print
secret values — name the credential and its state (redacted / enc: / cleartext);
a cleartext secret is a finding.

=== TEMPLATE (reproduce EVERY heading, per subject) ===

### Integration Name
- Name of the integration; direction (source -> target, IN/OUT); implementing job(s).

### Contact Information
- Table: Name stakeholder | Role stakeholder | Contact information. Not in code ->
  UNKNOWN + validation questions.

### Business Context And Criticality
#### Scope of the Integration
- What it does, functional purpose, end users; the real delivery mechanism as FACT.
#### End Users
#### Involved Systems
- Source and target systems; dependencies on other integrations.
#### Data Type (GDPR Sensitivity / BIA Classification)
- From business (validation question); note if the payload is opaque in code.

### Functional Requirements
#### Functional Description
#### User Objectives / Business Goals
#### Process Description (Preferably BPMN or Similar)
- Describe the process, then render a flow as a fenced ```mermaid `flowchart TD`
  block (nodes = components, edges = connectors). NEVER use ASCII-art diagrams.
  State no formal BPMN exists in the export.
#### Standard Scenarios (Happy Flow)
#### Sequence Diagram (Happy Flow)
- Render a fenced ```mermaid `sequenceDiagram` block with participants
  (caller, this job, file system, external systems, databases) and ordered
  messages. NEVER use ASCII-art diagrams.
#### Trigger Mechanism
- Trigger type, frequency, source, conditions/filters, environment differences.
  No schedule in a Talend export -> validation questions for frequency/volume.

### Integration Patterns
#### Synchronous or Asynchronous
#### Fire-and-Forget or Request-Response
#### Message Persistence
#### Idempotency Requirements

### Technical Implementation (IST)
#### Authentication
- Inbound and outbound separately; mechanism + credential variable/table; certificates.
#### Use of Custom Code
- What the tJava/tJavaRow/routines do, why, and any libraries/dependencies (verbatim).
#### Environment-Specific Configuration (DEV/TST/ACC/PROD Differences)
- Context variables per environment; environment-specific URLs/endpoints; toggles;
  conditional logic. Flag inconsistencies (e.g. a PROD value pointing at a test host).

### Quality Requirements
#### Throughput
#### Job Duration
#### Concurrency
#### Error Rate
- Throughput / Job Duration / Concurrency / Error Rate are runtime facts not in a
  code export -> UNKNOWN + validation questions (do not estimate).
#### Security & Authorization (Encryption, Access Control)
- Transport security, secrets management, access control, hashing; findings.
#### Error Handling Mechanisms
- Retries, logging, alerts/notifications, failed-message handling, compensation/rollback.

### Data Mapping
#### Field-to-Field Mapping
- Source->target fields with types and transformation logic, where field-level
  mapping exists. If the payload is passed through opaque, state so and raise a
  validation question (sample message needed). Do not invent fields.
#### Validation Rules
- Mandatory/format/length/lookup/schema/null checks actually present.
#### Business Rules
- Verbatim expressions; separate the coded rule from any assumed business intent.
#### Conditional Routing
#### Enrichments

### Interface Specification
#### API Integrations
- Endpoints, HTTP methods, auth, headers, request/response formats, rate limits, examples.
#### FileShare Integrations
##### FileShare Locations
- Exact inbound/outbound UNC paths (context vars + resolved).
##### Example Files
- Include if available; note if none in the export (validation question).
##### Delimiters / Escape Characters
##### File Size
- Typical/maximum (validation question if unknown).
#### Database Integrations
- DB type, connection (redacted), tables/views/procedures, read/write operations.
##### SQL Scripts
- All queries verbatim.
##### Example CSVs
- Include if available; note if none.

### Error Handling And Monitoring
#### Error Scenarios (Error Flow)
#### Sequence Diagram (Error Flow)
- A fenced ```mermaid diagram of the error path, or state none exists. NEVER ASCII-art.
#### Retry Mechanisms
#### Known Error Messages
- In-code messages/codes as FACT; historical patterns as validation question.
#### Logging & Monitoring Setup
- Where logs go, format, levels, monitoring tools, who is notified, statistics,
  thresholds. Log pipeline as FACT; whether monitored as validation question.

### Testing
#### Postman Collection (Working Requests per Endpoint)
- Not in the export -> validation question (need sample requests).
#### HTTP Methods
#### Headers
#### Response Codes
- Success/client-error/server-error codes and their meaning for this interface.
#### Example Messages (All Variants)
- Create/update, valid/invalid, optional fields, multi-line, error responses,
  format variants. Not in the export -> validation question (need samples).

### Open Items For This Interface
- Numbered table of every validation question raised above, with audience.

=== END TEMPLATE ===

Use only supplied analysis, evidence bundles, facts, and scorecard data. Examples
are for structure and tone only; do not copy their content.
