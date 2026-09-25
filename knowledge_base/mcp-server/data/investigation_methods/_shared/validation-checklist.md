---
applies to: all platforms
artifact format: method - not tied to an input artifact format
verified against: 1 engagement end-to-end
evidence class: literature-backed method; ceiling and benchmark values are inherited priors, flagged in place
last verified: 2026-07-25
---

# Integration Validation Checklist

Unified validation framework for integration designs and improvement roadmaps. Section A is run by the designer before delivery. Section B is run independently as peer review.

## Section A: Pre-Delivery Validation

Validate before delivering a design document or improvement roadmap.

### A1. Completeness

- [ ] All 8 dimensions addressed in design
- [ ] Requirements from assessment covered
- [ ] NFRs defined (latency, throughput, availability, security)
- [ ] Error scenarios documented (transient, permanent, timeout, auth failure)
- [ ] Testing strategy included (unit, integration, E2E, performance)
- [ ] Migration/deployment plan present
- [ ] Success criteria defined with measurable targets
- [ ] Risks identified with mitigation strategies

### A2. Architecture

- [ ] Pattern selection justified with rationale
- [ ] Alternatives considered and documented
- [ ] Coupling level appropriate (loose preferred)
- [ ] Sync/async choice matches requirements
- [ ] No anti-patterns present (spaghetti, shared DB, chatty interface)

### A3. Data Design

- [ ] Schemas defined (source, target, canonical)
- [ ] Mapping specifications complete (field-level)
- [ ] Transformation logic documented
- [ ] Schema versioning strategy defined
- [ ] Validation rules specified (pre-send, post-receive)

### A4. Error Handling

- [ ] Retry strategy defined (backoff, max attempts, timeout)
- [ ] Circuit breaker configured for external dependencies
- [ ] Dead letter queue specified (when to DLQ, monitoring, reprocessing)
- [ ] Compensation/rollback logic for multi-step operations
- [ ] Alert rules defined (who, when, how)

### A5. Security

- [ ] Authentication mechanism specified (OAuth 2.0 preferred)
- [ ] Encryption in transit (TLS 1.2+)
- [ ] Secrets management defined (vault, never hardcoded)
- [ ] Audit trail specified
- [ ] PII handling documented (if applicable)
- [ ] No credentials in code, config, or documentation

### A6. Monitoring

- [ ] Correlation IDs planned
- [ ] Metrics defined (rate, errors, duration at minimum)
- [ ] Logging strategy specified (levels, format, what to log)
- [ ] Dashboards identified
- [ ] SLOs defined

### A7. Pragmatism

- [ ] Design matches team's skill level
- [ ] Platform constraints considered
- [ ] Budget/timeline constraints reflected
- [ ] Phased approach if full solution is complex
- [ ] Trade-offs documented explicitly

---

## Section B: Peer Review Validation

Run independently to validate designs and improvement roadmaps against best practices.

### B1. Completeness Check

- [ ] All 8 dimensions addressed
- [ ] Requirements from assessment covered
- [ ] NFRs defined (latency, throughput, availability)
- [ ] Error scenarios documented (transient, permanent, timeout)
- [ ] Testing strategy included (unit, integration, E2E)
- [ ] Migration/deployment plan present
- [ ] Success criteria measurable
- [ ] Risks identified with mitigations

### B2. Pattern Validation

- [ ] Pattern choice appropriate for requirements (volume, latency, coupling needs)
- [ ] Rationale for pattern selection documented
- [ ] Alternatives considered
- [ ] Coupling level acceptable (loose preferred)
- [ ] Sync vs async correct for use case
- [ ] API maturity level appropriate
- [ ] No anti-patterns present:
  - [ ] No point-to-point spaghetti
  - [ ] No shared database integration
  - [ ] No synchronous chaining
  - [ ] No chatty interfaces
  - [ ] No distributed monolith

### B3. Data Design Validation

- [ ] Schemas defined and version-controlled
- [ ] Canonical model used (or justified why not)
- [ ] Field mappings complete and documented
- [ ] Transformation logic clear and testable
- [ ] Validation rules specified (pre-send, post-receive)
- [ ] Data quality monitoring planned
- [ ] Schema evolution strategy defined

### B4. Error Handling Validation

- [ ] Retry logic defined with exponential backoff
- [ ] Max retry attempts specified
- [ ] Timeout values configured per operation
- [ ] Circuit breaker for external dependencies
- [ ] Dead letter queue configured
- [ ] DLQ monitoring and reprocessing strategy
- [ ] Compensation logic for multi-step operations
- [ ] Error classification (transient vs permanent)

### B5. Monitoring Validation

- [ ] Correlation IDs implemented
- [ ] Logging strategy defined (structured, levels, what to log)
- [ ] Metrics defined (RED: rate, errors, duration)
- [ ] Dashboards specified (operations, business, health)
- [ ] Alerting rules defined (thresholds, recipients, escalation)
- [ ] SLOs defined with error budgets
- [ ] Runbook/playbook referenced or planned

### B6. Reliability Validation

- [ ] Idempotency ensured (safe to retry)
- [ ] Delivery semantics defined (at-least-once, at-most-once, exactly-once)
- [ ] Backpressure handling (rate limiting, load shedding)
- [ ] Graceful degradation strategy
- [ ] DR plan defined (RTO/RPO)
- [ ] Failure modes analyzed
- [ ] Rollback strategy documented

### B7. Maintainability Validation

- [ ] Testing pyramid defined (unit >80%, integration, E2E)
- [ ] CI/CD pipeline planned
- [ ] Deployment automation
- [ ] Rollback capability
- [ ] Architecture documentation planned
- [ ] Runbooks for common operations
- [ ] Knowledge transfer plan

### B8. Platform Validation

- [ ] Platform choice justified
- [ ] Platform supports all required patterns
- [ ] Lock-in risk assessed
- [ ] TCO estimated
- [ ] Team skills sufficient (or training planned)
- [ ] Migration path identified (if platform change needed)

### B9. Data Collection Safety

- [ ] Log redaction is customer-configurable before collecting runtime logs (built-in field removal + configurable regex + customer-specific patterns)
- [ ] No tenant IDs, API endpoints, or credentials exposed in assessment artifacts

---

## Finding Severity Classification

| Severity | Definition | Action |
|----------|-----------|--------|
| **Critical** | Security vulnerability, data loss risk, SPOF with no DR | Must fix before implementation |
| **High** | Missing resilience pattern, inadequate monitoring, no retry | Should fix before implementation |
| **Medium** | Suboptimal pattern, documentation gap, optimization opportunity | Improve after MVP |
| **Low** | Enhancement, additional testing, nice-to-have | Backlog |

## Approval Recommendations

| Status | Criteria | Action |
|--------|----------|--------|
| **Approved** | No critical or high findings. Design is implementation-ready. | Proceed to implementation |
| **Approved with Conditions** | Has high findings that must be resolved. | Can start implementation in parallel with fixes |
| **Revisions Required** | Has critical findings. | Must re-design and re-review before implementation |
