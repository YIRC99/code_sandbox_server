# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    /bin/uv sync --frozen --no-install-project --no-dev

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install --no-install-recommends --yes tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp sandbox \
    && mkdir -p /data/uploads \
    && chown 10001:10001 /data/uploads

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src/

ENV PATH="/app/.venv/bin:$PATH" \
    HOME="/tmp" \
    PYTHONUNBUFFERED="1" \
    PYTHONDONTWRITEBYTECODE="1"

USER 10001:10001
EXPOSE 32004

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "src.sandbox.main:app", "--host", "0.0.0.0", "--port", "32004", "--no-access-log"]

