"""
IntoData KB — Architecture PDF
Style: NotebookLM-inspired — warm cream bg, blueprint grid, dark text on light, whitespace-first.
Run: python scripts/generate_architecture_pdf.py
"""
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib import colors
from reportlab.lib.units import mm
import os

W, H = landscape(A4)
M = 20 * mm

# ── Palette ───────────────────────────────────────────────────────────────────
BG       = colors.HexColor("#f5f2ec")
GRID     = colors.HexColor("#dedad2")
NAVY     = colors.HexColor("#1e2a4a")
NAVY2    = colors.HexColor("#2d3f6e")
NAVY3    = colors.HexColor("#3d5299")
AMBER    = colors.HexColor("#f5a623")
AMBER_LT = colors.HexColor("#fef3d8")
TEAL     = colors.HexColor("#0d9488")
TEAL_LT  = colors.HexColor("#d0f5f1")
SLATE    = colors.HexColor("#64748b")
SLATE_LT = colors.HexColor("#eaecf3")
WHITE    = colors.white
BLACK    = colors.HexColor("#0f172a")
GREEN    = colors.HexColor("#166534")


# ── Shared primitives ─────────────────────────────────────────────────────────

def draw_bg(c):
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setStrokeColor(GRID)
    c.setLineWidth(0.4)
    step = 18
    for x in range(0, int(W) + step, step):
        c.line(x, 0, x, H)
    for y in range(0, int(H) + step, step):
        c.line(0, y, W, y)


def draw_header(c, title, subtitle=None):
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(M, H - 32, title)
    if subtitle:
        c.setFillColor(SLATE)
        c.setFont("Helvetica", 11)
        c.drawString(M, H - 48, subtitle)
    rule_y = H - (56 if subtitle else 42)
    c.setFillColor(AMBER)
    c.rect(M, rule_y, W - 2 * M, 2.5, fill=1, stroke=0)


def draw_footer(c, n, total):
    c.setFillColor(GRID)
    c.rect(0, 0, W, 14, fill=1, stroke=0)
    c.setFillColor(SLATE)
    c.setFont("Helvetica", 7)
    c.drawString(M, 4, "IntoData KB  |  Integration Intelligence Platform")
    c.drawRightString(W - M, 4, f"{n} / {total}")


def rbox(c, x, y, w, h, fill=WHITE, stroke=NAVY2, r=5, lw=0.8):
    c.setLineWidth(lw)
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.roundRect(x, y, w, h, r, fill=1, stroke=1)


def pill_label(c, text, cx, cy, fill=AMBER, fg=NAVY, fs=7.5):
    tw = len(text) * fs * 0.54 + 10
    th = fs + 5
    c.setFillColor(fill)
    c.roundRect(cx - tw / 2, cy - 3, tw, th, th / 2, fill=1, stroke=0)
    c.setFillColor(fg)
    c.setFont("Helvetica-Bold", fs)
    c.drawCentredString(cx, cy, text)


def arrow_right(c, x1, y, x2, color=AMBER, lw=1.2):
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(lw)
    c.line(x1, y, x2, y)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x2 - 5, y - 3.5, "›")


def arrow_down(c, x, y1, y2, color=AMBER, lw=1.2):
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(lw)
    c.line(x, y1, x, y2)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(x, y2 - 1, "↓")


def dashed_hline(c, x1, y, x2, color=SLATE, lw=0.8):
    c.setStrokeColor(color)
    c.setLineWidth(lw)
    c.setDash([4, 3])
    c.line(x1, y, x2, y)
    c.setDash([])


def t(c, text, x, y, font="Helvetica", size=9, color=NAVY, align="left"):
    c.setFont(font, size)
    c.setFillColor(color)
    if align == "center":
        c.drawCentredString(x, y, text)
    elif align == "right":
        c.drawRightString(x, y, text)
    else:
        c.drawString(x, y, text)


def wrapped(c, text, x, y, max_w, font="Helvetica", size=8.5, color=SLATE, leading=12):
    c.setFont(font, size)
    c.setFillColor(color)
    words = text.split()
    line = ""
    for word in words:
        test = (line + " " + word).strip()
        if c.stringWidth(test, font, size) <= max_w:
            line = test
        else:
            c.drawString(x, y, line)
            y -= leading
            line = word
    if line:
        c.drawString(x, y, line)
    return y - leading


