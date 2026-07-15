from src.harness.commands import list_slash_commands, resolve_slash_command


def test_slash_commands_expose_only_discovery_metadata() -> None:
    commands = list_slash_commands()

    assert [command.name for command in commands] == [
        "onboard",
        "docs",
        "investigate",
        "pack",
        "status",
        "capabilities",
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


def test_pack_command_and_legacy_alias_seed_documentation_pack_management() -> None:
    current = resolve_slash_command("/pack create customer-api")
    legacy = resolve_slash_command("/packs customer-api")

    assert current is not None
    assert current.command.name == "pack"
    assert current.planner_query == "Manage the documentation Pack create customer-api"
    assert legacy is not None
    assert legacy.command.name == "pack"
    assert legacy.planner_query == "Manage the documentation Pack customer-api"


def test_onboarding_alias_seeds_the_onboard_command() -> None:
    resolved = resolve_slash_command("/onboarding")

    assert resolved is not None
    assert resolved.command.name == "onboard"


def test_capabilities_command_is_read_only_planner_input() -> None:
    resolved = resolve_slash_command("/capabilities")

    assert resolved is not None
    assert resolved.planner_query == (
        "Explain your currently available capabilities without executing tools"
    )
