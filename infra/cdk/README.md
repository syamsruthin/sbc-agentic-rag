# infra/cdk

The AWS CDK app (Python) for the AWS deployment (docs/aws/MIGRATION_PLAN.md WP9). It is its own uv project
(`uv sync --project infra/cdk`), so CDK's dependencies stay out of the runtime lock and the Lambda image.

| Stack | What | Deployed |
|---|---|---|
| `SbcFoundation-<env>` | Data bucket (RUNBOOK §3 layout, lifecycle rules), DynamoDB `app`/`audit`/`checkpoints` tables (`sbc_infra/tables.py` = `sbc_aws.schema`), Cognito pool + SPA client + `broker`/`admin` groups, ECR repositories, budget + anomaly alerts | First, before any image exists |
| `SbcApp-<env>` | API Lambda (arm64, 2 GB, image from ECR) + `live` alias + streaming Function URL (`AWS_IAM`), CloudFront + web bucket (OAC for both origins), ECS build task (Fargate Spot, no VPC), alarms + metric filters, optional warmer, GitHub OIDC deploy role | After the images are pushed: `-c apiImageTag=<tag>` |

```bash
uv sync --project infra/cdk
uv run --project infra/cdk pytest infra/cdk/tests      # template tests: cost guardrails, IAM, streaming, auth
cd infra/cdk && npx -y aws-cdk@2.1147.0 synth -c apiImageTag=test   # no AWS credentials needed
```

Deploying is done with the scripts in `scripts/aws/` (bootstrap, deploy, secrets, images, users, demo password,
offline build); the step-by-step first deployment is in [docs/aws/RUNBOOK.md](../../docs/aws/RUNBOOK.md) §4.

**Context** (`cdk.json`, or `-c key=value`):

| Key | Default | Meaning |
|---|---|---|
| `envName` | `dev` | Names every resource (`sbc-<env>-…`) and the SSM prefix (`/sbc/<env>/`) |
| `apiImageTag` | — | API image tag in ECR; `SbcApp` is synthesized only when it's set |
| `buildImageTag` | `latest` | Build image tag in ECR |
| `alarmEmail` | empty | Alarm and budget emails; budgets and anomaly alerts are created only with it |
| `monthlyBudgetUsd` | `10` | The cost budget |
| `demoUserIds` | `[]` | JSON list of the demo-broker account's Cognito `sub` (`scripts/aws/seed-users.sh` prints it) |
| `warmer` | `false` | Ping the API every 5 min on weekday business hours (free; keeps an instance warm) |
| `githubOidcProviderArn` | empty | An existing GitHub OIDC provider in the account (only one is allowed); empty creates it |
| `githubRepository`, `githubBranch` | this repo, `main_AWS` | Who may assume the deploy role |

**Rules the API depends on** (and the tests check):

- **Bearer tokens travel in `X-Sbc-Authorization`.** Origin access control signs every request to the Function URL with SigV4 in `Authorization`, replacing the browser's value.
- **CloudFront forwards `CloudFront-Viewer-Address`** for the per-IP limits and Turnstile.
- **There is no VPC, NAT, load balancer, Secrets Manager secret, KMS key or custom resource.**
- **The audit table is `PutItem`-only for the API.**
- **CloudFormation owns the `live` alias.** Deploy a new API image by deploying `SbcApp` with its tag (WP11 does this), not by moving the alias by hand, or the next deploy moves it back.
