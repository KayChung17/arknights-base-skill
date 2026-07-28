#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate candidate schedules, including v0.5 room timelines and guide baselines."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from data_loader import load_mechanics, operator_index, select_available_skills
from efficiency_calculator import EfficiencyCalculator
from timeline_utils import (
    continuous_work_hours,
    facility_capacity,
    get_segments,
    get_strategy_template,
    hours_between,
    room_configuration,
    total_work_hours,
)

MAIN_FACILITIES = {"trading_post", "factory", "power_plant", "control_center"}
CALCULATED_FACILITIES = MAIN_FACILITIES


def _normalize_occupants(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("operators 必须是列表")
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append({"name": item, "elite": 0, "morale": None})
        elif isinstance(item, dict):
            result.append({
                "name": str(item.get("name", "")).strip(),
                "elite": int(item.get("elite", 0) or 0),
                "level": int(item.get("level", 1) or 1),
                "morale": item.get("morale"),
                "roster_verified": item.get("roster_verified"),
            })
        else:
            raise ValueError(f"无法解析干员项: {item!r}")
    return [item for item in result if item["name"]]


def _external_skill_map(schedule: dict[str, Any]) -> set[tuple[str, str, str]]:
    verified: set[tuple[str, str, str]] = set()
    for item in schedule.get("external_skill_evidence", []):
        if not isinstance(item, dict) or item.get("verified") is not True:
            continue
        name = str(item.get("operator", ""))
        facility = str(item.get("facility_id", ""))
        products = item.get("product_ids") or [""]
        for product in products:
            verified.add((name, facility, str(product)))
            verified.add((name, facility, ""))
    return verified


def _has_verified_skill(
    index: dict[str, dict],
    external: set[tuple[str, str, str]],
    op: dict[str, Any],
    facility: str,
    product: str,
) -> tuple[bool, str]:
    record = index.get(op["name"])
    if record:
        selected = select_available_skills(record, facility, int(op.get("elite", 0)), product, int(op.get("level", 90) or 90))
        if selected:
            return True, "local"
    if (op["name"], facility, product) in external or (op["name"], facility, "") in external:
        return True, "external"
    return False, "missing"


def _check_economy_projection(schedule: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    projection = schedule.get("economy_projection")
    final = schedule.get("plan_status") == "final"
    if not isinstance(projection, dict):
        (errors if final else warnings).append("赚钱加搓玉方案缺少 economy_projection")
        return
    daily = projection.get("daily")
    costs = projection.get("costs")
    required_daily = {"lmd_orders", "pure_gold", "orundum", "battle_record_exp"}
    required_costs = {"orirock_cube", "lmd"}
    if not isinstance(daily, dict) or not required_daily.issubset(daily):
        (errors if final else warnings).append(
            f"economy_projection.daily 需要包含 {sorted(required_daily)}"
        )
    if not isinstance(costs, dict) or not required_costs.issubset(costs):
        (errors if final else warnings).append(
            f"economy_projection.costs 需要包含 {sorted(required_costs)}"
        )
    if projection.get("warehouse_overflow_checked") is not True:
        (errors if final else warnings).append("尚未确认仓库容量与爆仓时间")
    if not projection.get("drone_policy"):
        warnings.append("economy_projection 缺少无人机分配策略")


def _check_baseline(schedule: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    final = schedule.get("plan_status") == "final"
    goal = schedule.get("goal")
    baseline = schedule.get("baseline")
    if goal != "gold_origin":
        return
    if not isinstance(baseline, dict) or not baseline.get("reference_id"):
        (errors if final else warnings).append("赚钱加搓玉方案尚未声明攻略基线")
        return
    try:
        get_strategy_template(str(baseline["reference_id"]))
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not isinstance(baseline.get("comparison"), dict):
        (errors if final else warnings).append("尚未执行 compare_to_baseline.py")
    if baseline.get("deviations") and not all(str(item).strip() for item in baseline["deviations"]):
        errors.append("baseline.deviations 包含空说明")


def validate_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    mechanics = load_mechanics()
    index = operator_index()
    errors: list[str] = []
    warnings: list[str] = []
    room_results: dict[str, dict] = {}
    final = schedule.get("plan_status") == "final"
    external_skills = _external_skill_map(schedule)

    layout_id = str(schedule.get("layout", ""))
    layout = mechanics["layouts"].get(layout_id)
    if not layout:
        errors.append(f"未知布局: {layout_id}")

    segments = get_segments(schedule)
    if not segments:
        errors.append("排班没有 segments 或 shifts 数据")
        return {"errors": errors, "warnings": warnings, "room_results": room_results, "valid": False}

    if final and schedule.get("normalization", {}).get("facility_configuration_inferred"):
        errors.append("final 方案必须显式提供 facility_configuration 和设施等级")

    configuration = schedule.get("facility_configuration", {}).get("rooms", {})
    if not isinstance(configuration, dict) or not configuration:
        errors.append("排班缺少 facility_configuration.rooms")
        configuration = {}

    # Layout counts come from the explicit configuration, not from a blanket
    # assumption that every room is level 3.
    configured_counts = Counter(
        str(room.get("facility_id", ""))
        for room in configuration.values()
        if isinstance(room, dict) and room.get("facility_id") in MAIN_FACILITIES
    )
    if layout:
        for facility in MAIN_FACILITIES:
            expected = int(layout.get(facility, 0))
            actual = int(configured_counts.get(facility, 0))
            if expected != actual:
                errors.append(f"facility_configuration 中 {facility} 为 {actual}，布局 {layout_id} 要求 {expected}")

    nodes = schedule.get("operation_nodes", [])
    if schedule.get("schema_version", 3) >= 4:
        total_hours = 0.0
        starts = []
        for segment_name, segment in segments.items():
            try:
                calculated = hours_between(segment.get("start", ""), segment.get("end", ""))
            except (ValueError, TypeError) as exc:
                errors.append(f"[{segment_name}] {exc}")
                continue
            declared = float(segment.get("hours", calculated) or calculated)
            if abs(calculated - declared) > 0.01:
                errors.append(f"[{segment_name}] hours={declared:g} 与 {segment.get('start')}→{segment.get('end')} 的 {calculated:g} 小时不一致")
            total_hours += declared
            starts.append(segment.get("start"))
        if abs(total_hours - 24.0) > 0.01:
            errors.append(f"时间区间合计 {total_hours:g} 小时，循环日必须覆盖 24 小时")
        if final:
            if not isinstance(nodes, list) or len(nodes) != len(segments):
                errors.append("final 方案的 operation_nodes 数量必须与每日操作区间一致")
            else:
                node_times = {str(item.get("time", "")) for item in nodes if isinstance(item, dict)}
                if node_times != set(starts):
                    errors.append("operation_nodes 时间必须与 segments 的开始时间一致")

    policy = schedule.get("cross_shift_reuse_policy") or "allowed_with_warning"
    repeating_daily = bool(schedule.get("assumptions", {}).get("repeating_daily", False))
    usage_by_segment: dict[str, set[str]] = {}

    for segment_name, segment in segments.items():
        rooms = segment.get("rooms", {})
        if not isinstance(rooms, dict):
            errors.append(f"[{segment_name}] rooms 必须是对象")
            continue

        all_segment_ops: list[dict[str, Any]] = []
        for room_id, raw_room in rooms.items():
            room = raw_room if isinstance(raw_room, dict) else {"operators": raw_room}
            config = room_configuration(schedule, room_id, room)
            facility = str(config.get("facility_id") or room.get("facility_id") or "")
            product = str(config.get("product_id") or room.get("product_id") or "")
            occupants = _normalize_occupants(room.get("operators", []))
            all_segment_ops.extend(occupants)

            if room_id not in configuration:
                (errors if final else warnings).append(f"[{segment_name}] {room_id}: 未在 facility_configuration 中声明")
            if facility not in mechanics["facilities"]:
                errors.append(f"[{segment_name}] {room_id}: 无法识别设施 {facility}")
                continue
            capacity = facility_capacity(facility, config.get("level"), config.get("capacity"))
            if len(occupants) > capacity:
                errors.append(f"[{segment_name}] {room_id}: {len(occupants)} 人超过等级容量 {capacity}")
            if len(occupants) < capacity:
                warnings.append(f"[{segment_name}] {room_id}: 仅 {len(occupants)}/{capacity} 人")

            product_info = mechanics["products"].get(product)
            if not product_info:
                warnings.append(f"[{segment_name}] {room_id}: 未知产品 {product}")
            elif product_info["facility"] != facility:
                errors.append(f"[{segment_name}] {room_id}: 产品 {product} 不属于设施 {facility}")

            for op in occupants:
                if op.get("roster_verified") is False:
                    errors.append(f"[{segment_name}] {room_id}: {op['name']} 不在提供的已招募干员表中")
                verified, source = _has_verified_skill(index, external_skills, op, facility, product)
                if facility in MAIN_FACILITIES and not verified:
                    message = (
                        f"[{segment_name}] {room_id}: {op['name']}@E{op['elite']} 的 "
                        f"{facility}/{product} 技能数据未经验证"
                    )
                    (errors if final else warnings).append(message)
                elif source == "external":
                    warnings.append(f"[{segment_name}] {room_id}: {op['name']} 使用外部已验证技能证据")

                morale = op.get("morale")
                hours = float(segment.get("hours", 0) or 0)
                if morale is not None:
                    try:
                        if float(morale) - hours < 0:
                            errors.append(f"[{segment_name}] {room_id}: {op['name']} 心情 {morale} 不足以覆盖 {hours:g} 小时")
                    except (TypeError, ValueError):
                        warnings.append(f"[{segment_name}] {room_id}: {op['name']} 心情值无法解析")

        # Recalculate all production rooms after collecting the complete
        # segment scope, so global counters see every simultaneously staffed
        # room rather than only rooms visited earlier in iteration order.
        for room_id, raw_room in rooms.items():
            room = raw_room if isinstance(raw_room, dict) else {"operators": raw_room}
            config = room_configuration(schedule, room_id, room)
            facility = str(config.get("facility_id") or room.get("facility_id") or "")
            product = str(config.get("product_id") or room.get("product_id") or "")
            if facility not in CALCULATED_FACILITIES:
                continue
            occupants = _normalize_occupants(room.get("operators", []))
            calc = EfficiencyCalculator(
                facility,
                occupants,
                product,
                trading_post_count=int(layout.get("trading_post", 0)) if layout else 0,
                power_plant_count=int(layout.get("power_plant", 0)) if layout else 0,
                global_operators=all_segment_ops,
            )
            room_results[f"{segment_name}/{room_id}"] = calc.compute()

        # Every configured non-dorm room must appear in each interval. A room
        # can keep the same crew across intervals, which represents 12h or
        # longer work without forcing global equal-length shifts.
        required_room_ids = {
            room_id for room_id, info in configuration.items()
            if isinstance(info, dict) and info.get("facility_id") != "dormitory"
        }
        missing_rooms = sorted(required_room_ids - set(rooms))
        for room_id in missing_rooms:
            (errors if final else warnings).append(f"[{segment_name}] 缺少配置房间 {room_id}")

        names = [op["name"] for op in all_segment_ops]
        for name, count in Counter(names).items():
            if count > 1:
                errors.append(f"[{segment_name}] {name} 在同一班次重复进驻（同一时间区间）")
        usage_by_segment[segment_name] = set(names)

    operator_segments: dict[str, list[str]] = {}
    for segment_name, names in usage_by_segment.items():
        for name in names:
            operator_segments.setdefault(name, []).append(segment_name)
    for name, used in sorted(operator_segments.items()):
        if len(used) <= 1:
            continue
        message = f"{name} 跨时间区间使用: {'、'.join(used)}"
        if policy == "forbidden":
            errors.append(message)
        elif policy == "allowed_with_warning":
            warnings.append(message)

    continuous = continuous_work_hours(schedule)
    totals = total_work_hours(schedule)
    recovery = schedule.get("recovery_plan", {})
    recovery_events = recovery.get("events", []) if isinstance(recovery, dict) else []
    restored = {
        name
        for event in recovery_events
        if isinstance(event, dict) and event.get("verified") is True
        for name in event.get("targets", [])
    }
    for event in recovery_events:
        if isinstance(event, dict) and event.get("type") == "fiammetta_full_restore" and len(event.get("targets", [])) > 1:
            errors.append("单次菲亚梅塔恢复事件只能指定一名目标干员")
    for name, value in continuous.items():
        if value > 12:
            message = f"{name} 连续工作 {value:g} 小时，需要核验心情消耗与恢复"
            if final and name not in restored:
                errors.append(message)
            else:
                warnings.append(message)
        if totals.get(name, 0) > 12 and name not in restored:
            warnings.append(f"{name} 每日工作 {totals[name]:g} 小时，未记录已验证恢复事件")
    if final and repeating_daily and recovery.get("repeating_day_verified") is not True:
        errors.append("final 循环方案必须将 recovery_plan.repeating_day_verified 设为 true")

    goal_id = schedule.get("goal")
    if goal_id in {"gold_record", "gold_origin"}:
        room_products = [
            str(info.get("product_id", ""))
            for info in configuration.values()
            if isinstance(info, dict)
        ]
        if goal_id == "gold_origin":
            required = Counter({
                "lmd_order": 2,
                "orundum_order": 1,
                "pure_gold": 2,
                "orundum_shard": 1,
                "battle_record": 1,
            })
            actual = Counter(room_products)
            missing = {key: count - actual[key] for key, count in required.items() if actual[key] < count}
            if missing:
                (errors if final else warnings).append(f"342 攻略基线产品结构缺失: {missing}")
            _check_economy_projection(schedule, errors, warnings)
            _check_baseline(schedule, errors, warnings)
        else:
            if "pure_gold" not in room_products or "battle_record" not in room_products:
                errors.append("赚钱加经验书要求同时存在赤金与作战记录制造站")

    return {
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "room_results": room_results,
        "timeline": {
            "continuous_work_hours": continuous,
            "total_work_hours": totals,
            "verified_recovery_targets": sorted(restored),
        },
        "valid": not errors,
    }


def validate_schedule_file(path: str | Path) -> dict[str, Any]:
    schedule_path = Path(path)
    with schedule_path.open("r", encoding="utf-8") as handle:
        schedule = json.load(handle)
    return validate_schedule(schedule)


def format_validation_report(report: dict[str, Any]) -> str:
    lines = [
        "=" * 72,
        f"排班校验：{'通过' if not report.get('errors') else '未通过'}",
        f"错误 {len(report.get('errors', []))} 个，警告 {len(report.get('warnings', []))} 个",
    ]
    if report.get("errors"):
        lines.append("\n错误：")
        lines.extend(f"  - {item}" for item in report["errors"])
    if report.get("warnings"):
        lines.append("\n警告：")
        lines.extend(f"  - {item}" for item in report["warnings"])
    lines.append("=" * 72)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="验证明日方舟基建排班 JSON")
    parser.add_argument("schedule")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = validate_schedule_file(args.schedule)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_validation_report(report))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
