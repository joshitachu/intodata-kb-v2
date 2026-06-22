#!/usr/bin/env python3
"""Post GitHub-style webhook smoke events and print reingestion status."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import urllib.error
import urllib.request
import uuid


JsonObject = dict[str, object]


def _root_base_url(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/mcp"):
        return clean[:-4]
    return clean


def _url(base_url: str, path: str) -> str:
    return f"{_root_base_url(base_url)}{path}"


def _json_bytes(payload: JsonObject) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signed_headers(payload: bytes, event: str, secret: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": f"smoke-{uuid.uuid4()}",
    }
    if secret:
        signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={signature}"
    return headers


def _ping_payload(repo_url: str) -> JsonObject:
    return {
        "zen": "Smoke test delivery",
        "hook_id": 0,
        "repository": {
            "clone_url": repo_url,
            "html_url": repo_url.removesuffix(".git"),
        },
    }


def _push_payload(repo_url: str, before_sha: str, after_sha: str) -> JsonObject:
    return {
        "ref": "refs/heads/main",
        "before": before_sha,
        "after": after_sha,
        "repository": {
            "clone_url": repo_url,
            "html_url": repo_url.removesuffix(".git"),
        },
        "head_commit": {
            "id": after_sha,
            "message": "Smoke reingestion webhook push",
        },
    }


def _http_request(
    method: str,
    url: str,
    *,
    payload: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=payload,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def _print_event(
    *,
    base_url: str,
    event: str,
    payload: JsonObject,
    secret: str | None,
    dry_run: bool,
    timeout: float,
) -> int:
    body = _json_bytes(payload)
    headers = _signed_headers(body, event, secret)
    target = _url(base_url, "/webhook/github/webhook")

    if dry_run:
        safe_headers = dict(headers)
        if secret and "X-Hub-Signature-256" in safe_headers:
            safe_headers["X-Hub-Signature-256"] = "<computed sha256 signature>"
        print(f"DRY RUN: would POST {event} to {target}")
        print(json.dumps({"headers": safe_headers, "payload": payload}, indent=2, sort_keys=True))
        return 0

    status, text = _http_request("POST", target, payload=body, headers=headers, timeout=timeout)
    print(f"{event}: HTTP {status}")
    _print_json_or_text(text)
    return 0 if 200 <= status < 300 else 1


def _print_json_or_text(text: str) -> None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print(text)
        return
    print(json.dumps(parsed, indent=2, sort_keys=True))


def _print_status_summary(status: JsonObject) -> None:
    webhook = status.get("webhook") if isinstance(status.get("webhook"), dict) else {}
    update = status.get("last_update") if isinstance(status.get("last_update"), dict) else {}
    remote = status.get("remote_hygiene") if isinstance(status.get("remote_hygiene"), dict) else {}
    cache = status.get("cache") if isinstance(status.get("cache"), dict) else {}
    observation = (
        webhook.get("last_observation")
        if isinstance(webhook.get("last_observation"), dict)
        else {}
    )

    print("Status summary:")
    print(f"  project: {status.get('project_id')} ({status.get('project_status')})")
    print(
        "  webhook: "
        f"{webhook.get('status')} event={observation.get('event')} "
        f"delivery={observation.get('delivery_id')}"
    )
    print(
        "  update: "
        f"outcome={update.get('outcome')} action={update.get('action')} "
        f"reason_code={update.get('reason_code')} error={update.get('error')}"
    )
    print(f"  last_ingested_sha: {status.get('last_ingested_commit_sha')}")
    print(
        "  remote: "
        f"status={remote.get('status')} ready={remote.get('ready')} "
        f"repairable={remote.get('repairable')} issues={remote.get('issues')}"
    )
    print(
        "  cache: "
        f"targeted_ready={cache.get('targeted_regen_ready')} "
        f"full_rebaseline={cache.get('requires_full_rebaseline')} "
        f"reasons={cache.get('reason_codes')}"
    )


def _fetch_status(
    *,
    base_url: str,
    project_id: str,
    auth_token: str | None,
    dry_run: bool,
    timeout: float,
) -> int:
    target = _url(base_url, f"/mcp/projects/{project_id}/reingestion-status")
    headers = {"Accept": "application/json"}
    if auth_token:
        headers["Cookie"] = f"access_token={auth_token}"

    if dry_run:
        safe_headers = dict(headers)
        if "Cookie" in safe_headers:
            safe_headers["Cookie"] = "access_token=<redacted>"
        print(f"DRY RUN: would GET {target}")
        print(json.dumps({"headers": safe_headers}, indent=2, sort_keys=True))
        return 0

    status, text = _http_request("GET", target, headers=headers, timeout=timeout)
    print(f"status: HTTP {status}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print(text)
        return 1
    print(json.dumps(parsed, indent=2, sort_keys=True))
    if isinstance(parsed, dict):
        _print_status_summary(parsed)
    return 0 if 200 <= status < 300 else 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test GitHub webhook reingestion by posting ping and/or push "
            "payloads, then reading the project reingestion status endpoint."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8002",
        help="MCP server root URL. If it ends with /mcp, the script strips that suffix.",
    )
    parser.add_argument("--project-id", required=True, help="Registered MCP project ID.")
    parser.add_argument("--repo-url", required=True, help="Repository clone URL to place in payloads.")
    parser.add_argument("--secret", help="Webhook secret used for X-Hub-Signature-256 signing.")
    parser.add_argument(
        "--auth-token",
        help="Optional JWT access token for the /mcp/projects/... status request cookie.",
    )
    parser.add_argument("--before", default="a" * 40, help="Push before SHA.")
    parser.add_argument("--after", default="b" * 40, help="Push after SHA.")
    parser.add_argument(
        "--event",
        choices=["ping", "push", "both"],
        default="both",
        help="Webhook event(s) to send before reading status.",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Print requests without sending them.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    failures = 0
    if args.event in {"ping", "both"}:
        failures += _print_event(
            base_url=args.base_url,
            event="ping",
            payload=_ping_payload(args.repo_url),
            secret=args.secret,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    if args.event in {"push", "both"}:
        failures += _print_event(
            base_url=args.base_url,
            event="push",
            payload=_push_payload(args.repo_url, args.before, args.after),
            secret=args.secret,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    failures += _fetch_status(
        base_url=args.base_url,
        project_id=args.project_id,
        auth_token=args.auth_token,
        dry_run=args.dry_run,
        timeout=args.timeout,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