# ── SLIDE 1: Cover ────────────────────────────────────────────────────────────
def s_cover(c, n, total):
    draw_bg(c)

    c.setFillColor(colors.HexColor("#e8e3d8"))
    c.circle(W - 90, 80, 130, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#ddd8cc"))
    c.circle(W - 90, 80, 80, fill=1, stroke=0)
    c.setFillColor(AMBER)
    c.circle(W - 90, 80, 22, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#e8e3d8"))
    c.circle(W - 90, 80, 10, fill=1, stroke=0)

    c.setFillColor(AMBER)
    c.rect(0, 0, 7, H, fill=1, stroke=0)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 44)
    c.drawString(M + 10, H - 90, "IntoData KB")
    c.setFont("Helvetica-Bold", 22)
    c.drawString(M + 10, H - 120, "Integration Intelligence Platform")
    c.setFillColor(AMBER)
    c.rect(M + 10, H - 130, 210, 3, fill=1, stroke=0)
    c.setFillColor(SLATE)
    c.setFont("Helvetica", 13)
    c.drawString(M + 10, H - 148, "Technical Architecture  ·  v3.0  ·  May 2026")

    pillars = [
        ("Code Intelligence",     "Scan & document integration codebases automatically"),
        ("Knowledge Q&A",         "RAG-powered search over accumulated project knowledge"),
        ("Provider-Agnostic AI",  "Swappable LLM & embedding backends — no vendor lock-in"),
        ("Enterprise-Ready",      "RBAC, audit logs, usage tracking, one VPS per client"),
    ]
    y = H - 185
    for title, desc in pillars:
        c.setFillColor(AMBER)
        c.circle(M + 18, y + 4, 4, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(M + 30, y, title)
        c.setFillColor(SLATE)
        c.setFont("Helvetica", 9.5)
        c.drawString(M + 30, y - 14, desc)
        y -= 36

    c.setFillColor(SLATE)
    c.setFont("Helvetica", 8)
    c.drawString(M + 10, 20, "intodata.nl")


# ── SLIDE 2: Platform Vision ──────────────────────────────────────────────────
def s_vision(c, n, total):
    draw_bg(c)
    draw_header(c, "Platform Vision", "What IntoData KB does — and why it exists")
    draw_footer(c, n, total)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(W / 2, H - 78,
        "Integration projects generate tacit knowledge that disappears when the project ends.")
    c.setFillColor(SLATE)
    c.setFont("Helvetica", 10)
    c.drawCentredString(W / 2, H - 94,
        "Source code (Talend, Workato, Lobster, ...) is opaque. Design decisions live in people's heads.")

    cols = [
        ("1", "Observe",    NAVY,
         "The platform scans integration codebases and infers structure,\nlineage and intent — automatically.",
         "No manual tagging. No extra steps."),
        ("2", "Accumulate", NAVY2,
         "Generated docs, decisions and lessons are ingested into\na vector knowledge base per project.",
         "Every project makes the next one smarter."),
        ("3", "Answer",     TEAL,
         "Consultants ask natural-language questions and receive\ngrounded answers with source citations.",
         "The right knowledge, at the right moment."),
    ]
    cw = (W - 2 * M - 2 * 14) / 3
    bh = 148
    y0 = H - 108

    for i, (num, title, col, body, tag) in enumerate(cols):
        x = M + i * (cw + 14)
        rbox(c, x, y0 - bh, cw, bh, fill=col, stroke=col, r=8)
        c.setFillColor(AMBER)
        c.circle(x + cw / 2, y0 - 20, 14, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(x + cw / 2, y0 - 24, num)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(x + cw / 2, y0 - 45, title)
        c.setFillColor(colors.HexColor("#c8d4ee"))
        for li, line in enumerate(body.split("\n")):
            c.setFont("Helvetica", 9)
            c.drawCentredString(x + cw / 2, y0 - 65 - li * 13, line)
        pill_label(c, tag, x + cw / 2, y0 - bh + 14, fill=AMBER, fg=NAVY, fs=8)

    for i in range(2):
        mid_x = M + (i + 1) * (cw + 14) - 7
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 20)
        c.drawCentredString(mid_x, y0 - bh / 2 - 6, "→")


# ── SLIDE 3: Full System Architecture ─────────────────────────────────────────
def s_arch(c, n, total):
    draw_bg(c)
    draw_header(c, "Full System Architecture",
                "All components, services and the data paths that connect them")
    draw_footer(c, n, total)

    lx = M + 48
    content_w = W - M - lx

    layers = [
        (H - 62,  H - 98,  "USER LAYER"),
        (H - 102, H - 192, "APPLICATION\nLAYER"),
        (H - 196, H - 282, "INTELLIGENCE\nLAYER"),
        (H - 286, H - 356, "STORAGE\nLAYER"),
        (H - 360, H - 400, "OPS"),
    ]
    for y_top, y_bot, label in layers:
        mid = (y_top + y_bot) / 2
        dashed_hline(c, M, y_top, W - M, color=GRID, lw=0.8)
        c.setFillColor(SLATE)
        c.setFont("Helvetica-Bold", 6)
        for li, ln in enumerate(label.split("\n")):
            c.drawString(M, mid + 4 - li * 8, ln)

    bw_u = 96; bh_u = 26

    # USER LAYER
    users = [
        (lx + 30,  H - 78, "RAG Chat UI",        ":3000"),
        (lx + 178, H - 78, "Admin Panel",         ":3000"),
        (lx + 326, H - 78, "intodoc Dashboard",   ":8001"),
        (lx + 490, H - 78, "Claude Code (MCP)",   "stdio"),
    ]
    for (ux, uy, ut, up) in users:
        rbox(c, ux, uy, bw_u, bh_u, fill=SLATE_LT, stroke=NAVY2, r=4)
        t(c, ut, ux + bw_u / 2, uy + 16, "Helvetica-Bold", 8, NAVY, "center")
        pill_label(c, up, ux + bw_u / 2, uy + 5, fill=AMBER, fg=NAVY, fs=6.5)

    # APPLICATION LAYER
    apps = [
        (lx + 10,  H - 185, 160, "RAG Backend (FastAPI)",  NAVY,
         ["Auth · Query · Ingest", "Conversations · Admin", "Benchmarks · Usage · Tenant"]),
        (lx + 185, H - 185, 125, "Frontend (Next.js)",     NAVY2,
         ["Onboarding Wizard", "Chat · Admin UI", "TenantProvider"]),
        (lx + 325, H - 185, 130, "intodoc Dashboard",      NAVY3,
         ["Project Management", "Scan Trigger", "Doc Generation UI"]),
        (lx + 470, H - 185, 125, "MCP Server",             TEAL,
         ["stdio (Claude Code)", "REST :8002", "RagBridge"]),
    ]
    bh_a = 72
    for (ax, ay, aw, at, ac, alines) in apps:
        rbox(c, ax, ay, aw, bh_a, fill=ac, stroke=ac, r=5)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(ax + aw / 2, ay + bh_a - 12, at)
        for li, line in enumerate(alines):
            c.setFont("Helvetica", 7.5)
            c.setFillColor(colors.HexColor("#c8d4ee"))
            c.drawCentredString(ax + aw / 2, ay + bh_a - 26 - li * 12, line)

    # RagBridge arrow
    c.setStrokeColor(AMBER)
    c.setLineWidth(0.9)
    c.setDash([5, 3])
    mid_y = H - 148
    c.line(lx + 470, mid_y, lx + 170, mid_y)
    c.setDash([])
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(lx + 320, mid_y + 5, "POST /ingest/md  (RagBridge)")
    c.line(lx + 170, mid_y, lx + 178, mid_y - 3)
    c.line(lx + 170, mid_y, lx + 178, mid_y + 3)

    # INTELLIGENCE LAYER
    intel = [
        (lx + 10,  H - 275, 148, "Chunker + Embedder",  NAVY,
         ["Text extraction", "Semantic chunking", "Configurable embed provider"]),
        (lx + 173, H - 275, 148, "RAG Chain",            NAVY2,
         ["Dense + BM25 hybrid search", "Cross-encoder reranker", "RBAC group filter"]),
        (lx + 336, H - 275, 130, "LLM Client",           NAVY3,
         ["Provider chain (config)", "Streaming SSE", "Auto fallback"]),
        (lx + 481, H - 275, 115, "intodoc Agents",       TEAL,
         ["Phase 1: Investigation", "Phase 2+3: Analysis+Write", "Concurrent · Resumable"]),
    ]
    bh_i = 70
    for (ix, iy, iw, it, ic, ilines) in intel:
        rbox(c, ix, iy, iw, bh_i, fill=ic, stroke=ic, r=5)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(ix + iw / 2, iy + bh_i - 12, it)
        for li, line in enumerate(ilines):
            c.setFont("Helvetica", 7.5)
            c.setFillColor(colors.HexColor("#c8d4ee"))
            c.drawCentredString(ix + iw / 2, iy + bh_i - 26 - li * 12, line)

    # STORAGE LAYER
    stores = [
        (lx + 10,  H - 348, 128, "PostgreSQL :5432",    NAVY,
         ["Users · Roles · Projects", "QueryUsage · Benchmarks"]),
        (lx + 153, H - 348, 128, "Qdrant :6333",        TEAL,
         ["Dense + sparse vectors", "Payload RBAC indexes"]),
        (lx + 296, H - 348, 128, "Scan Cache (FS)",     NAVY2,
         ["JSON per platform scan", "Read-only after scan"]),
        (lx + 439, H - 348, 120, "Hindsight :8888",     SLATE,
         ["Long-context memory", "Optional / disabled"]),
    ]
    bh_s = 55
    for (sx, sy, sw, st, sc, slines) in stores:
        rbox(c, sx, sy, sw, bh_s, fill=sc, stroke=sc, r=5)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawCentredString(sx + sw / 2, sy + bh_s - 11, st)
        for li, line in enumerate(slines):
            c.setFont("Helvetica", 7.5)
            c.setFillColor(colors.HexColor("#c8d4ee"))
            c.drawCentredString(sx + sw / 2, sy + bh_s - 24 - li * 11, line)

    # OPS LAYER
    ops = ["Nginx + TLS", "Docker Compose (7 services)",
           "MCP container", "deploy-client.sh", "RUNBOOK.md"]
    ops_w = (content_w - (len(ops) - 1) * 6) / len(ops)
    for i, op in enumerate(ops):
        ox = lx + i * (ops_w + 6)
        rbox(c, ox, H - 397, ops_w, 26, fill=SLATE_LT, stroke=SLATE, r=3, lw=0.6)
        t(c, op, ox + ops_w / 2, H - 386, "Helvetica", 7.5, NAVY, "center")

    # Vertical arrows
    connections = [
        (lx + 78,  H - 98,  H - 113),
        (lx + 258, H - 98,  H - 113),
        (lx + 390, H - 98,  H - 113),
        (lx + 538, H - 98,  H - 113),
        (lx + 90,  H - 185, H - 205),
        (lx + 248, H - 185, H - 205),
        (lx + 538, H - 185, H - 205),
        (lx + 84,  H - 275, H - 293),
        (lx + 247, H - 275, H - 293),
        (lx + 538, H - 275, H - 293),
        (lx + 84,  H - 348, H - 360),
    ]
    for (ax, ay1, ay2) in connections:
        arrow_down(c, ax, ay1, ay2 + 10, color=AMBER, lw=0.9)


# ── SLIDE 4: intodoc Deep Dive ────────────────────────────────────────────────
def s_intodoc(c, n, total):
    draw_bg(c)
    draw_header(c, "Subsystem A — intodoc: Code Intelligence",
                "Turns opaque integration source code into structured, searchable documentation")
    draw_footer(c, n, total)

    half = (W - 2 * M - 16) * 0.52
    y0 = H - 65

    # Left: 7-stage pipeline (Phase 1 Investigation added)
    stages = [
        ("Source Code",       SLATE_LT, SLATE,
         "Talend XMI / Workato JSON\nLobster XML / Generic source"),
        ("Scanner CLI",       NAVY,     WHITE,
         "Platform-specific parser\nCredential redaction built-in\nNormalized IR component model"),
        ("JSON Cache",        NAVY2,    WHITE,
         "intodoc-output/ per project\nJobs · groups · lineage\nRead-only after scan"),
        ("Phase 1: Investigate", NAVY3, WHITE,
         "LLM reads scan cache\nAnswers structured fact questions\nBuilds domain context"),
        ("Phase 2: Analyse",  colors.HexColor("#4a3580"), WHITE,
         "Group-level LLM agents (parallel)\nCross-group quality scorecard\nMerged for large groups"),
        ("Phase 3: Write",    TEAL,     WHITE,
         "Per-document LLM agents\nConcurrent · resumable\ndocs_staging/ → docs/ (atomic swap)"),
        ("Markdown Docs",     GREEN,    WHITE,
         "Structured output · ASCII-safe\nRagBridge pushes to RAG\nVersioned per GenerateRun"),
    ]
    bw = half - 20
    bh = 40
    gap = 5
    x0 = M
    y = y0

    for i, (title, fill, fg, desc) in enumerate(stages):
        rbox(c, x0, y - bh, bw, bh, fill=fill, stroke=fill, r=5)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x0 + 10, y - 13, title)
        c.setFillColor(fg)
        for li, line in enumerate(desc.split("\n")):
            c.setFont("Helvetica", 7)
            c.drawString(x0 + 10, y - 24 - li * 10, line)
        if i < len(stages) - 1:
            arrow_down(c, x0 + bw / 2, y - bh, y - bh - gap + 7, color=AMBER)
        y -= bh + gap

    # Right: design decisions
    rx = M + half + 16
    rw = W - M - rx

    decisions = [
        ("Multi-Platform Architecture", NAVY,
         "One adapter per platform (Talend, Workato, Lobster, Generic),\neach producing the same normalised IR. Adding a platform = one adapter."),
        ("Four-Phase AI Generation", NAVY2,
         "Phase 1 builds domain context. Phase 2 analyses groups (parallel agents).\nPhase 2.6 runs a cross-group quality scorecard. Phase 3 writes docs."),
        ("Custom Agent Runtime", NAVY3,
         "Bounded agent harness — no external orchestration framework.\nProviders are config-driven: custom, openai-compat, anthropic, ollama."),
        ("Atomic Staging Swap", TEAL,
         "Docs written to docs_staging/ during generation.\nServer swaps docs/ only when all phases succeed — no partial state."),
        ("Credential Safety", SLATE,
         "Two-layer redaction (regex + structural) before any content\nreaches an LLM. No secrets sent to AI providers."),
    ]
    dh = (y0 - 30 - 15) / len(decisions)
    dy = y0

    for title, col, body in decisions:
        rbox(c, rx, dy - dh + 5, rw, dh - 7, fill=col, stroke=col, r=5)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(rx + 10, dy - dh + dh - 5 - 12, title)
        c.setFillColor(colors.HexColor("#c8d4ee"))
        for li, line in enumerate(body.split("\n")):
            c.setFont("Helvetica", 7.5)
            c.drawString(rx + 10, dy - dh + dh - 5 - 26 - li * 12, line)
        dy -= dh


# ── SLIDE 5: RAG Pipeline ─────────────────────────────────────────────────────
def s_rag(c, n, total):
    draw_bg(c)
    draw_header(c, "Subsystem B — RAG Pipeline: Knowledge Q&A",
                "From raw documents to grounded, project-scoped answers")
    draw_footer(c, n, total)

    y0 = H - 65
    half = (W - 2 * M - 16) / 2

    def flow_col(x, title, stages, bh, gap):
        t(c, title, x, y0 + 4, "Helvetica-Bold", 10, NAVY)
        c.setFillColor(AMBER)
        c.rect(x, y0 - 2, 80, 2, fill=1, stroke=0)
        y = y0 - 16
        for i, (stitle, fill, fg, desc) in enumerate(stages):
            rbox(c, x, y - bh, half - 10, bh, fill=fill, stroke=fill, r=5)
            c.setFillColor(AMBER)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawString(x + 8, y - 13, stitle)
            c.setFillColor(fg)
            for li, line in enumerate(desc.split("\n")):
                c.setFont("Helvetica", 7.5)
                c.drawString(x + 8, y - 25 - li * 11, line)
            if i < len(stages) - 1:
                arrow_down(c, x + (half - 10) / 2, y - bh, y - bh - gap + 8, color=AMBER)
            y -= bh + gap

    ingest_stages = [
        ("Document Input",    SLATE_LT, SLATE,
         "PDF / DOCX / PPTX / XLSX / MD\nManual upload or RagBridge POST"),
        ("Parse & Chunk",     NAVY,     WHITE,
         "Format-specific text extraction\nSemantic chunking (boundary-aware)\nFixed fallback: ~500 tokens + overlap"),
        ("Embed",             NAVY2,    WHITE,
         "Dense: configurable embedding provider\nSparse: BM25 keyword vectors\nDimension-aware collection mgmt"),
        ("Qdrant Upsert",     TEAL,     WHITE,
         "Dense + BM25 sparse vectors stored\nPayload: allowed_groups, doc_id, project_id\nIndexed for fast RBAC filter"),
        ("Postgres Record",   NAVY3,    WHITE,
         "Document row: filename, uploader\nchunk_count, project_id, timestamps\nIngestionJob tracks status"),
    ]

    query_stages = [
        ("User Question",     SLATE_LT, SLATE,
         "POST /api/v1/query or /query/stream\nJWT auth (httpOnly cookie)\nproject_id + intent classification"),
        ("Memory Recall",     NAVY,     WHITE,
         "Hindsight: user memories (optional)\nQuality insights, past corrections\nBudget-aware recall (non-blocking)"),
        ("Hybrid Search",     NAVY2,    WHITE,
         "Dense cosine + BM25 sparse (parallel)\nRBAC filter injected server-side\nRRF fusion of both result lists"),
        ("Rerank",            TEAL,     WHITE,
         "Cross-encoder re-scores top candidates\nMin score threshold applied\nTop-k selected for context window"),
        ("LLM Generate",      NAVY3,    WHITE,
         "Context + memory + history assembled\nConfigurable provider chain generates\nAnswer streamed via SSE token-by-token"),
        ("Usage Persist",     NAVY,     WHITE,
         "Tokens · cost · latency · model\nQueryUsage row in Postgres\nDashboard-visible per project"),
    ]

    flow_col(M, "Ingestion Path", ingest_stages, bh=46, gap=7)
    flow_col(M + half + 16, "Query Path", query_stages, bh=38, gap=6)


# ── SLIDE 6: Provider-Agnostic AI ─────────────────────────────────────────────
def s_ai(c, n, total):
    draw_bg(c)
    draw_header(c, "The Provider-Agnostic AI Layer",
                "LLM and embedding providers are runtime configuration — the platform is not tied to any vendor")
    draw_footer(c, n, total)

    y0 = H - 68

    rbox(c, M, 28, W - 2 * M, y0 - 30, fill=SLATE_LT, stroke=NAVY2, r=10, lw=0.8)
    t(c, "PLATFORM (provider-neutral core)", M + 10, y0 - 12, "Helvetica-Bold", 8, SLATE)

    comp_y = y0 - 38
    comps = [
        (M + 20,  comp_y, 148, 52, "RAG Chain",        NAVY,
         ["Takes query vector", "Calls Qdrant search", "Assembles context"]),
        (M + 182, comp_y, 148, 52, "LLM Client",       NAVY2,
         ["Tries provider[0]", "Falls back to [1..n]", "Streams tokens via SSE"]),
        (M + 344, comp_y, 148, 52, "Embedder",         NAVY3,
         ["Tries provider[0]", "Falls back to [1..n]", "Returns float[] vector"]),
        (M + 506, comp_y, 148, 52, "intodoc Agents",   TEAL,
         ["Phase 1/2/3 orchestration", "Custom agent runtime", "Calls LLM Client internally"]),
    ]
    for (bx, by, bw, bh, bt, bc, blines) in comps:
        rbox(c, bx, by, bw, bh, fill=bc, stroke=bc, r=6)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawCentredString(bx + bw / 2, by + bh - 12, bt)
        for li, ln in enumerate(blines):
            c.setFont("Helvetica", 7.5)
            c.setFillColor(colors.HexColor("#c8d4ee"))
            c.drawCentredString(bx + bw / 2, by + bh - 26 - li * 12, ln)

    arrow_down(c, M + 256, comp_y, comp_y - 32, color=AMBER)
    arrow_down(c, M + 418, comp_y, comp_y - 32, color=AMBER)
    arrow_down(c, M + 580, comp_y, comp_y - 32, color=AMBER)

    pool_y = comp_y - 36
    pool_bh = 62

    rbox(c, M + 170, pool_y - pool_bh, 175, pool_bh, fill=AMBER_LT, stroke=AMBER, r=5)
    t(c, "LLM Provider Pool (config-driven)",
      M + 170 + 87, pool_y - 10, "Helvetica-Bold", 8, NAVY, "center")
    t(c, "OpenAI-compatible APIs",
      M + 170 + 87, pool_y - 23, "Helvetica", 7.5, SLATE, "center")
    t(c, "Anthropic API",
      M + 170 + 87, pool_y - 34, "Helvetica", 7.5, SLATE, "center")
    t(c, "Local Ollama models",
      M + 170 + 87, pool_y - 45, "Helvetica", 7.5, SLATE, "center")
    t(c, "Any compatible interface",
      M + 170 + 87, pool_y - 56, "Helvetica", 7.5, SLATE, "center")

    rbox(c, M + 362, pool_y - pool_bh, 175, pool_bh, fill=TEAL_LT, stroke=TEAL, r=5)
    t(c, "Embedding Provider Pool (config-driven)",
      M + 362 + 87, pool_y - 10, "Helvetica-Bold", 8, TEAL, "center")
    t(c, "Cloud embedding APIs (high-dim)",
      M + 362 + 87, pool_y - 23, "Helvetica", 7.5, SLATE, "center")
    t(c, "Local Ollama embedding models",
      M + 362 + 87, pool_y - 34, "Helvetica", 7.5, SLATE, "center")
    t(c, "BM25 sparse (always on)",
      M + 362 + 87, pool_y - 45, "Helvetica", 7.5, SLATE, "center")
    t(c, "Swappable — no downstream changes",
      M + 362 + 87, pool_y - 56, "Helvetica", 7.5, SLATE, "center")

    fb_y = pool_y - pool_bh - 14
    rbox(c, M + 20, fb_y - 44, W - 2 * M - 40, 46, fill=NAVY, stroke=NAVY, r=5)
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(M + 30, fb_y - 10, "Provider Chain Mechanics:")
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 8)
    c.drawString(M + 30, fb_y - 22,
        "Provider lists are ordered in config. On failure, timeout or error the next provider is tried automatically — no code change required.")
    c.drawString(M + 30, fb_y - 34,
        "Qdrant collection auto-recreates if embedding dimension changes. Usage tracking fires regardless of which provider answered.")


