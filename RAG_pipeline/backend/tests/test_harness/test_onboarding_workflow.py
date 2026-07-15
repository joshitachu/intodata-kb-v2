from __future__ import annotations

import pytest

from src.harness.executor import CapabilityExecutionContext, CapabilityObservation
from src.harness.models import (
    ATTACH_GIT_SOURCE_CAPABILITY,
    ATTACH_ZIP_SOURCE_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    PROJECT_STATUS_CAPABILITY,
    PUBLISH_INDEX_CAPABILITY,
    TRIGGER_SCAN_CAPABILITY,
)
from src.harness.workflows.onboarding import OnboardingRequest, ProjectOnboardingWorkflow


class _OnboardingExecutor:
    def __init__(self) -> None:
        self.requests = []

    async def execute(self, request, _context):  # noqa: ANN001
        self.requests.append(request)
        capability_id = request.capability_id
        if capability_id in {ATTACH_GIT_SOURCE_CAPABILITY.id, ATTACH_ZIP_SOURCE_CAPABILITY.id}:
            return CapabilityObservation(
                capability_id=capability_id,
                status="succeeded",
                data={
                    "project_id": "fixture-project",
                    "platform": "generic",
                    "source_type": "zip" if capability_id == ATTACH_ZIP_SOURCE_CAPABILITY.id else "git",
                    "filename": "fixture.zip",
                    "size_bytes": 4,
                    "sha256": "a" * 64,
                },
                artifact_links=[{"label": "Project", "url": "/projects/fixture-project", "kind": "project"}],
            )
        if capability_id == PROJECT_STATUS_CAPABILITY.id:
            return CapabilityObservation(capability_id=capability_id, status="succeeded", data={"status": "ready"})
        if capability_id == DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id:
            return CapabilityObservation(capability_id=capability_id, status="succeeded", data={"status": "completed"})
        return CapabilityObservation(capability_id=capability_id, status="triggered", data={"status": "accepted"})


@pytest.mark.parametrize(
    ("onboarding_request", "attachment_capability"),
    [
        (
            OnboardingRequest(workflow_run_id=11, project_id="fixture-project", repo_url="https://github.com/acme/fixture.git", selected_pack_ids=["nexus.documentation"], plugin_pack_id=17),
            ATTACH_GIT_SOURCE_CAPABILITY.id,
        ),
        (
            OnboardingRequest(workflow_run_id=12, project_id="fixture-project", upload_receipt="x" * 32, selected_pack_ids=["nexus.documentation"], plugin_pack_id=17),
            ATTACH_ZIP_SOURCE_CAPABILITY.id,
        ),
    ],
)
async def test_git_and_zip_onboarding_share_the_same_downstream_workflow(
    onboarding_request: OnboardingRequest, attachment_capability: str
) -> None:
    executor = _OnboardingExecutor()
    workflow = ProjectOnboardingWorkflow(scan_poll_interval=0)

    result = await workflow.run(
        onboarding_request,
        executor=executor,  # type: ignore[arg-type]
        context=CapabilityExecutionContext(user_id=1, allow_external_side_effects=True),
    )

    assert result.status == "triggered"
    assert [item.capability_id for item in executor.requests] == [
        attachment_capability,
        TRIGGER_SCAN_CAPABILITY.id,
        PROJECT_STATUS_CAPABILITY.id,
        DOCUMENTATION_GENERATION_CAPABILITY.id,
        DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id,
        PUBLISH_INDEX_CAPABILITY.id,
    ]
    assert executor.requests[3].arguments["selected_pack_ids"] == ["nexus.documentation"]
    assert executor.requests[3].arguments["plugin_pack_id"] == 17
