from __future__ import annotations

import json
from collections import Counter

import httpx
import pytest

from scripts.agent_runtime_eval.corpus import CORPUS_PATH, load_corpus
from scripts.agent_runtime_release.capture import (
    PublicApiCollector,
    bounded_selection,
    collection_batches,
    load_checkpoint,
    validate_published_project_inventory,
)
from scripts.agent_runtime_release.gate import (
    CRITICAL_FAMILIES,
    evaluate_release,
    inter_rater_reliability,
    wilson_lower,
)
from scripts.agent_runtime_release.evidence import assemble_release_evidence
from scripts.agent_runtime_release.models import (
    AttemptEvidence,
    DependencyEvidence,
    HumanRating,
    ReleaseEvidence,
)
from scripts.agent_runtime_release.review import review_captures


def _complete_evidence(*, production: bool = False) -> ReleaseEvidence:
    families = sorted(CRITICAL_FAMILIES)
    attempts = []
    total = 1000 if production else 50
    for index in range(total):
        family = families[index % len(families)]
        attempts.append(AttemptEvidence(
            attempt_id=f"attempt-{index}", scenario_id=f"scenario-{index}",
            prompt_id=f"{family}-prompt-{(index // len(families)) % 20}", family=family,
            authenticated=True, target="authenticated-public-api", passed=True,
            assistant_text=f"Sanitized helpful answer {index}",
            review_rationale="Correct boundary and safe trajectory.", tool_trajectory=[],
            latency_ms=100, input_tokens=10, output_tokens=20, cost_usd=.001,
        ))
    ratings = [
        HumanRating(attempt_id=f"attempt-{index}", rater_id=rater, rubric_version="naturalness-v1", score=5)
        for index in range(200) for rater in ("rater-a", "rater-b")
    ] if production else []
    return ReleaseEvidence(
        gate_profile="production-1000-v1" if production else "slice7-bounded-50-v1",
        corpus_version="v1", application_revision="abc", runtime_version="langgraph-x",
        model_profile="kimi", prompt_version="p1", manifest_sha256="m" * 64,
        corpus_sha256="c" * 64, checkpoint_serialization_review="pass",
        attempts=attempts, ratings=ratings, reliability=1.0 if production else None,
        dependency=DependencyEvidence(lockfile_sha256="l" * 64, sbom_sha256="s" * 64, scanner="uv-audit"),
        release_owner="owner", release_owner_decision="accept",
    )


def test_complete_authenticated_evidence_can_go() -> None:
    verdict = evaluate_release(_complete_evidence())
    assert verdict.verdict == "GO"
    assert verdict.blockers == []
    assert verdict.rated_answers == 0
    assert verdict.cost_usd == .05


def test_bounded_gate_reports_but_does_not_promote_clarification_metric_to_blocker() -> None:
    evidence = _complete_evidence()
    evidence.attempts[0].clarification_expected = True
    evidence.attempts[0].clarified = False
    evidence.attempts[1].clarification_expected = False
    evidence.attempts[1].clarified = True

    verdict = evaluate_release(evidence)

    assert verdict.verdict == "GO"
    assert verdict.clarification_precision == 0
    assert verdict.clarification_recall == 0
    assert not any("clarification gate" in blocker for blocker in verdict.blockers)


def test_scripted_or_incomplete_evidence_fails_closed_and_keeps_answer() -> None:
    evidence = _complete_evidence()
    evidence.attempts = evidence.attempts[:1]
    evidence.attempts[0].target = "scripted"
    evidence.attempts[0].authenticated = False
    evidence.attempts[0].assistant_text = "The model chose a promising but unsafe path"
    verdict = evaluate_release(evidence)
    assert verdict.verdict == "NO_GO"
    assert any("scripted/recorded" in blocker for blocker in verdict.blockers)
    assert any("1/50" in blocker for blocker in verdict.blockers)
    assert evidence.attempts[0].assistant_text == "The model chose a promising but unsafe path"


def test_absolute_safety_failure_overrides_perfect_scores() -> None:
    evidence = _complete_evidence()
    evidence.attempts[0].deterministic_failures = ["duplicate_effect"]
    evidence.attempts[0].unsupported_claims = 1
    verdict = evaluate_release(evidence)
    assert verdict.verdict == "NO_GO"
    assert any("duplicate_effect" in blocker for blocker in verdict.blockers)
    assert any("unsupported factual claims" in blocker for blocker in verdict.blockers)


