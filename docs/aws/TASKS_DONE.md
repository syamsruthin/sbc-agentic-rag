# AWS track: tasks done

Everything finished so far on the AWS deployment track (PLAN.md revision 3), on branch `main_AWS`.

- Status as of **2026-11-05**, after WP9.
- What's left is in [TODO.md](TODO.md).
- The plan these tasks follow is [MIGRATION_PLAN.md](MIGRATION_PLAN.md).
- The analysis behind it is [DEPLOYMENT_ANALYSIS.md](DEPLOYMENT_ANALYSIS.md).

---

## 1. At a glance

| # | Task | Commit | Date | Result |
|---|---|---|---|---|
| A | Discovery and AWS deployment analysis | `6ffc89a` | 2026-11-03 | `docs/aws/DEPLOYMENT_ANALYSIS.md` (architecture, costs, three scenarios, portfolio mode, free tier, DigitalOcean) |
| WP0 | Branch `main_AWS` and the migration plan | `6ffc89a`, `7a85b56` | 2026-11-03 | `docs/aws/MIGRATION_PLAN.md` (WP0–WP14) and decisions D1–D3 |
| S | Spikes 1–3 | `1518e33` | 2026-11-04 | Spike 1 passed; spikes 2 and 3 passed locally (AWS leg pending). `docs/aws/SPIKES.md` |
| WP1 | Configuration for AWS | `5115bd3` | 2026-11-04 | Supabase/Upstash settings replaced by AWS ones; Cognito auth; portfolio limits; `immutable=1` |
| WP2 | `sbc_aws` adapters | `b91e8c7` | 2026-11-04 | 10 modules; 37 moto tests + 2 DynamoDB Local tests |
| WP3 | Bundle `plan_source` table | `f38e0f2` | 2026-11-04 | PDF form fields + sha256 in the bundle; reader `pdf_source()`; proxy links |
| WP4 | FastAPI app, SSE, agent streaming | `135deea` | 2026-11-04 | `sbc_api` app, chat/plans/health routes, `Agent.stream()`; 14 tests + 1 integration test |
| WP5 | Authentication | `db6b62a` | 2026-11-05 | Cognito JWKS verification, public sessions after Turnstile; 21 tests |
| WP6 | Guards | `44ca2fc` | 2026-11-05 | Limits, daily cap, answer cache, kill switch, quarantine, ownership, audit, feedback, admin; 17 tests |
| WP7 | SBC PDF proxy | `2a29bde` | 2026-11-05 | `GET /api/sbc/{plan_key}.pdf`; 9 tests |
| D | Documentation sync and these tracking files | `af59ee0` | 2026-11-05 | PLAN.md revision 3, README, TODO.md, docs, `TASKS_DONE.md`, `TODO.md` |
| WP8 | Containers and the local stack | `a2ec16b` | 2026-11-05 | API and build images, compose with DynamoDB Local, smoke test, CI image job, RUNBOOK.md; 5 tests |
| B | Branch policy: `main_AWS` not merged into `main` | `8adb851` | 2026-11-05 | PR #14 closed unmerged; CI runs on pushes to `main_AWS` |
| WP9 | CDK stacks, operator scripts | (this commit) | 2026-11-05 | `SbcFoundation` + `SbcApp`, 12 template tests, 8 scripts, CI `cdk` job; `X-Sbc-Authorization` fix; 4 more tests |

**Health at the time of writing:**

- ruff check and ruff format pass on 185 files.
- mypy passes on 136 source files; `sbc_core`, `sbc_extract`, `sbc_tools` and `sbc_aws` are strict.
- import-linter keeps all 7 contracts.
- Unit tests: **419 passed**; CDK template tests: **12 passed**; shellcheck clean on every script.
- The API image builds and passes `smoke.sh`, the compose stack runs on DynamoDB Local, and both CDK stacks synthesize.
- Deterministic eval gate: plan filter 1.0, resolution 1.0, 0 confident wrong-plan, service mapping 1.0, recall@5 1.0 on the fixture.
- DynamoDB Local integration tests: 3 passed (the 2 ingest regression tests skip without corpus data).

---

## 2. Decisions taken (with the person's answers)

