"""A short-lived S3 cache for SBC PDFs fetched from the insurer's portal by ``GET /api/sbc/{plan_key}.pdf``.

Objects live under ``pdf-cache/`` (expired after a day by the bucket's lifecycle rule) and are named by plan
and expected sha256, so a PDF that changed upstream is never served from an old object. Every stored and
returned body is checked against the bundle's sha256.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

log = logging.getLogger(__name__)
DEFAULT_PREFIX = "pdf-cache/"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


class PdfHashMismatchError(ValueError):
    """The PDF's sha256 is not the one the bundle recorded."""


class S3PdfCache:
    def __init__(self, client: S3Client, bucket: str, prefix: str = DEFAULT_PREFIX) -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix

    def key(self, plan_key: str, sha256: str) -> str:
        if not _SHA256.match(sha256):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return f"{self._prefix}{_SAFE.sub('_', plan_key)}-{sha256[:16]}.pdf"

    def get(self, plan_key: str, sha256: str) -> bytes | None:
        """The cached PDF, or None on a miss, a read error or a body that fails the hash check."""
        try:
            body = self._client.get_object(Bucket=self._bucket, Key=self.key(plan_key, sha256))["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                log.warning("pdf cache read failed: %s", exc.response.get("Error", {}).get("Code"))
            return None
        except BotoCoreError as exc:
            log.warning("pdf cache read failed: %s", type(exc).__name__)
            return None
        if hashlib.sha256(body).hexdigest() != sha256:
            log.warning("pdf cache object failed its hash check: %s", plan_key)
            return None
        return body

    def put(self, plan_key: str, sha256: str, data: bytes) -> None:
        """Store ``data`` after checking its hash; raise :class:`PdfHashMismatchError` if it doesn't match."""
        if hashlib.sha256(data).hexdigest() != sha256:
            raise PdfHashMismatchError(f"PDF for {plan_key} doesn't match the bundle's sha256")
        self._client.put_object(
            Bucket=self._bucket,
            Key=self.key(plan_key, sha256),
            Body=data,
            ContentType="application/pdf",
            ServerSideEncryption="AES256",
        )
