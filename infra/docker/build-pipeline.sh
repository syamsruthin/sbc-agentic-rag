#!/usr/bin/env bash
# The offline SBC build inside the build image (docs/aws/MIGRATION_PLAN.md WP8), in /work (SBC_PROJECT_ROOT).
#
#   build-pipeline full     # refresh the catalog, download new PDFs, convert the new ones, verify all, then bundle
#   build-pipeline bundle   # only rebuild the bundle, embeddings and eval from the Markdown already in S3
#
# Environment:
#   S3_DATA_BUCKET            the data bucket (required); data/ and bundles/ mirror the repository layout there
#   BUNDLE_VERSION            e.g. 2027.3 (required)
#   SBC_YEAR                  plan year (default 2027)
#   OPENROUTER_API_KEY_BROKER for the embeddings and the full retrieval eval (an ECS secret from SSM)
#   FORCE_CONVERT=1           reconvert every PDF (after a converter change); default converts new PDFs only
#   EVAL_SETUP                retrieval eval setup (default full; bm25_only needs no key)
set -euo pipefail

mode="${1:-full}"
: "${S3_DATA_BUCKET:?set S3_DATA_BUCKET}"
: "${BUNDLE_VERSION:?set BUNDLE_VERSION, e.g. 2027.3}"
year="${SBC_YEAR:-2027}"
bucket="s3://${S3_DATA_BUCKET}"
bundle="bundles/facts-${BUNDLE_VERSION}.sqlite"
sync() { python /app/s3_sync.py "$@"; }
step() { printf '\n== %s  [%s]\n' "$1" "$(date -u +%H:%M:%S)"; }

case "$mode" in full|bundle) ;; *) echo "usage: build-pipeline full|bundle" >&2; exit 2 ;; esac
cd /work
ln -sfn /app/config config   # the CLIs read config/ relative to the project root

step "sync down from ${bucket}"
sync down "${bucket}/data" data
sync down "${bucket}/bundles/cache" bundles   # the embedding cache, so only new chunk text is embedded

if [ "$mode" = full ]; then
  step "catalog";  sbc-portal catalog --year "$year"
  step "download"; sbc-portal download --year "$year"
  step "manifest"; sbc-portal manifest --year "$year"
  step "sync PDFs and catalog up early (a later failure doesn't lose the download)"
  sync up data "${bucket}/data"
  step "convert";  sbc-to-markdown --year "$year" ${FORCE_CONVERT:+--force}
  step "verify";   sbc-verify --year "$year"
fi

step "facts bundle ${BUNDLE_VERSION}"
sbc-build-bundle --year "$year" --version "$BUNDLE_VERSION" --release
step "embeddings"
sbc-embed-bundle "$bundle"
step "retrieval eval (${EVAL_SETUP:-full})"
mkdir -p reports
python -m evals.harness.retrieval --bundle "$bundle" --golden /app/evals/golden/0.2.0 \
  --setup "${EVAL_SETUP:-full}" | tee "reports/retrieval-${BUNDLE_VERSION}.txt"

step "sync up"
sync up data "${bucket}/data"
mkdir -p "bundles/out" && cp "$bundle" "reports/retrieval-${BUNDLE_VERSION}.txt" bundles/out/
sync up bundles/out "${bucket}/bundles/${BUNDLE_VERSION}"
[ -f bundles/embeddings.sqlite ] && mkdir -p bundles/cache-out && cp bundles/embeddings.sqlite bundles/cache-out/ \
  && sync up bundles/cache-out "${bucket}/bundles/cache"
step "done: ${bucket}/bundles/${BUNDLE_VERSION}/facts-${BUNDLE_VERSION}.sqlite"
