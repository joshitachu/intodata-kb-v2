"""PromptRenderer -- loads .md template files and renders them with string.Template slots."""
from __future__ import annotations

from pathlib import Path
from string import Template

def _resolve_template_root() -> Path:
    src_layout = Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "prompt_templates"
    if src_layout.exists():
        return src_layout
    app_data = Path("/app/data/prompt_templates")
    if app_data.exists():
        return app_data
    cwd_data = Path.cwd() / "data" / "prompt_templates"
    if cwd_data.exists():
        return cwd_data
    raise FileNotFoundError(
        f"prompt_templates/ not found at {src_layout}, {app_data}, or {cwd_data}"
    )


_TEMPLATE_ROOT = _resolve_template_root()


class PromptRenderer:
    """Load and render prompt templates from data/prompt_templates/."""

    def __init__(self, template_root: Path | None = None) -> None:
        self._root = template_root or _TEMPLATE_ROOT

    def render(self, template_name: str, slots: dict[str, str]) -> str:
        """Load template_name and substitute slots.

        Uses safe_substitute so unknown ${SLOT} tokens are left as-is
        (visible in output as ${SLOT_NAME}, easy to catch in tests).
        """
        path = self._root / template_name
        raw = path.read_text(encoding="utf-8")
        return Template(raw).safe_substitute(slots)

    def render_platform_section_depth(self, platform: str) -> str:
        """Return the platform-specific Phase 2 section extraction requirements."""
        candidates = [platform.lower(), "generic"]
        for candidate in candidates:
            path = self._root / "phase2_platform_sections" / f"{candidate}.md"
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""


_renderer: PromptRenderer | None = None


def get_renderer(template_root: Path | None = None) -> PromptRenderer:
    """Return the module-level PromptRenderer singleton."""
    global _renderer
    if _renderer is None:
        _renderer = PromptRenderer(template_root)
    return _renderer