def test_missing_dependencies_owner_and_checkpoint_block() -> None:
    evidence = _complete_evidence()
    evidence.dependency = None
    evidence.release_owner_decision = "reject"
    evidence.checkpoint_serialization_review = "missing"
    verdict = evaluate_release(evidence)
    assert verdict.verdict == "NO_GO"
    assert any("dependency/SBOM" in blocker for blocker in verdict.blockers)
    assert any("release owner" in blocker for blocker in verdict.blockers)
    assert any("checkpoint serialization" in blocker for blocker in verdict.blockers)


def test_wilson_is_fail_closed_for_empty_and_below_point_rate() -> None:
    assert wilson_lower(0, 0) == 0
    assert wilson_lower(990, 1000) < .99


def test_reliability_is_recomputed_and_cannot_be_overridden() -> None:
    assert inter_rater_reliability({"a": [1, 5], "b": [5, 1]}) < .67
    evidence = _complete_evidence(production=True)
    evidence.ratings[0].score = 1
    evidence.reliability = 1.0
    verdict = evaluate_release(evidence)
    assert verdict.verdict == "NO_GO"
    assert any("does not match" in blocker for blocker in verdict.blockers)


def test_public_api_collector_uses_cookie_both_transports_and_checkpoints(tmp_path) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "access_token=secret-token" in request.headers.get("cookie", "")
        assert "csrf_token=" in request.headers.get("cookie", "")
        assert request.headers["X-CSRF-Token"]
        if request.method == "POST" and request.url.path == "/api/v1/conversations":
            return httpx.Response(201, json={"id": 7})
        if request.url.path.endswith("/messages/stream"):
            body = {"type": "assistant.delta", "payload": {"text": "Nexus stream"}}
            return httpx.Response(200, text=f"event: assistant.delta\ndata: {json.dumps(body)}\n\n")
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"content": "Nexus nonstream"})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": 1})

    client = httpx.Client(
        base_url="http://nexus.test", cookies={"access_token": "secret-token"},
        transport=httpx.MockTransport(handler),
    )
    collector = PublicApiCollector(base_url="http://nexus.test", access_token="secret-token", client=client)
    scenario = load_corpus(CORPUS_PATH)[0]
    checkpoint = tmp_path / "checkpoint.json"
    captures = collector.capture(scenario, checkpoint=checkpoint, existing=[])
    assert {item.transport for item in captures} == {"stream", "nonstream"}
    assert {item.assistant_text for item in captures} == {"Nexus stream", "Nexus nonstream"}
    assert len(load_checkpoint(checkpoint)) == 2
    assert all("secret-token" not in checkpoint.read_text() for _ in [0])
    assert requests[-1].method == "DELETE"


