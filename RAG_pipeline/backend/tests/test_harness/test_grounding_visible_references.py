"""What the validator refuses to let into prose, and why the list must be live.

Two constants decide it: the semantic tool names the model may not name, and the
evidence-id namespaces it may not print. Both were hand-maintained lists that
nothing checked against reality, and both had drifted --

* ``read_document`` was still listed after being split into
  ``search_within_document`` and ``read_document``. The dead key scrubbed
  nothing and neither new name was covered.
* ``doctext:`` -- the whole-document read's own id namespace, added by the same
  split -- was in neither the validator's pattern nor the scrubber's, so those
  ids could reach the answer verbatim.

So these tests check the constants against the registry and against the
builders, not against a second copy of themselves.
"""

from __future__ import annotations

import re

import pytest

from src.harness.grounding import (
    VISIBLE_EVIDENCE_KEY_RE,
    AnswerStatus,
    GroundedAnswer,
    GroundingValidator,
    GroundingVerdict,
    _VISIBLE_TOOL_NAMES,
)
from src.harness.manifest import ManifestScope
from src.harness.readonly_runtime import _VISIBLE_EVIDENCE_KEY_RE
from src.harness.registry import default_capability_registry
from src.harness.semantic_tools import build_semantic_tool_snapshot


def _registered_names() -> set[str]:
    snapshot = build_semantic_tool_snapshot(
        default_capability_registry(),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    return {record.semantic_name for record in snapshot.tools}


def test_every_scrubbed_tool_name_is_a_capability_that_exists() -> None:
    dead = set(_VISIBLE_TOOL_NAMES) - _registered_names()
    assert not dead, f"validator scrubs names no capability answers to: {sorted(dead)}"


def test_the_scrubber_and_the_validator_share_one_evidence_pattern() -> None:
    # Not "the patterns are equal" -- the same object. Two equal copies drift the
    # first time only one of them is edited, which is the history above.
    assert _VISIBLE_EVIDENCE_KEY_RE is VISIBLE_EVIDENCE_KEY_RE


@pytest.mark.parametrize(
    "evidence_id",
    [
        # Namespaces minted in src/harness/evidence.py, one case each.
        "doc:12:3",
        "doc:12:0-1-2",  # a merged document source
        "doctext:44:2",  # the whole-document read
        "metadata:project_list:proj-a",
        "conversation:9:message:17",
        "mcp-doc:proj-a:Architecture.md",
        "mcp-doc-content:proj-a:Architecture.md",
        "runtime-semantic:tmc:run-1:0",
        "runtime-log:exec-7:3",
        "topology:proj-a",
    ],
)
def test_every_minted_evidence_id_shape_is_matched(evidence_id: str) -> None:
    assert VISIBLE_EVIDENCE_KEY_RE.search(evidence_id), evidence_id


@pytest.mark.parametrize(
    "prose",
    [
        "The order job writes to the warehouse table.",
        "See the architecture document, section 4.",
        "Version 2:3 of the mapping was deployed.",
    ],
)
def test_ordinary_prose_is_not_matched(prose: str) -> None:
    assert VISIBLE_EVIDENCE_KEY_RE.search(prose) is None


@pytest.mark.parametrize(
    "content",
    [
        "I used search_within_document to find it.",
        "Calling read_document for the complete list.",
        "Evidence doctext:44:2 supports this.",
    ],
)
def test_a_leaked_reference_is_correctable_not_accepted(content: str) -> None:
    outcome = GroundingValidator().validate(
        GroundedAnswer(content=content, answer_status=AnswerStatus.NO_EVIDENCE),
        allowed=set(),
    )

    assert outcome.verdict is GroundingVerdict.CORRECTABLE
    assert outcome.reason_code == "visible_implementation_reference"
    # The hint is model-facing: no regex, no constant name, no validator text.
    assert outcome.correction_hint is not None
    assert "doctext" not in outcome.correction_hint
    assert not re.search(r"[\\^$*+]", outcome.correction_hint)
