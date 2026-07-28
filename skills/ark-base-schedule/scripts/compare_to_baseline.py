#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare a candidate plan with a structured community-guide baseline."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from plan_utils import load_json_file, write_json
from timeline_utils import get_segments, get_strategy_template


def _room_signature(configuration: dict[str, Any]) -> Counter[tuple[str, int, str]]:
    result: Counter[tuple[str, int, str]] = Counter()
    for room in configuration.get("rooms", {}).values():
        if not isinstance(room, dict):
            continue
        facility = str(room.get("facility_id", ""))
        if facility == "dormitory":
            continue
        result[(facility, int(room.get("level", 1)), str(room.get("product_id", "")))] += 1
    return result


def _segment_hours(plan: dict[str, Any]) -> list[float]:
    return [float(segment.get("hours", 0) or 0) for segment in get_segments(plan).values()]


def compare_to_baseline(plan: dict[str, Any], baseline_id: str) -> dict[str, Any]:
    baseline = get_strategy_template(baseline_id)
    deviations: list[str] = []
    checks: dict[str, Any] = {}

    checks["layout"] = {
        "expected": baseline["layout"],
        "actual": plan.get("layout"),
        "matched": plan.get("layout") == baseline["layout"],
    }
    if not checks["layout"]["matched"]:
        deviations.append(f"布局由 {baseline['layout']} 改为 {plan.get('layout')}")

    expected_rooms = _room_signature(baseline["facility_configuration"])
    actual_rooms = _room_signature(plan.get("facility_configuration", {}))
    checks["facility_room_signatures"] = {
        "expected": {"|".join(map(str, key)): value for key, value in sorted(expected_rooms.items())},
        "actual": {"|".join(map(str, key)): value for key, value in sorted(actual_rooms.items())},
        "matched": expected_rooms == actual_rooms,
    }
    for signature in sorted(set(expected_rooms) | set(actual_rooms)):
        delta = actual_rooms[signature] - expected_rooms[signature]
        if delta:
            facility, level, product = signature
            deviations.append(f"{facility}/L{level}/{product} 房间数量相对基线变化 {delta:+d}")

    expected_count = int(baseline["operation_model"]["online_count"])
    actual_count = len(plan.get("operation_nodes", [])) or len(get_segments(plan))
    checks["online_count"] = {
        "expected": expected_count,
        "actual": actual_count,
        "matched": expected_count == actual_count,
    }
    if expected_count != actual_count:
        deviations.append(f"每日上线节点由 {expected_count} 次改为 {actual_count} 次")

    expected_hours = sorted(float(value) for value in baseline["operation_model"]["segment_hours"])
    actual_hours = sorted(_segment_hours(plan))
    checks["segment_hours"] = {
        "expected": expected_hours,
        "actual": actual_hours,
        "matched": expected_hours == actual_hours,
    }
    if expected_hours != actual_hours:
        deviations.append(f"操作区间由 {expected_hours} 改为 {actual_hours}")

    recovery = plan.get("recovery_plan", {})
    verified_targets = {
        target
        for event in recovery.get("events", []) if isinstance(event, dict) and event.get("verified") is True
        for target in event.get("targets", [])
    }
    priority_targets = set(baseline["recovery_requirements"]["fiammetta_priority_targets"])
    checks["priority_recovery_targets"] = {
        "expected": sorted(priority_targets),
        "verified": sorted(verified_targets & priority_targets),
        "matched": priority_targets.issubset(verified_targets),
    }
    if not priority_targets.issubset(verified_targets):
        deviations.append(f"基线恢复目标尚未全部验证: {sorted(priority_targets - verified_targets)}")

    expected_economy = baseline.get("economy_baseline", {})
    projection = plan.get("economy_projection", {})
    checks["economy_projection_present"] = {
        "expected": True,
        "actual": bool(projection),
        "matched": bool(projection),
    }
    economy_delta: dict[str, Any] = {}
    if projection:
        for key, expected in expected_economy.get("daily", {}).items():
            actual = projection.get("daily", {}).get(key)
            economy_delta[key] = {
                "baseline": expected,
                "actual": actual,
                "delta": None if actual is None else actual - expected,
            }
    else:
        deviations.append("缺少经济投影，无法与攻略日产基线比较")
    checks["economy_delta"] = economy_delta

    matched_checks = [
        value.get("matched")
        for value in checks.values()
        if isinstance(value, dict) and "matched" in value
    ]
    score = sum(bool(value) for value in matched_checks) / len(matched_checks) if matched_checks else 0.0

    return {
        "comparison_type": "guide_baseline_comparison",
        "baseline_id": baseline_id,
        "baseline_source": baseline.get("source"),
        "structural_match_ratio": round(score, 6),
        "checks": checks,
        "generated_deviations": deviations,
        "requires_model_explanation": bool(deviations),
        "rule": "结构差异由脚本列出，是否接受差异由模型结合用户 roster、当前攻略和用户偏好决定。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="将候选排班与结构化攻略基线比较")
    parser.add_argument("plan")
    parser.add_argument("--baseline")
    parser.add_argument("--output")
    parser.add_argument("--embed-plan", help="将比较结果写入 baseline.comparison 后保存到新文件")
    args = parser.parse_args()

    loaded = load_json_file(args.plan)
    plan = loaded.get("normalized_plan") if isinstance(loaded.get("normalized_plan"), dict) else loaded
    baseline_id = args.baseline or plan.get("baseline", {}).get("reference_id")
    if not baseline_id:
        raise ValueError("没有提供 baseline id")
    result = compare_to_baseline(plan, str(baseline_id))
    if args.output:
        write_json(args.output, result)
    if args.embed_plan:
        plan.setdefault("baseline", {})["reference_id"] = baseline_id
        plan["baseline"]["comparison"] = result
        existing = [str(item) for item in plan["baseline"].get("deviations", [])]
        plan["baseline"]["deviations"] = existing or result["generated_deviations"]
        write_json(args.embed_plan, plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
