FROM python:3.11-slim

# WeasyPrint system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libglib2.0-0 \
    fonts-dejavu-core \
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
    chown -R appuser:appgroup /app
USER appuser

EXPOSE 8001

CMD ["python", "-m", "mcp_server", "--serve", "--host", "0.0.0.0", "--port", "8001"]
