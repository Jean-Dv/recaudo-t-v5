"""Cryptographic protocol helpers.

The project implements the challenge/response protocol, but deliberately uses
the standard library implementation of HMAC-SHA256 instead of inventing a
cryptographic primitive.
"""

from __future__ import annotations

import hashlib
import hmac


def test_key_for_card(card_id: str) -> bytes:
    """Return deterministic, fictional key material used only by simulators."""

    return hashlib.sha256(f"recaudot-test-key:{card_id}".encode()).digest()


def response_message(card_id: str, nonce: str, counter: int) -> bytes:
    return f"{card_id}:{nonce}:{counter}".encode()


def calculate_proof(key: bytes, card_id: str, nonce: str, counter: int) -> str:
    return hmac.new(
        key,
        response_message(card_id, nonce, counter),
        hashlib.sha256,
    ).hexdigest()


def verify_proof(
    key: bytes,
    card_id: str,
    nonce: str,
    counter: int,
    supplied_proof: str,
) -> bool:
    expected = calculate_proof(key, card_id, nonce, counter)
    return hmac.compare_digest(expected, supplied_proof)

