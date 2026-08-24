"""Deterministic completeness checks for authored plugin Pack workspaces."""

from __future__ import annotations

import re

from mcp_server.services.plugin_schema import PluginPackManifest

MAX_FILE_CHARS = 200_000
_PLACEHOLDER_RE = re.compile(
    r"\b(?:TODO|TBD|FIXME|CHANGEME)\b|"
    r"\[\s*(?:insert|fill in|your)\b[^\]]*\]|"
    r"<\s*(?:insert|fill|your|name|description)[^>]*>",
    re.IGNORECASE,
)


def required_generated_file_paths(
    manifest: PluginPackManifest, platform: str | None = None
) -> tuple[str, ...]:
    paths = ["README.md", "examples/output-style.md", "prompts/phase2.md", "prompts/phase3.md"]
    phases = manifest.phases
    if phases and phases.policy_for("investigation") == "run":
        paths.append("prompts/investigation.md")
    if phases and phases.policy_for("scorecard") == "run":
        paths.append("prompts/scorecard.md")
    if platform:
        paths.append(f"prompts/extensions/platforms/{platform}.md")
    paths.extend(f"prompts/docs/{doc.name}.md" for doc in manifest.docs or [])
    return tuple(paths)


def _mentions_requirement(content: str, requirement: str) -> bool:
    def words(value: str) -> set[str]:
        normalized: set[str] = set()
        for word in re.findall(r"[a-z0-9]+", value.lower()):
            if len(word) < 3:
                continue
            normalized.add(word[:-1] if len(word) > 4 and word.endswith("s") else word)
        return normalized

    required = words(requirement)
    return bool(required) and required.issubset(words(content))


def validate_generated_pack_files(
    manifest: PluginPackManifest,
    files: dict[str, str],
    *,
    platform: str | None = None,
) -> list[str]:
    """Validate a file set against its manifest without imposing count caps."""
    expected = set(required_generated_file_paths(manifest, platform))
    actual = set(files)
    errors: list[str] = []
    missing = sorted(expected - actual)
    if missing:
        errors.append("missing required file(s): " + ", ".join(missing))

    for path in sorted(actual & expected):
        content = files[path].strip()
        minimum = 220 if path.startswith("examples/") else 160
        if len(content) < minimum:
            errors.append(f"{path} is too thin ({len(content)} characters; need {minimum})")
        if len(content) > MAX_FILE_CHARS:
            errors.append(f"{path} exceeds {MAX_FILE_CHARS} characters")
        match = _PLACEHOLDER_RE.search(content)
        if match:
            errors.append(f"{path} contains placeholder text: {match.group(0)}")
        if "prompts/phase2_platform_sections" in path or path.startswith("prompts/templates/"):
            errors.append(f"{path} attempts to replace Nexus-owned prompt policy")

    phase2 = files.get("prompts/phase2.md", "").lower()
    readme = files.get("README.md", "").lower()
    example = files.get("examples/output-style.md", "").lower()
    for section_id in manifest.declared_section_ids:
        if section_id.lower() not in phase2:
            errors.append(f"prompts/phase2.md does not address section {section_id}")
        if section_id.lower() not in readme:
            errors.append(f"README.md does not explain analysis section {section_id}")
    phase3 = files.get("prompts/phase3.md", "").lower()
    for role in manifest.audience.roles if manifest.audience else []:
        if not _mentions_requirement(phase3, role):
            errors.append(f"prompts/phase3.md does not address audience role '{role}'")
    for doc in manifest.docs or []:
        doc_prompt_path = f"prompts/docs/{doc.name}.md"
        doc_prompt = files.get(doc_prompt_path, "").lower()
        if doc.name.lower() not in readme:
            errors.append(f"README.md does not explain document {doc.name}")
        for heading in doc.required_sections:
            if heading.lower() not in doc_prompt:
                errors.append(f"{doc_prompt_path} omits required heading '{heading}'")
            if heading.lower() not in example:
                errors.append(f"examples/output-style.md omits required heading '{heading}' for {doc.name}")
        for output in doc.required_outputs:
            if output.lower() not in doc_prompt:
                errors.append(f"{doc_prompt_path} omits required output '{output}'")
        for evidence in doc.required_evidence:
            if not _mentions_requirement(doc_prompt, evidence):
                errors.append(f"{doc_prompt_path} omits required evidence '{evidence}'")
    if platform:
        extension = files.get(f"prompts/extensions/platforms/{platform}.md", "").lower()
        if "nexus" not in extension or "additive" not in extension:
            errors.append(
                f"platform extension for {platform} must state that it is additive to Nexus policy"
            )
    return errors
