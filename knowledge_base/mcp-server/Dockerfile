FROM python:3.11-slim

ARG OPENCODE_VERSION=1.18.7

# WeasyPrint plus runtime tools used by MCP scan, git, and repo-agent flows.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    nodejs \
    npm \
    git \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libglib2.0-0 \
    fonts-dejavu-core \
    gosu \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

# OpenCode is a production runtime dependency. Pin the npm package and verify
# the installed CLI version at image build time.
RUN npm install --global "opencode-ai@${OPENCODE_VERSION}" \
    && test "$(opencode --version)" = "${OPENCODE_VERSION}"

WORKDIR /app

# OpenCode/Bun resolve user state through HOME and the XDG directories. The
# service drops privileges to appuser, whose system-account home is
# /nonexistent, so give CLI runtimes an explicit writable home instead.
ENV HOME=/app/.home \
    XDG_CONFIG_HOME=/app/.home/.config \
    XDG_CACHE_HOME=/app/.home/.cache \
    XDG_DATA_HOME=/app/.home/.local/share

# Install intodoc CLI first (scanner dependency)
COPY intodoc/pyproject.toml /tmp/intodoc/pyproject.toml
COPY intodoc/src/ /tmp/intodoc/src/
COPY intodoc/CLAUDE.md /tmp/intodoc/CLAUDE.md
RUN pip install --no-cache-dir /tmp/intodoc && rm -rf /tmp/intodoc

# Install mcp-server
COPY mcp-server/pyproject.toml .
COPY mcp-server/src/ src/
COPY mcp-server/alembic/ alembic/
COPY mcp-server/alembic.ini .
COPY mcp-server/data/ data/
# The grounded OpenAI runtime imports the separate OpenAI Agents SDK as
# `agents`.  Keep it optional for library consumers, but include the declared
# extra in the production image whenever this API route is advertised.
RUN pip install --no-cache-dir ".[openai-agents]" && python -c "import agents"

# Run as non-root user
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup \
        --home /app/.home --no-create-home appuser && \
    mkdir -p /app/.home/.config /app/.home/.cache /app/.home/.local/share \
        /app/data /app/intodoc-output /app/workspace/output /app/workspace/packs && \
    chown -R appuser:appgroup /app
COPY mcp-server/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8002

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["sh", "-c", "alembic upgrade head && python -m mcp_server --serve --host 0.0.0.0 --port 8002"]
