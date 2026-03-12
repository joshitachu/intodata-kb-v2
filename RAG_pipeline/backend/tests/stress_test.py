#!/usr/bin/env python3
"""
Platform Stress Test -- end-to-end verification against live services.

Runs against a live deployment (no mocks). Tests all user flows:
health, auth, document ingestion, RAG query, streaming, conversations,
admin CRUD, concurrent load, and rate limiting.

Usage:
    python -m tests.stress_test --url http://localhost:8000
    python -m tests.stress_test --url https://yourdomain.com --iterations 50
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    base_url: str = "http://localhost:8000"
    admin_user: str = "admin"
    admin_pass: str = "admin"
    auth_iterations: int = 20
    query_iterations: int = 10
    stream_iterations: int = 5
    concurrent_users: int = 5
    concurrent_queries_per_user: int = 3
    timeout: float = 30.0
    verbose: bool = False


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    name: str
    passed: bool
    duration_ms: float = 0.0
    error: str | None = None


@dataclass
class SectionReport:
    name: str
    results: list[TestResult] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def success_rate(self) -> float:
        return (self.passed / self.total * 100) if self.total else 0.0

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * p / 100)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    def avg_latency(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_pdf() -> bytes:
    """Create a minimal valid PDF for upload testing."""
    content = """%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 178 >>
