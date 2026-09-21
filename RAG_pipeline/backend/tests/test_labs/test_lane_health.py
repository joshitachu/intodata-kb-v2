"""The lane health payload, which the labs banner mirrors field for field.

The two drifted and nobody noticed for as long as it took someone to look at the
banner: it declared `status`, `healthy` and `message` -- none of which this model
has ever sent -- and typed `detail` as a string while `_probe_opencode` fills it
with OpenCode's health object. The effect was inverted. A reachable lane failed
the client's validation and rendered a red Zod error; an unreachable one parsed
cleanly and rendered "Lane responding" with `error` dropped unread.

These tests pin the wire shape. A change that breaks one of them is a change that
has to be made in `app/labs/chat/page.tsx` in the same commit.
"""

from __future__ import annotations

import pytest

from src.api.routes.labs import LABS_COHORT, LabsHealthResponse, _probe_opencode
from src.labs.opencode_client import OpenCodeError


def test_the_payload_has_exactly_the_fields_the_banner_reads() -> None:
    assert set(LabsHealthResponse.model_fields) == {
        "lane",
        "opencode_available",
        "detail",
        "error",
        "model",
    }


def test_a_reachable_lane_reports_an_object_in_detail_not_a_sentence() -> None:
    payload = LabsHealthResponse(
        opencode_available=True, detail={"healthy": True}
    ).model_dump()

    assert payload["lane"] == LABS_COHORT
    assert payload["opencode_available"] is True
    # The field the frontend used to type as a string. It is OpenCode's own
    # health body and has never been one.
    assert payload["detail"] == {"healthy": True}
    assert payload["error"] is None
    assert isinstance(payload["model"], str) and payload["model"]


async def test_an_unreachable_lane_reports_the_reason_and_no_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure path still has to fill the one field that explains it."""

    class _FailingClient:
        async def __aenter__(self) -> _FailingClient:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

        async def health(self) -> dict[str, bool]:
            raise OpenCodeError("connection refused")

    monkeypatch.setattr("src.api.routes.labs.OpenCodeClient", _FailingClient)

    probe = await _probe_opencode()

    assert probe.opencode_available is False
    assert probe.error == "connection refused"
    assert probe.detail is None
    # Still reported: which model the lane *would* answer with is a fact about
    # the configuration, not about whether the server answered.
    assert probe.model


async def test_a_non_dict_probe_result_is_still_an_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`detail` is typed as an object, so it is one whatever the probe returned."""

    class _TerseClient:
        async def __aenter__(self) -> _TerseClient:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

        async def health(self) -> str:
            return "ok"

    monkeypatch.setattr("src.api.routes.labs.OpenCodeClient", _TerseClient)

    probe = await _probe_opencode()

    assert probe.opencode_available is True
    assert probe.detail == {"status": "ok"}