# ── SLIDE 7: RBAC & Security ──────────────────────────────────────────────────
def s_rbac(c, n, total):
    draw_bg(c)
    draw_header(c, "Security Architecture — RBAC & Auth",
                "Project-scoped access enforced from API gateway to vector search")
    draw_footer(c, n, total)

    y0 = H - 65
    half = (W - 2 * M - 16) * 0.48

    t(c, "Authentication & Session Flow", M, y0 + 4, "Helvetica-Bold", 10, NAVY)
    c.setFillColor(AMBER)
    c.rect(M, y0 - 2, 140, 2, fill=1, stroke=0)

    auth = [
        ("Login Request",         SLATE_LT, SLATE, "Email + password submitted\nRate limited: 5 req/min"),
        ("Credential Verify",     NAVY,     WHITE,  "bcrypt hash compare\nDB lookup, is_active check"),
        ("MFA Challenge (opt.)",  NAVY2,    WHITE,  "If TOTP enabled:\npyotp.verify() → mfa_token JWT"),
        ("Token Pair Issued",     NAVY3,    WHITE,  "Access JWT (60 min TTL)\nRefresh token (SHA-256 hash in DB)"),
        ("httpOnly Cookies Set",  TEAL,     WHITE,  "Tokens set as httpOnly cookies\nXSS-resistant, SameSite=lax"),
        ("RBAC Middleware",       NAVY,     WHITE,  "Every request: decode JWT\nLoad roles + 9 permissions\nEnforced before handler"),
    ]
    bw = half - 10; bh = 42; gap = 6
    y = y0 - 16
    for i, (title, fill, fg, desc) in enumerate(auth):
        rbox(c, M, y - bh, bw, bh, fill=fill, stroke=fill, r=4)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(M + 8, y - 13, title)
        c.setFillColor(fg)
        for li, line in enumerate(desc.split("\n")):
            c.setFont("Helvetica", 7.5)
            c.drawString(M + 8, y - 25 - li * 11, line)
        if i < len(auth) - 1:
            arrow_down(c, M + bw / 2, y - bh, y - bh - gap + 8, color=AMBER)
        y -= bh + gap

    rx = M + half + 16
    rw = W - M - rx
    t(c, "RBAC Model", rx, y0 + 4, "Helvetica-Bold", 10, NAVY)
    c.setFillColor(AMBER)
    c.rect(rx, y0 - 2, 60, 2, fill=1, stroke=0)

    roles = [
        ("admin",     NAVY,  "Full platform: user mgmt, all projects, system config"),
        ("manager",   NAVY2, "Manage projects, assign users, view usage dashboards"),
        ("developer", NAVY3, "Query + ingest within explicitly assigned projects only"),
    ]
    t(c, "Roles", rx, y0 - 16, "Helvetica-Bold", 9, NAVY)
    ry = y0 - 30
    for role, col, desc in roles:
        rbox(c, rx, ry - 20, rw, 22, fill=col, stroke=col, r=4)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(rx + 8, ry - 8, role)
        c.setFillColor(WHITE)
        c.setFont("Helvetica", 7.5)
        c.drawString(rx + 72, ry - 8, desc)
        ry -= 28

    t(c, "Vector Search Filter (every query)", rx, ry - 6, "Helvetica-Bold", 9, NAVY)
    rbox(c, rx, ry - 58, rw, 50, fill=TEAL, stroke=TEAL, r=5)
    lines = [
        "Every chunk stores allowed_groups in its Qdrant payload.",
        "Every query applies must filter: user's groups ∩ chunk's groups ≠ ∅",
        "Users in group 'project-A' cannot see chunks from 'project-B'.",
    ]
    c.setFillColor(WHITE)
    for li, ln in enumerate(lines):
        c.setFont("Helvetica", 8)
        c.drawString(rx + 8, ry - 24 - li * 12, ln)
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(rx + 8, ry - 56, "RBAC enforced at Qdrant level — not post-hoc Python filtering.")

    t(c, "9 Discrete Permissions", rx, ry - 68, "Helvetica-Bold", 9, NAVY)
    perms = ["query_rag", "ingest_documents", "manage_users", "manage_roles",
             "assign_projects", "view_all_projects", "manage_projects",
             "view_audit_logs", "view_assigned_projects"]
    pw = (rw - 8) / 3
    for i, p in enumerate(perms):
        px2 = rx + (i % 3) * (pw + 4)
        py2 = ry - 84 - (i // 3) * 16
        rbox(c, px2, py2, pw, 13, fill=NAVY2, stroke=NAVY2, r=3, lw=0)
        c.setFillColor(colors.HexColor("#c8d4ee"))
        c.setFont("Helvetica", 7)
        c.drawCentredString(px2 + pw / 2, py2 + 3, p)


# ── SLIDE 8: Data & Storage ───────────────────────────────────────────────────
def s_storage(c, n, total):
    draw_bg(c)
    draw_header(c, "Data & Storage Architecture",
                "What lives where — and the design rationale behind each store")
    draw_footer(c, n, total)

    y0 = H - 65
    stores = [
        {
            "name": "PostgreSQL 16", "port": ":5432", "col": NAVY,
            "purpose": "Relational truth — identity, access control, usage, benchmarks",
            "schema": [
                ("users",               "id, email, hashed_password, role, totp_secret, is_active"),
                ("refresh_tokens",      "token_hash (SHA-256), user_id, expires_at, revoked"),
                ("projects",            "id, name, slug, allowed_groups[]"),
                ("project_assignments", "user_id ↔ project_id  (M2M)"),
                ("documents",           "id, filename, project_id, chunk_count, uploaded_by"),
                ("conversations",       "id, user_id + conversation_messages[]"),
                ("query_usage",         "tokens, cost_usd, latency_ms, model, provider, project_id"),
                ("benchmark_runs",      "question, answer, precision@5, mrr, llm_judge_score"),
            ],
            "rationale": "ACID for identity & billing. Async SQLAlchemy. Schema managed by Alembic. Both RAG and MCP subsystems share this instance."
        },
        {
            "name": "Qdrant", "port": ":6333/6334", "col": TEAL,
            "purpose": "Vector similarity search — dense semantic + sparse BM25 per chunk",
            "schema": [
                ("collection (per project/tenant)", "dense vector (semantic similarity)"),
                ("",                                "sparse BM25 vector (keyword precision)"),
                ("payload",                         "chunk_text, doc_id, project_id, allowed_groups[]"),
                ("indexes",                         "allowed_groups (keyword), document_id (integer)"),
                ("",                                "project_id (keyword) for scoped search"),
                ("",                                "Collection auto-recreated on dimension change"),
            ],
            "rationale": "Hybrid search outperforms pure semantic on technical queries. Payload RBAC filter eliminates cross-project leakage at DB level, not application level."
        },
        {
            "name": "Scan Cache (filesystem)", "port": "intodoc-output/", "col": NAVY2,
            "purpose": "Normalised intermediate representation from the intodoc scanner CLI",
            "schema": [
                ("per-project JSON",  "jobs, components, groups, lineage"),
                ("",                  "platform metadata normalised to common IR"),
                ("",                  "redaction_report.json alongside scan_result.json"),
                ("",                  "read-only after scan; re-scan overwrites"),
                ("docs_staging/",     "agent output staging area (atomic swap on success)"),
                ("docs/",             "final Markdown — replaced only on full-phase success"),
            ],
            "rationale": "Filesystem cache decouples scanner from doc generation. Staging swap guarantees users always see a complete, consistent doc set per GenerateRun."
        },
    ]

    col_w = (W - 2 * M - 2 * 14) / 3
    for si, store in enumerate(stores):
        x = M + si * (col_w + 14)
        col = store["col"]
        rbox(c, x, 28, col_w, y0 - 30, fill=col, stroke=col, r=7)

        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 10, y0 - 12, store["name"])
        pill_label(c, store["port"], x + col_w - 30, y0 - 5, fill=AMBER, fg=NAVY, fs=7)

        c.setFillColor(colors.HexColor("#c8d4ee"))
        c.setFont("Helvetica", 8)
        y = y0 - 26
        wrapped(c, store["purpose"], x + 10, y, col_w - 20, size=8,
                color=colors.HexColor("#c8d4ee"), leading=12)
        y -= 24

        c.setStrokeColor(AMBER)
        c.setLineWidth(0.5)
        c.line(x + 8, y, x + col_w - 8, y)
        y -= 12

        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 8, y, "Schema / contents")
        y -= 12

        for tname, tdesc in store["schema"]:
            if tname:
                c.setFillColor(WHITE)
                c.setFont("Helvetica-Bold", 7.5)
                c.drawString(x + 8, y, tname)
                y -= 10
            c.setFillColor(colors.HexColor("#b8c8e0"))
            c.setFont("Helvetica", 7)
            c.drawString(x + 12, y, tdesc)
            y -= 10

        y -= 4
        c.setStrokeColor(AMBER)
        c.line(x + 8, y, x + col_w - 8, y)
        y -= 12

        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 8, y, "Design rationale")
        y -= 11
        for line in store["rationale"].split(". "):
            if line:
                c.setFillColor(colors.HexColor("#c8d4ee"))
                c.setFont("Helvetica", 7)
                c.drawString(x + 8, y, line.strip() + ("." if not line.endswith(".") else ""))
                y -= 10


