"""Sync a local directory with an S3 prefix by content (sha256), for the offline build image.

    python s3_sync.py down s3://bucket/data data      # fetch what's missing or different locally
    python s3_sync.py up   data s3://bucket/data      # upload what's new or changed

Each uploaded object carries its sha256 in metadata, so unchanged files (including regenerated Markdown with
the same bytes) are skipped both ways, and a changed file of the same size is still noticed. Objects without
the metadata are compared by size. Nothing is ever deleted on either side.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import boto3

CHUNK = 8 * 1024 * 1024


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def _split(url: str) -> tuple[str, str]:
    if not url.startswith("s3://"):
        raise SystemExit(f"not an s3:// URL: {url}")
    bucket, _, prefix = url[5:].partition("/")
    return bucket, prefix.rstrip("/")


def _remote(s3: object, bucket: str, prefix: str) -> dict[str, tuple[int, str | None]]:
    """``{relative key: (size, sha256 or None)}`` for every object under the prefix."""
    out: dict[str, tuple[int, str | None]] = {}
    paginator = s3.get_paginator("list_objects_v2")  # type: ignore[attr-defined]
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/" if prefix else ""):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(prefix) + 1 :] if prefix else obj["Key"]
            out[rel] = (obj["Size"], None)
    return out


def _remote_sha(s3: object, bucket: str, key: str) -> str | None:
    head = s3.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
    return head.get("Metadata", {}).get("sha256")


def down(url: str, local: Path, s3: object) -> int:
    bucket, prefix = _split(url)
    fetched = 0
    for rel, (size, _) in sorted(_remote(s3, bucket, prefix).items()):
        dest = local / rel
        key = f"{prefix}/{rel}" if prefix else rel
        if dest.is_file() and dest.stat().st_size == size:
            sha = _remote_sha(s3, bucket, key)
            if sha is None or sha == _sha256(dest):
                continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(dest))  # type: ignore[attr-defined]
        fetched += 1
    return fetched


def up(local: Path, url: str, s3: object) -> int:
    bucket, prefix = _split(url)
    remote = _remote(s3, bucket, prefix)
    sent = 0
    for path in sorted(p for p in local.rglob("*") if p.is_file()):
        rel = path.relative_to(local).as_posix()
        key = f"{prefix}/{rel}" if prefix else rel
        sha = _sha256(path)
        if rel in remote and remote[rel][0] == path.stat().st_size and _remote_sha(s3, bucket, key) == sha:
            continue
        s3.upload_file(  # type: ignore[attr-defined]
            str(path), bucket, key, ExtraArgs={"Metadata": {"sha256": sha}, "ServerSideEncryption": "AES256"}
        )
        sent += 1
    return sent


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("direction", choices=["down", "up"])
    ap.add_argument("source")
    ap.add_argument("target")
    args = ap.parse_args(argv)
    s3 = boto3.client("s3")
    if args.direction == "down":
        n = down(args.source, Path(args.target), s3)
        print(f"downloaded {n} files from {args.source}", file=sys.stderr)
    else:
        n = up(Path(args.source), args.target, s3)
        print(f"uploaded {n} files to {args.target}", file=sys.stderr)


if __name__ == "__main__":
    main()
