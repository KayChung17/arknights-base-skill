#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arknights base efficiency estimator.

The tool keeps percentage efficiency and fixed order value in separate units.
It uses the versioned JSON dataset in ../assets and respects each operator's
actual elite level.

Examples:
  python scripts/efficiency_calculator.py 贸易站 "龙舌兰@E2,巫恋@E2,但书@E2"
  python scripts/efficiency_calculator.py 制造站 "清流@E1,温蒂@E1,冬时@E1" 赤金
  python scripts/efficiency_calculator.py --roster samples/sample_干员练度表.txt \
      贸易站 "龙舌兰,巫恋,但书"
  python scripts/efficiency_calculator.py --check samples/sample_243方案.json
  python scripts/efficiency_calculator.py --list-skills
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from data_loader import (
    OwnedOperator,
    load_mechanics,
    load_operator_data,
    normalize_elite,
    operator_index,
    parse_operator_list,
    read_roster,
    select_available_skills,
)

FACILITY_ALIASES = {
    "贸易站": "trading_post",
    "trading_post": "trading_post",
    "trading": "trading_post",
    "制造站": "factory",
    "factory": "factory",
    "manufacturing": "factory",
    "发电站": "power_plant",
    "power_plant": "power_plant",
    "控制中枢": "control_center",
    "control_center": "control_center",
}

PRODUCT_ALIASES = {
    "龙门币": "lmd_order",
    "龙门币订单": "lmd_order",
    "lmd": "lmd_order",
    "lmd_order": "lmd_order",
    "合成玉": "orundum_order",
    "合成玉订单": "orundum_order",
    "orundum_order": "orundum_order",
    "赤金": "pure_gold",
    "贵金属": "pure_gold",
    "pure_gold": "pure_gold",
    "作战记录": "battle_record",
    "经验书": "battle_record",
    "battle_record": "battle_record",
    "源石碎片": "orundum_shard",
    "orundum_shard": "orundum_shard",
    "无人机恢复": "drone_recovery",
    "drone_recovery": "drone_recovery",
    "基建管理": "base_management",
    "base_management": "base_management",
}


def normalize_facility(value: str) -> str:
    key = value.strip()
    if key not in FACILITY_ALIASES:
        raise ValueError(f"不支持的设施: {value}")
    return FACILITY_ALIASES[key]


def normalize_product(value: str) -> str:
    if not value:
        return ""
    key = value.strip()
    return PRODUCT_ALIASES.get(key, key)


def _operator_dict(op: OwnedOperator | dict | str) -> dict:
    if isinstance(op, OwnedOperator):
        return asdict(op)
    if isinstance(op, str):
        return asdict(parse_operator_list(op)[0])
    return {
        "name": str(op.get("name", "")).strip(),
        "elite": normalize_elite(op.get("elite", 0)),
        "level": max(1, int(float(op.get("level", 1) or 1))),
        "recruited": bool(op.get("recruited", True)),
        "morale": op.get("morale"),
    }