# ── SLIDE 9: Multi-Client Tenancy ─────────────────────────────────────────────
def s_tenant(c, n, total):
    draw_bg(c)
    draw_header(c, "Multi-Client Tenancy & Deployment Model",
                "One VPS per client — full isolation through deploy-client.sh")
    draw_footer(c, n, total)

    y0 = H - 68

    # Left: isolation model
    lw2 = (W - 2 * M - 20) * 0.44
    t(c, "Per-Client Isolation Guarantees", M, y0 + 4, "Helvetica-Bold", 10, NAVY)
    c.setFillColor(AMBER)
    c.rect(M, y0 - 2, 180, 2, fill=1, stroke=0)

    isolations = [
        (NAVY,  "PostgreSQL Database",
         "Separate DB: intodata_kb_{client}\nNo cross-client data access"),
        (TEAL,  "Qdrant Collection",
         "Scoped collection: {client}_documents\nVectors fully isolated"),
        (NAVY2, "Secrets & JWT",
         "Randomised per-client: JWT_SECRET_KEY\nPOSTGRES_PASSWORD, MCP_INGEST_KEY"),
        (NAVY3, "nginx + TLS",
         "Separate server block per client\nIndividual certbot cert per domain"),
        (SLATE, "Docker Compose Project",
         "docker compose -p {client}\nPrefixes all containers, networks, volumes"),
        (colors.HexColor("#4a3580"), "MCP Container",
         "mcp-server compose service\nInstance per client stack"),
    ]
    bh_i = 44; gap_i = 6
    y = y0 - 16
    for col, title, desc in isolations:
        rbox(c, M, y - bh_i, lw2, bh_i, fill=col, stroke=col, r=5)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(M + 10, y - 14, title)
        c.setFillColor(colors.HexColor("#c8d4ee"))
        for li, line in enumerate(desc.split("\n")):
            c.setFont("Helvetica", 7.5)
            c.drawString(M + 10, y - 26 - li * 11, line)
        y -= bh_i + gap_i

    # Right: deploy flow
    rx = M + lw2 + 20
    rw2 = W - M - rx
    t(c, "deploy-client.sh Workflow", rx, y0 + 4, "Helvetica-Bold", 10, NAVY)
    c.setFillColor(AMBER)
    c.rect(rx, y0 - 2, 160, 2, fill=1, stroke=0)

    steps = [
        (NAVY,  "1. Invoke Script",
         "./deploy/deploy-client.sh\n--client-name acme --port-offset 0\n--base-domain nexus.intodata.nl"),
        (NAVY2, "2. Generate Secrets",
         "Randomise JWT_SECRET_KEY\nPOSTGRES_PASSWORD, MCP_INGEST_KEY\nWrite /etc/intodata-kb/envs/acme.env"),
        (NAVY3, "3. Render nginx Block",
         "envsubst on nexus-client.conf.template\nWrite /etc/nginx/sites-available/nexus-acme.conf\n(nginx -t to validate)"),
        (TEAL,  "4. Launch Stack",
         "docker compose --env-file acme.env -p acme up -d\nHealth checks gate service startup\ncertbot obtains Let's Encrypt cert"),
        (GREEN, "5. Verify MCP Service",
         "docker compose ps mcp-server\nREST API :8002 available locally\nClaude Code stdio remains available separately"),
    ]
    bh_s = 48; gap_s = 6
    y2 = y0 - 16
    for col, title, desc in steps:
        rbox(c, rx, y2 - bh_s, rw2, bh_s, fill=col, stroke=col, r=5)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(rx + 10, y2 - 13, title)
        c.setFillColor(colors.HexColor("#c8d4ee"))
        for li, line in enumerate(desc.split("\n")):
            c.setFont("Helvetica", 7.5)
            c.drawString(rx + 10, y2 - 25 - li * 11, line)
        if steps.index((col, title, desc)) < len(steps) - 1:
            arrow_down(c, rx + rw2 / 2, y2 - bh_s, y2 - bh_s - gap_s + 7, color=AMBER)
        y2 -= bh_s + gap_s


