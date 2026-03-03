"""MCP server with 8 tools for code intelligence orchestration."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.log_manager import LogManager
from mcp_server.services import memory_service
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.services.scanner_service import ScannerService

logger = logging.getLogger(__name__)

server = Server("intodoc")
log_manager = LogManager()
scanner_service = ScannerService(log_manager=log_manager)
rag_bridge = RagBridge(log_manager=log_manager)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="add_repo",
            description=(
                "Register a project for scanning. Accepts a local path, git URL, "
                "or 'zip' (for previously uploaded ZIPs). Optionally triggers first scan."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Git URL, local path, or 'zip'",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Unique project identifier (e.g. 'vaxxinova')",
                    },
                    "platform": {
                        "type": "string",
                        "description": "Integration platform (default: talend)",
                        "default": "talend",
                    },
                },
                "required": ["url", "project_id"],
            },
        ),
        Tool(
            name="fetch_json",
            description=(
                "Returns cached scan JSON for a project. Use section parameter to "
                "request specific parts: 'all' (default), 'meta', 'manifest', "
                "'connections', 'contexts', 'jobs/{group}'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "section": {
                        "type": "string",
                        "description": (
                            "Section to fetch: 'all', 'meta', 'manifest', "
                            "'connections', 'contexts', 'jobs/{group}'"
                        ),
                        "default": "all",
                    },
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="trigger_scan",
            description=(
                "Runs `intodoc scan` subprocess for a project, updates cache. "
                "Returns scan summary."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "force": {
                        "type": "boolean",
                        "description": "Force re-scan even if cache exists",
                        "default": False,
                    },
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="get_scan_status",
            description=(
                "Returns last scan time, cache age, job count, and current status "
                "for a project."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="insert_to_rag",
            description=(
                "Push documents to the RAG pipeline for vector indexing. "
                "If docs list provided, pushes those. Otherwise reads .md files "
                "from the project output directory."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "docs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["filename", "content"],
                        },
                        "description": "Optional list of docs to push directly",
                    },
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="list_projects",
            description="Returns all registered projects with status, last scan time, job/doc counts.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_chunks",
            description=(
                "Returns chunk definitions for a project's scan data. "
                "Each chunk is an LLM-sized group of jobs (~30K tokens). "
                "Returns chunk_id, group, job_count, job_names, token_estimate "
                "for each chunk. Use fetch_chunk to load actual job data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="fetch_chunk",
            description=(
                "Returns full job data for a specific chunk. Includes jobs, "
                "related connections, contexts, routines, and filtered manifest. "
                "Use get_chunks first to see available chunk IDs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "chunk_id": {
                        "type": "string",
                        "description": (
                            "Chunk ID from get_chunks (e.g. 'DEU_all', 'BRA_0')"
                        ),
                    },
                },
                "required": ["project_id", "chunk_id"],
            },
        ),
        Tool(
            name="get_analysis_prompts",
            description=(
                "Returns all analysis prompt files and best_practices.yaml "
                "bundled as a single dict. Eliminates the need for sub-agents "
                "to make 16+ individual Read calls for prompt files."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "description": "Integration platform (default: talend)",
                        "default": "talend",
                    },
                },
            },
        ),
        Tool(
            name="get_reference_docs",
            description=(
                "Returns reference doc content for a specific doc agent group. "
                "Groups: Overview, Architecture, Data, Business, Operations, "
                "Config, External, Patterns, UserDocs, Preventive. "
                "Eliminates the need for sub-agents to make individual Read calls."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_group": {
                        "type": "string",
                        "description": (
                            "Doc agent group name: Overview, Architecture, Data, "
                            "Business, Operations, Config, External, Patterns, "
                            "UserDocs, Preventive"
                        ),
                    },
                },
                "required": ["agent_group"],
            },
        ),
        Tool(
            name="recall_process_memory",
            description=(
                "Recall cross-project lessons and best practices from Hindsight "
                "memory. Call before doc generation to leverage past experience."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to recall (e.g. 'common doc generation errors')",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="recall_project_memory",
            description=(
                "Recall prior analysis findings for a specific project. "
                "Call before re-scanning to see what changed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "query": {
                        "type": "string",
                        "description": "What to recall about this project",
                    },
                },
                "required": ["project_id", "query"],
            },
        ),
        Tool(
            name="retain_findings",
            description=(
                "Store analysis findings for a project into Hindsight memory. "
                "Call after group analysis or at end of doc generation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "findings": {
                        "type": "string",
                        "description": "The findings text to store",
                    },
                    "context": {
                        "type": "string",
                        "description": "Context label (e.g. 'group-summary', 'run-summary')",
                        "default": "analysis-findings",
                    },
                },
                "required": ["project_id", "findings"],
            },
        ),
        Tool(
            name="retain_process_lesson",
            description=(
                "Store an operational lesson learned during doc generation. "
                "Call when you discover errors, optimizations, or workflow improvements."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "lesson": {
                        "type": "string",
                        "description": "The lesson to remember",
                    },
                    "context": {
                        "type": "string",
                        "description": "Context label (e.g. 'error-recovery', 'optimization')",
                        "default": "process-lesson",
                    },
                },
                "required": ["lesson"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, separators=(",", ":"), default=str))]
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, separators=(",", ":")))]


async def _dispatch(name: str, args: dict) -> dict:
    # Strip whitespace from project_id (MCP args bypass Pydantic validation)
    if "project_id" in args and isinstance(args["project_id"], str):
        args["project_id"] = args["project_id"].strip()

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)

        if name == "add_repo":
            return await _add_repo(registry, db, args)
        elif name == "fetch_json":
            return await _fetch_json(registry, args)
        elif name == "trigger_scan":
            return await _trigger_scan(registry, db, args)
        elif name == "get_scan_status":
            return await _get_scan_status(registry, args)
        elif name == "insert_to_rag":
            return await _insert_to_rag(registry, db, args)
        elif name == "list_projects":
            return await _list_projects(registry)
        elif name == "get_chunks":
            return await _get_chunks(registry, args)
        elif name == "fetch_chunk":
            return await _fetch_chunk(registry, args)
        elif name == "get_analysis_prompts":
            return _get_analysis_prompts(args)
        elif name == "get_reference_docs":
            return _get_reference_docs(args)
        elif name == "recall_process_memory":
            return await _recall_process_memory(args)
        elif name == "recall_project_memory":
            return await _recall_project_memory(args)
        elif name == "retain_findings":
            return await _retain_findings(args)
        elif name == "retain_process_lesson":
            return await _retain_process_lesson(args)
        else:
            return {"error": f"Unknown tool: {name}"}


async def _add_repo(registry: ProjectRegistry, db, args: dict) -> dict:
    url = args["url"]
    project_id = args["project_id"]
    platform = args.get("platform", "talend")

    # Determine source type and local path
    path = Path(url)
    if path.exists():
        source_type = "local"
        local_path = str(path.resolve())
        repo_url = None
    elif url.startswith(("http://", "https://", "git@")):
        # Clone the repo
        source_type = "git"
        repo_url = url
        clone_dir = config.repos_dir / project_id
        if not clone_dir.exists():
            import subprocess
            subprocess.run(
                ["git", "clone", url, str(clone_dir)],
                check=True,
                capture_output=True,
            )
        local_path = str(clone_dir)
    elif url == "zip":
        source_type = "zip"
        local_path = str(config.repos_dir / project_id)
        repo_url = None
        if not Path(local_path).exists():
            return {"error": f"No unpacked ZIP found at {local_path}. Upload via REST endpoint first."}
    else:
        return {"error": f"Invalid URL/path: {url}"}

    project = await registry.add_project(
        project_id=project_id,
        local_path=local_path,
        repo_url=repo_url,
        platform=platform,
        source_type=source_type,
    )
    await db.commit()

    return {
        "status": "registered",
        "project_id": project.id,
        "local_path": project.local_path,
        "platform": project.platform,
        "source_type": project.source_type,
    }


async def _fetch_json(registry: ProjectRegistry, args: dict) -> dict:
    project_id = args["project_id"]
    section = args.get("section", "all")

    project = await registry.get_project(project_id)
    if not project:
        return {"error": f"Project not found: {project_id}"}

    output_dir = project.output_dir or str(config.intodoc_output_dir)
    return scanner_service.read_scan_json(output_dir, section=section)


async def _trigger_scan(registry: ProjectRegistry, db, args: dict) -> dict:
    project_id = args["project_id"]
    project = await registry.get_project(project_id)
    if not project:
        return {"error": f"Project not found: {project_id}"}

    await registry.update_status(project_id, "scanning")
    run = await registry.create_scan_run(project_id, triggered_by="mcp")
    await db.commit()

    # Use per-project output directory to avoid overwriting scans
    project_output_dir = config.intodoc_output_dir / project_id
    
    log_manager.reset_log_file(project_id)
    result = await scanner_service.run_scan(
        project_id=project_id,
        repo_path=project.local_path,
        platform=project.platform,
        output_dir=str(project_output_dir),
    )

    if result["success"]:
        await registry.update_scan_result(
            project_id,
            scan_result_path=result["scan_result_path"],
            output_dir=result["output_dir"],
            job_count=result["job_count"],
        )
        await registry.complete_scan_run(
            run.id, status="completed", job_count=result["job_count"]
        )
    else:
        await registry.update_status(project_id, "error", error_message=result.get("error"))
        await registry.complete_scan_run(run.id, status="failed")

    await db.commit()

    return {
        "project_id": project_id,
        "status": "completed" if result["success"] else "failed",
        "job_count": result["job_count"],
        "scan_file": result.get("scan_result_path"),
    }


async def _get_scan_status(registry: ProjectRegistry, args: dict) -> dict:
    project_id = args["project_id"]
    project = await registry.get_project(project_id)
    if not project:
        return {"error": f"Project not found: {project_id}"}

    cache_age = None
    if project.last_scan_at:
        delta = datetime.datetime.now(datetime.timezone.utc) - project.last_scan_at
        cache_age = round(delta.total_seconds() / 60, 1)

    return {
        "project_id": project.id,
        "status": project.status,
        "last_scan_at": str(project.last_scan_at) if project.last_scan_at else None,
        "cache_age_minutes": cache_age,
        "job_count": project.job_count,
        "doc_count": project.doc_count,
        "error_message": project.error_message,
    }


async def _insert_to_rag(registry: ProjectRegistry, db, args: dict) -> dict:
    project_id = args["project_id"]
    docs = args.get("docs")

    project = await registry.get_project(project_id)
    if not project:
        return {"error": f"Project not found: {project_id}"}

    output_dir = project.output_dir or str(config.intodoc_output_dir)
    result = await rag_bridge.push_docs(
        project_id=project_id,
        docs=docs,
        output_dir=output_dir if not docs else None,
    )

    if result["pushed"] > 0:
        await registry.update_rag_push(project_id)
        await db.commit()

    return {
        "project_id": project_id,
        "pushed": result["pushed"],
        "errors": result["errors"],
    }


async def _list_projects(registry: ProjectRegistry) -> dict:
    projects = await registry.list_projects()
    return {
        "projects": [
            {
                "id": p.id,
                "display_name": p.display_name,
                "platform": p.platform,
                "status": p.status,
                "source_type": p.source_type,
                "last_scan_at": str(p.last_scan_at) if p.last_scan_at else None,
                "job_count": p.job_count,
                "doc_count": p.doc_count,
            }
            for p in projects
        ],
        "total": len(projects),
    }


async def _get_chunks(registry: ProjectRegistry, args: dict) -> dict:
    project_id = args["project_id"]
    project = await registry.get_project(project_id)
    if not project:
        return {"error": f"Project not found: {project_id}"}

    output_dir = project.output_dir or str(config.intodoc_output_dir)
    return scanner_service.get_chunk_definitions(output_dir)


async def _fetch_chunk(registry: ProjectRegistry, args: dict) -> dict:
    project_id = args["project_id"]
    chunk_id = args["chunk_id"]
    project = await registry.get_project(project_id)
    if not project:
        return {"error": f"Project not found: {project_id}"}

    output_dir = project.output_dir or str(config.intodoc_output_dir)
    return scanner_service.read_chunk_data(output_dir, chunk_id)


def _get_analysis_prompts(args: dict) -> dict:
    platform = args.get("platform", "talend")
    return scanner_service.get_analysis_prompts(platform)


def _get_reference_docs(args: dict) -> dict:
    agent_group = args["agent_group"]
    return scanner_service.get_reference_docs(agent_group)


async def _recall_process_memory(args: dict) -> dict:
    if not config.hindsight_enabled:
        return {"note": "Hindsight memory is disabled"}
    query = args["query"]
    await memory_service.ensure_process_bank()
    memories = await memory_service.recall_process_memory(query)
    return {"memories": memories, "count": len(memories)}


async def _recall_project_memory(args: dict) -> dict:
    if not config.hindsight_enabled:
        return {"note": "Hindsight memory is disabled"}
    project_id = args["project_id"]
    query = args["query"]
    await memory_service.ensure_project_bank(project_id)
    memories = await memory_service.recall_project_memory(project_id, query)
    return {"memories": memories, "count": len(memories)}


async def _retain_findings(args: dict) -> dict:
    if not config.hindsight_enabled:
        return {"note": "Hindsight memory is disabled"}
    project_id = args["project_id"]
    findings = args["findings"]
    context = args.get("context", "analysis-findings")
    await memory_service.ensure_project_bank(project_id)
    await memory_service.retain_project_findings(project_id, findings, context=context)
    return {"status": "retained", "project_id": project_id}


async def _retain_process_lesson(args: dict) -> dict:
    if not config.hindsight_enabled:
        return {"note": "Hindsight memory is disabled"}
    lesson = args["lesson"]
    context = args.get("context", "process-lesson")
    await memory_service.ensure_process_bank()
    await memory_service.retain_process_lesson(lesson, context=context)
    return {"status": "retained"}


def run_mcp_server() -> None:
    """Entry point for MCP stdio server."""
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
