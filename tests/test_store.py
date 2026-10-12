from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from recaudot.crypto import calculate_proof, test_key_for_card
from recaudot.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "test.db"
        self.store = Store(self.db_path)
        self.store.initialize()
        self.store.seed_cards(["CARD-1"], balance_cents=10_000)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_legitimate_challenge_response_is_accepted(self) -> None:
        challenge = self.store.issue_challenge("CARD-1")
        proof = calculate_proof(
            test_key_for_card("CARD-1"), "CARD-1", challenge["nonce"], 1
        )
        result = self.store.validate(
            card_id="CARD-1",
            operation_id=uuid.uuid4().hex,
            fare_cents=300,
            node="primary",
            auth_enabled=True,
            challenge_id=challenge["challenge_id"],
            counter=1,
            proof=proof,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(9_700, result.balance_cents)

    def test_clone_is_rejected_and_blocked(self) -> None:
        challenge = self.store.issue_challenge("CARD-1")
        proof = calculate_proof(
            b"wrong-key", "CARD-1", challenge["nonce"], 1
        )
        result = self.store.validate(
            card_id="CARD-1",
            operation_id=uuid.uuid4().hex,
            fare_cents=300,
            node="primary",
            auth_enabled=True,
            challenge_id=challenge["challenge_id"],
            counter=1,
            proof=proof,
        )
        self.assertFalse(result.accepted)
        self.assertEqual("invalid_proof", result.reason)
        self.assertEqual(1, self.store.counts()["blocked"])

    def test_operation_id_makes_retry_idempotent(self) -> None:
        operation_id = uuid.uuid4().hex
        first = self.store.validate(
            card_id="CARD-1",
            operation_id=operation_id,
            fare_cents=300,
            node="primary",
            auth_enabled=False,
        )
        second = self.store.validate(
            card_id="CARD-1",
            operation_id=operation_id,
            fare_cents=300,
            node="standby",
            auth_enabled=False,
        )
        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertTrue(second.idempotent_replay)
        self.assertEqual(first.balance_cents, second.balance_cents)


if __name__ == "__main__":
    unittest.main()

