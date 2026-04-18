# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the CREAVY Ads MCP server.
#
# Builder: installs the project + deps into /opt/venv via `uv`.
# Runtime: copies the venv + source, runs as non-root `mcp` user,
#          exposes SSE on 8765, healthchecks the SSE endpoint.

# ---------- builder ----------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy lockfile + manifest first to leverage layer cache.
# uv.lock* with glob — tolerates missing lockfile.
COPY pyproject.toml uv.lock* README.md ./

# Copy the source so `uv pip install .` can find the package.
COPY creavy_ads ./creavy_ads
COPY google_ads_server.py ./google_ads_server.py

# Create venv and install the project (and its deps) into it.
RUN uv venv /opt/venv --python /usr/local/bin/python3 \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache --python /opt/venv/bin/python .

# ---------- runtime ----------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/vlazaay/mcp-google-ads" \
      org.opencontainers.image.description="CREAVY Google Ads MCP server (SSE transport)" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.licenses="MIT"

# curl is needed for the HEALTHCHECK; keep the image lean otherwise.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -r -u 1000 -m mcp \
 && mkdir -p /data \
 && chown mcp:mcp /data

# Bring the prebuilt venv from the builder stage.
COPY --from=builder --chown=mcp:mcp /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY --chown=mcp:mcp creavy_ads ./creavy_ads
COPY --chown=mcp:mcp google_ads_server.py ./google_ads_server.py
COPY --chown=mcp:mcp pyproject.toml ./pyproject.toml

USER mcp

# Default deployment config: SSE bound to all interfaces *inside* the
# container. The host-side port mapping in docker-compose pins this to
# 127.0.0.1, so it stays invisible to the public internet.
ENV MCP_TRANSPORT=sse \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8765 \
    GOOGLE_ADS_CREDENTIALS_PATH=/data/google_ads_token.json

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS -m 2 -H "Accept: text/event-stream" \
        "http://127.0.0.1:${MCP_PORT}/sse" >/dev/null || exit 1

ENTRYPOINT ["python", "-m", "creavy_ads.server"]
