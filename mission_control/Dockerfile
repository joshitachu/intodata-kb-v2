FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup appuser && \
    mkdir -p /app/snapshots && \
    chown -R appuser:appgroup /app
USER appuser

EXPOSE 9100

CMD ["uvicorn", "mission_control.main:app", "--host", "0.0.0.0", "--port", "9100"]
