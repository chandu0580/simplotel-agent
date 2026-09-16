# syntax=docker/dockerfile:1
# Backend image: multi-stage, pinned base, non-root, no secrets (configuration comes from the environment at runtime).

FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r /tmp/requirements.txt

FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    LOG_FORMAT=json
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=root:root app ./app
COPY --chown=root:root migrations ./migrations
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
# SIGTERM: uvicorn stops accepting, drains in-flight requests (up to 25 s), then runs the lifespan shutdown.
STOPSIGNAL SIGTERM
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--timeout-graceful-shutdown", "25"]
