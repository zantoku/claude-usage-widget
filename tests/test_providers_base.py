"""Tests for the provider abstraction primitives."""

from __future__ import annotations

import math
import unittest

from claude_usage.providers.base import Meter, ProviderSnapshot


class TestMeter(unittest.TestCase):
    def test_utilization_clamped_high(self) -> None:
        self.assertEqual(Meter("k", "L", utilization=1.7).utilization, 1.0)

    def test_utilization_clamped_low(self) -> None:
        self.assertEqual(Meter("k", "L", utilization=-0.3).utilization, 0.0)

    def test_nan_and_inf_become_zero(self) -> None:
        self.assertEqual(Meter("k", "L", utilization=float("nan")).utilization, 0.0)
        self.assertEqual(Meter("k", "L", utilization=float("inf")).utilization, 0.0)

    def test_bad_type_becomes_zero(self) -> None:
        self.assertEqual(Meter("k", "L", utilization="oops").utilization, 0.0)  # type: ignore[arg-type]

    def test_valid_value_preserved(self) -> None:
        m = Meter("premium", "Premium", utilization=0.42, reset_ts=123, detail="x")
        self.assertAlmostEqual(m.utilization, 0.42)
        self.assertEqual(m.reset_ts, 123)
        self.assertEqual(m.detail, "x")


class TestProviderSnapshot(unittest.TestCase):
    def test_to_public_dict_shape(self) -> None:
        snap = ProviderSnapshot(
            "copilot", "COPILOT",
            meters=[Meter("premium", "Premium", utilization=0.5, detail="5/10")],
            error="", available=True, rich={"plan": "business"},
        )
        d = snap.to_public_dict()
        self.assertEqual(d["provider_id"], "copilot")
        self.assertEqual(d["display_name"], "COPILOT")
        self.assertTrue(d["available"])
        self.assertEqual(len(d["meters"]), 1)
        self.assertEqual(d["meters"][0]["key"], "premium")
        # rich is provider-internal and must NOT leak into the public dict.
        self.assertNotIn("rich", d)

    def test_to_public_dict_empty_meters(self) -> None:
        snap = ProviderSnapshot("copilot", "COPILOT", available=False)
        d = snap.to_public_dict()
        self.assertEqual(d["meters"], [])
        self.assertFalse(d["available"])


if __name__ == "__main__":
    unittest.main()
