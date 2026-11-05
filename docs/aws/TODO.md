# AWS track: to do

Everything still open on the AWS deployment track (PLAN.md revision 3), as of **2026-11-05**, after WP9 and the
port of the generation eval. Finished
work is in [TASKS_DONE.md](TASKS_DONE.md); each work package's full spec is in [MIGRATION_PLAN.md](MIGRATION_PLAN.md).

All work goes to `main_AWS`, the long-lived AWS branch; it isn't merged into `main`.

Items marked 👤 need you (an account, credentials or data that isn't in this environment). Everything else can be
done in code here.

---

## 1. Next up, in order

1. ~~WP8: containers and the local stack~~ (done).
2. ~~WP9: the CDK stacks~~ (done in code). 👤 **The first real deploy** (§2.4, RUNBOOK §4) can happen now: the API answers under `/api/*` before any frontend exists.
3. **WP11:** CI/CD, which deploys automatically.
4. **WP10:** frontends. It can start in parallel with WP11.
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

### 2.4 The first deploy (RUNBOOK §4)

- [ ] `scripts/aws/bootstrap.sh` (quota check, existing OIDC provider, `cdk bootstrap`).
- [ ] `ALARM_EMAIL=… scripts/aws/deploy.sh foundation`, then confirm the SNS/budget emails.
- [ ] `scripts/aws/put-secrets.sh` (OpenRouter keys with credit limits, Turnstile secret; session secret generated).
- [ ] `scripts/aws/upload-bundle.sh bundles/facts-2027.2.sqlite bundles/embeddings.sqlite`.
- [ ] `scripts/aws/push-images.sh 2027.2` (arm64 build and push; prints the tags).
- [ ] `scripts/aws/deploy.sh app <api-tag> <build-tag>`; `seed-users.sh`; redeploy with `DEMO_USER_IDS`; `rotate-demo-password.sh`; add the CloudFront host to Turnstile.
- [ ] Check `https://<site>/api/healthz?deep=1` (`"stores": "dynamodb"`, both bots `ready`), a public session, a broker sign-in token on `/api/broker/plans/search` sent as `X-Sbc-Authorization: Bearer …`, and a streamed chat turn through CloudFront.
- [ ] Note any fixes the scripts needed (none has run against AWS yet) and update RUNBOOK §4.

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

### WP9: infrastructure as code: done

Done on `main_AWS` (TASKS_DONE.md, WP9): the two stacks, 12 template tests, the scripts and a CI `cdk` job.
Open: the first real deploy (§2.4).

### WP10: frontends

- [ ] **Toolchain:** `apps/` workspace (npm or pnpm), React + Vite + TypeScript + Tailwind; four packages: `chat-ui`, `landing`, `broker`, `shopper`. Vite `base` per app (`/`, `/broker/`, `/shopper/`), and a dev proxy from `/api` to `localhost:8080`.
- [ ] **`chat-ui`:**
  - [ ] SSE over `fetch`: read the body stream; parse `thread`, `status`, `answer`, `clarify`, `limit`, `error` and `done`; ignore keepalive comments.
  - [ ] Every POST gets `x-amz-content-sha256` (the body's SHA-256 through `crypto.subtle`).
  - [ ] **The bearer token goes in `X-Sbc-Authorization: Bearer …`, never in `Authorization`.** CloudFront's signing replaces `Authorization` (WP9 finding).
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
- [x] **`ci.yml`:** the `cdk` job (template tests, synth, shellcheck) landed with WP9.
- [ ] **`ci.yml` additions:** a `frontend` job. A DynamoDB Local integration job is optional (`services:` container plus `DDB_LOCAL_ENDPOINT`).
- [ ] **`deploy-aws.yml`** (on push to `main_AWS`, plus `workflow_dispatch`; `permissions: id-token: write, contents: read`; actions pinned by SHA):
  1. [ ] Assume `sbc-github-deploy` through OIDC.
  2. [ ] Download the pinned bundle from S3.
  3. [ ] `docker buildx --platform linux/arm64`; push with tag `<git-sha>-<bundle-version>`.
  4. [ ] **Deploy through the CDK:** `cdk deploy SbcApp-<env> -c apiImageTag=<tag>` (the deploy role needs the CDK bootstrap roles' permissions, i.e. `sts:AssumeRole` on `cdk-*-deploy-role` and `cdk-*-file-publishing-role`). CloudFormation owns the `live` alias (WP9), so raw `update-alias` calls would be undone by the next deploy.
  5. [ ] Smoke through CloudFront: `healthz?deep=1` and one fast-path question.
  6. [ ] Optional canary: a CodeDeploy-style gradual alias shift needs `aws_codedeploy`; decide whether it's worth it for a portfolio deploy.
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
| WP9 | `cdk deploy` of both stacks in a real account, and the eight `scripts/aws/*.sh` against AWS | 👤 (§2.4) |
| WP9 | The `X-Sbc-Authorization` path end to end through CloudFront with a real Cognito token | 👤 + WP13 |
| WP9 | The warmer's events reach `POST /events` through the Lambda Web Adapter (if `warmer=true`) | 👤 |

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
  - [x] Generation eval harness on golden 0.2.0 (`evals/harness/generation.py`, ported from
        `feat/phase-1-generation-eval`; TASKS_DONE.md, P).
  - [ ] 👤 The full gating run (353 items, about $5): blocked by the OpenRouter key's $5 total limit. Raise the
        limit or use a new key, then `python -m evals.harness.generation --bundle … --golden evals/golden/0.2.0`.
  - [ ] Recorded model responses, so CI can replay the generation eval without keys.
  - [ ] The LLM judge for faithfulness and citation accuracy, after the calibration set (PLAN.md 5.4).
  - [ ] Model bake-off report (Haiku 5.5 vs Sonnet 5.5 vs Opus 5.5, per bot and per path; the cheapest model that passes every hard gate).
  - [ ] The MVP gating set (353 items of golden 0.2.0) passing all hard gates; the other 57 reported separately.
- [ ] **Phase 2:** `estimate_cost` (the calculator, with property tests), `rank_plans`, `glossary_lookup`, multi-turn session references, feedback review queues, CI eval gates enforced, deterministic mode, canary deploys, 5–10 external brokers.
- [ ] **Phase 3:** Plan Shopper polish: readability check (grade ≤ 8), WCAG 2.1 AA, an adversarial set of 120 plus a red-team session, legal review of the disclaimers, a load test.
- [ ] **Phase 4:** SLO dashboards, weekly review, backups, 2028 readiness (multi-year bundle, crosswalk), cost review.

---

## 7. Known limitations to keep in mind

- **Tokens never go in `Authorization` behind CloudFront.** Origin access control overwrites it, so the apps use `X-Sbc-Authorization`. Direct calls to the Function URL aren't possible anyway (IAM auth).
- **`PUBLIC_BASE_URL` isn't set on AWS.** Setting it would create a circular stack dependency, and nothing reads it yet.
- **Limits reply in the stream.** A refused turn is an SSE `limit` event with HTTP 200, not a 429, so the frontend must handle `limit` (WP10). The PDF proxy does use 429.
- **DynamoDB errors fail closed.** If DynamoDB can't be reached, a turn returns a 500, because limits can't be checked. That is deliberate (PLAN.md §3.5.6).
- **Answers are sent whole.** They are not streamed token by token, because nothing passes before the verifier; users see status events, then the answer.
- **Quarantine is checked after the turn.** The model is still called for a quarantined plan; only the answer is withheld. Checking before the turn would need the plan resolved first.
- **A cached answer still writes the audit record and the thread,** but not a LangGraph trace of its own.
- **Local runs** use a developer principal for both bots, in-process stores, no Turnstile check and a per-process session secret. They never reach real users, because outside `APP_ENV=local` the API requires the real configuration.
- **The test helper `tests/unit/api/test_guards.py::app()`** changes frozen config objects with `object.__setattr__` to set limits. That is fine for tests, but it is not a pattern for the app.
