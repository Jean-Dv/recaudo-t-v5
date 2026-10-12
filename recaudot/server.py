"""Validation service exposing individual and batch protocol operations."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .store import Store


def _make_handler(store: Store, node: str, auth_enabled: bool):
    class ValidationHandler(BaseHTTPRequestHandler):
        server_version = "RecaudoTValidation/0.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            try:
                body = self._read_json()
                if self.path == "/challenges":
                    result = self._challenge(body)
                elif self.path == "/challenges/batch":
                    result = self._challenge_batch(body)
                elif self.path == "/validations":
                    result = self._validate(body)
                elif self.path == "/validations/batch":
                    result = self._validate_batch(body)
                else:
                    self._send_json(404, {"error": "not_found"})
                    return
                self._send_json(200, result)
            except (KeyError, PermissionError) as exc:
                self._send_json(422, {"error": str(exc).strip("'")})
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": "invalid_request", "detail": str(exc)})
            except Exception as exc:  # service boundary: turn faults into evidence
                self._send_json(500, {"error": "internal_error", "detail": str(exc)})

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 10_000_000:
                raise ValueError("invalid body length")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise TypeError("JSON body must be an object")
            return value

        def _challenge(self, body: dict[str, Any]) -> dict[str, Any]:
            challenge = store.issue_challenge(str(body["card_id"]))
            return {**challenge, "card_id": str(body["card_id"]), "node": node}

        def _challenge_batch(self, body: dict[str, Any]) -> dict[str, Any]:
            card_ids = body["card_ids"]
            if not isinstance(card_ids, list) or not card_ids:
                raise ValueError("card_ids must be a non-empty list")
            challenges = []
            for card_id in card_ids:
                challenge = store.issue_challenge(str(card_id))
                challenges.append(
                    {**challenge, "card_id": str(card_id), "node": node}
                )
            return {"challenges": challenges, "node": node}

        def _validate(self, body: dict[str, Any]) -> dict[str, Any]:
            result = store.validate(
                card_id=str(body["card_id"]),
                operation_id=str(body["operation_id"]),
                fare_cents=int(body.get("fare_cents", 0)),
                node=node,
                auth_enabled=auth_enabled,
                challenge_id=body.get("challenge_id"),
                counter=int(body["counter"]) if body.get("counter") is not None else None,
                proof=body.get("proof"),
            )
            return {**result.as_dict(), "node": node}

        def _validate_batch(self, body: dict[str, Any]) -> dict[str, Any]:
            validations = body["validations"]
            if not isinstance(validations, list) or not validations:
                raise ValueError("validations must be a non-empty list")
            results = [self._validate(validation) for validation in validations]
            return {"results": results, "node": node}

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

    return ValidationHandler


def serve(
    *,
    host: str,
    port: int,
    db_path: str | Path,
    node: str,
    auth_enabled: bool,
) -> None:
    store = Store(db_path)
    store.initialize()
    server = HTTPServer((host, port), _make_handler(store, node, auth_enabled))
    server.serve_forever()

