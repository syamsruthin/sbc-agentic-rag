# Local runbook: the whole application on your machine

How to run the full stack locally with stand-ins for the AWS services. The plan, the roadmap and what local
can and can't prove are in [LOCAL_PLAN.md](LOCAL_PLAN.md). This runbook grows with each phase; sections marked
*(coming in Lx)* aren't built yet.

## 1. Prerequisites

- Docker Desktop, running (4 GB of memory is plenty). Compose 2.23 or newer.
- [uv](https://docs.astral.sh/uv/) and `uv sync` done in the repository.
- A facts bundle in `bundles/` (`FACTS_BUNDLE_PATH` in `infra/local/stack.env`; today `facts-2027.1.sqlite`).
- `.env` with `OPENROUTER_API_KEY_BROKER` and `OPENROUTER_API_KEY_PUBLIC` for answers (keys with credit).
- Node 22 for the frontends *(used from L5)*.

## 2. Start and stop

```bash
make up      # or scripts/local/up.sh: stand-ins, edge, tables
make api     # or scripts/local/api.sh: the API natively with reload, in its own terminal
make status
```

Open http://localhost:8088. Stop with `make down` (keeps all state) or `make wipe` (drops it).

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
| `aws` (moto server) | 5050 | SSM Parameter Store (`/sbc/local/`) and, from L4, the Cognito user pool | In memory; recreated by the bootstrap on every `up` |

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
| `.local/runtime.env` | Values the bootstrap generates *(from L4: Cognito IDs, the demo user's `sub`)* | No |
| `config/env/local.yaml` | Behavior for every `APP_ENV=local` run (the portfolio limits stay off there) | Yes |

The AWS SDK reaches the stand-ins through `AWS_ENDPOINT_URL_<SERVICE>` variables, so the API's AWS code is the
same code that runs on AWS.

**Secrets travel the AWS way.** `bootstrap.py` mirrors the secrets into the local Parameter Store under
`/sbc/local/`, as `scripts/aws/put-secrets.sh` does on AWS: from `.env`, `OPENROUTER_API_KEY_BROKER`,
`OPENROUTER_API_KEY_PUBLIC`, `TURNSTILE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and
`LANGFUSE_HOST` when set; and `SESSION_JWT_SECRET` from `.local/secrets.env`. It prints names, never values, and
removes parameters `.env` no longer has. The API loads `/sbc/local/` at start-up (the start-up log line shows
`"secrets_loaded"`). The API image has no `.env` at all, so there the keys come only from Parameter Store; the
native API also reads `.env` itself, but the Parameter Store values win.

**After changing `.env`,** run `scripts/local/bootstrap.sh` (or `make up`), then restart the API.

## 5. Scripts

| Script | Make | What |
|---|---|---|
| `scripts/local/up.sh [--image]` | `make up`, `make up-image` | Start the stack, wait for it, bootstrap, print status |
| `scripts/local/api.sh` | `make api` | The API natively on :8080 with reload; logs to the console and `.local/logs/api.jsonl` |
| `scripts/local/bootstrap.sh` | — | Create the tables and the bucket, and mirror the secrets into the local Parameter Store (idempotent; `up.sh` runs it). It refuses to run unless every AWS endpoint points at a stand-in |
| `scripts/local/status.sh` | `make status` | Which services answer |
| `scripts/local/logs.sh [service…]` | `make logs` | Follow the containers' logs |
| `scripts/local/down.sh [--wipe]` | `make down`, `make wipe` | Stop; `--wipe` drops the volumes and `.local/runtime.env` |

## 6. Checks

```bash
curl -s 'localhost:8088/api/healthz?deep=1'    # "stores": "dynamodb"; both bots "ready" when keys are set
curl -s 'localhost:8088/api/broker/plans/search?q=blue&limit=2'
grep '"event": "init"' .local/logs/api.jsonl | tail -1    # "secrets_loaded": 2 or more
DDB_LOCAL_ENDPOINT=http://localhost:8000 uv run pytest -m integration tests/integration   # needs no model calls
```

The integration tests use their own tables in the stack's DynamoDB Local, with scripted models.

Full smoke test *(coming in L9)*.

## 7. Troubleshooting

- **`Docker isn't running`:** start Docker Desktop and wait for it to say it's running.
- **A port is taken:** `lsof -i :8088` (or the port in question), or set the matching `SBC_PORT_*`.
- **A bot shows `unavailable: OPENROUTER_API_KEY_… is not set`:** put the key in `.env`, run
  `scripts/local/bootstrap.sh`, and restart the API.
- **Every answer is "I couldn't answer this from the SBC…":** look at the `turn` line in `.local/logs/api.jsonl`;
  `fallback: model_unavailable … HTTP 403: Key limit exceeded` means the OpenRouter key is out of credit.
- **Start over:** `make wipe && make up`.
