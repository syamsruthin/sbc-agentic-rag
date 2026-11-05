"""The CDK stack's tables (infra/cdk/sbc_infra/tables.py) equal the ones the API uses (sbc_aws.schema)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sbc_aws.schema import TABLE_SPECS

TABLES_PY = Path(__file__).resolve().parents[2] / "infra" / "cdk" / "sbc_infra" / "tables.py"


def test_cdk_tables_match_the_app_schema() -> None:
    spec = importlib.util.spec_from_file_location("cdk_tables", TABLES_PY)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = tuple((s.role, s.partition_key, s.sort_key, s.ttl_attribute) for s in TABLE_SPECS)
    assert app == module.TABLES
