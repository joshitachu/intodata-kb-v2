#!/bin/sh
set -eu

for dir in /app/data /app/intodoc-output /app/workspace /app/agentic-workspaces; do
  mkdir -p "$dir"
  chown -R appuser:appgroup "$dir" 2>/dev/null || true
done

exec gosu appuser:appgroup "$@"