# ── SLIDE 10: Deployment Topology ─────────────────────────────────────────────
def s_deploy(c, n, total):
    draw_bg(c)
    draw_header(c, "Deployment Topology",
                "Client VPS — services, host processes and traffic routing")
    draw_footer(c, n, total)

    y0 = H - 65

    t(c, "Internet / Browser / Claude Code CLI", W / 2, y0, "Helvetica", 9.5, SLATE, "center")
    arrow_down(c, W / 2, y0 - 10, y0 - 28, color=AMBER, lw=1.2)
    t(c, "HTTPS :443", W / 2 + 6, y0 - 18, "Helvetica", 7.5, SLATE)

    vps_y = 26
    vps_h = y0 - 32 - vps_y
    rbox(c, M + 10, vps_y, W - 2 * M - 20, vps_h, fill=SLATE_LT, stroke=NAVY2, r=8, lw=0.8)
    t(c, "CLIENT VPS  (one per client — no shared infrastructure)",
      M + 20, vps_y + vps_h - 10, "Helvetica-Bold", 8, SLATE)

    ng_w = 220; ng_h = 32
    ng_x = W / 2 - ng_w / 2; ng_y = y0 - 38
    rbox(c, ng_x, ng_y, ng_w, ng_h, fill=SLATE, stroke=SLATE, r=5)
    t(c, "Nginx  (TLS termination + reverse proxy)", W / 2, ng_y + 21,
      "Helvetica-Bold", 9, WHITE, "center")
    t(c, "certbot / Let's Encrypt  ·  Port 80/443  ·  UFW: only 22/80/443 open", W / 2, ng_y + 10,
      "Helvetica", 7, colors.HexColor("#c8d4ee"), "center")

    arrow_down(c, W / 2, ng_y, ng_y - 14, color=AMBER, lw=1.2)

    dc_x = M + 30; dc_y = vps_y + 60
    dc_w = W - 2 * M - 60; dc_h = ng_y - 18 - dc_y
    rbox(c, dc_x, dc_y, dc_w, dc_h, fill=WHITE, stroke=NAVY2, r=6, lw=0.7)
    t(c, "Docker Compose — 6 Services (all bound to 127.0.0.1)", dc_x + 10, dc_y + dc_h - 10,
      "Helvetica-Bold", 8, NAVY2)

    svcs = [
        ("frontend", ":3000",  NAVY3),
        ("backend",  ":8000",  NAVY),
        ("postgres", ":5432",  NAVY2),
        ("qdrant",   ":6333",  TEAL),
        ("dashboard",":8001",  NAVY3),
        ("hindsight",":8888",  SLATE),
    ]
    svc_n = len(svcs)
    svc_w = (dc_w - 20 - (svc_n - 1) * 8) / svc_n
    svc_h = dc_h - 30
    for i, (name, port, col) in enumerate(svcs):
        sx = dc_x + 10 + i * (svc_w + 8)
        sy = dc_y + 8
        rbox(c, sx, sy, svc_w, svc_h, fill=col, stroke=col, r=5)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(sx + svc_w / 2, sy + svc_h / 2 + 5, name)
        c.setFillColor(AMBER)
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(sx + svc_w / 2, sy + svc_h / 2 - 7, port)

    note_y = vps_y + 12
    note_w = (dc_w - 8) / 2
    rbox(c, dc_x, note_y, note_w, 40, fill=TEAL, stroke=TEAL, r=4)
    t(c, "MCP Server  (Docker Compose service)", dc_x + 8, note_y + 28,
      "Helvetica-Bold", 8, WHITE)
    t(c, "mcp-server container  ·  REST :8002", dc_x + 8, note_y + 17,
      "Helvetica", 7.5, colors.HexColor("#c8d4ee"))
    t(c, "Bind mounts: data, scan cache, docs, packs", dc_x + 8, note_y + 7,
      "Helvetica", 7, colors.HexColor("#c8d4ee"))

    rbox(c, dc_x + note_w + 8, note_y, note_w, 40, fill=NAVY, stroke=NAVY, r=4)
    t(c, "Ollama  (host process — GPU/CPU)", dc_x + note_w + 18, note_y + 28,
      "Helvetica-Bold", 8, AMBER)
    t(c, "host.docker.internal:11434  (reachable from containers)", dc_x + note_w + 18, note_y + 17,
      "Helvetica", 7.5, WHITE)
    t(c, "LLM + embedding fallback — no external API required", dc_x + note_w + 18, note_y + 7,
      "Helvetica", 7, colors.HexColor("#c8d4ee"))


