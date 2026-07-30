#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from drone_model import (
    accelerated_base_minutes,
    describe_target,
    drone_metrics_per_drone,
    drones_for_base_minutes,
    expected_lmd_order,
    recovered_drones,
    recovery_rate_per_hour,
    simulate_lmd_order_queue,
    special_order_resolution,
)


class DroneModelTests(unittest.TestCase):
    def test_special_order_resolution_reports_suppressed_high_value_effects(self):
        resolution = special_order_resolution({"operators": [
            {"name": "巫恋", "elite": 2},
            {"name": "可露希尔", "elite": 2},
            {"name": "但书", "elite": 2},
        ]})
        self.assertEqual(resolution["active"], [
            {"operator": "可露希尔", "effect": "closure_special_order"}
        ])
        self.assertIn(
            {"operator": "但书", "effect": "proviso_breach_order"},
            resolution["suppressed"],
        )
        self.assertTrue(resolution["has_suppressed_high_value_effect"])

    def test_recovery_formula(self):
        self.assertAlmostEqual(recovery_rate_per_hour(0), 10.0)
        self.assertAlmostEqual(recovery_rate_per_hour(45), 14.5)
        self.assertAlmostEqual(recovered_drones(6, 45), 87.0)

    def test_acceleration_is_three_minutes_per_drone(self):
        self.assertEqual(accelerated_base_minutes(40), 120)
        self.assertEqual(drones_for_base_minutes(72), 24)
        self.assertEqual(drones_for_base_minutes(60), 20)
        self.assertEqual(drones_for_base_minutes(180), 60)
        self.assertEqual(drones_for_base_minutes(120), 40)

    def test_factory_yields_ignore_operator_efficiency(self):
        room = {"facility_id": "factory", "product_id": "pure_gold", "level": 3}
        weak = {"operators": [{"name": "测试A", "elite": 0}]}
        strong = {"operators": [{"name": "测试B", "elite": 2}]}
        self.assertEqual(drone_metrics_per_drone(room, weak), drone_metrics_per_drone(room, strong))
        self.assertAlmostEqual(drone_metrics_per_drone(room, weak)["pure_gold"], 1 / 24)

    def test_orundum_order_uses_forty_drones(self):
        room = {"facility_id": "trading_post", "product_id": "orundum_order", "level": 3}
        target = describe_target(room)
        self.assertEqual(target["drones_per_order"], 40)
        self.assertAlmostEqual(target["metrics_per_drone"]["orundum"], 0.5)
        self.assertAlmostEqual(target["metrics_per_drone"]["orundum_shard_consumption"], 0.05)

    def test_closure_and_proviso_special_orders(self):
        closure = expected_lmd_order(1, {"operators": [{"name": "可露希尔", "elite": 2}]})
        self.assertEqual(closure["minutes"], 144)
        self.assertEqual(closure["lmd"], 1200)
        proviso = expected_lmd_order(1, {"operators": [{"name": "但书", "elite": 2}]})
        self.assertEqual(proviso["minutes"], 144)
        self.assertEqual(proviso["pure_gold"], 4)
        self.assertEqual(proviso["lmd"], 2000)

    def test_tequila_applies_only_to_four_gold_orders(self):
        tequila = expected_lmd_order(3, {"operators": [{"name": "龙舌兰", "elite": 2}]})
        # Level-3 expected LMD is 1450; 20% of orders receive +500.
        self.assertAlmostEqual(tequila["lmd"], 1550.0)
        proviso_tequila = expected_lmd_order(3, {
            "operators": [{"name": "但书", "elite": 2}, {"name": "龙舌兰", "elite": 2}]
        })
        # Proviso transforms 2/3-gold orders; Tequila still applies to the
        # original non-breach 4-gold orders.
        self.assertAlmostEqual(proviso_tequila["lmd"], 2350.0)

    def test_tailoring_warmup_probability_tables(self):
        alpha = {"operators": [{"name": "巫恋", "elite": 0}]}
        cold = expected_lmd_order(3, alpha, warmup_hours=2.99)
        warm = expected_lmd_order(3, alpha, warmup_hours=3.0)
        self.assertAlmostEqual(cold["pure_gold"], 2.9)
        self.assertAlmostEqual(warm["pure_gold"], 3.4)
        self.assertEqual(warm["model"], "tailoring_alpha_empirical_3h_order")

        alpha_pair = {"operators": [
            {"name": "巫恋", "elite": 0},
            {"name": "贝娜", "elite": 2},
        ]}
        pair = expected_lmd_order(3, alpha_pair, warmup_hours=3.0)
        self.assertAlmostEqual(pair["pure_gold"], 3.52)

        beta = {"operators": [{"name": "明椒", "elite": 2}]}
        warming = expected_lmd_order(3, beta, warmup_hours=3.0)
        mature = expected_lmd_order(3, beta, warmup_hours=5.0)
        self.assertAlmostEqual(warming["pure_gold"], 3.7333333333)
        self.assertAlmostEqual(mature["pure_gold"], 3.8)

        refraction_alpha = expected_lmd_order(
            3, {"operators": [{"name": "折光", "elite": 0}]}, warmup_hours=3.0,
        )
        refraction_beta = expected_lmd_order(
            3, {"operators": [{"name": "折光", "elite": 2}]}, warmup_hours=5.0,
        )
        self.assertAlmostEqual(refraction_alpha["pure_gold"], 3.4)
        self.assertAlmostEqual(refraction_beta["pure_gold"], 3.8)

    def test_special_order_priority_is_exclusive(self):
        combo = {"operators": [
            {"name": "佩佩", "elite": 2},
            {"name": "可露希尔", "elite": 2},
            {"name": "但书", "elite": 2},
        ]}
        pepe = expected_lmd_order(3, combo, warmup_hours=5)
        self.assertEqual(pepe["model"], "pepe_exclusive_order")
        self.assertEqual((pepe["minutes"], pepe["pure_gold"], pepe["lmd"]), (270, 0, 1000))

        closure = expected_lmd_order(3, {"operators": [
            {"name": "可露希尔", "elite": 2},
            {"name": "U-Official", "elite": 0},
            {"name": "但书", "elite": 2},
            {"name": "龙舌兰", "elite": 2},
        ]})
        self.assertEqual(closure["model"], "closure_special_order")
        self.assertEqual((closure["pure_gold"], closure["lmd"]), (2, 1200))

        u_official = expected_lmd_order(3, {"operators": [
            {"name": "U-Official", "elite": 0},
            {"name": "但书", "elite": 2},
            {"name": "龙舌兰", "elite": 2},
        ]})
        self.assertEqual(u_official["model"], "u_official_two_gold_order")
        self.assertEqual((u_official["pure_gold"], u_official["lmd"]), (2, 1000))

    def test_jaye_e0_queue_slows_and_collection_restores_bonus(self):
        combo = {"operators": [{"name": "孑", "elite": 0}]}
        first = simulate_lmd_order_queue(
            1, combo, elapsed_hours=8, base_efficiency_bonus_pct=1,
            order_capacity=10,
        )
        self.assertEqual(first["state"]["completed_orders"], 4)
        self.assertAlmostEqual(first["state"]["current_order"]["remaining_base_minutes"], 77.92, places=2)

        collected = simulate_lmd_order_queue(
            1, combo, elapsed_hours=0, base_efficiency_bonus_pct=1,
            order_capacity=10, state=first["state"], collect_at_start=True,
        )
        self.assertEqual(collected["state"]["completed_orders"], 0)
        self.assertAlmostEqual(
            collected["state"]["current_order"]["remaining_base_minutes"], 77.92, places=2,
        )

    def test_jaye_e0_queue_stops_at_capacity_and_drones_collect_immediately(self):
        combo = {"operators": [{"name": "孑", "elite": 0}]}
        full = simulate_lmd_order_queue(
            1, combo, elapsed_hours=30, base_efficiency_bonus_pct=1,
            order_capacity=3,
        )
        self.assertEqual(full["state"]["completed_orders"], 3)
        self.assertIsNone(full["state"]["current_order"])

        accelerated = simulate_lmd_order_queue(
            1, combo, elapsed_hours=0, base_efficiency_bonus_pct=1,
            order_capacity=10, drone_count=96,
        )
        self.assertEqual(accelerated["drone_metrics"]["lmd_trade_work"], 2)
        self.assertEqual(accelerated["state"]["completed_orders"], 0)
        self.assertIsNotNone(accelerated["state"]["current_order"])

    def test_tailoring_warmup_resets_when_crew_or_workstation_changes(self):
        combo = {"operators": [{"name": "巫恋", "elite": 0}]}
        warm = simulate_lmd_order_queue(
            3, combo, elapsed_hours=3, base_efficiency_bonus_pct=1,
            crew_signature="巫恋@slot1",
        )
        self.assertAlmostEqual(warm["state"]["tailoring_warmup_hours"], 3.0)
        reset = simulate_lmd_order_queue(
            3, combo, elapsed_hours=0, base_efficiency_bonus_pct=1,
            state=warm["state"], crew_signature="巫恋@slot2",
        )
        self.assertEqual(reset["state"]["tailoring_warmup_hours"], 0.0)


if __name__ == "__main__":
    unittest.main()
