FROM ghcr.io/astral-sh/uv:0.12.10 AS uvbin

FROM python:3.12-slim-bookworm AS builder
COPY --from=uvbin /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=0 UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY apps/api/pyproject.toml apps/api/uv.lock ./apps/api/
RUN uv sync --project apps/api --locked --no-dev --no-install-project
COPY apps/api/src ./apps/api/src
RUN uv sync --project apps/api --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/app/apps/api/.venv/bin:$PATH"
RUN groupadd --gid 10001 cce \
    && useradd --uid 10001 --gid 10001 --no-create-home cce
WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/apps/api/.venv ./apps/api/.venv
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 infrastructure/migrations ./infrastructure/migrations
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "cce.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
