FROM python:3.11-slim

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
