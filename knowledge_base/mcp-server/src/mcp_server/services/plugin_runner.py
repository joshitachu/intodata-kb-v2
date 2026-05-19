"""PluginRunner — executes plugin pack steps against a DocGenContext.

Each step is either:
  llm     — single LLM call: prompt file rendered with context, response stored
  harness — delegates to the wrapped inner DocGenerationProvider (full pipeline)

PluginRunnerProvider implements DocGenerationProvider so it can be used
anywhere the harness is used, selected via the plugin_pack_id on a project.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback
from mcp_server.providers.custom.models import ModelOptions, ModelResult
from mcp_server.providers.custom.providers import ModelProvider
from mcp_server.services.plugin_schema import PluginPackManifest, PluginStep, load_plugin_manifest


class PluginRunnerProvider(DocGenerationProvider):
    """DocGenerationProvider that executes a plugin pack step sequence.

    For 'harness' steps the inner_provider (CustomHarnessProvider) runs the full
    multi-phase pipeline.  For 'llm' steps a single model call is made via
    model_provider, with outputs from prior steps injected as context.
    """

    def __init__(
        self,
        manifest: PluginPackManifest,
        pack_dir: Path,
        inner_provider: DocGenerationProvider,
        model_provider: ModelProvider | None = None,
    ) -> None:
        self.manifest = manifest
        self.pack_dir = pack_dir
        self.inner_provider = inner_provider
        self.model_provider = model_provider

    @classmethod
    def from_path(
        cls,
        pack_dir: Path,
        inner_provider: DocGenerationProvider,
        model_provider: ModelProvider | None = None,
    ) -> "PluginRunnerProvider":
        manifest = load_plugin_manifest(pack_dir)
        return cls(manifest, pack_dir, inner_provider, model_provider)

    async def run(self, ctx: DocGenContext, log_cb: LogCallback) -> AgentResult:
        step_outputs: dict[str, str] = {}
        total_in = 0
        total_out = 0
        t_start = time.monotonic()

        for step in self.manifest.steps:
            await log_cb(ctx.project_id, f"[STEP:start] {step.id}", "info")
            try:
                if step.type == "harness":
                    result = await self._run_harness_step(step, ctx, log_cb)
                else:
                    result = await self._run_llm_step(step, ctx, step_outputs, log_cb)

                if not result.success:
                    err = result.raw_error or "(no detail)"
                    await log_cb(ctx.project_id, f"[STEP:error] {step.id}: {err}", "error")
                    return AgentResult(success=False, error=f"Step '{step.id}' failed: {err}")

                step_outputs[step.id] = result.text
                total_in += result.input_tokens
                total_out += result.output_tokens
                await log_cb(
                    ctx.project_id,
                    f"[STEP:done] {step.id} ({result.input_tokens}in/{result.output_tokens}out tokens)",
                    "info",
                )

            except Exception as exc:
                await log_cb(ctx.project_id, f"[STEP:error] {step.id}: {exc}", "error")
                return AgentResult(success=False, error=f"Step '{step.id}' raised: {exc}")

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        await log_cb(
            ctx.project_id,
            f"[PLUGIN:done] {len(self.manifest.steps)} step(s) completed "
            f"({total_in}in/{total_out}out tokens, {elapsed_ms}ms)",
            "info",
        )
        return AgentResult(
            success=True,
            input_tokens=total_in,
            output_tokens=total_out,
            duration_ms=elapsed_ms,
            model_name=self.manifest.name,
        )

    async def _run_harness_step(
        self,
        step: PluginStep,
        ctx: DocGenContext,
        log_cb: LogCallback,
    ) -> ModelResult:
        result = await self.inner_provider.run(ctx, log_cb)
        return ModelResult(
            success=result.success,
            text="",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            raw_error=result.error,
        )

    async def _run_llm_step(
        self,
        step: PluginStep,
        ctx: DocGenContext,
        step_outputs: dict[str, str],
        log_cb: LogCallback,
    ) -> ModelResult:
        prompt_path = self.pack_dir / step.prompt_file  # type: ignore[arg-type]
        if not prompt_path.exists():
            return ModelResult(
                success=False,
                raw_error=f"Prompt file not found: {prompt_path}",
            )

        if self.model_provider is None:
            return ModelResult(
                success=False,
                raw_error="No model_provider configured for llm steps",
            )

        prompt = prompt_path.read_text(encoding="utf-8")

        if step.context_from:
            context_parts: list[str] = []
            for dep_id in step.context_from:
                dep_output = step_outputs.get(dep_id)
                if dep_output:
                    context_parts.append(f"--- Output of step '{dep_id}' ---\n{dep_output}")
            if context_parts:
                prompt = "\n\n".join(context_parts) + "\n\n---\n\n" + prompt

        options = ModelOptions(max_tokens=8192, temperature=0.1)
        return await self.model_provider.complete(prompt, options=options)


def build_plugin_runner(
    pack_dir: Path,
    inner_provider: DocGenerationProvider,
    model_provider: ModelProvider | None = None,
) -> PluginRunnerProvider:
    """Convenience factory — load manifest and construct PluginRunnerProvider."""
    return PluginRunnerProvider.from_path(pack_dir, inner_provider, model_provider)
