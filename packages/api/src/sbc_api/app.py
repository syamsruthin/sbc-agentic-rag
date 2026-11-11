"""The FastAPI app (docs/aws/MIGRATION_PLAN.md WP4).

Run it with the factory, which builds everything once per process (a Lambda cold start):

    uvicorn --factory sbc_api.app:create_app --port 8080

On Lambda the container runs that command behind the Lambda Web Adapter, with response streaming.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from sbc_api import logging as api_logging
from sbc_api.deps import ApiState, build_state
from sbc_api.routes import admin, chat, feedback, health, plans, sbc_pdf, session

log = logging.getLogger("sbc_api")
REQUEST_ID_HEADERS = ("x-amzn-request-id", "x-request-id")
INTERNAL_ERROR = "Something went wrong. Please try again."


def create_app(state: ApiState | None = None) -> FastAPI:
    if state is None:
        api_logging.configure()
        state = build_state()
        logging.getLogger().setLevel(state.env.log_level)
        log.info("ready", extra={"event": "init", **state.init})
    app = FastAPI(title="sbc-agentic-rag API", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    app.state.api = state
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(plans.router)
    app.include_router(session.router)
    app.include_router(feedback.router)
    app.include_router(admin.router)
    app.include_router(sbc_pdf.router)

    @app.middleware("http")
    async def request_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = next((request.headers[h] for h in REQUEST_ID_HEADERS if request.headers.get(h)), None)
        request.state.request_id = rid or uuid.uuid4().hex
        started = time.perf_counter()

        def route() -> str:
            return str(getattr(request.scope.get("route"), "path", request.url.path))

        try:
            response = await call_next(request)
        except Exception:
            # Every failure keeps its request ID: in one JSON log line with the traceback (the ApiErrors alarm
            # watches ERROR lines), and in the response the apps show under "Report a problem" (WP12).
            log.exception(
                "unhandled error",
                extra={"event": "unhandled_error", "request_id": request.state.request_id, "route": route()},
            )
            response = JSONResponse(
                {"detail": INTERNAL_ERROR, "request_id": request.state.request_id}, status_code=500
            )
        response.headers["x-request-id"] = request.state.request_id
        log.info(
            "request",
            extra={
                "event": "request",
                "request_id": request.state.request_id,
                "method": request.method,
                "route": route(),
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return response

    return app
