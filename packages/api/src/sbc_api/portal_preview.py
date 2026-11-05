"""Fetches one SBC PDF through the insurer portal's preview form, for SBCs with no public PDF URL.

This mirrors ``sbc_ingest.portal._preview_post`` (the form the portal's download button submits). The runtime
can't import ``sbc_ingest`` (a build-time package), and that module is kept byte-for-byte as moved, so the few
lines are repeated here; a test checks the two send the same form fields.
"""

from __future__ import annotations

import logging

import httpx

from sbc_core.types import PdfSource

log = logging.getLogger(__name__)

HOSTS = {"anthem": "sbc.anthem.com", "wellpoint": "sbc.wellpoint.com"}
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) plan-info-chatbot/0.1"
TIMEOUT_SECONDS = 30.0
MAX_PDF_BYTES = 20 * 1024 * 1024


class PortalError(RuntimeError):
    """The portal couldn't be reached or didn't return a PDF."""


def preview_form(source: PdfSource) -> dict[str, str]:
    """The previewDocument form for one SBC, field for field as ``sbc-portal download`` sends it."""
    return {
        "contractCode": source.contract_code, "planName": source.plan_name, "state": source.state,
        "planType": source.plan_type, "marketUnit": source.market_unit,
        "coverageEffectiveDate": source.coverage_period, "exchangeIndicator": "undefined",
        "networkName": source.network, "language": source.language,
        "planStatus": source.plan_status or "null",
        "docSysId": source.doc_sys_id, "sbcId": source.sbc_id, "dpsUniqueId": source.dps_unique_id,
        "brand": source.brand, "productId": "-", "replicationFlag": "OFF", "epdgReplicationFlag": "OFF",
        "total": "1", "flag": "download", "sourceSystem": source.source_system, "docType": "SBC",
    }  # fmt: skip


class PortalPreviewClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def fetch(self, source: PdfSource) -> bytes:
        """The PDF bytes (not yet hash-checked). Raises :class:`PortalError`."""
        host = HOSTS.get(source.portal)
        if host is None:
            raise PortalError(f"unknown portal {source.portal!r}")
        # A fresh cookie session per fetch: the portal ties previewDocument to a search page opened for the
        # same state and market.
        with httpx.Client(
            transport=self._transport, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:  # fmt: skip
            try:
                client.get(f"https://{host}/dps/").raise_for_status()
                client.post(
                    f"https://{host}/dps/searchPage",
                    data={"stateCode": source.state, "sdbcFlag": "false", "marketUnit": source.market_unit},
                ).raise_for_status()
                response = client.post(
                    f"https://{host}/dps/search/previewDocument", data=preview_form(source)
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning(
                    "portal fetch failed: %s", type(exc).__name__, extra={"plan_key": source.plan_key}
                )
                raise PortalError("the insurer's site didn't respond") from exc
        body = response.content
        if len(body) > MAX_PDF_BYTES or not body.startswith(b"%PDF"):
            raise PortalError("the insurer's site didn't return a PDF")
        return body