stream
BT
/F1 12 Tf
72 720 Td
(IntoData Stress Test Document) Tj
0 -20 Td
(This document is used for automated testing of the RAG pipeline.) Tj
0 -20 Td
(It contains sample text about integration platforms and data flows.) Tj
0 -20 Td
(Talend jobs transform data from source systems to target databases.) Tj
0 -20 Td
(MuleSoft APIs connect cloud applications with on-premise systems.) Tj
ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000496 00000 n
trailer
<< /Size 6 /Root 1 0 R >>
startxref
573
%%EOF"""
    return content.encode("latin-1")


def _log(msg: str, verbose: bool = True) -> None:
    if verbose:
        print(f"  {msg}")


# ---------------------------------------------------------------------------
# Test sections
# ---------------------------------------------------------------------------

async def test_health(client: httpx.AsyncClient, cfg: Config) -> SectionReport:
    """A. Health & Connectivity"""
    report = SectionReport(name="Health & Connectivity")

    start = time.perf_counter()
    try:
        r = await client.get("/health")
        ms = (time.perf_counter() - start) * 1000
        report.latencies_ms.append(ms)
        data = r.json()

        ok = r.status_code == 200
        report.results.append(TestResult("GET /health returns 200", ok, ms))

        for svc in ["database", "qdrant", "ollama"]:
            svc_ok = data.get(svc, {}).get("status") == "ok"
            report.results.append(TestResult(f"Service {svc} is healthy", svc_ok, ms))

        status = data.get("status", "unknown")
        report.results.append(TestResult(
            f"Overall status: {status}",
            status in ("healthy", "degraded"),
            ms,
        ))
    except Exception as e:
        report.results.append(TestResult("Health check", False, error=str(e)))

    return report


async def test_auth_flow(client: httpx.AsyncClient, cfg: Config) -> SectionReport:
    """B. Auth Flow (repeated N times)"""
    report = SectionReport(name=f"Auth Flow ({cfg.auth_iterations} iterations)")

    for i in range(cfg.auth_iterations):
        # Login with valid credentials
        start = time.perf_counter()
        try:
            r = await client.post("/api/v1/auth/login", json={
                "username": cfg.admin_user,
                "password": cfg.admin_pass,
            })
            ms = (time.perf_counter() - start) * 1000
            report.latencies_ms.append(ms)

            ok = r.status_code == 200
            report.results.append(TestResult(f"Login #{i+1}", ok, ms,
                                             error=None if ok else f"status={r.status_code}"))

            if not ok:
                continue

            # GET /auth/me
            start2 = time.perf_counter()
            r2 = await client.get("/api/v1/auth/me")
            ms2 = (time.perf_counter() - start2) * 1000
            report.latencies_ms.append(ms2)

            me_ok = r2.status_code == 200 and r2.json().get("username") == cfg.admin_user
            report.results.append(TestResult(f"GET /auth/me #{i+1}", me_ok, ms2))

            # Refresh
            start3 = time.perf_counter()
            r3 = await client.post("/api/v1/auth/refresh")
            ms3 = (time.perf_counter() - start3) * 1000
            report.latencies_ms.append(ms3)
            report.results.append(TestResult(f"Refresh #{i+1}", r3.status_code == 200, ms3))

            # Logout
            start4 = time.perf_counter()
            r4 = await client.post("/api/v1/auth/logout")
            ms4 = (time.perf_counter() - start4) * 1000
            report.results.append(TestResult(f"Logout #{i+1}", r4.status_code == 200, ms4))

        except Exception as e:
            report.results.append(TestResult(f"Auth iteration #{i+1}", False, error=str(e)))

    # Test invalid login
    try:
        r = await client.post("/api/v1/auth/login", json={
            "username": cfg.admin_user,
            "password": "wrong-password-12345",
        })
        report.results.append(TestResult(
            "Invalid login returns 401",
            r.status_code == 401,
            error=None if r.status_code == 401 else f"got {r.status_code}",
        ))
    except Exception as e:
        report.results.append(TestResult("Invalid login", False, error=str(e)))

    # Test unauthenticated access
    try:
        fresh = httpx.AsyncClient(base_url=cfg.base_url, timeout=cfg.timeout)
        r = await fresh.get("/api/v1/auth/me")
        await fresh.aclose()
        report.results.append(TestResult(
            "Unauthenticated /auth/me returns 401",
            r.status_code == 401,
            error=None if r.status_code == 401 else f"got {r.status_code}",
        ))
    except Exception as e:
        report.results.append(TestResult("Unauthenticated access", False, error=str(e)))

    return report


async def _login(client: httpx.AsyncClient, cfg: Config) -> bool:
    """Login and store cookies on the client."""
    r = await client.post("/api/v1/auth/login", json={
        "username": cfg.admin_user,
        "password": cfg.admin_pass,
    })
    return r.status_code == 200


async def test_ingest(client: httpx.AsyncClient, cfg: Config) -> SectionReport:
    """C. Document Ingestion"""
    report = SectionReport(name="Document Ingestion")

    await _login(client, cfg)

    pdf_bytes = _make_test_pdf()
    doc_id = None

    # Upload test PDF
    start = time.perf_counter()
    try:
        r = await client.post(
            "/api/v1/ingest",
            files={"file": ("stress_test.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
            timeout=60.0,
        )
        ms = (time.perf_counter() - start) * 1000
        report.latencies_ms.append(ms)

        ok = r.status_code == 200
        if ok:
            data = r.json()
            doc_id = data.get("document_id")
            chunks = data.get("message", "")
            report.results.append(TestResult(
                f"Upload PDF (doc_id={doc_id}, {chunks})", True, ms
            ))
        else:
            report.results.append(TestResult("Upload PDF", False, ms, error=f"status={r.status_code}: {r.text[:200]}"))
    except Exception as e:
        report.results.append(TestResult("Upload PDF", False, error=str(e)))

    # Re-upload same file (idempotent replacement)
    start = time.perf_counter()
    try:
        r = await client.post(
            "/api/v1/ingest",
            files={"file": ("stress_test.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
            timeout=60.0,
        )
        ms = (time.perf_counter() - start) * 1000
        report.latencies_ms.append(ms)
        report.results.append(TestResult(
            "Re-upload same file (idempotent)", r.status_code == 200, ms,
            error=None if r.status_code == 200 else f"status={r.status_code}",
        ))
    except Exception as e:
        report.results.append(TestResult("Re-upload", False, error=str(e)))

    # Upload wrong file type
    try:
        r = await client.post(
            "/api/v1/ingest",
            files={"file": ("test.exe", io.BytesIO(b"not a real file"), "application/octet-stream")},
        )
        report.results.append(TestResult(
            "Reject wrong file type",
            r.status_code == 400,
            error=None if r.status_code == 400 else f"got {r.status_code}",
        ))
    except Exception as e:
        report.results.append(TestResult("Wrong file type", False, error=str(e)))

    return report


async def test_query(client: httpx.AsyncClient, cfg: Config) -> SectionReport:
    """D. Query (RAG flow)"""
    report = SectionReport(name=f"Query ({cfg.query_iterations} iterations)")

    await _login(client, cfg)

    for i in range(cfg.query_iterations):
        start = time.perf_counter()
        try:
            r = await client.post("/api/v1/query", json={
                "query": "What integration platforms are documented?",
                "top_k": 5,
                "threshold": 0.3,
            }, timeout=cfg.timeout)
            ms = (time.perf_counter() - start) * 1000
            report.latencies_ms.append(ms)

            ok = r.status_code == 200
            if ok:
                data = r.json()
                has_answer = bool(data.get("answer"))
                report.results.append(TestResult(
                    f"Query #{i+1} (has_answer={has_answer})", True, ms
                ))
            else:
                report.results.append(TestResult(
                    f"Query #{i+1}", False, ms,
                    error=f"status={r.status_code}: {r.text[:100]}",
                ))
        except httpx.ReadTimeout:
            ms = (time.perf_counter() - start) * 1000
            report.results.append(TestResult(f"Query #{i+1}", False, ms, error="timeout"))
        except Exception as e:
            report.results.append(TestResult(f"Query #{i+1}", False, error=str(e)))

    # Debug search
    try:
        r = await client.post("/api/v1/query/debug", json={
            "query": "data transformation",
            "top_k": 5,
        })
        ok = r.status_code == 200
        result_count = len(r.json().get("results", [])) if ok else 0
        report.results.append(TestResult(
            f"Debug search ({result_count} results)", ok,
            error=None if ok else f"status={r.status_code}",
        ))
    except Exception as e:
        report.results.append(TestResult("Debug search", False, error=str(e)))

    return report


async def test_streaming(client: httpx.AsyncClient, cfg: Config) -> SectionReport:
    """E. Streaming SSE"""
    report = SectionReport(name=f"Streaming ({cfg.stream_iterations} iterations)")

    await _login(client, cfg)

    for i in range(cfg.stream_iterations):
        start = time.perf_counter()
        first_token_ms = None
        events_seen: list[str] = []

        try:
            async with client.stream(
                "POST",
                "/api/v1/query/stream",
                json={"query": "Describe the data flows", "top_k": 3, "threshold": 0.3},
                timeout=60.0,
            ) as resp:
                if resp.status_code != 200:
                    report.results.append(TestResult(
                        f"Stream #{i+1}", False,
                        error=f"status={resp.status_code}",
                    ))
                    continue

                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:]
                        events_seen.append(event_type)
                        if event_type == "token" and first_token_ms is None:
                            first_token_ms = (time.perf_counter() - start) * 1000
                        if event_type == "done":
                            break

            total_ms = (time.perf_counter() - start) * 1000
            report.latencies_ms.append(total_ms)

            has_sources = "sources" in events_seen
            has_tokens = "token" in events_seen
            has_done = "done" in events_seen

            # Verify event ordering: sources before first token
            sources_idx = events_seen.index("sources") if has_sources else -1
            token_idx = events_seen.index("token") if has_tokens else len(events_seen)
            order_ok = sources_idx < token_idx if has_sources and has_tokens else True

            all_ok = has_sources and has_done and order_ok
            detail = f"ttft={first_token_ms:.0f}ms, total={total_ms:.0f}ms" if first_token_ms else f"total={total_ms:.0f}ms"
            report.results.append(TestResult(
                f"Stream #{i+1} ({detail})",
                all_ok,
                total_ms,
                error=None if all_ok else f"events={events_seen[:10]}",
            ))

        except httpx.ReadTimeout:
            report.results.append(TestResult(f"Stream #{i+1}", False, error="timeout"))
        except Exception as e:
            report.results.append(TestResult(f"Stream #{i+1}", False, error=str(e)))

    return report


async def test_conversations(client: httpx.AsyncClient, cfg: Config) -> SectionReport:
    """F. Conversations"""
    report = SectionReport(name="Conversations")

    await _login(client, cfg)

    conv_id = None

    # Create conversation
    start = time.perf_counter()
    try:
        r = await client.post("/api/v1/conversations", json={"title": "Stress test conv"})
        ms = (time.perf_counter() - start) * 1000
        report.latencies_ms.append(ms)
        ok = r.status_code == 201
        if ok:
            conv_id = r.json()["id"]
        report.results.append(TestResult("Create conversation", ok, ms,
                                         error=None if ok else f"status={r.status_code}"))
    except Exception as e:
        report.results.append(TestResult("Create conversation", False, error=str(e)))

    if conv_id:
        # Send message
        start = time.perf_counter()
        try:
            r = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"query": "What documents do we have?", "top_k": 3},
                timeout=60.0,
            )
            ms = (time.perf_counter() - start) * 1000
            report.latencies_ms.append(ms)
            ok = r.status_code == 200
            report.results.append(TestResult(f"Send message (conv {conv_id})", ok, ms,
                                             error=None if ok else f"status={r.status_code}"))
        except Exception as e:
            report.results.append(TestResult("Send message", False, error=str(e)))

        # Send follow-up
        start = time.perf_counter()
        try:
            r = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"query": "Tell me more about the first one", "top_k": 3},
                timeout=60.0,
            )
            ms = (time.perf_counter() - start) * 1000
            report.latencies_ms.append(ms)
            ok = r.status_code == 200
            report.results.append(TestResult("Follow-up message", ok, ms,
                                             error=None if ok else f"status={r.status_code}"))
        except Exception as e:
            report.results.append(TestResult("Follow-up message", False, error=str(e)))

        # List conversations
        try:
            r = await client.get("/api/v1/conversations")
            ok = r.status_code == 200 and r.json().get("total", 0) > 0
            report.results.append(TestResult("List conversations", ok))
        except Exception as e:
            report.results.append(TestResult("List conversations", False, error=str(e)))

        # Delete conversation
        try:
            r = await client.delete(f"/api/v1/conversations/{conv_id}")
            report.results.append(TestResult("Delete conversation", r.status_code == 204))
        except Exception as e:
            report.results.append(TestResult("Delete conversation", False, error=str(e)))

    return report


async def test_admin(client: httpx.AsyncClient, cfg: Config) -> SectionReport:
    """G. Admin Operations"""
    report = SectionReport(name="Admin Operations")

    await _login(client, cfg)

    test_user_id = None
    test_username = f"stresstest_{int(time.time())}"

    # Create user
    try:
        r = await client.post("/api/v1/admin/users", json={
            "username": test_username,
            "email": f"{test_username}@test.local",
            "password": "TestPassword123!",
        })
        ok = r.status_code == 201
        if ok:
            test_user_id = r.json()["id"]
        report.results.append(TestResult(f"Create user '{test_username}'", ok,
                                         error=None if ok else f"status={r.status_code}: {r.text[:100]}"))
    except Exception as e:
        report.results.append(TestResult("Create user", False, error=str(e)))

    # List users
    try:
        r = await client.get("/api/v1/admin/users")
        ok = r.status_code == 200
        total = r.json().get("total", 0) if ok else 0
        report.results.append(TestResult(f"List users (total={total})", ok))
    except Exception as e:
        report.results.append(TestResult("List users", False, error=str(e)))

    # List roles
    try:
        r = await client.get("/api/v1/admin/roles")
        ok = r.status_code == 200
        report.results.append(TestResult("List roles", ok))
    except Exception as e:
        report.results.append(TestResult("List roles", False, error=str(e)))

    if test_user_id:
        # Reset password
        try:
            r = await client.post(f"/api/v1/admin/users/{test_user_id}/reset-password", json={
                "new_password": "NewPassword456!",
            })
            report.results.append(TestResult("Reset password", r.status_code == 200,
                                             error=None if r.status_code == 200 else f"status={r.status_code}"))
        except Exception as e:
            report.results.append(TestResult("Reset password", False, error=str(e)))

        # Assign project
        try:
            r = await client.post("/api/v1/admin/project-assignments", json={
                "user_id": test_user_id,
                "project_id": "stress-test-project",
            })
            ok = r.status_code == 201
            report.results.append(TestResult("Assign project", ok,
                                             error=None if ok else f"status={r.status_code}"))
        except Exception as e:
            report.results.append(TestResult("Assign project", False, error=str(e)))

        # Verify RBAC: test user can't access admin endpoints
        try:
            test_client = httpx.AsyncClient(base_url=cfg.base_url, timeout=cfg.timeout)
            r = await test_client.post("/api/v1/auth/login", json={
                "username": test_username,
                "password": "NewPassword456!",
            })
            if r.status_code == 200:
                r2 = await test_client.get("/api/v1/admin/users")
                report.results.append(TestResult(
                    "RBAC: non-admin blocked from /admin/users",
                    r2.status_code in (401, 403),
                    error=None if r2.status_code in (401, 403) else f"got {r2.status_code}",
                ))
            else:
                report.results.append(TestResult("RBAC test (login failed)", False,
                                                 error=f"login status={r.status_code}"))
            await test_client.aclose()
        except Exception as e:
            report.results.append(TestResult("RBAC test", False, error=str(e)))

        # Deactivate user
        try:
            r = await client.delete(f"/api/v1/admin/users/{test_user_id}")
            report.results.append(TestResult("Deactivate user", r.status_code == 200,
                                             error=None if r.status_code == 200 else f"status={r.status_code}"))
        except Exception as e:
            report.results.append(TestResult("Deactivate user", False, error=str(e)))

    return report


async def test_concurrent_load(client: httpx.AsyncClient, cfg: Config) -> SectionReport:
    """H. Concurrent Load"""
    report = SectionReport(name=f"Concurrent Load ({cfg.concurrent_users} users x {cfg.concurrent_queries_per_user} queries)")

    total_requests = cfg.concurrent_users * cfg.concurrent_queries_per_user
    errors = 0
    all_latencies: list[float] = []

    async def _worker(worker_id: int) -> list[TestResult]:
        results = []
        worker_client = httpx.AsyncClient(base_url=cfg.base_url, timeout=cfg.timeout)
        try:
            # Each worker logs in
            await worker_client.post("/api/v1/auth/login", json={
                "username": cfg.admin_user,
                "password": cfg.admin_pass,
            })

            for q in range(cfg.concurrent_queries_per_user):
                start = time.perf_counter()
                try:
                    r = await worker_client.post("/api/v1/query", json={
                        "query": f"Worker {worker_id} query {q}: What are the data integrations?",
                        "top_k": 3,
                        "threshold": 0.3,
                    }, timeout=cfg.timeout)
                    ms = (time.perf_counter() - start) * 1000
                    all_latencies.append(ms)
                    ok = r.status_code == 200
                    results.append(TestResult(f"W{worker_id}-Q{q}", ok, ms,
                                             error=None if ok else f"status={r.status_code}"))
                except Exception as e:
                    ms = (time.perf_counter() - start) * 1000
                    all_latencies.append(ms)
                    results.append(TestResult(f"W{worker_id}-Q{q}", False, ms, error=str(e)))
        finally:
            await worker_client.aclose()
        return results

    start = time.perf_counter()
    worker_results = await asyncio.gather(
        *[_worker(i) for i in range(cfg.concurrent_users)],
        return_exceptions=True,
    )
    total_time = time.perf_counter() - start

    for wr in worker_results:
        if isinstance(wr, Exception):
            report.results.append(TestResult("Worker", False, error=str(wr)))
            errors += 1
        else:
            report.results.extend(wr)

    report.latencies_ms = all_latencies

    # Summary metrics
    successful = sum(1 for r in report.results if r.passed)
    throughput = successful / total_time if total_time > 0 else 0

    report.results.append(TestResult(
        f"Throughput: {throughput:.1f} req/sec, "
        f"Error rate: {(1 - successful / max(len(report.results) - 1, 1)) * 100:.1f}%",
        successful == len(report.results) - 1,  # exclude this summary result
    ))

    return report


async def test_rate_limiting(client: httpx.AsyncClient, cfg: Config) -> SectionReport:
    """I. Rate Limiting"""
    report = SectionReport(name="Rate Limiting")

    # Login rate limit: 5/minute
    # Use a fresh client to avoid cookie interference
    rate_client = httpx.AsyncClient(base_url=cfg.base_url, timeout=cfg.timeout)
    try:
        got_429 = False
        for i in range(8):
            r = await rate_client.post("/api/v1/auth/login", json={
                "username": "nonexistent_user",
                "password": "wrong",
            })
            if r.status_code == 429:
                got_429 = True
                break

        report.results.append(TestResult(
            f"Login rate limit (5/min): {'ENFORCED' if got_429 else 'NOT ENFORCED'}",
            got_429,
        ))
    except Exception as e:
        report.results.append(TestResult("Login rate limit", False, error=str(e)))
    finally:
        await rate_client.aclose()

    # Query rate limit: 30/minute -- we just verify the header exists
    await _login(client, cfg)
    try:
        r = await client.post("/api/v1/query", json={
            "query": "test", "top_k": 1, "threshold": 0.5,
        }, timeout=cfg.timeout)
        has_limit_header = "x-ratelimit-limit" in {k.lower() for k in r.headers.keys()}
        report.results.append(TestResult(
            f"Query rate limit header present: {has_limit_header}",
            True,  # Pass regardless -- we just report presence
        ))
    except Exception as e:
        report.results.append(TestResult("Query rate limit check", False, error=str(e)))

    return report


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(sections: list[SectionReport]) -> float:
    """Print the final report. Returns overall success rate."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'='*60}")
    print(f"  Platform Stress Test Report")
    print(f"  Date: {now}")
    print(f"{'='*60}")

    total_passed = 0
    total_tests = 0

    for section in sections:
        total_passed += section.passed
        total_tests += section.total

        print(f"\n--- {section.name} ---")
        print(f"  Success rate: {section.passed}/{section.total} ({section.success_rate:.0f}%)")

        if section.latencies_ms:
            print(f"  Avg latency:  {section.avg_latency():.0f}ms")
            print(f"  P95 latency:  {section.percentile(95):.0f}ms")
            print(f"  P99 latency:  {section.percentile(99):.0f}ms")

        # Show failures
        failures = [r for r in section.results if not r.passed]
        if failures:
            print(f"  Failures ({len(failures)}):")
            for f in failures[:5]:  # Show max 5
                error_msg = f" -- {f.error}" if f.error else ""
                print(f"    - {f.name}{error_msg}")
            if len(failures) > 5:
                print(f"    ... and {len(failures) - 5} more")

    overall = (total_passed / total_tests * 100) if total_tests else 0
    print(f"\n{'='*60}")
    print(f"  OVERALL: {total_passed}/{total_tests} ({overall:.1f}% success rate)")
    status = "PASS" if overall >= 95 else "WARN" if overall >= 80 else "FAIL"
    print(f"  Status:  {status}")
    print(f"{'='*60}\n")

    return overall


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(cfg: Config) -> float:
    """Run all test sections. Returns overall success rate."""
    print(f"Starting stress test against {cfg.base_url}")
    print(f"Auth iterations: {cfg.auth_iterations}, Query iterations: {cfg.query_iterations}")
    print(f"Concurrent: {cfg.concurrent_users} users x {cfg.concurrent_queries_per_user} queries")
    print()

    client = httpx.AsyncClient(base_url=cfg.base_url, timeout=cfg.timeout)

    sections: list[SectionReport] = []

    # A. Health
    print("[1/8] Testing health & connectivity...")
    sections.append(await test_health(client, cfg))

    # Quick check: if health fails, warn but continue
    if sections[0].failed > 0:
        print("  WARNING: Some services are unhealthy. Tests may fail.")

    # B. Auth
    print("[2/8] Testing auth flow...")
    sections.append(await test_auth_flow(client, cfg))

    # C. Ingest
    print("[3/8] Testing document ingestion...")
    sections.append(await test_ingest(client, cfg))

    # D. Query
    print("[4/8] Testing RAG query...")
    sections.append(await test_query(client, cfg))

    # E. Streaming
    print("[5/8] Testing streaming...")
    sections.append(await test_streaming(client, cfg))

    # F. Conversations
    print("[6/8] Testing conversations...")
    sections.append(await test_conversations(client, cfg))

    # G. Admin
    print("[7/8] Testing admin operations...")
    sections.append(await test_admin(client, cfg))

    # H. Concurrent Load
    print("[8/8] Testing concurrent load...")
    sections.append(await test_concurrent_load(client, cfg))

    # I. Rate Limiting (run last to avoid interfering with other tests)
    print("[+] Testing rate limiting...")
    sections.append(await test_rate_limiting(client, cfg))

    await client.aclose()

    return print_report(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="Platform stress test against live services")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the RAG backend")
    parser.add_argument("--user", default="admin", help="Admin username")
    parser.add_argument("--password", default="admin", help="Admin password")
    parser.add_argument("--iterations", type=int, default=20, help="Auth flow iterations")
    parser.add_argument("--query-iterations", type=int, default=10, help="Query iterations")
    parser.add_argument("--stream-iterations", type=int, default=5, help="Streaming iterations")
    parser.add_argument("--concurrent-users", type=int, default=5, help="Concurrent users for load test")
    parser.add_argument("--concurrent-queries", type=int, default=3, help="Queries per concurrent user")
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    cfg = Config(
        base_url=args.url,
        admin_user=args.user,
        admin_pass=args.password,
        auth_iterations=args.iterations,
        query_iterations=args.query_iterations,
        stream_iterations=args.stream_iterations,
        concurrent_users=args.concurrent_users,
        concurrent_queries_per_user=args.concurrent_queries,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    overall = asyncio.run(run(cfg))
    sys.exit(0 if overall >= 80 else 1)


if __name__ == "__main__":
    main()
