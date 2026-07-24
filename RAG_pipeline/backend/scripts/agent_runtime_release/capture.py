"""Authenticated public-API capture with resumable sanitized checkpoints."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scripts.agent_runtime_eval.models import Scenario


class PublicApiCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    scenario_id: str
    family: str
    prompt_id: str
    turn_id: str
    transport: str
    status_code: int
    assistant_text: str
    public_events: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0
    run_id: str | None = None
    persisted_message_id: int | None = None
    error: str | None = None

    @model_validator(mode="after")
    def derive_safe_terminal_data(self) -> "PublicApiCapture":
        if not self.assistant_text:
            self.assistant_text = "".join(
                str(event.get("public_payload", {}).get("content", ""))
                for event in self.public_events if event.get("type") == "assistant.delta"
            )
        terminal = next(
            (event.get("public_payload", {}) for event in self.public_events if event.get("type") == "run.completed"),
            {},
        )
        usage = terminal.get("usage", {})
        self.input_tokens = int(usage.get("input_tokens", self.input_tokens))
        self.output_tokens = int(usage.get("output_tokens", self.output_tokens))
        self.cost_usd = float(usage.get("model_cost_usd", self.cost_usd)) + float(
            usage.get("capability_cost_usd", 0)
        )
        done = next(
            (event for event in self.public_events if event.get("type") == "done"),
            {},
        )
        self.run_id = str(done["run_id"]) if done.get("run_id") else self.run_id
        if done.get("message_id") is not None:
            self.persisted_message_id = int(done["message_id"])
        return self


class PublicApiCollector:
    """Uses only the authenticated public conversation API; never imports runtime code."""

    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        access_tokens_by_family: dict[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("an authenticated access token is required")
        csrf_token = secrets.token_urlsafe(32)
        self._owned_client = client is None
        self._default_access_token = access_token
        self._access_tokens_by_family = dict(access_tokens_by_family or {})
        self.client = client or httpx.Client(
            base_url=base_url,
            cookies={"access_token": access_token, "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            timeout=180,
        )
        if client is not None:
            self.client.cookies.set("access_token", access_token)
            self.client.cookies.set("csrf_token", csrf_token)
            self.client.headers["X-CSRF-Token"] = csrf_token

    def close(self) -> None:
        if self._owned_client:
            self.client.close()

    def capture(
        self,
        scenario: Scenario,
        *,
        checkpoint: Path,
        existing: list[PublicApiCapture],
        selected_transports: set[str] | None = None,
        evaluation_family: str | None = None,
    ) -> list[PublicApiCapture]:
        access_token = self._access_tokens_by_family.get(
            scenario.family, self._default_access_token
        )
        self.client.cookies.set("access_token", access_token)
        completed = {item.attempt_id for item in existing}
        conversation = self.client.post("/api/v1/conversations", json={"title": f"eval:{scenario.scenario_id}"})
        conversation.raise_for_status()
        conversation_id = conversation.json()["id"]
        try:
            for user_text, assistant_text in _seed_pairs(scenario.conversation_seed):
                response = self.client.post(
                    f"/api/v1/conversations/{conversation_id}/messages/save",
                    json={"user_message": user_text, "assistant_message": assistant_text},
                )
                response.raise_for_status()
            for turn in scenario.turns:
                for transport in turn.transports:
                    if selected_transports is not None and transport not in selected_transports:
                        continue
                    attempt_id = f"{scenario.scenario_id}:{turn.turn_id}:{transport}"
                    if attempt_id in completed:
                        continue
                    started = time.monotonic()
                    capture = self._invoke(
                        conversation_id, scenario, turn.turn_id, turn.user_message,
                        transport, attempt_id, evaluation_family or scenario.family,
                    )
                    capture.latency_ms = (time.monotonic() - started) * 1000
                    existing.append(capture)
                    _write_checkpoint(checkpoint, existing)
        finally:
            try:
                self.client.delete(f"/api/v1/conversations/{conversation_id}")
            except httpx.HTTPError:
                # Capture evidence is authoritative. A transient provider/stream
                # disconnect must not discard the checkpoint merely because the
                # evaluator's best-effort fixture cleanup reused that connection.
                pass
        return existing

    def _invoke(self, conversation_id: int, scenario: Scenario, turn_id: str, query: str, transport: str, attempt_id: str, family: str) -> PublicApiCapture:
        project_id = scenario.authorized_context.project_scope[0] if len(scenario.authorized_context.project_scope) == 1 else None
        payload = {
            "query": query, "project_id": project_id,
            "client_turn_id": _client_turn_id(f"{scenario.scenario_id}:{turn_id}"),
        }
        path = f"/api/v1/conversations/{conversation_id}/messages"
        if transport == "stream":
            response = self.client.post(path + "/stream", json=payload)
            events = _parse_sse(response.text) if response.status_code == 200 else []
            answer = "".join(
                str(event.get("payload", {}).get("text", event.get("content", "")))
                for event in events if event.get("type") in {"assistant.delta", "agent_response"}
            )
            if response.status_code == 200 and family in {
                "rapid_duplicate_turn", "stream_reconnect"
            }:
                params = None
                if family == "stream_reconnect":
                    first_sequence = next(
                        (
                            int(event["sequence"])
                            for event in events
                            if isinstance(event.get("sequence"), int)
                        ),
                        0,
                    )
                    params = {"after_sequence": first_sequence}
                replay = self.client.post(path + "/stream", json=payload, params=params)
                if replay.status_code == 200:
                    events = _parse_sse(replay.text)
        else:
            response = self.client.post(path, json=payload)
            answer = str(response.json().get("content", "")) if response.status_code == 200 else ""
            replay = self.client.post(path + "/stream", json=payload) if response.status_code == 200 else None
            events = _parse_sse(replay.text) if replay is not None and replay.status_code == 200 else []
        return PublicApiCapture(
            attempt_id=attempt_id, scenario_id=scenario.scenario_id,
            family=family, prompt_id=scenario.scenario_id, turn_id=turn_id,
            transport=transport, status_code=response.status_code, assistant_text=answer,
            public_events=events, latency_ms=0,
            error=None if response.status_code == 200 else f"public_api_http_{response.status_code}",
        )


def _client_turn_id(attempt_id: str) -> str:
    import hashlib
    return "eval-" + hashlib.sha256(attempt_id.encode()).hexdigest()[:32]


_BOUNDED_FAMILIES: dict[str, tuple[str, ...]] = {
    "direct_answer": ("direct_conversation",),
    "rag_followup": ("rag_followup",),
    "multi_tool_read": ("multi_part_read", "multiple_reads"),
    "discuss_versus_act": ("capability_discussion", "guidance", "negated_action"),
    "pack_write": ("explicit_creation",),
    "workflow_approval": ("cancel_workflow", "approval_reject", "approval_edit"),
    "task_interruption": ("task_switch", "active_workflow_interruption", "pause_conversation"),
    "no_evidence": ("no_evidence",),
    "denial_recovery": ("policy_denial", "scope_denied", "source_unavailable", "correctable_args"),
}


def bounded_selection(scenarios: list[Scenario]) -> list[tuple[Scenario, str, str]]:
    """Return the versioned 50-attempt stratification without model decisions."""
    by_family: dict[str, list[Scenario]] = {}
    for scenario in scenarios:
        by_family.setdefault(scenario.family, []).append(scenario)
    selected: list[tuple[Scenario, str, str]] = []
    for canonical, sources in _BOUNDED_FAMILIES.items():
        candidates = [scenario for source in sources for scenario in by_family.get(source, [])]
        if len(candidates) < 5:
            raise ValueError(f"bounded corpus lacks five scenarios for {canonical}")
        selected.extend(
            (scenario, "stream" if index % 2 else "nonstream", canonical)
            for index, scenario in enumerate(candidates[:5])
        )
    for family in ("stream_reconnect", "grounding_safe_stream", "rapid_duplicate_turn"):
        selected.append((by_family[family][0], "stream", family))
    parity = by_family["transport_parity"][0]
    selected.extend(((parity, "stream", "transport_parity"), (parity, "nonstream", "transport_parity")))
    if len(selected) != 50:
        raise AssertionError(f"bounded selection must contain 50 attempts, got {len(selected)}")
    return selected


def collection_batches(
    selection: list[tuple[Scenario, str, str]],
) -> list[tuple[Scenario, set[str], str]]:
    """Co-locate paired transports so the second projection replays one turn."""

    batches: list[tuple[Scenario, set[str], str]] = []
    paired: set[str] = set()
    for scenario, transport, family in selection:
        if family == "transport_parity":
            if scenario.scenario_id in paired:
                continue
            paired.add(scenario.scenario_id)
            transports = {"stream", "nonstream"}
        else:
            transports = {transport}
        batches.append((scenario, transports, family))
    return batches


def validate_published_project_inventory(
    *,
    snapshot: Path,
    docs_base_url: str,
    access_token: str,
    client: httpx.Client | None = None,
) -> None:
    """Fail before model spend when the authenticated published corpus drifted."""

    expected = json.loads(snapshot.read_text())
    project_id = str(expected["project_id"])
    expected_docs = {
        (str(item["document"]), int(item["size_bytes"]))
        for item in expected["documents"]
    }
    owned = client is None
    docs_client = client or httpx.Client(
        base_url=docs_base_url,
        cookies={"access_token": access_token},
        timeout=30,
    )
    try:
        response = docs_client.get(
            f"/projects/{project_id}/docs", params={"source": "published"}
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owned:
            docs_client.close()
    actual_docs = {
        (str(item.get("name", "")), int(item.get("size_bytes", 0)))
        for item in payload.get("docs", [])
        if isinstance(item, dict)
    }
    if actual_docs != expected_docs:
        missing = sorted(expected_docs - actual_docs)
        unexpected = sorted(actual_docs - expected_docs)
        raise ValueError(
            "published real-project inventory drifted; "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.split("\n\n"):
        event_type = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), None)
        data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), None)
        if data and data.startswith("{"):
            value = json.loads(data)
            if isinstance(value, dict):
                value.setdefault("type", event_type)
                events.append(value)
    return events


def _seed_pairs(history: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Convert semantic history to public save-exchange calls without dropping context."""
    pairs: list[tuple[str, str]] = []
    pending_user: str | None = None
    for message in history:
        role, content = message.get("role"), message.get("content", "")
        if role == "user":
            if pending_user is not None:
                pairs.append((pending_user, "[No assistant response was recorded.]"))
            pending_user = content
        elif role == "assistant":
            pairs.append((pending_user or "[Prior evaluation context]", content))
            pending_user = None
    if pending_user is not None:
        pairs.append((pending_user, "[No assistant response was recorded.]"))
    return pairs


def load_checkpoint(path: Path) -> list[PublicApiCapture]:
    if not path.exists():
        return []
    return [PublicApiCapture.model_validate(item) for item in json.loads(path.read_text())]


def _write_checkpoint(path: Path, captures: list[PublicApiCapture]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps([item.model_dump(mode="json") for item in captures], indent=2) + "\n"
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)
