# infra/

Deployment and environment definitions (PLAN.md sections 8 and 9, revision 3: AWS). CI workflows live in
[`.github/workflows/`](../.github/workflows), because GitHub only runs workflows from there. The work packages
that fill this folder are in [docs/aws/MIGRATION_PLAN.md](../docs/aws/MIGRATION_PLAN.md).

| Folder | What | Work package |
|---|---|---|
| `docker/` | API image (python:3.13-slim, uv, Lambda Web Adapter, facts bundle), offline-build image (with poppler), local `compose.yaml` with DynamoDB Local | WP8 |
| `cdk/` | AWS CDK app (Python, its own uv project): DynamoDB, S3, Cognito, ECR, Lambda + Function URL, CloudFront, ECS build task, alarms and budgets, GitHub OIDC role | WP9 |

The `cloudrun/` and `supabase/migrations/` folders of the original design (revision 2) are no longer used: the
API runs on Lambda and the operational data is in DynamoDB, whose tables `sbc_aws.schema` defines.
