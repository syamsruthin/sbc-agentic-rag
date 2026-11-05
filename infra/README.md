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
| `cdk/` | AWS CDK app (Python, its own uv project): DynamoDB, S3, Cognito, ECR, Lambda + Function URL, CloudFront, ECS build task, alarms and budgets, GitHub OIDC role | WP9 |

The `cloudrun/` and `supabase/migrations/` folders of the original design (revision 2) are no longer used: the
API runs on Lambda and the operational data is in DynamoDB, whose tables `sbc_aws.schema` defines.
