# apps/

React + Vite + TypeScript + Tailwind frontends, hosted on S3 behind the same CloudFront distribution as the API
(PLAN.md sections 2.2 and 4, revision 3; docs/aws/MIGRATION_PLAN.md WP10). One npm workspace, one lock file.

| Folder | What | Served at |
|---|---|---|
| `landing/` | Landing page with the demo broker login (read at runtime from `/landing/demo.json`), "Try these" questions, and the "How it works" and "Quality and cost" pages | `/` |
| `broker/` | Broker Assistant: Cognito sign-in (SRP, TOTP for accounts that use it), plan picker with state, market, plan type and metal filters (up to 6 plans), CSV export of comparison tables, earlier conversations | `/broker/` |
| `shopper/` | Plan Shopper: state choice and Turnstile, then a session token; plan picker (up to 3 Individual plans in that state), plain-language chat | `/shopper/` |
| `chat-ui/` | Shared package: the API client, SSE over `fetch`, the chat panel, answer rendering with sources, clarification buttons, limit and error notices, feedback, the "Report a problem" link with the request ID, and the plan picker | — |

## How the apps talk to the API

All of it is in `chat-ui/src/api.ts` and `chat-ui/src/sse.ts`:

- **The bearer token goes in `X-Sbc-Authorization: Bearer …`, never in `Authorization`.** CloudFront's origin
  access control signs each request to the Lambda Function URL with SigV4 in `Authorization`, replacing the
  browser's value.
- **Every POST carries `x-amz-content-sha256`**, the hex SHA-256 of its body, which that signing needs.
- **Chat turns are SSE over `fetch`** (a POST, so not `EventSource`): `thread`, `status`, `answer`, `clarify`,
  `limit`, `error` and `done`, with keepalive comments skipped. A refused turn is a `limit` event with HTTP 200.
- **Each app calls `/api/healthz` as it loads**, so the Lambda is warm by the first question.
- Tokens live in memory only: a Cognito session (refreshed with its refresh token) or a 24-hour public session.
- Catalog metadata (state, market, metal tier, plan status) is labeled "catalog" in the plan picker: it isn't
  SBC text (PLAN.md 3.3, rule 3). Answers are rendered as Markdown with raw HTML disabled.

## Local development

Needs Node 20.10 or newer (Node 22 recommended: Node 20 reached end of life on 2026-10-29) and the API on
port 8080.

```bash
APP_ENV=local FACTS_BUNDLE_PATH=bundles/facts-2027.2.sqlite uv run uvicorn --factory sbc_api.app:create_app --port 8080
```

```bash
cd apps && npm install
```

Then, in three terminals (each from `apps/`):

```bash
npm run dev:broker
```

```bash
npm run dev:shopper
```

```bash
npm run dev:landing
```

Open http://localhost:5173. The landing server proxies `/api` to the API and `/broker/` and `/shopper/` to their
dev servers, so the paths match CloudFront's.

- **Local mode.** With no `VITE_COGNITO_*` settings, the Broker Assistant skips sign-in (the local API accepts
  every request as a local broker). With no `VITE_TURNSTILE_SITE_KEY`, the Plan Shopper skips the bot check (the
  local API does too, when `TURNSTILE_SECRET_KEY` isn't set).
- **Model calls** need `OPENROUTER_API_KEY_BROKER` and `OPENROUTER_API_KEY_PUBLIC` with credit; without them, plan
  search works but questions end in an error or a fallback message.
- The landing page's demo login shows "not available" locally: `/landing/demo.json` exists only in the web bucket.
- **The full local stack** (docs/local/RUNBOOK.md) builds the apps with real local sign-in and Turnstile test keys
  instead: `scripts/local/web.sh` (served at http://localhost:8088, with `/landing/demo.json`), or
  `scripts/local/web.sh --dev` for these dev servers with the same settings.

## Checks and build

```bash
npm run check    # lint, typecheck, unit tests (chat-ui), then build all three and assemble apps/site/
```

CI runs the same steps (the `frontend` job in `.github/workflows/ci.yml`).

## Build settings

All public by design; `scripts/aws/deploy-web.sh` sets them from the stack outputs:

| Variable | Value |
|---|---|
| `VITE_API_BASE_URL` | `/api` |
| `VITE_COGNITO_USER_POOL_ID`, `VITE_COGNITO_CLIENT_ID` | `SbcFoundation` outputs `UserPoolId`, `UserPoolClientId` |
| `VITE_COGNITO_ENDPOINT` | Never on AWS. Only for the full local stack (docs/local/LOCAL_PLAN.md): the Cognito stand-in's URL; with it set, the broker app signs in with `USER_PASSWORD_AUTH`, because the stand-in can't complete SRP |
| `VITE_TURNSTILE_SITE_KEY` | the Turnstile widget's site key (saved in SSM under `/sbc-deploy/<env>/turnstileSiteKey`) |
| `VITE_REPORT_URL` | optional: where "Report a problem" points (default: this repository's new-issue page) |

## Deploy

`scripts/aws/deploy-web.sh` builds with those settings, syncs `apps/site/` to the web bucket (`--delete`, never
touching `landing/`, where `demo.json` lives), caches hashed assets for a year and `index.html` not at all, and
invalidates CloudFront. CI runs it after every API deploy (`.github/workflows/deploy-aws.yml`). See
docs/aws/RUNBOOK.md section 4 for the first run.
