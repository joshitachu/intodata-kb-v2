"""Project registry — CRUD for projects in PostgreSQL."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.db.models import GenerateRun, Project, ScanRun
from mcp_server.licensing.usage import (
    RunAdmission,
    admit_generation,
    admit_scan,
    is_quota_counted,
)
from mcp_server.services.model_pricing import calculate_model_cost, load_model_pricing

logger = logging.getLogger(__name__)


class ProjectRegistry:
    """Manages project registration and status in the database."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add_project(
        self,
        project_id: str,
        local_path: str,
        *,
        display_name: str | None = None,
        repo_url: str | None = None,
        platform: str = "talend",
        source_type: str = "local",
    ) -> Project:
        """Register a new project. If it already exists, update its path."""
        existing = await self.get_project(project_id)
        if existing:
            existing.local_path = local_path
            existing.repo_url = repo_url
            existing.platform = platform
            existing.source_type = source_type
            existing.status = "registered"
            existing.error_message = None
            if display_name:
                existing.display_name = display_name
            await self.db.flush()
            logger.info(f"Updated existing project: {project_id}")
            return existing

        project = Project(
            id=project_id,
            display_name=display_name or project_id,
            repo_url=repo_url,
            local_path=local_path,
            platform=platform,
            source_type=source_type,
            status="registered",
        )
        self.db.add(project)
        await self.db.flush()
        logger.info(f"Registered new project: {project_id}")
        return project

    async def get_project(self, project_id: str) -> Project | None:
        result = await self.db.execute(
            select(Project).where(Project.id == project_id)
        )
        return result.scalar_one_or_none()

    async def list_projects(self) -> list[Project]:
        result = await self.db.execute(
            select(Project).order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_auto_reingest_git_projects(self) -> list[Project]:
        result = await self.db.execute(
            select(Project)
            .where(Project.source_type == "git")
            .where(Project.auto_reingest_enabled.is_(True))
            .order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        project_id: str,
        status: str,
        *,
        error_message: str | None = None,
    ) -> None:
        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(
                status=status,
                error_message=error_message,
                updated_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        await self.db.flush()

    async def update_scan_result(
        self,
        project_id: str,
        *,
        scan_result_path: str,
        output_dir: str,
        job_count: int,
    ) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(
                scan_result_path=scan_result_path,
                output_dir=output_dir,
                job_count=job_count,
                last_scan_at=now,
                status="ready",
                error_message=None,
                updated_at=now,
            )
        )
        await self.db.flush()

    async def update_generate_result(
        self, project_id: str, *, doc_count: int
    ) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(
                doc_count=doc_count,
                last_generate_at=now,
                status="ready",
                updated_at=now,
            )
        )
        await self.db.flush()

    _UNSET = object()

    async def update_project(
        self,
        project_id: str,
        *,
        display_name: str | None = None,
        platform: str | None = None,
        doc_generation_provider: object = _UNSET,
        instruction_pack_dir: object = _UNSET,
    ) -> Project | None:
        """Update editable fields on a project."""
        project = await self.get_project(project_id)
        if not project:
            return None
        if display_name is not None:
            project.display_name = display_name
        if platform is not None:
            project.platform = platform
        if doc_generation_provider is not ProjectRegistry._UNSET:
            project.doc_generation_provider = doc_generation_provider  # type: ignore[assignment]
        if instruction_pack_dir is not ProjectRegistry._UNSET:
            project.instruction_pack_dir = instruction_pack_dir  # type: ignore[assignment]
        await self.db.flush()
        return project

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project and all related records (cascade deletes scan_runs, generate_runs)."""
        project = await self.get_project(project_id)
        if not project:
            return False
        await self.db.delete(project)
        await self.db.flush()
        logger.info("Deleted project: %s", project_id)
        return True

    async def list_scan_runs(self, project_id: str) -> list[ScanRun]:
        result = await self.db.execute(
            select(ScanRun)
            .where(ScanRun.project_id == project_id)
            .order_by(ScanRun.started_at.desc())
        )
        return list(result.scalars().all())

    async def update_rag_push(self, project_id: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(last_rag_push_at=now, updated_at=now)
        )
        await self.db.flush()

    async def update_auto_reingest_check(
        self,
        project_id: str,
        *,
        error: str | None = None,
    ) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(
                auto_reingest_last_checked_at=now,
                auto_reingest_error=error,
                updated_at=now,
            )
        )
        await self.db.flush()

    async def update_last_ingested_commit(
        self,
        project_id: str,
        commit_sha: str,
    ) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(
                last_ingested_commit_sha=commit_sha,
                auto_reingest_error=None,
                updated_at=now,
            )
        )
        await self.db.flush()

    async def update_source_path(self, project_id: str, local_path: str) -> None:
        """Promote an already-activated immutable managed source root.

        ZIP replacement uses this only after its candidate docs and matching
        knowledge revision have activated. It is deliberately separate from
        project registration so a staged archive can never replace the active
        source by accident.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(local_path=local_path, updated_at=now)
        )
        await self.db.flush()

    async def update_git_update_summary(
        self,
        project_id: str,
        summary: dict,
        *,
        error: str | None = None,
    ) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        existing = await self.get_project(project_id)
        existing_summary = existing.git_update_summary if existing else None
        if (
            isinstance(existing_summary, dict)
            and isinstance(existing_summary.get("webhook"), dict)
            and isinstance(summary, dict)
            and "webhook" not in summary
        ):
            summary = {**summary, "webhook": existing_summary["webhook"]}
        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(
                git_update_summary=summary,
                auto_reingest_last_checked_at=now,
                auto_reingest_error=error,
                updated_at=now,
            )
        )
        await self.db.flush()

    async def record_webhook_observation(
        self,
        project_id: str,
        observation: dict[str, Any],
        *,
        error: str | None = None,
        set_error: bool = False,
    ) -> None:
        """Merge sanitized webhook receipt/matching evidence into project metadata."""
        now = datetime.datetime.now(datetime.timezone.utc)
        project = await self.get_project(project_id)
        existing_summary = project.git_update_summary if project else None
        summary = dict(existing_summary) if isinstance(existing_summary, dict) else {}
        existing_webhook = summary.get("webhook")
        webhook = dict(existing_webhook) if isinstance(existing_webhook, dict) else {}
        webhook.update(observation)
        summary["webhook"] = webhook

        values: dict[str, object | None] = {
            "git_update_summary": summary,
            "auto_reingest_last_checked_at": now,
            "updated_at": now,
        }
        if set_error:
            values["auto_reingest_error"] = error

        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(**values)
        )
        await self.db.flush()

    async def update_git_remote_metadata(
        self,
        project_id: str,
        *,
        repo_url: str | None = None,
        summary: dict | None = None,
        error: str | None = None,
    ) -> None:
        """Record sanitized Git remote hygiene metadata without triggering workflows."""
        now = datetime.datetime.now(datetime.timezone.utc)
        existing = await self.get_project(project_id)
        existing_summary = existing.git_update_summary if existing else None
        if (
            isinstance(existing_summary, dict)
            and isinstance(existing_summary.get("webhook"), dict)
            and isinstance(summary, dict)
            and "webhook" not in summary
        ):
            summary = {**summary, "webhook": existing_summary["webhook"]}
        values: dict[str, object | None] = {
            "auto_reingest_last_checked_at": now,
            "auto_reingest_error": error,
            "updated_at": now,
        }
        if repo_url is not None:
            values["repo_url"] = repo_url
        if summary is not None:
            values["git_update_summary"] = summary
        await self.db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(**values)
        )
        await self.db.flush()

    async def create_scan_run(
        self,
        project_id: str,
        triggered_by: str = "api",
    ) -> ScanRun:
        """Create a running scan record. The licence chokepoint for every scan.

        Raises ``LicenceRefused`` (HTTP 402 via the app handler) before writing
        anything when the licence does not allow a scan.
        """
        admit_scan()
        run = ScanRun(
            project_id=project_id,
            triggered_by=triggered_by,
            status="running",
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def complete_scan_run(
        self,
        run_id: int,
        *,
        status: str = "completed",
        job_count: int = 0,
        log_file: str | None = None,
    ) -> None:
        await self.db.execute(
            update(ScanRun)
            .where(ScanRun.id == run_id)
            .values(
                status=status,
                job_count=job_count,
                log_file=log_file,
                completed_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        await self.db.flush()

    # --- GenerateRun CRUD ---

    async def get_generate_run(self, run_id: int) -> GenerateRun | None:
        from sqlalchemy import select
        result = await self.db.execute(select(GenerateRun).where(GenerateRun.id == run_id))
        return result.scalar_one_or_none()

    async def create_generate_run(
        self,
        project_id: str,
        triggered_by: str = "api",
        model_name: str | None = None,
        partition_name: str | None = None,
        generation_profile: dict | None = None,
        *,
        admission: RunAdmission = RunAdmission.NEW,
    ) -> GenerateRun:
        """Create a running generation record. The licence chokepoint for every run.

        ``admission`` says how the run is admitted (``licensing/usage.py``):
        ``NEW`` needs a valid licence and a free slot in the period's generation
        allowance, and consumes one; ``CONTINUATION`` is a composite child or a
        gate retry of a run that was already admitted, needs a valid licence
        only, and is never counted. It must agree with the row's own fields --
        the quota count selects rows by those fields, so a mismatch would either
        let a run bypass the allowance or charge one that should be free.

        For ``NEW`` the quota lock is taken in this session's transaction and is
        held until the caller commits the insert. Raises ``LicenceRefused``
        before writing anything when refused.
        """
        counted = is_quota_counted(partition_name=partition_name, triggered_by=triggered_by)
        if counted != (admission is RunAdmission.NEW):
            raise ValueError(
                f"admission={admission.value} disagrees with the run's fields "
                f"(partition_name={partition_name!r}, triggered_by={triggered_by!r})"
            )
        await admit_generation(self.db, admission)
        run = GenerateRun(
            project_id=project_id,
            triggered_by=triggered_by,
            status="running",
            model_name=model_name,
            partition_name=partition_name,
            generation_profile=generation_profile,
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def complete_generate_run(
        self,
        run_id: int,
        *,
        status: str = "completed",
        cost_usd: float | None = None,
        duration_ms: int | None = None,
        duration_api_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        num_turns: int | None = None,
        session_id: str | None = None,
        doc_count: int = 0,
        model_name: str | None = None,
        write_model_name: str | None = None,
        quality_score: float | None = None,
        error_message: str | None = None,
        log_file: str | None = None,
        run_verdict: str | None = None,
        gate_results: dict | None = None,
    ) -> None:
        values = dict(
            status=status,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            duration_api_ms=duration_api_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            num_turns=num_turns,
            session_id=session_id,
            doc_count=doc_count,
            model_name=model_name,
            write_model_name=write_model_name,
            quality_score=quality_score,
            error_message=error_message,
            log_file=log_file,
            run_verdict=run_verdict,
            gate_results=gate_results,
            completed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        await self.db.execute(
            update(GenerateRun)
            .where(GenerateRun.id == run_id)
            .values(**values)
        )
        await self.db.flush()

    async def list_generate_runs(self, project_id: str) -> list[GenerateRun]:
        result = await self.db.execute(
            select(GenerateRun)
            .where(GenerateRun.project_id == project_id)
            .order_by(GenerateRun.started_at.desc())
        )
        return list(result.scalars().all())

    async def backfill_generate_run_costs(self, project_id: str | None = None) -> int:
        """Populate null GenerateRun costs from configured model pricing."""

        pricing = load_model_pricing()
        if not pricing:
            return 0

        conditions = [
            GenerateRun.cost_usd.is_(None),
            GenerateRun.input_tokens.is_not(None),
            GenerateRun.output_tokens.is_not(None),
            GenerateRun.model_name.is_not(None),
        ]
        if project_id is not None:
            conditions.append(GenerateRun.project_id == project_id)

        result = await self.db.execute(select(GenerateRun).where(*conditions))
        updated = 0
        for run in result.scalars().all():
            cost_usd = calculate_model_cost(
                run.model_name,
                run.input_tokens,
                run.output_tokens,
                pricing,
            )
            if cost_usd is None:
                continue
            run.cost_usd = cost_usd
            updated += 1

        await self.db.flush()
        return updated
