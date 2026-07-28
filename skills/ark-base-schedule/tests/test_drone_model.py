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
)


class DroneModelTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
