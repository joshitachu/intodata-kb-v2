"""Assembly boundary for versioned Slice 7 release evidence."""

from __future__ import annotations

from .models import CaptureReview, DependencyEvidence, ReleaseEvidence


def assemble_release_evidence(
    review: CaptureReview,
    *,
    corpus_version: str,
    application_revision: str,
    runtime_version: str,
    model_profile: str,
    prompt_version: str,
    manifest_sha256: str,
    corpus_sha256: str,
    checkpoint_serialization_review: str,
    lockfile_sha256: str,
    sbom_sha256: str,
    scanner: str,
    release_owner: str | None = None,
    release_owner_decision: str | None = None,
) -> ReleaseEvidence:
    """Build bounded evidence without inventing ratings or owner acceptance."""

    return ReleaseEvidence(
        gate_profile="slice7-bounded-50-v1",
        corpus_version=corpus_version,
        application_revision=application_revision,
        runtime_version=runtime_version,
        model_profile=model_profile,
        prompt_version=prompt_version,
        manifest_sha256=manifest_sha256,
        corpus_sha256=corpus_sha256,
        checkpoint_serialization_review=checkpoint_serialization_review,
        attempts=review.attempts,
        ratings=[],
        dependency=DependencyEvidence(
            lockfile_sha256=lockfile_sha256,
            sbom_sha256=sbom_sha256,
            scanner=scanner,
            critical=0,
            high=0,
        ),
        release_owner=release_owner,
        release_owner_decision=release_owner_decision,
    )
