# Integration Assessor Plugin Pack

Default plugin pack for intodoc.  Delegates to the built-in
`CustomHarnessProvider` which runs the full four-phase pipeline:

1. **Investigation** — evidence extraction from scan data
2. **Analysis** — per-group analysis guide generation
3. **Scorecard** — quality scoring
4. **Doc writing** — final documentation output

## Usage

Register this pack and assign it to a project:

```bash
# Register (local path)
curl -X POST http://localhost:8001/mcp/plugins \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/plugin_packs/integration_assessor"}'

# Assign to project
curl -X PATCH http://localhost:8001/mcp/projects/<project_id>/plugin \
  -H "Content-Type: application/json" \
  -d '{"plugin_pack_id": <id>}'
```

## Customisation

To override prompts for a specific project, copy this directory,
add `.md` prompt files in `prompts/`, change `steps` in `plugin.yaml`
to use `type: llm` with your custom `prompt_file` paths, and register
the new pack.
