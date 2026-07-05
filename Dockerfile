# Ged Invest MCP - remote (HTTP) server image for ChatGPT custom connectors.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

# The MCP endpoint is served at http://<host>:$PORT/mcp
EXPOSE 8000

# $PORT is honoured at runtime (Render/Railway/Fly/Cloud Run inject it).
CMD ["ged-invest-mcp", "--http"]
