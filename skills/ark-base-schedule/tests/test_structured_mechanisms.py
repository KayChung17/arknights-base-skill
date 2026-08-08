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
from validate_data import granted_effect_conflicts, singleton_semantic_tag_conflicts
from tag_registry import registration_for, unregistered_tags
from data_loader import load_operator_data


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

    def test_every_asset_tag_has_a_declared_consumer(self) -> None:
        data = load_operator_data()
        tags = {
            str(tag)
            for operator in data["operators"]
            for skill in operator.get("skills", [])
            for tag in skill.get("tags", [])
        }
        self.assertEqual(unregistered_tags(tags), [])
        self.assertIsNone(registration_for("unreviewed_magic_tag"))

    def test_mantra_counts_elite_operator_faction_facilities(self) -> None:
        mantra = {
            "name": "真言", "elite": 2, "level": 90,
            "assigned_facility": "trading_post", "assigned_room_id": "trading_post_1",
        }
        operators = [
            mantra,
            {"name": "煌", "elite": 0, "level": 1, "assigned_facility": "factory", "assigned_room_id": "factory_1"},
            {"name": "逻各斯", "elite": 2, "level": 90, "assigned_facility": "factory", "assigned_room_id": "factory_1"},
            {"name": "迷迭香", "elite": 0, "level": 1, "assigned_facility": "dormitory", "assigned_room_id": "dormitory_1"},
            {"name": "但书", "elite": 2, "level": 80, "assigned_facility": "factory", "assigned_room_id": "factory_2"},
            {"name": "烛煌", "elite": 2, "level": 90, "assigned_facility": "activity_room", "assigned_room_id": "activity_room"},
        ]
        result = EfficiencyCalculator(
            "trading_post", [mantra], "lmd_order", global_operators=operators,
        ).compute()
        self.assertEqual(result["paper_bonus_pct"], 31)
        detail = result["operator_details"][0]
        self.assertIn("有效设施 3 间", "；".join(detail["notes"]))

    def test_mantra_elite_operator_facility_bonus_caps_at_ten(self) -> None:
        mantra = {
            "name": "真言", "elite": 2, "level": 90,
            "assigned_facility": "trading_post", "assigned_room_id": "trading_post_1",
        }
        members = ["电弧", "煌", "机械师", "逻各斯", "迷迭香", "烛煌"]
        operators = [mantra] + [
            {
                "name": members[index % len(members)], "elite": index % 3, "level": 90,
                "assigned_facility": "factory", "assigned_room_id": f"room_{index}",
            }
            for index in range(12)
        ]
        result = EfficiencyCalculator(
            "trading_post", [mantra], "lmd_order", global_operators=operators,
        ).compute()
        self.assertEqual(result["paper_bonus_pct"], 45)

    def test_mantra_e2_skill_replaces_e0_order_distribution(self) -> None:
        mantra = operator_index()["真言"]
        e0 = select_available_skills(mantra, "trading_post", 0, "lmd_order", 1)
        e2 = select_available_skills(mantra, "trading_post", 2, "lmd_order", 90)
        self.assertEqual([skill["skill_name"] for skill in e0], ["订单分发·α"])
        self.assertEqual([skill["skill_name"] for skill in e2], ["精英小队"])

    def test_automation_uses_skill_stage_and_eunectes_virtual_plants(self) -> None:
        cases = (("异客", 2, 5), ("森蚺", 0, 5), ("森蚺", 2, 10), ("温蒂", 0, 10), ("温蒂", 2, 15))
        for name, elite, per_plant in cases:
            with self.subTest(name=name, elite=elite):
                worker = self.assigned(name, elite, "factory")
                result = EfficiencyCalculator(
                    "factory", [worker], "pure_gold", power_plant_count=2,
                    global_operators=[worker],
                ).compute()
                self.assertEqual(result["layers"]["facility_bonus_pct"], per_plant * 2)

        worker = self.assigned("森蚺", 2, "factory")
        controller = self.assigned("森蚺", 2, "control_center")
        lancet = self.assigned("Lancet-2", 0, "power_plant")
        virtual = EfficiencyCalculator(
            "factory", [worker], "pure_gold", power_plant_count=2,
            global_operators=[worker, controller, lancet],
        ).compute()
        self.assertEqual(virtual["layers"]["facility_bonus_pct"], 40)

    def test_sui_morale_threshold_switches_non_consuming_chains(self) -> None:
        jieyun = self.assigned("截云", 2, "factory")
        ling_high = dict(self.assigned("令", 2, "control_center"), morale=13)
        high = EfficiencyCalculator(
            "factory", [jieyun], "battle_record", global_operators=[jieyun, ling_high],
        ).compute()
        self.assertEqual(high["intermediate_products"]["human_fireworks"], 15)
        self.assertEqual(high["intermediate_products"]["witchcraft_crystal"], 3)

        ling_low = dict(self.assigned("令", 2, "control_center"), morale=12)
        rosemary = self.assigned("迷迭香", 2, "factory")
        low = EfficiencyCalculator(
            "factory", [rosemary], "battle_record", global_operators=[rosemary, ling_low],
        ).compute()
        self.assertEqual(low["intermediate_products"]["perception_information"], 10)
        self.assertEqual(low["intermediate_products"]["thought_chain"], 10)

    def test_sui_control_morale_immunity_keeps_room_recovery(self) -> None:
        dusk = self.assigned("夕", 0, "control_center")
        without_ling = EfficiencyCalculator(
            "control_center", [dusk], "base_management", global_operators=[dusk],
        ).morale_cost_rates()
        self.assertAlmostEqual(without_ling["夕"], 1.40)

        ling = self.assigned("令", 2, "control_center")
        with_ling = EfficiencyCalculator(
            "control_center", [ling, dusk], "base_management", global_operators=[ling, dusk],
        ).morale_cost_rates()
        self.assertAlmostEqual(with_ling["夕"], 0.85)
        self.assertAlmostEqual(with_ling["令"], 0.85)

    def test_order_capacity_converters_ignore_negative_capacity(self) -> None:
        swire = self.assigned("琳琅诗怀雅", 2, "trading_post")
        steward = self.assigned("史都华德", 0, "trading_post")
        dagger = self.assigned("锏", 2, "trading_post")
        positive = EfficiencyCalculator(
            "trading_post", [swire, steward], "lmd_order", global_operators=[swire, steward],
        ).compute()
        self.assertEqual(positive["positive_order_capacity_increase"], 5)
        self.assertEqual(positive["layers"]["global_bonus_pct"], 20)
        mixed = EfficiencyCalculator(
            "trading_post", [dagger, steward], "lmd_order", global_operators=[dagger, steward],
        ).compute()
        self.assertEqual(mixed["positive_order_capacity_increase"], 5)
        self.assertEqual(mixed["order_capacity_decrease"], 6)
        self.assertEqual(mixed["layers"]["global_bonus_pct"], 25)

    def test_hongxue_lines_require_hongxue_source(self) -> None:
        durin = self.assigned("桃金娘", 0, "office")
        hongxue = self.assigned("鸿雪", 2, "trading_post")
        active = EfficiencyCalculator(
            "trading_post", [hongxue], "lmd_order", global_operators=[hongxue, durin],
        ).compute()
        self.assertEqual(active["layers"]["global_bonus_pct"], 5)

        qiliang = self.assigned("齐良", 2, "trading_post")
        inactive = EfficiencyCalculator(
            "trading_post", [qiliang], "lmd_order", global_operators=[qiliang, durin],
        ).compute()
        self.assertEqual(inactive["layers"]["global_bonus_pct"], 0)

    def test_dorm_level_and_skill_category_cross_facility_formulas(self) -> None:
        archetto = self.assigned("空弦", 2, "trading_post")
        trade = EfficiencyCalculator(
            "trading_post", [archetto], "lmd_order", dormitory_levels=[5, 4, 3, 2],
            global_operators=[archetto],
        ).compute()
        self.assertEqual(trade["layers"]["facility_bonus_pct"], 28)

        philae = self.assigned("菲莱", 2, "power_plant")
        power = EfficiencyCalculator(
            "power_plant", [philae], "drone_recovery", dormitory_levels=[5, 4, 3, 2],
            global_operators=[philae],
        ).compute()
        self.assertEqual(power["estimated_efficiency_bonus_pct"], 17)

        dorothy = self.assigned("多萝西", 2, "factory")
        muelsyse = self.assigned("缪尔赛思", 0, "factory")
        category = EfficiencyCalculator(
            "factory", [dorothy, muelsyse], "battle_record", global_operators=[dorothy, muelsyse],
        ).compute()
        self.assertGreaterEqual(category["layers"]["global_bonus_pct"], 5)

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

    def test_new_full_table_control_factory_effect_conditions(self) -> None:
        factory = [self.assigned("夜烟", 0, "factory")]

        mon3tr = self.assigned("Mon3tr", 2, "control_center")
        unconditional = EfficiencyCalculator(
            "factory", factory, "pure_gold",
            global_operators=factory + [mon3tr],
        ).compute()
        self.assertEqual(unconditional["layers"]["global_bonus_pct"], 2)

        pudding = self.assigned("布丁", 1, "control_center")
        one_platform = self.assigned("Lancet-2", 0, "power_plant")
        inactive = EfficiencyCalculator(
            "factory", factory, "pure_gold",
            global_operators=factory + [pudding, one_platform],
        ).compute()
        self.assertEqual(inactive["layers"]["global_bonus_pct"], 0)
        two_platforms = one_platform, self.assigned("Castle-3", 0, "power_plant")
        active = EfficiencyCalculator(
            "factory", factory, "pure_gold",
            global_operators=factory + [pudding, *two_platforms],
        ).compute()
        self.assertEqual(active["layers"]["global_bonus_pct"], 2)

        hoshi = self.assigned("斩业星熊", 2, "control_center")
        chen = self.assigned("陈", 2, "control_center")
        companion = EfficiencyCalculator(
            "factory", factory, "pure_gold",
            global_operators=factory + [hoshi, chen],
        ).compute()
        self.assertEqual(companion["layers"]["global_bonus_pct"], 3)

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

    def test_bellone_vigil_conditions_and_independent_debt_skill(self) -> None:
        bellone = self.assigned("贝洛内", 2, "trading_post")
        alone = EfficiencyCalculator(
            "trading_post", [bellone], "lmd_order", global_operators=[bellone],
        )
        alone_result = alone.compute()
        self.assertEqual(alone_result["paper_bonus_pct"], 30)
        self.assertEqual(alone_result["order_capacity"], 10)

        vigil_office = self.assigned("伺夜", 2, "office")
        anywhere_result = EfficiencyCalculator(
            "trading_post", [bellone], "lmd_order",
            global_operators=[bellone, vigil_office],
        ).compute()
        self.assertEqual(anywhere_result["paper_bonus_pct"], 40)
        self.assertEqual(anywhere_result["order_capacity"], 10)

        vigil_same_room = self.assigned("伺夜", 2, "trading_post")
        same_room = EfficiencyCalculator(
            "trading_post", [bellone, vigil_same_room], "lmd_order",
            global_operators=[bellone, vigil_same_room],
        )
        same_room_result = same_room.compute()
        self.assertEqual(same_room_result["order_capacity"], 12)
        self.assertAlmostEqual(same_room.morale_cost_rates()["贝洛内"], 0.85)

    def test_vigil_trade_bonus_reads_reception_room_level(self) -> None:
        vigil = self.assigned("伺夜", 2, "trading_post")
        for level, expected in ((1, 30), (2, 35), (3, 40)):
            with self.subTest(level=level):
                result = EfficiencyCalculator(
                    "trading_post", [vigil], "lmd_order",
                    reception_room_level=level,
                    global_operators=[vigil],
                ).compute()
                self.assertEqual(result["paper_bonus_pct"], expected)

    def test_wisadel_control_skill_adds_capacity_only_to_hoederer_room(self) -> None:
        wisadel = self.assigned("维什戴尔", 2, "control_center")
        hoederer = self.assigned("赫德雷", 2, "trading_post")
        other = self.assigned("但书", 2, "trading_post")
        hoederer_result = EfficiencyCalculator(
            "trading_post", [hoederer], "lmd_order",
            global_operators=[wisadel, hoederer],
        ).compute()
        other_result = EfficiencyCalculator(
            "trading_post", [other], "lmd_order",
            global_operators=[wisadel, other],
        ).compute()
        self.assertEqual(hoederer_result["order_capacity"], 12)
        self.assertEqual(other_result["order_capacity"], 10)

    def test_black_key_reuses_perception_and_counts_direct_silent_resonance(self) -> None:
        black_key = self.assigned("黑键", 2, "trading_post")
        black_only = EfficiencyCalculator(
            "trading_post", [black_key], "lmd_order",
            dormitory_occupant_count=20,
            global_operators=[black_key],
        ).compute()
        self.assertEqual(black_only["intermediate_products"], {
            "perception_information": 20.0,
            "silent_resonance": 20.0,
        })
        self.assertEqual(black_only["paper_bonus_pct"], 10)

        basline = self.assigned("深律", 2, "office")
        full_chain = EfficiencyCalculator(
            "trading_post", [black_key], "lmd_order",
            office_level=3,
            dormitory_occupant_count=20,
            global_operators=[black_key, basline],
        ).compute()
        self.assertEqual(full_chain["intermediate_products"], {
            "perception_information": 20.0,
            "silent_resonance": 65.0,
        })
        self.assertEqual(full_chain["paper_bonus_pct"], 32)

    def test_black_key_skill_slot_upgrades_without_replacing_musicality(self) -> None:
        skills = select_available_skills(
            operator_index()["黑键"], "trading_post", 2, "lmd_order", 90,
        )
        self.assertEqual({skill["skill_name"] for skill in skills}, {"乐感", "怅惘和声"})

    def test_guide_trade_groups_reproduce_paper_efficiencies(self) -> None:
        shamare_group = [
            self.assigned("巫恋", 2, "trading_post"),
            self.assigned("折光", 2, "trading_post"),
            self.assigned("龙舌兰", 2, "trading_post"),
        ]
        self.assertEqual(EfficiencyCalculator(
            "trading_post", shamare_group, "lmd_order", global_operators=shamare_group,
        ).compute()["paper_bonus_pct"], 90)

        vigil_group = [
            self.assigned("伺夜", 2, "trading_post"),
            self.assigned("贝洛内", 2, "trading_post"),
            self.assigned("但书", 2, "trading_post"),
        ]
        self.assertEqual(EfficiencyCalculator(
            "trading_post", vigil_group, "lmd_order", reception_room_level=3,
            global_operators=vigil_group + [self.assigned("八幡海铃", 2, "control_center")],
        ).compute()["paper_bonus_pct"], 90)

        black_key_group = [
            self.assigned("黑键", 2, "trading_post"),
            self.assigned("吉星", 2, "trading_post"),
            self.assigned("可露希尔", 2, "trading_post"),
        ]
        self.assertEqual(EfficiencyCalculator(
            "trading_post", black_key_group, "lmd_order", office_level=3,
            dormitory_occupant_count=20,
            global_operators=black_key_group + [self.assigned("深律", 2, "office")],
        ).compute()["paper_bonus_pct"], 82)

    def test_siracusa_group_data_covers_owned_link_candidates(self) -> None:
        index = operator_index()
        for name in ("德克萨斯", "巫恋", "拉普兰德", "红云", "贾维", "子月", "伺夜", "阿罗玛", "复奏"):
            with self.subTest(name=name):
                self.assertIn("siracusa", index[name]["groups"])

    def test_laterano_and_texas_room_links_are_counted(self) -> None:
        index = operator_index()
        self.assertIn("laterano", index["安德切尔"]["groups"])
        trading = [
            self.assigned("德克萨斯", 0, "trading_post"),
            self.assigned("拉普兰德", 1, "trading_post"),
            self.assigned("玫兰莎", 1, "trading_post"),
        ]
        result = EfficiencyCalculator(
            "trading_post", trading, "orundum_order", global_operators=trading,
        ).compute()
        self.assertEqual(result["layers"]["direct_bonus_pct"], 90)

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

    def test_huojguo_different_named_factory_skills_stack(self) -> None:
        skills = select_available_skills(operator_index()["褐果"], "factory", 1, "orundum_shard", 1)
        self.assertEqual({skill["skill_name"] for skill in skills}, {"地质学·α", "标准化·α"})

    def test_jaye_different_named_skills_stack_after_e1(self) -> None:
        index = operator_index()["孑"]
        e0 = select_available_skills(index, "trading_post", 0, "lmd_order", 1)
        e1 = select_available_skills(index, "trading_post", 1, "lmd_order", 1)
        self.assertEqual([skill["skill_name"] for skill in e0], ["摊贩经济"])
        self.assertEqual({skill["skill_name"] for skill in e1}, {"市井之道", "摊贩经济"})

    def test_jaye_e1_combines_both_skills_into_constant_limit_bonus(self) -> None:
        jaye = [self.assigned("孑", 1, "trading_post")]
        result = EfficiencyCalculator(
            "trading_post", jaye, "lmd_order", global_operators=jaye,
        ).compute()
        self.assertEqual(result["layers"]["global_bonus_pct"], 40)
        self.assertEqual(result["paper_bonus_pct"], 40)
        self.assertEqual(result["staffing_base_bonus_pct"], 1)
        self.assertNotIn("代理", "".join(result["warnings"]))

    def test_jaye_e1_uses_teammate_efficiency_and_capacity(self) -> None:
        trading = [
            self.assigned("孑", 1, "trading_post"),
            self.assigned("梓兰", 1, "trading_post"),
        ]
        result = EfficiencyCalculator(
            "trading_post", trading, "lmd_order", global_operators=trading,
        ).compute()
        self.assertEqual(result["layers"]["direct_bonus_pct"], 25)
        self.assertEqual(result["layers"]["global_bonus_pct"], 36)
        self.assertEqual(result["paper_bonus_pct"], 61)
        self.assertEqual(result["staffing_base_bonus_pct"], 2)

    def test_gladiia_alpha_beta_special_bonus_scales_per_abyssal_operator(self) -> None:
        factory = [
            self.assigned("乌尔比安", 0, "factory"),
            self.assigned("安哲拉", 0, "factory"),
        ]
        expected = {0: 10, 2: 20}
        for elite, bonus in expected.items():
            with self.subTest(elite=elite):
                result = EfficiencyCalculator(
                    "factory", factory, "orundum_shard",
                    global_operators=factory + [self.assigned("歌蕾蒂娅", elite, "control_center")],
                ).compute()
                self.assertEqual(result["layers"]["direct_bonus_pct"], 0)
                self.assertEqual(result["layers"]["global_bonus_pct"], bonus)
                self.assertEqual(result["paper_bonus_pct"], bonus)

    def test_gladiia_bonus_is_applied_once_per_eligible_factory_room(self) -> None:
        first = self.assigned("乌尔比安", 0, "factory")
        first["assigned_room_id"] = "factory_1"
        second = self.assigned("安哲拉", 0, "factory")
        second["assigned_room_id"] = "factory_2"
        gladiia = self.assigned("歌蕾蒂娅", 2, "control_center")
        global_ops = [first, second, gladiia]

        one_abyssal_room = EfficiencyCalculator(
            "factory", [first], "orundum_shard", global_operators=global_ops,
        ).compute()
        self.assertEqual(one_abyssal_room["layers"]["global_bonus_pct"], 20)

        two_abyssal_same_room = EfficiencyCalculator(
            "factory", [first, second], "orundum_shard", global_operators=global_ops,
        ).compute()
        self.assertEqual(two_abyssal_same_room["layers"]["global_bonus_pct"], 20)

        ordinary_room = [self.assigned("泡普卡", 0, "factory")]
        no_abyssal = EfficiencyCalculator(
            "factory", ordinary_room, "orundum_shard", global_operators=global_ops + ordinary_room,
        ).compute()
        self.assertEqual(no_abyssal["layers"]["global_bonus_pct"], 0)

    def test_abyssal_factory_bonus_requires_gladiia_in_control_center(self) -> None:
        index = operator_index()
        for name in ("乌尔比安", "安哲拉", "斯卡蒂", "幽灵鲨"):
            with self.subTest(name=name):
                self.assertEqual(select_available_skills(index[name], "factory", 2, "orundum_shard", 90), [])
                factory = [self.assigned(name, 2, "factory")]
                result = EfficiencyCalculator(
                    "factory", factory, "orundum_shard", global_operators=factory,
                ).compute()
                self.assertEqual(result["paper_bonus_pct"], 0)
                self.assertEqual(result["layers"]["global_bonus_pct"], 0)

    def test_granted_effect_alias_cannot_be_reintroduced_as_standalone_skill(self) -> None:
        fixture = {
            "operators": [
                {
                    "name": "授予者",
                    "skills": [{
                        "facility": "control_center",
                        "skill_name": "体系技能",
                        "special_rules": [{
                            "type": "group_factory_bonus",
                            "count_facility": "factory",
                            "granted_effect_skill_names": ["体系授予·制造"],
                        }],
                    }],
                },
                {
                    "name": "成员",
                    "skills": [{
                        "facility": "factory",
                        "skill_name": "体系授予·制造",
                        "base_bonus_pct": 20,
                    }],
                },
            ],
        }
        conflicts = granted_effect_conflicts(fixture)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("授予型效果不得作为独立技能计入", conflicts[0])

    def test_red_pine_factory_skill_unlock_stages_are_preserved(self) -> None:
        index = operator_index()
        for name in ("灰毫", "野鬃"):
            with self.subTest(name=name):
                alpha = select_available_skills(index[name], "factory", 0, "battle_record", 1)
                beta = select_available_skills(index[name], "factory", 2, "battle_record", 90)
                self.assertEqual([(s["skill_name"], s["base_bonus_pct"]) for s in alpha], [("红松骑士团·α", 15)])
                self.assertEqual([(s["skill_name"], s["base_bonus_pct"]) for s in beta], [("红松骑士团·β", 25)])

    def test_justice_knight_power_and_wild_mane_link_use_actual_assignment(self) -> None:
        justice = self.assigned("正义骑士号", 0, "power_plant")
        justice["level"] = 30
        power = EfficiencyCalculator(
            "power_plant", [justice], "drone_recovery", global_operators=[justice],
        ).compute()
        self.assertEqual(power["estimated_efficiency_bonus_pct"], 10)

        wild_mane = [self.assigned("野鬃", 2, "factory")]
        active = EfficiencyCalculator(
            "factory", wild_mane, "battle_record", global_operators=wild_mane + [justice],
        ).compute()
        self.assertEqual(active["layers"]["direct_bonus_pct"], 25)
        self.assertEqual(active["layers"]["global_bonus_pct"], 5)

        justice["level"] = 29
        locked = EfficiencyCalculator(
            "factory", wild_mane, "battle_record", global_operators=wild_mane + [justice],
        ).compute()
        self.assertEqual(locked["layers"]["global_bonus_pct"], 0)

    def test_singleton_activation_tag_rejects_legacy_alias_slot(self) -> None:
        fixture = {"operators": [{
            "name": "测试干员",
            "skills": [
                {"skill_name": "正式技能", "variant_group": "slot:a", "tags": ["glasgow_center"]},
                {"skill_name": "旧占位", "variant_group": "slot:b", "tags": ["glasgow_center"]},
            ],
        }]}
        conflicts = singleton_semantic_tag_conflicts(fixture)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("多个独立技能槽", conflicts[0])

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
            self.assigned("孑", 0, "trading_post"),
            self.assigned("瑰盐", 0, "trading_post"),
        ]
        result = EfficiencyCalculator(
            "trading_post", trading, "lmd_order", facility_level=3,
            global_operators=trading,
        ).compute()
        self.assertEqual(result["layers"]["global_bonus_pct"], 52)

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

    def test_greyy_virtual_plant_only_affects_automation(self) -> None:
        factory = [self.assigned("温蒂", 1, "factory")]
        grey = self.assigned("承曦格雷伊", 2, "power_plant")
        with_grey = EfficiencyCalculator(
            "factory", factory, "pure_gold", power_plant_count=2,
            global_operators=factory + [grey],
        ).compute()
        self.assertEqual(with_grey["layers"]["facility_bonus_pct"], 30)

        platform = self.assigned("Lancet-2", 0, "power_plant")
        with_real_platform = EfficiencyCalculator(
            "factory", factory, "pure_gold", power_plant_count=2,
            global_operators=factory + [grey, platform],
        ).compute()
        self.assertEqual(with_real_platform["layers"]["facility_bonus_pct"], 20)

    def test_work_platform_skills_count_actual_platform_operators(self) -> None:
        factory = [self.assigned("阿兰娜", 0, "factory")]
        no_platform = EfficiencyCalculator(
            "factory", factory, "pure_gold", power_plant_count=3,
            global_operators=factory,
        ).compute()
        self.assertEqual(no_platform["layers"]["facility_bonus_pct"], 0)

        platform = self.assigned("Lancet-2", 0, "power_plant")
        one_platform = EfficiencyCalculator(
            "factory", factory, "pure_gold", power_plant_count=3,
            global_operators=factory + [platform],
        ).compute()
        self.assertEqual(one_platform["layers"]["facility_bonus_pct"], 5)

    def test_heat_and_human_fireworks_use_dormitory_occupancy(self) -> None:
        control = [self.assigned("阿米娅", 2, "control_center")]
        trading = [self.assigned("乌有", 2, "trading_post")]
        result = EfficiencyCalculator(
            "trading_post", trading, "lmd_order",
            dormitory_occupant_count=7,
            global_operators=trading + control,
        ).compute()
        detail = next(item for item in result["operator_details"] if item["name"] == "乌有")
        self.assertTrue(any("人间烟火 7" in note for note in detail["notes"]))

    def test_catnip_is_recomputed_from_control_center_crew(self) -> None:
        survey = self.assigned("泰拉大陆调查团", 0, "factory")
        blackhorn = self.assigned("火龙S黑角", 0, "control_center")
        result = EfficiencyCalculator(
            "factory", [survey], "pure_gold",
            global_operators=[survey, blackhorn],
        ).compute()
        # One Monster Hunter member in control gives 2 catnip: 5% base + 2% chain.
        self.assertEqual(result["layers"]["direct_bonus_pct"], 5)
        self.assertEqual(result["layers"]["global_bonus_pct"], 2)

    def test_control_center_room_recovery_and_targeted_pair_stack(self) -> None:
        control = [
            self.assigned("红", 0, "control_center"),
            self.assigned("玛恩纳", 0, "control_center"),
            self.assigned("杜宾", 0, "control_center"),
        ]
        rates = EfficiencyCalculator(
            "control_center", control, "base_management", global_operators=control,
        ).morale_cost_rates()
        for name in ("红", "玛恩纳", "杜宾"):
            self.assertAlmostEqual(rates[name], 0.70)

        pair = [
            self.assigned("魔王", 0, "control_center"),
            self.assigned("阿米娅", 0, "control_center"),
        ]
        pair_rates = EfficiencyCalculator(
            "control_center", pair, "base_management", global_operators=pair,
        ).morale_cost_rates()
        self.assertAlmostEqual(pair_rates["魔王"], 0.85)
        self.assertAlmostEqual(pair_rates["阿米娅"], 0.85)

    def test_mlynar_business_scope_and_extended_control_skills(self) -> None:
        control = [
            self.assigned("玛恩纳", 2, "control_center"),
            self.assigned("红", 0, "control_center"),
        ]
        factory = [self.assigned("砾", 0, "factory")]
        factory_rates = EfficiencyCalculator(
            "factory", factory, "pure_gold", global_operators=control + factory,
        ).morale_cost_rates()
        # Two occupied control slots give -0.10/h. 独善其身 and S.W.E.E.P.
        # each extend another -0.05/h to factories under 公事公办.
        self.assertAlmostEqual(factory_rates["砾"], 0.80)

        power = [self.assigned("Lancet-2", 0, "power_plant")]
        power_rates = EfficiencyCalculator(
            "power_plant", power, "drone_recovery", global_operators=control + power,
        ).morale_cost_rates()
        # Power plants additionally receive the direct +0.10/h recovery.
        self.assertAlmostEqual(power_rates["Lancet-2"], 0.70)

    def test_other_facility_morale_special_comparison_uses_max(self) -> None:
        control = [
            self.assigned("玛恩纳", 2, "control_center"),
            self.assigned("红", 0, "control_center"),
            self.assigned("重岳", 2, "control_center"),
            self.assigned("维什戴尔", 2, "control_center"),
            self.assigned("魔王", 0, "control_center"),
        ]
        factory = [self.assigned("砾", 0, "factory")]
        rates = EfficiencyCalculator(
            "factory", factory, "pure_gold", global_operators=control + factory,
        ).morale_cost_rates()
        # CC base reduction is 0.25. Public-business extension is 0.10,
        # Chongyue is 0.05 here, and Babel with Demon King is 0.20. Only 0.20 wins.
        self.assertAlmostEqual(rates["砾"], 0.55)

    def test_alternate_and_lgd_control_groups_use_current_room_members(self) -> None:
        alternates = [
            self.assigned("濯尘芙蓉", 0, "control_center"),
            self.assigned("寒芒克洛丝", 0, "control_center"),
            self.assigned("承曦格雷伊", 0, "control_center"),
        ]
        alternate_rates = EfficiencyCalculator(
            "control_center", alternates, "base_management", global_operators=alternates,
        ).morale_cost_rates()
        # Two skill sources each read three alternate operators: 2 * 3 * 0.05.
        for name in ("濯尘芙蓉", "寒芒克洛丝", "承曦格雷伊"):
            self.assertAlmostEqual(alternate_rates[name], 0.55)

        lgd = [
            self.assigned("陈", 0, "control_center"),
            self.assigned("星熊", 0, "control_center"),
            self.assigned("诗怀雅", 0, "control_center"),
        ]
        lgd_rates = EfficiencyCalculator(
            "control_center", lgd, "base_management", global_operators=lgd,
        ).morale_cost_rates()
        for name in ("陈", "星熊", "诗怀雅"):
            self.assertAlmostEqual(lgd_rates[name], 0.70)

    def test_remaining_control_morale_and_glasgow_rules(self) -> None:
        lee_control = [
            self.assigned("吽", 2, "control_center"),
            self.assigned("老鲤", 0, "control_center"),
            self.assigned("砾", 0, "control_center"),
        ]
        lee_rates = EfficiencyCalculator(
            "control_center", lee_control, "base_management", global_operators=lee_control,
        ).morale_cost_rates()
        self.assertAlmostEqual(lee_rates["吽"], 0.35)
        self.assertAlmostEqual(lee_rates["老鲤"], 0.35)
        self.assertAlmostEqual(lee_rates["砾"], 0.75)

        pair = [
            self.assigned("魔王", 2, "control_center"),
            self.assigned("阿米娅", 0, "control_center"),
        ]
        pair_rates = EfficiencyCalculator(
            "control_center", pair, "base_management", global_operators=pair,
        ).morale_cost_rates()
        self.assertAlmostEqual(pair_rates["魔王"], 0.75)
        self.assertAlmostEqual(pair_rates["阿米娅"], 0.75)

        trading = [
            self.assigned("推进之王", 0, "trading_post"),
            self.assigned("因陀罗", 0, "trading_post"),
        ]
        for elite, expected in ((0, 0), (2, 20)):
            with self.subTest(dagda_elite=elite):
                control = [self.assigned("戴菲恩", elite, "control_center")]
                result = EfficiencyCalculator(
                    "trading_post", trading, "lmd_order", global_operators=trading + control,
                ).compute()
                self.assertEqual(result["layers"]["global_bonus_pct"], expected)

    def test_conditional_power_plant_skills_use_actual_assignments(self) -> None:
        friston = self.assigned("Friston-3", 0, "power_plant")
        without_kaltsit = EfficiencyCalculator(
            "power_plant", [friston], "drone_recovery", global_operators=[friston],
        ).compute()
        self.assertEqual(without_kaltsit["paper_bonus_pct"], 10)

        kaltsit = self.assigned("凯尔希", 0, "control_center")
        with_kaltsit = EfficiencyCalculator(
            "power_plant", [friston], "drone_recovery", global_operators=[friston, kaltsit],
        ).compute()
        self.assertEqual(with_kaltsit["paper_bonus_pct"], 15)

        gallus = self.assigned("GALLUS²", 0, "power_plant")
        alone = EfficiencyCalculator(
            "power_plant", [gallus], "drone_recovery", global_operators=[gallus],
        ).compute()
        platform = self.assigned("Lancet-2", 0, "power_plant")
        paired = EfficiencyCalculator(
            "power_plant", [gallus], "drone_recovery", global_operators=[gallus, platform],
        ).compute()
        self.assertEqual(alone["paper_bonus_pct"], 10)
        self.assertEqual(paired["paper_bonus_pct"], 15)

    def test_christine_morgan_and_firewhistle_conditions(self) -> None:
        christine = self.assigned("Miss.Christine", 2, "factory")
        jiushen = self.assigned("酒神", 2, "factory")
        record = EfficiencyCalculator(
            "factory", [christine, jiushen], "battle_record",
            global_operators=[christine, jiushen],
        ).compute()
        self.assertEqual(record["layers"]["direct_bonus_pct"], 35)
        self.assertEqual(record["layers"]["global_bonus_pct"], 30)
        gold = EfficiencyCalculator(
            "factory", [christine, jiushen], "pure_gold",
            global_operators=[christine, jiushen],
        ).compute()
        self.assertEqual(gold["layers"]["global_bonus_pct"], 0)

        morgan = self.assigned("摩根", 2, "trading_post")
        siege = self.assigned("推进之王", 0, "trading_post")
        compass = EfficiencyCalculator(
            "trading_post", [morgan, siege], "lmd_order",
            global_operators=[morgan, siege],
        ).compute()
        morgan_detail = next(item for item in compass["operator_details"] if item["name"] == "摩根")
        self.assertEqual(morgan_detail["global_bonus_pct"], 75)

        firewhistle = self.assigned("火哨", 2, "trading_post")
        workers = [firewhistle, self.assigned("砾", 0, "trading_post"), self.assigned("杜宾", 0, "trading_post")]
        negotiation = EfficiencyCalculator(
            "trading_post", workers, "lmd_order", global_operators=workers,
        ).compute()
        detail = next(item for item in negotiation["operator_details"] if item["name"] == "火哨")
        self.assertEqual(detail["direct_bonus_pct"], 30)

    def test_capacity_conversion_training_room_and_sui_facilities(self) -> None:
        redcloud = self.assigned("红云", 1, "factory")
        christine = self.assigned("Miss.Christine", 0, "factory")
        recycled = EfficiencyCalculator(
            "factory", [redcloud, christine], "battle_record",
            global_operators=[redcloud, christine],
        ).compute()
        self.assertEqual(recycled["layers"]["global_bonus_pct"], 36)

        bubble = self.assigned("泡泡", 1, "factory")
        priority = EfficiencyCalculator(
            "factory", [bubble, redcloud, christine], "battle_record",
            global_operators=[bubble, redcloud, christine],
        ).compute()
        # 10 + 8 + 10 capacity, all individual increases are at most 16.
        self.assertEqual(priority["layers"]["global_bonus_pct"], 28)

        wei = self.assigned("维伊", 2, "factory")
        for level, expected in ((1, 10), (2, 20), (3, 30)):
            with self.subTest(training_room_level=level):
                result = EfficiencyCalculator(
                    "factory", [wei], "battle_record", training_room_level=level,
                    global_operators=[wei],
                ).compute()
                self.assertEqual(result["layers"]["facility_bonus_pct"], expected)

        fengxu = dict(self.assigned("风絮", 2, "trading_post"), assigned_room_id="trade_1")
        sui = [
            dict(self.assigned("年", 0, "factory"), assigned_room_id="factory_1"),
            dict(self.assigned("夕", 0, "factory"), assigned_room_id="factory_1"),
            dict(self.assigned("令", 0, "control_center"), assigned_room_id="control_center"),
            dict(self.assigned("重岳", 0, "dormitory"), assigned_room_id="dormitory_1"),
        ]
        teachable = EfficiencyCalculator(
            "trading_post", [fengxu], "lmd_order", global_operators=[fengxu, *sui],
        ).compute()
        detail = next(item for item in teachable["operator_details"] if item["name"] == "风絮")
        self.assertEqual(detail["global_bonus_pct"], 12)

    def test_automation_alpha_clears_only_non_facility_count_teammate_bonus(self) -> None:
        windflit = self.assigned("掠风", 2, "factory")
        gravel = self.assigned("砾", 1, "factory")
        cleared = EfficiencyCalculator(
            "factory", [windflit, gravel], "pure_gold", power_plant_count=2,
            global_operators=[windflit, gravel],
        ).compute()
        self.assertEqual(cleared["paper_bonus_pct"], 10)
        gravel_detail = next(item for item in cleared["operator_details"] if item["name"] == "砾")
        self.assertEqual(gravel_detail["direct_bonus_pct"], 0)

        purestream = self.assigned("清流", 1, "factory")
        preserved = EfficiencyCalculator(
            "factory", [windflit, purestream], "pure_gold",
            trading_post_count=2, power_plant_count=2,
            global_operators=[windflit, purestream],
        ).compute()
        self.assertEqual(preserved["layers"]["facility_bonus_pct"], 50)
        self.assertIn("automation_reset_others", preserved["special_flags"])

    def test_relevant_unmodeled_report_deduplicates_and_flags_numeric_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "roster.tsv"
            roster.write_text(
                "干员名称\t是否已招募\t等级\t精英化等级\n"
                "测试干员\tTRUE\t1\t0\n会客甲\tTRUE\t1\t0\n会客乙\tTRUE\t1\t0\n办公室\tTRUE\t1\t0\n",
                encoding="utf-8",
            )
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
            roster.write_text(
                "干员名称\t是否已招募\t等级\t精英化等级\n"
                "测试干员\tTRUE\t1\t0\n会客甲\tTRUE\t1\t0\n会客乙\tTRUE\t1\t0\n办公室\tTRUE\t1\t0\n",
                encoding="utf-8",
            )
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
                "right_side_schedule": [
                    {"meeting": ["会客甲", "会客乙"], "hire": ["办公室"]},
                    {"meeting": ["会客甲", "会客乙"], "hire": ["办公室"]},
                    {"meeting": ["会客甲", "会客乙"], "hire": ["办公室"]}
                ],
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
