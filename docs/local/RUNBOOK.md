# Local runbook: the whole application on your machine

How to run the full stack locally with stand-ins for the AWS services. The plan, the roadmap and what local
can and can't prove are in [LOCAL_PLAN.md](LOCAL_PLAN.md). This branch, `main_local`, is for local runs only and is
never merged into `main_AWS`.

## 1. Prerequisites

- Docker Desktop, running (4 GB of memory is plenty). Compose 2.23 or newer.
- [uv](https://docs.astral.sh/uv/) and `uv sync` done in the repository.
- A facts bundle in `bundles/` (`FACTS_BUNDLE_PATH` in `infra/local/stack.env`; today `facts-2027.2.sqlite`,
  built by `sbc-build-bundle --version 2027.2 --release` and `sbc-embed-bundle`, docs/local/LOCAL_PLAN.md L2).
- `.env` with `OPENROUTER_API_KEY_BROKER` and `OPENROUTER_API_KEY_PUBLIC` for answers (keys with credit).
- Node.js for the frontends: 20.10 works, 22 is recommended (Node 20 is end of life).

## 2. Start and stop

```bash
make up      # or scripts/local/up.sh: stand-ins, edge, tables
make api     # or scripts/local/api.sh: the API natively with reload, in its own terminal
make web     # or scripts/local/web.sh: build the apps with the local settings
make status
```

Open http://localhost:8088. Sign in to the Broker Assistant as one of the users in section 5; the Plan Shopper
asks for a state and shows Cloudflare's test widget ("for testing only"), which always passes. Stop with `make down` (keeps all state) or `make wipe` (drops it).

To run the API image Lambda runs instead of the native API:

```bash
scripts/local/down.sh && scripts/local/up.sh --image       # builds sbc-api:local with the bundle baked in
SBC_BUNDLE_FILE=facts-2027.2.sqlite scripts/local/up.sh --image   # another file from bundles/
```

## 3. What runs where

| Service | Port | What | State |
|---|---|---|---|
| `edge` (Caddy) | 8088 | CloudFront stand-in: the built frontends (`apps/site/`) at `/`, `/broker/`, `/shopper/`; the API at `/api/*` | — |
| API | 8080 | `scripts/local/api.sh` (native) or the `api` container (`--image`) | — |
| `dynamodb` (DynamoDB Local) | 8000 | Tables `sbc-local-app`, `sbc-local-audit`, `sbc-local-checkpoints` | Named volume, survives `down` |
| `s3` (Adobe S3Mock) | 9090 | Bucket `sbc-local-data`: PDF cache, checkpoint offload | Named volume, survives `down` |
| `aws` (moto server) | 5050 | SSM Parameter Store (`/sbc/local/`) and the Cognito user pool `sbc-local-brokers` | In memory; the bootstrap recreates it on every `up`, with the same IDs |

All ports listen on 127.0.0.1 only. Override any of them with `SBC_PORT_EDGE`, `SBC_PORT_API`,
`SBC_PORT_DYNAMODB`, `SBC_PORT_S3` or `SBC_PORT_MOTO` (5000 is avoided: macOS AirPlay Receiver uses it).

The edge behaves like CloudFront where it matters to the apps: it drops `Authorization` (the apps must send
`X-Sbc-Authorization`), sets `CloudFront-Viewer-Address` from the client socket (the per-IP limits read it),
never caches `/api/*`, rewrites the apps' client-side routes to their `index.html`, and sends the same security
headers. Only `/api/*` reaches the API through it; on AWS the warmer calls `POST /events` on the function
directly, so locally call it on :8080.

## 4. Settings

| File | What | Committed |
|---|---|---|
| `infra/local/stack.env` | Endpoints, table and bucket names, dummy AWS credentials, the bundle path, and the portfolio limits (a global daily cap of 200 turns, demo limits of 5 a minute and 30 a day) as `SBC_PORTFOLIO__*` | Yes (no secrets) |
| `.env` | Secrets: OpenRouter keys, Langfuse keys. The bootstrap copies them into the local Parameter Store | No |
| `.local/secrets.env` | The session secret, generated once (mode 600); kept by `make wipe` | No |
| `.local/users.env` | The local users' passwords, generated once (mode 600); kept by `make wipe` | No |
| `.local/runtime.env` | Written by the bootstrap on every start: Cognito pool and client IDs, `COGNITO_JWKS_URL`, the demo user's `sub` (`SBC_PORTFOLIO__DEMO_USER_IDS`) | No |
| `.local/turnstile` | `pass` or `block`: which Turnstile test secret the bootstrap uses | No |
| `config/env/local.yaml` | Behavior for every `APP_ENV=local` run (the portfolio limits stay off there) | Yes |

`stack.env` supplies defaults: a variable already set in your shell wins, for example
`FACTS_BUNDLE_PATH=bundles/facts-2027.2-local.sqlite scripts/local/api.sh`.

The AWS SDK reaches the stand-ins through `AWS_ENDPOINT_URL_<SERVICE>` variables, so the API's AWS code is the
same code that runs on AWS.

**Secrets travel the AWS way.** `bootstrap.py` mirrors the secrets into the local Parameter Store under
`/sbc/local/`, as `scripts/aws/put-secrets.sh` does on AWS: from `.env`, `OPENROUTER_API_KEY_BROKER`,
`OPENROUTER_API_KEY_PUBLIC`, `TURNSTILE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`LANGFUSE_BASE_URL` and `LANGFUSE_HOST` when set; and `SESSION_JWT_SECRET` from `.local/secrets.env`. It prints names, never values, and
removes parameters `.env` no longer has. The API loads `/sbc/local/` at start-up (the start-up log line shows
`"secrets_loaded"`). The API image has no `.env` at all, so there the keys come only from Parameter Store; the
native API also reads `.env` itself, but the Parameter Store values win.

**After changing `.env`,** run `scripts/local/bootstrap.sh` (or `make up`), then restart the API.

## 5. Sign-in, users and Turnstile

Every API call is authenticated as on AWS: the local API checks Cognito access tokens against the stand-in's keys,
and public sessions need a Turnstile check. (Plain local runs without the stack still use the developer
principal.)

| User | Groups | Use |
|---|---|---|
| `admin` | `broker`, `admin` | The admin routes (kill switches, quarantine) |
| `broker` | `broker` | An ordinary broker |
| `demo-broker` | `broker` | The shared demo account: the demo limits (5 turns a minute, 30 a day), 24-hour history, never admin |

Passwords are in `.local/users.env`. For `curl`, get an access token with `scripts/local/token.sh <user>` and send
it in `X-Sbc-Authorization` (the edge drops `Authorization`, as CloudFront does):

```bash
curl -s -H "X-Sbc-Authorization: Bearer $(scripts/local/token.sh broker)" localhost:8088/api/broker/threads
```

**Public sessions:** the bootstrap stores Cloudflare's Turnstile test secret, which the API checks with
Cloudflare's real `siteverify`. With the `pass` secret every token passes; with `block`, every token fails:

```bash
curl -s -X POST localhost:8088/api/public/session -H 'content-type: application/json' \
  -d '{"turnstile_token":"XXXX.DUMMY.TOKEN.XXXX","state":"OH"}'            # {"token": "..."}
scripts/local/bootstrap.sh --turnstile block    # then restart the API; sessions now get 403
scripts/local/bootstrap.sh --turnstile pass     # back (the choice is remembered in .local/turnstile)
```

A `TURNSTILE_SECRET_KEY` in `.env` replaces the test secret.

**Differences from AWS** (docs/local/LOCAL_PLAN.md section 7): the broker app signs in with `USER_PASSWORD_AUTH`
instead of SRP when `VITE_COGNITO_ENDPOINT` is set, because moto can't finish an SRP sign-in; there is no user
with a temporary password, because moto's "choose a new password" challenge doesn't work with the app's Cognito
library; and moto accepts any TOTP code. The local app client also allows `ADMIN_USER_PASSWORD_AUTH`, for
`token.sh`; the AWS client allows SRP only.

## 6. Scripts

| Script | Make | What |
|---|---|---|
| `scripts/local/up.sh [--image]` | `make up`, `make up-image` | Start the stack, wait for it, bootstrap, print status |
| `scripts/local/api.sh` (`LLM_MODE=record\|replay` optional) | `make api`, `make api-record`, `make api-replay` | The API natively on :8080 with reload; its JSON log lines go to the console and `.local/logs/api.jsonl` (`LOG_FILE`) |
| `scripts/local/find-request.sh <id>` | — | Every log line of one request, by its request ID (from `.local/logs/api.jsonl` and the API container) |
| `scripts/local/bootstrap.sh [--turnstile pass\|block]` | — | Create the tables, the bucket, the user pool and users; mirror the secrets into the local Parameter Store; write `.local/runtime.env` (idempotent; `up.sh` runs it). It refuses to run unless every AWS endpoint points at a stand-in |
| `scripts/local/token.sh <user>` | — | Print an access token for `admin`, `broker` or `demo-broker` |
| `scripts/local/build.sh seed \| run <version> \| fetch <version>` | — | The offline build image against the local bucket (section 10) |
| `scripts/local/smoke.sh [--record\|--allow-live]` | `make smoke` | The end-to-end smoke test: 43 checks through the edge (section 7) |
| `scripts/local/eval-http.sh [args]` | `make eval-http` | Golden items through the API over HTTP (section 7) |
| `scripts/local/web.sh [--dev]` | `make web`, `make web-dev` | Build the apps with the local `VITE_*` values into `apps/site/` (served by the edge) and write `/landing/demo.json` for `demo-broker`; `--dev` runs the three Vite dev servers instead (http://localhost:5173, `/api` straight to :8080) |
| `scripts/local/status.sh` | `make status` | Which services answer |
| `scripts/local/logs.sh [service…]` | `make logs` | Follow the containers' logs |
| `scripts/local/down.sh [--wipe]` | `make down`, `make wipe` | Stop; `--wipe` drops the volumes and `.local/runtime.env` |

## 7. Checks

```bash
curl -s 'localhost:8088/api/healthz?deep=1'    # "stores": "dynamodb"; both bots "ready" when keys are set
curl -s -H "X-Sbc-Authorization: Bearer $(scripts/local/token.sh broker)" 'localhost:8088/api/broker/plans/search?q=blue&limit=2'
grep '"event": "init"' .local/logs/api.jsonl | tail -1    # "secrets_loaded": 2 or more
DDB_LOCAL_ENDPOINT=http://localhost:8000 uv run pytest -m integration tests/integration   # needs no model calls
```

The integration tests use their own tables in the stack's DynamoDB Local, with scripted models.

**The smoke test** checks every route and guard through the edge (43 checks; `scripts/local/smoke.py` lists
them). It costs nothing with the API in replay mode, and refuses a live API unless given `--allow-live`:

```bash
make api-record          # once, after a change to prompts, models, the bundle or behavior settings
scripts/local/smoke.sh --record                      # about $0.03 of model calls, saved
make api-replay          # from then on
make smoke                                            # 43 passed, 0 failed, about 6 s, $0
uv run pytest -m integration tests/integration/local  # the same, as a test (skipped unless the stack is up in replay)
```

It clears the answer cache first (so the questions take the model path) and puts back the kill switch and the
quarantine it changes. The demo account allows 5 turns a minute, so wait a minute between two runs.

**The HTTP eval** runs golden items through the API the way the apps do, scored like the in-process generation
eval (`evals/README.md`):

```bash
scripts/local/eval-http.sh --sample 3          # in record mode the first time (about $0.01 an item), then replay
make eval-http                                 # --sample 30
```

Reports go to `.local/evals/`. Over HTTP, ungrounded numbers and tool selection show as not scored (–).

## 8. Logs, request IDs and tracing

Every API response carries `x-request-id`; the apps show it under "Report a problem", and chat streams send it in
their first (`thread`) event. Each request writes one JSON `request` line, each turn a `turn` line (path, model
calls, cost, fallback reason), and each error an `ERROR` line with its traceback, all with that `request_id`:

```bash
scripts/local/find-request.sh <request-id>
jq -c 'select(.level == "ERROR")' .local/logs/api.jsonl | tail -5          # recent errors
jq -c 'select(.event == "turn") | {request_id, bot, path, cost_usd}' .local/logs/api.jsonl | tail -5
```

An unhandled error answers 500 with `{"detail": …, "request_id": …}`. On AWS the same lines go to CloudWatch, and
the `ApiErrors` alarm emails on any `ERROR` line.

**Langfuse Cloud** (optional): put `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` (as
Langfuse shows them with the project's keys; `https://us.cloud.langfuse.com` for a US-region project, the EU region
by default) in `.env`, run `scripts/local/bootstrap.sh` and restart the API; deep health then shows
`"tracing": "langfuse"`, and each turn sends one redacted trace (the turn, its model and tool calls, cost, and the
request ID in its metadata).

