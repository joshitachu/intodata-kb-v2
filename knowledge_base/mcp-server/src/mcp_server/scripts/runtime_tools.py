#!/usr/bin/env python3
"""Query the live runtime estate from inside a documentation run.

Usage (from the staging directory)::

    python3 _tools/runtime_tools.py list
    python3 _tools/runtime_tools.py call <tool_name> '<json arguments>'
    python3 _tools/runtime_tools.py elastic <probe_name> '<json body>'

WHY THIS EXISTS.  The CLI documentation lane used to receive runtime facts as
one pre-computed markdown rollup and nothing else, so a project with tens of
thousands of mirrored executions shipped documents that mentioned none of them:
a rollup answers the questions it was built to answer and no others.  This is
the other half -- the agent asks its own questions, of the same mirror, while it
is writing.

STANDALONE BY DESIGN.  ``services/claude_service.py`` copies this file into
``{staging}/_tools/runtime_tools.py`` and the agent runs it with a bare
``python3`` in an environment that is not the server's virtualenv.  It must
therefore import nothing from ``mcp_server`` and nothing outside the standard
library -- no ``httpx``, no ``pydantic``, no ``requests``.

IDENTITY IS NOT THE AGENT'S TO CHOOSE.  ``project_id`` is injected here, from
configuration the agent did not write, and any ``project_id`` in agent-supplied
JSON is dropped before the request is built.  Two sources provide it, in this
order:

1. ``_tools/runtime_tools.json`` beside this script -- written per run, holding
   ONLY non-secret values (base URL, project id, staging dir).
2. the environment (``NEXUS_RAG_BASE_URL``, ``NEXUS_PROJECT_ID``,
   ``NEXUS_STAGING_DIR``).

The sidecar wins because the environment is process-global on the server side:
two documentation runs in one server process overwrite each other's
``NEXUS_PROJECT_ID``, and the failure mode of that race is one project's
documents quoting another project's runtime data.  A per-run file cannot race.
The bearer key is the exception -- it is read from ``NEXUS_INGEST_KEY`` only and
is never written to disk, because staging is published.

THE KEY IS NEVER PRINTED.  Every byte this script writes to stdout or stderr
passes through :func:`_scrub`, and no traceback is ever emitted: an unexpected
exception is reported as a one-line redacted reason.

EXIT CODES.  ``0`` the request completed and its result is on stdout (including
a result whose own ``success`` field is ``false`` -- that is an answer, not a
failure of this script).  ``2`` the request did not complete: bad configuration,
transport error, non-2xx status, unparseable body.  The reason is on stderr and
whatever the server did return is still printed to stdout.

EVERY CALL IS LEDGERED.  One JSON line per invocation in
``{staging}/_assessment/runtime_probe_ledger.jsonl``: timestamp, command, tool,
arguments, outcome and a SHORT digest of the result (top-level keys and row
counts, never payloads).  An adaptive agent asks different questions every run,
so without this ledger two runs of one project are not comparable and nobody can
say afterwards what the documents were actually allowed to see.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

#: Non-secret per-run configuration, written beside this script by the
#: coordinator.  See the module docstring for why it outranks the environment.
SIDECAR_NAME = "runtime_tools.json"

#: Server contract.  These three paths are fixed; do not invent variants.
TOOLS_PATH = "/api/v1/runtime/tools"
ELASTIC_PROBE_PATH = "/api/v1/runtime/elastic/probe"

#: Documented for the agent's benefit only.  Deliberately NOT enforced as a
#: whitelist: a client-side copy of the server's probe list is a second contract
#: that drifts, and an unknown name already fails loudly (404) with the server's
#: own message.
KNOWN_PROBES = (
    "describe-fields",
    "list-indices",
    "count-matching",
    "size-distribution",
)

LEDGER_RELATIVE_PATH = ("_assessment", "runtime_probe_ledger.jsonl")

#: Recorded arguments are capped: a probe body can carry a long query and the
#: ledger is a record of what was ASKED, not a copy of it.
MAX_LEDGER_ARGUMENT_CHARS = 2_000

#: At most this many top-level keys are named in a digest.
MAX_DIGEST_KEYS = 24

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_STDOUT_BYTES = 120_000

REDACTED = "***"


class ConfigurationError(RuntimeError):
    """The script was invoked without enough configuration to build a request.

    Its message names the missing setting and never its value -- a "missing
    NEXUS_INGEST_KEY" diagnostic that helpfully prints the key it did find is
    the defect this whole module is careful about.
    """


class TransportError(RuntimeError):
    """The request did not complete, or completed with a status we cannot use.

    ``body`` is whatever the server returned (already scrubbed and bounded), so
    the agent can read a 404's explanation instead of guessing.
    """

    def __init__(self, reason: str, body: object = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.body = body


# ───────────────────────────────────────────────────────────────────────────
# Redaction
# ───────────────────────────────────────────────────────────────────────────

#: The bearer key, captured at settings load so :func:`_scrub` can strip it from
#: any string on its way out -- including strings this module never composed,
#: such as an exception message from the standard library.
_SECRETS: list[str] = []


def _register_secret(value: str) -> None:
    """Remember a value that must never be printed.

    Values shorter than 8 characters are ignored: scrubbing a 3-character string
    out of every message would mangle unrelated output, and a 3-character bearer
    key is not a secret anybody is protecting.
    """
    text = (value or "").strip()
    if len(text) >= 8 and text not in _SECRETS:
        _SECRETS.append(text)


def _scrub(text: object) -> str:
    """Return ``text`` as a string with every registered secret replaced."""
    result = text if isinstance(text, str) else str(text)
    for secret in _SECRETS:
        if secret in result:
            result = result.replace(secret, REDACTED)
    return result


def _out(text: str) -> None:
    print(_scrub(text))


def _err(text: str) -> None:
    print(_scrub(text), file=sys.stderr)


# ───────────────────────────────────────────────────────────────────────────
# Settings
# ───────────────────────────────────────────────────────────────────────────


class Settings:
    """Resolved, non-negotiable request identity for this invocation."""

    __slots__ = ("base_url", "ingest_key", "project_id", "staging_dir",
                 "timeout_s", "max_stdout_bytes", "source")

    def __init__(
        self,
        *,
        base_url: str,
        ingest_key: str,
        project_id: str,
        staging_dir: Path,
        timeout_s: float,
        max_stdout_bytes: int,
        source: str,
    ) -> None:
        self.base_url = base_url
        self.ingest_key = ingest_key
        self.project_id = project_id
        self.staging_dir = staging_dir
        self.timeout_s = timeout_s
        self.max_stdout_bytes = max_stdout_bytes
        self.source = source

    @property
    def ledger_path(self) -> Path:
        return self.staging_dir.joinpath(*LEDGER_RELATIVE_PATH)

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"


def _read_sidecar(script_dir: Path) -> dict:
    """Read the per-run sidecar, or ``{}`` when it is absent or unreadable.

    A malformed sidecar falls back to the environment rather than failing the
    call: the agent still gets an answer, and the fallback is recorded in the
    ledger's ``config_source`` so nobody has to guess which identity was used.
    """
    path = script_dir / SIDECAR_NAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        _err(f"runtime_tools: {path.name} is not valid JSON; using the environment instead")
        return {}
    return data if isinstance(data, dict) else {}


def _positive_float(raw: str | None, default: float) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def load_settings(script_path: Path | None = None) -> Settings:
    """Resolve request identity from the sidecar, then the environment."""
    script_dir = (script_path or Path(__file__).resolve()).parent
    sidecar = _read_sidecar(script_dir)

    def _pick(sidecar_key: str, env_key: str) -> tuple[str, str]:
        value = str(sidecar.get(sidecar_key) or "").strip()
        if value:
            return value, "sidecar"
        return str(os.environ.get(env_key) or "").strip(), "environment"

    base_url, base_source = _pick("base_url", "NEXUS_RAG_BASE_URL")
    project_id, project_source = _pick("project_id", "NEXUS_PROJECT_ID")
    staging_raw, _staging_source = _pick("staging_dir", "NEXUS_STAGING_DIR")

    ingest_key = str(os.environ.get("NEXUS_INGEST_KEY") or "").strip()
    _register_secret(ingest_key)

    missing = [
        name
        for name, value in (
            ("NEXUS_RAG_BASE_URL", base_url),
            ("NEXUS_PROJECT_ID", project_id),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(
            "runtime tools are not configured for this run: missing "
            + ", ".join(missing)
            + f" (looked in {script_dir / SIDECAR_NAME} and the environment). "
            "No runtime query can be attributed to a project without it."
        )

    # The script lives at {staging}/_tools/runtime_tools.py, so its parent's
    # parent is the staging directory -- the last resort when neither the
    # sidecar nor the environment says where to write the ledger.
    staging_dir = Path(staging_raw) if staging_raw else script_dir.parent

    return Settings(
        base_url=base_url.rstrip("/"),
        ingest_key=ingest_key,
        project_id=project_id,
        staging_dir=staging_dir,
        timeout_s=_positive_float(os.environ.get("NEXUS_RUNTIME_TOOLS_TIMEOUT"), DEFAULT_TIMEOUT_S),
        max_stdout_bytes=_positive_int(
            os.environ.get("NEXUS_RUNTIME_TOOLS_MAX_BYTES"), DEFAULT_MAX_STDOUT_BYTES
        ),
        source=f"base={base_source},project={project_source}",
    )


# ───────────────────────────────────────────────────────────────────────────
# HTTP (standard library only)
# ───────────────────────────────────────────────────────────────────────────


def _open(request: urllib.request.Request, timeout: float):
    """Perform the request.  The single seam the tests replace."""
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - fixed https/http base


def _decode(raw: bytes) -> tuple[bool, object]:
    """Decode a response body: ``(is_json, value)``.

    A body that is not JSON is carried through as ``raw_response`` rather than
    discarded -- an HTML error page from a proxy in front of the backend is the
    diagnosis -- but it is flagged, because a 200 carrying prose is not a
    runtime tool result and must not be reported to the agent as one.
    """
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return True, None
    try:
        return True, json.loads(text)
    except ValueError:
        return False, {"raw_response": _scrub(text[:4000])}


def _request(settings: Settings, method: str, path: str, payload: dict | None) -> object:
    """Send one request and return its decoded JSON body.

    Raises :class:`TransportError` for every outcome that is not a 2xx with a
    body -- including a 4xx, whose body is carried on the exception so the agent
    reads the server's own explanation rather than a guess made here.
    """
    url = settings.url(path)
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if settings.ingest_key:
        headers["Authorization"] = f"Bearer {settings.ingest_key}"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _open(request, settings.timeout_s) as response:
            status = int(getattr(response, "status", None) or getattr(response, "code", 0) or 0)
            is_json, body = _decode(response.read())
    except urllib.error.HTTPError as exc:  # a response, not a failure to reach one
        try:
            _is_json, body = _decode(exc.read())
        except Exception:  # noqa: BLE001 - diagnostics must not mask the status
            body = None
        raise TransportError(
            f"{method} {path} failed with HTTP {exc.code}", body=body
        ) from None
    except urllib.error.URLError as exc:
        raise TransportError(
            f"{method} {path} could not reach {settings.base_url}: {_scrub(exc.reason)}"
        ) from None
    except TimeoutError:
        raise TransportError(
            f"{method} {path} timed out after {settings.timeout_s:.0f}s"
        ) from None
    except OSError as exc:
        raise TransportError(f"{method} {path} failed: {_scrub(exc)}") from None

    if status and not (200 <= status < 300):
        raise TransportError(f"{method} {path} failed with HTTP {status}", body=body)
    if body is None:
        raise TransportError(f"{method} {path} returned an empty body")
    if not is_json:
        raise TransportError(
            f"{method} {path} returned a body that is not JSON "
            "(a proxy or an error page, not a runtime tool result)",
            body=body,
        )
    return body


# ───────────────────────────────────────────────────────────────────────────
# Agent-supplied JSON
# ───────────────────────────────────────────────────────────────────────────


def parse_arguments(raw: str | None) -> tuple[dict, bool]:
    """Parse the agent's JSON object and strip ``project_id`` from it.

    Returns ``(arguments, overrode_project_id)``.  A missing or empty argument
    is an empty object -- several tools take none.  Anything that is not a JSON
    object is a usage error: a bare list or string cannot be merged into a
    request body, and silently coercing it would send the server a shape it
    never agreed to.
    """
    text = (raw or "").strip()
    if not text:
        return {}, False
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ConfigurationError(
            f"arguments must be a JSON object; could not parse it ({exc})"
        ) from None
    if not isinstance(parsed, dict):
        raise ConfigurationError(
            f"arguments must be a JSON object, got {type(parsed).__name__}"
        )
    overrode = "project_id" in parsed
    parsed.pop("project_id", None)
    return parsed, overrode


# ───────────────────────────────────────────────────────────────────────────
# Result digest + ledger
# ───────────────────────────────────────────────────────────────────────────


def digest_result(value: object) -> dict:
    """Describe a result's SHAPE for the ledger: keys and counts, never values.

    The ledger has to survive being read by whoever audits what a run was shown,
    which means it must not become a second copy of the payload -- and a payload
    copied into ``_assessment/`` is a payload nobody redacted.
    """
    if value is None:
        return {"type": "null"}
    if isinstance(value, dict):
        keys = list(value.keys())
        counts = {
            key: len(item)
            for key, item in value.items()
            if isinstance(item, (list, tuple, dict, str))
        }
        return {
            "type": "object",
            "keys": [str(key) for key in keys[:MAX_DIGEST_KEYS]],
            "key_count": len(keys),
            "counts": {
                str(key): counts[key] for key in list(counts)[:MAX_DIGEST_KEYS]
            },
        }
    if isinstance(value, (list, tuple)):
        first = value[0] if value else None
        item_keys = (
            [str(key) for key in list(first.keys())[:MAX_DIGEST_KEYS]]
            if isinstance(first, dict)
            else []
        )
        return {"type": "array", "rows": len(value), "item_keys": item_keys}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    return {"type": type(value).__name__, "chars": len(str(value))}


def _bounded_arguments(arguments: dict) -> object:
    try:
        text = json.dumps(arguments, sort_keys=True)
    except (TypeError, ValueError):
        return {"unserializable": True}
    if len(text) <= MAX_LEDGER_ARGUMENT_CHARS:
        return arguments
    return {
        "truncated": True,
        "chars": len(text),
        "preview": _scrub(text[:MAX_LEDGER_ARGUMENT_CHARS]),
    }


def append_ledger(settings: Settings, entry: dict) -> None:
    """Append one JSON line to the probe ledger.  Never raises.

    A ledger write that fails must not cost the agent its answer, but it must
    also not be silent: the reason goes to stderr, where the run's log keeps it.
    """
    line = json.dumps(_strip_secrets(entry), ensure_ascii=False, sort_keys=False)
    try:
        path = settings.ledger_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        _err(f"runtime_tools: probe ledger not written ({_scrub(exc)})")


def _strip_secrets(value: object) -> object:
    """Recursively scrub registered secrets out of a JSON-able structure."""
    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, dict):
        return {str(key): _strip_secrets(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strip_secrets(item) for item in value]
    return value


def _ledger_entry(
    *,
    command: str,
    tool: str,
    arguments: dict,
    settings: Settings,
    overrode_project_id: bool,
    success: bool,
    error_code: object,
    duration_ms: int,
    digest: dict | None,
    truncated: object = None,
    reason: str | None = None,
) -> dict:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": command,
        "tool": tool,
        "project_id": settings.project_id,
        "config_source": settings.source,
        "arguments": _bounded_arguments(arguments),
        "overrode_project_id": overrode_project_id,
        "success": success,
        "error_code": error_code,
        "duration_ms": duration_ms,
    }
    if digest is not None:
        entry["digest"] = digest
    if truncated is not None:
        entry["truncated"] = truncated
    if reason:
        entry["reason"] = _scrub(reason)
    return entry


# ───────────────────────────────────────────────────────────────────────────
# Output
# ───────────────────────────────────────────────────────────────────────────


def render(value: object, max_bytes: int) -> str:
    """Render a result as readable JSON, bounded, and honest when it is bounded.

    An over-long result is replaced by a VALID JSON object that states the
    reduction, its size and how to narrow the query.  Cutting the JSON in half
    would hand the agent a fragment that does not parse and reads as data.
    """
    text = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    notice = {
        "truncated_by_client": True,
        "reason": (
            "the result exceeded the local print limit and was NOT shown in full; "
            "narrow the query (fewer units, shorter window, a more specific tool) "
            "and ask again"
        ),
        "bytes": len(text.encode("utf-8")),
        "limit_bytes": max_bytes,
        "shape": digest_result(value),
        "preview": text[: max(0, max_bytes - 1_000)],
    }
    return json.dumps(notice, indent=2, ensure_ascii=False)


# ───────────────────────────────────────────────────────────────────────────
# Commands
# ───────────────────────────────────────────────────────────────────────────


def _run_request(
    settings: Settings,
    *,
    command: str,
    tool: str,
    method: str,
    path: str,
    payload: dict | None,
    arguments: dict,
    overrode_project_id: bool,
) -> int:
    """Send one request, print its result, ledger it, and return an exit code."""
    started = time.monotonic()
    try:
        body = _request(settings, method, path, payload)
    except TransportError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        append_ledger(
            settings,
            _ledger_entry(
                command=command,
                tool=tool,
                arguments=arguments,
                settings=settings,
                overrode_project_id=overrode_project_id,
                success=False,
                error_code="transport_error",
                duration_ms=elapsed,
                digest=digest_result(exc.body) if exc.body is not None else None,
                reason=exc.reason,
            ),
        )
        if exc.body is not None:
            _out(render(exc.body, settings.max_stdout_bytes))
        _err(f"runtime_tools: {exc.reason}")
        return 2

    elapsed = int((time.monotonic() - started) * 1000)
    success = True
    error_code: object = None
    truncated: object = None
    digest_source: object = body
    if isinstance(body, dict):
        if "success" in body:
            success = bool(body.get("success"))
        error_code = body.get("error_code")
        truncated = body.get("truncated")
        if "data" in body:
            digest_source = body.get("data")

    append_ledger(
        settings,
        _ledger_entry(
            command=command,
            tool=tool,
            arguments=arguments,
            settings=settings,
            overrode_project_id=overrode_project_id,
            success=success,
            error_code=error_code,
            duration_ms=elapsed,
            digest=digest_result(digest_source),
            truncated=truncated,
        ),
    )
    _out(render(body, settings.max_stdout_bytes))
    return 0


def command_list(settings: Settings) -> int:
    return _run_request(
        settings,
        command="list",
        tool="__list__",
        method="GET",
        path=TOOLS_PATH,
        payload=None,
        arguments={},
        overrode_project_id=False,
    )


def command_call(settings: Settings, tool_name: str, raw_arguments: str | None) -> int:
    name = (tool_name or "").strip()
    if not name:
        raise ConfigurationError("call requires a tool name (see: runtime_tools.py list)")
    arguments, overrode = parse_arguments(raw_arguments)
    payload = {"project_id": settings.project_id, "arguments": arguments}
    path = f"{TOOLS_PATH}/{urllib.parse.quote(name, safe='')}"
    return _run_request(
        settings,
        command="call",
        tool=name,
        method="POST",
        path=path,
        payload=payload,
        arguments=arguments,
        overrode_project_id=overrode,
    )


def command_elastic(settings: Settings, probe_name: str, raw_body: str | None) -> int:
    name = (probe_name or "").strip()
    if not name:
        raise ConfigurationError(
            "elastic requires a probe name, one of: " + ", ".join(KNOWN_PROBES)
        )
    arguments, overrode = parse_arguments(raw_body)
    # Ours last: the agent's project_id was already dropped, and this ordering
    # keeps that true even if parse_arguments is ever relaxed.
    payload = {**arguments, "project_id": settings.project_id}
    path = f"{ELASTIC_PROBE_PATH}/{urllib.parse.quote(name, safe='')}"
    return _run_request(
        settings,
        command="elastic",
        tool=name,
        method="POST",
        path=path,
        payload=payload,
        arguments=arguments,
        overrode_project_id=overrode,
    )


# ───────────────────────────────────────────────────────────────────────────
# Entry point
# ───────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runtime_tools.py",
        description=(
            "Query this project's live runtime estate (execution history, "
            "reliability, schedules, log aggregates) while writing documentation."
        ),
        epilog=(
            "project_id is injected from the run's own configuration; passing one "
            "in the JSON has no effect. Elastic probes: " + ", ".join(KNOWN_PROBES)
        ),
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="list the available runtime tools and their schemas")

    call = sub.add_parser("call", help="call one runtime tool")
    call.add_argument("tool_name")
    call.add_argument("arguments", nargs="?", default="{}")

    elastic = sub.add_parser("elastic", help="run one Elastic discovery probe")
    elastic.add_argument("probe_name")
    elastic.add_argument("body", nargs="?", default="{}")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # keep non-ASCII job names readable on every host
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2

    try:
        settings = load_settings()
        if args.command == "list":
            return command_list(settings)
        if args.command == "call":
            return command_call(settings, args.tool_name, args.arguments)
        return command_elastic(settings, args.probe_name, args.body)
    except ConfigurationError as exc:
        _err(f"runtime_tools: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        # No traceback, ever: a traceback prints locals and frames, and one of
        # those frames holds the bearer key.
        _err(f"runtime_tools: unexpected {type(exc).__name__}: {_scrub(exc)}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
