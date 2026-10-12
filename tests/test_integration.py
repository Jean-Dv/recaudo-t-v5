from __future__ import annotations

import tempfile
import unittest
import socket
from pathlib import Path

from recaudot.http_utils import post_json
from recaudot.runtime import (
    free_port,
    start_gateway,
    start_service,
    stop_process,
)
from recaudot.simulator import CardSimulator
from recaudot.store import Store


class ServiceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.close()
        except PermissionError as exc:
            self.skipTest(f"local sockets are disabled by the execution sandbox: {exc}")
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "integration.db"
        store = Store(self.db_path)
        store.initialize()
        store.seed_cards(["CARD-1", "CARD-2"])
        self.port = free_port()
        self.process = start_service(
            port=self.port,
            db_path=self.db_path,
            node="primary",
            auth_enabled=True,
        )
        self.extra_processes = []
        self.url = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        for process in reversed(getattr(self, "extra_processes", [])):
            stop_process(process)
        stop_process(self.process)
        self.temporary.cleanup()

    def test_real_process_accepts_legitimate_card(self) -> None:
        response, latency_ms = CardSimulator.legitimate("CARD-1").validate(
            self.url, fare_cents=100
        )
        self.assertTrue(response["accepted"])
        self.assertGreater(latency_ms, 0)

    def test_real_process_rejects_clone(self) -> None:
        response, _ = CardSimulator.clone("CARD-2").validate(
            self.url, fare_cents=100
        )
        self.assertFalse(response["accepted"])
        self.assertEqual("invalid_proof", response["reason"])

    def test_batch_endpoint_processes_multiple_cards(self) -> None:
        cards = [CardSimulator.legitimate("CARD-1"), CardSimulator.legitimate("CARD-2")]
        challenges, _ = post_json(
            self.url,
            "/challenges/batch",
            {"card_ids": [card.card_id for card in cards]},
        )
        validations = []
        for card, challenge in zip(cards, challenges["challenges"], strict=True):
            validations.append(
                {
                    **card.proof_for(challenge),
                    "operation_id": f"batch-{card.card_id}",
                    "fare_cents": 100,
                }
            )
        response, _ = post_json(
            self.url, "/validations/batch", {"validations": validations}
        )
        self.assertTrue(all(result["accepted"] for result in response["results"]))

    def test_gateway_switches_to_passive_node_after_real_failure(self) -> None:
        standby_port = free_port()
        gateway_port = free_port()
        standby = start_service(
            port=standby_port,
            db_path=self.db_path,
            node="standby",
            auth_enabled=True,
        )
        gateway = start_gateway(
            port=gateway_port,
            primary_url=self.url,
            standby_url=f"http://127.0.0.1:{standby_port}",
        )
        self.extra_processes.extend([standby, gateway])
        gateway_url = f"http://127.0.0.1:{gateway_port}"

        first, _ = CardSimulator.legitimate("CARD-1").validate(gateway_url)
        self.assertEqual("primary", first["node"])
        card = CardSimulator.legitimate("CARD-2")
        challenge, _ = post_json(
            gateway_url, "/challenges", {"card_id": card.card_id}
        )
        stop_process(self.process)
        second, _ = post_json(
            gateway_url,
            "/validations",
            {
                **card.proof_for(challenge),
                "operation_id": "failover-operation",
                "fare_cents": 0,
            },
        )
        self.assertTrue(second["accepted"])
        self.assertEqual("standby", second["node"])
        self.assertGreater(second["_gateway"]["failover_ms"], 0)


if __name__ == "__main__":
    unittest.main()
