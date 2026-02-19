 Setup & Test Workflow                                                                                                                    
  1. Install dependencies                                                                                                                  
  # MCP server (Python)                                                                                                                   cd C:/Users/kabeljo/Documents/intodata-kb/vaxxinova/mcp-server                                                                        
  pip install -e .

  # Dashboard (Node)
  cd ../dashboard
  npm install


  2. Run the DB migration

  cd C:/Users/kabeljo/Documents/intodata-kb/RAG_pipeline/backend
  alembic upgrade head


  This adds the projects and scan_runs tables to your existing intodata_kb database.

  3. Start services (3 terminals)

  # Terminal 1: RAG pipeline (already exists, port 8000)
  cd C:/Users/kabeljo/Documents/intodata-kb/RAG_pipeline/backend
  python -m uvicorn src.api.main:app --port 8000

  # Terminal 2: MCP REST API (port 8001)
  cd C:/Users/kabeljo/Documents/intodata-kb/vaxxinova/mcp-server
  python -m mcp_server --serve

  # Terminal 3: Dashboard (port 5173)
  cd C:/Users/kabeljo/Documents/intodata-kb/vaxxinova/dashboard
  npm run dev


  4. Test the REST API (curl)

  # Health check
  curl http://localhost:8001/health

  # Register vaxxinova-talend
  curl -X POST http://localhost:8001/mcp/add-repo \
    -H "Content-Type: application/json" \
    -d '{"url": "C:/Users/kabeljo/Documents/intodata-kb/vaxxinova/vaxxinova-talend", "project_id": "vaxxinova", "platform": "talend"}'  

  # List projects
  curl http://localhost:8001/mcp/projects

  # Trigger scan
  curl -X POST http://localhost:8001/mcp/scan \
    -H "Content-Type: application/json" \
    -d '{"project_id": "vaxxinova", "force": true}'

  # Check status
  curl http://localhost:8001/mcp/status/vaxxinova


  5. Test the dashboard (browser)

  1. Open http://localhost:5173
  2. Click Add Repo → enter path C:/Users/kabeljo/Documents/intodata-kb/vaxxinova/vaxxinova-talend, project ID vaxxinova, platform      
  talend
  3. Click Re-scan → watch the log panel at the bottom for live output
  4. Click Push to RAG → sends generated .md files to RAG pipeline
  5. Click Generate Docs → spawns Claude Code CLI (requires claude in PATH)

  6. Test the RAG /ingest/md endpoint

  curl -X POST http://localhost:8000/api/v1/ingest/md \
    -H "Content-Type: application/json" \
    -d '{"text": "# Test Job\nThis job loads payment data.", "filename": "test_job.md", "source": "intodoc:vaxxinova", "project_id":    
  "vaxxinova"}'


  7. Test MCP tools (from Claude Code)

  Restart Claude Code in the vaxxinova/ directory so it picks up .mcp.json, then try:

  > Use the intodoc MCP tools to list projects
  > Use add_repo to register vaxxinova-talend
  > Use fetch_json to read the scan data for vaxxinova


  What to expect to break first

  ┌──────────────────────┬───────────────────────────┬─────────────────────────────────────────────────────────────┐
  │         Area         │       Likely issue        │                        How to check                         │
  ├──────────────────────┼───────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ pip install -e .     │ Missing mcp package       │ pip install mcp separately                                  │
  ├──────────────────────┼───────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ alembic upgrade head │ DB connection refused     │ Make sure PostgreSQL is running                             │
  ├──────────────────────┼───────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ /mcp/scan            │ intodoc command not found │ Ensure intodoc is installed: cd intodoc && pip install -e . │
  ├──────────────────────┼───────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ /mcp/generate        │ claude CLI not in PATH    │ Only works if Claude Code CLI is installed globally         │
  ├──────────────────────┼───────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Dashboard            │ API 500s                  │ Check Terminal 2 logs for Python tracebacks                 │
  ├──────────────────────┼───────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ /ingest/md           │ Ollama not running        │ Embedding requires Ollama with nomic-embed-text model       │
  └──────────────────────┴───────────────────────────┴─────────────────────────────────────────────────────────────┘

  Start with steps 1-4 (curl tests). Fix any errors there before moving to the dashboard or MCP tools.