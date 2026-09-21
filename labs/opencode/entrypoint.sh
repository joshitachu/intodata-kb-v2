#!/bin/sh
# Start the OpenCode headless server with the Moonshot provider registered and
# authenticated, without ever writing the API key to a config file.
#
# Three things here are load-bearing and were each established by testing against
# OpenCode 1.18.7 rather than from docs:
#
#  1. The server only reads provider definitions from the GLOBAL config. A
#     `provider` block in a per-workspace opencode.json is ignored, so the model
#     silently falls back to a default gateway model.
#  2. `{env:VAR}` interpolation inside opencode.json works for `opencode run` but
#     NOT in serve mode -- the placeholder reaches the provider verbatim and the
#     request fails with 401. So the key goes in via the auth API instead, which
#     also keeps it off the workspace volume the backend writes to.
#  3. The key is passed in as MOONSHOT_API_KEY, deliberately NOT as KIMI_API_KEY.
#     OpenCode auto-detects KIMI_API_KEY and binds it to its built-in
#     `kimi-for-coding` provider, which points at a different API base
#     (api.kimi.com/coding/v1) than the Moonshot platform key is valid for.

set -eu

PORT="${OPENCODE_PORT:-4096}"
CONFIG_DIR="${XDG_CONFIG_HOME:-/app/.home/.config}/opencode"
SKILL_SOURCE="${NEXUS_SKILL_SOURCE:-/opt/nexus/skills}"
WORKSPACE_ROOT="${LABS_WORKSPACE_ROOT:-/labs-workspaces}"
# The backend container writes each conversation workspace here and this
# container reads it. Docker creates a named volume root-owned 0755, and the
# backend runs as a non-root user, so without this it cannot write at all.
# This container is root, so it stays writable either way.
WORKSPACE_UID="${LABS_WORKSPACE_UID:-1001}"
WORKSPACE_GID="${LABS_WORKSPACE_GID:-1001}"

mkdir -p "$WORKSPACE_ROOT"
chown "${WORKSPACE_UID}:${WORKSPACE_GID}" "$WORKSPACE_ROOT"
chmod 0775 "$WORKSPACE_ROOT"

mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_DIR/opencode.json" <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "moonshot": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Moonshot",
      "options": { "baseURL": "https://api.moonshot.ai/v1" },
      "models": { "kimi-k2.6": { "name": "Kimi K2.6" } }
    },
    "deepseek": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "DeepSeek",
      "options": { "baseURL": "https://api.deepseek.com/v1" },
      "models": {
        "deepseek-flash": { "name": "DeepSeek Flash" },
        "deepseek-v4-pro": { "name": "DeepSeek V4 Pro" }
      }
    }
  }
}
JSON

# Install the lane's skills into the global config dir, which is the only place
# `opencode serve` discovers them. Replaced wholesale rather than merged: the
# image is the source of truth, /app/.home is a persistent volume, and a skill
# that was renamed or deleted in the image has to disappear here too. A stale
# procedure the model can still select is worse than no procedure at all.
if [ -d "$SKILL_SOURCE" ]; then
    rm -rf "$CONFIG_DIR/skill"
    mkdir -p "$CONFIG_DIR/skill"
    cp -R "$SKILL_SOURCE/." "$CONFIG_DIR/skill/"
    echo "opencode-labs: installed skills: $(ls "$CONFIG_DIR/skill" | tr '\n' ' ')"
else
    echo "opencode-labs: no skill source at $SKILL_SOURCE; the lane has no procedures" >&2
fi

opencode serve --port "$PORT" --hostname 0.0.0.0 &
SERVER_PID=$!

# Provider registration is async at startup; poll rather than sleeping blind.
i=0
while [ "$i" -lt 60 ]; do
    if curl -sf -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        break
    fi
    i=$((i + 1))
    sleep 1
done

if [ "$i" -ge 60 ]; then
    echo "opencode-labs: server did not become healthy within 60s" >&2
    kill "$SERVER_PID" 2>/dev/null || true
    exit 1
fi

# DeepSeek. OpenAI-compatible; `deepseek-flash` and `deepseek-v4-pro` are what
# the account's /models endpoint actually returns.
if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    ds_status=$(printf '{"type":"api","key":"%s"}' "$DEEPSEEK_API_KEY" \
        | curl -s -o /dev/null -w '%{http_code}' \
            -X PUT "http://127.0.0.1:${PORT}/auth/deepseek" \
            -H 'Content-Type: application/json' \
            --data-binary @- || echo 000)
    if [ "$ds_status" = "200" ]; then
        echo "opencode-labs: deepseek provider authenticated"
    else
        echo "opencode-labs: deepseek auth failed (HTTP $ds_status)" >&2
    fi
fi

# OpenCode Zen. Its `*-free` models list without credentials but fail with
# UnknownError until the provider is authenticated; the key is minted by a human
# at https://opencode.ai/auth. Installed the same way as Moonshot so it never
# reaches a config file, and so it can be supplied non-interactively -- the
# `opencode auth login` flow needs a TTY.
if [ -n "${OPENCODE_API_KEY:-}" ]; then
    zen_status=$(printf '{"type":"api","key":"%s"}' "$OPENCODE_API_KEY" \
        | curl -s -o /dev/null -w '%{http_code}' \
            -X PUT "http://127.0.0.1:${PORT}/auth/opencode" \
            -H 'Content-Type: application/json' \
            --data-binary @- || echo 000)
    if [ "$zen_status" = "200" ]; then
        echo "opencode-labs: opencode zen provider authenticated"
    else
        echo "opencode-labs: opencode zen auth failed (HTTP $zen_status)" >&2
    fi
fi

if [ -n "${MOONSHOT_API_KEY:-}" ]; then
    # --data-binary @- keeps the key out of the process argument list, where it
    # would otherwise be visible to anything that can read /proc.
    status=$(printf '{"type":"api","key":"%s"}' "$MOONSHOT_API_KEY" \
        | curl -s -o /dev/null -w '%{http_code}' \
            -X PUT "http://127.0.0.1:${PORT}/auth/moonshot" \
            -H 'Content-Type: application/json' \
            --data-binary @- || echo 000)
    if [ "$status" = "200" ]; then
        echo "opencode-labs: moonshot provider authenticated"
    else
        echo "opencode-labs: moonshot auth failed (HTTP $status)" >&2
    fi
else
    echo "opencode-labs: MOONSHOT_API_KEY is empty; the lane cannot reach a model" >&2
fi

wait "$SERVER_PID"
