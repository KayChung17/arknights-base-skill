#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a deterministic fallback candidate plan.

The language model remains responsible for strategy and final selection. This
script supplies an auditable greedy baseline for comparison, repair, and tests.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from data_loader import (
    OwnedOperator,
    load_mechanics,
    load_operator_data,
    operator_index,
    read_roster,
    select_available_skills,
)
from efficiency_calculator import EfficiencyCalculator
from schedule_validator import format_validation_report, validate_schedule
from timeline_utils import get_strategy_template

GOAL_ALIASES = {
    "纯赚钱": "all_gold",
    "all_gold": "all_gold",
    "纯搓玉": "all_origin",
    "all_origin": "all_origin",
    "全力搓玉": "max_origin",
    "max_origin": "max_origin",
    "赚钱+经验书": "gold_record",
    "gold_record": "gold_record",
    "赚钱+搓玉": "gold_origin",
    "gold_origin": "gold_origin",
    "合成玉优先+龙门币平衡": "orundum_lmd_balance",
    "orundum_lmd_balance": "orundum_lmd_balance",
}


def normalize_goal(value: str) -> str:
    if value not in GOAL_ALIASES:
        raise ValueError(f"未知目标: {value}")
    return GOAL_ALIASES[value]


def factory_product_allocation(goal: dict, room_count: int) -> list[str]:
    products = list(goal["factory_products"])
    if goal.get("factory_strategy") == "all" or len(products) == 1:
        return [products[0]] * room_count
    # Balanced allocation keeps both products present and differs by at most one room.
    allocation = [products[i % len(products)] for i in range(room_count)]
    return allocation