# ── SLIDE 11: End-to-End Knowledge Flow ───────────────────────────────────────
def s_flow(c, n, total):
    draw_bg(c)
    draw_header(c, "End-to-End Knowledge Flow",
                "How raw integration source code becomes an answerable knowledge base")
    draw_footer(c, n, total)

    y0 = H - 68

    stages = [
        ("1", "Integration\nSource Code",    SLATE_LT, SLATE,
         "Talend / Workato\nLobster / Generic"),
        ("2", "intodoc\nScanner",             NAVY,     WHITE,
         "Platform parser\nCredential redaction\nNormalized JSON cache"),
        ("3", "AI Doc\nAgents (4 phases)",   NAVY2,    WHITE,
         "Investigate · Analyse\nQuality check · Write\nConcurrent · resumable"),
        ("4", "Markdown\nDocuments",          TEAL,     WHITE,
         "Structured output\nASCII-safe\nPer-project"),
        ("5", "RAG\nIngestion",               NAVY3,    WHITE,
         "Parse · chunk · embed\nDense + sparse vectors\nQdrant upsert"),
        ("6", "Knowledge\nBase",              NAVY,     WHITE,
         "RBAC-filtered\nhybrid vector search\nProject-scoped"),
        ("7", "Consultant\nQ&A",              GREEN,    WHITE,
         "Natural language query\nGrounded answer + SSE\nSource citations"),
    ]

    ns = len(stages)
    sw = (W - 2 * M - (ns - 1) * 5) / ns
    sh = 104
    y_box = y0 - 18 - sh

    for i, (num, title, fill, fg, desc) in enumerate(stages):
        x = M + i * (sw + 5)
        rbox(c, x, y_box, sw, sh, fill=fill, stroke=fill, r=6)
        c.setFillColor(AMBER)
        c.circle(x + sw / 2, y_box + sh - 14, 10, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(x + sw / 2, y_box + sh - 17, num)
        c.setFillColor(fg if fg != SLATE else NAVY)
        c.setFont("Helvetica-Bold", 8.5)
        for li, ln in enumerate(title.split("\n")):
            c.drawCentredString(x + sw / 2, y_box + sh - 34 - li * 11, ln)
        c.setFillColor(fg)
        for li, ln in enumerate(desc.split("\n")):
            c.setFont("Helvetica", 7.5)
            c.drawCentredString(x + sw / 2, y_box + sh - 60 - li * 11, ln)
        if i < ns - 1:
            c.setFillColor(AMBER)
            c.setFont("Helvetica-Bold", 16)
            c.drawCentredString(x + sw + 2.5, y_box + sh / 2 - 5, "›")

    py = y_box - 18
    hw = (W - 2 * M - 12) / 2

    rbox(c, M, py - 50, hw, 52, fill=NAVY, stroke=NAVY, r=6)
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(M + 10, py - 10, "Accumulation Principle")
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 8.5)
    c.drawString(M + 10, py - 24,
        "Every project adds to the shared vector store. Later projects benefit from")
    c.drawString(M + 10, py - 36,
        "earlier ones — decisions, patterns and lessons searchable across the portfolio.")

    rbox(c, M + hw + 12, py - 50, hw, 52, fill=TEAL, stroke=TEAL, r=6)
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(M + hw + 22, py - 10, "Zero-Overhead Capture")
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 8.5)
    c.drawString(M + hw + 22, py - 24,
        "Consultants do not tag, categorise or manually document. The scanner observes")
    c.drawString(M + hw + 22, py - 36,
        "code; the AI agent writes the docs; the platform ingests them. Zero extra steps.")