| When | Question | Decision |
|---|---|---|
| 2026-11-03 | LLM provider for AWS | Analyze both; **OpenRouter for now**, **Amazon Bedrock as a later phase** (WP14), driven by the HIPAA BAA need before GA |
| 2026-11-03 | Operational database | Analyze both; **DynamoDB** recommended and adopted (no VPC, so no NAT; $0 idle). Aurora/RDS documented as alternatives |
| 2026-11-03 | Third-party SaaS | **AWS-native where cheap**: Cognito, DynamoDB, Parameter Store, S3 + CloudFront, CloudWatch. Kept external: OpenRouter, Cloudflare Turnstile, Langfuse (optional), GitHub Actions |
| 2026-11-03 | Region | **us-east-1** |
| 2026-11-03 | Scope of the analysis | The **target application in PLAN.md**, with built and not-built pieces marked |
| 2026-11-03 | Bots in the demo | **Both** (Broker Assistant and Plan Shopper) |
| 2026-11-03 | AWS account | **New account** (after 15 July 2025): credit-based Free plan |
| 2026-11-03 | Where the analysis goes | Markdown; later committed to `docs/aws/` |
| 2026-11-03 | Free tier for 5–6 months? | AWS side fits the credits (≈ $12 on-demand, ≈ $3 in portfolio mode); LLM tokens are not covered; **upgrade to the Paid plan early** |
| 2026-11-03 | DigitalOcean vs AWS | **AWS** (≈ $0 vs ≈ $48–58 out of pocket over 6 months, no idle cost, nothing to patch, better interview story) |
| 2026-11-03 | Use case | **Personal portfolio project**; interviewers get the URL; **both bots**; **default `*.cloudfront.net` URL** |
| 2026-11-03 | Branch | **`main_AWS`**, from `main`; plan + analysis committed under `docs/aws/` |
| 2026-11-03 | IaC tool | **AWS CDK (Python)** |
| 2026-11-03 | D1: replace or keep Supabase/Upstash settings | **Replace** (no platform switch) |
| 2026-11-03 | D2: demo broker credentials | **Shown on the landing page** (with the safeguards in MIGRATION_PLAN §12) |
| 2026-11-03 | D3: Presidio NER before launch | **Deferred** until after launch; regex redaction stays |
| 2026-11-04 | Branching per work package | Commit straight to `main_AWS` (no per-WP branches or PRs) |
| 2026-11-05 | Tracking files and old docs | Files in **`docs/aws/`**; **update the older docs now** (PLAN.md revision 3) |
| 2026-11-05 | Merging into `main` | **Don't.** `main_AWS` stays the long-lived main branch of the AWS implementation; PR #14 (`main_AWS` → `main`) was closed unmerged. CI now also runs on pushes to `main_AWS` |

---

## 3. Details

### A. Discovery and the deployment analysis (`6ffc89a`)

- **Read on `main` at `e7ec0e7`:** README, PLAN.md, TODO.md, the configs, and every runtime package (`sbc_core.llm`, `config`, `facts`, `search`; `sbc_retrieval`; `sbc_tools`; `sbc_agent.graph`, `respond`, `cli` and `redact`).
- **Built at the time:** ingest, extract and index, the facts bundle (217 MB with embeddings), the agent graph, the tools, the verifier, and the `sbc-ask` CLI.
- **Not built at the time:** the API, the frontends, the Supabase schema, auth, Langfuse, Docker and Cloud Run configuration.
- **What the analysis covers** (`docs/aws/DEPLOYMENT_ANALYSIS.md`, 19 sections):
  - current and proposed architecture, with Mermaid diagrams;
  - mapping of 26 components to AWS services;
  - the database, model-provider and compute decisions;
  - the gap analysis, environment variables, IAM roles and networking;
  - detailed configuration and a service-by-service cost table;
  - three scenarios: always-on ≈ $18.90 a month, on-demand ≈ $1.96, ultra-low ≈ $0.40;
  - per-session cost (≈ $0.95 all-in), start/stop automation and hidden costs;
  - risks and a roadmap;
  - added later: portfolio mode (§17), free-tier fit (§18) and the DigitalOcean comparison (§19).
- **Pricing facts checked** in November 2026:
  - App Runner closed to new customers on 30 April 2026;
  - Lambda SnapStart for containers costs money for Python;
  - CloudFront with OAC needs `x-amz-content-sha256` on POSTs;
  - Aurora Serverless v2 can scale to 0 ACU;
  - Cognito is free up to 10K MAU;
  - Bedrock lists Claude 5.5 models, but not bge-m3 or the qwen3 reranker;
  - Free-plan credits aren't documented as covering Claude on Bedrock.

### WP0: branch and plan (`6ffc89a`, `7a85b56`)

