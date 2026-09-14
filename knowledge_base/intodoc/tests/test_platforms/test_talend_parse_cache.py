from __future__ import annotations

from pathlib import Path

from intodoc.config import RedactionLevel
from intodoc.platforms.talend.parse_cache import design_cache_key
from intodoc.platforms.talend.parser import TalendParser
from intodoc.scanner.credential_filter import CredentialFilter


_XMLNS = (
    'xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" '
    'xmlns:TalendMapper="http://www.talend.org/mapper" '
    'xmlns:xmi="http://www.omg.org/XMI" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


def _write_job(
    project: Path, group: str, name: str, *, property_id: str, body: str = "",
) -> tuple[Path, Path]:
    directory = project / "process" / group
    directory.mkdir(parents=True, exist_ok=True)
    item = directory / f"{name}_0.1.item"
    item.write_text(f'<talendfile:ProcessType {_XMLNS}>{body}</talendfile:ProcessType>', encoding="utf-8")
    properties = item.with_suffix(".properties")
    properties.write_text(
        '<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Property id="{property_id}" label="{name}" version="0.1"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )
    return item, properties


def _run(root: Path, cache: Path, level: RedactionLevel = RedactionLevel.STANDARD):
    return TalendParser(CredentialFilter(level=level), parse_cache_dir=cache).parse(root)


def _run_with_filter(root: Path, cache: Path):
    credential_filter = CredentialFilter()
    result = TalendParser(credential_filter, parse_cache_dir=cache).parse(root)
    return result, credential_filter


def test_key_uses_both_files_parser_and_redaction_but_not_path(tmp_path: Path) -> None:
    first = tmp_path / "one" / "Job_0.1.item"
    first.parent.mkdir()
    first.write_text("<job/>", encoding="utf-8")
    first.with_suffix(".properties").write_text("<property version='1'/>", encoding="utf-8")
    second = tmp_path / "two" / "Renamed_0.1.item"
    second.parent.mkdir()
    second.write_bytes(first.read_bytes())
    second.with_suffix(".properties").write_bytes(first.with_suffix(".properties").read_bytes())

    standard = CredentialFilter().cache_fingerprint
    key = design_cache_key(first, redaction_fingerprint=standard, resolution_fingerprint="same")
    assert key == design_cache_key(second, redaction_fingerprint=standard, resolution_fingerprint="same")

    second.with_suffix(".properties").write_text("<property version='2'/>", encoding="utf-8")
    assert key != design_cache_key(second, redaction_fingerprint=standard, resolution_fingerprint="same")
    assert key != design_cache_key(first, redaction_fingerprint=CredentialFilter(
        level=RedactionLevel.STRICT,
    ).cache_fingerprint, resolution_fingerprint="same")
    assert key != design_cache_key(
        first, redaction_fingerprint=standard,
        resolution_fingerprint="same", parser_version="next",
    )

    missing_sidecar = tmp_path / "missing" / "Job_0.1.item"
    missing_sidecar.parent.mkdir()
    missing_sidecar.write_bytes(first.read_bytes())
    literal_marker = tmp_path / "marker" / "Job_0.1.item"
    literal_marker.parent.mkdir()
    literal_marker.write_bytes(first.read_bytes())
    literal_marker.with_suffix(".properties").write_text("<missing>", encoding="utf-8")
    assert design_cache_key(
        missing_sidecar, redaction_fingerprint=standard, resolution_fingerprint="same",
    ) != design_cache_key(
        literal_marker, redaction_fingerprint=standard, resolution_fingerprint="same",
    )


def test_cache_hits_after_rename_rewrites_paths_and_replays_warnings(tmp_path: Path) -> None:
    project = tmp_path / "PROJECT"
    _write_job(project, "Targets", "Target", property_id="_target")
    caller_body = (
        '<node componentName="tRunJob">'
        '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tRunJob_1"/>'
        '<elementParameter field="PROCESS_TYPE" name="PROCESS" value="OldTarget"/>'
        '<elementParameter field="TEXT" name="PROCESS:PROCESS_TYPE_PROCESS" value="_target"/>'
        '<elementParameter field="PASSWORD" name="id_Password" value="secret-value"/>'
        "</node>"
    )
    caller, caller_props = _write_job(
        project, "OldGroup", "Caller", property_id="_caller", body=caller_body,
    )
    cache = tmp_path / "cache"

    first, first_filter = _run_with_filter(tmp_path, cache)
    assert first.stats["parse_cache_hits"] == 0
    assert first.stats["parse_cache_misses"] == 2
    assert any(warning.kind == "stale_label" for warning in first.warnings)

    new_dir = project / "process" / "NewGroup"
    new_dir.mkdir()
    new_caller = new_dir / caller.name
    new_props = new_dir / caller_props.name
    caller.rename(new_caller)
    caller_props.rename(new_props)
    second, second_filter = _run_with_filter(tmp_path, cache)

    assert second.stats["parse_cache_hits"] == 2
    parsed = next(job for job in second.jobs if job.name == "Caller")
    assert parsed.path == "NewGroup/Caller_0.1.item"
    assert parsed.group == "NewGroup"
    warning = next(warning for warning in second.warnings if warning.kind == "stale_label")
    assert warning.file == "PROJECT/process/NewGroup/Caller_0.1.item"
    assert "OldGroup/Caller_0.1.item" not in warning.message
    assert second_filter.get_audit_report() == first_filter.get_audit_report()


def test_corrupt_entry_is_clean_observable_miss_and_is_replaced(tmp_path: Path) -> None:
    project = tmp_path / "PROJECT"
    _write_job(project, "Group", "Only", property_id="_only")
    cache = tmp_path / "cache"
    _run(tmp_path, cache)
    entry = next(cache.glob("*.json"))
    entry.write_text("{not-json", encoding="utf-8")

    result = _run(tmp_path, cache)

    assert result.stats["parse_cache_hits"] == 0
    assert result.stats["parse_cache_misses"] == 1
    assert result.stats["parse_cache_invalid_entries"] == 1
    assert result.stats["parse_cache_writes"] == 1
    assert not list(cache.glob("*.tmp"))


def test_cache_round_trip_preserves_descriptor_aware_uri_redaction(tmp_path: Path) -> None:
    project = tmp_path / "PROJECT"
    body = (
        '<node componentName="tMap"><nodeData xsi:type="TalendMapper:MapperData">'
        '<outputTables name="out"><mapperTableEntries name="endpoint" '
        'expression="&quot;jdbc:sqlserver://user:secret@example.invalid/db&quot;"/>'
        "</outputTables></nodeData></node>"
    )
    _write_job(project, "Group", "Mapping", property_id="_mapping", body=body)
    cache = tmp_path / "cache"

    first = _run(tmp_path, cache)
    second = _run(tmp_path, cache)

    assert first.stats["parse_cache_misses"] == 1
    assert second.stats["parse_cache_hits"] == 1
    first_expression = first.jobs[0].tmap_data[0].entries[0].expression
    second_expression = second.jobs[0].tmap_data[0].entries[0].expression
    assert first_expression == second_expression == '"[CONNECTION_STRING_REDACTED]"'
    assert "secret" not in second.model_dump_json()


def test_unrelated_design_addition_does_not_invalidate_cached_designs(tmp_path: Path) -> None:
    project = tmp_path / "PROJECT"
    _write_job(project, "First", "One", property_id="_one")
    _write_job(project, "Second", "Two", property_id="_two")
    cache = tmp_path / "cache"

    first = _run(tmp_path, cache)
    _write_job(project, "Third", "Three", property_id="_three")
    second = _run(tmp_path, cache)

    assert first.stats["parse_cache_misses"] == 2
    assert second.stats["parse_cache_hits"] == 2
    assert second.stats["parse_cache_misses"] == 1
