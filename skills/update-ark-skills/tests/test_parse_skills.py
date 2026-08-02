#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from parse_skills import parse_delimited
from import_owned_skill_table import EXTRA_GROUPS, KNOWN_TAGS


class ParseTests(unittest.TestCase):
    def test_mantra_elite_operator_group_and_skill_tag_are_persistent(self):
        self.assertEqual(
            EXTRA_GROUPS["elite_operator"],
            {"电弧", "煌", "机械师", "逻各斯", "迷迭香", "真言", "烛煌"},
        )
        self.assertEqual(
            KNOWN_TAGS[("真言", "精英小队")],
            ["trade_per_elite_operator_facility_2_cap_20"],
        )

    def test_pipe_input(self):
        records = parse_delimited([
            "干员名|精等级|设施|技能名|技能描述",
            "但书|E2|贸易站|违约体验·β|描述",
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["facility"], "trading_post")
        self.assertEqual(records[0]["elite"], 2)

    def test_export_preserves_structured_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing = {
                "schema_version": 1,
                "data_version": "old",
                "operators": [{
                    "id": "op_1",
                    "name": "但书",
                    "groups": [],
                    "skills": [{
                        "facility": "trading_post",
                        "elite": 2,
                        "skill_name": "违约体验·β",
                        "description": "旧描述",
                        "base_bonus_pct": 0,
                        "model_status": "structured",
                        "mechanism": {
                            "type": "step_bonus",
                            "input": "drone_capacity",
                            "step": 10,
                            "bonus_pct_per_step": 1,
                            "cap_pct": 25,
                        },
                        "effects": [{
                            "effect_key": "global_trading_order_efficiency_pct",
                            "stacking": "max",
                            "value_pct": 7,
                        }],
                        "special_rules": [{
                            "rule_id": "test_rule",
                            "type": "amplifier_exclusion",
                            "excluded_amplifier_skill_names": ["测试技能"],
                        }],
                        "tags": ["proviso_breach_order"],
                        "products": ["lmd_order"],
                    }],
                }],
            }
            parsed = {
                "schema_version": 1,
                "records": [{
                    "name": "但书",
                    "elite": 2,
                    "facility": "trading_post",
                    "skill_name": "违约体验·β",
                    "description": "新描述",
                    "base_bonus_pct": 0,
                    "tags": [],
                    "products": [],
                }],
            }
            existing_path = tmp_path / "existing.json"
            parsed_path = tmp_path / "parsed.json"
            output_path = tmp_path / "output.json"
            existing_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            parsed_path.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
            subprocess.run([
                sys.executable,
                str(SCRIPTS / "export_operator_skills.py"),
                "--parsed", str(parsed_path),
                "--existing", str(existing_path),
                "--output", str(output_path),
                "--data-version", "new",
            ], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
            output = json.loads(output_path.read_text(encoding="utf-8"))
            skill = output["operators"][0]["skills"][0]
            self.assertEqual(skill["tags"], ["proviso_breach_order"])
            self.assertEqual(skill["products"], ["lmd_order"])
            self.assertEqual(skill["description"], "新描述")
            self.assertEqual(skill["model_status"], "structured")
            self.assertEqual(skill["mechanism"]["type"], "step_bonus")
            self.assertEqual(skill["effects"][0]["stacking"], "max")
            self.assertEqual(skill["special_rules"][0]["rule_id"], "test_rule")

    def test_export_explicit_zero_status_clears_stale_numeric_bonus(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing = {
                "schema_version": 1,
                "data_version": "old",
                "operators": [{
                    "id": "op_1",
                    "name": "成员",
                    "groups": [],
                    "skills": [{
                        "facility": "factory",
                        "elite": 0,
                        "skill_name": "旧占位技能",
                        "description": "生产力+20%",
                        "base_bonus_pct": 20,
                        "model_status": "structured",
                        "tags": [],
                        "products": ["pure_gold"],
                    }],
                }],
            }
            parsed = {
                "schema_version": 1,
                "records": [{
                    "name": "成员",
                    "elite": 0,
                    "facility": "factory",
                    "skill_name": "旧占位技能",
                    "description": "仅由其他技能授予",
                    "base_bonus_pct": 0,
                    "model_status": "description_only",
                    "tags": [],
                    "products": ["pure_gold"],
                }],
            }
            existing_path = tmp_path / "existing.json"
            parsed_path = tmp_path / "parsed.json"
            output_path = tmp_path / "output.json"
            existing_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            parsed_path.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
            subprocess.run([
                sys.executable,
                str(SCRIPTS / "export_operator_skills.py"),
                "--parsed", str(parsed_path),
                "--existing", str(existing_path),
                "--output", str(output_path),
                "--data-version", "new",
            ], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
            skill = json.loads(output_path.read_text(encoding="utf-8"))["operators"][0]["skills"][0]
            self.assertEqual(skill["base_bonus_pct"], 0)
            self.assertEqual(skill["model_status"], "description_only")


class OwnedSkillTableImportTests(unittest.TestCase):
    def test_full_delimited_table_format(self):
        from import_owned_skill_table import parse_table

        source = """机械师|精2|制造站|“我睡过了”|进驻制造站时，若单次工作时长达到12小时，生产力+10%
机械师|无|制造站|作战指导录像|进驻制造站时，作战记录类配方的生产力+30%
佩德洛|精2|训练室|实战技巧：游击手|进驻训练室协助位时，辅助干员的专精技能训练速度+30%
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skills_parsed.txt"
            path.write_text(source, encoding="utf-8")
            operators, warnings = parse_table(path)
        self.assertEqual(warnings, [])
        self.assertEqual(len(operators), 2)
        mechanic = next(item for item in operators if item["name"] == "机械师")
        self.assertEqual(len(mechanic["skills"]), 2)
        record_skill = next(item for item in mechanic["skills"] if item["skill_name"] == "作战指导录像")
        self.assertEqual(record_skill["base_bonus_pct"], 30)
        self.assertEqual(record_skill["products"], ["battle_record"])

    def test_full_table_drops_old_only_aliases_and_migrates_renamed_skill(self):
        from import_owned_skill_table import merge_existing, parse_table

        source = "缪尔赛思|精2|发电站|生态科主任|进驻发电站时，无人机充能速度+10%\n"
        existing = {"operators": [
            {
                "id": "old_muelsyse", "name": "缪尔赛思", "groups": ["rhine_lab"],
                "skills": [{
                    "facility": "power_plant", "elite": 0, "skill_name": "莱茵科技·能源",
                    "description": "旧名", "base_bonus_pct": 0,
                    "tags": ["muelsyse_drone_per_rhine"], "products": [], "model_status": "structured",
                }],
            },
            {"id": "alias", "name": "旧别名", "groups": [], "skills": []},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "skills_parsed.txt"
            existing_path = root / "existing.json"
            source_path.write_text(source, encoding="utf-8")
            existing_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            parsed, _ = parse_table(source_path)
            merged = merge_existing(parsed, existing_path, retain_existing_only=False)
        self.assertEqual([item["name"] for item in merged], ["缪尔赛思"])
        self.assertEqual(len(merged[0]["skills"]), 1)
        self.assertEqual(merged[0]["skills"][0]["skill_name"], "生态科主任")
        self.assertIn("muelsyse_drone_per_rhine", merged[0]["skills"][0]["tags"])

    def test_new_known_tag_is_not_downgraded_by_old_description_only_status(self):
        from import_owned_skill_table import merge_existing, parse_table

        source = "清流|精1|制造站|再生能源|进驻制造站时，每个贸易站为当前制造站贵金属类配方的生产力+20%\n"
        existing = {"operators": [{
            "name": "清流",
            "groups": [],
            "skills": [{
                "facility": "factory",
                "elite": 1,
                "skill_name": "再生能源",
                "description": "旧描述",
                "base_bonus_pct": 0,
                "model_status": "description_only",
                "tags": [],
                "products": ["pure_gold"],
            }],
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "skills_parsed.txt"
            existing_path = root / "existing.json"
            source_path.write_text(source, encoding="utf-8")
            existing_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            parsed, warnings = parse_table(source_path)
            merged = merge_existing(parsed, existing_path)

        self.assertEqual(warnings, [])
        skill = merged[0]["skills"][0]
        self.assertEqual(skill["model_status"], "structured")
        self.assertIn("qingliu_per_trading_post", skill["tags"])

    def test_existing_fixed_bonus_does_not_override_current_conditional_text(self):
        from import_owned_skill_table import merge_existing, parse_table

        source = "耶拉|精2|会客室|耶拉冈德|进驻会客室后，每当新搜集到的线索不是喀兰贸易时，则额外增加喀兰贸易线索的出现概率（工作时长影响概率）\n"
        existing = {"operators": [{
            "name": "耶拉", "groups": ["karlan_trade"],
            "skills": [{
                "facility": "reception_room", "elite": 2, "skill_name": "耶拉冈德",
                "description": "旧占位", "base_bonus_pct": 10,
                "model_status": "structured", "tags": ["time_dependent_probability"],
            }],
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "skills.txt"
            existing_path = root / "existing.json"
            source_path.write_text(source, encoding="utf-8")
            existing_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            parsed, warnings = parse_table(source_path)
            merged = merge_existing(parsed, existing_path, retain_existing_only=False)

        self.assertEqual(warnings, [])
        self.assertEqual(merged[0]["skills"][0]["base_bonus_pct"], 0)

    def test_roster_scoped_block_format(self):
        from import_owned_skill_table import parse_table

        source = """干员基建技能全表
你拥有 2 名干员
================================================================================

【Friston-3】星级1 Lv1 E0
  30级 | 发电站 | “愉快的对谈” | 进驻发电站时，如果凯尔希进驻在控制中枢，则无人机充能速度+5%
  无 | 发电站 | 备用能源 | 进驻发电站时，无人机充能速度+10%

【但书】星级5 Lv1 E2
  精2 | 贸易站 | 违约索赔·β | 进驻贸易站时，如果下笔赤金订单是违约订单，则赤金交付数额外+2
  无 | 贸易站 | 合同法 | 进驻贸易站时，如果下笔赤金订单交付数小于4，则视为违约订单
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "owned.txt"
            path.write_text(source, encoding="utf-8")
            operators, warnings = parse_table(path)
        self.assertEqual(warnings, [])
        self.assertEqual(len(operators), 2)
        friston = next(item for item in operators if item["name"] == "Friston-3")
        level_skill = next(item for item in friston["skills"] if item["skill_name"] == "“愉快的对谈”")
        self.assertEqual(level_skill["required_level"], 30)
        self.assertEqual(level_skill["tags"], ["power_with_kaltsit_control_5"])
        proviso = next(item for item in operators if item["name"] == "但书")
        beta = next(item for item in proviso["skills"] if item["skill_name"] == "违约索赔·β")
        self.assertIn("proviso_breach_order", beta["tags"])

    def test_trade_corrections_survive_existing_data_merge(self):
        from import_owned_skill_table import merge_existing, parse_table

        source = """干员基建技能全表
你拥有 2 名干员
================================================================================

【巫恋】星级5 Lv1 E2
  精2 | 贸易站 | 低语 | 进驻贸易站时，当前贸易站内其他干员提供的订单获取效率全部归零，且每人为自身+45%订单获取效率，同时全体心情每小时消耗+0.25

【龙舌兰】星级5 Lv1 E2
  精2 | 贸易站 | 投资·β | 进驻贸易站后，如果下笔赤金订单交付数大于3（违约订单不视作赤金订单），则其龙门币收益+500，心情每小时消耗-0.25
"""
        existing = {
            "operators": [
                {
                    "name": "巫恋",
                    "groups": [],
                    "skills": [{
                        "facility": "trading_post",
                        "elite": 2,
                        "skill_name": "低语",
                        "base_bonus_pct": 65,
                        "tags": ["override_room_direct_bonus", "morale_cost_plus_0.25"],
                    }],
                },
                {
                    "name": "龙舌兰",
                    "groups": [],
                    "skills": [{
                        "facility": "trading_post",
                        "elite": 2,
                        "skill_name": "投资·β",
                        "base_bonus_pct": 30,
                        "tags": ["independent_order_lmd_500", "morale_cost_minus_0.25"],
                    }],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "owned.txt"
            existing_path = root / "existing.json"
            source_path.write_text(source, encoding="utf-8")
            existing_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            parsed, warnings = parse_table(source_path)
            merged = merge_existing(parsed, existing_path)

        self.assertEqual(warnings, [])
        shamare = next(item for item in merged if item["name"] == "巫恋")["skills"][0]
        self.assertEqual(shamare["base_bonus_pct"], 0)
        self.assertEqual(
            shamare["tags"],
            ["room_morale_cost_plus_0.25", "shamare_whisper_per_other_worker_45"],
        )
        tequila = next(item for item in merged if item["name"] == "龙舌兰")["skills"][0]
        self.assertEqual(tequila["base_bonus_pct"], 0)
        self.assertNotIn("independent_order_lmd_500", tequila["tags"])
        self.assertIn("tequila_investment_order", tequila["tags"])


if __name__ == "__main__":
    unittest.main()
