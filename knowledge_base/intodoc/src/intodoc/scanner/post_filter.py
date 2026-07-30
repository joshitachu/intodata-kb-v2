"""Post-parse structural filter -- walks ScanResult and redacts by semantic context.

Layer 2 of the two-layer redaction strategy. Runs AFTER XML parsing on the
Pydantic models, using field semantics (what a value *means*) rather than
just pattern matching.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from intodoc.config import RedactionLevel
from intodoc.models import (
    ComponentParam,
    ContextGroup,
    ContextVariable,
    MetadataConnection,
    ParsedJob,
    ScanResult,
)
from intodoc.scanner.credential_filter import CredentialFilter


def filter_scan_result(
    result: ScanResult,
    cred_filter: CredentialFilter,
) -> ScanResult:
    """Walk a ScanResult and apply semantic redaction in-place. Returns result."""
    level = cred_filter.level

    # 1. Metadata connections
    for conn in result.metadata_connections:
        _filter_metadata_connection(conn, cred_filter, level)

    # 2. Top-level context groups
    for cg in result.context_groups:
        _filter_context_group(cg, cred_filter, level)

    # 3. Jobs
    for job in result.jobs:
        _filter_job(job, cred_filter, level)

    # URI descriptor markers are a private Layer-1-to-parser transport, not
    # a public redaction shape.  Most exact component parameters consume them
    # while parsing and attach a descriptor; this final recursive sweep covers
    # tMap/tXMLMap expressions, context values, metadata and any future model
    # field that carries a larger string around a marker.
    _normalize_internal_descriptor_tokens(result, cred_filter)
    return result


def _normalize_internal_descriptor_tokens(value: object, cred_filter: CredentialFilter) -> object:
    """Mutate a Pydantic/list/dict tree so no internal URI token can persist."""
    # Str-backed enums (for example FileType) satisfy isinstance(value, str).
    # Preserve their model type instead of turning them into plain strings.
    if isinstance(value, Enum):
        return value
    if isinstance(value, str):
        return cred_filter.normalize_redaction_tokens(value)
    if isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            setattr(
                value,
                field_name,
                _normalize_internal_descriptor_tokens(
                    getattr(value, field_name), cred_filter,
                ),
            )
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _normalize_internal_descriptor_tokens(item, cred_filter)
        return value
    if isinstance(value, dict):
        for key, item in list(value.items()):
            value[key] = _normalize_internal_descriptor_tokens(item, cred_filter)
        return value
    return value


def _filter_metadata_connection(
    conn: MetadataConnection,
    cred_filter: CredentialFilter,
    level: RedactionLevel,
) -> None:
    """Redact sensitive fields on a MetadataConnection."""
    # Host is always sensitive (contains server names / IPs)
    if conn.host:
        descriptor = cred_filter.describe_value(conn.host, is_host=True)
        cred_filter._record_redaction("connection_host", conn.host)
        conn.host = "[HOST_REDACTED]"
        if descriptor is not None:
            conn.host_redaction = descriptor

    # Port is fine -- it's structural, not identifying

    # Database and schema: strict+
    if level != RedactionLevel.STANDARD:
        if conn.database:
            cred_filter._record_redaction("connection_database", conn.database)
            conn.database = "[DATABASE_REDACTED]"
        if conn.schema_name:
            cred_filter._record_redaction("connection_schema", conn.schema_name)
            conn.schema_name = "[SCHEMA_REDACTED]"

    # File path in connection
    if conn.file_path:
        cred_filter._record_redaction("connection_file_path", conn.file_path)
        conn.file_path = "[FILE_PATH_REDACTED]"

    # Re-run text filter on additional_params values
    for key in list(conn.additional_params.keys()):
        val = conn.additional_params[key]
        if isinstance(val, str):
            filtered = cred_filter.filter_text(val)
            if filtered != val:
                descriptor = cred_filter.describe_value(val)
                conn.additional_params[key] = filtered
                if descriptor is not None:
                    conn.additional_params_redactions[key] = descriptor


def _filter_context_group(
    cg: ContextGroup,
    cred_filter: CredentialFilter,
    level: RedactionLevel,
) -> None:
    """Redact context variable values based on name semantics and level."""
    for var in cg.variables:
        _filter_context_variable(var, cred_filter, level)


def _filter_context_variable(
    var: ContextVariable,
    cred_filter: CredentialFilter,
    level: RedactionLevel,
) -> None:
    """Redact a single context variable's values."""
    # Paranoid: redact ALL values
    if level == RedactionLevel.PARANOID:
        for env in list(var.values.keys()):
            if var.values[env]:
                descriptor = cred_filter.describe_value(var.values[env])
                cred_filter._record_redaction("context_var_paranoid", var.values[env])
                var.values[env] = "[REDACTED]"
                if descriptor is not None:
                    var.redaction_descriptors[env] = descriptor
        return

    # id_Password type: always redact regardless of level
    if var.type and var.type.lower() in ("id_password", "password"):
        for env in list(var.values.keys()):
            if var.values[env]:
                descriptor = cred_filter.describe_value(var.values[env])
                cred_filter._record_redaction("context_var_password", var.values[env])
                var.values[env] = "[PASSWORD_REDACTED]"
                if descriptor is not None:
                    var.redaction_descriptors[env] = descriptor
        return

    # Check variable name for sensitivity
    if cred_filter.is_sensitive_var_name(var.name):
        for env in list(var.values.keys()):
            if var.values[env]:
                descriptor = cred_filter.describe_value(var.values[env])
                cred_filter._record_redaction("context_var_sensitive", var.values[env])
                var.values[env] = "[REDACTED]"
                if descriptor is not None:
                    var.redaction_descriptors[env] = descriptor
        return

    # Catch-all: re-run text filter on all values
    for env in list(var.values.keys()):
        val = var.values[env]
        if isinstance(val, str):
            filtered = cred_filter.filter_text(val)
            if filtered != val:
                descriptor = cred_filter.describe_value(val)
                var.values[env] = filtered
                if descriptor is not None:
                    var.redaction_descriptors[env] = descriptor


def _filter_job(
    job: ParsedJob,
    cred_filter: CredentialFilter,
    level: RedactionLevel,
) -> None:
    """Redact sensitive data within a parsed job."""
    # Job-level context groups
    for cg in job.context_groups:
        _filter_context_group(cg, cred_filter, level)

    # Component parameters: re-run text filter as catch-all
    for comp in job.components:
        _filter_component_params(comp.params, cred_filter)
        # Top-level code mirrors code-bearing params; must be redacted too
        if comp.code:
            filtered = cred_filter.filter_text(comp.code)
            if filtered != comp.code:
                comp.code = filtered

    # tRunJob context params
    for ref in job.trunjob_refs:
        _filter_component_params(ref.context_params, cred_filter)


def _filter_component_params(
    params: list[ComponentParam],
    cred_filter: CredentialFilter,
) -> None:
    """Redact component parameter values by name semantics then text patterns."""
    for param in params:
        if not param.value:
            continue
        if cred_filter.is_sensitive_var_name(param.name):
            descriptor = cred_filter.describe_value(param.value)
            cred_filter._record_redaction("component_param_sensitive", param.value)
            param.value = "[REDACTED]"
            if descriptor is not None:
                param.redaction_descriptor = descriptor
            continue
        original = param.value
        filtered = cred_filter.filter_text(original)
        if filtered != original:
            descriptor = cred_filter.describe_value(original)
            param.value = filtered
            if descriptor is not None:
                param.redaction_descriptor = descriptor
