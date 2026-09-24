#!/bin/sh
set -eu

# /app/.home/.claude is a named volume in compose so that `claude login` survives
# an image rebuild. Docker creates a fresh mountpoint root-owned, and this loop
# runs before the gosu drop, so it is the only place that can hand it to appuser.
for dir in /app/.home /app/.home/.claude /app/data /app/intodoc-output /app/workspace /app/agentic-workspaces; do
  mkdir -p "$dir"
  chown -R appuser:appgroup "$dir" 2>/dev/null || true
done

# `env` here REPLACES the inherited value for each name it sets, so anything the
# image ENV declares that matters to a spawned CLI must be repeated -- a name
# only in the Dockerfile ENV survives, but one set here must be correct here.
# CLAUDE_CONFIG_DIR keeps Claude Code's .claude.json inside the persistent
# claude_home volume; see the Dockerfile note.
exec gosu appuser:appgroup env \
  HOME=/app/.home \
  XDG_CONFIG_HOME=/app/.home/.config \
  XDG_CACHE_HOME=/app/.home/.cache \
  XDG_DATA_HOME=/app/.home/.local/share \
  CLAUDE_CONFIG_DIR=/app/.home/.claude \
  "$@"
