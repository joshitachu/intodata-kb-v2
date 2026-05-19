#!/bin/sh
set -eu

for dir in /app/data /app/intodoc-output /app/workspace; do
  mkdir -p "$dir"
  chown -R appuser:appgroup "$dir"
done

exec gosu appuser:appgroup "$@"
