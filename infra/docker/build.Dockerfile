# syntax=docker/dockerfile:1.7
# The offline SBC build (docs/aws/MIGRATION_PLAN.md WP8): catalog -> download -> convert -> verify -> facts
# bundle -> embeddings -> retrieval eval, synced with the S3 data bucket. Runs as an on-demand ECS Fargate Spot
# task (WP9), or locally with AWS credentials. See infra/docker/build-pipeline.sh for the modes.
#
#   docker buildx build --platform linux/arm64 -f infra/docker/build.Dockerfile -t sbc-build .

ARG PYTHON_IMAGE=python:3.13-slim

FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM ${PYTHON_IMAGE}
COPY --from=uv /uv /bin/uv
# poppler-utils: pdftotext for the independent verifier (sbc-verify).
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
ARG HTTPS_PROXY
RUN --mount=type=secret,id=ca,target=/etc/ssl/certs/build-ca.pem \
    --mount=type=cache,target=/root/.cache/uv \
    if [ -s /etc/ssl/certs/build-ca.pem ]; then export SSL_CERT_FILE=/etc/ssl/certs/build-ca.pem; fi \
 && uv sync --frozen --no-dev --no-editable
COPY config ./config
COPY evals ./evals
COPY infra/docker/build-pipeline.sh /usr/local/bin/build-pipeline
COPY infra/docker/s3_sync.py /app/s3_sync.py
RUN chmod 0755 /usr/local/bin/build-pipeline && useradd --uid 10001 --create-home builder \
 && mkdir -p /work && chown builder /work
# The pipeline reads and writes data/ and bundles/ under the project root (sbc_ingest.paths): /work, which is
# the task's ephemeral storage. Code, config and the golden sets stay in /app.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    SBC_PROJECT_ROOT=/work
USER builder
WORKDIR /work
ENTRYPOINT ["build-pipeline"]
CMD ["full"]
