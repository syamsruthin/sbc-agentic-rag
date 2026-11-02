"""Read-only access to the facts bundle (the SQLite file built by ``sbc-build-bundle``).

This is the production :class:`~sbc_core.interfaces.FactsReader`. It opens the bundle read-only and only runs
fixed, parameterized queries. The schema is owned by ``sbc_index.bundle``; a schema change there needs a
matching change here, which the round-trip tests catch.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, get_args
from urllib.parse import quote

from sbc_core.template import LIMIT_QUESTIONS, LIST_TITLES, QUESTION_IDS, QUESTIONS
from sbc_core.tiers import TierId
from sbc_core.types import ChunkContext, Citation, Fact, PdfSource, PlanSummary

_PLAN_COLUMNS = (
    "plan_key, plan_year, sbc_id, contract_code, plan_name, issuer, plan_type, coverage_period, coverage_for,"
    " state, market, network, exchange, plan_status, metal_tier, metal_tier_source, source_url, eoc_url"
)
_PLAN_FIELDS = [c.strip() for c in _PLAN_COLUMNS.split(",")]
_PLAN_SELECT = ", ".join(f"p.{c}" for c in _PLAN_FIELDS)
# Column order of the SBC benefit table (the bundle stores tiers by ID, not by column position).
_TIER_ORDER = {t: i for i, t in enumerate(get_args(TierId))}
_MIN_NAME_QUERY = 3  # the trigram tokenizer cannot match shorter strings


class BundleError(Exception):
    """The bundle is missing or not a facts bundle this code can read."""


def _pages(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split(",") if p)


def _in(values: Sequence[str]) -> str:
    return ",".join("?" * len(values))


_SOURCE_COLUMNS = (
    "fetch_method", "portal", "catalog_url", "state", "market_unit", "plan_name", "plan_type",
    "coverage_period", "network", "language", "plan_status", "doc_sys_id", "dps_unique_id", "brand",
    "source_system", "pdf_sha256", "pdf_bytes",
)  # fmt: skip


class SqliteFactsReader:
    """Reads the facts bundle.

    ``pdf_proxy_base`` (the API passes ``/api/sbc/``) points the ``source_url`` of plans whose PDF is served
    only by the portal's preview form at the API's PDF proxy, ``{base}{plan_key}.pdf``, so their citations
    link to the PDF at the cited page. Without it (the CLI, tests) ``source_url`` is the bundle's own value.
    """

    def __init__(self, path: Path | str, *, pdf_proxy_base: str | None = None) -> None:
        path = Path(path)
        if not path.is_file():
            raise BundleError(f"facts bundle not found: {path}")
        # immutable=1: a bundle never changes while open, so SQLite skips locking (the image is read-only).
        uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
        self._db = sqlite3.connect(uri, uri=True, check_same_thread=False)
        try:
            meta = {k: json.loads(v) for k, v in self._db.execute("SELECT key, value FROM meta")}
        except sqlite3.DatabaseError as exc:
            self._db.close()
            raise BundleError(f"not a facts bundle: {path}: {exc}") from None
        self.meta: dict[str, Any] = meta
        self._plan_ids: dict[str, int] = {}
        self._plans: dict[str, PlanSummary] = {}
        self._has_sources = bool(
            self._db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'plan_source'"
            ).fetchone()
        )
        self._pdf_proxy_base = pdf_proxy_base
        self._preview_only: frozenset[int] = frozenset()
        if pdf_proxy_base is not None and self._has_sources:
            rows = self._db.execute("SELECT plan_id FROM plan_source WHERE fetch_method = 'preview_post'")
            self._preview_only = frozenset(r[0] for r in rows)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> SqliteFactsReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def bundle_version(self) -> str:
        return str(self.meta["bundle_version"])

    # ------------------------------------------------------------------ plans

    def _load_plans(self, where: str, params: Sequence[object]) -> list[PlanSummary]:
        rows = self._db.execute(f"SELECT p.plan_id, {_PLAN_SELECT} FROM plan p {where}", params).fetchall()  # noqa: S608 - fixed SQL fragments
        out = []
        for plan_id, *values in rows:
            fields = {k: v for k, v in zip(_PLAN_FIELDS, values, strict=True) if v is not None}
            if plan_id in self._preview_only:
                fields["source_url"] = f"{self._pdf_proxy_base}{quote(str(fields['plan_key']), safe='')}.pdf"
            summary = PlanSummary(**fields)
            self._plan_ids[summary.plan_key] = plan_id
            self._plans[summary.plan_key] = summary
            out.append(summary)
        return out

    def plans(self, plan_keys: Sequence[str]) -> dict[str, PlanSummary]:
        missing = [k for k in dict.fromkeys(plan_keys) if k not in self._plans]
        if missing:
            self._load_plans(f"WHERE p.plan_key IN ({_in(missing)})", missing)
        return {k: self._plans[k] for k in plan_keys if k in self._plans}

    def _plan(self, plan_key: str) -> tuple[int, PlanSummary] | None:
        found = self.plans([plan_key])
        if plan_key not in found:
            return None
        return self._plan_ids[plan_key], found[plan_key]

    def pdf_source(self, plan_key: str) -> PdfSource | None:
        if not self._has_sources:
            return None
        found = self._plan(plan_key)
        if found is None:
            return None
        plan_id, plan = found
        row = self._db.execute(
            f"SELECT {', '.join(_SOURCE_COLUMNS)} FROM plan_source WHERE plan_id = ?",  # noqa: S608 - fixed columns
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        sbc_id = self._db.execute("SELECT sbc_id FROM plan WHERE plan_id = ?", (plan_id,)).fetchone()[0]
        return PdfSource(
            plan_key=plan_key, sbc_id=sbc_id, contract_code=plan.contract_code,
            **dict(zip(_SOURCE_COLUMNS, row, strict=True)),
        )  # fmt: skip

    def find_plans_by_name(
        self,
        text: str,
        *,
        plan_year: int | None = None,
        states: Sequence[str] = (),
        markets: Sequence[str] = (),
        exclude_plan_statuses: Sequence[str] = (),
        exclude_exchanges: Sequence[str] = (),
        limit: int = 10,
    ) -> list[PlanSummary]:
        text = " ".join(text.split())
        if len(text) < _MIN_NAME_QUERY:
            return []
        where = ["plan_name_fts MATCH ?"]
        params: list[object] = ['"' + text.replace('"', '""') + '"']
        if plan_year is not None:
            where.append("p.plan_year = ?")
            params.append(plan_year)
        if states:
            where.append(f"p.state IN ({_in(states)})")
            params += list(states)
        if markets:
            where.append(f"p.market IN ({_in(markets)})")
            params += list(markets)
        if exclude_plan_statuses:
            where.append(f"COALESCE(p.plan_status, '') NOT IN ({_in(exclude_plan_statuses)})")
            params += list(exclude_plan_statuses)
        if exclude_exchanges:
            where.append(f"COALESCE(p.exchange, '') NOT IN ({_in(exclude_exchanges)})")
            params += list(exclude_exchanges)
        params.append(limit)
        sql = (
            "JOIN plan_name_fts f ON f.plan_key = p.plan_key WHERE "
            + " AND ".join(where)
            + " ORDER BY bm25(plan_name_fts), p.plan_name LIMIT ?"
        )
        return self._load_plans(sql, params)

    def filter_plans(
        self,
        *,
        name: str | None = None,
        plan_year: int | None = None,
        states: Sequence[str] = (),
        markets: Sequence[str] = (),
        plan_types: Sequence[str] = (),
        metal_tiers: Sequence[str] = (),
        exclude_plan_statuses: Sequence[str] = (),
        exclude_exchanges: Sequence[str] = (),
        list_item: tuple[str, str] | None = None,
        limit: int = 10,
    ) -> tuple[list[PlanSummary], int]:
        joins, where, params = "", list[str](), list[object]()
        if name is not None:
            name = " ".join(name.split())
            if len(name) < _MIN_NAME_QUERY:
                return [], 0
            joins = "JOIN plan_name_fts f ON f.plan_key = p.plan_key "
            where.append("plan_name_fts MATCH ?")
            params.append('"' + name.replace('"', '""') + '"')
        if plan_year is not None:
            where.append("p.plan_year = ?")
            params.append(plan_year)
        for column, values in (("p.state", states), ("p.market", markets), ("p.metal_tier", metal_tiers)):
            if values:
                where.append(f"{column} IN ({_in(values)})")
                params += list(values)
        if plan_types:  # "PPO" matches "PPO", "PPO + HSA" and "PPO+HRA"
            where.append("(" + " OR ".join("p.plan_type LIKE ?" for _ in plan_types) + ")")
            params += [f"{t}%" for t in plan_types]
        for column, values in (("p.plan_status", exclude_plan_statuses), ("p.exchange", exclude_exchanges)):
            if values:
                where.append(f"COALESCE({column}, '') NOT IN ({_in(values)})")
                params += list(values)
        if list_item is not None:
            list_id, item = list_item
            where.append(
                "p.plan_id IN (SELECT i.plan_id FROM plan_list_item i"
                " JOIN text_string t ON t.text_id = i.item_id"
                " WHERE i.list_id = ? AND t.text LIKE ? ESCAPE '\\')"
            )
            escaped = item.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params += [list_id, f"%{escaped}%"]
        clause = joins + ("WHERE " + " AND ".join(where) if where else "")
        (total,) = self._db.execute(f"SELECT COUNT(*) FROM plan p {clause}", params).fetchone()  # noqa: S608
        order = "bm25(plan_name_fts), p.plan_name" if name is not None else "p.state, p.market, p.plan_name"
        plans = self._load_plans(f"{clause} ORDER BY {order}, p.plan_key LIMIT ?", [*params, limit])
        return plans, int(total)

    def list_item_texts(self, list_id: str) -> list[str]:
        rows = self._db.execute(
            """SELECT DISTINCT t.text FROM plan_list_item i JOIN text_string t ON t.text_id = i.item_id
               WHERE i.list_id = ? ORDER BY t.text""",
            (list_id,),
        )
        return [r[0] for r in rows]

    def find_plans_by_identifier(self, identifier: str, *, plan_year: int | None = None) -> list[PlanSummary]:
        identifier = identifier.strip()
        if not identifier:
            return []
        sql = "JOIN plan_identifier i ON i.plan_id = p.plan_id WHERE i.value = ?"
        params: list[object] = [identifier]
        if plan_year is not None:
            sql += " AND p.plan_year = ?"
            params.append(plan_year)
        return self._load_plans(sql + " GROUP BY p.plan_id ORDER BY p.plan_key", params)

    # ------------------------------------------------------------------ facts

    @staticmethod
    def _citation(plan: PlanSummary, section_path: str, pages: tuple[int, ...]) -> Citation:
        return Citation(
            plan_key=plan.plan_key, plan_name=plan.plan_name, contract_code=plan.contract_code,
            plan_year=plan.plan_year, section_path=section_path, pages=pages, source_url=plan.source_url,
        )  # fmt: skip

    def benefit_facts(
        self, plan_key: str, service_ids: Sequence[str] = (), tier_ids: Sequence[str] = ()
    ) -> list[Fact]:
        found = self._plan(plan_key)
        if found is None:
            return []
        plan_id, plan = found
        sql = """SELECT b.service_id, b.tier_id, sl.text AS label, tl.text AS tier_label, c.verbatim,
                        c.status, c.parse_json, lim.text AS limitations, b.limitations_kind, b.carried_note,
                        b.event_inferred, b.pages, ch.section_path, b.row_order
                 FROM plan_benefit b
                 JOIN text_string sl ON sl.text_id = b.service_label_id
                 JOIN text_string tl ON tl.text_id = b.tier_label_id
                 JOIN text_string lim ON lim.text_id = b.limitations_id
                 JOIN cost_string c ON c.cost_id = b.cost_id
                 JOIN chunk ch ON ch.chunk_id = b.chunk_id
                 WHERE b.plan_id = ?"""
        params: list[object] = [plan_id]
        if service_ids:
            sql += f" AND b.service_id IN ({_in(service_ids)})"
            params += list(service_ids)
        if tier_ids:
            sql += f" AND b.tier_id IN ({_in(tier_ids)})"
            params += list(tier_ids)
        cursor = self._db.cursor()
        cursor.row_factory = sqlite3.Row
        rows = cursor.execute(sql, params).fetchall()
        rows.sort(key=lambda r: (r["row_order"], _TIER_ORDER.get(r["tier_id"], len(_TIER_ORDER))))
        facts = []
        for r in rows:
            exact = r["status"] == "exact"
            facts.append(
                Fact(
                    kind="benefit", key=f"benefit:{r['service_id']}:{r['tier_id']}", plan_key=plan_key,
                    label=r["label"], verbatim=r["verbatim"],
                    parsed=json.loads(r["parse_json"]) if exact else None, parse_status=r["status"],
                    tier_id=r["tier_id"], tier_label=r["tier_label"],
                    limitations=r["limitations"], limitations_kind=r["limitations_kind"],
                    carried_note=bool(r["carried_note"]), event_inferred=bool(r["event_inferred"]),
                    citation=self._citation(plan, r["section_path"], _pages(r["pages"])),
                )
            )  # fmt: skip
        return facts

    def question_facts(self, plan_key: str, question_ids: Sequence[str] = ()) -> list[Fact]:
        found = self._plan(plan_key)
        if found is None:
            return []
        plan_id, plan = found
        rows = self._db.execute(
            """SELECT q.question_id, a.text, w.text, q.pages FROM plan_question q
               JOIN text_string a ON a.text_id = q.answer_id JOIN text_string w ON w.text_id = q.why_id
               WHERE q.plan_id = ?""",
            (plan_id,),
        ).fetchall()
        limits = {
            LIMIT_QUESTIONS[kind]: (status, parse)
            for kind, status, parse in self._db.execute(
                """SELECT l.kind, s.status, s.parse_json FROM plan_limit l
                   JOIN limit_string s USING (limit_id) WHERE l.plan_id = ?""",
                (plan_id,),
            )
        }
        by_id = {r[0]: r for r in rows}
        facts = []
        for qid in QUESTION_IDS:
            if qid not in by_id or (question_ids and qid not in question_ids):
                continue
            _, answer, why, pages = by_id[qid]
            status, parse = limits.get(qid, (None, None))
            question = QUESTIONS[qid]
            facts.append(
                Fact(
                    kind="question", key=f"question:{qid}", plan_key=plan_key, label=question,
                    verbatim=answer,
                    parsed=json.loads(parse) if status == "exact" else None, parse_status=status,
                    why_this_matters=why,
                    citation=self._citation(plan, f"Important Questions > {question}", _pages(pages)),
                )
            )  # fmt: skip
        return facts

    def list_facts(self, plan_key: str, list_ids: Sequence[str] = ()) -> list[Fact]:
        found = self._plan(plan_key)
        if found is None:
            return []
        plan_id, plan = found
        rows = self._db.execute(
            """SELECT i.list_id, i.position, t.text, i.pages FROM plan_list_item i
               JOIN text_string t ON t.text_id = i.item_id
               WHERE i.plan_id = ? ORDER BY i.list_id, i.position""",
            (plan_id,),
        ).fetchall()
        facts = []
        for list_id, position, item, pages in rows:
            if list_ids and list_id not in list_ids:
                continue
            title = LIST_TITLES.get(list_id, list_id)
            facts.append(
                Fact(
                    kind="list_item", key=f"list:{list_id}:{position}", plan_key=plan_key, label=title,
                    verbatim=item, citation=self._citation(plan, title, _pages(pages)),
                )
            )  # fmt: skip
        return facts

    def example_facts(self, plan_key: str, example_ids: Sequence[str] = ()) -> list[Fact]:
        found = self._plan(plan_key)
        if found is None:
            return []
        plan_id, plan = found
        rows = self._db.execute(
            """SELECT e.example_id, t.text, e.total_cost, e.total_pay, d.text, e.pages FROM plan_example e
               JOIN text_string t ON t.text_id = e.title_id JOIN text_string d ON d.text_id = e.data_id
               WHERE e.plan_id = ?""",
            (plan_id,),
        ).fetchall()
        order = {"peg": 0, "joe": 1, "mia": 2}
        facts = []
        for example_id, title, total_cost, total_pay, data_json, pages in sorted(
            rows, key=lambda r: order[r[0]]
        ):
            if example_ids and example_id not in example_ids:
                continue
            data = json.loads(data_json)
            lines = [*data["plan_terms"], ["Total Example Cost", total_cost], *data["cost_sharing"]]
            lines += [*data["not_covered"], [data.get("total_pay_label") or "total_pay", total_pay]]
            citation = self._citation(plan, f"Coverage Examples > {title}", _pages(pages))
            for label, value in lines:
                facts.append(
                    Fact(
                        kind="example", key=f"example:{example_id}:{label}", plan_key=plan_key, label=label,
                        verbatim=value, citation=citation,
                    )
                )  # fmt: skip
        return facts

    def chunk_context(self, plan_key: str, chunk_hashes: Sequence[str]) -> dict[str, ChunkContext]:
        found = self._plan(plan_key)
        if found is None or not chunk_hashes:
            return {}
        plan_id, _ = found
        rows = self._db.execute(
            f"""SELECT c.chunk_hash, pc.pages, MIN(pc.position),
                       COALESCE(MAX(b.carried_note), 0), COALESCE(MAX(b.event_inferred), 0)
                FROM plan_chunk pc JOIN chunk c ON c.chunk_id = pc.chunk_id
                LEFT JOIN plan_benefit b ON b.plan_id = pc.plan_id AND b.chunk_id = pc.chunk_id
                WHERE pc.plan_id = ? AND c.chunk_hash IN ({_in(chunk_hashes)})
                GROUP BY c.chunk_hash""",  # noqa: S608 - placeholders only
            (plan_id, *chunk_hashes),
        ).fetchall()
        return {
            h: ChunkContext(
                plan_key=plan_key, chunk_hash=h, pages=_pages(pages), carried_note=bool(carried),
                event_inferred=bool(inferred),
            )
            for h, pages, _, carried, inferred in rows
        }  # fmt: skip
