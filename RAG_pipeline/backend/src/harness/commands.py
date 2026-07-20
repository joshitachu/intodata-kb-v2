"""Public slash-command discovery metadata.

Slash commands are presentation hints only. Their text is submitted unchanged
through the same authoritative agent loop as every other user message.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    argument_hint: str | None

    def public(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "description": self.description,
            "argument_hint": self.argument_hint,
        }


_COMMANDS = (
    SlashCommand(
        name="onboard",
        description="Create a project from Git or ZIP and select its documentation Pack.",
        argument_hint="project, source, platform, and Pack (optional)",
    ),
    SlashCommand(
        name="docs",
        description="Generate or refresh project documentation with a reusable Pack.",
        argument_hint="project and optional Pack",
    ),
    SlashCommand(
        name="investigate",
        description="Investigate a failure or operational incident.",
        argument_hint="failure, system, or time window",
    ),
    SlashCommand(
        name="pack",
        description="Create, browse, or assign reusable documentation Packs.",
        argument_hint="create, list, or use <Pack> for <project>",
    ),
    SlashCommand(
        name="status",
        description="Check the current documentation workflow status.",
        argument_hint="project name (optional)",
    ),
    SlashCommand(
        name="capabilities",
        description="Explain what Nexus can currently do.",
        argument_hint=None,
    ),
)


def list_slash_commands() -> tuple[SlashCommand, ...]:
    """Return discoverable command labels without routing behavior."""
    return _COMMANDS
