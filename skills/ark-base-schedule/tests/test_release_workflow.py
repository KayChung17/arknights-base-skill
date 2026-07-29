#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audit_result import audit_result
from generate_report import generate_report
from layout_profiles import generate_grid_profiles, power_summary
from reproducibility import build_manifest
from run_project import run_project


class ReleaseWorkflowTests(unittest.TestCase):
    def test_grid_profiles_record_truncation_and_power(self):
        profiles, meta = generate_grid_profiles(layouts=["252"], dorm_levels=[1, 1, 1, 1], max_profiles=3)
        self.assertEqual(len(profiles), 3)
        self.assertTrue(meta["profiles_truncated"])
        for profile in profiles.values():
            self.assertGreaterEqual(power_summary(profile)["spare_power"], 0)

    def test_power_plant_levels_are_not_hardcoded(self):
        base = {
            "layout": "252",
            "trading_levels": [1, 1],
            "factory_levels": [1, 1, 1, 1, 1],
            "power_plant_levels": [3, 2],
            "dorm_levels": [1, 1, 1, 1],
        }
        summary = power_summary(base)
        self.assertEqual(summary["supply"], 400)

    def test_manifest_builds_minimal_structure(self):
        manifest = build_manifest(run_type="test")
        self.assertEqual(manifest["run_type"], "test")
        self.assertIn("runtime", manifest)
        self.assertNotIn("configuration_sha256", manifest)
        self.assertNotIn("input_files", manifest)

    def test_audit_rejects_floor_violation_and_overclaim(self):
        value = {
            "search_type": "outer_layout_configuration_plus_inner_hybrid_schedule_solver",
            "objective": {"constraints": {
                "minimum_net_lmd_per_day": -100,
                "minimum_orundum_shard_balance": 0,
                "minimum_pure_gold_balance": 0,
            }},
            "selected": {
                "orundum_per_day": 300,
                "net_lmd_per_day": -200,
                "orundum_shard_balance": 1,
                "pure_gold_balance": 1,
                "solver_result": {"solver": {
                    "optimality_claim": "proxy_optimal_within_complete_candidate_library",
                    "candidate_library_complete": False,
                    "proxy_models_solved_to_gap": True,
                    "actual_simulation_global_optimality_proven": False,
                }},
            },
        }
        audit = audit_result(value)
        self.assertEqual(audit["status"], "failed")
        failed = {item["code"] for item in audit["checks"] if not item["ok"]}
        self.assertIn("net_lmd_floor", failed)
        self.assertIn("optimality_claim_library_complete", failed)

    def test_chinese_report_for_layout_search(self):
        value = {
            "search_type": "outer_layout_configuration_plus_inner_hybrid_schedule_solver",
            "objective": {"primary": "maximize_orundum", "constraints": {}},
            "selected": {
                "profile_id": "252-test", "layout": "252", "orundum_per_day": 400,
                "net_lmd_per_day": -5000, "orundum_shard_balance": 0.2,
                "pure_gold_balance": 0.1, "trading_levels": [3, 3],
                "factory_levels": [3, 3, 2, 2, 1], "power_plant_levels": [3, 3],
                "dormitory_levels": [1, 1, 1, 1], "product_split": {},
                "plan": {
                    "plan_id": "test-plan",
                    "title": "测试排班",
                    "facility_configuration": {
                        "rooms": {
                            "trading_post_1": {"facility_id": "trading_post", "product_id": "lmd_order", "level": 3},
                            "control_center": {"facility_id": "control_center", "product_id": "base_management", "level": 5},
                        },
                        "dormitories": [{"room_id": f"dormitory_{i}", "level": 1} for i in range(1, 5)],
                    },
                    "segments": {
                        "segment_1": {
                            "start": "08:00", "end": "14:00",
                            "rooms": {
                                "trading_post_1": {"operators": []},
                                "control_center": {"operators": []},
                            },
                        },
                        "segment_2": {
                            "start": "14:00", "end": "20:00",
                            "rooms": {
                                "trading_post_1": {"operators": []},
                                "control_center": {"operators": []},
                            },
                        },
                        "segment_3": {
                            "start": "20:00", "end": "08:00",
                            "rooms": {
                                "trading_post_1": {"operators": []},
                                "control_center": {"operators": []},
                            },
                        },
                    },
                    "simulation": {"drone_plan": {"allocations": []}},
                },
            },
            "results": [], "limitations": [],
        }
        report = generate_report(value)
        self.assertIn("基建布局优化报告", report)
        self.assertIn("400.00", report)

    def test_project_runner_writes_release_artifacts(self):
        fake_result = {
            "schema_version": 2,
            "search_type": "outer_layout_configuration_plus_inner_hybrid_schedule_solver",
            "objective": {"primary": "maximize_orundum", "constraints": {
                "minimum_net_lmd_per_day": -10000,
                "minimum_orundum_shard_balance": 0,
                "minimum_pure_gold_balance": 0,
            }},
            "selected": {
                "profile_id": "252-test", "layout": "252", "orundum_per_day": 400,
                "net_lmd_per_day": -9000, "orundum_shard_balance": 0.2,
                "pure_gold_balance": 0.1, "trading_levels": [3, 3],
                "power": {"spare_power": 0, "fixed_right_consumption": 40},
                "factory_levels": [3, 3, 2, 2, 1], "power_plant_levels": [3, 3],
                "dormitory_levels": [1, 1, 1, 1], "product_split": {},
                "plan": {
                    "plan_id": "runner-test-plan",
                    "title": "测试排班",
                    "facility_configuration": {
                        "rooms": {
                            "trading_post_1": {"facility_id": "trading_post", "product_id": "lmd_order", "level": 3},
                            "control_center": {"facility_id": "control_center", "product_id": "base_management", "level": 5},
                        },
                        "dormitories": [{"room_id": f"dormitory_{i}", "level": 1} for i in range(1, 5)],
                    },
                    "segments": {
                        segment_id: {
                            "start": start, "end": end,
                            "rooms": {
                                "trading_post_1": {"operators": []},
                                "control_center": {"operators": []},
                            },
                        }
                        for segment_id, start, end in (
                            ("segment_1", "08:00", "14:00"),
                            ("segment_2", "14:00", "20:00"),
                            ("segment_3", "20:00", "08:00"),
                        )
                    },
                    "recovery_plan": {
                        "events": [
                            {
                                "segment_id": segment_id,
                                "dormitory_id": "dormitory_1",
                                "operators": ["测试"],
                            }
                            for segment_id in ("segment_1", "segment_2", "segment_3")
                        ],
                        "repeating_day_verified": True,
                        "automation_rules_used": False,
                    },
                    "simulation": {"drone_plan": {"allocations": []}},
                },
                "solver_result": {
                    "solver": {
                        "optimality_claim": "best_found_within_truncated_candidate_library",
                        "candidate_library_complete": False,
                        "proxy_models_solved_to_gap": False,
                        "actual_simulation_global_optimality_proven": False,
                    },
                    "selected_solution": {"simulation": {
                        "drone_plan": {"feasible": True},
                        "dormitory_plan": {
                            "repeating_day_verified": True,
                            "automation_rules_used": False,
                        },
                    }},
                },
            },
            "results": [], "failures": [], "limitations": [],
            "reproducibility": {"manifest_schema_version": 1},
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            roster = root / "roster.tsv"
            roster.write_text("干员名称\t是否已招募\t等级\t精英化等级\n测试\tTRUE\t1\t0\n", encoding="utf-8")
            config = root / "config.json"
            config.write_text(json.dumps({
                "schema_version": 1,
                "mode": "layout_search",
                "roster": "roster.tsv",
                "objective": {
                    "goal": "测试",
                    "online_times": ["08:00", "14:00", "20:00"],
                    "minimum_net_lmd_per_day": -10000,
                    "minimum_originium_shard_balance": 0,
                    "minimum_pure_gold_balance": 0,
                    "max_daily_work_hours": 18,
                },
                "base_state": {
                    "drone_capacity": 235,
                    "initial_drone_stock": 0,
                    "dormitory_levels": [1, 1, 1, 1],
                    "right_side_levels": {
                        "reception_room": 1,
                        "office": 1,
                        "training_room": 1,
                        "workshop": 1,
                    },
                    "right_side_levels_confirmed": True,
                },
                "horizon": {"mode": "steady_state"},
                "profiles": {"mode": "representative"},
            }, ensure_ascii=False), encoding="utf-8")
            with patch("run_project.search_layouts", return_value=fake_result):
                summary = run_project(config, output_dir=root / "output")
            self.assertIn(summary["audit_status"], {"passed", "passed_with_warnings"})
            for name in ("result.json", "audit.json", "coverage.json", "report.md", "schedule.json", "summary.json", "config.resolved.json", "unmodeled-relevant-skills.json"):
                self.assertTrue((root / "output" / name).exists())


class ProjectCoverageAuditTests(unittest.TestCase):
    def test_project_manifest_and_complete_coverage_avoid_warnings(self):
        value = {
            "search_type": "outer_layout_configuration_plus_inner_hybrid_schedule_solver",
            "selected": {
                "orundum_per_day": 100.0,
                "net_lmd_per_day": 0.0,
                "orundum_shard_balance": 0.0,
                "pure_gold_balance": 0.0,
                "solver_result": {
                    "solver": {
                        "actual_simulation_global_optimality_proven": False,
                        "optimality_claim": "best_found_within_truncated_candidate_library",
                    },
                    "selected_solution": {"simulation": {"drone_plan": {"feasible": True}}},
                },
            },
            "objective": {"constraints": {}},
            "project_reproducibility": {"configuration_sha256": "abc"},
            "project_data_coverage": {
                "roster": {"operator_coverage_ratio": 1.0},
                "unlocked_skill_coverage": {},
            },
        }
        audited = audit_result(value)
        warning_codes = {item["code"] for item in audited["checks"] if not item["ok"] and item["severity"] == "warning"}
        self.assertNotIn("reproducibility_manifest_present", warning_codes)
        self.assertNotIn("operator_data_coverage_complete", warning_codes)


if __name__ == "__main__":
    unittest.main()
