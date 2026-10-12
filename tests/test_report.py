from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recaudot import report


class ReportTests(unittest.TestCase):
    def test_report_resolves_all_template_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "results"
            results.mkdir()
            output = root / "report.md"

            security_variant = {
                "attempts": 1_000,
                "attacks_blocked": 1_000,
                "attack_block_rate_pct": 100.0,
                "fraudulent_discounts": 0,
                "p95_latency_ms": 2.0,
                "maximum_reaction_ms": 3.0,
                "elapsed_s": 1.0,
            }
            performance_variant = {
                "validations": 6_000,
                "accepted": 6_000,
                "unprocessed": 0,
                "elapsed_s": 59.0,
                "throughput_per_s": 101.69,
                "throughput_per_min": 6_101.0,
                "p95_latency_ms": 200.0,
                "mean_latency_ms": 120.0,
                "jitter_stddev_ms": 70.0,
                "service_cpu_pct_of_one_core": 20.0,
                "max_batch_size": 25,
                "mean_batch_size": 6.0,
                "max_batch_wait_ms": 50.0,
                "offered_rate_per_min": 6_120.0,
                "http_requests": 480,
            }
            documents = {
                "s1-security.json": {
                    "sample_size_per_variant": 1_000,
                    "without_tactic": {**security_variant, "attacks_blocked": 0},
                    "with_tactic": security_variant,
                    "criteria": {"sample": True, "blocked": True},
                },
                "p1-performance.json": {
                    "without_tactic": {
                        **performance_variant,
                        "max_batch_size": 1,
                        "mean_batch_size": 1.0,
                        "max_batch_wait_ms": 0.0,
                        "http_requests": 12_000,
                    },
                    "with_tactic": performance_variant,
                    "capacity_without_tactic": {
                        **performance_variant,
                        "max_batch_size": 1,
                        "mean_batch_size": 1.0,
                        "max_batch_wait_ms": 0.0,
                        "throughput_per_min": 20_000.0,
                        "http_requests": 12_000,
                    },
                    "capacity_with_tactic": {
                        **performance_variant,
                        "throughput_per_min": 40_000.0,
                    },
                    "capacity_throughput_gain_pct": 100.0,
                    "sla_http_request_reduction_pct": 96.0,
                    "criteria": {
                        "sample": True,
                        "latency": True,
                        "capacity": True,
                    },
                },
                "d1-availability.json": {
                    "injected_failure_after_requests": 50,
                    "without_tactic": {
                        "requests": 200,
                        "successful": 50,
                        "availability_pct": 25.0,
                        "recovery_ms": None,
                        "gateway_switch_ms": None,
                        "confirmed_transactions_lost": 0,
                    },
                    "with_tactic": {
                        "requests": 200,
                        "successful": 200,
                        "availability_pct": 100.0,
                        "recovery_ms": 10.0,
                        "gateway_switch_ms": 2.0,
                        "confirmed_transactions_lost": 0,
                    },
                    "criteria": {"recovery": True, "loss": True},
                },
                "tradeoff-security-performance.json": {
                    "sample_size_per_variant": 1_000,
                    "without_authentication": {
                        "validations": 1_000,
                        "p95_latency_ms": 1.0,
                        "mean_latency_ms": 0.8,
                        "accepted": 1_000,
                    },
                    "with_challenge_response": {
                        "validations": 1_000,
                        "p95_latency_ms": 2.0,
                        "mean_latency_ms": 1.5,
                        "accepted": 1_000,
                    },
                    "p95_increase_ms": 1.0,
                    "p95_increase_pct": 100.0,
                    "cost_acceptable_for_v5": True,
                },
            }
            for name, document in documents.items():
                (results / name).write_text(json.dumps(document), encoding="utf-8")

            with (
                patch.object(report, "RESULTS", results),
                patch.object(report, "OUTPUT", output),
            ):
                report.main()

            rendered = output.read_text(encoding="utf-8")
            self.assertNotIn("{{", rendered)
            self.assertIn("evidencia completa", rendered)


if __name__ == "__main__":
    unittest.main()