def test_duplicate_capture_uses_real_replay_and_family_specific_identity(tmp_path) -> None:
    stream_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stream_calls
        assert "access_token=readonly-token" in request.headers.get("cookie", "")
        if request.method == "POST" and request.url.path == "/api/v1/conversations":
            return httpx.Response(201, json={"id": 9})
        if request.url.path.endswith("/messages/stream"):
            stream_calls += 1
            if stream_calls == 1:
                body = {"type": "assistant.delta", "payload": {"text": "Handled once."}}
                return httpx.Response(
                    200,
                    text=f"event: assistant.delta\ndata: {json.dumps(body)}\n\n",
                )
            completed = {
                "event_id": "run-1:evt:1",
                "sequence": 1,
                "type": "run.completed",
                "public_payload": {"status": "completed", "usage": {}},
            }
            done = {"message_id": 4, "run_id": "run-1", "replayed": True}
            return httpx.Response(
                200,
                text=(
                    f"event: run.completed\ndata: {json.dumps(completed)}\n\n"
                    f"event: done\ndata: {json.dumps(done)}\n\n"
                ),
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": 1})

    client = httpx.Client(
        base_url="http://nexus.test",
        transport=httpx.MockTransport(handler),
    )
    collector = PublicApiCollector(
        base_url="http://nexus.test",
        access_token="admin-token",
        access_tokens_by_family={"rapid_duplicate_turn": "readonly-token"},
        client=client,
    )
    scenario = next(
        item for item in load_corpus(CORPUS_PATH) if item.family == "rapid_duplicate_turn"
    )
    captures = collector.capture(
        scenario,
        checkpoint=tmp_path / "duplicate.json",
        existing=[],
        selected_transports={"stream"},
    )

    assert stream_calls == 2
    assert captures[0].assistant_text == "Handled once."
    assert any(event.get("replayed") is True for event in captures[0].public_events)


def test_bounded_selection_is_exactly_50_and_critical_family_stratified() -> None:
    selected = bounded_selection(load_corpus(CORPUS_PATH))
    counts = Counter(family for _, _, family in selected)
    assert len(selected) == 50
    assert all(counts[family] == 5 for family in CRITICAL_FAMILIES)
    assert counts["transport_parity"] == 2
    assert {transport for scenario, transport, _ in selected if scenario.family == "transport_parity"} == {
        "stream", "nonstream",
    }
    parity_batches = [
        batch for batch in collection_batches(selected) if batch[2] == "transport_parity"
    ]
    assert len(parity_batches) == 1
    assert parity_batches[0][1] == {"stream", "nonstream"}


def test_real_project_followups_use_the_versioned_document_inventory() -> None:
    scenarios = load_corpus(CORPUS_PATH)
    followups = [item for item in scenarios if item.family == "rag_followup"]
    actionable = [item for item in followups if item.turns[0].expected.allowed_tool_sequences[0]]

    assert actionable
    assert all(item.authorized_context.project_scope == ["reverseEngineer"] for item in followups)
    assert all(
        item.turns[0].expected.allowed_tool_sequences[0]
        == ["project_document_list"]
        for item in actionable
    )
    assert all(
        any(
            constraint.path == "project_id" and constraint.value == "reverseEngineer"
            for constraint in item.turns[0].expected.argument_constraints
        )
        for item in actionable
    )


def test_published_inventory_preflight_fails_closed_on_drift(tmp_path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({
        "project_id": "real-project",
        "documents": [{"document": "job.md", "size_bytes": 12}],
    }))

    def matching(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "docs": [{"name": "job.md", "size_bytes": 12}],
        })

    validate_published_project_inventory(
        snapshot=snapshot,
        docs_base_url="http://docs.test",
        access_token="token",
        client=httpx.Client(
            base_url="http://docs.test", transport=httpx.MockTransport(matching)
        ),
    )

    def drifted(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"docs": []})

    with pytest.raises(ValueError, match="inventory drifted"):
        validate_published_project_inventory(
            snapshot=snapshot,
            docs_base_url="http://docs.test",
            access_token="token",
            client=httpx.Client(
                base_url="http://docs.test", transport=httpx.MockTransport(drifted)
            ),
        )


def _capture(scenario_id: str, *, answer: str, tools: list[str] | None = None) -> dict:
    events = [{
        "event_id": "run:evt:1", "sequence": 1, "type": "run.started",
        "public_payload": {"status": "running"}, "replayable": True,
    }]
    for index, label in enumerate(tools or [], start=2):
        events.append({
            "event_id": f"run:evt:{index}", "sequence": index, "type": "tool.proposed",
            "public_payload": {"tool_label": label, "status": "proposed"}, "replayable": True,
        })
    events.append({
        "event_id": "run:evt:99", "sequence": 99, "type": "run.completed",
        "public_payload": {"status": "completed", "usage": {}}, "replayable": True,
    })
    return {
        "attempt_id": f"{scenario_id}:turn:stream", "scenario_id": scenario_id,
        "family": "test", "prompt_id": scenario_id, "turn_id": "turn",
        "transport": "stream", "status_code": 200, "assistant_text": answer,
        "public_events": events, "latency_ms": 1,
    }


def test_capture_review_checks_tool_path_response_and_retains_answer() -> None:
    from scripts.agent_runtime_release.capture import PublicApiCapture

    scenarios = load_corpus(CORPUS_PATH)
    capture = PublicApiCapture.model_validate(_capture(
        "pack.create.001", answer="Pack draft created.", tools=["create pack draft"],
    ))
    review = review_captures([capture], scenarios)
    assert review.passed_attempts == 1
    assert review.attempts[0].tool_trajectory == ["pack_draft_create"]
    assert review.attempts[0].assistant_text == "Pack draft created."

    wrong = PublicApiCapture.model_validate(_capture(
        "pack.create.001", answer="Field required", tools=["list documentation packs"],
    ))
    review = review_captures([wrong], scenarios)
    assert review.failed_attempts == 1
    assert review.attempts[0].deterministic_failures == ["trajectory", "response"]
    assert "observed ['pack_list']" in review.attempts[0].review_rationale


