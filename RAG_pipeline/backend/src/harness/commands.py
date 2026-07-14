"""Public slash-command discovery and safe planner seeding.

Commands are presentation shortcuts only.  Resolving one produces ordinary
planner input and never selects or invokes a capability on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    argument_hint: str | None
    planner_template: str

    def public(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "description": self.description,
            "argument_hint": self.argument_hint,
        }

    def seed(self, arguments: str) -> str:
        context = arguments.strip()
        return self.planner_template.format(arguments=context).strip()


@dataclass(frozen=True)
class ResolvedSlashCommand:
    command: SlashCommand
    arguments: str
    planner_query: str


_COMMANDS = (
    SlashCommand(
        name="onboard",
        description="Create a project and start source onboarding.",
        argument_hint="project name and optional Git URL",
        planner_template="Onboard a project {arguments}",
    ),
    SlashCommand(
        name="docs",
        description="Generate or refresh project documentation.",
        argument_hint="project name",
        planner_template="Generate documentation {arguments}",
    ),
    SlashCommand(
        name="investigate",
        description="Investigate a failure or operational incident.",
        argument_hint="failure, system, or time window",
        planner_template="Investigate an operational failure {arguments}",
    ),
    SlashCommand(
        name="packs",
        description="Review project packs available for the current scope.",
        argument_hint="project name (optional)",
        planner_template="Show available project packs {arguments}",
    ),
    SlashCommand(
        name="status",
        description="Check the current documentation workflow status.",
        argument_hint="project name (optional)",
        planner_template="Show documentation generation status {arguments}",
    ),
)
_BY_NAME = {command.name: command for command in _COMMANDS}
_COMMAND_RE = re.compile(r"^/(?P<name>[a-z][a-z0-9_-]*)(?:\s+(?P<arguments>.*))?$", re.IGNORECASE)


def list_slash_commands() -> tuple[SlashCommand, ...]:
    """Return discoverable commands without internal planner templates."""
    return _COMMANDS


def resolve_slash_command(query: str) -> ResolvedSlashCommand | None:
    """Resolve a known slash command into normal planner input.

    Unknown slash input is intentionally returned as ``None`` so it follows
    the same normal chat path as any other message rather than becoming a
    special routing surface.
    """
    match = _COMMAND_RE.fullmatch(query.strip())
    if match is None:
        return None
    command = _BY_NAME.get(match.group("name").lower())
    if command is None:
        return None
    arguments = (match.group("arguments") or "").strip()
    return ResolvedSlashCommand(
        command=command,
        arguments=arguments,
        planner_query=command.seed(arguments),
    )
