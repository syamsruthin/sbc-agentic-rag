"""``GET /api/sbc/{plan_key}.pdf``: an SBC PDF for citations (PLAN.md 3.7).

SBCs with a public URL are redirected there. The ~1,400 the portal serves only through its preview form are
fetched from the portal on request, checked against the sha256 the bundle recorded, cached in S3 for a day and
streamed; the insurer stays the source and nothing is hosted long term. SBCs are public documents, so there is
no sign-in, but each IP may fetch at most 30 an hour, to protect the portal.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import RedirectResponse, Response

from sbc_api.deps import ApiState
from sbc_api.portal_preview import PortalError
from sbc_api.routes.session import client_ip
from sbc_aws.ratelimit import Window

log = logging.getLogger(__name__)
router = APIRouter()

PDF_WINDOWS = [Window("pdfhour", 3600, 30)]
PLAN_KEY = r"^\d{4}:[0-9A-Za-z]{1,20}$"


@router.get("/api/sbc/{plan_key}.pdf", response_model=None)
def sbc_pdf(
    request: Request, plan_key: Annotated[str, Path(pattern=PLAN_KEY)]
) -> Response | RedirectResponse:
    api: ApiState = request.app.state.api
    source = api.reader.pdf_source(plan_key)
    if source is None:
        raise HTTPException(status_code=404, detail="no SBC PDF for this plan")
    if not source.preview_only:
        return RedirectResponse(source.catalog_url, status_code=302)
    ip = client_ip(request)
    decision = api.stores.limiter.hit(f"pdf:ip:{ip or 'unknown'}", PDF_WINDOWS)
    if not decision.allowed:
        raise HTTPException(
            status_code=429, detail="too many PDF downloads; please try again later",
            headers={"Retry-After": str(decision.retry_after_s)},
        )  # fmt: skip
    body = api.stores.pdfs.get(plan_key, source.pdf_sha256)
    cached = body is not None
    if body is None:
        try:
            body = api.portal.fetch(source)
        except PortalError as exc:
            raise HTTPException(status_code=502, detail=f"{exc}; try the insurer's SBC search page") from None
        if hashlib.sha256(body).hexdigest() != source.pdf_sha256:
            # The SBC changed upstream since the bundle was built: never serve a document we can't vouch for.
            log.warning("pdf hash mismatch", extra={"event": "pdf_hash_mismatch", "plan_key": plan_key})
            raise HTTPException(
                status_code=502,
                detail="this SBC changed since our data was built; please use the insurer's SBC search page",
            )
        api.stores.pdfs.put(plan_key, source.pdf_sha256, body)
    log.info(
        "sbc pdf", extra={"event": "sbc_pdf", "plan_key": plan_key, "cached": cached, "bytes": len(body)}
    )
    filename = f"SBC-{source.contract_code}-{source.sbc_id}.pdf"
    return Response(
        body,
        media_type="application/pdf",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