- `main_AWS` was created from `origin/main` and pushed.
- **`docs/aws/MIGRATION_PLAN.md`:**
  - scope and decisions, plus a file-by-file table of the current code and its AWS impact;
  - the target layout and import-linter contracts;
  - WP0–WP14 with files, sketches, tests and "done when" checks;
  - specs: HTTP API, SSE events, DynamoDB keys, environment variables, IAM;
  - testing strategy, local development, first-deploy runbook, rollback and teardown;
  - deviations from PLAN.md, risks, sequencing and a definition of done.
- **Found while planning:** the bundle had neither the portal preview-form fields nor the PDF sha256, so the PDF proxy needed WP3.
- **D1–D3 recorded** (`7a85b56`), with the safeguards for showing the demo credentials publicly.

### S: spikes (`1518e33`, results in `SPIKES.md`)

- **Spike 1, checkpoint resume across processes: passed.**
  - Setup: `langgraph-checkpoint-aws==1.2.6` with the locked langgraph 1.2.19, against DynamoDB Local 2.6.1.
  - Process A asked a clarifying question; process B, scripted with only the compose response, answered it from DynamoDB.
  - 46 items after the question and 78 after the answer; ≈ 43 write units per request; largest item 3.5 KB; TTL set on every item.
- **Spike 2, SSE streaming: passed locally.**
  - The container (`python:3.13-slim` + Lambda Web Adapter 0.9.1) streamed 5 status events about 1 s apart on a read-only root filesystem.
  - The `x-amz-content-sha256` check worked both ways.
  - A CDK spike stack (`spikes/aws/cdk_sse`) synthesizes cleanly: arm64 Lambda, `RESPONSE_STREAM` and `AWS_IAM` Function URL, origin access control for Lambda, and the two CloudFront permissions (`InvokeFunctionUrl` and `InvokeFunction`).
  - `spikes/aws/deploy_spike.sh up|check|destroy` is ready for the AWS run.
- **Spike 3, image size and start-up: measured locally.**
  - Image ≈ 331 MB without the bundle and ≈ 549 MB with a 217 MB stand-in (≈ 104–321 MB compressed).
  - Start to ready: 2.1–2.3 s, of which imports take 1.2–1.6 s and wiring 130–200 ms. Bundle size doesn't matter.
  - SQLite opens fine on a read-only filesystem.
- **Corrections folded into the plan:**
  - the checkpoints table's TTL attribute is `ttl`;
  - the library version (1.2.6) and its constructor;
  - CloudFront needs both Lambda permissions.

### WP1: configuration (`5115bd3`)

- **`packages/core/src/sbc_core/config.py`:**
  - `BotProfile.auth` is now `Literal["cognito", "anonymous"]`, and the broker must use `cognito`.
  - New optional `Portfolio` section: `global_daily_llm_turns`, `demo_user_ids` (set through `SBC_PORTFOLIO__DEMO_USER_IDS`), `demo_rate_limits` (default 15 a minute, 100 a day) and `demo_history_ttl_hours` (24).
  - `EnvSettings`:
    - removed: the Supabase fields, `database_url` and the Upstash fields;
    - added: `aws_region`, `ssm_prefix`, `ddb_table_app`, `ddb_table_audit`, `ddb_table_checkpoints`, `s3_data_bucket`, `cognito_user_pool_id`, `cognito_app_client_id`, `public_base_url`;
    - `langfuse_enabled` is on only when both keys are set.
  - `missing_for()` in dev and prd requires:
    - every bot: its OpenRouter key, the three tables and the bucket;
    - the broker: the Cognito pool and client;
    - the public bot: the Turnstile secret and the session secret;
    - Langfuse is no longer required.
- **Config files:** `config/bots/broker.yaml` says `auth: cognito`; `config/env/dev.yaml` has the portfolio limits (300 turns a day, 15 a minute and 100 a day for the demo account, 24 h history); `config/env/prd.yaml` is commented for AWS.
- **Readers:** `facts.py` and `search.py` open the bundle with `?mode=ro&immutable=1`.
- **`.env.example`:** rewritten for AWS.
- **Tests:** `tests/unit/test_config.py` updated and extended with 9 new tests.

### WP2: the `sbc_aws` package (`b91e8c7`)

