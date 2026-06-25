# Swap Pack Starter

This repository is a starter kit for an IntoData Swap Pack.

A Swap Pack controls how project documentation is generated. The platform uses
the runtime pack files during generation, while the authoring files help a human
and an AI coding assistant customize the pack safely.

This starter is intended to be published as a public GitHub template repository.
Admins can clone it, create a branch, tune the prompts with Codex or Claude, and
then either push that branch or upload a ZIP through the IntoData dashboard.

## Runtime Files

- `plugin.yaml` defines the pack name, version, generation phases, document set,
  quality gates, and runner steps.
- `prompts/investigation.md` guides evidence investigation.
- `prompts/phase2.md` guides group analysis.
- `prompts/scorecard.md` guides quality and risk scoring.
- `prompts/phase3.md` guides document writing.
- `prompts/docs/*.md` gives per-document instructions.
- `examples/*.md` gives style and structure examples.

## Authoring Files

- `AGENTS.md` tells AI coding agents how to work in this pack.
- `.codex/skills/tune-swap-pack/SKILL.md` gives Codex a local tuning workflow.
- `.claude/commands/tune-pack.md` gives Claude Code a similar workflow.

The platform treats these authoring files as text. It does not execute local
skills, commands, or scripts from an uploaded pack.

## Customize

1. Edit `plugin.yaml` when the document set, phase behavior, or quality gates
   should change.
2. Edit `prompts/phase2.md` when analysis is missing the facts you want.
3. Edit `prompts/phase3.md` when the writing style or evidence rules should
   change across all docs.
4. Edit `prompts/docs/*.md` when one document needs specific sections or focus.
5. Update `version` in `plugin.yaml` before uploading a new release.

## Upload

Zip this directory with `plugin.yaml` at the zip root, then upload it in the
dashboard under Swap Packs. You can also push the pack to GitHub and register
the repository URL from the dashboard.
