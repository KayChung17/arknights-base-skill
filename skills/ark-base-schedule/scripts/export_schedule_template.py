#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export the selected solver plan to the user-facing template.json format."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from optimizer_common import read_json, write_json


ASSET_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "template.json"
FACILITY_KEYS = {
    "trading_post": "trading",
    "factory": "manufacture",
    "power_plant": "power",
    "control_center": "control",
}
PRODUCT_NAMES = {
    "lmd_order": "LMD",
    "orundum_order": "Orundum",
    "battle_record": "Battle Record",
    "pure_gold": "Pure Gold",
    "orundum_shard": "Originium Shard",
}
ROOM_DISPLAY_NAMES = {"trading": "贸易站", "manufacture": "制造站"}


def _stable_numeric_id(value: Any) -> int:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:13], 16)


def _room_index(room_id: str) -> int:
    try:
        return int(room_id.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 1


def _empty_room(*, product: str | None = None, skip: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {
        "skip": skip,
        "operators": [],
        "sort": False,
        "autofill": False,
    }
    if product is not None:
        value["product"] = product
    return value


def _selected_plan(value: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if "selected" in value:
        selected = value.get("selected") or {}
        plan = selected.get("plan") or {}
        return plan, selected
    if "candidate_plan" in value:
        return value.get("candidate_plan") or {}, value
    return value, {}


def export_schedule(value: dict[str, Any], template: dict[str, Any] | None = None) -> dict[str, Any]:
    plan, selected = _selected_plan(value)
    segments = plan.get("segments") or {}
    facility_rooms = ((plan.get("facility_configuration") or {}).get("rooms") or {})
    if not segments or not facility_rooms:
        raise ValueError("输入缺少 selected.plan.segments 或 facility_configuration.rooms")

    base = deepcopy(template or read_json(ASSET_TEMPLATE))
    required_room_keys = list((((base.get("plans") or [{}])[0].get("rooms") or {}).keys()))
    expected = {"trading", "manufacture", "power", "dormitory", "control", "meeting", "hire", "processing"}
    if set(required_room_keys) != expected:
        raise ValueError("template.json 的 rooms 键与排班导出契约不一致")

    ordered_rooms: dict[str, list[tuple[str, dict[str, Any]]]] = {key: [] for key in FACILITY_KEYS.values()}
    for room_id, room in facility_rooms.items():
        output_key = FACILITY_KEYS.get(str(room.get("facility_id") or ""))
        if output_key:
            ordered_rooms[output_key].append((room_id, room))
    for rooms in ordered_rooms.values():
        rooms.sort(key=lambda item: _room_index(item[0]))

    drone_allocations: dict[str, list[dict[str, Any]]] = {}
    for item in ((((plan.get("simulation") or {}).get("drone_plan") or {}).get("allocations") or [])):
        if float(item.get("drones", 0) or 0) <= 0:
            continue
        drone_allocations.setdefault(str(item.get("segment_id")), []).append(item)
    invalid_segments = sorted(segment_id for segment_id, items in drone_allocations.items() if len(items) > 1)
    if invalid_segments:
        raise ValueError(f"同一上线节点存在多个无人机目标: {invalid_segments}")
    dormitory_assignments = {
        (str(item.get("segment_id")), str(item.get("dormitory_id"))): item
        for item in ((plan.get("recovery_plan") or {}).get("events") or [])
    }
    right_side_assignments = {
        str(item.get("segment_id")): item.get("rooms") or {}
        for item in ((plan.get("right_side_plan") or {}).get("assignments") or [])
    }
    if set(right_side_assignments) != set(segments):
        raise ValueError("输入缺少逐班次 right_side_plan，不能导出空会客室或办公室")
    exported_plans: list[dict[str, Any]] = []
    for ordinal, (segment_id, segment) in enumerate(segments.items(), 1):
        assignments = segment.get("rooms") or {}
        rooms: dict[str, list[dict[str, Any]]] = {key: [] for key in required_room_keys}
        for output_key in ("trading", "manufacture", "power", "control"):
            for room_id, room_config in ordered_rooms[output_key]:
                assignment = assignments.get(room_id) or {}
                product = PRODUCT_NAMES.get(str(room_config.get("product_id") or ""))
                exported = _empty_room(product=product, skip=False) if output_key in {"trading", "manufacture"} else _empty_room(skip=False)
                exported["operators"] = [str(op.get("name")) for op in assignment.get("operators") or []]
                rooms[output_key].append(exported)
        dorm_count = len((plan.get("facility_configuration") or {}).get("dormitories") or [])
        rooms["dormitory"] = []
        for dorm_index in range(dorm_count):
            dorm_id = f"dormitory_{dorm_index + 1}"
            recovery = dormitory_assignments.get((segment_id, dorm_id)) or {}
            exported_dorm = _empty_room(skip=False)
            exported_dorm["operators"] = [str(name) for name in recovery.get("operators") or []]
            rooms["dormitory"].append(exported_dorm)
        fixed_right = right_side_assignments[segment_id]
        rooms["meeting"] = [_empty_room(skip=False)]
        rooms["meeting"][0]["operators"] = [str(name) for name in fixed_right.get("meeting") or []]
        rooms["hire"] = [_empty_room(skip=False)]
        rooms["hire"][0]["operators"] = [str(name) for name in fixed_right.get("hire") or []]
        rooms["processing"] = [_empty_room(skip=True)]

        allocations = drone_allocations.get(segment_id, [])
        allocation = max(allocations, key=lambda item: float(item.get("drones", 0) or 0), default=None)
        drone = {"room": "trading", "index": 1, "enable": False, "order": "pre"}
        if allocation:
            room_id = str(allocation.get("room_id") or "")
            facility = str(allocation.get("facility_id") or "")
            drone.update({
                "room": FACILITY_KEYS.get(facility, "trading"),
                "index": _room_index(room_id),
                "enable": True,
            })
        start = str(segment.get("start") or "")
        end = str(segment.get("end") or "")
        drone_description = f"完成换班后运行至 {end}"
        if allocation:
            count = float(allocation.get("drones", 0) or 0)
            count_text = str(int(round(count))) if abs(count - round(count)) < 1e-6 else f"{count:.2f}"
            room_key = FACILITY_KEYS.get(str(allocation.get("facility_id") or ""), "trading")
            room_text = f"{ROOM_DISPLAY_NAMES.get(room_key, room_key)}{_room_index(str(allocation.get('room_id') or ''))}"
            drone_description = f"无人机全部加速{room_text}，长期稳态使用{count_text}架；{drone_description}"
            if ordinal == 1:
                initial = float(((value.get("base_state") or {}).get("initial_drone_stock", count)) or count)
                if initial > count + 1e-6:
                    initial_text = str(int(round(initial))) if abs(initial - round(initial)) < 1e-6 else f"{initial:.2f}"
                    drone_description = f"无人机全部加速{room_text}；首次用完当前{initial_text}架，长期稳态约{count_text}架；完成换班后运行至 {end}"
        exported_plans.append({
            "name": f"第{ordinal:02d}班 {start}",
            "description": f"{start} 上线执行，覆盖 {start}–{end}",
            "description_post": drone_description,
            "Fiammetta": {"enable": False, "target": "", "order": "pre"},
            "drones": drone,
            "rooms": rooms,
        })

    project = value.get("project") or {}
    metrics = selected if selected else ((plan.get("simulation") or {}).get("aggregate_metrics") or {})
    description = "由 ark-base-schedule 求解、模拟并验证后导出"
    if metrics.get("orundum_per_day") is not None:
        description += f"；合成玉 {float(metrics['orundum_per_day']):.2f}/日，龙门币 {float(metrics.get('net_lmd_per_day', 0)):.2f}/日"
    return {
        "author": "ark-base-schedule",
        "description": description,
        "id": _stable_numeric_id({"plan_id": plan.get("plan_id"), "segments": segments}),
        "title": str(project.get("name") or plan.get("title") or "明日方舟基建排班"),
        "planTimes": f"{len(exported_plans)}班",
        "plans": exported_plans,
        "scheduleType": {
            "planTimes": len(exported_plans),
            "trading": len(ordered_rooms["trading"]),
            "manufacture": len(ordered_rooms["manufacture"]),
            "power": len(ordered_rooms["power"]),
            "dormitory": len((plan.get("facility_configuration") or {}).get("dormitories") or []),
        },
    }


def validate_exported_schedule(value: dict[str, Any]) -> list[str]:
    """Return structural errors for one template-compatible schedule."""
    errors: list[str] = []
    required = {"author", "description", "id", "title", "planTimes", "plans", "scheduleType"}
    missing = sorted(required - set(value))
    if missing:
        errors.append(f"缺少顶层字段: {missing}")
        return errors
    plans = value.get("plans") or []
    schedule_type = value.get("scheduleType") or {}
    if int(schedule_type.get("planTimes", -1)) != len(plans):
        errors.append("scheduleType.planTimes 与 plans 数量不一致")
    room_counts = {key: int(schedule_type.get(key, -1)) for key in ("trading", "manufacture", "power", "dormitory")}
    room_keys = {"trading", "manufacture", "power", "dormitory", "control", "meeting", "hire", "processing"}
    for index, plan in enumerate(plans, 1):
        rooms = plan.get("rooms") or {}
        if set(rooms) != room_keys:
            errors.append(f"第 {index} 班 rooms 键不完整")
            continue
        for key, expected in room_counts.items():
            if len(rooms.get(key) or []) != expected:
                errors.append(f"第 {index} 班 {key} 房间数与 scheduleType 不一致")
    return errors


def validate_schedule_matches_result(result: dict[str, Any], schedule: dict[str, Any]) -> list[str]:
    """Require the delivered schedule to be the deterministic export of result."""
    try:
        expected = export_schedule(result)
    except (KeyError, TypeError, ValueError) as exc:
        return [f"result.json 无法确定性导出排班: {exc}"]
    if schedule == expected:
        return []

    def first_difference(left: Any, right: Any, path: str = "$") -> str:
        if type(left) is not type(right):
            return f"{path} 类型不同"
        if isinstance(left, dict):
            if set(left) != set(right):
                return f"{path} 字段不同: expected={sorted(left)}, actual={sorted(right)}"
            for key in left:
                if left[key] != right[key]:
                    return first_difference(left[key], right[key], f"{path}.{key}")
        elif isinstance(left, list):
            if len(left) != len(right):
                return f"{path} 长度不同: expected={len(left)}, actual={len(right)}"
            for index, item in enumerate(left):
                if item != right[index]:
                    return first_difference(item, right[index], f"{path}[{index}]")
        return f"{path} 值不同: expected={left!r}, actual={right!r}"

    return [first_difference(expected, schedule)]


def main() -> int:
    parser = argparse.ArgumentParser(description="把求解结果导出为 template.json 兼容排班文件")
    parser.add_argument("input", help="result.json、solver-result.json 或独立 schema 4 plan")
    parser.add_argument("--template", default=str(ASSET_TEMPLATE))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    exported = export_schedule(read_json(args.input), read_json(args.template))
    errors = validate_exported_schedule(exported)
    if errors:
        raise ValueError("；".join(errors))
    write_json(args.output, exported)
    print(f"已生成排班文件: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
