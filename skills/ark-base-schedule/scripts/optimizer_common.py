#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for the hybrid enumerator + MILP scheduling workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from data_loader import load_mechanics, normalize_elite, operator_index, select_available_skills


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent


@dataclass(frozen=True)
class Segment:
    segment_id: str
    name: str
    start: str
    end: str
    hours: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "hours": self.hours,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def stable_id(prefix: str, payload: Any, length: int = 12) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def context_segments(context: dict[str, Any]) -> list[Segment]:
    template = context.get("segment_template") or {}
    result: list[Segment] = []
    for segment_id, record in template.items():
        result.append(
            Segment(
                segment_id=segment_id,
                name=str(record.get("name") or segment_id),
                start=str(record.get("start") or ""),
                end=str(record.get("end") or ""),
                hours=float(record.get("hours") or 0),
            )
        )
    if not result:
        raise ValueError("decision context 缺少 segment_template")
    if abs(sum(item.hours for item in result) - 24.0) > 1e-6:
        raise ValueError("segment_template 必须覆盖完整24小时")
    return result


def context_rooms(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rooms = (context.get("facility_configuration") or {}).get("rooms") or {}
    if not rooms:
        raise ValueError("decision context 缺少 facility_configuration.rooms")
    mechanics = load_mechanics()
    facilities = mechanics.get("facilities", {})
    normalized: dict[str, dict[str, Any]] = {}
    for room_id, room in rooms.items():
        facility = str(room.get("facility_id") or "")
        product = str(room.get("product_id") or "")
        level = int(room.get("level") or 1)
        facility_record = facilities.get(facility, {})
        level_caps = facility_record.get("level_capacities", {})
        capacity = int(level_caps.get(str(level), facility_record.get("capacity", 1)))
        normalized[room_id] = {
            "room_id": room_id,
            "facility_id": facility,
            "product_id": product,
            "level": level,
            "capacity": capacity,
        }
    return normalized


def context_roster(context: dict[str, Any]) -> list[dict[str, Any]]:
    roster: list[dict[str, Any]] = []
    for item in context.get("roster") or []:
        if not item.get("recruited", True):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        roster.append(
            {
                "name": name,
                "elite": normalize_elite(item.get("elite", 0)),
                "level": max(1, int(float(item.get("level", 1) or 1))),
                "recruited": True,
                "morale": float(item["morale"]) if item.get("morale") is not None else None,
            }
        )
    if not roster:
        raise ValueError("decision context 的 roster 为空")
    return roster


def eligible_operators(
    context: dict[str, Any],
    facility_id: str,
    product_id: str,
    *,
    allow_external_evidence: bool = True,
) -> list[dict[str, Any]]:
    """Return owned operators with a verified skill for the room.

    Local versioned data is authoritative. Verified external evidence can add an
    operator to the candidate pool, but its numerical score remains conservative
    unless the evidence contains a structured ``base_bonus_pct``.
    """

    roster = context_roster(context)
    index = operator_index()
    external = context.get("external_evidence") or []
    external_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    if allow_external_evidence:
        for evidence in external:
            if not evidence.get("verified"):
                continue
            key = (
                str(evidence.get("operator") or ""),
                str(evidence.get("facility_id") or ""),
                str(evidence.get("product_id") or ""),
            )
            external_map[key] = evidence

    eligible: list[dict[str, Any]] = []
    for op in roster:
        record = index.get(op["name"])
        local_skills = []
        if record:
            for skill in record.get("skills", []):
                if skill.get("facility") != facility_id:
                    continue
                if int(skill.get("elite", 0)) > int(op["elite"]):
                    continue
                products = skill.get("products") or []
                if products and product_id and product_id not in products:
                    continue
                local_skills.append(skill)
        external_key = (op["name"], facility_id, product_id)
        evidence = external_map.get(external_key)
        if local_skills or evidence:
            copy = dict(op)
            copy["skill_source"] = "local_versioned_data" if local_skills else "verified_external_evidence"
            copy["external_evidence"] = evidence
            eligible.append(copy)
    return eligible


def objective_profile(context: dict[str, Any]) -> dict[str, float]:
    """Resolve transparent metric weights from preferences and goal."""

    preferences = (context.get("objective") or {}).get("preferences") or {}
    explicit = preferences.get("solver_weights")
    if isinstance(explicit, dict) and explicit:
        return {str(key): float(value) for key, value in explicit.items()}

    goal = str((context.get("objective") or {}).get("goal_id") or "")
    priority = str(preferences.get("priority") or "balanced")
    profiles: dict[str, dict[str, float]] = {
        "balanced": {
            "lmd_trade_work": 35.0,
            "orundum": 8.0,
            "orundum_shard": 80.0,
            "pure_gold": 20.0,
            "pure_gold_consumption": -20.0,
            "lmd_cost": -0.002,
            "orirock_cube_consumption": -1.0,
            "battle_record_exp": 0.006,
            "drone_recovery": 4.0,
            "hr_network": 0.5,
            "base_management": 2.0,
            "fixed_lmd": 0.002,
            "continuity": 0.5,
        },
        "orundum": {
            "lmd_trade_work": 15.0,
            "orundum": 15.0,
            "orundum_shard": 150.0,
            "pure_gold": 10.0,
            "pure_gold_consumption": -10.0,
            "lmd_cost": -0.001,
            "orirock_cube_consumption": -0.5,
            "battle_record_exp": 0.001,
            "drone_recovery": 5.0,
            "hr_network": 0.2,
            "base_management": 2.0,
            "fixed_lmd": 0.001,
            "continuity": 0.25,
        },
        "lmd": {
            "lmd_trade_work": 60.0,
            "orundum": 2.0,
            "orundum_shard": 20.0,
            "pure_gold": 30.0,
            "pure_gold_consumption": -30.0,
            "lmd_cost": -0.004,
            "orirock_cube_consumption": -1.5,
            "battle_record_exp": 0.001,
            "drone_recovery": 4.0,
            "hr_network": 0.2,
            "base_management": 2.0,
            "fixed_lmd": 0.004,
            "continuity": 0.5,
        },
        "low_operation": {
            "lmd_trade_work": 30.0,
            "orundum": 6.0,
            "orundum_shard": 60.0,
            "pure_gold": 15.0,
            "pure_gold_consumption": -15.0,
            "lmd_cost": -0.002,
            "orirock_cube_consumption": -1.0,
            "battle_record_exp": 0.004,
            "drone_recovery": 3.0,
            "hr_network": 0.5,
            "base_management": 2.0,
            "fixed_lmd": 0.002,
            "continuity": 8.0,
        },
        "orundum_lmd_balance": {
            # Lexicographic intent is approximated with a dominant orundum
            # coefficient while LMD and material flows are constrained by the
            # MILP. Battle records intentionally have zero value.
            "orundum": 1000.0,
            "lmd": 0.001,
            "lmd_cost": -0.001,
            "orundum_shard": 0.0,
            "orundum_shard_consumption": 0.0,
            "pure_gold": 0.0,
            "pure_gold_consumption": 0.0,
            "battle_record_exp": 0.0,
            "drone_recovery": 0.0,
            "hr_network": 0.0,
            "base_management": 0.25,
            "fixed_lmd": 0.001,
            "continuity": 0.2,
            "orirock_cube_consumption": 0.0,
        },
    }
    if priority in profiles:
        return profiles[priority]
    if goal in {"all_origin", "max_origin"}:
        return profiles["orundum"]
    if goal == "all_gold":
        return profiles["lmd"]
    return profiles["balanced"]


def factory_base_metrics(product_id: str) -> dict[str, float]:
    """Base production metrics per hour before efficiency bonuses.

    Values are intentionally explicit and versioned in ``mechanics.json`` when
    present. The fallback values preserve compatibility with v0.5 data.
    """

    mechanics = load_mechanics()
    values = mechanics.get("base_output_metrics_per_hour", {}).get(product_id)
    if isinstance(values, dict):
        return {str(k): float(v) for k, v in values.items()}
    fallback = {
        "pure_gold": {"pure_gold": 1 / 1.2},
        "orundum_shard": {"orundum_shard": 1.0},
        "battle_record": {"battle_record_exp": 1000 / 3.0},
        "drone_recovery": {"drone_recovery": 1.0},
        "hr_network": {"hr_network": 1.0},
        "base_management": {"base_management": 1.0},
    }
    return fallback.get(product_id, {})


def trading_base_metrics(product_id: str) -> dict[str, float]:
    mechanics = load_mechanics()
    values = mechanics.get("base_output_metrics_per_hour", {}).get(product_id)
    if isinstance(values, dict):
        return {str(k): float(v) for k, v in values.items()}
    if product_id == "orundum_order":
        return {"orundum": 10.0, "orundum_shard_consumption": 1.0}
    if product_id == "lmd_order":
        return {"lmd_trade_work": 1.0}
    return {}


def warehouse_capacity(room: dict[str, Any], operators: Iterable[Any] = ()) -> float | None:
    if room.get("facility_id") != "factory":
        return None
    mechanics = load_mechanics()
    factory = mechanics.get("warehouse_capacity", {}).get("factory", {})
    base = float(factory.get(str(room.get("level")), {1: 24, 2: 36, 3: 54}.get(room.get("level"), 24)))
    index = operator_index()
    extra = 0.0
    rhine_skill_count = 0
    selected_skills: list[dict[str, Any]] = []
    product = str(room.get("product_id") or "")
    for item in operators:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            elite = normalize_elite(item.get("elite", 0))
            level = int(item.get("level", 90) or 90)
        else:
            name = str(item)
            elite = 2
            level = 90
        record = index.get(name) or {}
        skills = select_available_skills(record, "factory", elite, product, level)
        selected_skills.extend(skills)
        rhine_skill_count += sum(str(skill.get("skill_name") or "").startswith("莱茵科技") for skill in skills)
        for skill in skills:
            for tag in skill.get("tags", []):
                if isinstance(tag, str) and tag.startswith("warehouse_capacity_"):
                    try:
                        extra += float(tag.rsplit("_", 1)[1])
                    except ValueError:
                        pass
    for skill in selected_skills:
        if "warehouse_per_rhine_skill_5" in skill.get("tags", []):
            extra += rhine_skill_count * 5.0
    return base + extra


def metric_score(metrics: dict[str, float], weights: dict[str, float]) -> float:
    return sum(float(metrics.get(key, 0.0)) * float(weight) for key, weight in weights.items())
