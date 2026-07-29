#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from data_loader import OwnedOperator, load_mechanics
from efficiency_calculator import (
    EfficiencyCalculator,
    effective_bonus_for_duration,
    production_bonus_for_duration,
)


class EfficiencyTests(unittest.TestCase):
    def test_layout_243_is_correct(self):
        layout = load_mechanics()["layouts"]["243"]
        self.assertEqual(layout["trading_post"], 2)
        self.assertEqual(layout["factory"], 4)
        self.assertEqual(layout["power_plant"], 3)

    def test_elite_level_is_respected(self):
        e1 = EfficiencyCalculator(
            "贸易站",
            [OwnedOperator("巫恋", 1)],
            "龙门币",
        ).compute()
        e2 = EfficiencyCalculator(
            "贸易站",
            [OwnedOperator("巫恋", 2)],
            "龙门币",
        ).compute()
        # Current roster data records Tailoring α as a probability skill,
        # not a deterministic efficiency percentage.
        self.assertEqual(e1["estimated_efficiency_bonus_pct"], 0)
        self.assertEqual(e2["estimated_efficiency_bonus_pct"], 65)

    def test_fixed_order_value_is_separate(self):
        result = EfficiencyCalculator(
            "贸易站",
            [OwnedOperator("龙舌兰", 2)],
            "龙门币",
        ).compute()
        self.assertEqual(result["estimated_efficiency_bonus_pct"], 30)
        self.assertEqual(result["fixed_order_value_lmd_per_trigger"], 500)

    def test_warfarin_override_keeps_global_layer(self):
        global_ops = [
            OwnedOperator("巫恋", 2),
            OwnedOperator("鸿雪", 2),
            OwnedOperator("桃金娘", 1),
            OwnedOperator("杜林", 1),
        ]
        result = EfficiencyCalculator(
            "贸易站",
            [OwnedOperator("巫恋", 2), OwnedOperator("鸿雪", 2)],
            "龙门币",
            global_operators=global_ops,
        ).compute()
        self.assertEqual(result["layers"]["direct_bonus_pct"], 65)
        self.assertGreater(result["layers"]["global_bonus_pct"], 0)

    def test_dongshi_resets_direct_layer_only(self):
        result = EfficiencyCalculator(
            "制造站",
            [
                OwnedOperator("清流", 1),
                OwnedOperator("温蒂", 1),
                OwnedOperator("冬时", 1),
            ],
            "赤金",
            trading_post_count=2,
            power_plant_count=3,
        ).compute()
        self.assertEqual(result["layers"]["direct_bonus_pct"], 30)
        self.assertEqual(result["layers"]["facility_bonus_pct"], 85)
        self.assertEqual(result["estimated_efficiency_bonus_pct"], 115)

    def test_hourly_growth_is_integrated_over_the_shift(self):
        operators = [
            OwnedOperator("克洛丝", 1, 55),
            OwnedOperator("铅踝", 0, 1),
            OwnedOperator("芬", 0, 15),
        ]
        control = [OwnedOperator("凯尔希", 2, 80)]
        assigned = [dict(item.to_dict(), assigned_facility="factory") for item in operators]
        global_ops = assigned + [dict(item.to_dict(), assigned_facility="control_center") for item in control]
        result = EfficiencyCalculator(
            "factory", assigned, "battle_record",
            trading_post_count=2, power_plant_count=3,
            facility_level=3,
            global_operators=global_ops,
        ).compute()
        self.assertEqual(result["estimated_efficiency_bonus_pct"], 82)
        self.assertAlmostEqual(effective_bonus_for_duration(result, 8), 76.375)
        self.assertAlmostEqual(effective_bonus_for_duration(result, 6), 74.5)
        self.assertAlmostEqual(production_bonus_for_duration(result, 8), 79.375)


if __name__ == "__main__":
    unittest.main()
