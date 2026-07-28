#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from data_loader import OwnedOperator
from schedule_generator import ScheduleGenerator, factory_product_allocation
from schedule_validator import validate_schedule


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        names = [
            "但书","龙舌兰","巫恋","鸿雪","图耶","绮良","月见夜","空爆","玫兰莎","慕斯",
            "清流","温蒂","森蚺","冬时","娜斯提","多萝西","斯卡蒂","幽灵鲨","乌尔比安","安哲拉",
            "砾","斑点","夜烟","温米","苍苔","引星棘刺","野鬃","灰毫","芬","泡普卡",
            "桃金娘","杜林","褐果","推进之王","摩根","贝洛内","伺夜","缪尔赛思",
            "正义骑士号","戴菲恩","八幡海铃","歌蕾蒂娅","可露希尔","雪雉","拜松",
        ]
        self.roster = [OwnedOperator(name, 2) for name in names]

    def test_mixed_factory_allocation(self):
        allocation = factory_product_allocation(
            {"factory_strategy":"balanced","factory_products":["pure_gold","battle_record"]},
            4,
        )
        self.assertEqual(set(allocation), {"pure_gold", "battle_record"})
        self.assertEqual(len(allocation), 4)

    def test_generated_mixed_goal_contains_both_products(self):
        schedule = ScheduleGenerator(
            self.roster, "243", "gold_record", 2, strict_rotation=False
        ).generate()
        for shift in schedule["shifts"].values():
            products = {
                room["product_id"]
                for room in shift["rooms"].values()
                if room["facility_id"] == "factory"
            }
            self.assertEqual(products, {"pure_gold", "battle_record"})
        self.assertFalse(schedule["validation"]["errors"])

    def test_duplicate_in_same_shift_is_error(self):
        schedule = {
            "layout":"243",
            "goal":"all_gold",
            "generator":{"cross_shift_reuse_policy":"allowed_with_warning"},
            "shifts":{
                "A班":{
                    "hours":8,
                    "rooms":{
                        "贸易站#1":{"facility_id":"trading_post","product_id":"lmd_order","operators":[{"name":"巫恋","elite":2}]},
                        "贸易站#2":{"facility_id":"trading_post","product_id":"lmd_order","operators":[{"name":"巫恋","elite":2}]},
                        "制造站#1":{"facility_id":"factory","product_id":"pure_gold","operators":[]},
                        "制造站#2":{"facility_id":"factory","product_id":"pure_gold","operators":[]},
                        "制造站#3":{"facility_id":"factory","product_id":"pure_gold","operators":[]},
                        "制造站#4":{"facility_id":"factory","product_id":"pure_gold","operators":[]},
                        "发电站#1":{"facility_id":"power_plant","product_id":"drone_recovery","operators":[]},
                        "发电站#2":{"facility_id":"power_plant","product_id":"drone_recovery","operators":[]},
                        "发电站#3":{"facility_id":"power_plant","product_id":"drone_recovery","operators":[]},
                        "控制中枢":{"facility_id":"control_center","product_id":"base_management","operators":[]},
                    }
                }
            }
        }
        report = validate_schedule(schedule)
        self.assertTrue(any("同一班次重复" in item for item in report["errors"]))


if __name__ == "__main__":
    unittest.main()
