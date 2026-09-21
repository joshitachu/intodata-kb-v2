# OpenCode headless server for the labs chat lane.
#
# This is NOT part of the EP-001 seven-service health boundary. It runs only
# under the `labs` compose profile, so a normal `docker-compose up -d` never
# starts it and client deployments are unaffected.
#
# The version is pinned and verified at build time, matching the pin already
# used by knowledge_base/mcp-server.
FROM node:22-slim

ARG OPENCODE_VERSION=1.18.7

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN npm install --global "opencode-ai@${OPENCODE_VERSION}" \
    && test "$(opencode --version)" = "${OPENCODE_VERSION}"

# OpenCode/Bun resolve user state through HOME and the XDG directories. Give the
# CLI runtime an explicit writable home rather than inheriting an unwritable one.
ENV HOME=/app/.home \
    XDG_CONFIG_HOME=/app/.home/.config \
    XDG_CACHE_HOME=/app/.home/.cache \
    XDG_DATA_HOME=/app/.home/.local/share

RUN mkdir -p /app/.home /labs-workspaces

# Skills. Staged here and installed into the global config dir by the
# entrypoint -- deliberately NOT copied straight to
# /app/.home/.config/opencode/skill/, which is where OpenCode reads them from.
#
# Two constraints meet at that path:
#   1. `opencode serve` only discovers skills under the GLOBAL config dir.
#      A per-workspace `.opencode/skill(s)/` is never scanned and the
#      `directory` query parameter does not change skill resolution; both were
#      probed against 1.18.7 with GET /skill.
#   2. /app/.home is a named volume, and Docker seeds a named volume from the
#      image only while the volume is EMPTY. The volume already holds the auth
#      store, so an image layer written under it would be shadowed on every
#      existing deployment and the skills would silently never appear.
# Staging outside the volume and copying in at start makes the image the source
# of truth on every rebuild.
#
# They belong in the image rather than in the per-conversation workspace the
# backend writes: they are static procedure, identical for every conversation.
COPY skills/ /opt/nexus/skills/

COPY entrypoint.sh /usr/local/bin/opencode-labs-entrypoint
RUN chmod +x /usr/local/bin/opencode-labs-entrypoint

WORKDIR /labs-workspaces

EXPOSE 4096

# The entrypoint writes the global provider config and authenticates Moonshot
# through the auth API before handing off to the server. See the comments there:
# neither step can be done from a per-workspace opencode.json on this version.
CMD ["/usr/local/bin/opencode-labs-entrypoint"]
