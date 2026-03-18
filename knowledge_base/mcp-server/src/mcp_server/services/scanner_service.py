"""Scanner service — wraps `intodoc scan` subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from mcp_server.config import config
from mcp_server.services.log_manager import LogManager

logger = logging.getLogger(__name__)


class ScannerService:
    """Runs `intodoc scan` as a subprocess and captures output."""

    def __init__(self, log_manager: LogManager | None = None) -> None:
        self.log_manager = log_manager

    async def run_scan(
        self,
        project_id: str,
        repo_path: str,
        platform: str = "talend",
        output_dir: str | None = None,
    ) -> dict:
        """Run `intodoc scan` subprocess.

        Returns:
            dict with keys: success, job_count, scan_result_path, output_dir
        """
        out_dir = Path(output_dir) if output_dir else config.intodoc_output_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        # Resolve full path so .CMD/.BAT scripts work on Windows
        # (subprocess.Popen without shell=True only finds .exe by bare name)
        resolved_cmd = shutil.which(config.intodoc_command)
        if not resolved_cmd:
            msg = f"intodoc command not found on PATH: {config.intodoc_command}"
            logger.error(msg)
            if self.log_manager:
                await self.log_manager.emit(project_id, msg, level="error")
            return {
                "success": False,
                "error": msg,
                "job_count": 0,
                "scan_result_path": None,
                "output_dir": str(out_dir),
            }

        cmd = [
            resolved_cmd,
            "scan",
            str(repo_path),
            "--platform", platform,
            "--output-dir", str(out_dir),
        ]

        # Diagnostic logging
        logger.info(f"intodoc resolved path: {resolved_cmd}")
        logger.info(f"Python {sys.version}, platform={sys.platform}")
        logger.info(f"Running scan: {' '.join(cmd)}")
        if self.log_manager:
            await self.log_manager.emit(
                project_id,
                f"intodoc resolved: {resolved_cmd} | Python {sys.version}",
                level="info",
            )
            await self.log_manager.emit(project_id, f"Starting scan: {' '.join(cmd)}", level="info")

        try:
            # Use subprocess.Popen instead of asyncio.create_subprocess_exec
            # to avoid ProactorEventLoop pipe issues on Windows + Python 3.14.
            # Read stdout/stderr line-by-line via asyncio.to_thread for
            # real-time log streaming without blocking the event loop.
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                cwd=str(config.project_root),
            )

            output_lines: list[str] = []
            stderr_lines: list[str] = []

            async def _read_stream(
                stream, lines: list[str], tag: str,
            ) -> None:
                """Read lines from a pipe in a thread, emit each to logs."""
                while True:
                    line = await asyncio.to_thread(stream.readline)
                    if not line:
                        break
                    stripped = line.rstrip()
                    lines.append(stripped)
                    if self.log_manager:
                        if tag == "stderr":
                            await self.log_manager.emit(
                                project_id, f"[stderr] {stripped}", level="warning",
                            )
                        else:
                            await self.log_manager.emit(
                                project_id, stripped, level="info",
                            )

            # Read both streams concurrently (avoids pipe deadlock)
            await asyncio.gather(
                _read_stream(proc.stdout, output_lines, "stdout"),
                _read_stream(proc.stderr, stderr_lines, "stderr"),
            )

            returncode = await asyncio.to_thread(proc.wait)

            if returncode != 0:
                all_lines = output_lines + stderr_lines
                error_msg = "\n".join(all_lines[-10:])
                logger.error(f"Scan failed (exit {returncode}): {error_msg}")
                if self.log_manager:
                    await self.log_manager.emit(
                        project_id,
                        f"Scan failed with exit code {returncode}",
                        level="error",
                    )
                return {
                    "success": False,
                    "error": error_msg,
                    "job_count": 0,
                    "scan_result_path": None,
                    "output_dir": str(out_dir),
                }

            # Read scan result to get job count
            scan_result_path = out_dir / "scan_result.json"
            job_count = 0
            if scan_result_path.exists():
                try:
                    data = json.loads(scan_result_path.read_text(encoding="utf-8"))
                    job_count = len(data.get("jobs", []))
                except Exception:
                    pass

            logger.info(f"Scan completed: {job_count} jobs found")
            if self.log_manager:
                await self.log_manager.emit(
                    project_id, f"Scan completed: {job_count} jobs found", level="info",
                )

            return {
                "success": True,
                "job_count": job_count,
                "scan_result_path": str(scan_result_path),
                "output_dir": str(out_dir),
            }

        except FileNotFoundError:
            msg = f"intodoc command not found: {config.intodoc_command} (resolved: {resolved_cmd})"
            logger.error(msg)
            return {
                "success": False,
                "error": msg,
                "job_count": 0,
                "scan_result_path": None,
                "output_dir": str(out_dir),
            }
        except Exception as e:
            # Detailed error logging for diagnosis
            err_type = type(e).__name__
            err_repr = repr(e)
            err_tb = traceback.format_exc()
            extra = ""
            if isinstance(e, OSError):
                extra = f" | errno={e.errno}, winerror={getattr(e, 'winerror', 'N/A')}"
            msg = f"Scan error [{err_type}]: {err_repr}{extra}"
            logger.error(f"{msg}\n{err_tb}")
            if self.log_manager:
                await self.log_manager.emit(project_id, msg, level="error")
            return {
                "success": False,
                "error": msg,
                "job_count": 0,
                "scan_result_path": None,
                "output_dir": str(out_dir),
            }

    def read_scan_json(
        self,
        output_dir: str,
        section: str = "all",
    ) -> dict:
        """Read cached scan JSON, returning the requested section.

        Sections:
            meta, manifest, connections, contexts, jobs/{group}, all
        """
        scan_dir = Path(output_dir) / "scan"
        base_dir = Path(output_dir)

        # Fallback: if segmented files don't exist, read monolithic
        if not scan_dir.exists():
            mono = base_dir / "scan_result.json"
            if not mono.exists():
                return {"error": f"No scan result found in {output_dir}"}
            data = json.loads(mono.read_text(encoding="utf-8"))
            if section == "all":
                return {
                    "meta": {
                        "platform": data.get("platform"),
                        "repo_root": data.get("repo_root"),
                        "scanned_at": data.get("scanned_at"),
                        "stats": data.get("stats"),
                    },
                    "manifest": data.get("manifest"),
                    "connections": data.get("metadata_connections"),
                    "job_groups": self._group_jobs_summary(data.get("jobs", [])),
                }
            if section == "meta":
                return {
                    "platform": data.get("platform"),
                    "repo_root": data.get("repo_root"),
                    "scanned_at": data.get("scanned_at"),
                    "stats": data.get("stats"),
                }
            if section == "manifest":
                return data.get("manifest", {})
            if section == "connections":
                return data.get("metadata_connections", [])
            if section == "contexts":
                return data.get("context_groups", [])
            if section.startswith("jobs/"):
                group = section[5:]
                return [
                    j for j in data.get("jobs", [])
                    if (j.get("group") or "Global") == group
                ]
            return {"error": f"Unknown section: {section}"}

        # Segmented files
        try:
            if section == "meta":
                return json.loads((scan_dir / "meta.json").read_text(encoding="utf-8"))
            if section == "manifest":
                return json.loads((scan_dir / "manifest.json").read_text(encoding="utf-8"))
            if section == "connections":
                return json.loads((scan_dir / "connections.json").read_text(encoding="utf-8"))
            if section == "contexts":
                return json.loads((scan_dir / "contexts.json").read_text(encoding="utf-8"))
            if section.startswith("jobs/"):
                group = section[5:]
                jobs_dir = scan_dir / "jobs" / group
                if not jobs_dir.exists():
                    return {"error": f"Job group not found: {group}"}
                jobs = []
                for f in sorted(jobs_dir.glob("*.json")):
                    jobs.append(json.loads(f.read_text(encoding="utf-8")))
                return jobs
            if section == "all":
                meta = json.loads((scan_dir / "meta.json").read_text(encoding="utf-8"))
                manifest = json.loads((scan_dir / "manifest.json").read_text(encoding="utf-8"))
                connections = json.loads(
                    (scan_dir / "connections.json").read_text(encoding="utf-8")
                )
                # Build job group summary
                jobs_dir = scan_dir / "jobs"
                job_groups: list[dict] = []
                if jobs_dir.exists():
                    for group_dir in sorted(jobs_dir.iterdir()):
                        if group_dir.is_dir():
                            count = len(list(group_dir.glob("*.json")))
                            job_groups.append({
                                "group": group_dir.name,
                                "job_count": count,
                            })
                return {
                    "meta": meta,
                    "manifest": manifest,
                    "connections": connections,
                    "job_groups": job_groups,
                }
        except FileNotFoundError as e:
            return {"error": f"File not found: {e}"}
        except json.JSONDecodeError as e:
            return {"error": f"JSON parse error: {e}"}

        return {"error": f"Unknown section: {section}"}

    def get_chunk_definitions(self, output_dir: str) -> dict:
        """Compute chunk definitions from scan data.

        Groups jobs by their group field, then splits large groups into
        sub-chunks of ~30K tokens (matching intodoc's distiller logic).

        Returns:
            dict with "chunks" list and "total_chunks" count.
            Each chunk has: chunk_id, group, job_count, job_names, token_estimate.
        """
        scan_dir = Path(output_dir) / "scan"
        base_dir = Path(output_dir)

        # Collect per-group job info: {group: [(filename, size_bytes), ...]}
        group_jobs: dict[str, list[tuple[str, int]]] = {}

        if scan_dir.exists() and (scan_dir / "jobs").exists():
            jobs_dir = scan_dir / "jobs"
            for group_dir in sorted(jobs_dir.iterdir()):
                if not group_dir.is_dir():
                    continue
                group = group_dir.name
                jobs_in_group = []
                for f in sorted(group_dir.glob("*.json")):
                    size = f.stat().st_size
                    job_name = f.stem
                    jobs_in_group.append((job_name, size))
                if jobs_in_group:
                    group_jobs[group] = jobs_in_group
        else:
            # Fallback: monolithic scan_result.json
            mono = base_dir / "scan_result.json"
            if not mono.exists():
                return {"error": f"No scan result found in {output_dir}"}
            data = json.loads(mono.read_text(encoding="utf-8"))
            for job in data.get("jobs", []):
                group = job.get("group") or "Global"
                name = job.get("name", "unknown")
                size = len(json.dumps(job))
                group_jobs.setdefault(group, []).append((name, size))

        # Build chunks: split groups exceeding 120KB (~30K tokens at 4 chars/token)
        TOKEN_LIMIT = 30_000
        BYTE_LIMIT = TOKEN_LIMIT * 4  # ~120KB

        chunks: list[dict] = []
        for group in sorted(group_jobs.keys()):
            jobs = group_jobs[group]
            total_bytes = sum(size for _, size in jobs)

            if total_bytes <= BYTE_LIMIT:
                # Single chunk for the whole group
                chunks.append({
                    "chunk_id": f"{group}_all",
                    "group": group,
                    "job_count": len(jobs),
                    "job_names": [name for name, _ in jobs],
                    "token_estimate": total_bytes // 4,
                })
            else:
                # Split into sub-chunks
                current_jobs: list[tuple[str, int]] = []
                current_bytes = 0
                chunk_idx = 0

                for name, size in jobs:
                    if current_bytes + size > BYTE_LIMIT and current_jobs:
                        chunks.append({
                            "chunk_id": f"{group}_{chunk_idx}",
                            "group": group,
                            "job_count": len(current_jobs),
                            "job_names": [n for n, _ in current_jobs],
                            "token_estimate": current_bytes // 4,
                        })
                        chunk_idx += 1
                        current_jobs = []
                        current_bytes = 0

                    current_jobs.append((name, size))
                    current_bytes += size

                if current_jobs:
                    chunks.append({
                        "chunk_id": f"{group}_{chunk_idx}",
                        "group": group,
                        "job_count": len(current_jobs),
                        "job_names": [n for n, _ in current_jobs],
                        "token_estimate": current_bytes // 4,
                    })

        # Add estimated overhead to token_estimate (connections + contexts + routines + manifest)
        OVERHEAD_ESTIMATE = 5000  # ~5K tokens
        for chunk in chunks:
            chunk["token_estimate"] += OVERHEAD_ESTIMATE

        return {
            "chunks": chunks,
            "total_chunks": len(chunks),
            "total_jobs": sum(c["job_count"] for c in chunks),
        }

    def read_chunk_data(self, output_dir: str, chunk_id: str) -> dict:
        """Read job data for a specific chunk.

        The chunk_id format is "{group}_all" or "{group}_{index}".
        Uses the same chunking logic as get_chunk_definitions to determine
        which jobs belong to the requested chunk.

        Returns:
            dict with "chunk_id", "group", "jobs" (list of job dicts),
            "related_connections", "related_contexts", and "manifest" (filtered).
        """
        # First get chunk definitions to find the job names for this chunk
        chunk_defs = self.get_chunk_definitions(output_dir)
        if "error" in chunk_defs:
            return chunk_defs

        target_chunk = None
        for c in chunk_defs["chunks"]:
            if c["chunk_id"] == chunk_id:
                target_chunk = c
                break

        if not target_chunk:
            available = [c["chunk_id"] for c in chunk_defs["chunks"]]
            return {"error": f"Chunk not found: {chunk_id}. Available: {available}"}

        group = target_chunk["group"]
        job_names = set(target_chunk["job_names"])

        scan_dir = Path(output_dir) / "scan"
        base_dir = Path(output_dir)

        # Load job data
        jobs: list[dict] = []
        if scan_dir.exists() and (scan_dir / "jobs" / group).exists():
            jobs_dir = scan_dir / "jobs" / group
            for f in sorted(jobs_dir.glob("*.json")):
                if f.stem in job_names:
                    jobs.append(json.loads(f.read_text(encoding="utf-8")))
        else:
            mono = base_dir / "scan_result.json"
            if mono.exists():
                data = json.loads(mono.read_text(encoding="utf-8"))
                for job in data.get("jobs", []):
                    if job.get("name") in job_names:
                        jobs.append(job)

        # Load related connections (match via component metadata_connection_ref)
        conn_refs: set[str] = set()
        for job in jobs:
            for comp in job.get("components", []):
                ref = comp.get("metadata_connection_ref")
                if ref:
                    conn_refs.add(ref)

        connections: list[dict] = []
        if conn_refs:
            all_conns = self._load_connections(output_dir)
            connections = [c for c in all_conns if c.get("name") in conn_refs]

        # Load and filter manifest to only include this chunk's jobs
        manifest = self._load_filtered_manifest(output_dir, job_names)

        # Load related contexts -- filter by actual usage in manifest
        context_names_used: set[str] = set()
        for ctx_list in manifest.get("job_contexts_used", {}).values():
            if isinstance(ctx_list, list):
                context_names_used.update(ctx_list)
            elif isinstance(ctx_list, str):
                context_names_used.add(ctx_list)
        all_contexts = self._load_contexts(output_dir)
        related_contexts = [
            c for c in all_contexts
            if c.get("name") in context_names_used
        ]

        # Load related routines
        routine_refs: set[str] = set()
        for job in jobs:
            routine_refs.update(job.get("raw_routines", []))
        routines: list[dict] = []
        if routine_refs:
            all_routines = self._load_routines(output_dir)
            routines = [
                r for r in all_routines
                if r.get("name") in routine_refs and not r.get("is_system", False)
            ]

        response = {
            "chunk_id": chunk_id,
            "group": group,
            "job_count": len(jobs),
            "jobs": jobs,
            "related_connections": connections,
            "related_contexts": related_contexts,
            "related_routines": routines,
            "manifest": manifest,
        }

        # Dynamic sizing: ensure response fits within Claude Code's tool result limit
        MAX_RESPONSE_BYTES = 90_000  # ~22K tokens, safely under 25K limit

        serialized = json.dumps(response, separators=(",", ":"), default=str)

        if len(serialized) <= MAX_RESPONSE_BYTES:
            response["token_estimate"] = len(serialized) // 4
            return response

        # Level 1: Strip routines (usually standard library, least useful)
        response["related_routines"] = []
        response["_stripped"] = ["routines"]
        serialized = json.dumps(response, separators=(",", ":"), default=str)
        if len(serialized) <= MAX_RESPONSE_BYTES:
            response["token_estimate"] = len(serialized) // 4
            return response

        # Level 2: Strip raw_properties from components (already distilled)
        for job in response["jobs"]:
            for comp in job.get("components", []):
                comp.pop("raw_properties", None)
        response["_stripped"].append("raw_properties")
        serialized = json.dumps(response, separators=(",", ":"), default=str)
        if len(serialized) <= MAX_RESPONSE_BYTES:
            response["token_estimate"] = len(serialized) // 4
            return response

        # Level 3: Strip contexts (sub-agent can call fetch_json if needed)
        response["related_contexts"] = []
        response["_stripped"].append("contexts")
        serialized = json.dumps(response, separators=(",", ":"), default=str)
        if len(serialized) <= MAX_RESPONSE_BYTES:
            response["token_estimate"] = len(serialized) // 4
            return response

        # Level 4: Strip manifest (sub-agent can call fetch_json if needed)
        response["manifest"] = {}
        response["_stripped"].append("manifest")
        serialized = json.dumps(response, separators=(",", ":"), default=str)
        if len(serialized) <= MAX_RESPONSE_BYTES:
            response["token_estimate"] = len(serialized) // 4
            return response

        # Level 5: Truncate large params in components
        # (e.g., Salesforce components with 60KB+ param lists)
        COMP_PARAMS_MAX = 2000  # max chars for serialized params per component
        for job in response["jobs"]:
            for comp in job.get("components", []):
                params = comp.get("params")
                if not params:
                    continue
                param_str = json.dumps(params, separators=(",", ":"), default=str)
                if len(param_str) > COMP_PARAMS_MAX:
                    # Keep only essential params (connection refs, modes, schemas)
                    if isinstance(params, list):
                        essential = [
                            p for p in params
                            if isinstance(p, dict) and any(
                                kw in (p.get("name", "") or "").upper()
                                for kw in ("PROPERTY_TYPE", "REPOSITORY", "SCHEMA",
                                           "CONNECTION", "ACTION", "MODULE", "TABLE",
                                           "QUERY", "ENDPOINT", "URL", "MODE",
                                           "LABEL", "FORMAT")
                            )
                        ]
                        comp["params"] = essential
                        comp["_params_truncated"] = True
                    elif isinstance(params, dict):
                        for k, v in list(params.items()):
                            s = json.dumps(v, separators=(",", ":"), default=str) if not isinstance(v, str) else v
                            if len(s) > 200:
                                params[k] = s[:200] + "..."
        response["_stripped"].append("large_params")
        serialized = json.dumps(response, separators=(",", ":"), default=str)
        response["token_estimate"] = len(serialized) // 4
        return response

    def _load_connections(self, output_dir: str) -> list[dict]:
        """Load all metadata connections from scan data."""
        scan_dir = Path(output_dir) / "scan"
        try:
            if (scan_dir / "connections.json").exists():
                return json.loads(
                    (scan_dir / "connections.json").read_text(encoding="utf-8")
                )
        except (json.JSONDecodeError, OSError):
            pass
        # Fallback to monolithic
        mono = Path(output_dir) / "scan_result.json"
        if mono.exists():
            try:
                data = json.loads(mono.read_text(encoding="utf-8"))
                return data.get("metadata_connections", [])
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _load_contexts(self, output_dir: str) -> list[dict]:
        """Load all context groups from scan data."""
        scan_dir = Path(output_dir) / "scan"
        try:
            if (scan_dir / "contexts.json").exists():
                return json.loads(
                    (scan_dir / "contexts.json").read_text(encoding="utf-8")
                )
        except (json.JSONDecodeError, OSError):
            pass
        mono = Path(output_dir) / "scan_result.json"
        if mono.exists():
            try:
                data = json.loads(mono.read_text(encoding="utf-8"))
                return data.get("context_groups", [])
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _load_routines(self, output_dir: str) -> list[dict]:
        """Load all routines from scan data."""
        scan_dir = Path(output_dir) / "scan"
        try:
            if (scan_dir / "routines.json").exists():
                return json.loads(
                    (scan_dir / "routines.json").read_text(encoding="utf-8")
                )
        except (json.JSONDecodeError, OSError):
            pass
        mono = Path(output_dir) / "scan_result.json"
        if mono.exists():
            try:
                data = json.loads(mono.read_text(encoding="utf-8"))
                return data.get("routines", [])
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _load_filtered_manifest(
        self, output_dir: str, job_names: set[str],
    ) -> dict:
        """Load manifest and filter to only edges involving given jobs."""
        scan_dir = Path(output_dir) / "scan"
        manifest: dict = {}
        try:
            if (scan_dir / "manifest.json").exists():
                manifest = json.loads(
                    (scan_dir / "manifest.json").read_text(encoding="utf-8")
                )
            else:
                mono = Path(output_dir) / "scan_result.json"
                if mono.exists():
                    data = json.loads(mono.read_text(encoding="utf-8"))
                    manifest = data.get("manifest", {})
        except (json.JSONDecodeError, OSError):
            return {}

        if not manifest:
            return {}

        return {
            "job_calls": {
                k: v for k, v in manifest.get("job_calls", {}).items()
                if k in job_names
            },
            "job_called_by": {
                k: v for k, v in manifest.get("job_called_by", {}).items()
                if k in job_names
            },
            "job_connections_used": {
                k: v for k, v in manifest.get("job_connections_used", {}).items()
                if k in job_names
            },
            "connection_used_by": {
                k: v for k, v in manifest.get("connection_used_by", {}).items()
                if any(j in job_names for j in v)
            },
            "job_contexts_used": {
                k: v for k, v in manifest.get("job_contexts_used", {}).items()
                if k in job_names
            },
            "routines_used_by": {
                k: v for k, v in manifest.get("routines_used_by", {}).items()
                if any(j in job_names for j in v)
            },
            "job_groups": {
                k: v for k, v in manifest.get("job_groups", {}).items()
                if k in job_names
            },
        }

    def _group_jobs_summary(self, jobs: list[dict]) -> list[dict]:
        """Group jobs by their group field, return summary."""
        groups: dict[str, int] = {}
        for job in jobs:
            group = job.get("group") or "Global"
            groups[group] = groups.get(group, 0) + 1
        return [{"group": g, "job_count": c} for g, c in sorted(groups.items())]

    def get_analysis_prompts(self, platform: str = "talend") -> dict:
        """Read all analysis prompt files + best_practices.yaml for a platform.

        Returns a single dict with all prompts bundled together, eliminating
        the need for sub-agents to make 16+ individual Read calls.

        Prompt resolution order:
        1. Platform-specific prompts (platforms/{platform}/prompts/)
        2. Shared prompts (platforms/_shared/prompts/) -- only for passes
           not covered by the platform
        """
        platforms_root = (
            config.project_root
            / "intodoc"
            / "src"
            / "intodoc"
            / "platforms"
        )
        platform_dir = platforms_root / platform
        shared_dir = platforms_root / "_shared"

        # Start with shared prompts as base layer
        prompts: dict[str, str] = {}
        shared_prompts_dir = shared_dir / "prompts"
        if shared_prompts_dir.exists():
            for f in sorted(shared_prompts_dir.glob("*.txt")):
                try:
                    prompts[f.stem] = f.read_text(encoding="utf-8")
                except OSError:
                    prompts[f.stem] = f"(error reading {f.name})"

        # Override with platform-specific prompts
        platform_prompts_dir = platform_dir / "prompts"
        if platform_prompts_dir.exists():
            for f in sorted(platform_prompts_dir.glob("*.txt")):
                try:
                    prompts[f.stem] = f.read_text(encoding="utf-8")
                except OSError:
                    prompts[f.stem] = f"(error reading {f.name})"

        if not prompts:
            return {"error": f"No prompts found for platform '{platform}'"}

        # Include best_practices.yaml (platform-specific, then shared fallback)
        bp_file = platform_dir / "best_practices.yaml"
        if not bp_file.exists():
            bp_file = shared_dir / "best_practices.yaml"
        if bp_file.exists():
            try:
                prompts["best_practices"] = bp_file.read_text(encoding="utf-8")
            except OSError:
                pass

        return {
            "platform": platform,
            "prompt_count": len(prompts),
            "prompts": prompts,
        }

    # Reference doc -> agent group mapping (consolidated 12-doc structure)
    _AGENT_REFERENCE_DOCS: dict[str, list[str]] = {
        "Core": [
            "00_EXECUTIVE_SUMMARY", "01_PROJECT_OVERVIEW",
            "02_ARCHITECTURE_INVENTORY", "03_DATA_FLOWS_LINEAGE",
        ],
        "Business+Ops": [
            "04_BUSINESS_LOGIC", "05_OPERATIONS_RISK",
            "06_ENVIRONMENT_EXTERNAL",
        ],
        "Patterns+User": [
            "07_SECURITY_PERFORMANCE", "08_PATTERNS_STANDARDS",
            "09_RUNBOOK", "10_ONBOARDING_FAQ",
            "11_MIGRATION_ASSESSMENT",
        ],
    }

    def get_reference_docs(self, agent_group: str) -> dict:
        """Read reference docs for a specific doc agent group.

        Returns the content of matching reference docs from intodoc/reference-docs/,
        eliminating the need for sub-agents to make individual Read calls.
        """
        ref_dir = config.project_root / "intodoc" / "reference-docs"
        if not ref_dir.exists():
            return {"error": f"Reference docs directory not found: {ref_dir}"}

        doc_names = self._AGENT_REFERENCE_DOCS.get(agent_group)
        if doc_names is None:
            return {
                "error": f"Unknown agent group: {agent_group}",
                "available_groups": list(self._AGENT_REFERENCE_DOCS.keys()),
            }

        docs: dict[str, str] = {}
        for name in doc_names:
            md_file = ref_dir / f"{name}.md"
            if md_file.exists():
                try:
                    docs[name] = md_file.read_text(encoding="utf-8")
                except OSError:
                    docs[name] = f"(error reading {md_file.name})"

        return {
            "agent_group": agent_group,
            "doc_count": len(docs),
            "docs": docs,
        }
