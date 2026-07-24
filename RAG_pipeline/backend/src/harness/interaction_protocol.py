"""Model-led conversation modes shared by Nexus agent runtimes.

The model interprets meaning from the full conversation. Runtime code consumes
only this bounded projection; it never classifies intent from message keywords.
"""

from __future__ import annotations

import json
from enum import StrEnum

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.runtime.models import RuntimeToolDefinition


TURN_ASSESSMENT_TOOL_NAME = "nexus_turn_assess"


class ConversationMode(StrEnum):
    DISCUSS = "discuss"
    PREPARE = "prepare"
    ACT = "act"
    EXPLAIN = "explain"


class AssessedAction(BaseModel):
    """Exact model-visible action proposed during turn assessment."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, object] = Field(default_factory=dict)


class TurnAssessment(BaseModel):
    """Small semantic projection, deliberately excluding private reasoning."""

    model_config = ConfigDict(extra="forbid")

    mode: ConversationMode
    objective: str = Field(min_length=1, max_length=240)
    action_ready: bool = False
    missing_information: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Human-facing descriptions of unresolved business information. Never use "
            "schema paths, tool fields, capability identifiers, or runtime mechanics."
        ),
    )
    proposed_action: AssessedAction | None = None
    clarification_to_user: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "One direct question from Nexus to the user about the declared missing "
            "business information. Never copy or restate the user's request."
        ),
    )

    @model_validator(mode="after")
    def validate_readiness(self) -> "TurnAssessment":
        if self.mode is not ConversationMode.ACT and self.action_ready:
            raise ValueError("only act mode can be action-ready")
        if self.action_ready and self.missing_information:
            raise ValueError("an action-ready assessment cannot have missing information")
        if self.action_ready and self.proposed_action is None:
            raise ValueError("an action-ready assessment requires an exact proposed action")
        if (
            not self.action_ready
            and self.proposed_action is not None
            and self.mode is not ConversationMode.PREPARE
        ):
            raise ValueError("only prepare mode may retain a non-ready proposal")
        if self.mode is ConversationMode.PREPARE and not self.missing_information:
            raise ValueError("a prepared action requires missing information")
        if any(not item.strip() for item in self.missing_information):
            raise ValueError("missing information descriptions cannot be blank")
        if self.mode is ConversationMode.PREPARE and self.clarification_to_user is None:
            raise ValueError("prepare mode requires one clarification directed to the user")
        if (
            self.mode is ConversationMode.PREPARE
            and self.clarification_to_user is not None
            and (
                self.clarification_to_user.count("?") != 1
                or not self.clarification_to_user.rstrip().endswith("?")
            )
        ):
            raise ValueError("prepare mode requires exactly one direct question")
        if (
            self.mode is not ConversationMode.PREPARE
            and self.clarification_to_user is not None
        ):
            raise ValueError("only prepare mode may include a clarification to the user")
        return self

    @property
    def permits_consequential_action(self) -> bool:
        return self.mode is ConversationMode.ACT and self.action_ready

    def matches_action(self, tool_name: str, arguments: dict[str, object]) -> bool:
        return bool(
            self.permits_consequential_action
            and self.proposed_action is not None
            and self.proposed_action.tool_name == tool_name
            and self.proposed_action.arguments == arguments
        )


INTERACTION_PROTOCOL_PROMPT = """Conversation-action protocol:
First understand the user's conversational stage from meaning and full history.
- discuss: answer questions, possibilities, guidance, greetings, objections, or unrelated conversation; do not perform a consequential action.
- prepare: help shape an action, collect or summarize supplied details, and ask one useful next question; do not perform a consequential action.
- act: use only when the user wants an operation performed now and its intended operation and target are sufficiently resolved from user statements or authoritative observations. Bind readiness to one exact model-visible tool and its complete proposed arguments.
- explain: explain a prior assistant, tool, workflow, validation, or policy result; do not repeat or retry that action unless the user supplies new information and asks to try again.
Schema-valid defaults do not by themselves establish conversational readiness. Do not invent optional configuration to make an action look complete. An explicit request for a blank or default artifact may still be action-ready when the capability supports it.
When a turn assessment is requested, return only the bounded assessment fields and no private reasoning. A prepare assessment may include a partial proposed action when a semantic tool and grounded arguments are already known, but it must not invent an empty proposal merely to ask a question. When a partial proposal is included, retain all known arguments. `clarification_to_user` means one question Nexus will ask the user; it never means the user's request and must never copy or restate a user message. Runtime code, not the model, enforces permissions, approvals, scope, budgets, receipts, and tool availability.
After a needs_input observation, ask a concise contextual clarification and do not call another tool in that user turn. Never present missing input as a permission denial. Never apologize for an action that was prevented, narrate hidden tool mechanics, or claim success without a typed receipt or successful observation.
When authoritative background contains a prepared action, update or resume that exact proposal instead of reconstructing it from conversation prose. "Do it", "continue", or equivalent confirmation applies only to that prepared action.
If an unexecuted proposal fails preflight validation and correction is requested, correct it once using the smallest schema-valid arguments grounded in known user facts. Remove malformed optional structures instead of asking the user for internal ids, labels, capability identifiers, or schema fields. If a real business decision is missing, switch to prepare mode, retain the partial proposal, and ask exactly one domain-level question.
User-facing responses must never say that you need to fix, correct, retry, repair, or reformat a tool, schema, payload, requirement object, id, or capability. Summarize what is already known and ask at most one direct user-level question.
"""


def turn_assessment_tool_definition() -> RuntimeToolDefinition:
    return RuntimeToolDefinition(
        name=TURN_ASSESSMENT_TOOL_NAME,
        description=(
            "Classify this conversation turn before consequential action. Return a "
            "bounded mode/readiness projection, not reasoning or a user-facing answer. "
            "Valid combinations are strict: act requires action_ready=true, an exact "
            "proposed_action, missing_information=[], and no clarification; prepare "
            "requires action_ready=false, at least one missing_information item, and one "
            "clarification_to_user; proposed_action is optional in prepare and may retain "
            "a grounded partial action; discuss/explain "
            "require action_ready=false and no proposed_action or clarification."
        ),
        input_schema=TurnAssessment.model_json_schema(),
    )


def assessment_tool_result(assessment: TurnAssessment) -> str:
    return json.dumps(
        {
            "version": "1.0.0",
            "status": "succeeded",
            "summary": "Conversation stage assessed.",
            "data": {
                "mode": assessment.mode.value,
                "action_ready": assessment.action_ready,
                "missing_information": assessment.missing_information,
                "proposed_tool": (
                    assessment.proposed_action.tool_name
                    if assessment.proposed_action is not None
                    else None
                ),
                "clarification_to_user": assessment.clarification_to_user,
            },
            "evidence_refs": [],
            "retryable": False,
            "evidence_state_relevant": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def build_turn_assessment_tool() -> BaseTool:
    async def submit_assessment(**values: object) -> str:
        return assessment_tool_result(TurnAssessment.model_validate(values))

    return StructuredTool.from_function(
        coroutine=submit_assessment,
        name=TURN_ASSESSMENT_TOOL_NAME,
        description=(
            "Classify the current conversation stage before consequential action. "
            "Return bounded fields only; never include private reasoning."
        ),
        args_schema=TurnAssessment,
    )


__all__ = [
    "AssessedAction",
    "ConversationMode",
    "INTERACTION_PROTOCOL_PROMPT",
    "TURN_ASSESSMENT_TOOL_NAME",
    "TurnAssessment",
    "assessment_tool_result",
    "build_turn_assessment_tool",
    "turn_assessment_tool_definition",
]