class ScheduleGenerator:
    def __init__(
        self,
        roster: list[OwnedOperator],
        layout_id: str,
        goal_id: str,
        shifts_per_day: int,
        *,
        strict_rotation: bool = False,
    ):
        self.roster = roster
        self.layout_id = layout_id
        self.goal_id = goal_id
        self.shifts_per_day = shifts_per_day
        self.strict_rotation = strict_rotation
        self.mechanics = load_mechanics()
        self.operator_data = load_operator_data()
        self.index = operator_index()
        self.layout = self.mechanics["layouts"][layout_id]
        self.goal = self.mechanics["goals"][goal_id]
        self.shift_template = self.mechanics["shift_templates"][str(shifts_per_day)]
        self.use_counts: Counter[str] = Counter()
        self.rank_cache: dict[tuple[str, str, int], list[tuple[float, tuple[OwnedOperator, ...], dict]]] = {}
        self.warnings: list[str] = []
        self.room_capacities = {
            "trading_post": [int(self.mechanics["facilities"]["trading_post"]["capacity"])] * self.layout["trading_post"],
            "factory": [int(self.mechanics["facilities"]["factory"]["capacity"])] * self.layout["factory"],
        }
        template_id = self.goal.get("recommended_template") if self.layout_id == self.goal.get("recommended_layout") else None
        if template_id:
            template_rooms = get_strategy_template(template_id)["facility_configuration"]["rooms"]
            self.room_capacities["trading_post"] = [
                int(template_rooms[f"trading_post_{index + 1}"]["level"])
                for index in range(self.layout["trading_post"])
            ]
            self.room_capacities["factory"] = [
                int(template_rooms[f"factory_{index + 1}"]["level"])
                for index in range(self.layout["factory"])
            ]

    def _unlocked_skills(self, op: OwnedOperator, facility: str, product: str) -> list[dict]:
        record = self.index.get(op.name)
        if not record:
            return []
        return select_available_skills(record, facility, op.elite, product, op.level)

    def _candidate_pool(self, facility: str, product: str) -> list[OwnedOperator]:
        skilled = [op for op in self.roster if self._unlocked_skills(op, facility, product)]
        if facility in {"power_plant", "control_center"}:
            unskilled = [op for op in self.roster if op not in skilled]
            return skilled + unskilled
        return skilled

    def _score_combo(self, facility: str, product: str, combo: tuple[OwnedOperator, ...]) -> tuple[float, dict]:
        calc = EfficiencyCalculator(
            facility,
            list(combo),
            product,
            trading_post_count=self.layout["trading_post"],
            power_plant_count=self.layout["power_plant"],
            global_operators=self.roster,
        )
        result = calc.compute()
        score = float(result.get("estimated_efficiency_bonus_pct", 0))
        # Fixed order value remains a separate unit in output; this small ranking
        # coefficient only breaks ties for room assignment.
        score += float(result.get("fixed_order_value_lmd_per_trigger", 0)) / 100.0
        score += len(result.get("special_flags", [])) * 0.1
        return score, result

    def _rank_combinations(self, facility: str, product: str, capacity: int | None = None) -> list[tuple[float, tuple[OwnedOperator, ...], dict]]:
        resolved_capacity = int(capacity or self.mechanics["facilities"][facility]["capacity"])
        key = (facility, product, resolved_capacity)
        if key in self.rank_cache:
            return self.rank_cache[key]
        capacity = resolved_capacity
        pool = self._candidate_pool(facility, product)
        if facility in {"power_plant", "control_center"}:
            # Room-level combinations are unnecessary for these support rooms.
            scored = []
            for op in pool:
                score, result = self._score_combo(facility, product, (op,))
                scored.append((score, (op,), result))
            scored.sort(key=lambda item: (item[0], item[1][0].name), reverse=True)
            self.rank_cache[key] = scored
            return scored

        if not pool:
            self.rank_cache[key] = []
            return []

        combo_size = min(capacity, len(pool))
        scored = []
        for combo in itertools.combinations(pool, combo_size):
            score, result = self._score_combo(facility, product, combo)
            scored.append((score, combo, result))
        scored.sort(
            key=lambda item: (
                item[0],
                tuple(op.name for op in item[1]),
            ),
            reverse=True,
        )
        self.rank_cache[key] = scored
        return scored

    def _allowed(
        self,
        combo: tuple[OwnedOperator, ...],
        used_in_shift: set[str],
        used_all_day: set[str],
    ) -> bool:
        names = {op.name for op in combo}
        if names & used_in_shift:
            return False
        if self.strict_rotation and names & used_all_day:
            return False
        return True

    def _choose_production_room(
        self,
        facility: str,
        product: str,
        used_in_shift: set[str],
        used_all_day: set[str],
        capacity: int | None = None,
    ) -> tuple[list[OwnedOperator], dict]:
        ranked = self._rank_combinations(facility, product, capacity)
        best = None
        best_adjusted = float("-inf")
        for score, combo, result in ranked:
            if not self._allowed(combo, used_in_shift, used_all_day):
                continue
            reuse_penalty = sum(self.use_counts[op.name] * 12 for op in combo)
            adjusted = score - reuse_penalty
            if adjusted > best_adjusted:
                best = (list(combo), result)
                best_adjusted = adjusted
        if best:
            return best

        # In strict mode, keep a room partially staffed rather than silently
        # violating the daily rotation constraint.
        capacity = int(capacity or self.mechanics["facilities"][facility]["capacity"])
        pool = [
            op
            for op in self._candidate_pool(facility, product)
            if op.name not in used_in_shift and (not self.strict_rotation or op.name not in used_all_day)
        ]
        selected = sorted(pool, key=lambda op: (self.use_counts[op.name], op.name))[:capacity]
        if selected:
            result = self._score_combo(facility, product, tuple(selected))[1]
            self.warnings.append(f"{facility}/{product} 缺少完整高分组合，已使用 {len(selected)} 人部分编制。")
            return selected, result

        self.warnings.append(f"{facility}/{product} 没有可用干员，房间留空。")
        empty = EfficiencyCalculator(
            facility,
            [],
            product,
            trading_post_count=self.layout["trading_post"],
            power_plant_count=self.layout["power_plant"],
            global_operators=self.roster,
        ).compute()
        return [], empty

    def _choose_support_room(
        self,
        facility: str,
        product: str,
        used_in_shift: set[str],
        used_all_day: set[str],
    ) -> tuple[list[OwnedOperator], dict]:
        capacity = int(self.mechanics["facilities"][facility]["capacity"])
        ranked = self._rank_combinations(facility, product)
        selected: list[OwnedOperator] = []
        for score, combo, _ in ranked:
            op = combo[0]
            if op.name in used_in_shift:
                continue
            if self.strict_rotation and op.name in used_all_day:
                continue
            selected.append(op)
            if len(selected) >= capacity:
                break

        result = EfficiencyCalculator(
            facility,
            selected,
            product,
            trading_post_count=self.layout["trading_post"],
            power_plant_count=self.layout["power_plant"],
            global_operators=self.roster,
        ).compute()
        if len(selected) < capacity:
            self.warnings.append(
                f"{self.mechanics['facilities'][facility]['display_name']}只有 {len(selected)}/{capacity} 人。"
            )
        return selected, result

    @staticmethod
    def _room_payload(
        facility: str,
        product: str,
        operators: list[OwnedOperator],
        result: dict,
    ) -> dict:
        return {
            "facility_id": facility,
            "product_id": product,
            "operators": [
                {"name": op.name, "elite": op.elite, "morale": op.morale}
                for op in operators
            ],
            "estimated_efficiency_bonus_pct": result.get("estimated_efficiency_bonus_pct", 0),
            "fixed_order_value_lmd_per_trigger": result.get("fixed_order_value_lmd_per_trigger", 0),
            "special_flags": result.get("special_flags", []),
            "calculation_warnings": result.get("warnings", []),
        }

    def generate(self) -> dict[str, Any]:
        all_day_used: set[str] = set()
        shifts: dict[str, dict] = {}
        factory_products = factory_product_allocation(self.goal, self.layout["factory"])
        trading_products = list(self.goal.get("trading_products") or [self.goal["trading_product"]] * self.layout["trading_post"])

        for shift_template in self.shift_template:
            shift_key = f"{shift_template['name']} ({shift_template['start']}-{shift_template['end']})"
            used_in_shift: set[str] = set()
            rooms: dict[str, dict] = {}

            for index in range(self.layout["trading_post"]):
                trading_product = trading_products[index % len(trading_products)]
                ops, result = self._choose_production_room(
                    "trading_post",
                    trading_product,
                    used_in_shift,
                    all_day_used,
                    self.room_capacities["trading_post"][index],
                )
                rooms[f"贸易站#{index + 1}"] = self._room_payload(
                    "trading_post", trading_product, ops, result
                )
                for op in ops:
                    used_in_shift.add(op.name)
                    all_day_used.add(op.name)
                    self.use_counts[op.name] += 1

            for index, product in enumerate(factory_products):
                ops, result = self._choose_production_room(
                    "factory", product, used_in_shift, all_day_used,
                    self.room_capacities["factory"][index]
                )
                rooms[f"制造站#{index + 1}"] = self._room_payload("factory", product, ops, result)
                for op in ops:
                    used_in_shift.add(op.name)
                    all_day_used.add(op.name)
                    self.use_counts[op.name] += 1

            for index in range(self.layout["power_plant"]):
                ops, result = self._choose_support_room(
                    "power_plant", "drone_recovery", used_in_shift, all_day_used
                )
                rooms[f"发电站#{index + 1}"] = self._room_payload(
                    "power_plant", "drone_recovery", ops, result
                )
                for op in ops:
                    used_in_shift.add(op.name)
                    all_day_used.add(op.name)
                    self.use_counts[op.name] += 1

            ops, result = self._choose_support_room(
                "control_center", "base_management", used_in_shift, all_day_used
            )
            rooms["控制中枢"] = self._room_payload(
                "control_center", "base_management", ops, result
            )
            for op in ops:
                used_in_shift.add(op.name)
                all_day_used.add(op.name)
                self.use_counts[op.name] += 1

            shifts[shift_key] = {
                "name": shift_template["name"],
                "start": shift_template["start"],
                "end": shift_template["end"],
                "hours": shift_template["hours"],
                "rooms": rooms,
            }

        plan_id = f"fallback-{self.layout_id}-{self.goal_id}-{self.shifts_per_day}shift"
        facility_rooms = {}
        trading_levels = [3] * self.layout["trading_post"]
        factory_levels = [3] * self.layout["factory"]
        baseline_id = self.goal.get("recommended_template") if self.layout_id == self.goal.get("recommended_layout") else None
        if baseline_id:
            template = get_strategy_template(baseline_id)
            facility_rooms = template["facility_configuration"]["rooms"]
        else:
            for index, product in enumerate(trading_products):
                facility_rooms[f"贸易站#{index + 1}"] = {"facility_id":"trading_post","level":trading_levels[index],"product_id":product}
            for index, product in enumerate(factory_products):
                facility_rooms[f"制造站#{index + 1}"] = {"facility_id":"factory","level":factory_levels[index],"product_id":product}
            for index in range(self.layout["power_plant"]):
                facility_rooms[f"发电站#{index + 1}"] = {"facility_id":"power_plant","level":3,"product_id":"drone_recovery"}
            facility_rooms["控制中枢"] = {"facility_id":"control_center","level":5,"product_id":"base_management"}
        if baseline_id:
            # Map the guide template's stable IDs to the fallback generator's room names.
            remapped = {}
            for index in range(self.layout["trading_post"]):
                remapped[f"贸易站#{index + 1}"] = facility_rooms[f"trading_post_{index + 1}"]
            for index in range(self.layout["factory"]):
                remapped[f"制造站#{index + 1}"] = facility_rooms[f"factory_{index + 1}"]
            for index in range(self.layout["power_plant"]):
                remapped[f"发电站#{index + 1}"] = facility_rooms[f"power_plant_{index + 1}"]
            remapped["控制中枢"] = facility_rooms["control_center"]
            facility_rooms = remapped
        schedule = {
            "schema_version": 3,
            "plan_status": "candidate",
            "plan_id": plan_id,
            "title": f"{self.layout_id} {self.goal['display_name']} {self.shifts_per_day}班备用候选",
            "name": f"{self.layout_id} {self.goal['display_name']} {self.shifts_per_day}班备用候选",
            "layout": self.layout_id,
            "goal": self.goal_id,
            "goal_display_name": self.goal["display_name"],
            "shifts_per_day": self.shifts_per_day,
            "data_version": self.operator_data.get("data_version"),
            "cross_shift_reuse_policy": "forbidden" if self.strict_rotation else "allowed_with_warning",
            "decision": {
                "strategy": "确定性备用基线",
                "rationale": ["按单房间组合评分和复用惩罚生成可审计基准候选"],
                "tradeoffs": ["逐房间贪心分配没有同时搜索完整日排班空间"],
                "external_evidence_ids": [],
            },
            "generator": {
                "role": "fallback_candidate_source",
                "method": "greedy_room_combination_ranking",
                "global_optimality_proven": False,
                "final_decision_owner": "language_model",
                "strict_rotation": self.strict_rotation,
                "cross_shift_reuse_policy": "forbidden" if self.strict_rotation else "allowed_with_warning",
            },
            "assumptions": {
                "global_counter_scope": "owned recruited roster",
                "morale_simulation": "basic coverage checks only",
                "fixed_order_value_kept_separate": True,
                "schedule_scope": "single-day snapshot",
            },
            "facility_configuration": {"rooms": facility_rooms, "dormitories": []},
            "baseline": {"reference_id": baseline_id, "deviations": ["备用生成器仍使用等长班次和房间级贪心分配"]} if baseline_id else None,
            "factory_product_allocation": factory_products,
            "trading_product_allocation": trading_products,
            "shifts": shifts,
            "generation_warnings": sorted(set(self.warnings)),
            "operator_usage_count": dict(sorted(self.use_counts.items())),
        }
        schedule["validation"] = validate_schedule(schedule)
        return schedule


