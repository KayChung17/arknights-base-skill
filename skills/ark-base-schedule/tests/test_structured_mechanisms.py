#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from coverage_report import build_relevant_unmodeled_report, skill_model_status
from efficiency_calculator import EfficiencyCalculator
from data_loader import OwnedOperator, apply_roster_overrides, operator_index, select_available_skills
from effect_resolver import EffectContribution, resolve_effects
from optimizer_common import warehouse_capacity
from preflight import PreflightError
from run_project import run_project


class StructuredMechanismTests(unittest.TestCase):
    @staticmethod
    def assigned(name: str, elite: int, facility: str) -> dict:
        return {"name": name, "elite": elite, "level": 90, "assigned_facility": facility}

    def test_greyy_alter_drone_capacity_step_bonus_boundaries(self) -> None:
        expected = {0: 0, 9: 0, 10: 1, 235: 23, 250: 25, 300: 25}
        for capacity, bonus in expected.items():
            with self.subTest(capacity=capacity):
                result = EfficiencyCalculator(
                    "power_plant",
                    ["承曦格雷伊@E0"],
                    "drone_recovery",
                    power_plant_count=2,
                    drone_capacity=capacity,
                ).compute()
                self.assertEqual(result["estimated_efficiency_bonus_pct"], bonus)

    def test_model_status_distinguishes_zero_and_unmodeled(self) -> None:
        self.assertEqual(skill_model_status({"base_bonus_pct": 0, "tags": [], "description": "效率不变"}), "description_only")
        self.assertEqual(skill_model_status({"model_status": "verified_zero", "description": "无收益"}), "verified_zero")
        self.assertEqual(skill_model_status({"mechanism": {"type": "step_bonus"}}), "structured")

    def test_effect_resolver_supports_max_and_add(self) -> None:
        values, sources = resolve_effects([
            EffectContribution("trade", "max", 7, "明椒"),
            EffectContribution("trade", "max", 2, "若叶睦"),
            EffectContribution("factory", "add", 2, "甲"),
            EffectContribution("factory", "add", 3, "乙"),
        ])
        self.assertEqual(values, {"trade": 7, "factory": 5})
        self.assertEqual(sources["trade"], ["明椒"])

    def test_same_kind_control_trade_effect_uses_highest_value(self) -> None:
        trading = [self.assigned("古米", 0, "trading_post")]
        global_ops = trading + [
            self.assigned("明椒", 0, "control_center"),
            self.assigned("若叶睦", 0, "control_center"),
        ]
        result = EfficiencyCalculator(
            "trading_post", trading, "lmd_order",
            trading_post_count=3, power_plant_count=2,
            global_operators=global_ops,
        ).compute()
        self.assertEqual(result["layers"]["global_bonus_pct"], 7)
        self.assertIn("明椒/朝气蓬勃", "".join(result["warnings"]))

    def test_control_skill_only_activates_in_control_center(self) -> None:
        trading = [self.assigned("古米", 0, "trading_post")]
        misplaced = self.assigned("明椒", 0, "trading_post")
        result = EfficiencyCalculator(
            "trading_post", trading, "lmd_order",
            global_operators=trading + [misplaced],
        ).compute()
        self.assertEqual(result["layers"]["global_bonus_pct"], 0)

    def test_hachiman_e2_counts_siracusa_operators_in_each_trading_post(self) -> None:
        trading = [
            self.assigned("巫恋", 2, "trading_post"),
            self.assigned("拉普兰德", 1, "trading_post"),
            self.assigned("古米", 0, "trading_post"),
        ]
        for elite, expected in ((1, 0), (2, 10)):
            with self.subTest(elite=elite):
                result = EfficiencyCalculator(
                    "trading_post", trading, "lmd_order",
                    global_operators=trading + [self.assigned("八幡海铃", elite, "control_center")],
                ).compute()
                self.assertEqual(result["layers"]["global_bonus_pct"], expected)

    def test_siracusa_group_data_covers_owned_link_candidates(self) -> None:
        index = operator_index()
        for name in ("巫恋", "拉普兰德", "红云", "贾维", "子月", "伺夜", "阿罗玛", "复奏"):
            with self.subTest(name=name):
                self.assertIn("siracusa", index[name]["groups"])

    def test_roster_override_unlocks_scenario_without_editing_workbook(self) -> None:
        original = [OwnedOperator("八幡海铃", elite=1, level=70)]
        updated = apply_roster_overrides(original, {"八幡海铃": {"elite": 2}})
        self.assertEqual(updated[0].elite, 2)
        self.assertEqual(updated[0].level, 70)
        self.assertEqual(original[0].elite, 1)

    def test_skill_slot_selects_beta_and_keeps_independent_skill(self) -> None:
        index = operator_index()
        mingjiao = select_available_skills(index["明椒"], "trading_post", 2, "lmd_order", 90)
        self.assertEqual([skill["skill_name"] for skill in mingjiao], ["裁缝·β"])
        gladiia = select_available_skills(index["歌蕾蒂娅"], "control_center", 2, "", 90)
        names = {skill["skill_name"] for skill in gladiia}
        self.assertIn("集群狩猎·β", names)
        self.assertIn("潮汐守望", names)
        self.assertNotIn("集群狩猎·α", names)

    def test_gladiia_alpha_beta_special_bonus_scales_per_abyssal_operator(self) -> None:
        factory = [
            self.assigned("乌尔比安", 0, "factory"),
            self.assigned("安哲拉", 0, "factory"),
        ]
        expected = {0: 20, 2: 40}
        for elite, bonus in expected.items():
            with self.subTest(elite=elite):
                result = EfficiencyCalculator(
                    "factory", factory, "orundum_shard",
                    global_operators=factory + [self.assigned("歌蕾蒂娅", elite, "control_center")],
                ).compute()
                self.assertEqual(result["layers"]["global_bonus_pct"], bonus)

    def test_jaye_and_snowant_do_not_amplify_each_other_alone(self) -> None:
        jaye_snowant = [
            self.assigned("孑", 1, "trading_post"),
            self.assigned("雪雉", 2, "trading_post"),
        ]
        alone = EfficiencyCalculator(
            "trading_post", jaye_snowant, "lmd_order", global_operators=jaye_snowant,
        ).compute()
        self.assertEqual(alone["layers"]["amplifier_bonus_pct"], 0)

        with_other = jaye_snowant + [self.assigned("古米", 0, "trading_post")]
        combined = EfficiencyCalculator(
            "trading_post", with_other, "lmd_order", global_operators=with_other,
        ).compute()
        self.assertEqual(combined["layers"]["amplifier_bonus_pct"], 30)

    def test_remaining_project_blocking_mechanisms_are_calculated(self) -> None:
        gold = [self.assigned("娜仁图亚", 2, "factory")]
        narantuya = EfficiencyCalculator(
            "factory", gold, "pure_gold",
            dormitory_levels=[1, 2, 3, 4], global_operators=gold,
        ).compute()
        self.assertEqual(narantuya["layers"]["facility_bonus_pct"], 10)

        trading = [
            self.assigned("吉星", 0, "trading_post"),
            self.assigned("古米", 0, "trading_post"),
            self.assigned("夜刀", 0, "trading_post"),
        ]
        jixing = EfficiencyCalculator(
            "trading_post", trading, "lmd_order", global_operators=trading,
        ).compute()
        self.assertEqual(jixing["operator_details"][0]["direct_bonus_pct"], 20)

        shu = [self.assigned("黍", 2, "factory")]
        sui_workers = shu + [
            self.assigned("重岳", 0, "control_center"),
            self.assigned("望", 0, "control_center"),
            self.assigned("年", 2, "power_plant"),
        ]
        shu_result = EfficiencyCalculator(
            "factory", shu, "orundum_shard", global_operators=sui_workers,
        ).compute()
        self.assertEqual(shu_result["layers"]["global_bonus_pct"], 6)

    def test_karlan_three_member_room_receives_control_bonus(self) -> None:
        trading = [
            self.assigned("银灰", 0, "trading_post"),
            self.assigned("讯使", 0, "trading_post"),
            self.assigned("角峰", 0, "trading_post"),
        ]
        global_ops = trading + [self.assigned("凛御银灰", 0, "control_center")]
        result = EfficiencyCalculator(
            "trading_post", trading, "lmd_order", global_operators=global_ops,
        ).compute()
        self.assertEqual(result["layers"]["global_bonus_pct"], 10)

    def test_dynamic_capacity_mechanisms_use_room_state(self) -> None:
        star = [self.assigned("溯光星源", 2, "factory")]
        room = {"facility_id": "factory", "product_id": "battle_record", "level": 3}
        self.assertEqual(warehouse_capacity(room, star), 59)

        trading = [
            self.assigned("孑", 1, "trading_post"),
            self.assigned("瑰盐", 0, "trading_post"),
        ]
        result = EfficiencyCalculator(
            "trading_post", trading, "lmd_order", facility_level=3,
            global_operators=trading,
        ).compute()
        self.assertEqual(result["layers"]["global_bonus_pct"], 64)

    def test_control_center_morale_rules_are_structured(self) -> None:
        control = [
            self.assigned("若叶睦", 2, "control_center"),
            self.assigned("丰川祥子", 2, "control_center"),
            self.assigned("杜宾", 0, "control_center"),
        ]
        calculator = EfficiencyCalculator(
            "control_center", control, "base_management", global_operators=control,
        )
        rates = calculator.morale_cost_rates()
        self.assertAlmostEqual(rates["若叶睦"], 0.80)
        self.assertAlmostEqual(rates["丰川祥子"], 0.85)
        self.assertAlmostEqual(rates["杜宾"], 0.80)

    def test_relevant_unmodeled_report_deduplicates_and_flags_numeric_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "roster.tsv"
            roster.write_text("干员名称\t是否已招募\t等级\t精英化等级\n测试干员\tTRUE\t1\t0\n", encoding="utf-8")
            record = {
                "name": "测试干员",
                "skills": [{
                    "facility": "power_plant",
                    "elite": 0,
                    "required_level": 1,
                    "skill_name": "动态技能",
                    "variant_group": "power:test",
                    "description": "每10架无人机上限+1%充能速度",
                    "base_bonus_pct": 0,
                    "tags": [],
                    "products": ["drone_recovery"],
                }],
            }
            config = {"mode": "layout_search", "objective": {}, "verification": {"relevant_unmodeled_skill_policy": "warn"}}
            with patch("coverage_report.operator_index", return_value={"测试干员": record}):
                report = build_relevant_unmodeled_report(roster, config)
            self.assertEqual(report["unmodeled_count"], 1)
            self.assertEqual(report["blocking_count"], 1)
            self.assertEqual(report["skills"][0]["risk_level"], "blocking")

    def test_block_policy_stops_before_solver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roster = root / "roster.tsv"
            roster.write_text("干员名称\t是否已招募\t等级\t精英化等级\n测试干员\tTRUE\t1\t0\n", encoding="utf-8")
            config = root / "project.json"
            config.write_text(json.dumps({
                "schema_version": 1,
                "mode": "layout_search",
                "roster": "roster.tsv",
                "objective": {
                    "goal": "测试",
                    "online_times": ["08:00", "14:00", "20:00"],
                    "minimum_net_lmd_per_day": 0,
                    "minimum_originium_shard_balance": 0,
                    "minimum_pure_gold_balance": 0,
                    "max_daily_work_hours": 18,
                },
                "base_state": {
                    "drone_capacity": 235,
                    "initial_drone_stock": 0,
                    "dormitory_levels": [1, 1, 1, 1],
                    "right_side_levels": {"reception_room": 1, "office": 1, "training_room": 1, "workshop": 1},
                    "right_side_levels_confirmed": True,
                },
                "horizon": {"mode": "steady_state"},
                "profiles": {"mode": "representative"},
                "verification": {"relevant_unmodeled_skill_policy": "block"},
            }, ensure_ascii=False), encoding="utf-8")
            risk = {
                "policy": "block",
                "unmodeled_count": 1,
                "blocking_count": 1,
                "warning_count": 0,
                "skills": [{"operator": "测试干员", "risk_level": "blocking"}],
            }
            with patch("run_project.build_relevant_unmodeled_report", return_value=risk), patch("run_project.search_layouts") as solve:
                with self.assertRaises(PreflightError) as caught:
                    run_project(config, output_dir=root / "output")
            self.assertEqual(caught.exception.report["status"], "execution_blocked")
            solve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
