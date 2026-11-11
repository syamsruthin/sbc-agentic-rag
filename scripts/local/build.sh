#!/usr/bin/env bash
# Run the offline SBC build image (infra/docker/build.Dockerfile, build-pipeline.sh) against the local bucket, as
# scripts/aws/run-build.sh runs it as an ECS task against the real one:
#
#   scripts/local/build.sh seed                    # copy data/ (Markdown, catalog, reference) and the embedding
#                                                  # cache into s3://sbc-local-data, as on AWS (no PDFs)
#   scripts/local/build.sh run 2027.2-local        # build the image, then `build-pipeline bundle` in it
#   scripts/local/build.sh fetch 2027.2-local      # copy the published bundle to bundles/ for the API
#
# EVAL_SETUP defaults to bm25_only here (free); EVAL_SETUP=full reranks about 430 searches (about $0.25).
# The `full` mode (catalog refresh and PDF downloads from the portals) isn't offered locally: it needs the 6 GB of
# PDFs in the bucket and hours of downloads.
source "$(dirname "$0")/_common.sh"
need docker "Docker Desktop"
load_env
cd "$REPO_ROOT"
bucket="s3://${S3_DATA_BUCKET}"
sync() { uv run --quiet python infra/docker/s3_sync.py "$@"; }

case "${1:-}" in
  seed)
    say "seeding ${bucket}/data (Markdown, catalog, reference data; no PDFs)"
    for dir in markdown catalog reference; do
      [ -d "data/$dir" ] || die "data/$dir is missing (data/README.md)"
      sync up "data/$dir" "${bucket}/data/$dir"
    done
    [ -f bundles/embeddings.sqlite ] || die "bundles/embeddings.sqlite is missing"
    mkdir -p "$RUNTIME_DIR/cache-up" && cp bundles/embeddings.sqlite "$RUNTIME_DIR/cache-up/"
    sync up "$RUNTIME_DIR/cache-up" "${bucket}/bundles/cache" && rm -rf "$RUNTIME_DIR/cache-up"
    ;;
  run)
    version="${2:?usage: build.sh run <bundle-version>}"
    say "building the build image (native arch)"
    docker build -q -f infra/docker/build.Dockerfile -t sbc-build:local . >/dev/null
    say "build-pipeline bundle ${version} against ${bucket} (eval: ${EVAL_SETUP:-bm25_only})"
    # The OpenRouter key comes from the local Parameter Store, as the ECS task's secret comes from SSM; passed by
    # name (-e NAME), so it never appears on a command line. Embeddings come from the cache when nothing changed.
    OPENROUTER_API_KEY_BROKER=$(uv run --quiet python -c "
import boto3, os
print(boto3.client('ssm', region_name=os.environ['AWS_REGION']).get_parameter(Name=os.environ['SSM_PREFIX'] + 'OPENROUTER_API_KEY_BROKER', WithDecryption=True)['Parameter']['Value'])")
    export OPENROUTER_API_KEY_BROKER
    docker run --rm --network sbc-local_default \
      -e AWS_REGION -e AWS_DEFAULT_REGION="$AWS_REGION" -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_ENDPOINT_URL_S3=http://s3:9090 \
      -e S3_DATA_BUCKET -e BUNDLE_VERSION="$version" -e EVAL_SETUP="${EVAL_SETUP:-bm25_only}" \
      -e OPENROUTER_API_KEY_BROKER sbc-build:local bundle
    ;;
  fetch)
    version="${2:?usage: build.sh fetch <bundle-version>}"
    mkdir -p "$RUNTIME_DIR/bundle-$version"
    sync down "${bucket}/bundles/${version}" "$RUNTIME_DIR/bundle-$version"
    cp "$RUNTIME_DIR/bundle-$version/facts-${version}.sqlite" bundles/
    say "bundles/facts-${version}.sqlite: run the API on it with FACTS_BUNDLE_PATH=bundles/facts-${version}.sqlite"
    ;;
  *) die "usage: build.sh seed | run <bundle-version> | fetch <bundle-version>" ;;
esac
