# Handoff: continue sbc-agentic-rag on the AWS track (`main_AWS`)

Paste everything below into a new Claude Code session opened on
`https://github.com/syamsruthin/sbc-agentic-rag`, branch `main_AWS`. Written 2026-11-11, after WP10 and WP12.

---

## Context

You are continuing an approved project: a chatbot that answers questions about health plans from their Summary
of Benefits and Coverage (SBC) documents, with two bots (Broker Assistant, signed in; Plan Shopper, public).
The AWS deployment track lives on `main_AWS`, a long-lived branch that is **never merged into `main`**.

Read these first, in order:

1. `docs/aws/TODO.md`: what is left, in order, and what needs the user (marked 👤).
2. `docs/aws/MIGRATION_PLAN.md`: the spec for every work package (WP0–WP14), each with an as-built status note.
3. `docs/aws/TASKS_DONE.md`: what is done, by commit.
4. `docs/aws/RUNBOOK.md`: how to deploy, roll back and operate.
5. `PLAN.md` (revision 3), `README.md`, `docs/answer-path.md`, `docs/agent-graph.md`, `docs/agent-tools.md`.

## Working rules (from the user, still in force)

- **Accuracy is legally critical.** Every number shown to a user must be the SBC's own wording, verified against
  the PDF. Never present inferred content (carried notes, inferred event labels, catalog metadata) as SBC text.
  When unsure, flag it instead of guessing.
- Small conventional commits (`feat:`, `fix:`, `docs:`, `ci:`, `chore:`, `test:`). On this track, commits go
  straight to `main_AWS`; don't open PRs into `main`. Commit or push only when the user asks.
- If something in PLAN.md or MIGRATION_PLAN.md turns out wrong or unworkable, stop and explain it with a
  proposed fix. Change the plan only after the user agrees.
- **Docs with every change:** before each commit, audit every tracked `.md` (including `docs/aws/*`) and bring
  the affected ones in line: status lines, TASKS_DONE (row plus detail section), TODO, MIGRATION_PLAN's
  as-built note, RUNBOOK, README.
- Models are all called through OpenRouter with ZDR routing. Don't add Voyage or Cohere (not ZDR there).
- Run the CI checks before every commit:

  ```bash
  uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports \
    && uv run pytest tests/unit && uv run python -m evals.harness.ci_gate
  uv run --project infra/cdk pytest infra/cdk/tests          # when infra/ changes
  uvx --from shellcheck-py shellcheck -x -P SCRIPTDIR -s bash scripts/aws/*.sh infra/docker/*.sh
  ```

  Pre-commit hooks (gitleaks, ruff, mypy, import contracts) are configured. Baseline: 439 unit tests,
  14 CDK template tests, `cdk synth` clean, actionlint clean. Frontends: `cd apps && npm run check` (ESLint, `tsc`,
  11 Vitest tests, build).

## Where things stand

**Done in code on `main_AWS` (WP0–WP12):**

- Config for local and AWS, AWS adapters (DynamoDB stores and LangGraph checkpoints, SSM secrets, S3 bundle
  fetch) in `packages/aws`.
- FastAPI app (`sbc_api.app:create_app`, port 8080) with SSE chat for both bots, Cognito auth for brokers,
  Turnstile-gated public sessions, rate limits, a global daily cap and a kill switch, and the SBC PDF proxy for
  preview-only plans.
- Containers (API and offline build images), a local compose stack and a smoke test (`infra/docker/`).
- CDK (Python, `infra/cdk/`): stacks `SbcFoundation-dev` and `SbcApp-dev`. Lambda container (arm64) behind a
  Function URL (`AWS_IAM`, response streaming) behind CloudFront with origin access control; DynamoDB, Cognito,
  S3, SSM, budgets and alarms, ECS Fargate Spot for the offline build, and a GitHub OIDC deploy role.
- Operator scripts in `scripts/aws/` (bootstrap, deploy, secrets, bundle upload, image push, seed users,
  smoke test).
- WP11 CI/CD: `ci.yml` calls `deploy-aws.yml` after every check passes on a push to `main_AWS`;
  `build-data.yml` runs the offline build on demand. CI skips the deploy with a notice until the repository
  variable `AWS_DEPLOY_ROLE_ARN` is set.
