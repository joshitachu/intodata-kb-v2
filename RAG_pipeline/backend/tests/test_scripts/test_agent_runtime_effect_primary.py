from scripts.agent_runtime_effect_primary.runner import scenarios


def test_effect_primary_fixture_is_stratified_and_has_fifty_trajectories() -> None:
    cases = scenarios()
    assert len(cases) == 50
    assert {item["expected"] for item in cases} == {
        "no_effect",
        "read",
        "one_effect",
        "approval",
        "max_one_effect",
    }
    assert all(item["prompt"].strip() for item in cases)
