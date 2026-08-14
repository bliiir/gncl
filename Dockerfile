# Chat runs on a hosted API, so the box needs no GPU and no large RAM.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer first, so source edits do not invalidate the install.
COPY pyproject.toml uv.lock README.md ./
COPY gncl/__init__.py gncl/__init__.py
# --extra llm as well as api: without it the `ollama` package is absent from the
# image, `available()` still passes its urllib probe, and every audit degrades to
# "not run" on an ImportError. The container could not satisfy case requirement 3
# no matter what the network allowed.
RUN uv sync --frozen --extra api --extra llm --no-dev

COPY gncl/ gncl/
COPY case/ case/

# Non-root. The container reads synthetic CSVs and writes nothing.
RUN useradd --create-home --uid 10001 gncl && chown -R gncl:gncl /app
USER gncl

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=2).status==200 else 1)"

# Credentials are enforced by `gncl serve` and by compose, via the `:?` operator
# on each variable. This image does not enforce them: `docker run` on it
# directly starts without credentials and answers 503 to authenticated requests.
#
# The venv binary rather than `uv run`: uv wants a writable $HOME/.cache/uv,
# which `read_only: true` in compose denies, and the container then crash-looped
# behind `restart: unless-stopped`. Nothing here needs uv at runtime.
CMD ["/app/.venv/bin/uvicorn", "gncl.api:app", "--host", "0.0.0.0", "--port", "8000"]
