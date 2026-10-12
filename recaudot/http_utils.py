"""Small JSON-over-HTTP helpers shared by services and simulators."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class TransportError(RuntimeError):
    pass


def post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    timeout_s: float = 2.0,
) -> tuple[dict[str, Any], float]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter_ns()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            parsed = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise TransportError(str(exc)) from exc
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return parsed, elapsed_ms

