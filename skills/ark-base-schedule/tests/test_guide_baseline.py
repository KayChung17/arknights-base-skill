#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from compare_to_baseline import compare_to_baseline
from data_loader import OwnedOperator
from normalize_input import build_decision_packet
from plan_utils import normalize_candidate_plan
from schedule_validator import validate_schedule
from timeline_utils import build_operation_timeline, get_strategy_template


class GuideBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster_path = SKILL_ROOT / "samples" / "sample_干员练度表.txt"

    def test_gold_origin_uses_342_guide_baseline(self):
        packet = build_decision_packet(
            self.roster_path,
            "赚钱+搓玉",
            None,
            3,
            {"priority": "guide_fidelity"},
            ["08:00", "14:00", "20:00"],
        )
        self.assertEqual(packet["objective"]["layout"], "342")
        self.assertEqual(packet["baseline"]["reference_id"], "guide_342_orundum_3_login")
        rooms = packet["facility_configuration"]["rooms"]
        trade_levels = sorted(
            item["level"] for item in rooms.values() if item["facility_id"] == "trading_post"
        )
        factory_levels = sorted(
            item["level"] for item in rooms.values() if item["facility_id"] == "factory"
        )
        self.assertEqual(trade_levels, [1, 3, 3])
        self.assertEqual(factory_levels, [2, 2, 3, 3])

    def test_three_online_nodes_are_not_forced_to_equal_shifts(self):
        nodes, segments = build_operation_timeline(["08:00", "14:00", "20:00"])
        self.assertEqual(len(nodes), 3)
        self.assertEqual([item["hours"] for item in segments.values()], [6.0, 6.0, 12.0])

    def _empty_baseline_plan(self):
        template = get_strategy_template("guide_342_orundum_3_login")
        nodes, segments = build_operation_timeline(["08:00", "14:00", "20:00"])
        for segment in segments.values():
            segment["rooms"] = {
                room_id: {"operators": []}
                for room_id, info in template["facility_configuration"]["rooms"].items()
                if info["facility_id"] != "dormitory"
            }
        return {
            "schema_version": 4,
            "plan_id": "guide-structure",
            "title": "攻略结构测试",
            "plan_status": "candidate",
            "layout": "342",
            "goal": "gold_origin",
            "decision": {"strategy": "攻略基线", "rationale": [], "tradeoffs": []},
            "baseline": {"reference_id": "guide_342_orundum_3_login", "deviations": []},
            "facility_configuration": copy.deepcopy(template["facility_configuration"]),
            "operation_nodes": nodes,
            "segments": segments,
            "recovery_plan": {"events": [], "repeating_day_verified": False},
            "economy_projection": {
                "source": "verified_guide",
                "daily": {"lmd_orders_lmd": 47000, "pure_gold_lmd_equivalent": 44000, "orundum": 535, "battle_record_exp": 14000},
                "costs": {"orirock_cube": 100, "lmd": 80000},
                "inventory_delta": {"orundum_shard": -4},
                "warehouse_overflow_checked": True,
                "drone_policy": "按攻略基线分配"
            },
            "assumptions": {"repeating_daily": True}
        }

    def test_baseline_comparison_detects_structure(self):
        plan = self._empty_baseline_plan()
        comparison = compare_to_baseline(plan, "guide_342_orundum_3_login")
        self.assertTrue(comparison["checks"]["layout"]["matched"])
        self.assertTrue(comparison["checks"]["facility_room_signatures"]["matched"])
        self.assertTrue(comparison["checks"]["segment_hours"]["matched"])

    def test_final_plan_requires_verified_skill_data_and_baseline_comparison(self):
        plan = self._empty_baseline_plan()
        plan["plan_status"] = "final"
        first_segment = next(iter(plan["segments"].values()))
        first_segment["rooms"]["trading_post_1"]["operators"] = [
            {"name": "未收录测试干员", "elite": 2}
        ]
        normalized = normalize_candidate_plan(plan, [OwnedOperator("未收录测试干员", 2)])
        report = validate_schedule(normalized)
        self.assertTrue(any("技能数据未经验证" in item for item in report["errors"]))
        self.assertTrue(any("compare_to_baseline.py" in item for item in report["errors"]))
        self.assertTrue(any("repeating_day_verified" in item for item in report["errors"]))

    def test_external_verified_skill_can_clear_unknown_skill_gate(self):
        plan = self._empty_baseline_plan()
        first_segment = next(iter(plan["segments"].values()))
        first_segment["rooms"]["trading_post_1"]["operators"] = [
            {"name": "未收录测试干员", "elite": 2}
        ]
        plan["external_skill_evidence"] = [{
            "operator": "未收录测试干员",
            "facility_id": "trading_post",
            "product_ids": ["lmd_order"],
            "source_id": "current-guide-1",
            "verified": True
        }]
        normalized = normalize_candidate_plan(plan, [OwnedOperator("未收录测试干员", 2)])
        report = validate_schedule(normalized)
        self.assertFalse(any("技能数据未经验证" in item for item in report["warnings"]))
        self.assertTrue(any("外部已验证技能证据" in item for item in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
