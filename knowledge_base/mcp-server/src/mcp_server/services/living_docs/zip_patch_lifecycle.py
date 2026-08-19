"""S6 adapter: route an immutable replacement ZIP through the S4 coordinator."""

from __future__ import annotations

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.git_patch_coordinator import GitPatchCoordinator
from mcp_server.services.living_docs.rollout import RolloutStage, inspect_project_patch_readiness
from mcp_server.services.living_docs.zip_revisions import (
    StagedZipRevision,
    ZipRevisionSubmission,
)
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.services.scan_fingerprint_service import ScanFingerprintService
from mcp_server.services.scanner_service import ScannerService
from mcp_server.services.true_patch_executor import TruePatchExecutor


class LiveZipPatchCoordinator:
    """Compare a staged ZIP scan to the active scan and run the common patch path."""

    def __init__(self, *, log_mgr: LogManager) -> None:
        self._log_mgr = log_mgr

    async def submit_zip_revision(self, staged: StagedZipRevision) -> ZipRevisionSubmission:
        if not config.living_doc_patching_enabled:
            return ZipRevisionSubmission(
                status="held", reason_code="living_doc_patching_disabled"
            )
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await registry.get_project(staged.project_id)
            if project is None or project.source_type != "zip":
                return ZipRevisionSubmission(status="held", reason_code="zip_project_not_found")
            platform = project.platform.lower()
            provider = project.doc_generation_provider
            active_source_path = project.local_path

        active_output = config.project_root / "output" / staged.project_id
        active_scan_dir = config.intodoc_output_dir / staged.project_id / "scan" / "jobs"
        if not (active_output / "docs").is_dir() or not active_scan_dir.is_dir():
            return ZipRevisionSubmission(status="held", reason_code="traceability_required")

        candidate_scan_output = staged.source_root.parent / "scan_output"
        scan = await ScannerService(log_manager=self._log_mgr).run_scan(
            project_id=staged.project_id,
            repo_path=str(staged.source_root),
            platform=platform,
            output_dir=str(candidate_scan_output),
        )
        if not scan.get("success"):
            return ZipRevisionSubmission(status="held", reason_code="candidate_scan_failed")
        current_hashes = ScanFingerprintService.compute_project_hashes(
            candidate_scan_output / "scan" / "jobs"
        )
        active_hashes = ScanFingerprintService.compute_project_hashes(active_scan_dir)
        changed_groups = sorted(
            set(current_hashes) ^ set(active_hashes)
            | {
                group
                for group in set(current_hashes) & set(active_hashes)
                if current_hashes[group] != active_hashes[group]
            }
        )
        if not changed_groups:
            return ZipRevisionSubmission(status="noop")

        raw_bridge = RagBridge(log_manager=self._log_mgr)
        revision_bridge = RagBridge(
            log_manager=self._log_mgr,
            revisioned_rag=raw_bridge.http_revisioned_adapter(),
        )
        store = FileCandidateStore(
            (config.data_dir / "living_docs" / "candidates" / staged.project_id).resolve()
        )
        coordinator = GitPatchCoordinator(
            store=store,
            publisher=CandidatePublisher(store=store, rag=revision_bridge),
            executor=TruePatchExecutor(doc_generation_provider=provider),
        )
        try:
            base_revision = await revision_bridge.active_knowledge_revision(staged.project_id)
            readiness = inspect_project_patch_readiness(
                project_id=staged.project_id,
                output_dir=active_output,
                feature_enabled=True,
                stage=RolloutStage(config.living_doc_rollout_stage),
                source_revision_id=active_source_path or None,
                active_knowledge_revision_id=base_revision,
            )
            if not readiness.ready:
                return ZipRevisionSubmission(
                    status="held", reason_code=readiness.reason_codes[0]
                )
            candidate = coordinator.create_zip_candidate(
                source_revision=staged.source_revision,
                base_source_revision_id=active_source_path,
                changed_groups=changed_groups,
                active_docs_dir=active_output / "docs",
                base_knowledge_revision_id=base_revision,
            )
            outcome = await coordinator.run(
                candidate,
                active_output_dir=active_output,
                candidate_scan_output_dir=candidate_scan_output,
                changed_groups=changed_groups,
                platform=platform,
                log_mgr=self._log_mgr,
                before_sha=active_source_path,
                after_sha=staged.source_revision.revision_id,
                active_source_revision_key=candidate.change_set.source_revision.key,
                rollout_stage=config.living_doc_rollout_stage,
                low_risk_max_artifacts=config.living_doc_low_risk_max_artifacts,
            )
        except Exception:
            return ZipRevisionSubmission(status="held", reason_code="zip_candidate_preflight_failed")
        if outcome.action != "active":
            return ZipRevisionSubmission(status="held", reason_code=outcome.reason_code)

        # This is deliberately last: a replacement source root becomes active
        # only after the matching docs and RAG revision passed their activation
        # checks. The staged ZIP remains content-addressed and recoverable.
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_source_path(staged.project_id, str(staged.source_root))
            await db.commit()
        return ZipRevisionSubmission(status="active")
