#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a deterministic decision packet for model-led schedule design."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from data_loader import (
    load_mechanics,
    load_operator_data,
    operator_index,
    read_roster,
    select_available_skills,
)
from schedule_generator import normalize_goal
from timeline_utils import build_operation_timeline, facility_capacity, get_strategy_template


def _parse_preferences(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    candidate = Path(value)
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("preferences 必须是 JSON 对象")
    return parsed


def _parse_times(value: str | None, online_count: int, mechanics: dict[str, Any]) -> list[str]:
    if value:
        values = [item.strip() for item in value.split(",") if item.strip()]
        if len(values) != online_count:
            raise ValueError(f"online-times 提供了 {len(values)} 个时间，但 online-count 为 {online_count}")
        return values
    template = mechanics.get("operation_node_templates", {}).get(str(online_count))
    if not template:
        raise ValueError(f"没有 {online_count} 次上线的默认时间模板，请提供 --online-times")
    return list(template["default_times"])


def _generic_facility_configuration(layout_id: str, goal: dict[str, Any], mechanics: dict[str, Any]) -> dict[str, Any]:
    layout = mechanics["layouts"][layout_id]
    rooms: dict[str, dict[str, Any]] = {}
    trading_products = list(goal.get("trading_products") or [goal.get("trading_product", "lmd_order")])
    factory_products = list(goal.get("factory_products") or ["pure_gold"])
    for index in range(int(layout.get("trading_post", 0))):
        product = trading_products[index % len(trading_products)]
        rooms[f"trading_post_{index + 1}"] = {
            "facility_id": "trading_post", "level": 3, "product_id": product
        }
    for index in range(int(layout.get("factory", 0))):
        product = factory_products[index % len(factory_products)]
        rooms[f"factory_{index + 1}"] = {
            "facility_id": "factory", "level": 3, "product_id": product
        }
    for index in range(int(layout.get("power_plant", 0))):
        rooms[f"power_plant_{index + 1}"] = {
            "facility_id": "power_plant", "level": 3, "product_id": "drone_recovery"
        }
    rooms["control_center"] = {
        "facility_id": "control_center", "level": 5, "product_id": "base_management"
    }
    return {"rooms": rooms, "dormitories": []}


def build_decision_packet(
    roster_path: str | Path,
    goal_value: str,
    layout_id: str | None,
    online_count: int,
    preferences: dict[str, Any] | None = None,
    online_times: list[str] | None = None,
) -> dict[str, Any]:
    mechanics = load_mechanics()
    data = load_operator_data()
    index = operator_index()
    roster = read_roster(roster_path)
    if not roster:
        raise ValueError("干员表中没有已招募干员")

    goal_id = normalize_goal(goal_value)
    goal = mechanics["goals"][goal_id]
    resolved_layout = layout_id or goal["recommended_layout"]
    if resolved_layout == "layout_search_required":
        raise ValueError("该目标需要先运行 search_layouts.py，或显式提供 --layout")
    if resolved_layout not in mechanics["layouts"]:
        raise ValueError(f"未知布局: {resolved_layout}")

    baseline_id = goal.get("recommended_template") if resolved_layout == goal.get("recommended_layout") else None
    baseline = get_strategy_template(baseline_id) if baseline_id else None
    facility_configuration = (
        baseline["facility_configuration"] if baseline else _generic_facility_configuration(resolved_layout, goal, mechanics)
    )

    times = online_times or list(
        mechanics.get("operation_node_templates", {}).get(str(online_count), {}).get("default_times", [])
    )
    if not times:
        raise ValueError(f"没有 {online_count} 次上线的默认时间模板")
    operation_nodes, empty_segments = build_operation_timeline(times)

    products = sorted({
        room.get("product_id", "")
        for room in facility_configuration.get("rooms", {}).values()
        if room.get("product_id")
    })
    capabilities: dict[str, list[dict[str, Any]]] = {}
    group_counts: Counter[str] = Counter()
    unknown: list[str] = []

    for op in roster:
        record = index.get(op.name)
        if not record:
            unknown.append(op.name)
            capabilities[op.name] = []
            continue
        group_counts.update(record.get("groups", []))
        entries = []
        for room in facility_configuration.get("rooms", {}).values():
            facility = room["facility_id"]
            product = room["product_id"]
            selected = select_available_skills(record, facility, op.elite, product, op.level)
            if selected:
                entries.append({
                    "facility_id": facility,
                    "product_id": product,
                    "skills": [item.get("skill_name", "") for item in selected],
                    "tags": sorted({tag for item in selected for tag in item.get("tags", [])}),
                    "source": "local_versioned_data",
                })
        capabilities[op.name] = entries

    required_slots_per_segment = sum(
        facility_capacity(
            room["facility_id"],
            int(room.get("level", 1)),
            room.get("capacity"),
        )
        for room in facility_configuration.get("rooms", {}).values()
    )

    return {
        "schema_version": 2,
        "packet_type": "model_decision_context",
        "data_version": data.get("data_version"),
        "objective": {
            "goal_id": goal_id,
            "goal_display_name": goal["display_name"],
            "layout": resolved_layout,
            "online_count": online_count,
            "online_times": times,
            "products": products,
            "preferences": preferences or {},
        },
        "baseline": {
            "reference_id": baseline_id,
            "template": baseline,
            "rule": "先以当前攻略基线构造候选，再根据 roster、设施和上线时间做局部替换。",
        } if baseline else None,
        "facility_configuration": facility_configuration,
        "operation_nodes": operation_nodes,
        "segment_template": empty_segments,
        "hard_rules": {
            "same_segment_duplicate_forbidden": True,
            "operator_must_exist_in_roster": True,
            "elite_level_from_roster": True,
            "product_must_match_facility": True,
            "facility_level_controls_capacity": True,
            "final_plan_requires_verified_skill_data": True,
            "final_plan_requires_room_specific_timeline": True,
            "final_plan_requires_repeating_day_morale_check": True,
            "final_gold_origin_requires_economy_projection": goal_id == "gold_origin",
            "unknown_local_operator_skill_requires_verified_external_evidence": True,
        },
        "model_decision_requirements": {
            "candidate_count": {"minimum": 2, "recommended": 3},
            "candidate_schema": "schemas/candidate-plan.schema.json",
            "must_start_from_current_guide_baseline": False,
            "must_include_current_guide_baseline_as_candidate": bool(baseline),
            "guide_template_is_search_boundary": False,
            "must_explain_every_baseline_deviation": bool(baseline),
            "hybrid_solver_preferred": True,
            "solver_workflow": [
                "build_combinations",
                "build_model",
                "solve_schedule",
                "simulate_schedule"
            ],
            "must_state_strategy": True,
            "must_state_tradeoffs": True,
            "must_validate_each_candidate": True,
            "must_evaluate_each_valid_candidate": True,
            "must_compare_to_baseline": bool(baseline),
            "script_ranking_is_advisory": True,
        },
        "roster": [item.to_dict() for item in roster],
        "roster_summary": {
            "owned_count": len(roster),
            "unknown_in_local_data": sorted(unknown),
            "local_data_coverage_ratio": round((len(roster) - len(unknown)) / len(roster), 6),
            "required_slots_per_segment": required_slots_per_segment,
            "group_counts": dict(sorted(group_counts.items())),
        },
        "capabilities": capabilities,
        "external_evidence": [],
        "instructions_for_model": [
            "把上线次数理解为可操作节点，不自动拆成等长班次。",
            "读取当前版本攻略基线并把它作为比较候选；攻略模板不能成为搜索边界。",
            "优先运行单房间组合枚举、全局MILP和复算链路，再由模型解释与修订候选。",
            "为每个房间单独决定连续工作区间；同一队可跨相邻区间持续工作。",
            "核心干员允许跨区间复用，前提是恢复事件和次日循环心情能够验证。",
            "本地未收录的生产干员必须附带已验证的外部技能证据，候选才可升级为 final。",
            "赚钱加搓玉方案必须给出赤金、订单、源石碎片、经验、合成玉、龙门币与材料消耗的经济投影。",
            "每个候选先校验与评价，再与攻略基线比较；所有偏离基线的部分都要说明原因和代价。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成供大模型决策的基建排班上下文")
    parser.add_argument("--roster", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--layout")
    parser.add_argument("--online-count", type=int, choices=[1, 2, 3, 4], default=3)
    parser.add_argument("--online-times", help="逗号分隔的上线时间，例如 08:00,14:00,20:00")
    parser.add_argument("--shifts", type=int, choices=[2, 3], help="兼容旧参数；等价于 --online-count")
    parser.add_argument("--preferences", help="JSON 文件路径或 JSON 字符串")
    parser.add_argument("--output")
    args = parser.parse_args()

    online_count = args.shifts or args.online_count
    times = _parse_times(args.online_times, online_count, load_mechanics())
    packet = build_decision_packet(
        args.roster,
        args.goal,
        args.layout,
        online_count,
        _parse_preferences(args.preferences),
        times,
    )
    text = json.dumps(packet, ensure_ascii=False, indent=2)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
