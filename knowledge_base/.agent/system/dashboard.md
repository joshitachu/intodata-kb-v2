# Dashboard

The dashboard (`dashboard/`) is a React control panel for managing intodoc projects,
triggering scans, viewing logs, generating docs, and reviewing quality.

## Technology

- React 19 + TypeScript 5
- Vite 6 (build tool, dev server on :5173)
- Tailwind CSS 3 (styling)
- TanStack Query 5 (server state management)
- Auth: JWT cookie (`credentials: "include"` on all API calls, 401 redirects to login)

## Features

- **Project list** -- View all registered projects with status badges
- **Add project** -- Register via dialog (local path, git URL, or ZIP upload)
- **Trigger scan** -- Start `intodoc scan` for a project
- **Generate docs** -- Trigger doc generation via active provider (claude-cli or custom API)
- **Provider/model selection** -- Choose backend and model before generation
- **View logs** -- Real-time log streaming via SSE during scan/generation
- **Run history** -- Per-project scan and generation run history
- **Structured verdicts** -- Generation runs show `draft`, `failed`, `published`, `published_with_rag_error`, or `client_ready`
- **Quality history** -- Doc quality scores over time per project
- **AB comparison** -- Side-by-side diff of two generation runs
- **Document catalogue** -- Browse generated docs for a project
- **Validation review** -- Review coverage validation results
- **Plugin pack management** -- Install/manage plugin packs
- **Project settings** -- Update project config, GitHub token, webhook
- **Push to RAG** -- Manual push of docs to RAG pipeline
- **Reset stuck status** -- Reset projects stuck in generating/scanning

## Source Structure

```
dashboard/src/
  main.tsx                    -- React entry point
  App.tsx                     -- Main layout and routing
  types.ts                    -- TypeScript type definitions
  api/
    client.ts                 -- REST API client (fetch wrapper, credentials: include)
  hooks/
    useProjects.ts            -- TanStack Query hooks for project CRUD (30+ exports)
    useLogStream.ts           -- SSE log streaming hook
    useAuth.ts                -- Auth state + login/logout
    useDiscovery.ts           -- Platform/model discovery
  components/
    ProjectList.tsx           -- Project table with status and actions
    ProjectCard.tsx           -- Single project card view
    ProjectSettings.tsx       -- Project config editor
    ActionButtons.tsx         -- Scan, Generate, Push to RAG, Reset buttons
    StatusBadge.tsx           -- Color-coded status indicator
    LogPanel.tsx              -- Real-time log output panel
    AddRepoDialog.tsx         -- Dialog for registering new projects
    RunHistory.tsx            -- Generation run history table
    ScanHistory.tsx           -- Scan run history table
    QualityHistoryPanel.tsx   -- Quality score trend panel
    ABComparison.tsx          -- Side-by-side doc diff
    DocumentCatalogue.tsx     -- Browse generated docs
    DocPreview.tsx            -- Markdown doc preview
    ValidationReviewPanel.tsx -- Coverage validation results
    KpiBar.tsx                -- Summary KPI bar
    PluginPacksPage.tsx       -- Plugin pack management
```

## Run Verdict Display

`RunHistory.tsx` prefers the enterprise `run_verdict` badge when present. The old
PASS/FAIL auto-comparison badge is still shown for legacy runs without a
structured verdict. Treat `client_ready` as the only positive delivery signal.
`published_with_rag_error` requires RAG repair even if docs exist on disk.

## Running

```bash
cd knowledge_base/dashboard
npm install
npm run dev    # Dev server on http://localhost:5173
```

Requires the MCP REST API server running on port 8001:
```bash
cd knowledge_base/mcp-server
python -m mcp_server --serve
```
