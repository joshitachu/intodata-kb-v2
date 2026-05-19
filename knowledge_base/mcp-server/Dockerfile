FROM python:3.11-slim

# WeasyPrint plus runtime tools used by MCP scan, git, and repo-agent flows.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
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

WORKDIR /app

# Install intodoc CLI first (scanner dependency)
COPY intodoc/pyproject.toml /tmp/intodoc/pyproject.toml
COPY intodoc/src/ /tmp/intodoc/src/
COPY intodoc/CLAUDE.md /tmp/intodoc/CLAUDE.md
RUN pip install --no-cache-dir /tmp/intodoc && rm -rf /tmp/intodoc

# Install mcp-server
COPY mcp-server/pyproject.toml .
COPY mcp-server/src/ src/
RUN pip install --no-cache-dir .

# Run as non-root user
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup appuser && \
    mkdir -p /app/data /app/intodoc-output /app/workspace/output /app/workspace/packs && \
    chown -R appuser:appgroup /app
COPY mcp-server/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8002

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "mcp_server", "--serve", "--host", "0.0.0.0", "--port", "8002"]
