# Tune Swap Pack

Use this command inside a cloned IntoData Swap Pack.

1. Read `AGENTS.md`, `README.md`, and `plugin.yaml`.
2. Ask the admin what audience, document set, and style they want.
3. Edit `plugin.yaml` only when the document set, phase policy, version, or
   quality gates need to change.
4. Edit `prompts/phase2.md` when the generated docs are missing facts.
5. Edit `prompts/phase3.md` when all generated docs need a writing or evidence
   behavior change.
6. Edit `prompts/docs/<DOC_NAME>.md` for document-specific sections and focus.
7. Check that no secrets or executable hooks were added.
8. Summarize changed files and the expected documentation behavior.
