# AWS runbook

How to build, run, deploy and operate the app on AWS (PLAN.md revision 3).

- **Sections 1–7** are in place as of WP9, and §5 has the WP11 workflows; WP13 (portfolio readiness) completes 8.
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
- **The frontends** run against it: `cd apps && npm install`, then `npm run dev:broker`, `npm run dev:shopper` and
  `npm run dev:landing`, and open http://localhost:5173 (apps/README.md). Locally the broker app skips sign-in
  and the shopper skips Turnstile.

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

You need: AWS CLI v2 with credentials for the account (an IAM Identity Center profile, `export AWS_PROFILE=…`),
Docker with buildx (arm64), uv, Node 20.10 or newer (for `npx` and the frontends; Node 22 recommended), the
Turnstile widget's site key, and the bundle `2027.2` built locally (TODO.md §2.3). Every
script reads `SBC_ENV` (default `dev`) and uses `us-east-1`.

| # | Step | Command |
|---|---|---|
| 1 | Account checks (Paid plan, quotas) and CDK bootstrap | `scripts/aws/bootstrap.sh` (prints `githubOidcProviderArn` if a GitHub provider already exists) |
| 2 | Foundation: bucket, tables, Cognito, ECR, budgets | `ALARM_EMAIL=you@example.com scripts/aws/deploy.sh foundation` (saves the email for later deploys) |
| 3 | Secrets into SSM (OpenRouter keys with credit limits set first, Turnstile secret; session secret generated) | `scripts/aws/put-secrets.sh` |
| 4 | Bundle and embedding cache into the data bucket | `scripts/aws/upload-bundle.sh bundles/facts-2027.2.sqlite bundles/embeddings.sqlite` |
| 5 | Build and push the arm64 images (prints the tags) | `scripts/aws/push-images.sh 2027.2` |
| 6 | App: Lambda, CloudFront, build task, alarms, deploy role | `scripts/aws/deploy.sh app <api-tag> <build-tag>` (prints the site URL; add `GITHUB_OIDC_PROVIDER_ARN=…` if step 1 printed one) |
| 7 | Users: admin (TOTP at first sign-in) and demo-broker | `scripts/aws/seed-users.sh <admin> <admin-email>` (prints the demo `sub`) |
| 8 | Redeploy with the demo account's ID | `DEMO_USER_IDS='["<sub>"]' scripts/aws/deploy.sh app <api-tag>` |
| 9 | Publish the demo password to the landing page | `scripts/aws/rotate-demo-password.sh` |
| 10 | Frontends: build with the stack outputs, publish, invalidate (saves the site key in SSM for CI) | `TURNSTILE_SITE_KEY=0x… scripts/aws/deploy-web.sh` |
| 11 | Turnstile: add the CloudFront host name to the widget's allowed domains | Cloudflare dashboard |
| 12 | Confirm the SNS subscription email, then check | `scripts/aws/smoke-deployed.sh 2027.2` (deep health, a POST through origin access control), then `scripts/aws/smoke-deployed.sh --web` |
| 13 | Turn on CI deploys | GitHub → Settings → Secrets and variables → Actions → Variables: `AWS_DEPLOY_ROLE_ARN` = the `SbcApp` output `DeployRoleArn` |

