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

from build_combinations import (
    _known_dominated_lmd_crew,
    build_library,
    build_room_combinations,
)
from data_loader import load_mechanics
from optimizer_common import context_rooms
from layout_profiles import facility_configuration_power_summary, fixed_right_power_consumption
from search_layouts import (
    COMMON_PROFILES,
    economic_result_sort_key,
    economic_utility_lmd,
    facility_configuration,
    power_summary,
    product_splits,
)
from simulate_schedule import simulate_assignment
from schedule_generator import normalize_goal


class LayoutOptimizerTests(unittest.TestCase):
    def test_known_dominated_special_order_crews_are_rejected(self):
        self.assertEqual(
            _known_dominated_lmd_crew({"U-Official", "但书"}),
            "u_official_overrides_proviso",
        )
        self.assertEqual(
            _known_dominated_lmd_crew({"但书", "龙舌兰"}),
            "proviso_orders_do_not_trigger_tequila",
        )
        self.assertEqual(
            _known_dominated_lmd_crew({"可露希尔", "龙舌兰"}),
            "closure_fixed_order_disables_tequila",
        )
        self.assertIsNone(_known_dominated_lmd_crew({"巫恋", "龙舌兰", "折光"}))
        self.assertIsNone(_known_dominated_lmd_crew({"可露希尔", "黑键", "吉星"}))
        self.assertIsNone(_known_dominated_lmd_crew({"伺夜", "贝洛内", "但书"}))

    def test_economic_utility_uses_fixed_orundum_lmd_rate(self):
        self.assertEqual(normalize_goal("lmd_equivalent"), "lmd_equivalent")
        constants = load_mechanics()["economy_constants"]
        self.assertEqual(constants["orundum_lmd_per_unit"], 160)
        self.assertEqual(constants["orundum_batch_units"], 20)
        self.assertEqual(constants["orundum_batch_lmd_equivalent"], 3200)
        self.assertEqual(economic_utility_lmd(20, 0, 0, 0), 3200)
        self.assertEqual(economic_utility_lmd(20, 0, -1, 0), 1600)
        self.assertEqual(economic_utility_lmd(20, 0, 0, -2), 2200)
        with self.assertRaisesRegex(ValueError, "固定机制常量"):
            economic_utility_lmd(20, 0, 0, 0, {"orundum_lmd": 200})

    def test_economic_sort_does_not_make_orundum_absolute_priority(self):
        more_orundum_lower_value = {
            "economic_utility_lmd_per_day": 9000,
            "net_lmd_per_day": -15000,
            "orundum_per_day": 200,
            "battle_record_exp_per_day": 0,
            "resource_balance_deviation": 0,
        }
        less_orundum_higher_value = {
            "economic_utility_lmd_per_day": 10000,
            "net_lmd_per_day": 2000,
            "orundum_per_day": 50,
            "battle_record_exp_per_day": 0,
            "resource_balance_deviation": 0,
        }
        ranked = sorted(
            [more_orundum_lower_value, less_orundum_higher_value],
            key=economic_result_sort_key,
        )
        self.assertIs(ranked[0], less_orundum_higher_value)

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

    def test_full_right_side_is_irreversible_190_power(self):
        right_full = {"reception_room": 3, "office": 3, "training_room": 3, "workshop": 3}
        self.assertEqual(fixed_right_power_consumption(right_full), 190)

    def test_preferred_342_profile_is_80_power_short_with_full_right_side(self):
        profile = {
            "layout": "342",
            "trading_levels": [3, 3, 2],
            "factory_levels": [3, 3, 3, 3],
            "power_plant_levels": [3, 3],
            "dorm_levels": [1, 1, 1, 1],
        }
        summary = power_summary(profile)
        self.assertEqual(summary["fixed_right_consumption"], 190)
        self.assertEqual(summary["spare_power"], -80)

    def test_fixed_configuration_power_uses_actual_rooms_and_right_side(self):
        profile = {
            "layout": "342",
            "trading_levels": [3, 3, 2],
            "factory_levels": [3, 3, 2, 1],
            "power_plant_levels": [3, 3],
            "dorm_levels": [1, 1, 1, 1],
        }
        config = facility_configuration(profile, 1, 1, 1)
        summary = facility_configuration_power_summary(
            config,
            right_side_levels={"reception_room": 3, "office": 3, "training_room": 3, "workshop": 3},
            expected_layout="342",
        )
        self.assertEqual(summary["spare_power"], 0)

    def test_layout_product_search_excludes_battle_records_by_default(self):
        profile = COMMON_PROFILES["252-output"]
        self.assertIn((1, 1, 0), product_splits(profile))
        config = facility_configuration(profile, 1, 1)
        products = {room["product_id"] for room in config["rooms"].values()}
        self.assertNotIn("battle_record", products)
        self.assertIn("orundum_order", products)
        self.assertIn("lmd_order", products)
        self.assertIn("orundum_shard", products)
        self.assertIn("pure_gold", products)

    def test_layout_product_search_can_require_battle_records(self):
        profile = COMMON_PROFILES["252-output"]
        splits = product_splits(profile, minimum_battle_record_factories=1)
        self.assertTrue(splits)
        self.assertTrue(all(split[2] >= 1 for split in splits))
        config = facility_configuration(profile, *splits[0])
        products = [room["product_id"] for room in config["rooms"].values()]
        self.assertIn("battle_record", products)
        self.assertIn("pure_gold", products)

    def test_economic_search_can_compare_zero_orundum_configuration(self):
        profile = COMMON_PROFILES["252-output"]
        splits = product_splits(
            profile,
            minimum_battle_record_factories=1,
            allow_zero_orundum=True,
        )
        self.assertIn((0, 0, 1), splits)
        config = facility_configuration(profile, 0, 0, 1)
        products = [room["product_id"] for room in config["rooms"].values()]
        self.assertNotIn("orundum_order", products)
        self.assertNotIn("orundum_shard", products)
        self.assertIn("battle_record", products)
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

    def test_partial_search_can_require_staffing_for_production_and_control(self):
        context = self._base_context(
            {
                "control_center": {"facility_id": "control_center", "level": 5, "product_id": "base_management"},
                "factory_1": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
                "trading_post_1": {"facility_id": "trading_post", "level": 1, "product_id": "lmd_order"},
            },
            [
                {"name": "阿米娅", "elite": 0, "level": 1, "recruited": True, "morale": 24},
                {"name": "杰西卡", "elite": 0, "level": 40, "recruited": True, "morale": 24},
                {"name": "月见夜", "elite": 1, "level": 15, "recruited": True, "morale": 24},
            ],
        )
        library = build_library(
            context,
            top_k=10,
            operator_pool_size=3,
            allow_partial=True,
            minimum_staffed_slots_by_facility={
                "control_center": 1,
                "factory": 1,
                "trading_post": 1,
            },
        )
        for room_id in ("control_center", "factory_1", "trading_post_1"):
            combos = library["rooms"][room_id]["combinations"]
            self.assertTrue(combos)
            self.assertTrue(all(combo["operators"] for combo in combos), room_id)


if __name__ == "__main__":
    unittest.main()
