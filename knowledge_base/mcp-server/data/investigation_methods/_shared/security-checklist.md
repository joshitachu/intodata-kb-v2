# Security Assessment Checklist

Structured security review for integration codebases. Evaluate each category and report findings.

## 1. Credential Management

| Check | Pass/Fail | Evidence |
|-------|-----------|----------|
| No hardcoded passwords in job definitions | | |
| No hardcoded API keys or tokens | | |
| No credentials in context variable default values | | |
| Credentials externalized via environment variables or vault | | |
| Different credentials per environment (dev/staging/prod) | | |
| Service accounts use least-privilege principle | | |
| Password complexity meets policy (if visible) | | |

**How to check**: Examine context variables, connection parameters, component configurations for plain-text credential values. Look for variables named `*password*`, `*secret*`, `*api_key*`, `*token*`, `*credential*`.

## 2. Data Protection

| Check | Pass/Fail | Evidence |
|-------|-----------|----------|
| PII fields encrypted at rest | | |
| PII fields encrypted in transit | | |
| Data masking applied for non-production environments | | |
| Sensitive fields not written to log files | | |
| Temporary files containing sensitive data are cleaned up | | |
| File permissions restrict access to data files | | |

**How to check**: Trace PII-candidate fields (SSN, DOB, email, phone, salary, medical) through data flows. Check if encryption/masking is applied before storage or transmission.

## 3. Access Control

| Check | Pass/Fail | Evidence |
|-------|-----------|----------|
| Database connections use role-based accounts (not root/admin) | | |
| API calls include authentication (OAuth, API key, certificate) | | |
| File system access uses appropriate permissions | | |
| Different access levels for different job types (read vs write) | | |
| No shared credentials across teams/regions | | |

**How to check**: Review connection definitions, context groups, and API configurations for authentication mechanisms and privilege levels.

## 4. Input Validation

| Check | Pass/Fail | Evidence |
|-------|-----------|----------|
| External file inputs validated against expected schema | | |
| API responses validated before processing | | |
| SQL inputs parameterized (no string concatenation) | | |
| File paths sanitized (no path traversal) | | |
| Data types validated at ingestion boundary | | |

**How to check**: Look at first components after data ingestion (tFileInput, tRESTClient, tDBInput). Check if schema validation, type checking, or sanitization occurs before data enters the processing pipeline.

## 5. Audit & Compliance

| Check | Pass/Fail | Evidence |
|-------|-----------|----------|
| Data modifications logged with timestamp and actor | | |
| Sensitive data access logged | | |
| Job execution history preserved | | |
| Error events captured with full context | | |
| Data retention policies enforced | | |
| DELETE operations logged before execution | | |

**How to check**: Look for audit table writes, logging components on data modification paths, archive/purge jobs, retention-related scheduling.

## 6. Network & Transport

| Check | Pass/Fail | Evidence |
|-------|-----------|----------|
| Database connections use SSL/TLS | | |
| File transfers use SFTP/FTPS (not plain FTP) | | |
| API calls use HTTPS (not HTTP) | | |
| No sensitive data in URL parameters | | |
| Connection timeouts configured | | |

**How to check**: Review connection strings for SSL parameters, FTP/SFTP component configurations, REST client URLs (http:// vs https://), timeout settings.

## 7. Error Handling Security

| Check | Pass/Fail | Evidence |
|-------|-----------|----------|
| Error messages don't expose sensitive data (credentials, PII, internal paths) | | |
| Stack traces not written to user-facing outputs | | |
| Failed authentication attempts logged | | |
| Error logs don't contain full query strings with data values | | |

**How to check**: Examine tLogCatcher output expressions, error routing paths, and error message formatting for information leakage.

## Summary Format

```markdown
## Security Assessment Summary

**Overall Security Posture**: CRITICAL / WEAK / MODERATE / STRONG / EXCELLENT

### Findings by Category
| Category | Status | Critical Issues |
|----------|--------|-----------------|
| Credential Management | PASS/FAIL/PARTIAL | Count of issues |
| Data Protection | PASS/FAIL/PARTIAL | Count of issues |
| Access Control | PASS/FAIL/PARTIAL | Count of issues |
| Input Validation | PASS/FAIL/PARTIAL | Count of issues |
| Audit & Compliance | PASS/FAIL/PARTIAL | Count of issues |
| Network & Transport | PASS/FAIL/PARTIAL | Count of issues |
| Error Handling Security | PASS/FAIL/PARTIAL | Count of issues |

### Critical Findings (must fix)
1. [Finding with job name and specific location]

### High Priority Findings (should fix)
1. [Finding with job name and specific location]

### Recommendations
1. [Specific actionable recommendation]
```
