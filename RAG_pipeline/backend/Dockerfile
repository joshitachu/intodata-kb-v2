FROM python:3.11-slim

# NOTE on PYTHONHASHSEED: deliberately NOT set.
#
# The sparse hybrid-search index used to depend on the builtin `hash()`, which
# Python salts per process, so the index written at ingestion and the vector
# built at query time disagreed after every restart. Pinning PYTHONHASHSEED here
# would have papered over that while disabling hash randomisation process-wide --
# a real, if small, DoS surface for anything that builds dicts from untrusted
# input. The mapping was made content-addressed instead
# (src/retrieval/sparse_embedder.py), so nothing in this image depends on the
# seed and randomisation stays on.

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
RUN pip install --no-cache-dir ".[hindsight]"

# Run as non-root user
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup appuser
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn src.api.main:app --host 0.0.0.0 --port 8000"]
