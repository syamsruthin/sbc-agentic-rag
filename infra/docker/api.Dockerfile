# syntax=docker/dockerfile:1.7
# The API image (docs/aws/MIGRATION_PLAN.md WP8): FastAPI behind the Lambda Web Adapter, with the facts bundle
# baked in, so code, config and data ship and roll back as one image.
#
#   docker buildx build --platform linux/arm64 -f infra/docker/api.Dockerfile \
#     --build-context bundle=<dir containing facts.sqlite> -t sbc-api .
#
# The same image runs locally (plain uvicorn on port 8080) and on Lambda (the adapter, an extension under
# /opt/extensions, turns Lambda invocations into HTTP requests and streams the responses).
# Behind a TLS-inspecting proxy add: --network=host --build-arg HTTPS_PROXY --secret id=ca,src=<ca-bundle.pem>

ARG PYTHON_IMAGE=python:3.13-slim

FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
ARG HTTPS_PROXY
# Runtime packages only (sbc_api and what it imports); the build-time packages (ingest, extract, index) stay out.
RUN --mount=type=secret,id=ca,target=/etc/ssl/certs/build-ca.pem \
    --mount=type=cache,target=/root/.cache/uv \
    if [ -s /etc/ssl/certs/build-ca.pem ]; then export SSL_CERT_FILE=/etc/ssl/certs/build-ca.pem; fi \
 && uv sync --frozen --no-dev --package sbc-api --no-editable

FROM ${PYTHON_IMAGE}
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY config ./config
ARG BUNDLE_FILE=facts.sqlite
COPY --from=bundle ${BUNDLE_FILE} /opt/bundle/facts.sqlite
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    FACTS_BUNDLE_PATH=/opt/bundle/facts.sqlite \
    AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_READINESS_CHECK_PATH=/api/healthz
USER app
EXPOSE 8080
CMD ["uvicorn", "--factory", "sbc_api.app:create_app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
