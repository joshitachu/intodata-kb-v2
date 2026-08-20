#!/bin/sh
set -eu

for dir in /app/.home /app/data /app/intodoc-output /app/workspace /app/agentic-workspaces; do
  mkdir -p "$dir"
  chown -R appuser:appgroup "$dir" 2>/dev/null || true
done

exec gosu appuser:appgroup env \
  HOME=/app/.home \
  XDG_CONFIG_HOME=/app/.home/.config \
  XDG_CACHE_HOME=/app/.home/.cache \
  XDG_DATA_HOME=/app/.home/.local/share \
  "$@"