- **Package setup:** a new workspace member `packages/aws` (`sbc_aws`), with `boto3>=1.43.64` and `langgraph-checkpoint-aws==1.2.6`. Dev dependencies: `boto3-stubs[dynamodb,s3,ssm]` and `moto[dynamodb,s3,ssm]`. Strict mypy.
- **New import-linter contracts:** runtime layers `api > (agent | aws) > (tools | retrieval) > core`; `sbc_aws` depends only on `sbc_core`; domain code never imports `sbc_aws`; build and runtime separation extended to `sbc_aws`.
- **Modules:**
  - **`secrets.load_ssm_parameters`:** reads parameters under a prefix (paginated, decrypted) into environment variables named by the last path segment. It keeps existing variables unless told to overwrite, skips invalid names, and never logs or returns values.
  - **`ddb`:** `Tables.from_env`, `session_for`, `dynamodb_client`, `ttl_in`, `to_item`/`from_item` (Decimal handling) and the `APP_TTL_ATTRIBUTE` constant (`expires_at`).
  - **`schema`:** `TABLE_SPECS` for app, audit and checkpoints (`PK`/`SK` strings; TTL on `expires_at`, or `ttl` for checkpoints) and an idempotent `create_tables()`.
  - **`checkpoint`:** `make_checkpointer()` (DynamoDBSaver with S3 offload over 350 KB) and `checkpoint_ttl_seconds()` (bot retention or the demo history TTL).
  - **`ratelimit`:**
    - `DynamoRateLimiter.hit()` uses conditional `ADD` counters per window, shortest window first; counters expire an hour after their window.
    - `GlobalCap` counts per UTC day.
    - `windows_for()` and `ip_windows()` build the windows.
    - DynamoDB errors are raised, so the limiter fails closed.
  - **`cache`:**
    - `answer_cache_key()` hashes the PLAN.md §9.5 key fields with the question normalized.
    - `DynamoAnswerCache`: 24 h TTL, 350 KB cap; expired items count as misses; errors count as misses.
    - `QueryEmbeddingCache`: in-memory LRU, then DynamoDB (float16), then the model, with hit counters.
  - **`audit`:** `AuditRecord`, `hash_actor()` (sha256, 32 hex characters), and `DynamoAuditSink` (conditional put, 2-year TTL, no user text).
  - **`killswitch`:** LLM switch per bot and plan quarantine, with a 60 s cache. Writes refresh the writing instance at once.
  - **`sessions`:** thread index `OWNER#<owner>`/`THREAD#<id>` with `updated_at` (titles of at most 80 characters, first title kept), `owns()`, `list_threads()`, and `add_feedback()` with reason codes only.
  - **`pdfcache`:** `S3PdfCache` keyed by plan and sha256 (`pdf-cache/<plan>-<sha16>.pdf`), hash-checked on write and read, SSE-S3.
- **Tests:**
  - 37 moto tests in `tests/unit/aws/`, including resuming a clarification on a second saver instance.
  - `tests/integration/test_aws_dynamodb_local.py`: 100 concurrent hits on a limit of 25 let exactly 25 through, and 120 concurrent turns on a cap of 40 let exactly 40 through.

### WP3: bundle PDF sources (`f38e0f2`)

- **Builder** (`packages/sbc_index/src/sbc_index/bundle.py`):
  - New `plan_source` table: fetch method, portal, catalog URL, state, market unit, catalog plan name, plan type, coverage period, network, language, plan status, `doc_sys_id`, `dps_unique_id`, brand, source system, `pdf_sha256` and `pdf_bytes`.
  - `load_manifest()` validates the manifest columns and keeps only downloaded rows with a valid sha256.
  - `build_bundle(manifest_csv=...)` records row, preview-only and missing counts in the metadata.
  - A `--release` build needs the manifest and refuses any plan missing from it.
  - `sbc-build-bundle --manifest` defaults to `data/catalog/sbc_manifest_<year>.csv`.
- **Core:**
  - `PdfSource` type and `FactsReader.pdf_source()`.
  - `SqliteFactsReader(path, pdf_proxy_base=None)`: with a base, preview-only plans' `source_url` becomes `{base}{url-encoded plan_key}.pdf`. Without one, nothing changes.
  - Old bundles without the table still open.
- **Docs:** `data/README.md` describes the manifest step and bundle `2027.2`.
- **Tests:** 9 new ones; the fixture bundle now has a manifest (Silver is preview-only, Gold has a deep link).

### WP4: API, SSE and agent streaming (`135deea`)