## 9. Record and replay model calls (no OpenRouter cost)

Repeated runs (the smoke test, UI work, demos) don't need to pay for the same model calls again:

```bash
make api-record     # LLM_MODE=record: calls OpenRouter and saves every call to .local/llm-recordings/
make api-replay     # LLM_MODE=replay: answers from those files; no network, no key needed, cost 0
LLM_MODE=replay scripts/local/up.sh --image     # the same for the API image (the folder is mounted)
```

Deep health shows `"llm": "live" | "record" | "replay"`, and each replayed answer's details show `cost_usd` 0.

- **What replays:** the same question in the same conversation state sends exactly the same requests, so a
  recorded conversation, follow-ups included, replays identically. A changed prompt, model, bundle or behavior
  setting changes the requests: record again.
- **A request that wasn't recorded** fails like an OpenRouter outage: the answer is the safe "I couldn't answer
  this…" message, and the `turn` log line says `model_unavailable: replay: no recorded chat call …`.
- **The answer cache comes first.** A first question already in the answer cache is served from DynamoDB without
  any model call, in every mode. Clear it (or `make wipe`) when you want to exercise the model path.
- **Local only.** The API refuses `record` and `replay` outside `APP_ENV=local`. The recordings contain SBC text,
  so they stay in `.local/` (git-ignored); delete the folder to start over.

