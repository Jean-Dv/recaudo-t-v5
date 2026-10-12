from __future__ import annotations

import unittest

from recaudot.experiments import adaptive_batch_size, percentile


class MetricTests(unittest.TestCase):
    def test_nearest_rank_percentile(self) -> None:
        values = [float(value) for value in range(1, 101)]
        self.assertEqual(95.0, percentile(values, 95))

    def test_percentile_does_not_depend_on_input_order(self) -> None:
        self.assertEqual(4.0, percentile([5.0, 1.0, 4.0, 2.0, 3.0], 80))

    def test_adaptive_batch_limits_wait_under_scenario_load(self) -> None:
        self.assertEqual(
            6,
            adaptive_batch_size(
                remaining=100,
                max_batch_size=25,
                offered_rate_per_s=102.0,
                max_batch_wait_ms=50.0,
            ),
        )

    def test_capacity_test_uses_full_batches(self) -> None:
        self.assertEqual(
            25,
            adaptive_batch_size(
                remaining=100,
                max_batch_size=25,
                offered_rate_per_s=None,
                max_batch_wait_ms=50.0,
            ),
        )


if __name__ == "__main__":
    unittest.main()