- **`sbc_agent.graph`:** `Agent.stream()` yields `Progress(node)` for each finished node, then the same `Reply` as `ask`/`choose`, interrupts included.
- **`sbc_api`:**
  - **App:** `app.create_app()` is the uvicorn factory. Request-ID middleware reads `x-amzn-request-id` and returns `x-request-id`; JSON access logs.
  - **`deps.build_state()`:**
    - loads SSM secrets (outside local runs), then settings for both bots;
    - opens the reader with `pdf_proxy_base` and the search index (BM25-only locally if the bundle has no embeddings; dev and prd require them);
    - picks the stores: DynamoDB and S3 when the tables are set, otherwise in-process;
    - builds one agent per bot with its own OpenRouter key and checkpointer. A bot without a key locally reports 503.
  - **`ports` and `memory`:** protocols and in-process implementations.
  - **`sse.stream_turn()`:** worker thread, `thread`, `status`, then `answer`/`clarify`/`error`, then `done`; 10 s keepalives, 55 s timeout, generic error text.
  - **Routes:** `GET /api/healthz` (`?deep=1`: bundle, `plan_source`, search mode, stores, bot readiness, init time), `POST /api/{bot}/chat` and `/chat/choose`, `GET /api/{bot}/plans/search` and `/plans/{plan_key}` (scope-filtered; the public bot needs a state; out-of-scope plans are 404).
  - **Logging:** JSON lines without user text.
- **Docs:** README "Run the API locally".
- **Tests:** 12 API tests and 2 stream tests. Integration test `tests/integration/test_api_dynamodb_local.py`: clarify, then choose, across two app instances sharing only the DynamoDB tables.
- **Checked by hand:** a real `uvicorn` start (32 ms init on the fixture bundle).

### WP5: authentication (`db6b62a`)

- **Broker tokens** (`auth/cognito.py`): `CognitoVerifier` checks RS256 through the pool's JWKS (`PyJWKClient`: 1 h key cache, 5 s timeout). It checks the issuer, `token_use == "access"`, `client_id`, expiry (30 s leeway), required claims, and that groups are a list.
- **Public sessions** (`auth/anon.py`): `SessionTokens` are HS256 with issuer `sbc-api` and audience `sbc-public`, last 24 h, and need a secret of at least 32 bytes. They carry a random session ID and the visitor's state, which wins over any request value.
- **Bot check** (`auth/turnstile.py`): Cloudflare siteverify with a 5 s timeout; outages are reported as such.
- **Routing rules** (`auth/principal.py`):
  - `BearerAuthenticator` sends each token only to its own bot's verifier. Broker tokens need `broker` or `admin` (else 403). The demo account loses `admin`.
  - `admin_principal` guards the admin routes; missing or bad tokens get 401 with `WWW-Authenticate: Bearer`.
- **Session route** (`routes/session.py`): `POST /api/public/session` uses the viewer IP from `CloudFront-Viewer-Address`; a failed check is 403 and an outage 503.
- **Local runs:** the Turnstile check is skipped without a secret, and a per-process session secret is used. Elsewhere both are required.
- **Docs:** `.env.example` lists Cloudflare's always-pass test keys.
- **Dependency:** `pyjwt[crypto]` 2.15.1.
- **Tests:** 21 with a generated RSA key behind a fake JWKS and a mock Turnstile transport, covering 10 kinds of bad token.

### WP6: guards (`44ca2fc`)

- **`sbc_api/guards.py`, before a turn** (no model call), in this order:
  1. Rate limits: the profile's, the demo account's, and per IP for the public bot.
  2. Thread ownership: 404 otherwise. Threads are now always created by the server.
  3. Kill switch: a fixed message.
  4. First-message exact-answer cache: no model call, doesn't count toward the cap.
  5. Global daily cap.

  Limits reply with an SSE `limit` event (scope `user`, `session`, `ip` or `global`).
- **After a turn:** answers about quarantined plans are withheld, first-message answers are cached (not clarifications), and an audit record is written (`audit_failed` is logged on error).
- **`Agent.remember()`:** records a cached turn in the new thread's history, so a follow-up keeps its plan.
- **Demo account:** the broker bot has a second agent for it, with a 24 h checkpoint TTL.
- **Query-embedding cache:** wraps `TextSearcher.embed_query` when DynamoDB is configured.
- **Routes:**
  - `POST /api/{bot}/feedback`: own threads only, reason codes only.
  - `GET /api/broker/threads`: empty for the demo account.
  - `POST /api/admin/kill-switch` and `/api/admin/quarantine`: `admin` only, never the demo account, audited.