## 10. The offline build against the local bucket

The build image (`infra/docker/build.Dockerfile`, `build-pipeline`) runs here the way `scripts/aws/run-build.sh`
runs it as an ECS task, against `s3://sbc-local-data` in S3Mock:

```bash
scripts/local/build.sh seed                  # once: data/markdown, catalog, reference and the embedding cache (~9 min)
scripts/local/build.sh run 2027.2-local      # build-pipeline bundle in the image (~25 min, mostly S3Mock syncs)
scripts/local/build.sh fetch 2027.2-local    # bundles/facts-2027.2-local.sqlite
FACTS_BUNDLE_PATH=bundles/facts-2027.2-local.sqlite scripts/local/api.sh
SBC_BUNDLE_FILE=facts-2027.2-local.sqlite scripts/local/up.sh --image      # or baked into the API image
```

- The eval runs `bm25_only` (free) unless you set `EVAL_SETUP=full` (about $0.25 of rerank).
- Embeddings come from the cache in the bucket; only chunks with new text cost anything.
- The OpenRouter key goes from the local Parameter Store into the container by name, never on a command line.
- The `full` mode (catalog refresh, PDF downloads) isn't offered locally: it needs the 6 GB of PDFs and hours of
  portal downloads. Build natively instead (`sbc-portal`, `sbc-to-markdown`, `sbc-verify`; data/README.md).

