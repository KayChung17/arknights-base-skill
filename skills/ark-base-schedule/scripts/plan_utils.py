#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Utilities for model-authored candidate plans.

The model owns candidate design. These helpers normalize structure, bind
operator names to the user's roster, and preserve explicit facility levels,
operation nodes, recovery events, and evidence metadata.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from data_loader import OwnedOperator, normalize_elite, read_roster
from timeline_utils import get_segments


def load_json_file(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return value


def roster_map(roster: list[OwnedOperator]) -> dict[str, OwnedOperator]:
    return {item.name: item for item in roster}


def _normalize_operator(raw: Any, owned: dict[str, OwnedOperator]) -> dict[str, Any]:
    if isinstance(raw, str):
        name = raw.strip()
        supplied: dict[str, Any] = {}
    elif isinstance(raw, dict):
        name = str(raw.get("name", "")).strip()
        supplied = raw
    else:
        raise ValueError(f"无法解析干员项: {raw!r}")

    if not name:
        raise ValueError("候选方案包含空干员名称")

    source = owned.get(name)
    if source:
        return {
            "name": source.name,
            "elite": source.elite,
            "morale": source.morale,
            "roster_verified": True,
        }

    return {
        "name": name,
        "elite": normalize_elite(supplied.get("elite", 0)),
        "morale": supplied.get("morale"),
        "roster_verified": False,
    }


def _infer_facility(room_key: str, room: dict[str, Any]) -> str:
    explicit = str(room.get("facility_id", ""))
    if explicit:
        return explicit
    lower = room_key.lower()
    if "贸易" in room_key or "trading" in lower:
        return "trading_post"
    if "制造" in room_key or "factory" in lower or "manufacturing" in lower:
        return "factory"
    if "发电" in room_key or "power" in lower:
        return "power_plant"
    if "控制" in room_key or "control" in lower:
        return "control_center"
    if "办公室" in room_key or "office" in lower:
        return "office"
    if "宿舍" in room_key or "dorm" in lower:
        return "dormitory"
    return "unknown"


def _default_level(facility_id: str) -> int:
    return {
        "trading_post": 3,
        "factory": 3,
        "power_plant": 3,
        "control_center": 5,
        "office": 3,
        "dormitory": 1,
    }.get(facility_id, 1)


def normalize_candidate_plan(
    plan: dict[str, Any],
    roster: list[OwnedOperator] | None = None,
) -> dict[str, Any]:
    """Normalize a model-authored plan without choosing assignments.

    A supplied roster is authoritative for elite and morale values. The
    function preserves the model's strategy, assignments, evidence, and
    timeline. Legacy `shifts` remain supported; new plans use `segments`.
    """

    result = copy.deepcopy(plan)
    result.setdefault("schema_version", 4 if result.get("segments") or result.get("facility_configuration") else 3)
    result.setdefault("plan_id", Path(str(result.get("name", "candidate"))).stem or "candidate")
    result.setdefault("title", result.get("name", result["plan_id"]))
    result.setdefault("plan_status", "candidate")
    result.setdefault("decision", {})
    result.setdefault("assumptions", {})
    result.setdefault("cross_shift_reuse_policy", "allowed_with_warning")
    result.setdefault("external_skill_evidence", [])
    result.setdefault("recovery_plan", {"events": [], "repeating_day_verified": False})

    key = "segments" if result.get("segments") is not None else "shifts"
    timeline = result.get(key)
    if isinstance(timeline, list):
        converted: dict[str, dict] = {}
        for index, segment in enumerate(timeline, 1):
            if not isinstance(segment, dict):
                raise ValueError(f"{key} 列表中的每项必须是对象")
            segment_key = str(segment.get("id") or segment.get("name") or f"区间{index}")
            converted[segment_key] = segment
        timeline = converted
        result[key] = timeline
    if not isinstance(timeline, dict) or not timeline:
        raise ValueError("候选方案必须包含非空 segments 或 shifts 对象")

    owned = roster_map(roster or [])
    for segment_key, segment in timeline.items():
        if not isinstance(segment, dict):
            raise ValueError(f"{segment_key}: 时间区间必须是对象")
        segment.setdefault("name", segment_key)
        rooms = segment.get("rooms")
        if not isinstance(rooms, dict):
            raise ValueError(f"{segment_key}: rooms 必须是对象")
        for room_key, room in list(rooms.items()):
            if isinstance(room, list):
                room = {"operators": room}
                rooms[room_key] = room
            if not isinstance(room, dict):
                raise ValueError(f"{segment_key}/{room_key}: 房间必须是对象或干员列表")
            occupants = room.get("operators", room.get("occupants", []))
            if occupants is None:
                occupants = []
            if not isinstance(occupants, list):
                raise ValueError(f"{segment_key}/{room_key}: operators 必须是列表")
            room["operators"] = [_normalize_operator(item, owned) for item in occupants]
            room.pop("occupants", None)

    # Facility configuration is explicit in v0.5.0. Legacy plans receive an
    # inferred configuration so they can still be evaluated as candidates.
    configuration = result.setdefault("facility_configuration", {})
    configured_rooms = configuration.setdefault("rooms", {})
    inferred = False
    first_segment = next(iter(get_segments(result).values()))
    for room_key, room in first_segment.get("rooms", {}).items():
        if room_key in configured_rooms:
            continue
        room = room if isinstance(room, dict) else {"operators": room}
        facility = _infer_facility(room_key, room)
        configured_rooms[room_key] = {
            "facility_id": facility,
            "level": int(room.get("level") or _default_level(facility)),
            "product_id": str(room.get("product_id", "")),
        }
        inferred = True

    result["normalization"] = {
        "model_assignments_preserved": True,
        "roster_is_authoritative": bool(roster),
        "unverified_operator_count": sum(
            1
            for segment in get_segments(result).values()
            for room in segment["rooms"].values()
            for op in room.get("operators", [])
            if not op.get("roster_verified", False)
        ),
        "facility_configuration_inferred": inferred,
        "legacy_shift_field": "segments" not in result and "shifts" in result,
    }
    return result


def normalize_plan_file(
    plan_path: str | Path,
    roster_path: str | Path | None = None,
) -> dict[str, Any]:
    roster = read_roster(roster_path) if roster_path else None
    return normalize_candidate_plan(load_json_file(plan_path), roster)


def write_json(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