- **Tests:** 17 in `tests/unit/api/test_guards.py`. The DynamoDB Local integration test still passes.

### WP7: SBC PDF proxy (`2a29bde`)

- **`sbc_api/portal_preview.py`:** `preview_form()` mirrors `sbc_ingest.portal._preview_post`, checked field for field by a test. `PortalPreviewClient.fetch()` opens a fresh cookie session each time: `GET /dps/`, `POST /dps/searchPage`, then `POST /dps/search/previewDocument`, with a 30 s timeout, a 20 MB cap and a `%PDF` check.
- **`routes/sbc_pdf.py`:**
  - Public PDFs get a 302 to the insurer.
  - Preview-only PDFs: 30 per IP per hour (429 with `Retry-After`), then the S3 cache, otherwise the portal fetch.
  - The sha256 must match the bundle, or the request gets a 502 and the mismatch is logged as `pdf_hash_mismatch`.
  - Successful responses are cached and served inline with `Cache-Control: public, max-age=3600`.
- **Fixture change:** `make_bundle(rows=...)` can take custom manifest rows.
- **Tests:** 9 in `tests/unit/api/test_sbc_pdf.py`.

### D: documentation sync (`af59ee0`)

- **PLAN.md, revision 3:**
  - Status line and a "Revision 3: AWS deployment track" change block that maps each replaced service.
  - Updated sections: TL;DR stack, §1.3 decisions (marked superseded), §2.2 auth, the §3.1 diagram, §3.5.1 checkpointer, §3.5.6 fallback, §4 tech-stack rows and launch costs, §7 tracing, dashboards, alerts, runbook and backups, §8 AuthN, AuthZ, secrets and retention, §9 environments, container, Lambda settings, release pipeline, rollback and caching, the §10 tree, the §11 roadmap note, §12 risks and Appendix B.
  - Models, the bundle, retrieval, the verifier and the evals are unchanged.
- **README.md:** architecture diagram, stack, layout (`aws/`, `spikes/`, `docs/aws/`), import layers, setup hint and status table.
- **TODO.md:** pointer to these files; superseded and finished items marked.
- **Other docs:**
  - `infra/README.md` and `apps/README.md` rewritten for AWS.
  - `docs/agent-graph.md`: DynamoDBSaver, `stream()` and `remember()`.
  - `docs/answer-path.md`: the preview-only link gap is now closed.
  - `docs/README.md`: index of `docs/aws/`.
  - `config/base.yaml`: the comment on where secrets come from.
- **`docs/aws/MIGRATION_PLAN.md`:** status notes on risks 1–7.
- **New:** `docs/aws/TASKS_DONE.md` (this file) and `docs/aws/TODO.md`.

### WP8: containers and the local stack (`a2ec16b`)

- **`infra/docker/api.Dockerfile`:**
  - Multi-stage on `python:3.13-slim`; uv 0.11.32 runs `uv sync --frozen --no-dev --package sbc-api --no-editable` into `/app/.venv`, so only runtime packages are installed and ingest/extract/index stay out.
  - Lambda Web Adapter 0.9.1 at `/opt/extensions/lambda-adapter`; `config/`; the bundle from the named `bundle` build context (`BUNDLE_FILE` build arg) at `/opt/bundle/facts.sqlite`.
  - Environment: `AWS_LWA_INVOKE_MODE=response_stream`, `AWS_LWA_READINESS_CHECK_PATH=/api/healthz`, `PORT=8080`, `PYTHONDONTWRITEBYTECODE=1`.
  - Non-root uid 10001; `CMD uvicorn --factory sbc_api.app:create_app --workers 1 --no-access-log`.
  - An optional `ca` build secret for TLS-inspecting proxies, and a uv cache mount.
- **`infra/docker/build.Dockerfile`:**
  - `python:3.13-slim` + `poppler-utils`; every workspace package; `config/` and `evals/` (golden sets) in `/app`.
  - Works in `/work` (`SBC_PROJECT_ROOT`) as the non-root `builder`; entrypoint `build-pipeline`, default `full`.
- **`infra/docker/build-pipeline.sh`:**
  - Modes `full` (catalog → download → manifest → early sync up → convert new PDFs → verify → bundle `--release` → embed → retrieval eval → sync up) and `bundle` (bundle → embed → eval from the Markdown in S3).
  - Environment: `S3_DATA_BUCKET`, `BUNDLE_VERSION`, `SBC_YEAR`, `OPENROUTER_API_KEY_BROKER`, `FORCE_CONVERT`, `EVAL_SETUP`.
  - Links `config/` into `/work`, since the CLIs read config relative to the project root.
  - Uploads the bundle and its eval report to `bundles/<version>/`, and the embedding cache to `bundles/cache/`.
