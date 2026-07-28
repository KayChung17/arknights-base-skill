#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_combinations import build_library, build_room_combinations
from data_loader import load_mechanics
from optimizer_common import context_rooms
from search_layouts import COMMON_PROFILES, facility_configuration, power_summary, product_splits
from simulate_schedule import simulate_assignment


class LayoutOptimizerTests(unittest.TestCase):
    def _base_context(self, rooms, roster):
        return {
            "schema_version": 2,
            "packet_type": "model_decision_context",
            "objective": {
                "goal_id": "orundum_lmd_balance",
                "layout": "252",
                "preferences": {
                    "priority": "orundum_lmd_balance",
                    "solver": {
                        "max_daily_work_hours": 24,
                        "allocate_drones": True,
                        "drone_repeating_day_balance": False,
                        "drone_capacity": 999,
                        "initial_drone_stock": 0,
                        "require_resource_balance": False,
                    },
                },
            },
            "baseline": None,
            "facility_configuration": {"rooms": rooms, "dormitories": []},
            "operation_nodes": [{"time": "08:00", "label": "节点"}],
            "segment_template": {
                "segment_1": {"name": "全天", "start": "08:00", "end": "08:00", "hours": 24.0, "rooms": {}}
            },
            "roster": roster,
        }

    def test_drone_capacity_is_cleared_area_capacity(self):
        mechanics = load_mechanics()
        drone = mechanics["drone_model"]
        self.assertEqual(drone["fully_cleared_capacity"], 235)
        self.assertEqual(drone["capacity_source"], "cleared_base_areas")
        self.assertFalse(drone["capacity_depends_on_power_plants"])

    def test_252_output_profile_exactly_fits_power_budget(self):
        summary = power_summary(COMMON_PROFILES["252-output"])
        self.assertEqual(summary["supply"], 540)
        self.assertEqual(summary["total_consumption"], 540)
        self.assertEqual(summary["spare_power"], 0)
        self.assertLess(power_summary(COMMON_PROFILES["351-min"])["spare_power"], 0)

    def test_layout_product_search_excludes_battle_records(self):
        profile = COMMON_PROFILES["252-output"]
        self.assertIn((1, 1), product_splits(profile))
        config = facility_configuration(profile, 1, 1)
        products = {room["product_id"] for room in config["rooms"].values()}
        self.assertNotIn("battle_record", products)
        self.assertIn("orundum_order", products)
        self.assertIn("lmd_order", products)
        self.assertIn("orundum_shard", products)
        self.assertIn("pure_gold", products)

    def test_candidate_pool_preserves_shard_specialists(self):
        context = self._base_context(
            {"factory_1": {"facility_id": "factory", "level": 3, "product_id": "orundum_shard"}},
            [
                {"name": "艾雅法拉", "elite": 2, "level": 1, "recruited": True, "morale": 24},
                {"name": "炎熔", "elite": 1, "level": 15, "recruited": True, "morale": 24},
                {"name": "月见夜", "elite": 1, "level": 15, "recruited": True, "morale": 24},
                {"name": "卡缇", "elite": 0, "level": 2, "recruited": True, "morale": 24},
            ],
        )
        room = context_rooms(context)["factory_1"]
        result = build_room_combinations(context, room, top_k=20, operator_pool_size=1, allow_partial=True)
        max_shards = max(float(combo["metrics_per_hour"].get("orundum_shard", 0)) for combo in result["combinations"])
        self.assertGreaterEqual(max_shards, 2.0)

    def test_empty_room_has_no_output_and_no_staffed_power_bonus(self):
        factory_context = self._base_context(
            {"factory_1": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"}},
            [{"name": "杰西卡", "elite": 0, "level": 40, "recruited": True, "morale": 24}],
        )
        factory_library = build_library(factory_context, top_k=10, operator_pool_size=3, allow_partial=True)
        empty = next(c for c in factory_library["rooms"]["factory_1"]["combinations"] if not c["operators"])
        self.assertEqual(empty["metrics_per_hour"], {})

        power_context = self._base_context(
            {"power_plant_1": {"facility_id": "power_plant", "level": 3, "product_id": "drone_recovery"}},
            [{"name": "格雷伊", "elite": 0, "level": 1, "recruited": True, "morale": 24}],
        )
        power_library = build_library(power_context, top_k=10, operator_pool_size=3, allow_partial=True)
        combos = power_library["rooms"]["power_plant_1"]["combinations"]
        empty_combo = next(c for c in combos if not c["operators"])
        staffed_combo = next(c for c in combos if c["operators"])
        empty_sim = simulate_assignment(power_context, power_library, [{
            "segment_id": "segment_1", "room_id": "power_plant_1", "combination_id": empty_combo["combination_id"]
        }])
        staffed_sim = simulate_assignment(power_context, power_library, [{
            "segment_id": "segment_1", "room_id": "power_plant_1", "combination_id": staffed_combo["combination_id"]
        }])
        self.assertAlmostEqual(empty_sim["drone_plan"]["total_recovered"], 240.0)
        self.assertAlmostEqual(staffed_sim["drone_plan"]["total_recovered"], 300.0)


if __name__ == "__main__":
    unittest.main()
