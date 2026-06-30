# Swap Pack Starter

This repository is a starting point for IntoData Swap Packs.

## Workflow

1. Clone this repository.
2. Create a branch from `main`.
3. Edit `plugin.yaml`, `prompts/`, and `examples/`.
4. Push the branch.
5. Register the branch from the IntoData dashboard Packs page.

Each branch is treated as its own available pack. Keep `main` as the clean base
foundation and put customer-specific or use-case-specific changes on branches.

## Files

- `plugin.yaml` defines the pack name, version, phase policy, and documents.
- `prompts/phase2.md` controls evidence extraction.
- `prompts/phase3.md` controls final document writing.
- `prompts/docs/*.md` adds document-specific instructions.
- `examples/*.md` gives style examples only.
- `AGENTS.md` and `.codex/skills/` tell AI coding agents how to edit the pack.

Do not commit credentials, raw customer secrets, or private source code into a
pack branch.
