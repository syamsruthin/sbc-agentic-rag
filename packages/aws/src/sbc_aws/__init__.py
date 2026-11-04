"""AWS adapters for the API (docs/aws/MIGRATION_PLAN.md, WP2).

Allowed internal dependencies: sbc_core only (enforced by import-linter). The API wires these adapters to its
ports; nothing in the agent, tools, retrieval or core packages imports them.
"""