- WP10 frontends: an npm workspace in `apps/` (`chat-ui`, `landing`, `broker`, `shopper`), published by
  `scripts/aws/deploy-web.sh` and by CI after each API deploy. Checked locally, not on AWS.
- WP12: Lambda JSON log format, `demo_user_limit_reached` and its alarm, request ID in the apps, a per-answer
  details footer (path, model calls, cost), and optional Langfuse: one trace per turn (`sbc_api/tracing.py`).
- Evals: retrieval eval and the generation eval through the agent graph, golden set `evals/golden/0.2.0`.

**Things a new session must know:**

- CloudFormation owns the Lambda `live` alias. Deploy with `scripts/aws/deploy.sh app <api-tag>` (it runs
  `cdk deploy SbcApp -c apiImageTag=<tag>`); never move the alias by hand.
- Bearer tokens travel in `X-Sbc-Authorization: Bearer …`, because CloudFront's origin signing replaces
  `Authorization`. Every POST needs an `x-amz-content-sha256` header with the body's SHA-256.
- `deploy.sh` saves deploy context (alarm email, demo user IDs, warmer, OIDC ARN, build image tag) in SSM under
  `/sbc-deploy/<env>/`, so CI deploys keep them.
- **Nothing has run against AWS yet.** The first deploy is manual (RUNBOOK §4) and will probably need script
  fixes.
- The local OpenRouter broker key has used its whole $5 limit (checked 2026-11-10), so every live model call
  fails until the user raises it or creates new keys.

## Data and secrets: not in git

Nothing under `data/` or `bundles/` is committed. The corpus, the `2027.1` bundle and the embedding cache are on
the user's machine. A cloud session has none of them: it can do code work against the fixture
bundle (`uv run python infra/docker/fixture_bundle.py bundles/fixture`), but not the data steps.
The frontends run against a local API (apps/README.md). Don't fake data.

Secrets come from the environment, never the repo; names are in `.env.example` (`OPENROUTER_API_KEY_BROKER`,
`OPENROUTER_API_KEY_PUBLIC`, `TURNSTILE_SECRET_KEY`, `SESSION_JWT_SECRET`, …). On AWS they live in SSM under
`/sbc/<env>/`.

## Next steps

### Needs the user 👤 (docs/aws/TODO.md §2)

1. OpenRouter: raise the broker key's limit or replace it; create a separate public key. Limits: $15 a month
   broker, $10 public.
2. AWS account: Paid plan (no Organizations or Control Tower), Identity Center admin with MFA, budgets, the
   Lambda concurrency quota, AWS CLI v2, Docker with buildx, `cdk bootstrap`.
3. A Cloudflare Turnstile widget.
4. Spikes 2 and 3 on AWS (`spikes/aws/deploy_spike.sh up`, `check`, `destroy`).
5. Rebuild the bundle as `2027.2`, embed it and rerun the retrieval eval (expect recall@5 ≈ 0.920).
6. The first deploy (RUNBOOK §4, including `TURNSTILE_SITE_KEY=… scripts/aws/deploy-web.sh` for the apps), then
   set `AWS_DEPLOY_ROLE_ARN` so pushes deploy, and check one CI deploy and one rollback through `deploy-aws.yml`.

When the user does these, help run them and fix whatever the scripts get wrong; record the fixes in RUNBOOK §4.

### Code work, in order

1. **Frontend toolchain:** Node 22, then Vite 7+, Vitest 4+ and a current typescript-eslint, which clears the
   dev-only `npm audit` advisories.
2. **WP13: portfolio readiness:** needs a live deploy. Caps tested one by one, latency measured, the evals
   HTTP adapter (about 30 golden items through the deployed API), the cold click-through, RUNBOOK and README
   "Live demo".
3. **WP14 (later, optional):** Bedrock.

Stop and report to the user after each work package, with the checks you ran and what still needs them.

## Open loose ends

- CI on `c0e47fb` (the first run of the WP11 workflows on GitHub) passed and skipped the deploy, as expected
  before `AWS_DEPLOY_ROLE_ARN` is set.
