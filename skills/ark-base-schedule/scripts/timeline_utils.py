#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Timeline, room configuration, and strategy-template helpers."""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from data_loader import load_json, load_mechanics


def parse_clock(value: str) -> int:
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"时间必须使用 HH:MM: {value}")
    hour, minute = int(parts[0]), int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"时间超出范围: {value}")
    return hour * 60 + minute


def hours_between(start: str, end: str) -> float:
    start_minute = parse_clock(start)
    end_minute = parse_clock(end)
    delta = (end_minute - start_minute) % (24 * 60)
    if delta == 0:
        delta = 24 * 60
    return delta / 60.0


def build_operation_timeline(times: list[str]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if len(times) < 1:
        raise ValueError("至少需要一个上线时间")
    normalized = []
    seen = set()
    for value in times:
        minute = parse_clock(value)
        if minute in seen:
            raise ValueError(f"上线时间重复: {value}")
        seen.add(minute)
        normalized.append((minute, f"{minute // 60:02d}:{minute % 60:02d}"))
    normalized.sort()
    clocks = [item[1] for item in normalized]
    nodes = [
        {"time": value, "label": f"第{index}次上线", "actions": ["收取产物", "执行计划中的换班与恢复"]}
        for index, value in enumerate(clocks, 1)
    ]
    segments: dict[str, dict[str, Any]] = {}
    for index, start in enumerate(clocks):
        end = clocks[(index + 1) % len(clocks)]
        segments[f"segment_{index + 1}"] = {
            "name": f"区间{index + 1}",
            "start": start,
            "end": end,
            "hours": hours_between(start, end),
            "rooms": {},
        }
    return nodes, segments


def get_segments(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = plan.get("segments")
    if isinstance(value, dict) and value:
        return value
    legacy = plan.get("shifts")
    if isinstance(legacy, dict) and legacy:
        return legacy
    return {}


def get_strategy_templates() -> dict[str, Any]:
    return load_json("strategy-templates.json").get("templates", {})


def get_strategy_template(template_id: str) -> dict[str, Any]:
    templates = get_strategy_templates()
    if template_id not in templates:
        raise ValueError(f"未知攻略基线模板: {template_id}")
    return copy.deepcopy(templates[template_id])


def facility_capacity(facility_id: str, level: int | None = None, explicit: int | None = None) -> int:
    if explicit is not None:
        return int(explicit)
    mechanics = load_mechanics()
    info = mechanics["facilities"].get(facility_id, {})
    if level is not None:
        capacities = info.get("level_capacities", {})
        if str(level) in capacities:
            return int(capacities[str(level)])
    return int(info.get("capacity", 0))


def room_configuration(plan: dict[str, Any], room_id: str, room: dict[str, Any] | None = None) -> dict[str, Any]:
    configuration = plan.get("facility_configuration", {}).get("rooms", {}).get(room_id)
    if isinstance(configuration, dict):
        return configuration
    room = room or {}
    return {
        "facility_id": room.get("facility_id", ""),
        "product_id": room.get("product_id", ""),
        "level": room.get("level"),
        "capacity": room.get("capacity"),
    }


def expected_main_room_counts(plan: dict[str, Any]) -> Counter[str]:
    result: Counter[str] = Counter()
    for room in plan.get("facility_configuration", {}).get("rooms", {}).values():
        if isinstance(room, dict):
            result[str(room.get("facility_id", ""))] += 1
    return result


def operator_work_intervals(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for segment_id, segment in get_segments(plan).items():
        hours = float(segment.get("hours") or hours_between(segment.get("start", "00:00"), segment.get("end", "00:00")))
        for room_id, raw_room in segment.get("rooms", {}).items():
            room = raw_room if isinstance(raw_room, dict) else {"operators": raw_room}
            for op in room.get("operators", []):
                name = op.get("name") if isinstance(op, dict) else str(op)
                if not name:
                    continue
                result.setdefault(str(name), []).append({
                    "segment_id": segment_id,
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                    "hours": hours,
                    "room_id": room_id,
                })
    return result


def continuous_work_hours(plan: dict[str, Any]) -> dict[str, float]:
    """Return maximum consecutive work represented by adjacent equal-day segments.

    The function treats an operator present in adjacent segments as continuous,
    including the last-to-first edge when repeating_daily is true.
    """
    segments = list(get_segments(plan).items())
    if not segments:
        return {}
    names_by_segment: list[set[str]] = []
    hours = []
    for _, segment in segments:
        current = set()
        for raw_room in segment.get("rooms", {}).values():
            room = raw_room if isinstance(raw_room, dict) else {"operators": raw_room}
            for op in room.get("operators", []):
                name = op.get("name") if isinstance(op, dict) else str(op)
                if name:
                    current.add(str(name))
        names_by_segment.append(current)
        hours.append(float(segment.get("hours", 0) or 0))
    all_names = set().union(*names_by_segment)
    repeating = bool(plan.get("assumptions", {}).get("repeating_daily", False))
    result: dict[str, float] = {}
    for name in all_names:
        flags = [name in names for names in names_by_segment]
        scan_flags = flags + flags if repeating else flags
        scan_hours = hours + hours if repeating else hours
        current = 0.0
        best = 0.0
        limit = len(flags) * (2 if repeating else 1)
        for index in range(limit):
            if scan_flags[index]:
                current += scan_hours[index]
                best = max(best, current)
            else:
                current = 0.0
        result[name] = min(best, 24.0 if repeating else sum(hours))
    return result


def total_work_hours(plan: dict[str, Any]) -> dict[str, float]:
    result: Counter[str] = Counter()
    for name, intervals in operator_work_intervals(plan).items():
        result[name] = sum(float(item["hours"]) for item in intervals)
    return dict(result)


def rotation_analysis(plan: dict[str, Any]) -> dict[str, Any]:
    """Describe cross-shift room/operator rotation without inventing bindings."""
    segments = list(get_segments(plan).items())
    if not segments:
        return {"segment_count": 0, "rooms": [], "operators": {}}
    room_ids = sorted({
        str(room_id)
        for _, segment in segments
        for room_id in (segment.get("rooms") or {})
    })
    rooms: list[dict[str, Any]] = []
    for room_id in room_ids:
        names_by_segment: list[list[str]] = []
        for _, segment in segments:
            raw = (segment.get("rooms") or {}).get(room_id) or {}
            room = raw if isinstance(raw, dict) else {"operators": raw}
            names_by_segment.append(sorted({
                str(op.get("name") if isinstance(op, dict) else op)
                for op in room.get("operators", [])
                if (op.get("name") if isinstance(op, dict) else op)
            }))
        presence: dict[str, str] = {}
        for index, names in enumerate(names_by_segment):
            for name in names:
                flags = list(presence.get(name, "0" * len(segments)))
                flags[index] = "1"
                presence[name] = "".join(flags)
        rooms.append({
            "room_id": room_id,
            "operator_presence": dict(sorted(presence.items())),
            "rotation_patterns": sorted(set(presence.values())),
        })
    operators: dict[str, dict[str, Any]] = {}
    for room in rooms:
        for name, pattern in room["operator_presence"].items():
            operators.setdefault(name, {"rooms": [], "patterns": []})
            operators[name]["rooms"].append(room["room_id"])
            operators[name]["patterns"].append(pattern)
    return {
        "segment_count": len(segments),
        "segment_hours": [float(segment.get("hours", 0.0) or 0.0) for _, segment in segments],
        "rooms": rooms,
        "operators": operators,
        "pattern_legend": "每位干员按区间顺序使用0/1表示休息/工作；110、101、011是典型错峰轮换。",
    }
