"""Focused R2 regression: native usage, cumulative dedup, and shared budget gate."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import unittest

from m1_checklist_loop_r2 import APP, budget_state, parse_native_usage


class R2UsageCheck(unittest.TestCase):
    def test_native_usage_cumulative_dedup_and_budget(self):
        events = [json.loads(line) for line in (APP / "runs/m1-checklist-loop-1030/muse-events.jsonl").read_text().splitlines() if line.strip()]
        event = next(row for row in events if row.get("payload_type") == "run.usage")
        repeated_snapshot = copy.deepcopy(event)
        repeated_snapshot["id"] = "same-counters-new-event"
        parsed = parse_native_usage("\n".join(json.dumps(e) for e in (event, event, repeated_snapshot)))
        self.assertEqual(len(parsed["records"]), 2)
        self.assertEqual(parsed["observed_usage_cost_usd"], "0.05037995")
        self.assertEqual(parsed["latest_models"][0]["cached_tokens"], 57058)
        self.assertEqual(parsed["children_included"], [False, False])
        self.assertIsNone(parsed["total_cost_usd"])
        self.assertFalse(parsed["auxiliary_usage_known"])
        early = copy.deepcopy(event)
        early["id"] = "earlier-cumulative"
        early["payload"]["models"][0].update(input_tokens=1000, cached_tokens=0, output_tokens=100, calls=1)
        parsed = parse_native_usage("\n".join(json.dumps(e) for e in (early, event)))
        self.assertEqual(parsed["observed_usage_cost_usd"], "0.05037995")
        backward = parse_native_usage("\n".join(json.dumps(e) for e in (event, early)))
        self.assertIsNone(backward["observed_usage_cost_usd"])
        self.assertTrue(backward["ambiguities"])
        self.assertIsNone(parse_native_usage("")["observed_usage_cost_usd"])
        allowed = budget_state(Decimal(0), Decimal("0.15"))
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["projected_occupied_usd"], "0.30037995")
        self.assertFalse(budget_state(None, Decimal("0.03"))["allowed"])
        self.assertFalse(budget_state(Decimal("0.33"), Decimal("0.03"))["allowed"])


if __name__ == "__main__":
    unittest.main()
