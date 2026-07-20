FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
RUN python -m venv /opt/venv
COPY pyproject.toml README.md ./
COPY task_digest ./task_digest
RUN /opt/venv/bin/pip install --no-compile .

FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HEARTBEAT_PATH=/tmp/task-digest/heartbeat

RUN addgroup --system --gid 10001 taskdigest \
    && adduser --system --uid 10001 --ingroup taskdigest --no-create-home taskdigest \
    && mkdir -p /tmp/task-digest /data \
    && chown taskdigest:taskdigest /tmp/task-digest /data

COPY --from=builder /opt/venv /opt/venv

USER 10001:10001
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "task_digest", "healthcheck"]

CMD ["python", "-m", "task_digest", "serve"]
