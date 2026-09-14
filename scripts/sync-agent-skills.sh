#!/usr/bin/env bash
# Mirror .claude/skills/ -> .codex/skills/
#
# .claude/skills/ is the single source of truth. Codex reads .codex/skills/, so the
# two must stay identical. They previously drifted into three hand-maintained copies
# (.claude/commands, .codex/skills, .opencode/skill) that disagreed on every file.
#
#   sync-agent-skills.sh          regenerate the mirror
#   sync-agent-skills.sh --check  exit 1 if the mirror is stale (for CI / pre-commit)
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
SRC="$REPO/.claude/skills"
DST="$REPO/.codex/skills"

[ -d "$SRC" ] || { echo "error: $SRC does not exist"; exit 1; }

if [ "${1:-}" = "--check" ]; then
  if diff -r "$SRC" "$DST" >/dev/null 2>&1; then
    echo "OK: .codex/skills matches .claude/skills"
    exit 0
  fi
  echo "STALE: .codex/skills does not match .claude/skills"
  diff -r "$SRC" "$DST" || true
  echo
  echo "Run scripts/sync-agent-skills.sh to regenerate."
  exit 1
fi

mkdir -p "$DST"
# Remove mirror entries that no longer exist in the source, then copy.
for d in "$DST"/*/; do
  [ -d "$d" ] || continue
  name=$(basename "$d")
  [ -d "$SRC/$name" ] || { echo "removing stale mirror: $name"; rm -rf "$d"; }
done
cp -R "$SRC"/. "$DST"/

echo "Synced $(find "$SRC" -name SKILL.md | wc -l | tr -d ' ') skills to .codex/skills"
