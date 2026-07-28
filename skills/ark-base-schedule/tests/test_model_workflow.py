#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from compare_plans import DEFAULT_PROFILES, compare
from data_loader import OwnedOperator
from evaluate_plan import evaluate_plan
from normalize_input import build_decision_packet
from plan_utils import normalize_candidate_plan
from schedule_generator import ScheduleGenerator
from schedule_validator import validate_schedule


class ModelWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster_path = SKILL_ROOT / "samples" / "sample_干员练度表.txt"
        names = [
            "但书","龙舌兰","巫恋","鸿雪","图耶","绮良","月见夜","空爆","玫兰莎","慕斯",
            "清流","温蒂","森蚺","冬时","娜斯提","多萝西","斯卡蒂","幽灵鲨","乌尔比安","安哲拉",
            "砾","斑点","夜烟","温米","苍苔","引星棘刺","野鬃","灰毫","芬","泡普卡",
            "桃金娘","杜林","褐果","推进之王","摩根","贝洛内","伺夜","缪尔赛思",
            "正义骑士号","戴菲恩","八幡海铃","歌蕾蒂娅","可露希尔","雪雉","拜松",
        ]
        cls.roster = [OwnedOperator(name, 2) for name in names]

    def _candidate(self):
        plan = ScheduleGenerator(self.roster, "243", "gold_record", 2).generate()
        plan.pop("validation", None)
        plan["schema_version"] = 3
        plan["plan_id"] = "candidate-a"
        plan["title"] = "效率基准候选"
        plan["decision"] = {
            "strategy": "优先房间效率",
            "rationale": ["使用备用生成器提供基准组合"],
            "tradeoffs": ["允许跨班复用并产生警告"],
            "external_evidence_ids": [],
        }
        return normalize_candidate_plan(plan, self.roster)

    def test_decision_packet_declares_model_ownership(self):
        packet = build_decision_packet(
            self.roster_path,
            "赚钱+经验书",
            "243",
            2,
            {"priority": "low_operation"},
        )
        self.assertEqual(packet["packet_type"], "model_decision_context")
        self.assertTrue(packet["model_decision_requirements"]["script_ranking_is_advisory"])
        self.assertGreaterEqual(packet["model_decision_requirements"]["candidate_count"]["minimum"], 2)
        self.assertEqual(packet["objective"]["preferences"]["priority"], "low_operation")

    def test_roster_is_authoritative(self):
        plan = self._candidate()
        first_room = next(iter(next(iter(plan["shifts"].values()))["rooms"].values()))
        first_room["operators"][0]["elite"] = 0
        normalized = normalize_candidate_plan(plan, self.roster)
        normalized_shift = next(iter(normalized["shifts"].values()))
        normalized_room = next(iter(normalized_shift["rooms"].values()))
        self.assertEqual(normalized_room["operators"][0]["elite"], 2)

    def test_unowned_operator_is_hard_error(self):
        plan = self._candidate()
        first_shift = next(iter(plan["shifts"].values()))
        first_room = next(iter(first_shift["rooms"].values()))
        first_room["operators"][0] = {
            "name": "不存在于干员表的测试干员",
            "elite": 2,
            "roster_verified": False,
        }
        report = validate_schedule(plan)
        self.assertTrue(any("不在提供的已招募干员表" in item for item in report["errors"]))

    def test_evaluate_and_compare_are_advisory(self):
        candidate_a = self._candidate()
        candidate_b = copy.deepcopy(candidate_a)
        candidate_b["plan_id"] = "candidate-b"
        candidate_b["title"] = "低覆盖候选"
        candidate_b["decision"]["strategy"] = "减少人员使用"
        first_shift = next(iter(candidate_b["shifts"].values()))
        first_room = next(iter(first_shift["rooms"].values()))
        first_room["operators"] = first_room["operators"][:-1]

        evaluation_a = evaluate_plan(candidate_a)
        evaluation_b = evaluate_plan(candidate_b)
        self.assertTrue(evaluation_a["valid"])
        self.assertTrue(evaluation_b["valid"])
        self.assertGreater(
            evaluation_a["metrics"]["coverage_ratio"],
            evaluation_b["metrics"]["coverage_ratio"],
        )

        comparison = compare([evaluation_a, evaluation_b], DEFAULT_PROFILES["balanced"])
        self.assertEqual(comparison["decision_owner"], "language_model_with_user_preferences")
        self.assertEqual(comparison["comparison_type"], "advisory_script_ranking")
        self.assertEqual(len(comparison["ranking"]), 2)


if __name__ == "__main__":
    unittest.main()