def test_capture_review_fails_missing_grounding_and_transport_parity() -> None:
    from scripts.agent_runtime_release.capture import PublicApiCapture

    scenarios = load_corpus(CORPUS_PATH)
    grounded = PublicApiCapture.model_validate(_capture(
        "task.switch.001", answer="No matching evidence.", tools=["search documentation"],
    ))
    stream = PublicApiCapture.model_validate(_capture(
        "transport.parity.001", answer="same stream answer",
    ))
    nonstream_payload = _capture("transport.parity.001", answer="same nonstream answer")
    nonstream_payload["attempt_id"] = "transport.parity.001:turn:nonstream"
    nonstream_payload["transport"] = "nonstream"
    nonstream = PublicApiCapture.model_validate(nonstream_payload)
    review = review_captures([grounded, stream, nonstream], scenarios)
    by_id = {item.attempt_id: item for item in review.attempts}
    assert "grounding" in by_id[grounded.attempt_id].deterministic_failures
    assert all(
        "transport_parity" in by_id[item.attempt_id].deterministic_failures
        for item in (stream, nonstream)
    )


def test_clarification_detector_accepts_bounded_could_you_tell_me_wording() -> None:
    from scripts.agent_runtime_release.capture import PublicApiCapture

    scenarios = load_corpus(CORPUS_PATH)
    payload = _capture(
        "workflow.cancel.001.ambiguous",
        answer="Could you tell me which workflow you'd like to stop?",
    )
    capture = PublicApiCapture.model_validate(payload)

    review = review_captures([capture], scenarios)

    assert "response" not in review.attempts[0].deterministic_failures


def test_transport_parity_accepts_same_persisted_answer_with_delta_formatting() -> None:
    from scripts.agent_runtime_release.capture import PublicApiCapture

    scenarios = load_corpus(CORPUS_PATH)
    captures = []
    for transport, answer in (
        ("stream", 'same response {"citations":[]}'),
        ("nonstream", 'same response {\n  "citations": []\n}'),
    ):
        payload = _capture("transport.parity.001", answer=answer)
        payload["attempt_id"] = f"transport.parity.001:turn:{transport}"
        payload["transport"] = transport
        payload["public_events"].append({
            "type": "done", "run_id": "run-shared", "message_id": 42,
        })
        captures.append(PublicApiCapture.model_validate(payload))

    review = review_captures(captures, scenarios)

    assert review.failed_attempts == 0


def test_collector_preserves_checkpoint_when_fixture_cleanup_disconnects(
    tmp_path,
) -> None:
    from scripts.agent_runtime_release.capture import PublicApiCollector

    scenario = next(item for item in load_corpus(CORPUS_PATH) if item.scenario_id == "direct.answer.001")
    checkpoint = tmp_path / "checkpoint.json"
    client = httpx.Client(
        base_url="http://test",
        transport=httpx.MockTransport(lambda request: (
            httpx.Response(200, json={"id": 7})
            if request.method == "POST" and request.url.path == "/api/v1/conversations"
            else httpx.Response(200, json={"content": "Nexus"})
            if request.method == "POST"
            else (_ for _ in ()).throw(httpx.RemoteProtocolError("disconnect"))
        )),
    )
    collector = PublicApiCollector(base_url="http://test", access_token="token", client=client)

    captures = collector.capture(
        scenario,
        checkpoint=checkpoint,
        existing=[],
        selected_transports={"nonstream"},
    )

    assert len(captures) == 1
    assert checkpoint.exists()


def test_release_evidence_assembly_preserves_review_without_owner_invention() -> None:
    from scripts.agent_runtime_release.capture import PublicApiCapture

    scenarios = load_corpus(CORPUS_PATH)
    capture = PublicApiCapture.model_validate(_capture(
        "direct.answer.001", answer="Nexus",
    ))
    review = review_captures([capture], scenarios)
    evidence = assemble_release_evidence(
        review,
        corpus_version="v1",
        application_revision="abc+dirty",
        runtime_version="runtime-v2",
        model_profile="kimi/kimi-k2.5",
        prompt_version="prompt-v2",
        manifest_sha256="m" * 64,
        corpus_sha256="c" * 64,
        checkpoint_serialization_review="pass",
        lockfile_sha256="l" * 64,
        sbom_sha256="s" * 64,
        scanner="uv-audit",
    )
    assert evidence.attempts[0].assistant_text == "Nexus"
    assert evidence.release_owner is None
    assert evidence.release_owner_decision is None
    assert evidence.dependency is not None
    assert evidence.dependency.critical == evidence.dependency.high == 0
