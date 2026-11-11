# AWS track: to do

Everything still open on the AWS deployment track (PLAN.md revision 3), as of **2026-11-11**: after WP9, the
port of the generation eval (2026-11-05), a doc audit against the code, WP11 (2026-11-10), and WP10 and WP12
(2026-11-11). Finished work is in [TASKS_DONE.md](TASKS_DONE.md); each work package's full spec is in [MIGRATION_PLAN.md](MIGRATION_PLAN.md).

All work goes to `main_AWS`, the long-lived AWS branch; it isn't merged into `main`.

Before the first deploy, the branch `main_local` runs the whole application locally with AWS stand-ins
([docs/local/LOCAL_PLAN.md](../local/LOCAL_PLAN.md)). Its L2 rebuilds the bundle `2027.2` (§2.3) and its L9 builds the
WP13 HTTP eval adapter; it merges back into `main_AWS` before the first deploy.

Items marked 👤 need you (an account, credentials or data that isn't in this environment). Everything else can be
done in code here.

---

## 1. Next up, in order

1. ~~WP8: containers and the local stack~~ (done).
2. ~~WP9: the CDK stacks~~ (done in code). 👤 **The first real deploy** (§2.4, RUNBOOK §4) can happen now; it publishes the API and the frontends (WP10).
3. ~~WP11: CI/CD~~ (done in code, with the frontend job and publishing from WP10). It deploys automatically once the first manual deploy sets `AWS_DEPLOY_ROLE_ARN` (§2.4).
4. ~~WP10: frontends~~ (done in code; checked locally, not on AWS).
5. ~~WP12: observability~~ (done in code, including optional Langfuse tracing).
6. **WP13:** portfolio readiness and docs.
7. **WP14 (later):** Bedrock.
8. **Toolchain:** move the frontends to Node 22 and the current majors (Vite, Vitest, typescript-eslint), which clears the dev-only `npm audit` advisories (§3 WP10).

👤 items 2.1–2.3 come before the first deploy (2.4). Start with the OpenRouter keys: as of 2026-11-10 the
broker key has used all of its $5 limit, so every live model call fails until the limit is raised or new keys
exist.

---

## 2. Needs you 👤

### 2.1 Accounts and external setup

- [ ] **Upgrade the AWS account to the Paid plan** before the 6-month Free plan ends. Ideally do it now: credits carry over, and Free-plan restrictions go away. **Don't** create or join an AWS Organization or Control Tower, which cancels the credits.
- [ ] Create an IAM Identity Center admin user with MFA. Don't create long-lived access keys.
- [ ] AWS Budgets at $5 and $10 a month with email alerts, plus Cost Anomaly Detection. These are free. WP9 also creates them in code; this is the manual safety net.
- [ ] Check Service Quotas → Lambda → "Concurrent executions" in `us-east-1`. New accounts can start low. Request 1,000 if it's under 100.
- [ ] Install AWS CLI v2 and have Docker running with buildx (arm64) on the machine that deploys. Upgrade Node to 22 there too: the local Node 20.10 reached end of life on 2026-10-29 (it still builds the frontends).
- [ ] `cdk bootstrap aws://<account>/us-east-1`, needed for the spike run and the first deploy (`scripts/aws/bootstrap.sh` does it).
- [ ] **OpenRouter keys and credit limits:** a broker key at $15 a month and a separate public key at $10 a month (`OPENROUTER_API_KEY_PUBLIC`; the local `.env` has only the broker key), before any public URL exists. This is the hard LLM cost cap. The current broker key is spent ($5.09 of $5, checked 2026-11-10), so raise its limit or replace it first.
- [ ] A Cloudflare Turnstile widget. Note its site key (for `deploy-web.sh`, §2.4) and secret (for `put-secrets.sh`); its allowed hostnames get the CloudFront domain after the first deploy.
- [ ] Decide whether Langfuse Cloud Hobby is wanted (optional). If so, create a project and give its keys and region to `put-secrets.sh` (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`; a US-region project exists and was checked locally on `main_local` L6); the API then traces each turn (`"tracing": "langfuse"` in deep health).

### 2.2 Spikes 2 and 3 on AWS (costs cents)

- [ ] `spikes/aws/deploy_spike.sh up`: deploys the throwaway stack and checks SSE through CloudFront, Function URL, origin access control and the Lambda Web Adapter (pass: status events about 1 s apart and `sha256_header_ok: true`).
- [ ] Wait at least 10 minutes, then `spikes/aws/deploy_spike.sh check`. Record the cold start (`Init Duration` in the REPORT lines) and the first-request latency in `docs/aws/SPIKES.md`.
- [ ] `spikes/aws/deploy_spike.sh destroy`.

### 2.3 Data and live checks

- [ ] **Rebuild the real bundle as `2027.2`** where the corpus lives. Almost all embeddings come from the cache. The local `2027.1` bundle has 9,690 plans and the manifest lists 9,692, so chunks new with the 2 added SBCs are embedded (cents; needs OpenRouter credit):
  ```bash
  uv run sbc-build-bundle --version 2027.2 --release      # needs data/catalog/sbc_manifest_2027.csv
  uv run sbc-embed-bundle bundles/facts-2027.2.sqlite
  ```
  Check the printed counts: about 1,383 preview-only plans, and 0 missing.
- [ ] Run the retrieval eval on `2027.2`: `python -m evals.harness.retrieval --bundle bundles/facts-2027.2.sqlite --golden evals/golden/0.2.0 --setup full`. Expect the 2027.1 numbers (recall@5 ≈ 0.920).
- [ ] A live answer through the API with your OpenRouter key: `APP_ENV=local FACTS_BUNDLE_PATH=bundles/facts-2027.2.sqlite uv run uvicorn --factory sbc_api.app:create_app --port 8080`, then the README's `curl` example. Also `sbc-ask "What's the deductible on BTCU8?"`.
- [ ] A live PDF proxy check: `curl -o x.pdf 'localhost:8080/api/sbc/<a preview-only plan_key>.pdf'` returns a PDF (the API checks its sha256 itself).
- [ ] Upload the bundle and embedding cache to S3 once the foundation stack has created the data bucket (`scripts/aws/upload-bundle.sh`; §2.4).

### 2.4 The first deploy (RUNBOOK §4)

- [ ] `scripts/aws/bootstrap.sh` (quota check, existing OIDC provider, `cdk bootstrap`).
- [ ] `ALARM_EMAIL=… scripts/aws/deploy.sh foundation`, then confirm the SNS/budget emails. `deploy.sh` saves each value it is given in SSM (`/sbc-deploy/<env>/`), so later deploys, including CI's, keep them.
- [ ] `scripts/aws/put-secrets.sh` (OpenRouter keys with credit limits, Turnstile secret; session secret generated).
- [ ] `scripts/aws/upload-bundle.sh bundles/facts-2027.2.sqlite bundles/embeddings.sqlite`.
- [ ] `scripts/aws/push-images.sh 2027.2` (arm64 build and push; prints the tags).
- [ ] `scripts/aws/deploy.sh app <api-tag> <build-tag>`; `seed-users.sh`; redeploy with `DEMO_USER_IDS`; `rotate-demo-password.sh`; `TURNSTILE_SITE_KEY=0x… scripts/aws/deploy-web.sh` (frontends; saves the site key for CI); add the CloudFront host to Turnstile.
- [ ] Check `https://<site>/api/healthz?deep=1` (`"stores": "dynamodb"`, both bots `ready`) and `smoke-deployed.sh --web`, then in a browser: the landing page's demo login, a Broker Assistant sign-in, plan search and a streamed answer, and a Plan Shopper session (Turnstile) and answer, on desktop and phone widths.
- [ ] Set the repository variable `AWS_DEPLOY_ROLE_ARN` (GitHub → Settings → Secrets and variables → Actions → Variables) to the `SbcApp` output `DeployRoleArn`. From then on, pushes to `main_AWS` deploy (WP11).
- [ ] Check the first CI deploy: run `deploy-aws.yml` by hand on `main_AWS`, then a rollback with the previous `image_tag`.
- [ ] Note any fixes the scripts needed (none has run against AWS yet) and update RUNBOOK §4.

---

## 3. Work packages left

### WP8: containers and the local stack: done

Done on `main_AWS` (TASKS_DONE.md, WP8). Open checks moved to §4: an end-to-end pipeline run against a real bucket,
and an arm64 build (WP11).

### WP9: infrastructure as code: done

Done on `main_AWS` (TASKS_DONE.md, WP9): the two stacks, 12 template tests (13 since WP11), the scripts and a CI `cdk` job.
Open: the first real deploy (§2.4).

### WP10: frontends: done in code

Done on `main_AWS` (TASKS_DONE.md, WP10): the `apps/` npm workspace (`chat-ui`, `landing`, `broker`, `shopper`),
`scripts/aws/deploy-web.sh`, the CI `frontend` job and publishing in `deploy-aws.yml`. Checked locally against the
API; not yet against AWS. Left:

- [ ] 👤 The **Done when** check: both bots end to end at the CloudFront URL, on desktop and phone widths (§2.4).
- [ ] Toolchain: Node 22, then Vite 7+, Vitest 4+ and a typescript-eslint without the `minimatch` advisory. Today's
      `npm audit` is clean for runtime packages; the remaining advisories are in dev tools only (Vitest's mock
      server, a lint dependency).
- [ ] Optional: a way to show an earlier thread's messages. The API keeps them in the checkpoints but has no route
      to send them back, so reopening a conversation continues it without showing it.

### WP11: CI/CD: done in code

Done on `main_AWS` (TASKS_DONE.md, WP11): `deploy-aws.yml` (called by CI after every check, or by hand for a
redeploy or a rollback), `build-data.yml`, the saved deploy context, the deploy role's CDK permissions and the
smoke test through CloudFront. Its first run comes after the first manual deploy (§2.4). Left:

- [x] A `frontend` CI job and, in `deploy-aws.yml`, the frontend build, `s3 sync --delete` and the CloudFront invalidation (with WP10).
- [ ] A question in the post-deploy smoke test: needs a token, so it comes with WP13's HTTP adapter.
- [ ] Optional: a DynamoDB Local integration job (`services:` container plus `DDB_LOCAL_ENDPOINT`), and a canary alias shift (`aws_codedeploy`), probably not worth it for a portfolio deploy.
- **Done when:** a push deploys in under 10 minutes, and a rollback through the workflow works (§4).

### WP12: observability: done in code

Done on `main_AWS` (TASKS_DONE.md, WP12): Lambda log format JSON with the `INFO` level, `demo_user_limit_reached`
and its metric filter, the request ID with "Report a problem" in the apps, the per-answer details footer, and
optional Langfuse tracing (one trace per turn).

- [x] Langfuse, one trace per turn (`sbc_api/tracing.py`), on when both keys are set.
- [ ] Optional: a span per graph node with real timings (the turn's trace records each call's latency, not its
      start time).
- [x] Unhandled errors keep their request ID (JSON `ERROR` line, response header and body) and raise the new
      `ApiErrors` alarm (9 of 10): fixed on `main_local` L6, where a 500 couldn't be traced by its ID.
- [ ] 👤 **Done when:** a deliberate error shows in CloudWatch, raises the alarm email, and is found by its request
      ID (checked locally on `main_local`: stop DynamoDB Local, then `scripts/local/find-request.sh <id>`). On the first deploy, also check that the API's lines stay JSON objects under the JSON log format (the
      metric filters read `$.event`).

### WP13: portfolio readiness and docs

- [ ] 👤 Run `seed-users.sh` (admin + demo-broker); set the demo `sub` through CDK context; redeploy.
- [ ] 👤 Add the CloudFront hostname to the Turnstile widget.
- [ ] Measure the cold first answer (target < 10 s) and the warm fast path (target ≤ 4 s p50); record them in the runbook.
- [ ] Deliberately hit every cap: per user, per session, per IP, demo, global daily, kill switch, quarantine, and the OpenRouter key limit (with a $0.10 test key).
- [ ] Check that the demo user's tokens **can't** change its password (`aws cognito-idp change-password` with a demo access token must fail).
- [ ] An `evals/harness` HTTP adapter, and a run of about 30 golden 0.2.0 items through the deployed API. It can
      reuse `generation.py`'s scoring (SSE events → `Reply`); without the trace, it scores behavior, exact
      match, plan filter and wrong-plan answers only (MIGRATION_PLAN WP13).
- [ ] The interviewer click-through on a cold system: landing → Shopper question → Broker sign-in → comparison → PDF citation (including a preview-only plan).
- [ ] **`docs/aws/RUNBOOK.md`:** sections 1–7 exist. Left: fixes from the first deploy (§4), the Lambda measurements (§2) and the full teardown (§8).
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
| WP8 | The arm64 API image builds (`buildx --platform linux/arm64`) and passes a smoke test | WP11's first CI deploy (`smoke-deployed.sh`) |
| WP11 | `deploy-aws.yml` and `build-data.yml` against AWS: a push deploys in under 10 minutes, a rollback by `image_tag` works, a `bundle` build finishes within the 1-hour credentials | 👤 after §2.4 |
| WP9 | `cdk deploy` of both stacks in a real account, and the eight `scripts/aws/*.sh` against AWS | 👤 (§2.4) |
| WP9 | The `X-Sbc-Authorization` path end to end through CloudFront with a real Cognito token | 👤 + WP13 |
| WP9 | The warmer's events reach `POST /events` through the Lambda Web Adapter (if `warmer=true`) | 👤 |
| WP10 | Both apps at the CloudFront URL: Cognito sign-in (SRP), a Turnstile session, a streamed answer, a PDF citation, CSV export, phone width | 👤 after §2.4 |
| WP12 | The JSON log format keeps the API's lines as JSON (metric filters match), and a deliberate error reaches the alarm email | 👤 after §2.4 |
| WP12 | With Langfuse keys: a turn's trace arrives (session, user, model calls, cost) and the cold start with tracing on is measured | 👤 after §2.4 |

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
  - [ ] 👤 The full gating run (353 items, about $5): blocked by the OpenRouter key's $5 total limit, which is
        used up (§2.1). Raise the limit or use a new key, then `python -m evals.harness.generation --bundle … --golden evals/golden/0.2.0`.
  - [ ] Recorded model responses, so CI can replay the generation eval without keys. The record/replay mechanism exists on `main_local` (L7, `sbc_core.replay`); left: storing recordings outside the repository (they contain SBC text) and a CI job that uses them.
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
- **Limits reply in the stream.** A refused turn is an SSE `limit` event with HTTP 200, not a 429, and the apps show it as a notice (WP10). The PDF proxy does use 429.
- **DynamoDB errors fail closed.** If DynamoDB can't be reached, a turn returns a 500, because limits can't be checked. That is deliberate (PLAN.md §3.5.6).
- **Answers are sent whole.** They are not streamed token by token, because nothing passes before the verifier; users see status events, then the answer.
- **Quarantine is checked after the turn.** The model is still called for a quarantined plan; only the answer is withheld. Checking before the turn would need the plan resolved first.
- **A cached answer still writes the audit record and the thread,** but not a LangGraph trace of its own.
- **Plain local runs** use a developer principal for both bots, in-process stores, no Turnstile check and a per-process session secret. (The full local stack on `main_local` signs in for real against a Cognito stand-in, checks Turnstile with Cloudflare's test keys and uses DynamoDB Local; docs/local/LOCAL_PLAN.md.) They never reach real users, because outside `APP_ENV=local` the API requires the real configuration.
- **The test helper `tests/unit/api/test_guards.py::app()`** changes frozen config objects with `object.__setattr__` to set limits. That is fine for tests, but it is not a pattern for the app.