**Saved deploy context.** `deploy.sh` saves each value you give it (`ALARM_EMAIL`, `DEMO_USER_IDS`, `WARMER`,
`GITHUB_OIDC_PROVIDER_ARN`, `BUILD_IMAGE_TAG` or the third argument) under `/sbc-deploy/<env>/` in SSM after a
successful deploy, and every later deploy reuses them, so a deploy that leaves one out (CI's, for example) doesn't
remove the alarm email or the demo account's limits. `aws ssm get-parameters-by-path --path /sbc-deploy/dev`
shows them; to clear one, `aws ssm delete-parameter --name /sbc-deploy/dev/<key>`.

Then open the site URL: the landing page shows the demo login (step 9); sign in to the Broker Assistant with it,
and start a Plan Shopper session (it needs step 11).

## 5. Deploy and roll back

- **Deploy a new API image:** push to `main_AWS`. After every CI check passes, the `deploy` job runs
  `deploy-aws.yml`: it builds the arm64 image with the pinned bundle (`PINNED_BUNDLE_VERSION`) on a native arm64
  runner, pushes `<git-sha12>-<bundle>`, runs `scripts/aws/deploy.sh app <tag>` (CloudFormation publishes a new
  version and moves `live`) and `scripts/aws/smoke-deployed.sh`, then builds and publishes the frontends
  (`scripts/aws/deploy-web.sh`, `smoke-deployed.sh --web`). Docs-only pushes don't deploy.
  - **Frontends only,** by hand: `scripts/aws/deploy-web.sh` (it reads the saved Turnstile site key).
  - **By hand,** without CI: `scripts/aws/push-images.sh <bundle-version> api`, then `scripts/aws/deploy.sh app <new-tag>`.
  - **Changes that widen IAM** (a new permission, a new role) fail in CI on purpose: CI deploys with
    `--require-approval broadening` and no terminal. Deploy them by hand with `deploy.sh`, which asks.
- **Roll back:** Actions → Deploy to AWS → Run workflow on `main_AWS` with `image_tag` set to an earlier tag
  (ECR keeps the last 3 API images; the build is skipped), or `scripts/aws/deploy.sh app <previous-tag>`. In an emergency,
  `aws lambda update-alias --function-name sbc-dev-api --name live --function-version <previous>` works at
  once, but the next `cdk deploy` moves the alias to whatever tag it is given, so follow it with a deploy of
  the tag you want.
  - **Frontends:** check out the commit you want and run `scripts/aws/deploy-web.sh`. A workflow rollback with
    `image_tag` republishes the frontends from the branch head.
- **New data:** Actions → Build SBC data (`version` 2027.3, `bundle` or `full`), or `scripts/aws/run-build.sh 2027.3 bundle --wait`.
  Read the bundle's `retrieval-2027.3.txt`, then set `PINNED_BUNDLE_VERSION` in `deploy-aws.yml` and push (or
  run the deploy workflow with `bundle_version`). The workflow waits for a `bundle` build but only starts a `full`
  one (2–3 hours, longer than the deploy role's 1-hour credentials).

## 6. Secrets and the demo password

- Secrets live only in SSM Parameter Store (`/sbc/<env>/…`, SecureString, AWS-managed key). Rotate one with
  `scripts/aws/put-secrets.sh` (an empty answer keeps a value); the Lambda reads them at cold start, so force
  new instances with a redeploy. `--rotate-session-secret` invalidates every public session.
- `scripts/aws/rotate-demo-password.sh` sets a new random password for `demo-broker` and republishes
  `/landing/demo.json` (no rebuild). The demo account can't change its own password (no
  `aws.cognito.signin.user.admin` scope on the SPA client).

## 7. Costs and alarms

- **Budget:** `monthlyBudgetUsd` (default $10) with email at 50/80/100% actual and 100% forecast, plus daily
  Cost Anomaly Detection (needs `alarmEmail`). Expected AWS bill ≈ $0.30–2 a month (DEPLOYMENT_ANALYSIS §17.8).
- **Alarms (9, inside the free 10), to the SNS topic `sbc-<env>-alerts`:** Lambda errors, throttles, p95
  duration > 20 s; and from the API's JSON logs: `global_cap_reached`, `pdf_hash_mismatch`, `audit_failed`, the
  demo account hitting its limits, any `PlanFilterViolationError` (P1), and `ApiErrors`, any `ERROR` line (an
  HTTP 500 through the Lambda Web Adapter isn't a Lambda error, so `lambda-errors` misses it). Each error line has
  the `request_id` the apps show under "Report a problem"; find it with a Logs Insights query on `request_id`.
- **The hard LLM cap** is the OpenRouter key credit limits, not AWS.

## 8. Teardown

*Completed in WP13.* `cdk destroy SbcApp-<env>` then `SbcFoundation-<env>`; the data bucket, web bucket, tables,
user pool and ECR repositories are retained on purpose, so delete them by hand afterwards (MIGRATION_PLAN §10),
then delete the SSM parameters and revoke the OpenRouter keys.
