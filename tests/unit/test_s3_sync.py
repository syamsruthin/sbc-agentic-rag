"""The build image's S3 sync (infra/docker/s3_sync.py) on moto: by content, both ways, never deleting."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import boto3
import pytest
from moto import mock_aws

SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "docker" / "s3_sync.py"
BUCKET = "test-data"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s3_sync", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = _load()


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _tree(root: Path, files: dict[str, bytes]) -> None:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(body)


def test_up_then_down_round_trips_and_records_the_hash(s3: Any, tmp_path: Path) -> None:
    _tree(tmp_path / "src", {"raw/a.pdf": b"%PDF a", "markdown/x/b.md": b"# b"})
    assert sync.up(tmp_path / "src", f"s3://{BUCKET}/data", s3) == 2
    head = s3.head_object(Bucket=BUCKET, Key="data/raw/a.pdf")
    assert head["Metadata"]["sha256"] == sync._sha256(tmp_path / "src" / "raw" / "a.pdf")
    assert head["ServerSideEncryption"] == "AES256"
    assert sync.down(f"s3://{BUCKET}/data", tmp_path / "dst", s3) == 2
    assert (tmp_path / "dst" / "markdown" / "x" / "b.md").read_bytes() == b"# b"


def test_unchanged_files_are_skipped_and_same_size_changes_are_noticed(s3: Any, tmp_path: Path) -> None:
    src = tmp_path / "src"
    _tree(src, {"a.md": b"aaaa", "b.md": b"bbbb"})
    sync.up(src, f"s3://{BUCKET}/data", s3)
    assert sync.up(src, f"s3://{BUCKET}/data", s3) == 0
    (src / "a.md").write_bytes(b"AAAA")  # same size, new content
    assert sync.up(src, f"s3://{BUCKET}/data", s3) == 1
    dst = tmp_path / "dst"
    _tree(dst, {"a.md": b"aaaa", "b.md": b"bbbb"})  # an old local copy
    assert sync.down(f"s3://{BUCKET}/data", dst, s3) == 1
    assert (dst / "a.md").read_bytes() == b"AAAA"


def test_nothing_is_deleted_either_way(s3: Any, tmp_path: Path) -> None:
    _tree(tmp_path / "one", {"keep.md": b"1"})
    sync.up(tmp_path / "one", f"s3://{BUCKET}/data", s3)
    _tree(tmp_path / "two", {"other.md": b"2"})
    sync.up(tmp_path / "two", f"s3://{BUCKET}/data", s3)
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]}
    assert keys == {"data/keep.md", "data/other.md"}
    sync.down(f"s3://{BUCKET}/data", tmp_path / "two", s3)
    assert (tmp_path / "two" / "other.md").exists()


def test_an_empty_prefix_downloads_nothing(s3: Any, tmp_path: Path) -> None:
    assert sync.down(f"s3://{BUCKET}/missing", tmp_path / "dst", s3) == 0


def test_urls_must_be_s3() -> None:
    with pytest.raises(SystemExit, match="s3://"):
        sync._split("https://example.com/x")
    assert sync._split("s3://bucket/a/b/") == ("bucket", "a/b")