def format_schedule(schedule: dict[str, Any]) -> str:
    mechanics = load_mechanics()
    lines = [
        "=" * 80,
        schedule["name"],
        f"布局：{schedule['layout']}  目标：{schedule['goal_display_name']}",
        f"数据版本：{schedule['data_version']}",
        "方法：备用贪心候选，仅供模型比较和修订，未证明全局最优",
        "=" * 80,
    ]
    for shift_name, shift in schedule["shifts"].items():
        lines.append(f"\n{shift_name}")
        for room_name, room in shift["rooms"].items():
            product = mechanics["products"].get(room["product_id"], {}).get("display_name", room["product_id"])
            occupants = " + ".join(
                f"{op['name']}@E{op['elite']}" for op in room["operators"]
            ) or "空缺"
            line = (
                f"  {room_name:<8} {product:<8} {occupants} "
                f"｜估算 +{room['estimated_efficiency_bonus_pct']:.1f}%"
            )
            if room.get("fixed_order_value_lmd_per_trigger"):
                line += f"；独立 +{room['fixed_order_value_lmd_per_trigger']} 龙门币/触发"
            lines.append(line)

    if schedule.get("generation_warnings"):
        lines.append("\n生成警告：")
        lines.extend(f"  - {item}" for item in schedule["generation_warnings"])
    report = schedule.get("validation", {})
    lines.append(
        f"\n校验：{len(report.get('errors', []))} 个错误，"
        f"{len(report.get('warnings', []))} 个警告"
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="明日方舟基建备用候选生成器")
    parser.add_argument("--roster", required=True, help="干员练度表 TSV/CSV")
    parser.add_argument("--goal", required=True, help="纯赚钱/纯搓玉/全力搓玉/赚钱+经验书/赚钱+搓玉")
    parser.add_argument("--layout", help="243/252/153/333/342；省略时使用目标推荐布局")
    parser.add_argument("--shifts", type=int, choices=[2, 3], default=3)
    parser.add_argument("--strict-rotation", action="store_true", help="同一干员一天只分配一次；人员不足时房间允许空缺")
    parser.add_argument("--output", help="输出 JSON 文件")
    parser.add_argument("--json", action="store_true", help="仅在终端输出 JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    mechanics = load_mechanics()
    goal_id = normalize_goal(args.goal)
    goal = mechanics["goals"][goal_id]
    layout_id = args.layout or goal["recommended_layout"]
    if layout_id == "layout_search_required":
        raise ValueError("该目标需要先运行 search_layouts.py，或显式提供 --layout")
    if layout_id not in mechanics["layouts"]:
        raise ValueError(f"未知布局: {layout_id}")

    roster = read_roster(args.roster)
    if not roster:
        raise ValueError("干员表中没有已招募干员")

    generator = ScheduleGenerator(
        roster,
        layout_id,
        goal_id,
        args.shifts,
        strict_rotation=args.strict_rotation,
    )
    schedule = generator.generate()

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(schedule, ensure_ascii=False, indent=2))
    else:
        print(format_schedule(schedule))
        if args.output:
            print(f"\nJSON 已写入：{args.output}")
    return 1 if schedule["validation"]["errors"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
