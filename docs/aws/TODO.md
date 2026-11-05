# AWS track: to do

Everything still open on the AWS deployment track (PLAN.md revision 3), as of **2026-11-05**, after WP8. Finished
work is in [TASKS_DONE.md](TASKS_DONE.md); each work package's full spec is in [MIGRATION_PLAN.md](MIGRATION_PLAN.md).

Items marked 👤 need you (an account, credentials or data that isn't in this environment). Everything else can be
done in code here.

---

## 1. Next up, in order

1. ~~WP8: containers and the local stack~~ (done).
2. **WP9:** the CDK stack.
3. **WP11:** CI/CD, which deploys automatically.
4. **WP10:** frontends. It can start in parallel with WP9.
5. **WP12:** observability.
6. **WP13:** portfolio readiness and docs.
7. **WP14 (later):** Bedrock.

👤 items 2.1–2.3 can happen any time before the first deploy, and are best done before WP9.

---

## 2. Needs you 👤

### 2.1 Accounts and external setup

- [ ] **Upgrade the AWS account to the Paid plan** before the 6-month Free plan ends. Ideally do it now: credits carry over, and Free-plan restrictions go away. **Don't** create or join an AWS Organization or Control Tower, which cancels the credits.
- [ ] Create an IAM Identity Center admin user with MFA. Don't create long-lived access keys.
- [ ] AWS Budgets at $5 and $10 a month with email alerts, plus Cost Anomaly Detection. These are free. WP9 also creates them in code; this is the manual safety net.
- [ ] Check Service Quotas → Lambda → "Concurrent executions" in `us-east-1`. New accounts can start low. Request 1,000 if it's under 100.
- [ ] `cdk bootstrap aws://<account>/us-east-1`, needed for the spike run and WP9.
- [ ] **OpenRouter key credit limits:** public key $10 a month, broker key $15 a month, before any public URL exists. This is the hard LLM cost cap.
- [ ] A Cloudflare Turnstile widget. Note its site key and secret; its allowed hostnames get the CloudFront domain after WP9.
- [ ] Decide whether Langfuse Cloud Hobby is wanted (optional). If so, create a project and keep its keys.

### 2.2 Spikes 2 and 3 on AWS (costs cents)

- [ ] `spikes/aws/deploy_spike.sh up`: deploys the throwaway stack and checks SSE through CloudFront, Function URL, origin access control and the Lambda Web Adapter (pass: status events about 1 s apart and `sha256_header_ok: true`).
- [ ] Wait at least 10 minutes, then `spikes/aws/deploy_spike.sh check`. Record the cold start (`Init Duration` in the REPORT lines) and the first-request latency in `docs/aws/SPIKES.md`.
- [ ] `spikes/aws/deploy_spike.sh destroy`.

### 2.3 Data and live checks

- [ ] **Rebuild the real bundle as `2027.2`** where the corpus lives. The embeddings come from the cache, so nothing is re-embedded:
  ```bash
  uv run sbc-build-bundle --version 2027.2 --release      # needs data/catalog/sbc_manifest_2027.csv
  uv run sbc-embed-bundle bundles/facts-2027.2.sqlite
  ```
  Check the printed counts: about 1,383 preview-only plans, and 0 missing.
- [ ] Run the retrieval eval on `2027.2`: `python -m evals.harness.retrieval --bundle bundles/facts-2027.2.sqlite --golden evals/golden/0.2.0 --setup full`. Expect the 2027.1 numbers (recall@5 ≈ 0.920).
- [ ] A live answer through the API with your OpenRouter key: `APP_ENV=local FACTS_BUNDLE_PATH=bundles/facts-2027.2.sqlite uv run uvicorn --factory sbc_api.app:create_app --port 8080`, then the README's `curl` example. Also `sbc-ask "What's the deductible on BTCU8?"`.
- [ ] A live PDF proxy check: `curl -o x.pdf 'localhost:8080/api/sbc/<a preview-only plan_key>.pdf'` returns a PDF (the API checks its sha256 itself).
- [ ] Upload the bundle and embedding cache to S3 once WP9 has created the data bucket (`scripts/aws/upload-bundle.sh`, WP9).

---

## 3. Work packages left

### WP8: containers and the local stack: done

Done on `main_AWS` (TASKS_DONE.md, WP8). Open checks moved to §4: an end-to-end pipeline run against a real bucket,
and an arm64 build (WP11).

### WP9: infrastructure as code (AWS CDK, Python)

- [ ] **`infra/cdk/`** as its own uv project: `pyproject.toml`, `uv.lock`, `cdk.json`, `app.py`; `sbc_infra/config.py` with account, region, names, limits and the warmer flag; `sbc_infra/stack.py` with `SbcPortfolioStack`.
- [ ] **`DataConstruct`:**
  - [ ] S3 data bucket: SSE-S3, public access blocked, `RETAIN`. The layout is the one the build image writes (RUNBOOK §3): `data/…`, `bundles/cache/`, `bundles/<version>/`, `pdf-cache/`, `checkpoints/`. Lifecycle: `pdf-cache/` 1 day, `checkpoints/` with TTL-aligned expiry, old `bundles/<version>/` 90 days, optional `data/raw/` to Glacier IR at 30 days.
  - [ ] DynamoDB `app`, `audit` and `checkpoints` tables: on-demand, `PK`/`SK` strings, TTL `expires_at` (app, audit) and `ttl` (checkpoints), `RETAIN`, PITR off. They must match `sbc_aws.schema.TABLE_SPECS`.
- [ ] **`AuthConstruct`:** Cognito user pool (self sign-up off, MFA **optional** with TOTP, password policy); SPA app client with SRP, no secret, and **without** the `aws.cognito.signin.user.admin` scope; access token 1 h, refresh 12 h; groups `broker` and `admin`.
- [ ] **`ApiConstruct`:**
  - [ ] ECR repository, keeping the last 3 images.
  - [ ] `DockerImageFunction`: arm64, 2,048 MB, 60 s timeout, 1,024 MB `/tmp`, 14-day log group.
  - [ ] Environment: `APP_ENV=dev`, `SSM_PREFIX=/sbc/dev/`, table names, bucket, Cognito IDs, `PUBLIC_BASE_URL`, `SBC_PORTFOLIO__DEMO_USER_IDS`.
  - [ ] `live` alias.
  - [ ] Function URL: `AWS_IAM` auth, `RESPONSE_STREAM`.
- [ ] **`WebConstruct`:**
  - [ ] Web bucket (private) and a CloudFront distribution (`PriceClass_100`, default domain).
  - [ ] Origin access control for S3, and `FunctionUrlOrigin.with_origin_access_control` for `/api/*`, which adds both Lambda permissions.
  - [ ] Behaviors: `/api/*` with `CachingDisabled`, `AllViewerExceptHostHeader` and all methods; `/broker/*`, `/shopper/*` and `/` from S3, with a CloudFront Function for SPA rewrites.
  - [ ] A response headers policy (CSP, HSTS, nosniff).
  - [ ] The `CloudFront-Viewer-Address` header forwarded to the origin (needed for the per-IP limits and Turnstile's `remoteip`).
- [ ] **`BuildConstruct`:** ECS cluster; Fargate task definition for the `sbc-build` image (arm64, 4 vCPU, 8 GB, 50 GB ephemeral storage, `FARGATE_SPOT`).
  - Environment: `S3_DATA_BUCKET`, `BUNDLE_VERSION` (overridden per run), `SBC_YEAR`, `AWS_REGION`.
  - `OPENROUTER_API_KEY_BROKER` as an ECS secret from SSM.
  - Command `full` or `bundle`.
  - Task role: S3 read/write on the data bucket.
  - Runs in the default VPC's public subnets with a public IP (no NAT).
  - An ECR repository for the build image too (keep the last 2).
- [ ] **`OpsConstruct`:**
  - [ ] SNS topic with email.
  - [ ] Alarms, at most 10: Lambda `Errors`, `Throttles`, p95 `Duration` > 20 s, and metric filters for `plan_filter_violation`, `global_cap_reached`, `pdf_hash_mismatch`, `audit_failed` and `demo_user_limit_reached` (rate-limited events from the demo account).
  - [ ] Budgets ($5 and $10) and a Cost Anomaly monitor.
  - [ ] An optional business-hours warmer: EventBridge Scheduler pinging `/api/healthz?deep=1` every 5 min, Monday–Friday 08:00–20:00.
- [ ] **`CicdConstruct`:** GitHub OIDC provider, and a `sbc-github-deploy` role trusted for `repo:syamsruthin/sbc-agentic-rag` on `refs/heads/main_AWS` (plus an environment), with least privilege: push to one ECR repo; `lambda:UpdateFunctionCode`, `PublishVersion`, `UpdateAlias` and `GetFunction`; S3 sync to the web bucket; `s3:GetObject` on `bundles/*`; `cloudfront:CreateInvalidation`.
- [ ] **IAM for `sbc-api-exec`** (DEPLOYMENT_ANALYSIS.md §6.5):
  - DynamoDB read/write on the app and checkpoints tables, and **`PutItem` only** on audit;
  - S3 on `pdf-cache/*` and `checkpoints/*`;
  - `ssm:GetParametersByPath` on `/sbc/dev/*`, plus `kms:Decrypt` through the `aws/ssm` key;
  - logs.
- [ ] **`infra/cdk/tests/test_stack.py`** (`aws_cdk.assertions`):
  - no NAT gateway, load balancer, Secrets Manager secret or customer-managed KMS key;
  - Function URL `RESPONSE_STREAM`;
  - audit table `PutItem` only;
  - every log group has retention set;
  - table keys and TTL attributes equal `sbc_aws.schema.TABLE_SPECS`.
- [ ] **Scripts** in `scripts/aws/`:
  - [ ] `bootstrap.sh`
  - [ ] `put-secrets.sh dev`: prompts for the values; writes SecureStrings `OPENROUTER_API_KEY_BROKER`, `OPENROUTER_API_KEY_PUBLIC`, `SESSION_JWT_SECRET` (generated, at least 32 bytes), `TURNSTILE_SECRET_KEY`, and optionally the Langfuse keys.
  - [ ] `upload-bundle.sh`: `bundles/facts-<v>.sqlite` → `s3://…/bundles/<v>/`, and `bundles/embeddings.sqlite` → `s3://…/bundles/cache/` (it can use `infra/docker/s3_sync.py`).
  - [ ] `seed-users.sh`: admin with TOTP enrollment, and `demo-broker` with no MFA in the `broker` group; outputs the demo `sub`.
  - [ ] `rotate-demo-password.sh`: Cognito plus `/landing/demo.json` plus invalidation.
  - [ ] `run-build.sh`: `aws ecs run-task`.
- **Done when:** `cdk synth` and the stack tests pass in CI, and `cdk deploy` creates the stack.

### WP10: frontends

- [ ] **Toolchain:** `apps/` workspace (npm or pnpm), React + Vite + TypeScript + Tailwind; four packages: `chat-ui`, `landing`, `broker`, `shopper`. Vite `base` per app (`/`, `/broker/`, `/shopper/`), and a dev proxy from `/api` to `localhost:8080`.
- [ ] **`chat-ui`:**
  - [ ] SSE over `fetch`: read the body stream; parse `thread`, `status`, `answer`, `clarify`, `limit`, `error` and `done`; ignore keepalive comments.
  - [ ] Every POST gets `x-amz-content-sha256` (the body's SHA-256 through `crypto.subtle`).
  - [ ] Bearer token handling.
  - [ ] Components: chat stream with status line, citations panel (opening `/api/sbc/...pdf#page=N`), comparison table, plan picker (`/api/{bot}/plans/search`), clarification options, and a limit or kill-switch notice.
  - [ ] Feedback buttons (thumbs plus reason codes).
  - [ ] Warm-on-load: `fetch('/api/healthz')` at start.
- [ ] **`broker`:** Cognito sign-in (`amazon-cognito-identity-js`, or Amplify Auth only) with SRP and TOTP for admins; tokens in memory with refresh; plan picker filters; CSV export of comparisons; thread list from `/api/broker/threads`, or browser-held thread IDs for the demo account (`shared_account: true`).
- [ ] **`shopper`:** Turnstile widget → `POST /api/public/session` with the state; token in memory; guided state → plan flow; plain-language UI; the "don't enter personal or health information" notice.
- [ ] **`landing`:**
  - [ ] Intro and "Try Plan Shopper" / "Try Broker Assistant" buttons.
  - [ ] Demo credentials from `/landing/demo.json` (runtime, not baked in), with copy buttons and the shared-account note.
  - [ ] "Try these" chips (4–6 per bot: a lookup, a comparison, a coverage check, a clarification and an out-of-scope question).
  - [ ] "How it works" page (diagrams) and "Quality and cost" page (eval numbers, cost per question, monthly AWS cost).
- [ ] **Environment:** `VITE_API_BASE_URL=/api`, `VITE_COGNITO_USER_POOL_ID`, `VITE_COGNITO_CLIENT_ID`, `VITE_TURNSTILE_SITE_KEY` from the CDK outputs.
- [ ] **CI job:** `npm ci`, lint, typecheck, build.
- **Done when:** both bots work end to end at the CloudFront URL, on desktop and phone widths.

### WP11: CI/CD

- [x] **`ci.yml`:** the `image` job (API image on the fixture bundle + `smoke.sh`) landed with WP8.
- [ ] **`ci.yml` additions:** a `cdk` job (`uv --project infra/cdk run pytest`, `cdk synth`), a `frontend` job, and the existing jobs already covering `sbc_aws` and `sbc_api`. A DynamoDB Local integration job is optional (`services:` container plus `DDB_LOCAL_ENDPOINT`).
- [ ] **`deploy-aws.yml`** (on push to `main_AWS`, plus `workflow_dispatch`; `permissions: id-token: write, contents: read`; actions pinned by SHA):
  1. [ ] Assume `sbc-github-deploy` through OIDC.
  2. [ ] Download the pinned bundle from S3.
  3. [ ] `docker buildx --platform linux/arm64`; push with tag `<git-sha>-<bundle-version>`.
  4. [ ] `update-function-code`, `wait function-updated`, `publish-version`.
  5. [ ] Smoke the new version directly: `healthz?deep=1` and one fast-path question.
  6. [ ] Move the `live` alias (optional 10% weighted canary for 10 minutes).
  7. [ ] Build the SPAs with the CDK outputs, `s3 sync --delete`, invalidate CloudFront.
- [ ] **`build-data.yml`** (`workflow_dispatch`, inputs `version` and `full|incremental`): runs `scripts/aws/run-build.sh` and waits for the task.
- **Done when:** a push deploys in under 10 minutes, and an alias rollback takes under 1 minute.

### WP12: observability

- [ ] Lambda log format JSON; `LOG_LEVEL` honored. The metric filters and alarms come from WP9.
- [ ] `x-request-id` shown in the UI's "report a problem" link.
- [ ] Optional Langfuse: attach the LangGraph Langfuse callback when `EnvSettings.langfuse_enabled`, with the redaction mask and PLAN.md §7.1's trace attributes (`bot`, `bundle_version`, `config_hash`, prompt versions, usage).
- [ ] An optional per-answer "details" footer (path, model calls, cost) from the existing trace fields.
- [ ] Log `demo_user_limit_reached` as its own event for the WP9 metric filter. Today the demo account's refusals log as `rate_limited` with `demo: true`.
- **Done when:** a deliberate error appears in CloudWatch, triggers the alarm email, and can be found by request ID.

### WP13: portfolio readiness and docs

- [ ] 👤 Run `seed-users.sh` (admin + demo-broker); set the demo `sub` through CDK context; redeploy.
- [ ] 👤 Add the CloudFront hostname to the Turnstile widget.
- [ ] Measure the cold first answer (target < 10 s) and the warm fast path (target ≤ 4 s p50); record them in the runbook.
- [ ] Deliberately hit every cap: per user, per session, per IP, demo, global daily, kill switch, quarantine, and the OpenRouter key limit (with a $0.10 test key).
- [ ] Check that the demo user's tokens **can't** change its password (`aws cognito-idp change-password` with a demo access token must fail).
- [ ] An `evals/harness` HTTP adapter, and a run of about 30 golden 0.2.0 items through the deployed API.
- [ ] The interviewer click-through on a cold system: landing → Shopper question → Broker sign-in → comparison → PDF citation (including a preview-only plan).
- [ ] **`docs/aws/RUNBOOK.md`:** deploy, rollback, rotate secrets and the demo password, rebuild data, check costs, teardown.
- [ ] README "Live demo" section with the URL and demo-login instructions.
- [ ] Final pass on PLAN.md, TODO.md and `docs/` (revision 3 is already applied).
- **Done when:** the click-through works with the first answer in under 10 s, and the runbook is complete.

### WP14 (later, optional): Amazon Bedrock

- [ ] `EmbeddingModel` and `Reranker` protocols in `sbc_core.interfaces`; `TextSearcher.with_gateway` takes them instead of `OpenRouterClient`.
- [ ] `sbc_aws.bedrock.BedrockGateway` implementing `ChatModel` on the Converse API: message and tool conversion, `toolChoice`, effort mapping, `cachePoint`, model fallbacks, and `Usage` from tokens plus a price table.
- [ ] Embeddings through Titan Text Embeddings V2 (1024 dimensions) or Cohere Embed v4; rerank through Amazon Rerank 1.0 or Cohere Rerank 3.5.
- [ ] `provider: openrouter | bedrock` per model role in `config/base.yaml`.
- [ ] Re-embed the bundle; re-run the Phase 0 retrieval gates and the Phase 1 gating set.
- [ ] IAM `bedrock:InvokeModel*` and `bedrock:Rerank` on the chosen ARNs. 👤 Paid plan and model access.
- [ ] The BAA decision (PLAN.md §1.4.2).

---

## 4. Open checks from finished work

| From | Check | Who |
|---|---|---|
| Spikes 2 and 3 | The AWS run of `deploy_spike.sh` (streaming through CloudFront, real cold start) | 👤 |
| WP3 | Rebuild the bundle `2027.2` with the manifest; ≈ 1,383 preview-only and 0 missing | 👤 |
| WP4 | A live streamed answer with a real OpenRouter key and bundle | 👤 |
| WP5 | Sign-in against a real Cognito pool, and Turnstile with a real widget (after WP9) | 👤 + WP13 |
| WP7 | A live portal fetch of one preview-only SBC, with its hash matching | 👤 |
| WP8 | An end-to-end `build-pipeline bundle` (then `full`) run against the real data bucket, as an ECS task | 👤 after WP9 |
| WP8 | The arm64 API image builds (`buildx --platform linux/arm64`) and passes `smoke.sh` | WP11 (CI) |

---

## 5. Deferred (decided)

- [ ] **Presidio NER** for names and addresses (decision D3: after launch). Add `en_core_web_sm` to the image, load it at init, and re-measure the cold start. The regex redaction (SSN, member ID, phone, email, dates) stays until then.
- [ ] **Sliding-window rate limits.** Fixed windows are in place (MIGRATION_PLAN §11); revisit only if a production load profile needs it.
- [ ] **Postgres/Aurora**, only if SQL analytics over conversations become a requirement (DEPLOYMENT_ANALYSIS §5.1).
- [ ] **WAF, a custom domain, PITR**: production items (DEPLOYMENT_ANALYSIS §13).

---

## 6. Product work outside the AWS track (from the root TODO.md and PLAN.md §11)

The AWS track hosts the app; these are features and evaluations the application itself still needs.

- [ ] **Phase 0 leftover:** taxonomy v2 synonyms from the eval ("delivering (a baby)" is unmapped; "pharmacy" maps to every drug tier; "hospital" adds inpatient rows to delivery questions).
- [ ] **Phase 1:**
  - [ ] Generation eval harness on golden 0.2.0 (answers, not just retrieval), with recorded responses.
  - [ ] Model bake-off report (Haiku 5.5 vs Sonnet 5.5 vs Opus 5.5, per bot and per path; the cheapest model that passes every hard gate).
  - [ ] The MVP gating set (353 items of golden 0.2.0) passing all hard gates; the other 57 reported separately.
- [ ] **Phase 2:** `estimate_cost` (the calculator, with property tests), `rank_plans`, `glossary_lookup`, multi-turn session references, feedback review queues, CI eval gates enforced, deterministic mode, canary deploys, 5–10 external brokers.
- [ ] **Phase 3:** Plan Shopper polish: readability check (grade ≤ 8), WCAG 2.1 AA, an adversarial set of 120 plus a red-team session, legal review of the disclaimers, a load test.
- [ ] **Phase 4:** SLO dashboards, weekly review, backups, 2028 readiness (multi-year bundle, crosswalk), cost review.

---

## 7. Known limitations to keep in mind

- **Limits reply in the stream.** A refused turn is an SSE `limit` event with HTTP 200, not a 429, so the frontend must handle `limit` (WP10). The PDF proxy does use 429.
- **DynamoDB errors fail closed.** If DynamoDB can't be reached, a turn returns a 500, because limits can't be checked. That is deliberate (PLAN.md §3.5.6).
- **Answers are sent whole.** They are not streamed token by token, because nothing passes before the verifier; users see status events, then the answer.
- **Quarantine is checked after the turn.** The model is still called for a quarantined plan; only the answer is withheld. Checking before the turn would need the plan resolved first.
- **A cached answer still writes the audit record and the thread,** but not a LangGraph trace of its own.
- **Local runs** use a developer principal for both bots, in-process stores, no Turnstile check and a per-process session secret. They never reach real users, because outside `APP_ENV=local` the API requires the real configuration.
- **The test helper `tests/unit/api/test_guards.py::app()`** changes frozen config objects with `object.__setattr__` to set limits. That is fine for tests, but it is not a pattern for the app.
