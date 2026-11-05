# AWS runbook

How to build, run, deploy and operate the app on AWS (PLAN.md revision 3).

- **Sections 1–3** are in place as of WP8.
- **Sections 4–8** are filled in by WP9 (CDK), WP11 (CI/CD) and WP13 (portfolio readiness).
- The plan is [MIGRATION_PLAN.md](MIGRATION_PLAN.md); open items are in [TODO.md](TODO.md).

---

## 1. Run locally

### 1.1 API on your machine (in-process stores)

```bash
APP_ENV=local FACTS_BUNDLE_PATH=bundles/facts-2027.2.sqlite \
  uv run uvicorn --factory sbc_api.app:create_app --port 8080
curl -s 'localhost:8080/api/healthz?deep=1'
```

- Every request is the local developer (an admin) for whichever bot it calls.
- Turnstile is skipped when `TURNSTILE_SECRET_KEY` is empty.
- The stores, checkpoints and caches are in-process.
- A bot without its `OPENROUTER_API_KEY_<BOT>` answers 503.

### 1.2 The Lambda image on DynamoDB Local (Docker Compose)

```bash
uv run python infra/docker/fixture_bundle.py bundles/fixture     # or a folder holding a real facts.sqlite
SBC_BUNDLE_DIR=../../bundles/fixture docker compose -f infra/docker/compose.yaml up --build
curl -s 'localhost:8080/api/healthz?deep=1'                       # "stores": "dynamodb"
docker compose -f infra/docker/compose.yaml down
```

- `tables` runs `sbc-create-tables` (the definitions in `sbc_aws.schema`) against DynamoDB Local, then exits.
- `api` starts from the same image Lambda runs, with a read-only root and `/tmp` writable. It reads OpenRouter keys from the repository's `.env` if that file exists.
- `SBC_BUNDLE_DIR` is relative to `infra/docker/`.

### 1.3 Build the images by hand

```bash
# API image (arm64 for Lambda; drop --platform for a local test image)
docker buildx build --platform linux/arm64 -f infra/docker/api.Dockerfile \
  --build-context bundle=bundles/fixture -t sbc-api:local .
infra/docker/smoke.sh sbc-api:local        # read-only start, deep health, plan search, prompts, non-root

# Offline build image
docker buildx build --platform linux/arm64 -f infra/docker/build.Dockerfile -t sbc-build:local .
```

- **Behind a TLS-inspecting proxy,** add `--network=host --build-arg HTTPS_PROXY=$HTTPS_PROXY --secret id=ca,src=<ca-bundle.pem>`.
- **Build contexts:** each image has its own ignore file (`*.Dockerfile.dockerignore`), so `data/`, `.venv/`, `bundles/` and `spikes/` never enter the build context. The bundle comes only from the named `bundle` context, and the API image always installs it at `/opt/bundle/facts.sqlite`. Pass `--build-arg BUNDLE_FILE=facts-2027.2.sqlite` to pick a file from a folder that holds several.

---

## 2. Measurements

Recorded locally on x86_64 (Docker 29.9), 2026-11-05. Lambda runs arm64; the WP13 run replaces these with
Lambda numbers.

| What | Value | How |
|---|---|---|
| API image, fixture bundle | ≈ 350 MB uncompressed (sum of layers), ≈ 109 MB compressed | `docker history`, `docker save \| gzip` |
| API image, real bundle | ≈ 570 MB uncompressed expected (the fixture image plus ≈ 217 MB) | spike 3 measured ≈ 549 MB with an earlier dependency set |
| Start to first `200` from `/api/healthz` | 2.6–2.8 s warm; 7.1 s on the very first start (cold page cache) | `infra/docker/smoke.sh`, 4 runs |
| Python imports at start-up | ≈ 2.3 s (`sbc_api.deps` 1.5 s, with boto3 and LangGraph; FastAPI 0.65 s) | `python -X importtime -c "import sbc_api.app"` |
| App wiring (`build_state`) | 55 ms in-process; 174 ms with DynamoDB stores | `healthz?deep=1` → `init_ms` |
| Memory after start | ≈ 89 MiB | `docker stats` |
| Offline build image | ≈ 681 MB | `docker image inspect` |
| Image build time | ≈ 36–39 s each, from scratch | `docker build` |