- **`infra/docker/s3_sync.py`:**
  - Up and down by sha256 (stored as object metadata, so a same-size change is still noticed); SSE-S3; never deletes.
  - Written instead of the AWS CLI: v1 pins an exact botocore that conflicts with the locked boto3.
- **`infra/docker/compose.yaml`:**
  - `dynamodb` (DynamoDB Local 2.6.1).
  - `tables`: the API image running `sbc-create-tables`.
  - `api`: read-only root and `/tmp` tmpfs; `.env` optional; `AWS_ENDPOINT_URL_DYNAMODB`, the `DDB_TABLE_*` variables and `APP_ENV=local`.
- **`sbc_aws.schema.main`** is the new `sbc-create-tables` console script, which waits for the endpoint (default 30 s).
- **`infra/docker/fixture_bundle.py`** writes the synthetic two-plan bundle; **`infra/docker/smoke.sh`** runs the image read-only and checks health, deep health, plan search, prompts and taxonomy, and the non-root user, then prints size, start-up time and memory.
- **Ignore files:** per-Dockerfile `.dockerignore` files keep `data/`, `.venv/`, `bundles/` and `spikes/` out of the build contexts.
- **CI:** an `image` job in `.github/workflows/ci.yml` builds the API image on the fixture bundle and runs `smoke.sh`.
- **Tests:** 5 for `s3_sync.py` in `tests/unit/test_s3_sync.py` (moto).
- **Verified here:**
  - The API image builds (36 s) and `smoke.sh` passes.
  - The compose stack: tables created, `"stores": "dynamodb"`, a rate-limit counter written to DynamoDB Local, a public session issued.
  - The build image builds (39 s) with `pdftotext` 25.03, every pipeline CLI, the eval harness and the golden sets, and it refuses to run without `S3_DATA_BUCKET`.
- **Docs:** `docs/aws/RUNBOOK.md` (sections 1–3 filled), README, `infra/README.md`, the docs index, MIGRATION_PLAN (WP8 status, the S3 layout, the bundle path for deploys), the DEPLOYMENT_ANALYSIS note and TODO.md.

### B: branch policy (`8adb851`)

- PR #14 (`main_AWS` → `main`) was closed without merging: `main_AWS` is the long-lived main branch of the AWS implementation. `main` is unchanged at `e7ec0e7`.
- CI (`.github/workflows/ci.yml`) now runs on pushes to `main_AWS` as well as `main`.

### WP9: CDK stacks and operator scripts (this commit)

- **`infra/cdk/`:** its own uv project (`aws-cdk-lib` 2.275.0, CDK CLI 2.1147.0 through `npx`), with `cdk.json` context (`envName`, `apiImageTag`, `buildImageTag`, `alarmEmail`, `monthlyBudgetUsd`, `demoUserIds`, `warmer`, `githubOidcProviderArn`, `githubRepository`, `githubBranch`), `sbc_infra/config.py` and `sbc_infra/tables.py`.
- **`SbcFoundation-<env>`:**
  - **`Data`:** a private, SSE-S3, SSL-only, retained data bucket. Lifecycle: `pdf-cache/` 1 day, `checkpoints/` 400 days, `data/raw/` to Glacier IR after 30 days, incomplete uploads 2 days. Three on-demand tables with AWS-managed encryption, TTL and `RETAIN`.
  - **`Auth`:** a Cognito pool (Essentials; self sign-up off; MFA optional with TOTP; 12-character passwords; no account recovery). An SPA client: SRP only, no secret, no OAuth (so no admin scope), 1 h access token, 12 h refresh, token revocation. Groups `broker` and `admin`.
  - **`Registry`:** ECR `sbc-<env>-api` (keep 3) and `sbc-<env>-build` (keep 2), scanned on push.
  - **`CostGuards`:** a monthly budget (50/80/100% actual, 100% forecast) and Cost Anomaly Detection (daily, ≥ $2), when `alarmEmail` is set.
