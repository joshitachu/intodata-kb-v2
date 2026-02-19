# ADR-002: Credential Filter Before Parsing

**Date:** 2026-02-16
**Status:** Accepted

## Context
Integration projects contain sensitive data: database passwords, API keys, JDBC connection strings, certificates, and IP addresses. This data passes through the LLM for analysis.

## Decision
Run the credential filter on raw file text BEFORE any parsing or LLM processing.

## Rationale
- **Defense in depth:** Filtering raw text catches credentials regardless of file format or parser behavior. Even if the parser has bugs, credentials never reach the LLM.
- **Seven regex patterns** cover: passwords, API keys, JDBC URLs, certificates, IP addresses, tokens, and secrets.
- **No false negatives on known patterns:** Better to over-redact than leak credentials to an external API.

## Consequences
- Credential filter runs on every file before platform parsing begins
- Redacted values are replaced with `[REDACTED]` markers
- Platform parsers work on pre-filtered text — they never see raw credentials
- 7/7 credential filter unit tests validate all patterns
