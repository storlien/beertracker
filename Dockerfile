# BeerTracker Backend Service
# Multi-stage build with uv for fast, reproducible dependency management

FROM python:3.12-slim-bookworm AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Copy dependency manifests
COPY pyproject.toml uv.lock /app/

# Install production dependencies into a virtual environment
RUN uv sync --no-dev --no-editable --frozen

# ──────────────────────────────────────────────────────────────

FROM python:3.12-slim-bookworm AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    BEERTRACKER_LOG_LEVEL=INFO

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --create-home appuser

WORKDIR /app

# Copy only the installed packages and the app code
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser beertracker /app/beertracker
COPY --chown=appuser:appuser healthcheck.py /app/healthcheck.py

# Make the venv Python available
ENV PATH="/app/.venv/bin:$PATH"

USER appuser

# Health check: verify last sync was within 10 minutes
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python /app/healthcheck.py || exit 1

CMD ["python", "-m", "beertracker"]
