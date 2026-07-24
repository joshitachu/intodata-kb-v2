from scripts.agent_runtime_plan_primary.cli import deterministic_report


def test_plan_primary_deterministic_evaluator_reports_zero_effects() -> None:
    report = deterministic_report()
    assert report["passed"] is True
    assert report["effects"] == 0
    assert all(item["expected"] == item["actual"] for item in report["outcomes"])
