#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate a model-authored candidate without making the final decision."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from data_loader import load_operator_data
from plan_utils import normalize_plan_file, write_json
from schedule_validator import validate_schedule
from timeline_utils import facility_capacity, get_segments, room_configuration


def _operator_names(room: dict[str, Any]) -> set[str]:
    return {
        str(item.get("name", ""))
        for item in room.get("operators", [])
        if isinstance(item, dict) and item.get("name")
    }


def _economy_completeness(plan: dict[str, Any]) -> float:
    projection = plan.get("economy_projection")
    if not isinstance(projection, dict):
        return 0.0
    checks = [
        isinstance(projection.get("daily"), dict),
        isinstance(projection.get("costs"), dict),
        isinstance(projection.get("inventory_delta", {}), dict),
        projection.get("warehouse_overflow_checked") is True,
        bool(projection.get("drone_policy")),
    ]
    return sum(checks) / len(checks)


def evaluate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    report = validate_schedule(plan)

    total_slots = 0
    occupied_slots = 0
    total_assignments = 0
    operator_segment_usage: dict[str, set[str]] = defaultdict(set)
    weighted_points = 0.0
    weighted_room_hours = 0.0
    fixed_lmd_weighted = 0.0
    facility_points: Counter[str] = Counter()
    unknown_operators: set[str] = set()
    morale_issue_count = 0

    segment_items = list(get_segments(plan).items())
    room_sets_by_segment: list[dict[str, set[str]]] = []

    for segment_name, segment in segment_items:
        hours = float(segment.get("hours", 0) or 0)
        rooms = segment.get("rooms", {})
        current_room_sets: dict[str, set[str]] = {}
        for room_name, raw_room in rooms.items():
            room = raw_room if isinstance(raw_room, dict) else {"operators": raw_room}
            config = room_configuration(plan, room_name, room)
            facility = str(config.get("facility_id", ""))
            capacity = facility_capacity(facility, config.get("level"), config.get("capacity"))
            occupants = room.get("operators", [])
            names = _operator_names(room)
            current_room_sets[room_name] = names
            total_slots += capacity
            occupied_slots += min(len(occupants), capacity)
            total_assignments += len(occupants)
            for item in occupants:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", ""))
                if not name:
                    continue
                operator_segment_usage[name].add(segment_name)
                if not item.get("roster_verified", True):
                    unknown_operators.add(name)

            result = report.get("room_results", {}).get(f"{segment_name}/{room_name}", {})
            bonus = float(result.get("estimated_efficiency_bonus_pct", 0) or 0)
            fixed_lmd = float(result.get("fixed_order_value_lmd_per_trigger", 0) or 0)
            weighted_points += bonus * hours / 24.0
            weighted_room_hours += hours / 24.0
            fixed_lmd_weighted += fixed_lmd * hours / 24.0
            facility_points[facility] += bonus * hours / 24.0
        room_sets_by_segment.append(current_room_sets)

    assignment_changes = 0
    compared_slots = 0
    if len(room_sets_by_segment) > 1:
        pairs = list(zip(room_sets_by_segment, room_sets_by_segment[1:]))
        if plan.get("assumptions", {}).get("repeating_daily"):
            pairs.append((room_sets_by_segment[-1], room_sets_by_segment[0]))
        for current, following in pairs:
            for room_name in sorted(set(current) | set(following)):
                before = current.get(room_name, set())
                after = following.get(room_name, set())
                assignment_changes += len(before.symmetric_difference(after))
                compared_slots += max(len(before), len(after), 1)

    cross_segment_reuse = {
        name: sorted(segments)
        for name, segments in operator_segment_usage.items()
        if len(segments) > 1
    }
    for item in [*report.get("errors", []), *report.get("warnings", [])]:
        if "心情" in item or "恢复" in item:
            morale_issue_count += 1
        if "技能数据未经验证" in item:
            try:
                unknown_operators.add(item.split(":", 1)[1].split("@", 1)[0].strip())
            except (IndexError, AttributeError):
                pass

    coverage_ratio = occupied_slots / total_slots if total_slots else 0.0
    stability_ratio = max(0.0, 1.0 - assignment_changes / compared_slots) if compared_slots else 1.0
    average_bonus = weighted_points / weighted_room_hours if weighted_room_hours else 0.0
    baseline_block = plan.get("baseline") if isinstance(plan.get("baseline"), dict) else {}
    baseline_match = float(
        baseline_block.get("comparison", {}).get("structural_match_ratio", 0.0) or 0.0
    )

    projection = plan.get("economy_projection", {})
    return {
        "schema_version": 2,
        "evaluation_type": "deterministic_candidate_metrics",
        "plan_id": plan.get("plan_id"),
        "title": plan.get("title"),
        "plan_status": plan.get("plan_status", "candidate"),
        "data_version": load_operator_data().get("data_version"),
        "valid": not report.get("errors"),
        "validation": report,
        "metrics": {
            "coverage_ratio": round(coverage_ratio, 6),
            "occupied_slots": occupied_slots,
            "total_slots": total_slots,
            "total_assignments": total_assignments,
            "unique_operators": len(operator_segment_usage),
            "cross_shift_reuse_operator_count": len(cross_segment_reuse),
            "cross_segment_reuse_operator_count": len(cross_segment_reuse),
            "cross_segment_reuse": cross_segment_reuse,
            "assignment_change_count": assignment_changes,
            "assignment_stability_ratio": round(stability_ratio, 6),
            "operation_node_count": len(plan.get("operation_nodes", [])) or len(segment_items),
            "segment_hours": [float(item.get("hours", 0) or 0) for _, item in segment_items],
            "weighted_efficiency_points": round(weighted_points, 3),
            "average_room_efficiency_bonus_pct": round(average_bonus, 3),
            "weighted_fixed_order_lmd_per_trigger": round(fixed_lmd_weighted, 3),
            "weighted_efficiency_points_by_facility": {
                key: round(value, 3) for key, value in sorted(facility_points.items())
            },
            "morale_issue_count": morale_issue_count,
            "maximum_continuous_work_hours": max(report.get("timeline", {}).get("continuous_work_hours", {}).values(), default=0),
            "guide_baseline_structural_match_ratio": round(baseline_match, 6),
            "economy_projection_completeness": round(_economy_completeness(plan), 6),
            "projected_daily": projection.get("daily", {}),
            "projected_costs": projection.get("costs", {}),
            "unknown_or_unverified_operators": sorted(unknown_operators),
            "validation_error_count": len(report.get("errors", [])),
            "validation_warning_count": len(report.get("warnings", [])),
        },
        "interpretation_limits": [
            "weighted_efficiency_points 是候选方案比较指标，不等于实际日产量。",
            "经济投影需要注明脚本、当前攻略或人工估算来源。",
            "攻略基线匹配度只表示结构接近程度，不表示收益自动相同。",
            "模型需要结合当前攻略、用户设施、库存与恢复时间线作出最终选择。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="计算大模型候选排班的确定性指标")
    parser.add_argument("plan")
    parser.add_argument("--roster", help="练度与心情的权威来源")
    parser.add_argument("--output")
    parser.add_argument("--include-plan", action="store_true")
    args = parser.parse_args()

    normalized = normalize_plan_file(args.plan, args.roster)
    evaluation = evaluate_plan(normalized)
    payload = {"plan": normalized, "evaluation": evaluation} if args.include_plan else evaluation
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if not evaluation["valid"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
