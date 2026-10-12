"""Card simulators used by scenario experiments."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .crypto import calculate_proof, test_key_for_card
from .http_utils import post_json


@dataclass
class CardSimulator:
    card_id: str
    key: bytes
    counter: int = 0

    @classmethod
    def legitimate(cls, card_id: str) -> "CardSimulator":
        return cls(card_id=card_id, key=test_key_for_card(card_id))

    @classmethod
    def clone(cls, card_id: str) -> "CardSimulator":
        return cls(card_id=card_id, key=b"copied-visible-data-without-secret-key")

    def proof_for(self, challenge: dict[str, Any]) -> dict[str, Any]:
        self.counter += 1
        return {
            "card_id": self.card_id,
            "challenge_id": challenge["challenge_id"],
            "counter": self.counter,
            "proof": calculate_proof(
                self.key,
                self.card_id,
                challenge["nonce"],
                self.counter,
            ),
        }

    def validate(
        self,
        base_url: str,
        *,
        fare_cents: int = 0,
        operation_id: str | None = None,
        authenticate: bool = True,
        timeout_s: float = 2.0,
    ) -> tuple[dict[str, Any], float]:
        elapsed_ms = 0.0
        payload: dict[str, Any] = {
            "card_id": self.card_id,
            "operation_id": operation_id or uuid.uuid4().hex,
            "fare_cents": fare_cents,
        }
        if authenticate:
            challenge, challenge_ms = post_json(
                base_url,
                "/challenges",
                {"card_id": self.card_id},
                timeout_s,
            )
            elapsed_ms += challenge_ms
            payload.update(self.proof_for(challenge))
        response, validation_ms = post_json(
            base_url, "/validations", payload, timeout_s
        )
        return response, elapsed_ms + validation_ms

