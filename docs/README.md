# docs/

- [../PLAN.md](../PLAN.md): the approved design, evaluation strategy and roadmap.
- [sbc-pipeline.md](sbc-pipeline.md): how the SBC corpus is collected, converted to Markdown and verified, and
  how the facts bundle, its search index and plan resolution work.
- [agent-graph.md](agent-graph.md): one user turn end to end (redaction, classification, plan resolution,
  clarifying questions, fast path and agent loop), and the `sbc-ask` development CLI.
- [agent-tools.md](agent-tools.md): the tools the agent can call and the rules they enforce in code.
- [answer-path.md](answer-path.md): placeholders, the number and citation firewall, the renderer and the
  template fallback.
- [aws/](aws/): the AWS deployment track (PLAN.md revision 3):
  - [DEPLOYMENT_ANALYSIS.md](aws/DEPLOYMENT_ANALYSIS.md): architecture, costs, free-tier fit, portfolio mode.
  - [MIGRATION_PLAN.md](aws/MIGRATION_PLAN.md): work packages WP0–WP14 with specs and acceptance checks.
  - [SPIKES.md](aws/SPIKES.md): results of the three de-risking spikes.
  - [RUNBOOK.md](aws/RUNBOOK.md): running, building and operating it (local stack, images, offline build).
  - [TASKS_DONE.md](aws/TASKS_DONE.md) and [TODO.md](aws/TODO.md): what is finished and what is left.
- `reviews/`: human review sheets and sign-offs (cost grammar, converter fix, corpus snapshot).
- `adr/`: architecture decision records, one per major decision (added as decisions are revisited).
- `runbook.md`: incident runbook (Phase 4; PLAN.md section 7.4 has the draft).