# ── SLIDE 12: API Surface ─────────────────────────────────────────────────────
def s_api(c, n, total):
    draw_bg(c)
    draw_header(c, "API Surface",
                "RAG Backend (:8000) and MCP Server (:8002) — route groups, purpose and access level")
    draw_footer(c, n, total)

    y0 = H - 68

    groups = [
        ("Auth  /api/v1/auth", NAVY, [
            ("POST /login",           "Credential verify → JWT pair in httpOnly cookies",    "public"),
            ("POST /logout",          "Revoke refresh token, clear cookies",                  "auth"),
            ("POST /refresh",         "Rotate access + refresh token pair",                   "auth"),
            ("POST /verify-mfa",      "Exchange mfa_token for full session after TOTP",       "mfa_token"),
            ("POST /bootstrap-admin", "Seed first admin (one-time, disabled after)",          "open-once"),
        ]),
        ("Query  /api/v1/query", NAVY2, [
            ("POST /query",          "Full RAG chain: embed → search → rerank → generate",  "developer+"),
            ("POST /query/stream",   "Same chain, SSE streaming response token-by-token",    "developer+"),
            ("POST /query/debug",    "Returns retrieved chunks + scores pre-generation",     "developer+"),
            ("POST /conversations",  "Multi-turn conversation session management",           "developer+"),
            ("GET  /projects",       "RBAC-filtered project listing for scoping",            "developer+"),
        ]),
        ("Ingest  /api/v1", TEAL, [
            ("POST /documents",      "Upload PDF/DOCX/PPTX/XLSX → chunk → embed → Qdrant",  "developer+"),
            ("POST /ingest/md",      "Ingest Markdown string (RagBridge from MCP)",          "ingest-key"),
            ("GET  /documents",      "List documents in a project (RBAC-filtered)",          "developer+"),
            ("DELETE /documents/:id","Remove document + all Qdrant vectors (cascade)",       "manager+"),
        ]),
        ("Admin  /api/v1/admin", NAVY3, [
            ("CRUD /users",          "User management: create, update, deactivate",          "admin"),
            ("CRUD /roles",          "Role + permission management",                          "admin"),
            ("POST /assignments",    "Assign user to project (project_assignments)",         "manager+"),
            ("GET  /audit-logs",     "Full audit trail of platform actions",                 "admin"),
            ("GET  /usage/summary",  "Aggregated token + cost data per project/model",       "manager+"),
        ]),
        ("System & KB  /api/v1", SLATE, [
            ("GET  /tenant/config",   "Public: name, logo, colors for TenantProvider",       "public"),
            ("GET  /health",          "DB, Qdrant, Ollama, Hindsight health check",          "public"),
            ("POST /benchmark/run",   "Run benchmark Q&A set against RAG chain",             "manager+"),
            ("GET/DEL /memory/:uid",  "Manage Hindsight long-context memory per user",       "admin"),
            ("GET  /onboarding/config","Wizard config for initial setup flow",               "auth"),
        ]),
        ("MCP Server  :8002", TEAL, [
            ("GET/POST /mcp/projects","List / register code repositories",                   "jwt"),
            ("POST /mcp/scan",        "Trigger intodoc scan for a project",                  "jwt"),
            ("POST /mcp/generate",    "Start 4-phase doc generation (custom runtime)",       "jwt"),
            ("GET  /mcp/generate-runs/:id","Poll doc generation run status + progress",      "jwt"),
            ("GET  /logs/stream",     "SSE real-time log stream during scan/generation",     "jwt"),
        ]),
    ]

    gn = len(groups)
    gw = (W - 2 * M - (gn - 1) * 6) / gn
    for gi, (gname, gcol, routes) in enumerate(groups):
        gx = M + gi * (gw + 6)
        gh = y0 - 28
        rbox(c, gx, 28, gw, gh, fill=gcol, stroke=gcol, r=6)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(gx + 6, gh + 20, gname)

        ry = gh + 8
        for route, desc, access in routes:
            ry -= 12
            pill_label(c, access, gx + gw - 22, ry + 5,
                       fill=AMBER if access in ("public", "open-once") else colors.HexColor("#3d5299"),
                       fg=NAVY if access in ("public", "open-once") else WHITE, fs=5.8)
            c.setFillColor(WHITE)
            c.setFont("Courier", 6)
            c.drawString(gx + 6, ry, route)
            ry -= 8
            c.setFillColor(colors.HexColor("#b8c8e0"))
            words = desc.split()
            line = ""
            for word in words:
                test = (line + " " + word).strip()
                if len(test) < 30:
                    line = test
                else:
                    c.setFont("Helvetica", 6)
                    c.drawString(gx + 8, ry, line)
                    line = word; ry -= 7
            if line:
                c.setFont("Helvetica", 6)
                c.drawString(gx + 8, ry, line)
            ry -= 5


