# apps/

React + Vite + TypeScript frontends, hosted on S3 + CloudFront on the same domain as the API (PLAN.md sections 2.2
and 4, revision 3; docs/aws/MIGRATION_PLAN.md WP10).

| Folder | What | Built in |
|---|---|---|
| `landing/` | Portfolio landing page, "How it works" and "Quality and cost" pages, demo broker login | WP10 |
| `broker/` | Broker Assistant: Cognito sign-in, plan picker, comparison tables, CSV export | WP10 (Phase 1) |
| `shopper/` | Plan Shopper: Turnstile, guided state and plan selection, plain-language answers | WP10 (Phase 3) |
| `chat-ui/` | Shared components: SSE chat stream (over `fetch`, with the `x-amz-content-sha256` body hash), citations panel, comparison table, plan picker | WP10 |

The Node toolchain and its CI job are added with the first app.
