"""Passive failover gateway.

The gateway never polls or pings the validation nodes. It starts with the
primary and changes to the passive node only after a real request observes a
transport failure. This is intentionally different from active redundancy.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


def _append_event(path: Path | None, event: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(event, sort_keys=True) + "\n")


def _make_handler(
    primary_url: str,
    standby_url: str,
    timeout_s: float,
    event_log: Path | None,
):
    state = {"active_url": primary_url, "active_name": "primary"}

    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "RecaudoTGateway/0.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            request_started = time.perf_counter_ns()
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            failover_ms = 0.0
            try:
                status, response_body = self._forward(state["active_url"], body)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if state["active_name"] == "standby":
                    self._send(503, {"error": "all_backends_unavailable"})
                    return
                state["active_url"] = standby_url
                state["active_name"] = "standby"
                try:
                    status, response_body = self._forward(standby_url, body)
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    self._send(503, {"error": "all_backends_unavailable"})
                    return
                # Includes failure detection and the successful retry on standby.
                failover_ms = (time.perf_counter_ns() - request_started) / 1_000_000
                _append_event(
                    event_log,
                    {
                        "event": "failover",
                        "failure": str(exc),
                        "failover_ms": failover_ms,
                        "new_active": "standby",
                        "timestamp_ns": time.time_ns(),
                    },
                )

            try:
                payload = json.loads(response_body)
                if isinstance(payload, dict):
                    payload["_gateway"] = {
                        "active_backend": state["active_name"],
                        "failover_ms": failover_ms,
                    }
                    self._send(status, payload)
                else:
                    self._send(status, {"result": payload})
            except json.JSONDecodeError:
                self._send(502, {"error": "invalid_backend_response"})

        def _forward(self, base_url: str, body: bytes) -> tuple[int, bytes]:
            request = urllib.request.Request(
                f"{base_url.rstrip('/')}{self.path}",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout_s) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

    return GatewayHandler


def serve(
    *,
    host: str,
    port: int,
    primary_url: str,
    standby_url: str,
    timeout_s: float,
    event_log: str | Path | None,
) -> None:
    log_path = Path(event_log) if event_log else None
    server = HTTPServer(
        (host, port),
        _make_handler(primary_url, standby_url, timeout_s, log_path),
    )
    server.serve_forever()
