# Dashboard

The dashboard (`dashboard/`) is a React control panel for managing intodoc projects,
triggering scans, viewing logs, and pushing docs to the RAG pipeline.

## Technology

- React 19 + TypeScript 5
- Vite 6 (build tool, dev server on :5173)
- Tailwind CSS 3 (styling)
- TanStack Query 5 (server state management)

## Features

- **Project list** -- View all registered projects with status badges
- **Add project** -- Register new projects via dialog (local path, git URL, or ZIP upload)
- **Trigger scan** -- Start `intodoc scan` for a project
- **View logs** -- Real-time log streaming via SSE during scan/generation
- **Push to RAG** -- Send generated docs to the RAG pipeline
- **Generate docs** -- Spawn Claude Code CLI for documentation generation
- **Reset stuck status** -- Reset projects stuck in generating/scanning back to error state

## API Integration

The dashboard communicates with the MCP REST API server on port 8001.
API client is in `src/api/client.ts`.

Key hooks:
- `useProjects()` -- Fetch and manage project list (polls every 10s)
- `useResetStatus()` -- Reset stuck generating/scanning projects
- `useLogStream()` -- Subscribe to SSE log events

## Source Structure

```
dashboard/src/
  main.tsx                  -- React entry point
  App.tsx                   -- Main layout and routing
  types.ts                  -- TypeScript type definitions
  api/
    client.ts               -- REST API client (fetch wrapper)
  hooks/
    useProjects.ts          -- TanStack Query hook for project CRUD
    useLogStream.ts         -- SSE log streaming hook
  components/
    ProjectList.tsx         -- Project table with status and actions
    ActionButtons.tsx       -- Scan, Generate, Push to RAG, Reset buttons
    StatusBadge.tsx         -- Color-coded status indicator
    LogPanel.tsx            -- Real-time log output panel
    AddRepoDialog.tsx       -- Dialog for registering new projects
```

## Running

```bash
cd dashboard
npm install
npm run dev    # Dev server on http://localhost:5173
```

Requires the MCP REST API server running on port 8001:
```bash
cd mcp-server
python -m mcp_server --serve
```
