#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_combinations import build_library
from data_loader import read_roster
from build_model import build_milp
from scipy.optimize import milp
from simulate_schedule import simulate_assignment
from solve_schedule import _apply_free_secondary_improvements, _simulation_constraint_violations, solve_hybrid


class HybridSolverTests(unittest.TestCase):
    def _context(self, rooms, segments=None, roster=None, solver=None):
        segments = segments or {
            "segment_1": {"name": "全天", "start": "08:00", "end": "08:00", "hours": 24.0, "rooms": {}}
        }
        roster = roster or [
            {"name": "砾", "elite": 2, "recruited": True, "morale": 24},
            {"name": "斑点", "elite": 2, "recruited": True, "morale": 24},
            {"name": "夜烟", "elite": 2, "recruited": True, "morale": 24},
        ]
        return {
            "schema_version": 2,
            "packet_type": "model_decision_context",
            "data_version": "test",
            "objective": {
                "goal_id": "gold_origin",
                "layout": "342",
                "online_count": len(segments),
                "online_times": [item["start"] for item in segments.values()],
                "products": [room["product_id"] for room in rooms.values()],
                "preferences": {
                    "priority": "balanced",
                    "solver": solver or {"max_daily_work_hours": 24},
                },
            },
            "facility_configuration": {"rooms": rooms, "dormitories": []},
            "operation_nodes": [],
            "segment_template": segments,
            "hard_rules": {},
            "model_decision_requirements": {},
            "roster": roster,
            "capabilities": {},
            "external_evidence": [],
        }

    def _manual_library(self):
        def combo(combo_id, room_id, name, score):
            return {
                "combination_id": combo_id,
                "room_id": room_id,
                "facility_id": "factory",
                "product_id": "pure_gold",
                "level": 1,
                "capacity": 1,
                "staffed_slots": 1,
                "operators": [{"name": name, "elite": 2, "skill_source": "local_versioned_data"}],
                "proxy_score_per_hour": score,
                "metrics_per_hour": {"pure_gold": score / 10},
                "fixed_metrics": {"fixed_lmd_per_trigger": 0},
                "warehouse_capacity": 1000,
                "morale_cost_per_operator_hour": 1.0,
                "efficiency_result": {},
                "warnings": [],
                "source_quality": 1.0,
            }

        return {
            "schema_version": 1,
            "library_type": "room_combination_library",
            "parameters": {"top_k_per_room": 2, "operator_pool_size": 3, "allow_partial": False},
            "objective_weights": {},
            "rooms": {
                "factory_1": {
                    "room": {"room_id": "factory_1", "facility_id": "factory", "product_id": "pure_gold", "level": 1, "capacity": 1},
                    "eligible_operator_count": 2,
                    "enumerated_count": 2,
                    "kept_count": 2,
                    "truncated": False,
                    "combinations": [
                        combo("f1_gravel", "factory_1", "砾", 10),
                        combo("f1_spot", "factory_1", "斑点", 8),
                    ],
                },
                "factory_2": {
                    "room": {"room_id": "factory_2", "facility_id": "factory", "product_id": "pure_gold", "level": 1, "capacity": 1},
                    "eligible_operator_count": 2,
                    "enumerated_count": 2,
                    "kept_count": 2,
                    "truncated": False,
                    "combinations": [
                        combo("f2_gravel", "factory_2", "砾", 9),
                        combo("f2_haze", "factory_2", "夜烟", 8),
                    ],
                },
            },
            "search_completeness": {"all_rooms_untruncated": True, "truncated_rooms": []},
        }

    def test_milp_selects_disjoint_global_assignment(self):
        rooms = {
            "factory_1": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
            "factory_2": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
        }
        context = self._context(rooms)
        bundle = build_milp(context, self._manual_library())
        result = milp(bundle.c, integrality=bundle.integrality, bounds=bundle.bounds, constraints=bundle.constraints)
        self.assertTrue(result.success)
        selected = {
            record["combination_id"]
            for record, value in zip(bundle.variable_records, result.x)
            if record["kind"] == "assignment" and value > 0.5
        }
        self.assertEqual(selected, {"f1_gravel", "f2_haze"})

    def test_fixed_right_side_workers_are_reserved_from_production(self):
        rooms = {
            "factory_1": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
            "factory_2": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
        }
        roster = [
            {"name": name, "elite": 2, "recruited": True, "morale": 24}
            for name in ("砾", "斑点", "夜烟", "甲", "乙")
        ]
        context = self._context(rooms, roster=roster)
        context["right_side_schedule"] = [{"meeting": ["砾", "甲"], "hire": ["乙"]}]
        bundle = build_milp(context, self._manual_library())
        result = milp(bundle.c, integrality=bundle.integrality, bounds=bundle.bounds, constraints=bundle.constraints)
        self.assertTrue(result.success)
        selected = {
            record["combination_id"]
            for record, value in zip(bundle.variable_records, result.x)
            if record["kind"] == "assignment" and value > 0.5
        }
        self.assertEqual(selected, {"f1_spot", "f2_haze"})

    def test_combination_builder_uses_verified_skills(self):
        rooms = {
            "factory_1": {"facility_id": "factory", "level": 3, "product_id": "pure_gold"},
        }
        context = self._context(rooms, roster=[
            {"name": "砾", "elite": 2, "recruited": True, "morale": 24},
            {"name": "斑点", "elite": 2, "recruited": True, "morale": 24},
            {"name": "夜烟", "elite": 2, "recruited": True, "morale": 24},
        ])
        library = build_library(context, top_k=10, operator_pool_size=5)
        combos = library["rooms"]["factory_1"]["combinations"]
        self.assertEqual(len(combos), 1)
        self.assertEqual({item["name"] for item in combos[0]["operators"]}, {"砾", "斑点", "夜烟"})

    def test_simulator_applies_warehouse_cap(self):
        rooms = {"factory_1": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"}}
        context = self._context(rooms, roster=[{"name": "砾", "elite": 2, "recruited": True, "morale": 24}])
        overflow_combo = dict(self._manual_library()["rooms"]["factory_1"]["combinations"][0])
        overflow_combo["warehouse_capacity"] = 24
        library = {
            "schema_version": 1,
            "library_type": "room_combination_library",
            "parameters": {},
            "rooms": {
                "factory_1": {
                    "room": {"room_id": "factory_1", "facility_id": "factory", "product_id": "pure_gold", "level": 1, "capacity": 1},
                    "eligible_operator_count": 1,
                    "enumerated_count": 1,
                    "kept_count": 1,
                    "truncated": False,
                    "combinations": [overflow_combo],
                }
            },
            "search_completeness": {"all_rooms_untruncated": True, "truncated_rooms": []},
        }
        sim = simulate_assignment(context, library, [
            {"segment_id": "segment_1", "room_id": "factory_1", "combination_id": "f1_gravel"}
        ])
        self.assertIsNotNone(sim["room_results"][0]["warehouse_overflow"])
        self.assertTrue(any("仓库封顶" in item for item in sim["warnings"]))

    def test_simulator_exposes_jaye_e0_queue_state_per_operation_interval(self):
        rooms = {"trading_post_1": {"facility_id": "trading_post", "level": 1, "product_id": "lmd_order"}}
        segments = {
            "segment_1": {"name": "一班", "start": "00:00", "end": "08:00", "hours": 8.0, "rooms": {}},
            "segment_2": {"name": "二班", "start": "08:00", "end": "16:00", "hours": 8.0, "rooms": {}},
            "segment_3": {"name": "三班", "start": "16:00", "end": "00:00", "hours": 8.0, "rooms": {}},
        }
        context = self._context(
            rooms,
            segments=segments,
            roster=[{"name": "孑", "elite": 0, "level": 1, "recruited": True, "morale": 24}],
            solver={"max_daily_work_hours": 24, "allocate_drones": False},
        )
        library = build_library(context, top_k=10, operator_pool_size=2, allow_partial=True)
        combo = next(
            item for item in library["rooms"]["trading_post_1"]["combinations"]
            if {op["name"] for op in item["operators"]} == {"孑"}
        )
        assignments = [
            {"segment_id": segment_id, "room_id": "trading_post_1", "combination_id": combo["combination_id"]}
            for segment_id in segments
        ]
        simulation = simulate_assignment(context, library, assignments)
        queues = [item["trade_queue"] for item in simulation["room_results"]]
        self.assertTrue(all(queue["queue_state_exact"] for queue in queues))
        self.assertTrue(all(queue["jaye_e0_dynamic"] for queue in queues))
        self.assertTrue(all(queue["state"]["completed_orders"] > 0 for queue in queues))
        self.assertEqual(simulation["simulation_type"], "segment_global_recalculation_with_trade_queue_and_drone_inventory")


    def test_xlsx_roster_is_supported(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roster.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.append(["干员名称", "是否已招募", "精英化等级", "当前心情"])
            sheet.append(["砾", True, 2, 20])
            sheet.append(["斑点", False, 1, 24])
            book.save(path)
            roster = read_roster(path)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0].name, "砾")
        self.assertEqual(roster[0].elite, 2)
        self.assertEqual(roster[0].morale, 20.0)

    def test_hybrid_solver_returns_alternatives_and_scope(self):
        rooms = {
            "factory_1": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
            "factory_2": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
        }
        result = solve_hybrid(
            self._context(rooms),
            library=self._manual_library(),
            top_solutions=2,
            time_limit=5,
        )
        self.assertGreaterEqual(len(result["alternatives"]), 1)
        self.assertIn("candidate_library", result["solver"]["optimality_claim"])
        self.assertFalse(result["solver"]["actual_simulation_global_optimality_proven"])
        self.assertEqual(result["candidate_plan"]["plan_status"], "candidate")

    def test_free_battle_record_slots_are_lexicographically_improved(self):
        rooms = {"factory_1": {"facility_id": "factory", "level": 3, "product_id": "battle_record"}}
        segments = {
            "segment_1": {"name": "早班", "start": "08:00", "end": "14:00", "hours": 6.0, "rooms": {}},
            "segment_2": {"name": "中班", "start": "14:00", "end": "20:00", "hours": 6.0, "rooms": {}},
            "segment_3": {"name": "晚班", "start": "20:00", "end": "08:00", "hours": 12.0, "rooms": {}},
        }
        context = self._context(
            rooms,
            segments=segments,
            roster=[
                {"name": "流星", "elite": 0, "level": 1, "recruited": True, "morale": 24},
                {"name": "酒神", "elite": 2, "level": 80, "recruited": True, "morale": 24},
                {"name": "断罪者", "elite": 1, "level": 1, "recruited": True, "morale": 24},
                {"name": "红豆", "elite": 0, "level": 1, "recruited": True, "morale": 24},
            ],
            solver={"max_daily_work_hours": 18, "allocate_drones": False},
        )
        context["objective"]["preferences"]["priority"] = "orundum_lmd_balance"
        library = build_library(context, top_k=60, operator_pool_size=4, allow_partial=True)
        combos = library["rooms"]["factory_1"]["combinations"]

        def combo_id(names):
            target = set(names)
            return next(item["combination_id"] for item in combos if {op["name"] for op in item["operators"]} == target)

        assignments = [
            {"segment_id": "segment_1", "room_id": "factory_1", "combination_id": combo_id(["流星"])},
            {"segment_id": "segment_2", "room_id": "factory_1", "combination_id": combo_id(["流星"])},
            {"segment_id": "segment_3", "room_id": "factory_1", "combination_id": combo_id(["红豆"])},
        ]
        before = simulate_assignment(context, library, assignments)
        improved, after, metadata = _apply_free_secondary_improvements(
            context, library, assignments, before, [], [], [],
        )
        late = next(item for item in improved if item["segment_id"] == "segment_3")
        selected = next(item for item in combos if item["combination_id"] == late["combination_id"])
        self.assertEqual({op["name"] for op in selected["operators"]}, {"酒神", "断罪者", "红豆"})
        self.assertGreater(after["aggregate_metrics"]["battle_record_exp"], before["aggregate_metrics"]["battle_record_exp"])
        self.assertGreater(metadata["battle_record_exp_gain"], 0)
        self.assertEqual(metadata["scope"]["product_id"], "battle_record")
        self.assertEqual(metadata["scope"]["drone_target_rooms"], "skipped")
        self.assertEqual(metadata["skipped_drone_target_rooms"], [])
        self.assertEqual(metadata["remaining_dominated_empty_slots"], [])


    def test_drone_inventory_is_closed_and_allocated(self):
        rooms = {
            "factory_1": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
        }
        context = self._context(
            rooms,
            solver={
                "max_daily_work_hours": 24,
                "allocate_drones": True,
                "drone_repeating_day_balance": True,
                "drone_capacity": 235,
                "empty_drone_inventory_at_each_node": True,
                "require_resource_balance": False,
            },
        )
        result = solve_hybrid(
            context,
            library=self._manual_library_with_one_room(),
            top_solutions=1,
            time_limit=5,
        )
        simulation = result["selected_solution"]["simulation"]
        self.assertTrue(simulation["drone_plan"]["feasible"])
        self.assertGreater(simulation["drone_plan"]["total_used"], 0)
        self.assertAlmostEqual(
            simulation["drone_plan"]["total_used"] + simulation["drone_plan"]["total_wasted"],
            simulation["drone_plan"]["total_recovered"],
            places=5,
        )

    def test_each_operation_node_uses_at_most_one_drone_target(self):
        rooms = {
            "factory_1": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
            "factory_2": {"facility_id": "factory", "level": 1, "product_id": "pure_gold"},
        }
        context = self._context(
            rooms,
            solver={
                "max_daily_work_hours": 24,
                "allocate_drones": True,
                "drone_repeating_day_balance": True,
                "drone_capacity": 235,
                "empty_drone_inventory_at_each_node": True,
                "require_resource_balance": False,
                "require_pure_gold_balance": False,
            },
        )
        result = solve_hybrid(
            context,
            library=self._manual_library(),
            top_solutions=1,
            time_limit=5,
        )
        allocations = result["selected_solution"]["drone_allocations"]
        counts: dict[str, int] = {}
        for allocation in allocations:
            segment_id = allocation["segment_id"]
            counts[segment_id] = counts.get(segment_id, 0) + 1
        self.assertTrue(allocations)
        self.assertTrue(all(count <= 1 for count in counts.values()))
        timeline = result["selected_solution"]["simulation"]["drone_plan"]["timeline"]
        self.assertTrue(all(item["start_inventory"] - item["used_at_start"] < 1.0 for item in timeline))

    def _manual_library_with_one_room(self):
        base = self._manual_library()
        return {
            **base,
            "rooms": {"factory_1": base["rooms"]["factory_1"]},
        }

    def test_post_simulation_resource_floor_is_enforced(self):
        context = self._context(
            {},
            solver={
                "max_daily_work_hours": 24,
                "require_resource_balance": True,
                "minimum_orundum_shard_balance": -4,
                "resource_balance_safety_factor": 1.07,
            },
        )
        self.assertTrue(
            _simulation_constraint_violations(
                context, {"orundum_shard_balance": -4.1}
            )
        )
        self.assertEqual(
            _simulation_constraint_violations(
                context, {"orundum_shard_balance": -3.9}
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()

# Additional regression tests are inserted before unittest.main below.
