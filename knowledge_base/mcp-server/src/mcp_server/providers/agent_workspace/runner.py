"""``AgentWorkspaceProvider``: build a workspace, run an agent, publish.

The whole lane, in one small object, and it is deliberately not an
orchestrator. There is no phase driver, no batch planner, no partitioner, no
scope seeder, no candidate derivation and no work plan. Four steps:

1. the workspace builder stages the evidence and the instructions,
2. one ``AgentProvider`` runs against it,
3. :mod:`publish` decides whether what came back is safe to ship,
4. :mod:`topology` compiles the agent's overlay, and never blocks step 3.

**Restart, not resume.** A provider that loses its session reports it, and the
runner starts a FRESH session over the same workspace with a plain-prose
checkpoint summary composed from what is actually on disk. The verified
failure: a repair attempt died with ``Error: Session not found`` (returncode 1)
*after* a valid 22,723-byte artifact had already been written, and the whole
run failed. No provider session id is ever required for correctness.

**No per-anything budget.** The only limits are the whole-run circuit breakers
in ``AgentWorkspaceResources`` -- wall time, processed tokens, cost -- plus
``max_restarts``. ``docgen_max_run_calls``, ``estimate_partition_calls`` and
``split_groups`` have no role in this lane and are not read; a composite ZIP is
one workspace with the sub-projects as siblings under ``scan/``.

**A run reports what happened.** Publication and the swap happen only when the
provider reported ``completed`` AND the agent authored at least one client
document outside the compiler-owned ``output/reference/``. Before this, the
runner returned ``success=True`` whenever publication accepted anything, and
publication's only quantity floor was satisfied by the registers the compiler
had written before the agent started -- so run 250 published eight documents
and reported success for work the agent had not done. ``incomplete``,
``cancelled`` and ``failed`` now keep their draft in staging and never replace
the previously published documentation.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.contract import (
    AgentRunRequest,
    AgentRunResult,
    WorkspaceBuilder,
    WorkspaceReceipt,
    WorkspaceUnavailableError,
)
from mcp_server.providers.base import (
    AgentResult,
    DocGenContext,
    DocGenerationProvider,
    LogCallback,
)
from mcp_server.providers.coding_agent.models import ResourceCeiling, UsageReceipt

logger = logging.getLogger(__name__)

#: Bytes of the agent's own progress notes carried into a restart prompt.
#: Bounded because it is prompt content, not a state store; the workspace
#: itself is still there and the agent can re-read whatever it wants.
_CHECKPOINT_NOTE_BYTES = 8_000

#: How many produced files the checkpoint summary lists by name.
_CHECKPOINT_FILE_LIMIT = 60

#: The document subtree the deterministic register compiler owns. A file here
#: can never count as agent-authored work: it exists before the agent starts
#: and the runner rebuilds it from the scan afterwards regardless.
_COMPILER_DOC_ROOT = "reference"


class AgentWorkspaceProvider(DocGenerationProvider):
    """The ``agent-workspace-v1`` documentation provider.

    Constructor arguments exist for testing and for slice 5's dispatch; a
    production run passes none of them. ``build_workspace`` is resolved lazily
    so this module can be imported (and the provider constructed) before the
    workspace builder module exists in a given branch -- the two halves of this
    lane are written against ``contract.py`` and nothing else.
    """

    def __init__(
        self,
        *,
        build_workspace: WorkspaceBuilder | None = None,
        build_registry: Any | None = None,
        model_route: str | None = None,
    ) -> None:
        self._build_workspace = build_workspace
        self._build_registry = build_registry
        self._model_route = model_route

    # -- DocGenerationProvider ------------------------------------------

    async def run(self, ctx: DocGenContext, log_cb: LogCallback) -> AgentResult:
        from mcp_server.services.generation_profile import (
            AgentWorkspaceSettings,
            normalize_profile,
        )

        started = time.monotonic()
        profile = normalize_profile(ctx.generation_profile) or {}
        settings = AgentWorkspaceSettings.model_validate(profile.get("agent_workspace") or {})
        model_route = self._model_route or profile.get("model") or ctx.write_model

        try:
            receipt = self._stage_workspace(
                ctx, goal=settings.goal, scope_mode=settings.scope_mode
            )
        except WorkspaceUnavailableError as exc:
            # Fail-closed before any model call: every alternative publishes
            # unverified or unredacted client material. ``exc`` stringifies as
            # ``[stage] message``, so the operator is told WHERE it failed
            # without the message having to remember to say so.
            return await self._stage_failure(
                ctx, log_cb, exc, started=started, model_route=model_route
            )
        except Exception as exc:  # noqa: BLE001 - see _stage_failure
            return await self._stage_failure(
                ctx, log_cb, exc, started=started, model_route=model_route
            )

        # Counts, not content. An operator reading the run log has to be able to
        # tell a full workspace from a nearly-empty one without opening it --
        # and the navigation views are the part whose silent absence would look
        # exactly like an estate that simply has no interfaces.
        navigation = ", ".join(
            f"{view} {count}" for view, count in sorted(receipt.navigation_rows.items())
        )
        reference = ", ".join(
            f"{kind} {count}" for kind, count in sorted(receipt.reference_registers.items())
        )
        # Reported alongside navigation, never folded into it. A thin index is
        # diagnostic in a way a thin document is not: zero `relationships` on a
        # real estate means dependency extraction produced nothing, and in a
        # finished document that is indistinguishable from an estate whose
        # designs genuinely call nothing.
        indexes = ", ".join(
            f"{name} {count}" for name, count in sorted(receipt.index_rows.items()) if count
        )
        await log_cb(
            ctx.project_id,
            (
                f"[agent-workspace] staged {receipt.design_count} design(s) in "
                f"{receipt.group_count} group(s), {receipt.mapping_table_count} mapping "
                f"table(s), {len(receipt.platform_guides)} platform guide(s); "
                f"{receipt.coverage_candidates} design(s) on the coverage roster; "
                f"navigation rows: {navigation or 'none'}; index rows: "
                f"{indexes or 'none'}; runtime evidence "
                f"{'available' if receipt.runtime_available else 'unavailable'}; "
                f"published factual rows: {reference or 'not supplied'}"
            ),
            "info",
        )
        for notice in receipt.notices[:10]:
            await log_cb(ctx.project_id, f"[agent-workspace] {notice}", "warning")

        try:
            provider = self._resolve_provider(settings.agent_provider, model_route)
        except Exception as exc:  # noqa: BLE001 - an unregistered adapter is a config error
            await log_cb(ctx.project_id, str(exc), "error")
            return AgentResult(
                success=False,
                error=str(exc),
                model_name=model_route,
                write_model_name=model_route,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        # Taken BEFORE the agent runs, and the reason completion can be
        # attributed at all: ``output/reference/`` is already populated at this
        # point, so "output/ is non-empty" says nothing about the agent.
        baseline = _document_digests(ctx.staging_dir / contract.OUTPUT)

        result, usage = await self._run_with_restarts(
            ctx,
            provider,
            settings=settings,
            log_cb=log_cb,
        )
        try:
            return await self._publish(
                ctx,
                log_cb,
                result=result,
                usage=usage,
                settings=settings,
                started=started,
                model_route=result.model or model_route,
                evidence_digest=receipt.content_digest,
                reference_expected=bool(receipt.reference_registers),
                baseline=baseline,
            )
        except WorkspaceUnavailableError as exc:
            return await self._stage_failure(
                ctx, log_cb, exc, started=started, model_route=model_route, usage=usage
            )
        except Exception as exc:  # noqa: BLE001 - see _stage_failure
            return await self._stage_failure(
                ctx, log_cb, exc, started=started, model_route=model_route, usage=usage
            )

    # -- steps -----------------------------------------------------------

    async def _stage_failure(
        self,
        ctx: DocGenContext,
        log_cb: LogCallback,
        exc: BaseException,
        *,
        started: float,
        model_route: str,
        usage: UsageReceipt | None = None,
    ) -> AgentResult:
        """Turn any staging or publication failure into a named failed result.

        Two rules, and they are the whole point of this method:

        * A :class:`WorkspaceUnavailableError` carries its ``stage`` and names
          a relative artifact, so it is client-safe by construction and is
          reported verbatim. Navigation, instruction rendering and reference
          compilation used to raise their own unrelated types, escaping the
          runner's single ``except`` and reaching the operator as a bare
          "Generation failed" with nothing to act on.
        * Anything else is UNEXPECTED. Its message may quote a file's contents
          or a path outside the run, so the client-facing text gets a
          correlation id and the type only; the full exception chain goes to
          the server log under the same id.

        Either way the existing ``docs/`` is untouched: this returns a failed
        ``AgentResult`` and never reaches ``finalize_publication``.
        """

        if isinstance(exc, WorkspaceUnavailableError):
            message = f"Workspace could not be staged: {exc}"
            logger.warning("agent-workspace staging failure (%s): %s", exc.stage, exc)
        else:
            correlation = uuid.uuid4().hex[:12]
            message = (
                f"Agent-workspace run failed unexpectedly ({type(exc).__name__}); "
                f"correlation id {correlation}. The previously published documentation "
                "was not replaced."
            )
            logger.exception("agent-workspace failure %s", correlation, exc_info=exc)
        await log_cb(ctx.project_id, message, "error")
        return self._result(
            success=False,
            error=message,
            usage=usage or UsageReceipt(),
            started=started,
            model_route=model_route,
            session_id=None,
        )

    @staticmethod
    def _result(
        *,
        success: bool,
        error: str | None,
        usage: UsageReceipt,
        started: float,
        model_route: str,
        session_id: str | None,
        required: list[str] | None = None,
        has_warnings: bool = False,
        cancelled: bool = False,
        outcome: str = "",
    ) -> AgentResult:
        """The single place this lane converts usage into a run record.

        ``input_tokens`` is every token the provider charged as input:
        uncached, cache READ and cache WRITE. Dropping cache writes -- which is
        what this did -- understates the run and lets a cost ceiling read a
        number that is not the bill. ``output_tokens`` includes reasoning
        tokens for the same reason: they are generated tokens, they are
        charged, and a reasoning-heavy model reported near-zero output without
        them.

        ``outcome`` is the four-way terminal state. ``success``/``cancelled``
        can only say three things, so "stopped early with a resumable draft"
        and "broke" were the same row in the run record.
        """

        return AgentResult(
            success=success,
            error=error,
            input_tokens=(
                usage.uncached_input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
            ),
            output_tokens=usage.output_tokens + usage.reasoning_tokens,
            cost_usd=usage.cost_usd or None,
            duration_ms=int((time.monotonic() - started) * 1000),
            duration_api_ms=usage.provider_time_ms or None,
            num_turns=usage.model_turns or None,
            session_id=session_id,
            model_name=model_route,
            write_model_name=model_route,
            required_docs=required or [],
            has_warnings=has_warnings,
            cancelled=cancelled,
            outcome=outcome or ("completed" if success else "cancelled" if cancelled else "failed"),
        )

    def _stage_workspace(
        self, ctx: DocGenContext, *, goal: str, scope_mode: str | None = None
    ) -> WorkspaceReceipt:
        builder = self._build_workspace
        if builder is None:
            from mcp_server.providers.agent_workspace.workspace import build_workspace

            builder = build_workspace
        return builder(
            project_id=ctx.project_id,
            staging_dir=ctx.staging_dir,
            platform=ctx.platform,
            goal=goal,
            scope_mode=scope_mode,
        )

    def _resolve_provider(self, provider_id: str, model_route: str) -> Any:
        build_registry = self._build_registry
        if build_registry is None:
            from mcp_server.providers.coding_agent.factory import (
                build_agent_provider_registry,
            )

            build_registry = build_agent_provider_registry
        return build_registry(model=model_route).create(provider_id)

    async def _run_with_restarts(
        self,
        ctx: DocGenContext,
        provider: Any,
        *,
        settings: Any,
        log_cb: LogCallback,
    ) -> tuple[AgentRunResult, UsageReceipt]:
        """Run the agent, restarting a lost or truncated session.

        A restart is a FRESH session over the same workspace. Two conditions
        allow one and both must also leave budget: the provider lost its
        session, or it stopped incomplete with the run ceiling not yet reached.
        A hard provider failure (a missing CLI, a version mismatch, a denied
        cache directory) is NOT retried -- repeating it three times only
        delays the same error.
        """

        ceiling = ResourceCeiling(
            wall_time_seconds=settings.resources.wall_time_seconds,
            processed_tokens=settings.resources.processed_token_ceiling,
            cost_usd=settings.resources.cost_ceiling_usd,
        )
        request = AgentRunRequest.for_workspace(
            ctx.staging_dir,
            run_id=str(ctx.run_id if ctx.run_id is not None else ctx.project_id),
            project_id=ctx.project_id,
            allowed_tools=contract.DEFAULT_TOOLS,
            ceiling=ceiling,
        )
        total = UsageReceipt()
        result: AgentRunResult | None = None

        for attempt in range(settings.resources.max_restarts + 1):
            request = request.model_copy(update={"adapter_options": {"attempt": str(attempt)}})
            result = await provider.run_agent(request, log_cb=log_cb)
            total = total.merged(result.usage)
            if result.success:
                break
            reached = ceiling.reached_by(total)
            retryable = result.session_was_lost or result.status == "incomplete"
            if attempt >= settings.resources.max_restarts or not retryable or reached:
                if reached:
                    await log_cb(
                        ctx.project_id,
                        "[agent-workspace] run ceiling reached ("
                        + ", ".join(reached)
                        + "); publishing what is on disk",
                        "warning",
                    )
                break
            summary = self._write_checkpoint_summary(
                ctx.staging_dir, previous=result, attempt=attempt
            )
            request = request.model_copy(update={"checkpoint_summary": summary})
            await log_cb(
                ctx.project_id,
                (
                    "[agent-workspace] restarting with a fresh session "
                    f"(attempt {attempt + 2} of {settings.resources.max_restarts + 1}): "
                    f"{result.stop_reason or result.status}"
                ),
                "warning",
            )

        assert result is not None  # the loop always runs at least once
        return result, total

    def _write_checkpoint_summary(
        self, staging_dir: Path, *, previous: AgentRunResult, attempt: int
    ) -> str:
        """Compose the between-attempt summary from what is actually on disk.

        Plain prose, never a machine-readable state blob: a schema here would
        be reintroducing the contract this lane exists to remove. The agent's
        own ``notes/progress.md`` is preserved inside the summary rather than
        overwritten -- it is the more informative half, and the runner only
        knows which files exist, not what is left to do.
        """

        staging_dir = Path(staging_dir)
        output_dir = staging_dir / contract.OUTPUT
        # READ the agent's own notes; WRITE Nexus's control file. Two paths,
        # because they were one: this method used to read `notes/progress.md`,
        # embed it in the summary, and write the summary back over it, so each
        # restart nested the previous one and the agent's own record was lost.
        agent_progress = staging_dir / contract.AGENT_PROGRESS
        checkpoint = staging_dir / contract.RUNNER_CHECKPOINT

        produced: list[str] = []
        if output_dir.is_dir():
            for path in sorted(output_dir.rglob("*")):
                if not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                produced.append(f"- {path.relative_to(output_dir).as_posix()} ({size} bytes)")

        own_notes = ""
        try:
            if agent_progress.is_file():
                own_notes = agent_progress.read_text(encoding="utf-8", errors="replace")[
                    -_CHECKPOINT_NOTE_BYTES:
                ]
        except OSError:
            own_notes = ""

        # Why it stopped, bounded. It goes to the same agent over the same
        # workspace, so a transport message is not a disclosure -- but it can
        # be long, and the prompt is not the place for a stack trace.
        reason = (
            " ".join(part for part in (previous.stop_reason, previous.error) if part).strip()
            or previous.status
        )
        lines = [
            f"## Checkpoint after attempt {attempt + 1}",
            "",
            f"The previous session ended ({previous.status}): {reason[:400]}",
            "",
        ]
        if produced:
            lines.append(
                "Files already written to output/ (do not redo these unless "
                "they are wrong or incomplete):"
            )
            lines.extend(produced[:_CHECKPOINT_FILE_LIMIT])
            if len(produced) > _CHECKPOINT_FILE_LIMIT:
                lines.append(f"- ... and {len(produced) - _CHECKPOINT_FILE_LIMIT} more")
        else:
            lines.append("output/ is still empty; nothing has been produced yet.")
        if own_notes.strip():
            lines.extend(["", "Your own notes from the previous session:", "", own_notes.strip()])
        summary = "\n".join(lines) + "\n"

        try:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(summary, encoding="utf-8")
        except OSError:
            logger.warning("Could not persist the agent-workspace checkpoint summary")
        return summary

    @staticmethod
    def _coverage_line(staging_dir: Path) -> str:
        """One clause describing how much of the estate was accounted for.

        Best effort and never raising: this runs on the path where the run has
        ALREADY failed, and an exception here would replace a useful warning
        with an unexplained crash.
        """

        from mcp_server.providers.agent_workspace import analysis, ledger

        try:
            coverage = ledger.read_ledger(staging_dir)
            analysis_coverage = analysis.read_analysis_coverage(staging_dir)
        except Exception:  # noqa: BLE001 - a diagnostic line may not fail a run
            return ""
        total = len(coverage.population)
        if not total:
            return ""
        outstanding = len(coverage.unreviewed) + len(coverage.unresolved)
        groups = len(analysis_coverage.groups)
        analysed = groups - len(analysis_coverage.missing)
        return (
            f"Coverage: {total - outstanding} of {total} design(s) accounted for, "
            f"{len(coverage.unreviewed)} never reviewed, "
            f"{len(coverage.unresolved)} unresolved; "
            f"{analysed} of {groups} group(s) analysed."
        )

    def _evaluate_accountability(
        self,
        ctx: DocGenContext,
        *,
        settings: Any,
        documents: frozenset[str],
    ) -> Any:
        """Recompute the obligation and judge the agent's claim against it.

        Every input is read HERE rather than carried from earlier in the run:
        the ledger's population comes from the digest-protected evidence, the
        model and the receipt come from disk. Nothing is taken on the agent's
        word, and nothing is taken on a count this method was handed.

        The three artifacts are persisted before the verdict is returned. A
        refused run is exactly the run whose operator needs to read them, and
        writing them only on success would mean the failing case is the one
        with no evidence.
        """

        from mcp_server.providers.agent_workspace import (
            accountability,
            analysis,
            completion,
            ledger,
        )
        from mcp_server.providers.agent_workspace.integration_model import (
            load_workspace_model,
        )

        staging_dir = ctx.staging_dir
        coverage = ledger.read_ledger(staging_dir)
        ledger.write_summary(staging_dir, coverage)

        # The analysis layer: one written analysis per parsed group. This is
        # the mechanism the predecessor workflow had and this lane did not --
        # it required a per-group analysis before any document could be
        # written, so the prose layer could not be reached without passing
        # through the evidence layer. MEASURED on run 252: without it the
        # agent went from the index to finished documents in about 90 seconds
        # having opened 6 of 142 design files, and the ~21 KB of depth
        # guidance it had already read did not change that.
        analysis_coverage = analysis.read_analysis_coverage(staging_dir)
        analysis.write_analysis_artifact(staging_dir, analysis_coverage)

        # `load_report` accepts per item, so a single malformed journey costs
        # that journey rather than the whole investigation. An absent or
        # unreadable file yields no knowledge at all, which the gate reports as
        # `model_missing` when material candidates exist.
        # BOTH shapes: the original single `notes/integration_model.json` and
        # the per-integration `notes/integrations/*.json` the agent is now
        # taught to write. Reading only the monolith would make a correctly
        # written model invisible and report the estate as having none.
        # Named `model_report`, never the bare `report`. The coverage gauge's
        # own result is the thing this module must never branch on, and
        # `test_coverage.py` enforces that by grepping this source for a
        # conditional on that bare name. A shared name would either trip the
        # guard or, worse, teach a later reader that branching on it is fine.
        knowledge = None
        try:
            model_report = load_workspace_model(staging_dir)
            knowledge = model_report.knowledge
            if model_report.salvaged:
                logger.warning("agent-workspace: %s", model_report.salvaged)
            for record in model_report.rejected[:5]:
                logger.warning("agent-workspace: integration model: %s", record.describe())
        except Exception:  # noqa: BLE001 - a file defect is a gate finding, not a crash
            logger.warning("agent-workspace: the integration model could not be read")

        receipt = completion.reconcile(
            completion.read_receipt(staging_dir),
            ledger=coverage,
            knowledge=knowledge,
            scope_mode=settings.scope_mode,
        )
        report = accountability.evaluate_accountability(
            staging_dir=staging_dir,
            ledger=coverage,
            knowledge=knowledge,
            receipt=receipt,
            scope_mode=settings.scope_mode,
            published_documents=documents,
            analysis=analysis_coverage,
        )
        accountability.write_accountability_artifact(staging_dir, report)
        return report

    async def _publish(
        self,
        ctx: DocGenContext,
        log_cb: LogCallback,
        *,
        result: AgentRunResult,
        usage: UsageReceipt,
        settings: Any,
        started: float,
        evidence_digest: str,
        model_route: str,
        reference_expected: bool,
        baseline: dict[str, str],
    ) -> AgentResult:
        """Validate, measure, compile the topology, then assemble the deliverable.

        Ordering matters and is enforced here rather than inside any of the
        modules: the topology compiler and the coverage gauge both read
        ``output/`` and the staged ``facts/`` while they are still intact, and
        ``finalize_publication`` is what replaces the draft with a clean room
        built from the in-memory snapshot.

        The gate in front of all of it is ``result.status``. Publication and
        the swap run for ``completed`` only. ``incomplete``, ``cancelled`` and
        ``failed`` validate the draft for the log, keep it in staging for a
        resume, and leave the previously published documentation exactly where
        it was -- which is what this method did not do when it returned
        ``success=True`` for a provider that had reported failure.
        """

        from mcp_server.providers.agent_workspace import coverage as coverage_gauge
        from mcp_server.providers.agent_workspace import publish as publisher
        from mcp_server.providers.agent_workspace import registers
        from mcp_server.providers.agent_workspace import topology as topology_compiler

        cancelled = result.status == "cancelled"
        if not result.success:
            # No rebuild, no topology, no assembly: none of them can turn an
            # unfinished run into a finished one, and each of them costs time
            # an operator is waiting through. The draft is validated purely so
            # the log can say what survives for the resume.
            draft = publisher.publish_agent_output(
                ctx.staging_dir, expected_evidence_digest=evidence_digest
            )
            authored = _authored_documents(baseline, draft.documents)
            # What is LEFT, not just what was produced. An operator deciding
            # whether to resume needs the denominator, and the ledger is the
            # only artifact that has it: "37 of 142 designs accounted for"
            # answers the question that "5 documents written" does not.
            await log_cb(
                ctx.project_id,
                (
                    f"[agent-workspace] the agent reported {result.status} "
                    f"({result.stop_reason or result.error or 'no reason given'}); "
                    "nothing was published and the existing documentation is unchanged. "
                    f"The draft in staging holds {len(authored)} agent-authored "
                    f"document(s) that passed validation and is kept for a resume. "
                    f"{self._coverage_line(ctx.staging_dir)}"
                ),
                "warning",
            )
            reason = result.stop_reason or result.error or result.status
            return self._result(
                success=False,
                error=f"Agent run {result.status}: {reason}",
                usage=usage,
                started=started,
                model_route=model_route,
                session_id=result.session_id,
                has_warnings=True,
                cancelled=cancelled,
                outcome=result.status,
            )

        # ``output/reference`` is compiler-owned. Rebuilding it here prevents
        # accidental edits or omissions during the agent run and guarantees
        # that publication contains the complete parse-derived registers the
        # narrative links to. Deliberately NOT guarded by the directory being
        # present: ``output/`` is agent-writable, so ``rm -rf output/reference``
        # would otherwise turn the mandatory rebuild into a silent no-op. The
        # only guard is the builder's own receipt, which is Nexus state.
        if reference_expected:
            reference = registers.compile_reference_registers(ctx.staging_dir)
            await log_cb(
                ctx.project_id,
                (
                    "[agent-workspace] rebuilt complete factual reference: "
                    f"{reference.unit_count} unit(s), {reference.mapping_table_count} "
                    f"mapping table(s), {reference.mapping_row_count} exact mapping row(s)"
                ),
                "info",
            )

        outcome = publisher.publish_agent_output(
            ctx.staging_dir, expected_evidence_digest=evidence_digest
        )
        for warning in outcome.warnings[:10]:
            await log_cb(ctx.project_id, f"[agent-workspace] {warning}", "warning")

        if not outcome.ok:
            reason = "; ".join(outcome.blocking[:5])
            extra = f" (+{len(outcome.blocking) - 5} more)" if len(outcome.blocking) > 5 else ""
            message = f"Publication refused: {reason}{extra}"
            await log_cb(ctx.project_id, message, "error")
            return self._result(
                success=False,
                error=message,
                usage=usage,
                started=started,
                model_route=model_route,
                session_id=result.session_id,
                has_warnings=True,
            )

        # Compiler output cannot satisfy completion (plan decision 4). Every
        # accepted document under ``reference/`` was written by
        # ``compile_reference_registers`` moments ago, and every one of them
        # existed before the agent started; a run that produced nothing else
        # is a run that produced nothing.
        authored = _authored_documents(baseline, outcome.documents)
        if not authored:
            message = (
                "The agent authored no client document: every accepted file is "
                f"compiler-owned output under {_COMPILER_DOC_ROOT}/ or is unchanged "
                "from the staged workspace. Nothing was published and the existing "
                "documentation is unchanged."
            )
            await log_cb(ctx.project_id, message, "error")
            return self._result(
                success=False,
                error=message,
                usage=usage,
                started=started,
                model_route=model_route,
                session_id=result.session_id,
                has_warnings=True,
            )

        # THE SEMANTIC HANDSHAKE (plan section 7). A provider process ending
        # normally means the session stopped, not that the goal was completed
        # -- so `result.success` is necessary here and is NOT sufficient. The
        # run must also produce a completion receipt that survives
        # reconciliation against what is on disk, and clear the structural
        # accountability checks in plan section 8.
        #
        # Failing this is `incomplete`, never `failed`: the draft is intact,
        # the ledger records exactly which designs are outstanding, and a
        # resume continues from there. That distinction is the whole reason
        # `_result` carries a four-way `outcome` -- "stopped early with a
        # resumable draft" and "broke" are different things for an operator.
        gate = self._evaluate_accountability(
            ctx, settings=settings, documents=frozenset(outcome.documents)
        )
        for finding in gate.advisory[:10]:
            await log_cb(ctx.project_id, f"[agent-workspace] {finding.message}", "warning")
        if not gate.ok:
            reasons = "; ".join(finding.message for finding in gate.blocking[:5])
            extra = (
                f" (+{len(gate.blocking) - 5} more)" if len(gate.blocking) > 5 else ""
            )
            message = (
                f"The agent reported completion but the run is not accountable: {reasons}"
                f"{extra}. Nothing was published, the existing documentation is unchanged, "
                "and the draft is kept in staging for a resume."
            )
            await log_cb(ctx.project_id, message, "error")
            return self._result(
                success=False,
                error=message,
                usage=usage,
                started=started,
                model_route=model_route,
                session_id=result.session_id,
                has_warnings=True,
                outcome="incomplete",
            )
        await log_cb(ctx.project_id, f"[agent-workspace] {gate.log_line()}", "info")

        # A GAUGE. Nothing below reads it, and `test_coverage.py` pins that.
        # It is NOT the agent's coverage register (PRD 10.1, `notes/coverage.json`)
        # and it is not a completion criterion: PRD 10.2 and 15.3 both forbid
        # requiring a document per candidate or a scanner-string-to-prose match,
        # so a low number here is a prompt to look, never a verdict.
        reach = coverage_gauge.measure_documentation_reach(ctx.staging_dir, outcome.documents)
        coverage_gauge.write_reach_artifact(ctx.staging_dir, reach)
        await log_cb(ctx.project_id, f"[agent-workspace] {reach.log_line()}", "info")

        topology_outcome = topology_compiler.compile_agent_topology(
            project_id=ctx.project_id,
            staging_dir=ctx.staging_dir,
            scan_output_dir=config.intodoc_output_dir / ctx.project_id,
            document_names=outcome.published,
            model_name=model_route,
        )
        await log_cb(
            ctx.project_id,
            (
                f"[agent-workspace] topology via {topology_outcome.lane}: "
                f"{topology_outcome.systems} system(s), "
                f"{topology_outcome.data_streams} data stream(s)"
            )
            if topology_outcome.validated
            else (
                "[agent-workspace] no business topology overlay was published "
                "(documentation is unaffected): " + "; ".join(topology_outcome.errors[:2])
            ),
            "info" if topology_outcome.validated else "warning",
        )

        assembly = publisher.finalize_publication(ctx.staging_dir, outcome)
        for warning in assembly.warnings[:10]:
            await log_cb(ctx.project_id, f"[agent-workspace] {warning}", "warning")
        if not assembly.ok:
            reason = "; ".join(assembly.blocking[:5])
            extra = f" (+{len(assembly.blocking) - 5} more)" if len(assembly.blocking) > 5 else ""
            message = f"Clean-room assembly refused the publication: {reason}{extra}"
            await log_cb(ctx.project_id, message, "error")
            return self._result(
                success=False,
                error=message,
                usage=usage,
                started=started,
                model_route=model_route,
                session_id=result.session_id,
                has_warnings=True,
            )
        if assembly.excluded:
            # The signal that did not exist: ``rogue.md`` used to publish with
            # no log line at all. Naming what did NOT publish is how an
            # operator learns the agent wrote outside ``output/``.
            await log_cb(
                ctx.project_id,
                (
                    "[agent-workspace] not published (outside output/): "
                    + ", ".join(assembly.excluded[:20])
                ),
                "info",
            )
        await log_cb(
            ctx.project_id,
            (
                f"[agent-workspace] published {len(assembly.published)} document(s), "
                f"{len(authored)} of them agent-authored"
            ),
            "info",
        )
        return self._result(
            success=True,
            error=None,
            usage=usage,
            started=started,
            model_route=model_route,
            session_id=result.session_id,
            required=[name.removesuffix(".md") for name in assembly.published],
            has_warnings=bool(outcome.warnings) or bool(assembly.warnings),
        )


# --------------------------------------------------------------------------
# Attribution
#
# "Did the agent do any work?" is answered by comparing the documents that
# publish against the documents that were on disk before the agent started.
# Every weaker proxy has already failed in production: ``output/`` being
# non-empty, the accepted count clearing a floor, and the provider's own
# status were all true for run 250, which published eight compiler-authored
# files for an agent that wrote nothing.
# --------------------------------------------------------------------------


def _document_digests(output_dir: Path) -> dict[str, str]:
    """``output/``-relative path -> sha256 of the bytes, for every file there."""

    digests: dict[str, str] = {}
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return digests
    for path in sorted(output_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            digests[path.relative_to(output_dir).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        except OSError:
            continue
    return digests


def _authored_documents(baseline: dict[str, str], documents: dict[str, str]) -> tuple[str, ...]:
    """Accepted documents the agent created or changed outside ``reference/``.

    A document is agent-authored when it is a client document (not under the
    compiler-owned root) AND it is either absent from the pre-run snapshot or
    differs from it byte for byte.
    """

    authored: list[str] = []
    for name in sorted(documents):
        if Path(name).parts[:1] == (_COMPILER_DOC_ROOT,):
            continue
        digest = hashlib.sha256(documents[name].encode("utf-8")).hexdigest()
        if baseline.get(name) != digest:
            authored.append(name)
    return tuple(authored)
