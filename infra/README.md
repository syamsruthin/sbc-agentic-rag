# infra/

Deployment and environment definitions (PLAN.md sections 8 and 9). CI workflows live in
[`.github/workflows/`](../.github/workflows), because GitHub only runs workflows from there.

| Folder | What | Added in |
|---|---|---|
| `docker/` | API container image (python:3.13-slim, uv, facts bundle copied in) | Phase 1 |
| `cloudrun/` | Cloud Run service definitions for `api-dev` and `api-prod` | Phase 1 |
| `supabase/migrations/` | Operational Postgres schema: sessions, checkpoints, feedback, audit log | Phase 1 |