## 11. Troubleshooting

- **`Docker isn't running`:** start Docker Desktop and wait for it to say it's running.
- **A port is taken:** `lsof -i :8088` (or the port in question), or set the matching `SBC_PORT_*`.
- **A bot shows `unavailable: OPENROUTER_API_KEY_… is not set`:** put the key in `.env`, run
  `scripts/local/bootstrap.sh`, and restart the API.
- **Every answer is "I couldn't answer this from the SBC…":** look at the `turn` line in `.local/logs/api.jsonl`;
  `fallback: model_unavailable … HTTP 403: Key limit exceeded` means the OpenRouter key is out of credit.
- **A 500:** its body has the `request_id`; `scripts/local/find-request.sh <id>` shows the error line with the
  traceback. A stopped stand-in (`make status`) is the usual cause: the API fails closed when DynamoDB is
  unreachable.
- **401 on every API call:** send a token (`scripts/local/token.sh`) in `X-Sbc-Authorization`, not `Authorization`.
- **The broker app at :8088 can't sign in, or the site shows "The frontends are not built yet":** run
  `make web`. Also after `npm run check` in `apps/` (CI's frontend check), which rebuilds `apps/site` without
  the local settings. Rebuild after `make wipe` (the build carries the pool and client IDs; they only change if moto's
  seed or the users change).
- **Signed out after a reload:** by design, the broker app keeps its tokens in memory only, as on AWS.
- **The Turnstile box is empty for a moment:** the widget loads from Cloudflare; it needs internet access.
- **Start over:** `make wipe && make up`. The passwords and the session secret in `.local/` are kept; delete
  `.local/users.env` too for new passwords.
