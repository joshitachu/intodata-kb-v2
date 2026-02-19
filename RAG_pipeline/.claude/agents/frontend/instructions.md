# Frontend Agent

## Responsibilities
- Next.js App Router application
- Route handlers (BFF proxy to FastAPI)
- UI components (Tailwind + shadcn/ui)
- React Query for state management
- Zod validation for API responses

## Files Owned
- `frontend/` — Entire frontend application

## Architecture
```
Browser → Next.js pages (React) → Next.js route.ts → FastAPI backend
```
- Client components call `/api/*` (Next.js route handlers)
- Route handlers in `app/api/*/route.ts` proxy to FastAPI
- `lib/fastapi.ts` — shared server-side fetch helper
- `FASTAPI_URL` env var is server-only (no NEXT_PUBLIC_ prefix)

## Pages
- `/` — Search page (home): SearchBar + ResponseDisplay + SourceCitation
- `/documents` — Document management: upload, list, delete
- `/settings` — User settings

## Components
- SearchBar: Natural language query input
- ResponseDisplay: Streamed answer with inline citations
- SourceCitation: Expandable source reference
- QueryHistory: Session history in sidebar
- DocumentUpload: Drag-and-drop file upload

## Acceptance Criteria
- Type query → see streamed answer with source citations
- Upload document → see ingestion progress
- All API calls go through Next.js route handlers
- Mobile-responsive
- Loading/error states for every API call
