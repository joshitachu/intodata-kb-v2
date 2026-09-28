FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir "mcp==1.30.0" \
    && useradd --system --uid 10001 --no-create-home demo-mcp

COPY server.py /app/server.py

USER demo-mcp
EXPOSE 8765
CMD ["python", "/app/server.py"]
