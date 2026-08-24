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

    def render(
        self,
        template_name: str,
        slots: dict[str, str],
        pack_dir: Path | None = None,
    ) -> str:
        """Load template_name and substitute slots.

        Uses safe_substitute so unknown ${SLOT} tokens are left as-is
        (visible in output as ${SLOT_NAME}, easy to catch in tests).

        A pack may replace ANY template by mirroring its filename under
        ``{pack_dir}/prompts/templates/``. This is one generic lookup rather
        than a per-prompt override flag on purpose: before it, only the three
        preambles and the platform skeleton were reachable, so the bodies
        carrying most of the pipeline's instruction -- the enumeration
        obligation, the output-size contract, the redaction and fabrication
        rules -- could not be expressed in a pack at all. A pack claiming to
        hold "the documentation behaviour" while omitting them is misleading.

        ``prompts/templates/`` and not ``prompts/`` because the flat directory
        is already the preamble namespace, and ``scorecard.md`` exists in both
        roles -- a shared namespace would make one silently shadow the other.
        """
        if pack_dir is not None:
            override = pack_dir / "prompts" / "templates" / template_name
            if override.is_file():
                return Template(override.read_text(encoding="utf-8")).safe_substitute(slots)
        path = self._root / template_name
        raw = path.read_text(encoding="utf-8")
        return Template(raw).safe_substitute(slots)

    def render_platform_section_depth(self, platform: str) -> str:
        """Return Nexus-owned platform extraction requirements.

        This is deliberately not pack-aware. Platform semantics are part of
        the Nexus foundation contract: a plugin may add guidance through the
        extension namespace, but it cannot replace or suppress this text.
        """
        candidates = [platform.lower(), "generic"]
        for candidate in candidates:
            path = self._root / "phase2_platform_sections" / f"{candidate}.md"
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    @staticmethod
    def render_platform_extension(platform: str, pack_dir: Path | None) -> str:
        """Return additive plugin guidance for ``platform``.

        Generic guidance applies first, followed by the platform-specific
        addition. Neither path shadows the Nexus-owned baseline.
        """
        if pack_dir is None:
            return ""
        root = pack_dir / "prompts" / "extensions" / "platforms"
        names = ["generic"]
        normalized = platform.lower()
        if normalized != "generic":
            names.append(normalized)
        parts = [
            path.read_text(encoding="utf-8").strip()
            for name in names
            if (path := root / f"{name}.md").is_file()
        ]
        return "\n\n".join(part for part in parts if part)


_renderer: PromptRenderer | None = None


def get_renderer(template_root: Path | None = None) -> PromptRenderer:
    """Return the module-level PromptRenderer singleton."""
    global _renderer
    if _renderer is None:
        _renderer = PromptRenderer(template_root)
    return _renderer
