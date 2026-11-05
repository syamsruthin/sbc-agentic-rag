"""WP7: ``GET /api/sbc/{plan_key}.pdf``: preview-only SBCs fetched from the portal (mocked), checked against
the bundle's sha256, cached, and limited per IP; public SBCs redirected."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from sbc_api.app import create_app
from sbc_api.portal_preview import PortalPreviewClient, preview_form
from tests.unit.api.conftest import Harness
from tests.unit.test_bundle import make_bundle, manifest_rows
from tests.unit.test_tools import GOLD, SILVER

PDF = b"%PDF-1.7\n% a tiny stand-in, not a real SBC\n%%EOF\n"
SHA = hashlib.sha256(PDF).hexdigest()
URL = "/api/sbc/2027:100000000001.pdf"


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    rows = manifest_rows()
    rows[0].update(portal="anthem", sha256=SHA)  # the Silver plan: preview-only, with this PDF's real hash
    return make_bundle(tmp_path, rows=rows)


class Portal:
    """A fake insurer portal: the search page sets a cookie, previewDocument needs it and returns ``body``."""

    def __init__(self, body: bytes = PDF, *, down: bool = False) -> None:
        self.body, self.down = body, down
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("portal down")
        form = dict(httpx.QueryParams(request.content.decode())) if request.content else {}
        self.requests.append((request.method, request.url.path, form))
        if request.url.path == "/dps/searchPage":
            return httpx.Response(200, headers={"set-cookie": "JSESSIONID=abc; Path=/"})
        if request.url.path == "/dps/search/previewDocument":
            assert "JSESSIONID=abc" in request.headers.get("cookie", "")
            return httpx.Response(200, content=self.body, headers={"content-type": "application/pdf"})
        return httpx.Response(200, text="<html></html>")


def client(harness: Harness, portal: Portal) -> TestClient:
    state = harness.state()
    state.portal = PortalPreviewClient(transport=httpx.MockTransport(portal))
    return TestClient(create_app(state), follow_redirects=False)


@pytest.fixture
def harness(bundle: Path) -> Harness:
    return Harness(bundle)


def test_a_preview_only_sbc_is_fetched_checked_cached_and_streamed(harness: Harness) -> None:
    portal = Portal()
    api = client(harness, portal)
    r = api.get(URL)
    assert r.status_code == 200
    assert r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'inline; filename="SBC-ZZ01-100000000001.pdf"'
    assert [(m, p) for m, p, _ in portal.requests] == [
        ("GET", "/dps/"), ("POST", "/dps/searchPage"), ("POST", "/dps/search/previewDocument"),
    ]  # fmt: skip
    assert portal.requests[1][2] == {"stateCode": "XX", "sdbcFlag": "false", "marketUnit": "Individual"}
    assert api.get(URL).content == PDF
    assert len(portal.requests) == 3  # the second request came from the cache


def test_the_form_matches_what_the_ingest_downloader_sends(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sbc_ingest.portal as ingest

    sent: dict[str, Any] = {}

    class FakeSession:
        def post(self, url: str, data: dict[str, str], timeout: int) -> None:
            sent.update(data)

    monkeypatch.setattr(ingest, "_portal_session", lambda row: FakeSession())
    row = {**manifest_rows()[0], "portal": "anthem"}
    ingest._preview_post(row)
    source = harness.state().reader.pdf_source(SILVER)
    assert source is not None
    assert preview_form(source) == sent


def test_a_pdf_that_changed_upstream_is_never_served(harness: Harness) -> None:
    api = client(harness, Portal(PDF + b"% a newer version\n"))
    r = api.get(URL)
    assert r.status_code == 502
    assert "changed" in r.json()["detail"]
    assert harness.stores.pdfs.get(SILVER, SHA) is None


@pytest.mark.parametrize("portal", [Portal(down=True), Portal(b"<html>maintenance</html>")])
def test_portal_failures_are_reported_not_served(harness: Harness, portal: Portal) -> None:
    assert client(harness, portal).get(URL).status_code == 502


def test_public_sbcs_are_redirected_to_the_insurer(harness: Harness) -> None:
    portal = Portal()
    r = client(harness, portal).get(f"/api/sbc/{GOLD}.pdf")
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://portal.example/dpsdeeplink/")
    assert portal.requests == []


def test_unknown_or_malformed_plans(harness: Harness) -> None:
    api = client(harness, Portal())
    assert api.get("/api/sbc/2027:999999999999.pdf").status_code == 404
    assert api.get("/api/sbc/not a key.pdf").status_code == 422


def test_each_ip_may_fetch_30_an_hour(harness: Harness) -> None:
    api = client(harness, Portal())
    headers = {"cloudfront-viewer-address": "198.51.100.4:1234"}
    assert all(api.get(URL, headers=headers).status_code == 200 for _ in range(30))
    r = api.get(URL, headers=headers)
    assert r.status_code == 429
    assert 1 <= int(r.headers["retry-after"]) <= 3600
    assert api.get(URL, headers={"cloudfront-viewer-address": "198.51.100.5:1"}).status_code == 200


def test_citations_of_preview_only_plans_open_through_this_route(harness: Harness) -> None:
    api = client(harness, Portal())
    plan = api.get(f"/api/broker/plans/{SILVER}").json()
    assert plan["source_url"] == "/api/sbc/2027%3A100000000001.pdf"
    assert api.get(plan["source_url"]).content == PDF  # the URL-encoded key works too
