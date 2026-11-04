# Spikes 2 and 3: the API image shape from MIGRATION_PLAN.md WP8, with the spike app instead of sbc_api.
# Build from the repository root (see spikes/aws/README.md):
#   docker build --network=host -f spikes/aws/sse_app/Dockerfile --build-context bundle=<dir> -t sbc-spike .
FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.13-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
ARG HTTPS_PROXY
# The optional "ca" secret is only for build machines behind a TLS-inspecting proxy.
RUN --mount=type=secret,id=ca,target=/etc/ssl/certs/build-ca.pem \
    if [ -s /etc/ssl/certs/build-ca.pem ]; then export SSL_CERT_FILE=/etc/ssl/certs/build-ca.pem; fi \
 && uv sync --frozen --no-dev --package sbc-api --no-editable \
 && uv pip install --python /app/.venv/bin/python \
      fastapi==0.162.0 uvicorn==0.54.0 langgraph-checkpoint-aws==1.2.6

FROM python:3.13-slim
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
RUN useradd -u 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY config ./config
COPY spikes/aws/sse_app/app.py ./spike_app.py
COPY --from=bundle / /opt/bundle/
ENV PATH=/app/.venv/bin:$PATH PORT=8080 PYTHONDONTWRITEBYTECODE=1 \
    AWS_LWA_INVOKE_MODE=response_stream AWS_LWA_READINESS_CHECK_PATH=/api/healthz \
    FACTS_BUNDLE_PATH=/opt/bundle/facts.sqlite SBC_CONFIG_DIR=/app/config
USER app
CMD ["uvicorn", "spike_app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
