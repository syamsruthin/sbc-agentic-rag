"""Cloudflare Turnstile: the public bot's check that a person, not a script, started the session."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5.0


class TurnstileUnavailableError(RuntimeError):
    """Cloudflare couldn't be reached or answered oddly; the caller should say "try again"."""


class TurnstileVerifier:
    def __init__(self, secret: str, *, client: httpx.Client | None = None) -> None:
        self._secret = secret
        self._client = client if client is not None else httpx.Client(timeout=TIMEOUT_SECONDS)

    def verify(self, token: str, remote_ip: str | None = None) -> bool:
        """True when Cloudflare accepts ``token``; raises :class:`TurnstileUnavailableError` on an outage."""
        form = {"secret": self._secret, "response": token}
        if remote_ip:
            form["remoteip"] = remote_ip
        try:
            response = self._client.post(SITEVERIFY_URL, data=form)
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("turnstile check failed: %s", type(exc).__name__)
            raise TurnstileUnavailableError("the bot check is unavailable") from exc
        if not result.get("success"):
            log.info(
                "turnstile refused", extra={"event": "turnstile_refused", "codes": result.get("error-codes")}
            )
            return False
        return True