**Reading the numbers:**

- **Lambda memory:** 2,048 MB leaves plenty of headroom. Discovery searches scan about 60 MB of vectors from the bundle's pages.
- **Cold start:** expect about 3–5 s plus the image fetch on Lambda arm64 at 2 GB. Warm-on-load hides most of it (DEPLOYMENT_ANALYSIS §17.3).
- **If cold starts are too slow,** the first lever is lazy imports of boto3 clients and LangGraph's optional pieces.

---

## 3. The offline SBC build (data bucket)

**Image and modes:** `sbc-build` (`infra/docker/build.Dockerfile`) runs `build-pipeline` in `/work` (`SBC_PROJECT_ROOT`):

| Mode | Steps | Use it when |
|---|---|---|
| `full` | sync down → `sbc-portal catalog`, `download`, `manifest` → sync up (PDFs saved early) → `sbc-to-markdown` (new PDFs only; `FORCE_CONVERT=1` for all) → `sbc-verify` → `sbc-build-bundle --release` → `sbc-embed-bundle` → retrieval eval → sync up | Refreshing the corpus (new or changed SBCs) |
| `bundle` | sync down → `sbc-build-bundle --release` → `sbc-embed-bundle` → retrieval eval → sync up | Code changes in extraction, the grammar or the index, on the Markdown already in S3 |

**Environment:**

- `S3_DATA_BUCKET` (required) and `BUNDLE_VERSION` (required, e.g. `2027.3`);
- `SBC_YEAR` (default 2027);
- `OPENROUTER_API_KEY_BROKER`, for embeddings and the `full` eval (an ECS secret from SSM in WP9);
- `EVAL_SETUP`: `full` by default, or `bm25_only`, which needs no key.

**S3 layout** (the repository's own layout, so the CLIs work unchanged):

```text
s3://<data bucket>/
  data/catalog/…  data/raw/sbc/…  data/markdown/…  data/reference/…   # synced both ways by content (sha256)
  bundles/cache/embeddings.sqlite                                     # embedding cache: only new chunks are embedded
  bundles/<version>/facts-<version>.sqlite                            # the bundle each image is built from
  bundles/<version>/retrieval-<version>.txt                           # that bundle's retrieval eval
  pdf-cache/…                                                         # the API's PDF proxy cache (1-day expiry)
  checkpoints/…                                                       # oversized LangGraph checkpoints (rare)
```

**`infra/docker/s3_sync.py`:**

- It compares files by sha256, stored as object metadata, so unchanged files are never re-sent and a same-size change is still caught.
- It never deletes anything, on either side.
- It replaces the AWS CLI, whose v1 package pins a botocore version that conflicts with the locked boto3.

**Run it locally** against a real bucket (AWS credentials in your shell):

```bash
docker run --rm -e S3_DATA_BUCKET=<bucket> -e BUNDLE_VERSION=2027.3 -e OPENROUTER_API_KEY_BROKER \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN -e AWS_REGION=us-east-1 \
  sbc-build:local bundle
```

On AWS it runs as an ECS Fargate Spot task (4 vCPU, 8 GB, 50 GB ephemeral storage), started by `scripts/aws/run-build.sh` or the `build-data.yml` workflow (WP9, WP11). A `full` run takes roughly 2–3 hours, mostly conversion and verification.

---

## 4. First deployment

*Added in WP9.* The outline is in MIGRATION_PLAN.md §9: account, bootstrap, secrets, `cdk deploy`, data, app, users, Turnstile, verify.

## 5. Deploy and roll back

*Added in WP11.* Deploys publish a Lambda version and move the `live` alias; rolling back moves it back
(`aws lambda update-alias --function-name sbc-api --name live --function-version <previous>`).

## 6. Secrets and the demo password

*Added in WP9/WP13:* `scripts/aws/put-secrets.sh` and `scripts/aws/rotate-demo-password.sh`.

## 7. Costs and alarms

*Added in WP9/WP13.* Budgets alert at $5 and $10 a month. The expected AWS bill is ≈ $0.30–2 a month (DEPLOYMENT_ANALYSIS §17.8).

## 8. Teardown

*Added in WP13.* The outline is in MIGRATION_PLAN.md §10.
