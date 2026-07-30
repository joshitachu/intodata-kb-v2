"""Render Nexus semantic prompts only; runtime envelopes are deliberately absent."""

from __future__ import annotations
import re
import stat
from pathlib import Path


def resolve_text(reference: str, *, recipe_root: Path, shared_roots: tuple[Path, ...] = ()) -> str:
    candidate = Path(reference)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("recipe asset reference must be contained")
    for root in (recipe_root, *shared_roots):
        try:
            resolved_root = root.resolve(strict=True)
            unresolved = root / candidate
            relative_parts = unresolved.relative_to(root).parts
            cursor = root
            for part in relative_parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ValueError("recipe asset must not be a symlink")
            path = unresolved.resolve(strict=True)
            path.relative_to(resolved_root)
            mode = path.stat().st_mode
        except (OSError, ValueError):
            continue
        if stat.S_ISREG(mode) and path.stat().st_size <= 1024 * 1024:
            try:
                return path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("recipe asset must be UTF-8") from exc
    raise ValueError("recipe asset reference is not approved")


def render_semantic_prompt(template: str, *, output_id: str | None = None) -> str:
    placeholders = set(re.findall(r"\{([^{}]+)\}", template))
    if placeholders - {"output_id"}:
        raise ValueError("semantic prompt contains an unsupported placeholder")
    rendered = template.replace("{output_id}", output_id or "")
    if "{" in rendered or "}" in rendered:
        raise ValueError("semantic prompt contains an unresolved placeholder")
    return rendered
