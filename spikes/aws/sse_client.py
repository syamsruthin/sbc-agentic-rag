"""Spike 2 client: POST to the spike SSE endpoint and record when each event arrives.

    uv run python -m spikes.aws.sse_client http://localhost:8080
    uv run python -m spikes.aws.sse_client https://dxxxx.cloudfront.net     # after deploying the spike stack

PASS means the events arrived spread out over time (streamed), not all at the end (buffered), and that the
server saw a correct ``x-amz-content-sha256`` header for the POST body.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time

import httpx


def main() -> None:
    base = sys.argv[1].rstrip("/")
    body = json.dumps({"message": "What's the deductible on BTCU8?"}).encode()
    headers = {"content-type": "application/json", "x-amz-content-sha256": hashlib.sha256(body).hexdigest()}
    arrivals: list[tuple[str, float]] = []
    answer: dict[str, object] = {}
    started = time.perf_counter()
    with (
        httpx.Client(timeout=60) as client,
        client.stream("POST", f"{base}/api/spike/chat", content=body, headers=headers) as resp,
    ):
        print("status", resp.status_code, "content-type", resp.headers.get("content-type"))
        event = ""
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                arrivals.append((event, time.perf_counter() - started))
                if event == "answer":
                    answer = json.loads(line[6:])
    for name, t in arrivals:
        print(f"{t:6.2f}s  {name}")
    statuses = [t for name, t in arrivals if name == "status"]
    spread = statuses[-1] - statuses[0] if len(statuses) > 1 else 0.0
    streamed = len(statuses) == 5 and spread >= 3.0
    print("answer:", answer)
    ok = streamed and answer.get("sha256_header_ok") is True
    print(f"status events spread over {spread:.2f}s -> {'streamed' if streamed else 'BUFFERED'}")
    print("SPIKE 2:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