- **`SbcApp-<env>`:**
  - **`Api`:** Lambda from ECR (arm64, 2,048 MB, 60 s, 1 GB `/tmp`, 14-day logs) with the AWS environment variables; the `live` alias; a Function URL (`AWS_IAM`, `RESPONSE_STREAM`).
    - IAM: read/write on app and checkpoints, `PutItem` only on audit, S3 `pdf-cache/*` and `checkpoints/*`, `ssm:GetParametersByPath` on `/sbc/<env>`, `kms:Decrypt` only through SSM.
  - **`Web`:**
    - A private web bucket and a security-headers policy (CSP allowing Cognito and Turnstile, HSTS, nosniff, `DENY` frames, referrer policy).
    - An `/api/*` origin request policy (an allow-list of 5 headers) and a CloudFront Function rewriting SPA routes.
    - The distribution: S3 through OAC by default, `/api/*` to the Function URL through Lambda OAC (60 s read timeout, `CachingDisabled`, all methods), PriceClass_100, HTTP/2 and 3, default domain.
  - **`Build`:** an ECS cluster (L1, Fargate + Spot, no VPC); the task definition (arm64, 4 vCPU, 8 GB, 50 GB) with environment variables, the OpenRouter key as an ECS secret from SSM, and default command `bundle`; data-bucket access.
  - **`Ops`:** the SNS topic (+ email), 8 alarms (3 Lambda + 5 log metric filters), and the optional warmer.
  - **`Cicd`:** a GitHub OIDC provider (or an existing one), and the `sbc-<env>-github-deploy` role, trusted only for `repo:…:ref:refs/heads/main_AWS`. Permissions: ECR push/pull, Lambda update/publish/alias, web bucket read/write/delete, data bucket `bundles/*` read, CloudFront invalidation, ECS `RunTask` on the build task in the build cluster, `PassRole` to ECS tasks, subnet lookup.
- **API changes:**
  - **Tokens:** `sbc_api.auth.principal` reads `X-Sbc-Authorization` first (CloudFront's signing replaces `Authorization`). Test added.
  - **`POST /events`:** answers 204 for warmer pings arriving through the Lambda Web Adapter's pass-through path. Test added.
- **Scripts** (`scripts/aws/`, `_common.sh` shared; all pass shellcheck): `bootstrap.sh`, `deploy.sh`, `put-secrets.sh`, `upload-bundle.sh`, `push-images.sh`, `seed-users.sh`, `rotate-demo-password.sh`, `run-build.sh`. None has been run against AWS yet.
- **Tests:**
  - 12 template tests (`infra/cdk/tests/test_stack.py`).
  - `tests/unit/test_infra_tables.py` (CDK tables equal `sbc_aws.schema`).
  - 2 API tests (the token header, the warmer route).
- **CI:** a `cdk` job runs the template tests, `cdk synth` and shellcheck.
- **Docs:** `infra/cdk/README.md`; RUNBOOK §4–§8 (first deployment, deploy and rollback, secrets and the demo password, costs and alarms, teardown outline); MIGRATION_PLAN (WP9 as built, risk 3a, `PUBLIC_BASE_URL`); DEPLOYMENT_ANALYSIS (forwarded headers); SPIKES (§4a, the `Authorization` finding); PLAN.md §9.3; README; `infra/README.md`; TODO.

---

## 4. Measurements recorded so far

| What | Value | Where |
|---|---|---|
| Checkpoint writes per request | ≈ 43 write units (≈ $0.05 per 1,000 turns) | Spike 1 |
| Largest checkpoint item | ≈ 3.5 KB (offload threshold 350 KB) | Spike 1 |
| Image size | ≈ 331 MB without bundle, ≈ 549 MB with a 217 MB stand-in (uncompressed) | Spike 3 |
| Container start to ready | 2.1–2.3 s locally (imports 1.2–1.6 s) | Spike 3 |
| API init on the fixture bundle | 32 ms (uvicorn, in-process stores) | WP4 |
| API image (fixture bundle) | ≈ 350 MB uncompressed, ≈ 109 MB compressed; ≈ 570 MB expected with the real bundle | WP8 |
| Image start to ready | 2.6–2.8 s warm, 7.1 s cold first start; ≈ 2.3 s of imports; `build_state` 55 ms (174 ms with DynamoDB) | WP8 |
| Memory after start | ≈ 89 MiB | WP8 |
| Offline build image | ≈ 681 MB | WP8 |
| Rate limiter under concurrency | Exactly the limit, at 16 threads | WP2 integration |
| Live LLM cost (from `main`, before the AWS track) | ≈ $0.003 per fast-path turn, $0.02–0.04 per agent turn | TODO.md, Phase 1 |
