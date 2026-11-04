# AWS spikes: results

The three spikes from [MIGRATION_PLAN.md §13](MIGRATION_PLAN.md#13-sequencing-and-effort), run on
2026-11-04 on branch `main_AWS`. The code and instructions are in [`spikes/aws/`](../../spikes/aws/README.md).

| # | Question | Result |
|---|---|---|
| 1 | Does LangGraph interrupt/resume work through `DynamoDBSaver` when the resume runs in a different process? | **Pass** (DynamoDB Local) |
| 2 | Does SSE stream through the Lambda Web Adapter, a Function URL and CloudFront with OAC, including a POST with the body hash? | **Pass locally** (container + client); the AWS leg is ready to run but **not yet run** (no AWS account available in this environment) |
| 3 | How big is the image with the bundle, and how long is the cold start? | **Measured locally**: ≈ 549 MB uncompressed, 2.1–2.3 s from container start to ready. The Lambda cold start is **not yet measured** (same reason as 2) |

**Outcome:** nothing found so far changes the plan. Three details are corrected below (§4). The remaining AWS
checks take one command (`spikes/aws/deploy_spike.sh up`) once you have an account.

---

## 1. Spike 1: checkpoint resume across processes

**Setup:**

- `langgraph-checkpoint-aws==1.2.6`; its requirements, `langgraph>=1.0` and `langgraph-checkpoint>=3,<5`, are compatible with the locked `langgraph 1.2.19` and `langgraph-checkpoint 4.3.1`.
- DynamoDB Local 2.6.1, with the table created the way CDK will create it: `PK`/`SK` strings, on-demand billing, TTL attribute `ttl`.
- The real `sbc_agent` graph, on the two-plan fixture bundle, with scripted model responses.

**Test:**

1. Process A asks "What's the deductible on ZZ01?", which is ambiguous, so the graph pauses with a clarifying question.
2. Process B, a new Python process sharing nothing with A but the table, answers with Example Gold.
3. Process B's model script contains **only** the compose response, so it can finish only if the classification and the pending interrupt came back from DynamoDB.

| Measure | Value |
|---|---|
| Process A | `clarify` with both plans as options; 1 model call; 148 ms |
| Process B | `answer` for Example Gold with the correct deductible text; 1 model call (no re-classification); 107 ms |
| Items written | 46 after the question, 78 after the answer (a two-request turn) |
| Largest item | ≈ 3.5 KB (far below the 350 KB S3 offload threshold and the 400 KB item limit) |
| Write units | ≈ 86 WRU for the two requests, ≈ 43 per request, in line with the ≈ 70 per turn assumed in the cost model |
| TTL | Set on every item (`ttl_seconds` works) |

**Cost check:** at ≈ $0.625 per million write units, 1,000 turns a month is about $0.05. The DynamoDB line in the cost model stands.

**Note:** checkpoints accumulate per thread, one per graph step. Retention is handled by the TTL (30 days public, 365 days broker, 1 day for the demo user), so no cleanup job is needed.

## 2. Spike 2: SSE streaming and the body-hash header

**Local result (container, read-only root filesystem):**

```text
status 200 content-type text/event-stream; charset=utf-8
  0.09s  thread
  1.09s  status
  2.09s  status
  3.10s  status
  4.10s  status
  5.10s  status
  5.10s  answer
  5.10s  done
answer: {'markdown': 'Spike answer.', 'sha256_header_ok': True, 'body_bytes': 46}
status events spread over 4.01s -> streamed
SPIKE 2: PASS
```

- FastAPI's `StreamingResponse` with a sync generator streams event by event; nothing is buffered until the end.
- The `x-amz-content-sha256` check works both ways: a correct hash gives `true`, a wrong one gives `false`. The real frontend will compute the hash with `crypto.subtle`.

**AWS leg (ready, not run):**

- `spikes/aws/cdk_sse` synthesizes cleanly with `aws-cdk-lib 2.275.0` and CDK CLI `2.1147.0`. The template contains:
  - **Lambda:** container image, arm64, 2,048 MB.
  - **Function URL:** `AuthType: AWS_IAM`, `InvokeMode: RESPONSE_STREAM`.
  - **Origin access control:** `OriginAccessControlOriginType: lambda`, signing `always`, `sigv4`.
  - **Two Lambda permissions for CloudFront:** `lambda:InvokeFunctionUrl` **and** `lambda:InvokeFunction`. `FunctionUrlOrigin.with_origin_access_control` adds both automatically. The real stack (WP9) must keep both.
  - No NAT Gateway, no load balancer, no VPC.
- `spikes/aws/deploy_spike.sh up` builds the arm64 image, deploys, runs the same client through CloudFront, and prints the Lambda `REPORT` lines. Pass criteria: the same streamed timing, and `sha256_header_ok: true`, through CloudFront.

## 3. Spike 3: image size and start-up

**Image:** the WP8 shape.

- `python:3.13-slim`, plus the runtime workspace packages through `uv sync --no-dev --package sbc-api`, plus FastAPI, uvicorn and `langgraph-checkpoint-aws` (with boto3), plus Lambda Web Adapter 0.9.1.
- Built for x86_64 here; Lambda will run arm64.
- Two bundle variants: the fixture bundle alone, and the fixture plus 217 MB of incompressible padding standing in for the real `2027.1` bundle (which isn't in the repository).

| Variant | Uncompressed (sum of layers) | Compressed (gzip, ≈ what ECR stores) |
|---|---|---|
| Without the bundle | ≈ 331 MB | ≈ 104 MB |
| With a 217 MB bundle | ≈ 549 MB | ≈ 321 MB (upper bound: the padding doesn't compress; the real SQLite will) |

**Biggest dependencies in the virtual environment (185 MB total):**

- numpy 43 MB + 27 MB of bundled libraries;
- botocore 31 MB;
- zstandard 23 MB (pulled in by LangGraph and LangSmith);
- langsmith 9.6 MB, langchain_core 5.6 MB, pydantic + pydantic_core 9.3 MB.

**Start-up** (container started with `--read-only --tmpfs /tmp`, timed to the first `200` from `/api/healthz`; three runs each):

| Variant | Start → ready | Python imports | Wiring (open bundle, load both bot configs, build both agents and checkpointers) |
|---|---|---|---|
| Fixture bundle | 2.2–3.1 s (the first run includes a cold page cache) | 1.2–1.6 s | 130–200 ms |
| 217 MB bundle | 2.1–2.3 s | 1.2–1.4 s | 140–190 ms |

**Findings:**

- **The read-only filesystem is fine.** The bundle opened with `?mode=ro` on a read-only root, which is the Lambda case. That answers risk #5 in the plan. `immutable=1` (WP1) is still worth adding to skip locking, but it isn't required.
- **Bundle size doesn't affect start-up.** The bundle is opened, not read; only the `meta` table is read at init.
- **Imports dominate start-up.** On Lambda, expect roughly 2–4 s of init (arm64 at 2 GB is about 1.15 vCPU, and image layers load lazily on first use) plus the image fetch. That's comfortably inside the 10 s target, and warm-on-load (§17.3 of the analysis) hides most of it. The real number comes from `deploy_spike.sh check`.
- **Possible later savings, if the measured cold start is high:**
  - drop `langsmith`/`zstandard` if LangGraph allows it;
  - import boto3 clients lazily;
  - try Lambda SnapStart for containers only if it is still cheaper than the alternatives.

## 4. Corrections to the plan

| Plan text | Correction |
|---|---|
| Checkpoints table TTL attribute `expires_at` (WP9 `DataConstruct`) | `DynamoDBSaver` writes **`ttl`**. Configure the checkpoints table's TTL on `ttl`; keep `expires_at` for the `app` and `audit` tables |
| "Pin the newest `langgraph-checkpoint-aws` that supports langgraph 1.2.x; check its constructor" (WP2) | **1.2.6** works: `DynamoDBSaver(table_name, session=..., endpoint_url=..., ttl_seconds=..., s3_offload_config={"bucket_name": ...})`. Pass an explicit `boto3.Session` (tests and local runs must not pick up shell credentials) |
| OAC for the Lambda URL grants `lambda:InvokeFunctionUrl` (WP9) | CloudFront needs **both** `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`; CDK's `FunctionUrlOrigin.with_origin_access_control` adds both |

## 5. What's left

- [ ] Run `spikes/aws/deploy_spike.sh up` in your AWS account, then `check` after 10+ minutes idle. Record:
  - the streamed timing through CloudFront;
  - `sha256_header_ok`;
  - `Init Duration` from the `REPORT` lines;
  - first-request latency.
- [ ] Then `spikes/aws/deploy_spike.sh destroy`.
- [ ] If the AWS checks pass, start WP1 (configuration).