# ── SLIDE 13: Summary ─────────────────────────────────────────────────────────
def s_summary(c, n, total):
    draw_bg(c)

    t(c, "IntoData KB — Architecture at a Glance",
      W / 2, H - 48, "Helvetica-Bold", 24, NAVY, "center")
    c.setFillColor(AMBER)
    c.rect(W / 2 - 110, H - 56, 220, 3, fill=1, stroke=0)

    cards = [
        ("Code Intelligence", NAVY,
         "intodoc scans Talend, Workato, Lobster and generic integration platforms.",
         "Four AI phases: investigate, analyse, quality-check, write — concurrent & resumable.",
         "Markdown output is atomic — staging swap ensures a consistent doc set per run."),
        ("RAG Knowledge Q&A", NAVY2,
         "Hybrid vector search: dense semantic + BM25 sparse, cross-encoder reranked.",
         "Every query is RBAC-filtered at the Qdrant payload level (allowed_groups).",
         "Answers stream in real-time via SSE with source citations + usage tracking."),
        ("Provider-Agnostic AI", TEAL,
         "LLM and embedding providers are ordered config chains — no vendor lock-in.",
         "Automatic fallback: if provider[0] fails, provider[1] is tried — no code change.",
         "Swapping or adding a provider requires only an env var / config update."),
        ("Enterprise Operations", NAVY3,
         "Usage tracked per call: tokens, cost, latency, model, provider, project.",
         "One VPS per client — deploy-client.sh generates isolated env, nginx, Docker stack.",
         "Reproducible delivery: RUNBOOK.md + SMOKE-TEST.md gate every deployment."),
    ]

    cw = (W - 2 * M - 3 * 14) / 4
    ch = 130
    y0 = H - 72

    for i, (title, col, l1, l2, l3) in enumerate(cards):
        cx = M + i * (cw + 14)
        rbox(c, cx, y0 - ch, cw, ch, fill=col, stroke=col, r=8)
        c.setFillColor(AMBER)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawCentredString(cx + cw / 2, y0 - 16, title)
        for li, line in enumerate([l1, l2, l3]):
            c.setFillColor(colors.HexColor("#c8d4ee"))
            words = line.split()
            ln = ""; ly = y0 - 34 - li * 30
            for word in words:
                test = (ln + " " + word).strip()
                if len(test) * 4.5 < cw - 16:
                    ln = test
                else:
                    c.setFont("Helvetica", 7.5)
                    c.drawCentredString(cx + cw / 2, ly, ln)
                    ln = word; ly -= 11
            if ln:
                c.setFont("Helvetica", 7.5)
                c.drawCentredString(cx + cw / 2, ly, ln)

    c.setFillColor(SLATE)
    c.setFont("Helvetica", 8)
    c.drawCentredString(W / 2, 20,
        "intodata.nl  ·  Integration Intelligence Platform  ·  May 2026  ·  Confidential")

    draw_footer(c, n, total)


# ── Build ──────────────────────────────────────────────────────────────────────
SLIDES = [
    s_cover, s_vision, s_arch, s_intodoc, s_rag,
    s_ai, s_rbac, s_storage, s_tenant, s_deploy,
    s_flow, s_api, s_summary,
]

if __name__ == "__main__":
    out = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "IntoData_KB_Architecture.pdf"
    )
    cv = canvas.Canvas(out, pagesize=(W, H))
    for i, fn in enumerate(SLIDES):
        fn(cv, i + 1, len(SLIDES))
        cv.showPage()
    cv.save()
    print(f"Written: {out}  ({len(SLIDES)} slides)")
