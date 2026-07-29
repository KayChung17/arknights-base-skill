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


class ParseTests(unittest.TestCase):
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


class OwnedSkillTableImportTests(unittest.TestCase):
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