class EfficiencyCalculator:
    """Transparent, rule-based efficiency estimator."""

    def __init__(
        self,
        facility: str,
        operators: list[OwnedOperator | dict | str],
        product: str = "",
        *,
        trading_post_count: int = 2,
        power_plant_count: int = 3,
        global_operators: list[OwnedOperator | dict | str] | None = None,
    ):
        self.facility = normalize_facility(facility)
        self.product = normalize_product(product)
        self.operators = [_operator_dict(op) for op in operators]
        self.global_operators = [
            _operator_dict(op) for op in (global_operators if global_operators is not None else operators)
        ]
        self.trading_post_count = int(trading_post_count)
        self.power_plant_count = int(power_plant_count)
        self.index = operator_index()
        self.mechanics = load_mechanics()

    def _record(self, name: str) -> dict | None:
        return self.index.get(name)

    def _skills(self, operator: dict, facility: str | None = None, product: str | None = None) -> list[dict]:
        record = self._record(operator["name"])
        if not record:
            return []
        return select_available_skills(
            record,
            facility or self.facility,
            normalize_elite(operator.get("elite", 0)),
            self.product if product is None else product,
            int(operator.get("level", 90) or 90),
        )

    def _groups(self, operator: dict) -> set[str]:
        record = self._record(operator["name"])
        return set(record.get("groups", [])) if record else set()

    def count_global_group(self, group: str) -> int:
        return sum(1 for op in self.global_operators if group in self._groups(op))

    def count_room_group(self, group: str) -> int:
        return sum(1 for op in self.operators if group in self._groups(op))

    def _global_control_tags(self) -> set[str]:
        tags: set[str] = set()
        for op in self.global_operators:
            for skill in self._skills(op, "control_center", ""):
                tags.update(skill.get("tags", []))
        return tags

    def _global_control_heat(self) -> float:
        heat = 0.0
        for op in self.global_operators:
            for skill in self._skills(op, "control_center", ""):
                tags = set(skill.get("tags", []))
                if "ave_dorm_heat_1" in tags:
                    # Four level-1 dormitories hold 20 operators in the 342
                    # guide baseline. The value is a transparent proxy until
                    # dorm occupancy becomes a model variable.
                    heat += 20.0
                if "ave_heat_10" in tags:
                    heat += 10.0
                if "ave_heat_20" in tags:
                    heat += 20.0
        return heat

    def compute(self) -> dict[str, Any]:
        if self.facility == "trading_post":
            return self._compute_trading_post()
        if self.facility == "factory":
            return self._compute_factory()
        if self.facility == "power_plant":
            return self._compute_power_plant()
        if self.facility == "control_center":
            return self._compute_control_center()
        return {"error": f"不支持的设施: {self.facility}"}

    def _base_result(self) -> dict[str, Any]:
        unknown = [op["name"] for op in self.operators if op["name"] not in self.index]
        return {
            "facility_id": self.facility,
            "product_id": self.product,
            "operators": self.operators,
            "unknown_operators": unknown,
            "data_version": load_operator_data().get("data_version"),
            "model": "rule_based_estimate",
            "warnings": [],
            "operator_details": [],
        }

    def _compute_trading_post(self) -> dict[str, Any]:
        result = self._base_result()
        direct_bonus = 0.0
        facility_bonus = 0.0
        global_bonus = 0.0
        multiplier = 1.0
        fixed_order_lmd = 0
        override_values: list[float] = []
        special_flags: list[str] = []

        durin_count = min(self.count_global_group("durin"), 4)
        production_lines = durin_count
        if any(
            "qiliang_virtual_lines" in skill.get("tags", [])
            for op in self.operators
            for skill in self._skills(op)
        ):
            production_lines += (production_lines // 2) * 2

        control_tags = self._global_control_tags()
        room_glasgow = self.count_room_group("glasgow")
        room_siracusa = self.count_room_group("siracusa")
        room_laterano = self.count_room_group("laterano")
        room_names = {op["name"] for op in self.operators}
        room_order_capacity = 0
        for room_op in self.operators:
            for room_skill in self._skills(room_op):
                for room_tag in room_skill.get("tags", []):
                    if isinstance(room_tag, str) and room_tag.startswith("order_capacity_"):
                        try:
                            room_order_capacity += int(float(room_tag.rsplit("_", 1)[1]))
                        except ValueError:
                            pass
        snowant_caps: list[float] = []

        for op in self.operators:
            detail = {"name": op["name"], "elite": op["elite"], "notes": []}
            op_direct = 0.0
            op_facility = 0.0
            op_global = 0.0
            skills = self._skills(op)
            if not skills:
                detail["notes"].append("当前精英等级未解锁适配技能，或技能数据缺失")
            for skill in skills:
                tags = set(skill.get("tags", []))
                bonus = float(skill.get("base_bonus_pct", 0))
                if "override_room_direct_bonus" in tags:
                    override_values.append(bonus)
                    detail["notes"].append(f"直接订单效率替换为 +{bonus:.0f}%")
                    continue
                if "multiplier_1_556" in tags:
                    multiplier *= 1.556
                    detail["notes"].append("估算乘算 ×1.556")
                    continue
                if "independent_order_lmd_500" in tags:
                    fixed_order_lmd += 500
                    op_direct += bonus
                    detail["notes"].append(f"直接效率 +{bonus:.0f}%；独立订单价值 +500 龙门币/触发")
                    continue
                if "hongxue_line_source" in tags:
                    detail["notes"].append(f"赤金生产线来源：{durin_count} 条")
                    continue
                if "hongxue_per_line" in tags:
                    value = production_lines * bonus
                    op_global += value
                    detail["notes"].append(f"{production_lines} 条生产线 × {bonus:.0f}% = +{value:.0f}%")
                    continue
                if "tuye_per_two_lines" in tags:
                    value = (production_lines // 2) * 15
                    op_direct += bonus
                    op_global += value
                    detail["notes"].append(f"基础 +{bonus:.0f}%；生产线附加 +{value:.0f}%")
                    continue
                if "qiliang_virtual_lines" in tags:
                    op_direct += bonus
                    detail["notes"].append(f"基础 +{bonus:.0f}%；虚拟生产线已计入")
                    continue
                if "glasgow_per_member" in tags:
                    value = room_glasgow * 20
                    op_global += value
                    detail["notes"].append(f"同站格拉斯哥成员 {room_glasgow} 人：+{value:.0f}%")
                    continue
                if "laterano_per_member_15" in tags:
                    value = room_laterano * 15
                    op_global += value
                    detail["notes"].append(f"同站拉特兰成员 {room_laterano} 人：+{value:.0f}%")
                    continue
                if "lemuen_with_exusiai_25" in tags:
                    op_direct += bonus
                    extra = 25 if any("能天使" in name for name in room_names if name != op["name"]) else 0
                    op_global += extra
                    detail["notes"].append(f"基础 +{bonus:.0f}%；能天使同站附加 +{extra:.0f}%")
                    continue
                if "jaye_order_gap_4" in tags:
                    value = (10 + room_order_capacity) * 4
                    op_global += value
                    detail["notes"].append(f"按空订单代理：基础上限10+附加{room_order_capacity}，估算 +{value:.0f}%")
                    result["warnings"].append("孑E0效率按空订单代理值估算，实际随订单堆积下降。")
                    continue
                if "jaye_order_count_4" in tags:
                    value = 12
                    op_global += value
                    detail["notes"].append("孑E1动态订单技能按保守 +12% 代理")
                    result["warnings"].append("孑E1技能需要逐订单仿真，当前仅使用保守代理。")
                    continue
                if "snowant_amplifier_cap_25" in tags:
                    snowant_caps.append(25.0)
                    detail["notes"].append("雪雉放大上限 +25%")
                    continue
                if "snowant_amplifier_cap_35" in tags:
                    snowant_caps.append(35.0)
                    detail["notes"].append("雪雉放大上限 +35%")
                    continue
                if "special_order" in tags:
                    special_flags.append("special_order")
                    detail["notes"].append("特别订单机制已记录，未折算为百分比")
                    continue
                op_direct += bonus
                if bonus:
                    detail["notes"].append(f"直接效率 +{bonus:.0f}%")

            direct_bonus += op_direct
            facility_bonus += op_facility
            global_bonus += op_global
            detail.update({
                "direct_bonus_pct": op_direct,
                "facility_bonus_pct": op_facility,
                "global_bonus_pct": op_global,
            })
            result["operator_details"].append(detail)

        if override_values:
            direct_bonus = max(override_values)
            result["warnings"].append("巫恋类替换技能仅替换直接订单效率层，设施与全局联动层继续保留。")

        if "glasgow_center" in control_tags:
            external_glasgow = max(0, self.count_global_group("glasgow") - room_glasgow)
            global_bonus += external_glasgow * 10
        if "siracusa_center" in control_tags:
            global_bonus += room_siracusa * 5
        if "all_trading_bonus_7" in control_tags:
            global_bonus += 7
        if "wang_layout_balance" in control_tags and self.trading_post_count + self.power_plant_count >= 4:
            global_bonus += 7
        if "ave_trade_per_8_heat_1" in control_tags:
            heat = self._global_control_heat()
            value = int(heat // 8)
            global_bonus += value
            result["warnings"].append(f"Ave Mujica热情值按满员宿舍代理为 {heat:.0f}，贸易站 +{value}%")

        additive_before_amplifier = direct_bonus + facility_bonus + global_bonus
        generic_amplifier_count = sum(
            1
            for op in self.operators
            for skill in self._skills(op)
            if "amplifier_equal_additive" in skill.get("tags", [])
            and not any(tag.startswith("snowant_amplifier_cap_") for tag in skill.get("tags", []))
        )
        amplifier_bonus = additive_before_amplifier * generic_amplifier_count
        for cap in snowant_caps:
            amplifier_bonus += min(cap, max(0.0, additive_before_amplifier))
        paper_bonus = additive_before_amplifier + amplifier_bonus
        effective_bonus = ((1.0 + paper_bonus / 100.0) * multiplier - 1.0) * 100.0

        result.update({
            "layers": {
                "direct_bonus_pct": round(direct_bonus, 3),
                "facility_bonus_pct": round(facility_bonus, 3),
                "global_bonus_pct": round(global_bonus, 3),
                "amplifier_bonus_pct": round(amplifier_bonus, 3),
                "multiplier": round(multiplier, 6),
            },
            "paper_bonus_pct": round(paper_bonus, 3),
            "estimated_efficiency_bonus_pct": round(effective_bonus, 3),
            "fixed_order_value_lmd_per_trigger": fixed_order_lmd,
            "special_flags": sorted(set(special_flags)),
            "production_lines": production_lines,
        })
        return result

    def _compute_factory(self) -> dict[str, Any]:
        result = self._base_result()
        direct_bonus = 0.0
        facility_bonus = 0.0
        global_bonus = 0.0
        dongshi_values: list[float] = []
        control_tags = self._global_control_tags()
        room_names = {op["name"] for op in self.operators}
        work_platform_count = self.power_plant_count
        capacity_by_operator: dict[str, float] = {}
        for room_op in self.operators:
            extra = 0.0
            for room_skill in self._skills(room_op):
                for room_tag in room_skill.get("tags", []):
                    if isinstance(room_tag, str) and room_tag.startswith("warehouse_capacity_"):
                        try:
                            extra += float(room_tag.rsplit("_", 1)[1])
                        except ValueError:
                            pass
            capacity_by_operator[room_op["name"]] = extra

        for op in self.operators:
            detail = {"name": op["name"], "elite": op["elite"], "notes": []}
            op_direct = 0.0
            op_facility = 0.0
            op_global = 0.0
            skills = self._skills(op)
            if not skills:
                detail["notes"].append("当前精英等级未解锁适配技能，或产品/技能数据不匹配")
            for skill in skills:
                tags = set(skill.get("tags", []))
                bonus = float(skill.get("base_bonus_pct", 0))
                if "dongshi_reset" in tags:
                    value = len(self.operators) * 10
                    dongshi_values.append(value)
                    detail["notes"].append(f"直接生产力替换为站内 {len(self.operators)} 人 ×10% = +{value}%")
                    continue
                if "qingliu_per_trading_post" in tags:
                    value = self.trading_post_count * 20
                    op_facility += value
                    detail["notes"].append(f"{self.trading_post_count} 个贸易站 ×20% = +{value}%")
                    continue
                if "wendy_per_power_plant" in tags:
                    value = self.power_plant_count * 15
                    op_facility += value
                    detail["notes"].append(f"{self.power_plant_count} 个发电站 ×15% = +{value}%")
                    continue
                if "eunectes_per_power_plant" in tags:
                    value = self.power_plant_count * 20
                    op_facility += value
                    detail["notes"].append(f"{self.power_plant_count} 个发电站 ×20% = +{value}%")
                    continue
                if "nasti_per_rhine" in tags:
                    value = min(self.count_global_group("rhine_lab"), 5) * 3
                    op_global += value
                    detail["notes"].append(f"莱茵生命全局计数：+{value}%")
                    continue
                if "dorothy_rhine_room" in tags:
                    value = self.count_room_group("rhine_lab") * 10
                    op_global += value
                    detail["notes"].append(f"同站莱茵生命 {self.count_room_group('rhine_lab')} 人：+{value}%")
                    continue
                if "cangtai_per_other_metalcraft" in tags:
                    value = max(0, self.count_room_group("metalcraft") - 1) * 5
                    op_global += value
                    detail["notes"].append(f"其他金属工艺成员：+{value}%")
                    continue
                if "yinji_per_trading_post" in tags:
                    value = self.trading_post_count * 3
                    op_global += value
                    detail["notes"].append(f"{self.trading_post_count} 个贸易站 ×3% = +{value}%")
                    continue
                if "fen_per_a1" in tags:
                    value = self.count_room_group("a1") * 10
                    op_global += value
                    detail["notes"].append(f"同站 A1 成员 {self.count_room_group('a1')} 人：+{value}%")
                    continue
                if "work_platform_per_member_5" in tags:
                    value = work_platform_count * 5
                    op_facility += value
                    detail["notes"].append(f"按 {work_platform_count} 座作业平台发电站代理：+{value}%")
                    continue
                if "work_platform_per_member_10" in tags:
                    value = work_platform_count * 10
                    op_facility += value
                    detail["notes"].append(f"按 {work_platform_count} 座作业平台发电站代理：+{value}%")
                    continue
                if "with_wanqing_gold_15" in tags:
                    value = 15 if "温米" in room_names else 0
                    op_global += value
                    detail["notes"].append(f"温米同站附加：+{value}%")
                    continue
                if "bubble_capacity_conversion" in tags:
                    value = 0.0
                    for capacity in capacity_by_operator.values():
                        value += capacity * (3 if capacity > 16 else 1)
                    op_global += value
                    detail["notes"].append(f"仓库容量转生产力：+{value:.0f}%")
                    continue
                if "abyssal_factory" in tags and "gladiia_abyssal_activation" in control_tags:
                    op_global += 5
                    detail["notes"].append("控制中枢深海猎人联动估算：+5%")
                op_direct += bonus
                if bonus:
                    detail["notes"].append(f"直接生产力 +{bonus:.0f}%")

            direct_bonus += op_direct
            facility_bonus += op_facility
            global_bonus += op_global
            detail.update({
                "direct_bonus_pct": op_direct,
                "facility_bonus_pct": op_facility,
                "global_bonus_pct": op_global,
            })
            result["operator_details"].append(detail)

        if dongshi_values:
            direct_bonus = max(dongshi_values)
            result["warnings"].append("冬时类技能仅替换直接生产力层，设施与全局联动层继续保留。")

        if "all_factory_bonus_2" in control_tags:
            global_bonus += 2
        if self.product == "pure_gold" and "ave_gold_base_1_per_20_heat_1" in control_tags:
            heat = self._global_control_heat()
            value = 1 + int(heat // 20)
            global_bonus += value
            result["warnings"].append(f"Ave Mujica热情值按满员宿舍代理为 {heat:.0f}，赤金 +{value}%")

        paper_bonus = direct_bonus + facility_bonus + global_bonus
        result.update({
            "layers": {
                "direct_bonus_pct": round(direct_bonus, 3),
                "facility_bonus_pct": round(facility_bonus, 3),
                "global_bonus_pct": round(global_bonus, 3),
                "multiplier": 1.0,
            },
            "paper_bonus_pct": round(paper_bonus, 3),
            "estimated_efficiency_bonus_pct": round(paper_bonus, 3),
            "fixed_order_value_lmd_per_trigger": 0,
            "special_flags": ["dongshi_reset"] if dongshi_values else [],
        })
        return result

    def _compute_power_plant(self) -> dict[str, Any]:
        result = self._base_result()
        drone_bonus = 0.0
        flags = []
        for op in self.operators:
            detail = {"name": op["name"], "elite": op["elite"], "notes": []}
            for skill in self._skills(op):
                tags = set(skill.get("tags", []))
                base = float(skill.get("base_bonus_pct", 0) or 0)
                if base:
                    drone_bonus += base
                    detail["notes"].append(f"无人机恢复 +{base:.0f}%")
                if "muelsyse_drone_per_rhine" in tags:
                    value = self.count_global_group("rhine_lab") * 3
                    drone_bonus += value
                    detail["notes"].append(f"莱茵生命全局计数：无人机恢复 +{value}%")
                if "red_pine_power" in tags:
                    flags.append("red_pine_power")
                    detail["notes"].append("红松骑士团能源联动已记录")
            result["operator_details"].append(detail)
        result.update({
            "layers":{"direct_bonus_pct":drone_bonus,"facility_bonus_pct":0,"global_bonus_pct":0,"multiplier":1.0},
            "paper_bonus_pct":round(drone_bonus,3),
            "estimated_efficiency_bonus_pct":round(drone_bonus,3),
            "fixed_order_value_lmd_per_trigger":0,
            "special_flags":sorted(set(flags)),
        })
        return result

    def _compute_control_center(self) -> dict[str, Any]:
        result = self._base_result()
        flags: list[str] = []
        for op in self.operators:
            detail = {"name": op["name"], "elite": op["elite"], "notes": []}
            for skill in self._skills(op):
                flags.extend(skill.get("tags", []))
                detail["notes"].append(skill.get("description", "机制已记录"))
            result["operator_details"].append(detail)
        flag_set = set(flags)
        proxy = 0.0
        if "all_trading_bonus_7" in flag_set or "wang_layout_balance" in flag_set:
            proxy += 7.0
        if "all_factory_bonus_2" in flag_set:
            proxy += 2.0
        heat = self._global_control_heat()
        if "ave_trade_per_8_heat_1" in flag_set:
            proxy += int(heat // 8)
        if "ave_gold_base_1_per_20_heat_1" in flag_set:
            proxy += 1 + int(heat // 20)
        result.update({
            "layers":{"direct_bonus_pct":proxy,"facility_bonus_pct":0,"global_bonus_pct":0,"multiplier":1.0},
            "paper_bonus_pct":proxy,
            "estimated_efficiency_bonus_pct":proxy,
            "fixed_order_value_lmd_per_trigger":0,
            "special_flags":sorted(flag_set),
        })
        return result


def format_result(result: dict[str, Any]) -> str:
    mechanics = load_mechanics()
    facility_name = mechanics["facilities"][result["facility_id"]]["display_name"]
    product_name = mechanics["products"].get(result.get("product_id"), {}).get("display_name", result.get("product_id") or "未指定")
    lines = [
        "=" * 64,
        f"{facility_name}效率估算",
        f"产品：{product_name}",
        f"数据版本：{result.get('data_version')}",
        "-" * 64,
    ]
    for detail in result.get("operator_details", []):
        lines.append(f"{detail['name']}@E{detail['elite']}")
        for note in detail.get("notes", []):
            lines.append(f"  {note}")
    layers = result.get("layers", {})
    lines.extend([
        "-" * 64,
        f"直接加成：+{layers.get('direct_bonus_pct', 0):.1f}%",
        f"设施加成：+{layers.get('facility_bonus_pct', 0):.1f}%",
        f"全局加成：+{layers.get('global_bonus_pct', 0):.1f}%",
    ])
    if layers.get("amplifier_bonus_pct"):
        lines.append(f"放大器加成：+{layers['amplifier_bonus_pct']:.1f}%")
    lines.append(f"乘算：×{layers.get('multiplier', 1):.3f}")
    lines.append(f"估算效率加成：+{result.get('estimated_efficiency_bonus_pct', 0):.1f}%")
    fixed = result.get("fixed_order_value_lmd_per_trigger", 0)
    if fixed:
        lines.append(f"独立订单价值：+{fixed} 龙门币/触发")
    if result.get("warnings"):
        lines.append("警告：")
        lines.extend(f"  - {item}" for item in result["warnings"])
    if result.get("unknown_operators"):
        lines.append("未知干员：" + "、".join(result["unknown_operators"]))
    lines.append("=" * 64)
    return "\n".join(lines)


def _resolve_requested_operators(tokens: str, roster_path: str | None) -> tuple[list[OwnedOperator], list[OwnedOperator]]:
    requested = parse_operator_list(tokens)
    if not roster_path:
        return requested, requested
    roster = read_roster(roster_path)
    roster_map = {item.name: item for item in roster}
    resolved = []
    for item in requested:
        resolved.append(roster_map.get(item.name, item))
    return resolved, roster


def list_skills() -> None:
    data = load_operator_data()
    print(f"技能数据库：{len(data['operators'])} 名干员，版本 {data.get('data_version')}")
    for operator in sorted(data["operators"], key=lambda item: item["name"]):
        for skill in operator.get("skills", []):
            products = ",".join(skill.get("products", [])) or "*"
            print(
                f"{operator['name']} | E{skill.get('elite', 0)} | "
                f"{skill.get('facility')} | {products} | {skill.get('skill_name')} | "
                f"{skill.get('description')}"
            )


def check_schedule(path: str) -> int:
    from schedule_validator import validate_schedule_file, format_validation_report

    report = validate_schedule_file(path)
    print(format_validation_report(report))
    return 1 if report["errors"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="明日方舟基建效率估算器")
    parser.add_argument("facility", nargs="?", help="贸易站/制造站/发电站/控制中枢")
    parser.add_argument("operators", nargs="?", help='干员列表，例如 "龙舌兰@E2,巫恋@E2,但书@E2"')
    parser.add_argument("product", nargs="?", default="", help="产品，例如 赤金、作战记录")
    parser.add_argument("--roster", help="从干员练度表读取实际精英等级")
    parser.add_argument("--layout", default="243", help="用于设施计数的布局，默认 243")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--list-skills", action="store_true")
    parser.add_argument("--check", metavar="SCHEDULE_JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list_skills:
        list_skills()
        return 0
    if args.check:
        return check_schedule(args.check)
    if not args.facility or not args.operators:
        build_parser().print_help()
        return 2

    mechanics = load_mechanics()
    if args.layout not in mechanics["layouts"]:
        raise SystemExit(f"未知布局: {args.layout}")
    layout = mechanics["layouts"][args.layout]
    operators, global_operators = _resolve_requested_operators(args.operators, args.roster)
    calc = EfficiencyCalculator(
        args.facility,
        operators,
        args.product,
        trading_post_count=layout["trading_post"],
        power_plant_count=layout["power_plant"],
        global_operators=global_operators,
    )
    result = calc.compute()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_result(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
