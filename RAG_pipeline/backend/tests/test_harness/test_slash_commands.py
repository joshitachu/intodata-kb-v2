from src.harness.commands import list_slash_commands


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
    assert all(set(command.public()) == {"name", "description", "argument_hint"} for command in commands)


def test_slash_discovery_contains_no_query_rewrite_or_routing_contract() -> None:
    commands = list_slash_commands()

    assert all(not hasattr(command, "planner_template") for command in commands)
    assert all(not hasattr(command, "seed") for command in commands)
