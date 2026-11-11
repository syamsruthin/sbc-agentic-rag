# infra/

Deployment and environment definitions (PLAN.md sections 8 and 9, revision 3: AWS). CI workflows live in
[`.github/workflows/`](../.github/workflows), because GitHub only runs workflows from there. The work packages
that fill this folder are in [docs/aws/MIGRATION_PLAN.md](../docs/aws/MIGRATION_PLAN.md); how to use them is in
[docs/aws/RUNBOOK.md](../docs/aws/RUNBOOK.md).

| Path | What | Status |
|---|---|---|
| `docker/api.Dockerfile` | The API image: runtime packages only, Lambda Web Adapter, facts bundle at `/opt/bundle/facts.sqlite`, non-root | Done (WP8) |
| `docker/build.Dockerfile`, `build-pipeline.sh`, `s3_sync.py` | The offline SBC build image (`full` / `bundle` modes), synced with the S3 data bucket by content | Done (WP8) |
| `docker/compose.yaml` | Local stack: the API image on DynamoDB Local (`sbc-create-tables` creates the tables) | Done (WP8) |
| `docker/fixture_bundle.py`, `docker/smoke.sh` | The synthetic bundle for images without SBC data; the read-only smoke test (also run in CI) | Done (WP8) |
| `local/` | The full local stack (branch `main_local` only): compose with DynamoDB Local, S3Mock, moto and the Caddy edge, `stack.env`, the `Caddyfile`. See [docs/local/RUNBOOK.md](../docs/local/RUNBOOK.md) | Done (local track) |
| `cdk/` | AWS CDK app (Python, its own uv project): `SbcFoundation` (S3, DynamoDB, Cognito, ECR, budgets) and `SbcApp` (Lambda + Function URL, CloudFront, ECS build task, alarms, GitHub OIDC role); 14 template tests. See [cdk/README.md](cdk/README.md) | Done (WP9) |

The operator scripts for deploying and running it are in [`scripts/aws/`](../scripts/aws). CI deploys with them too:
`.github/workflows/deploy-aws.yml` (called by `ci.yml` on `main_AWS`) and `build-data.yml` (WP11).

The `cloudrun/` and `supabase/migrations/` folders of the original design (revision 2) are no longer used: the
API runs on Lambda and the operational data is in DynamoDB, whose tables `sbc_aws.schema` defines.
