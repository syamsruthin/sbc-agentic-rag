# AWS spikes

Throwaway experiments for the three risks named in [MIGRATION_PLAN.md §13](../../docs/aws/MIGRATION_PLAN.md).
Results are in [docs/aws/SPIKES.md](../../docs/aws/SPIKES.md).

Nothing here is imported by the application or collected by the test suite. The real work packages
(WP2, WP4, WP8, WP9) have landed, so the folder can be deleted once the AWS run of spikes 2 and 3 is done or
the first real deploy has shown the same thing.

| File | Spike |
|---|---|
| `checkpoint_resume.py` | 1: LangGraph interrupt/resume through `DynamoDBSaver` across two processes |
| `sse_app/app.py`, `sse_app/Dockerfile` | 2 and 3: SSE endpoint behind the Lambda Web Adapter; image size and init time with the real runtime stack |
| `sse_client.py` | 2: checks that SSE events arrive streamed (not buffered) and that the POST body hash header is accepted |
| `cdk_sse/` | 2 and 3 on AWS: ECR, Lambda (arm64), Function URL (`RESPONSE_STREAM`) behind CloudFront with OAC |
| `deploy_spike.sh` | Deploys `cdk_sse`, runs the client and the cold-start check, and tears down |

## Run locally (no AWS account)

```bash
# Spike 1
docker run -d --name ddb-local -p 8000:8000 amazon/dynamodb-local:2.6.1 -jar DynamoDBLocal.jar -inMemory -sharedDb
uv run --with langgraph-checkpoint-aws==1.2.6 python -m spikes.aws.checkpoint_resume run

# Spikes 2 and 3 (container; <dir> holds facts.sqlite, e.g. the test fixture bundle)
docker build -f spikes/aws/sse_app/Dockerfile --build-context bundle=<dir> -t sbc-spike .
docker run --rm --read-only --tmpfs /tmp -p 8080:8080 sbc-spike
uv run python -m spikes.aws.sse_client http://localhost:8080
```

Behind a TLS-inspecting proxy, add `--network=host --build-arg HTTPS_PROXY=$HTTPS_PROXY
--secret id=ca,src=<ca-bundle.pem>` to the build.

## Run on AWS (costs cents; tear down afterwards)

Needs AWS credentials for `us-east-1`, `cdk bootstrap` done once, Docker with buildx, uv and npx.

```bash
spikes/aws/deploy_spike.sh up        # deploy and run the checks
# wait 10+ minutes so the function goes cold, then:
spikes/aws/deploy_spike.sh check     # cold-start measurement (REPORT "Init Duration") and streaming again
spikes/aws/deploy_spike.sh destroy   # delete everything
```
