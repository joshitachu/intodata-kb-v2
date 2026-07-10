---
name: tune-swap-pack
description: Tune an IntoData Swap Pack by editing plugin.yaml and prompt markdown files for a specific documentation audience, style, and document set.
---

# Tune Swap Pack

Use this skill when working inside a cloned or unzipped IntoData Swap Pack.

## Workflow

1. Read `AGENTS.md`, `README.md`, and `plugin.yaml`.
2. Ask what documentation outcome the admin wants:
   - audience
   - documents to add, remove, or keep
   - preferred depth and tone
   - examples of bad current output, if available
3. Inspect `prompts/phase2.md`, `prompts/phase3.md`, and matching
   `prompts/docs/*.md` files.
4. Edit the smallest necessary set of files:
   - `plugin.yaml` for document set, phase policy, version, and quality gates
   - `prompts/phase2.md` for evidence extraction behavior
   - `prompts/phase3.md` for global writing behavior
   - `prompts/docs/<DOC_NAME>.md` for one document's instructions
5. Validate by checking:
   - `plugin.yaml` has `name`, `version`, and at least one `steps` entry
   - each document name is stable and filesystem-safe
   - prompt files contain no credentials or customer secrets
   - examples are generic and not copied from private source
6. Summarize which files changed and what generated output should change.

## Rules

- Do not add executable server hooks.
- Do not put secrets in prompts or examples.
- Keep generated Markdown requirements ASCII-compatible unless told otherwise.
- Prefer explicit evidence instructions over style-only prompt changes.
