from src.harness.commands import list_slash_commands, resolve_slash_command


def test_slash_commands_expose_only_discovery_metadata() -> None:
    commands = list_slash_commands()

    assert [command.name for command in commands] == [
        "onboard",
        "docs",
        "investigate",
        "packs",
        "status",
    ]
    assert all("planner_template" not in command.public() for command in commands)


def test_resolved_command_seeds_normal_planner_input() -> None:
    resolved = resolve_slash_command("/docs customer-api")

    assert resolved is not None
    assert resolved.command.name == "docs"
    assert resolved.arguments == "customer-api"
    assert resolved.planner_query == "Generate documentation customer-api"


def test_unknown_slash_input_remains_a_normal_chat_message() -> None:
    assert resolve_slash_command("/not-a-command something") is None
