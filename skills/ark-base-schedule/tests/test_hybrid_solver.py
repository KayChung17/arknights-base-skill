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
from solve_schedule import _simulation_constraint_violations, solve_hybrid


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
