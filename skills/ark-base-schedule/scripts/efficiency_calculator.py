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
from collections import defaultdict
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from effect_resolver import EffectContribution, resolve_effects

from data_loader import (
    OwnedOperator,
    load_mechanics,
    load_operator_data,
    normalize_elite,
    operator_group_index,
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

MLYNAR_EXTENDED_MORALE_SKILLS = {
    "左膀右臂", "S.W.E.E.P.", "零食网络", "清理协议", "替身", "必要责任", "护卫",
    "小小的领袖", "独善其身", "笑靥如春", "金盏花诗会", "捍卫之道", "博识生手",
    "点滴关照", "总工程师",
}
MLYNAR_DIRECT_FACILITIES = {"power_plant", "office", "reception_room"}
MLYNAR_EXTENDED_FACILITIES = {
    "power_plant", "factory", "trading_post", "office", "reception_room",
}


def _time_profile_value(profile: dict[str, Any], hour: float) -> float:
    """Return a deterministic skill's bonus during one hour of occupancy."""
    start = float(profile.get("start_bonus_pct", 0.0) or 0.0)
    final = float(profile.get("final_bonus_pct", start) or start)
    increment = float(profile.get("increment_pct_per_hour", 0.0) or 0.0)
    return min(final, start + max(0.0, hour - 1.0) * increment)


def average_time_dependent_bonus(result: dict[str, Any], hours: float) -> float:
    """Integrate deterministic ramp skills over a room segment.

    Base work is resolved in one-hour buckets. A fractional final bucket uses
    the value of the hour it enters, matching the game's continuous progress
    between hourly updates.
    """
    duration = max(0.0, float(hours or 0.0))
    if duration <= 0:
        return 0.0
    full_hours = int(duration)
    fraction = duration - full_hours
    total = 0.0
    for profile in result.get("time_dependent_bonus_profiles") or []:
        value = sum(_time_profile_value(profile, hour) for hour in range(1, full_hours + 1))
        if fraction > 1e-9:
            value += fraction * _time_profile_value(profile, full_hours + 1)
        total += value / duration
    return total


def effective_bonus_for_duration(result: dict[str, Any], hours: float) -> float:
    """Resolve a factory result at the requested occupancy duration."""
    profiles = result.get("time_dependent_bonus_profiles") or []
    if not profiles:
        return float(result.get("estimated_efficiency_bonus_pct", result.get("paper_bonus_pct", 0.0)) or 0.0)
    static = float(result.get("time_dependent_static_bonus_pct", 0.0) or 0.0)
    return static + average_time_dependent_bonus(result, hours)


def production_bonus_for_duration(result: dict[str, Any], hours: float) -> float:
    """Add the 1% base productivity provided by each working operator."""
    return effective_bonus_for_duration(result, hours) + float(
        result.get("staffing_base_bonus_pct", 0.0) or 0.0
    )


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
    result = {
        "name": str(op.get("name", "")).strip(),
        "elite": normalize_elite(op.get("elite", 0)),
        "level": max(1, int(float(op.get("level", 1) or 1))),
        "recruited": bool(op.get("recruited", True)),
        "morale": op.get("morale"),
    }
    for key in ("assigned_facility", "assigned_room_id"):
        if op.get(key):
            result[key] = str(op[key])
    return result


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
        drone_capacity: float = 235.0,
        facility_level: int = 1,
        training_room_level: int = 3,
        office_level: int = 3,
        reception_room_level: int = 3,
        dormitory_levels: list[int] | None = None,
        dormitory_occupant_count: int | None = None,
        global_operators: list[OwnedOperator | dict | str] | None = None,
        facility_level_sum: int | None = None,
    ):
        self.facility = normalize_facility(facility)
        self.product = normalize_product(product)
        self.operators = [_operator_dict(op) for op in operators]
        self.global_operators = [
            _operator_dict(op) for op in (global_operators if global_operators is not None else operators)
        ]
        self.trading_post_count = int(trading_post_count)
        self.power_plant_count = int(power_plant_count)
        self.drone_capacity = float(drone_capacity)
        self.facility_level = int(facility_level)
        self.training_room_level = max(1, min(3, int(training_room_level)))
        self.office_level = max(1, min(3, int(office_level)))
        self.reception_room_level = max(1, min(3, int(reception_room_level)))
        self.dormitory_levels = [int(value) for value in (dormitory_levels or [1, 1, 1, 1])]
        self.dormitory_occupant_count = (
            max(0, int(dormitory_occupant_count))
            if dormitory_occupant_count is not None
            else sum(1 for op in self.global_operators if op.get("assigned_facility") == "dormitory")
        )
        self.facility_level_sum = int(facility_level_sum or 0)
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

    def _mechanism_bonus_pct(self, skill: dict[str, Any]) -> float:
        mechanism = skill.get("mechanism") or {}
        if mechanism.get("type") != "step_bonus":
            return 0.0
        inputs = {"drone_capacity": self.drone_capacity}
        input_name = str(mechanism.get("input") or "")
        if input_name not in inputs:
            return 0.0
        step = float(mechanism.get("step", 0.0) or 0.0)
        if step <= 0:
            return 0.0
        value = math.floor(inputs[input_name] / step) * float(mechanism.get("bonus_pct_per_step", 0.0) or 0.0)
        cap = mechanism.get("cap_pct")
        return min(value, float(cap)) if cap is not None else value

    def _groups(self, operator: dict) -> set[str]:
        return set(operator_group_index().get(operator["name"], set()))

    def count_global_group(self, group: str, facility: str | None = None) -> int:
        return sum(
            1
            for op in self.global_operators
            if group in self._groups(op)
            and (facility is None or not op.get("assigned_facility") or op.get("assigned_facility") == facility)
        )

    def count_room_group(self, group: str) -> int:
        return sum(1 for op in self.operators if group in self._groups(op))

    def count_global_group_facilities(
        self,
        group: str,
        *,
        excluded_facilities: set[str] | None = None,
    ) -> int:
        excluded = excluded_facilities or set()
        occupied: set[str] = set()
        for op in self.global_operators:
            facility = str(op.get("assigned_facility") or "")
            if not facility or facility in excluded or group not in self._groups(op):
                continue
            room_id = str(op.get("assigned_room_id") or "")
            occupied.add(room_id or facility)
        return len(occupied)

    def _global_control_tags(self) -> set[str]:
        tags: set[str] = set()
        for op in self.global_operators:
            if op.get("assigned_facility") and op.get("assigned_facility") != "control_center":
                continue
            for skill in self._skills(op, "control_center", ""):
                tags.update(skill.get("tags", []))
        return tags

    def _global_control_skills(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        active: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for op in self.global_operators:
            if op.get("assigned_facility") and op.get("assigned_facility") != "control_center":
                continue
            for skill in self._skills(op, "control_center", ""):
                active.append((op, skill))
        return active

    def _global_power_skills(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        active: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for op in self.global_operators:
            if op.get("assigned_facility") != "power_plant":
                continue
            for skill in self._skills(op, "power_plant", "drone_recovery"):
                active.append((op, skill))
        return active

    def _global_control_heat(self) -> float:
        heat = 0.0
        for op, skill in self._global_control_skills():
                tags = set(skill.get("tags", []))
                if "ave_dorm_heat_1" in tags:
                    heat += float(self.dormitory_occupant_count)
                if "ave_heat_10" in tags:
                    heat += 10.0
                if "ave_heat_20" in tags:
                    heat += 20.0
        return heat

    def _effect_condition_met(self, condition: dict[str, Any] | None, source: dict[str, Any]) -> bool:
        if not condition:
            return True
        condition_type = str(condition.get("type") or "")
        factory_count = max(0, 9 - self.trading_post_count - self.power_plant_count)
        external = self.trading_post_count + self.power_plant_count
        if condition_type == "layout_external_gte_field":
            return external >= factory_count
        if condition_type == "layout_field_gt_external":
            return factory_count > external
        if condition_type == "control_center_group_companion":
            group = str(condition.get("group") or "")
            minimum = int(condition.get("minimum_count", 2) or 2)
            members = [
                op for op in self.global_operators
                if (not op.get("assigned_facility") or op.get("assigned_facility") == "control_center")
                and group in self._groups(op)
            ]
            return len(members) >= minimum and any(op["name"] == source["name"] for op in members)
        if condition_type == "global_group_at_facility_minimum":
            group = str(condition.get("group") or "")
            facility = str(condition.get("facility") or "")
            minimum = int(condition.get("minimum_count", 1) or 1)
            return self.count_global_group(group, facility) >= minimum
        return False

    def _effect_value(self, effect: dict[str, Any]) -> float:
        if effect.get("value_pct") is not None:
            return float(effect.get("value_pct", 0.0) or 0.0)
        mechanism = effect.get("mechanism") or {}
        if mechanism.get("type") == "step_bonus" and mechanism.get("input") == "control_heat":
            step = float(mechanism.get("step", 0.0) or 0.0)
            if step <= 0:
                return 0.0
            value = math.floor(self._global_control_heat() / step) * float(
                mechanism.get("bonus_pct_per_step", 0.0) or 0.0
            )
            cap = mechanism.get("cap_pct")
            return min(value, float(cap)) if cap is not None else value
        return 0.0

    def _resolved_control_effects(self) -> tuple[dict[str, float], dict[str, list[str]]]:
        contributions: list[EffectContribution] = []
        for op, skill in self._global_control_skills():
            effects = list(skill.get("effects") or [])
            for effect in effects:
                if not self._effect_condition_met(effect.get("condition"), op):
                    continue
                contributions.append(EffectContribution(
                    effect_key=str(effect.get("effect_key") or ""),
                    stacking=str(effect.get("stacking") or "add"),
                    value=self._effect_value(effect),
                    source=f"{op['name']}/{skill.get('skill_name', '')}",
                    priority=int(effect.get("priority", 0) or 0),
                ))
            if effects:
                continue
            # Backward compatibility for older records. Production-relevant
            # max effects are required to migrate to ``effects`` by validation.
            tags = set(skill.get("tags", []))
            source = f"{op['name']}/{skill.get('skill_name', '')}"
            if "all_trading_bonus_7" in tags:
                contributions.append(EffectContribution("global_trading_order_efficiency_pct", "max", 7.0, source))
            if "all_factory_bonus_2" in tags:
                contributions.append(EffectContribution("global_factory_productivity_pct", "max", 2.0, source))
        return resolve_effects(contributions)

    def _active_gladiia_rule(self) -> dict[str, Any] | None:
        for _, skill in self._global_control_skills():
            for rule in skill.get("special_rules") or []:
                if rule.get("type") == "group_factory_bonus":
                    return rule
        return None

    def _global_human_fireworks(self) -> float:
        value = 0.0
        for _, skill in self._global_control_skills():
            if "human_fireworks_per_sui_5_cap_25" in skill.get("tags", []):
                active_sui = sum(
                    1
                    for op in self.global_operators
                    if "sui" in self._groups(op)
                    and op.get("assigned_facility") not in {"dormitory", "activity_room"}
                )
                value += min(25.0, active_sui * 5.0)
        for op in self.global_operators:
            morale = float(op.get("morale", 24.0) if op.get("morale") is not None else 24.0)
            if op.get("assigned_facility") == "control_center":
                for skill in self._skills(op, "control_center", ""):
                    tags = set(skill.get("tags", []))
                    if "morale_threshold_fireworks_15_else_perception_10" in tags and morale > 12:
                        value += 15.0
                    if "morale_at_most_12_fireworks_15" in tags and morale <= 12:
                        value += 15.0
            if op.get("assigned_facility") and op.get("assigned_facility") != "trading_post":
                if op.get("assigned_facility") == "office":
                    for skill in self._skills(op, "office", ""):
                        if "human_fireworks_per_extra_recruitment_slot_10" in skill.get("tags", []):
                            value += float(self.office_level * 10)
                continue
            for skill in self._skills(op, "trading_post", ""):
                if "human_fireworks_per_dorm_occupant_1" in skill.get("tags", []):
                    value += float(self.dormitory_occupant_count)
        return value

    def _global_catnip(self) -> float:
        value = 0.0
        for _, skill in self._global_control_skills():
            tags = set(skill.get("tags", []))
            if "catnip_fixed_8" in tags:
                value += 8.0
            if "catnip_per_control_monster_hunter_2" in tags:
                value += 2.0 * self.count_global_group("monster_hunter", "control_center")
        return value

    def _global_perception_information(self) -> float:
        """Return the current perception-information counter.

        Intermediate-product conversion is non-consuming: callers may derive
        multiple downstream counters from the same perception information.
        """
        value = 0.0
        for op in self.global_operators:
            facility = op.get("assigned_facility")
            if facility == "trading_post":
                for skill in self._skills(op, "trading_post", "lmd_order"):
                    if "perception_per_dorm_occupant_1" in skill.get("tags", []):
                        value += float(self.dormitory_occupant_count)
            if facility == "control_center":
                for skill in self._skills(op, "control_center", ""):
                    tags = set(skill.get("tags", []))
                    morale = float(op.get("morale", 24.0) if op.get("morale") is not None else 24.0)
                    if "morale_threshold_fireworks_15_else_perception_10" in tags and morale <= 12:
                        value += 10.0
                    if "morale_above_12_perception_10" in tags and morale > 12:
                        value += 10.0
                    for tag in skill.get("tags", []):
                        if isinstance(tag, str) and tag.startswith("perception_fixed_"):
                            value += float(tag.rsplit("_", 1)[1])
        return value

    def _global_thought_chain(self) -> float:
        converts = any(
            "perception_to_thought_chain_1" in skill.get("tags", [])
            for op in self.global_operators
            if op.get("assigned_facility") == "factory"
            for skill in self._skills(op, "factory", self.product)
        )
        return self._global_perception_information() if converts else 0.0

    def _global_witchcraft_crystal(self) -> float:
        converts = any(
            "fireworks_to_witchcraft_5_1" in skill.get("tags", [])
            for op in self.global_operators
            if op.get("assigned_facility") == "factory"
            for skill in self._skills(op, "factory", self.product)
        )
        return math.floor(self._global_human_fireworks() / 5.0) if converts else 0.0

    def _global_monster_cooking(self) -> float:
        return sum(
            sum(self.dormitory_levels)
            for op in self.global_operators
            if op.get("assigned_facility") == "dormitory"
            for skill in self._skills(op, "dormitory", "")
            if "monster_cooking_per_dorm_level_1" in skill.get("tags", [])
        )

    def _global_engineering_robots(self) -> float:
        active = any(
            "engineering_robot_per_facility_level_1_cap_64" in skill.get("tags", [])
            for op in self.global_operators
            if op.get("assigned_facility") == "factory"
            for skill in self._skills(op, "factory", self.product)
        )
        return min(64.0, float(self.facility_level_sum)) if active else 0.0

    def _global_silent_resonance(self) -> float:
        direct = 0.0
        converts_perception = False
        for op in self.global_operators:
            facility = op.get("assigned_facility")
            if facility == "trading_post":
                for skill in self._skills(op, "trading_post", "lmd_order"):
                    if "perception_to_silent_resonance_1" in skill.get("tags", []):
                        converts_perception = True
            elif facility == "office":
                for skill in self._skills(op, "office", ""):
                    if "silent_resonance_per_extra_recruitment_slot_15" in skill.get("tags", []):
                        direct += float(self.office_level * 15)
            elif facility == "dormitory":
                for skill in self._skills(op, "dormitory", ""):
                    if "silent_resonance_per_dorm_occupant_1" in skill.get("tags", []):
                        direct += float(self.dormitory_occupant_count)
        if converts_perception:
            direct += self._global_perception_information()
        return direct

    def _automation_power_plant_count(self) -> int:
        virtual = 0
        actual_work_platforms = self.count_global_group("work_platform", "power_plant")
        if actual_work_platforms == 0:
            for op in self.global_operators:
                if op.get("assigned_facility") != "power_plant":
                    continue
                if any(
                    "automation_virtual_power_plant_1" in skill.get("tags", [])
                    for skill in self._skills(op, "power_plant", "drone_recovery")
                ):
                    virtual = 1
                    break
        lancet_active = any(
            op.get("assigned_facility") == "power_plant" and op.get("name") == "Lancet-2"
            for op in self.global_operators
        )
        if lancet_active and any(
            "automation_virtual_power_plant_2_if_lancet" in skill.get("tags", [])
            for _, skill in self._global_control_skills()
        ):
            virtual += 2
        return self.power_plant_count + virtual

    def _room_skill_category_count(self, category: str) -> int:
        categories: list[str] = []
        alias_standardization = False
        for op in self.operators:
            for skill in self._skills(op, "factory", self.product):
                variant = str(skill.get("variant_group") or "")
                if ":name:" in variant:
                    categories.append(variant.rsplit(":name:", 1)[1])
                if "standardization_alias_rhine_red_pine" in skill.get("tags", []):
                    alias_standardization = True
        if category == "标准化" and alias_standardization:
            return sum(value in {"标准化", "莱茵科技", "红松骑士团"} for value in categories)
        return categories.count(category)

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

    def morale_cost_rates(self) -> dict[str, float]:
        """Return per-hour morale consumption for operators in this room."""
        rates = {op["name"]: 1.0 for op in self.operators}
        room_recovery = 0.0
        room_morale_cost = 0.0
        staffed_reduction = 0.0
        if self.facility in {"trading_post", "factory"}:
            if len(self.operators) >= 3:
                staffed_reduction = 0.10
            elif len(self.operators) >= 2:
                staffed_reduction = 0.05
        control_occupants = sum(
            1 for op in self.global_operators
            if op.get("assigned_facility") == "control_center"
        )
        global_control_reduction = min(0.25, control_occupants * 0.05)
        mlynar_business_active = any(
            "mlynar_business_is_business" in skill.get("tags", [])
            for _, skill in self._global_control_skills()
        )
        mlynar_direct_recovery = (
            0.10 if mlynar_business_active and self.facility in MLYNAR_DIRECT_FACILITIES else 0.0
        )
        mlynar_extended_recovery = 0.0
        if mlynar_business_active and self.facility in MLYNAR_EXTENDED_FACILITIES:
            mlynar_extended_recovery = 0.05 * sum(
                1
                for _, skill in self._global_control_skills()
                if str(skill.get("skill_name") or "") in MLYNAR_EXTENDED_MORALE_SKILLS
            )
        chongyue_recovery = 0.0
        babel_recovery = 0.0
        if self.facility in MLYNAR_EXTENDED_FACILITIES:
            control_names = {
                op["name"] for op in self.global_operators
                if op.get("assigned_facility") == "control_center"
            }
            for _, skill in self._global_control_skills():
                tags = set(skill.get("tags", []))
                if "chongyue_other_facility_morale_recovery" in tags:
                    chongyue_recovery = max(
                        chongyue_recovery,
                        0.05 + math.floor(self._global_human_fireworks() / 20.0) * 0.05,
                    )
                if "babel_other_facility_morale_recovery" in tags:
                    babel_recovery = max(
                        babel_recovery,
                        0.10 + (0.10 if "魔王" in control_names else 0.0),
                    )
        compared_other_facility_recovery = max(
            mlynar_extended_recovery,
            chongyue_recovery,
            babel_recovery,
        )
        control_room_recovery = 0.0
        control_group_recovery = 0.0
        targeted_control_recovery: dict[str, float] = defaultdict(float)
        if self.facility == "control_center":
            control_names = {op["name"] for op in self.operators}
            for source, skill in self._global_control_skills():
                tags = set(skill.get("tags", []))
                if "control_room_all_morale_recovery_0.05" in tags:
                    control_room_recovery += 0.05
                if "control_alternate_per_member_morale_recovery_0.05" in tags:
                    control_group_recovery += 0.05 * self.count_global_group("alternate", "control_center")
                if "control_lgd_per_member_morale_recovery_0.05" in tags:
                    control_group_recovery += 0.05 * self.count_global_group("lungmen_guard_department", "control_center")
                if "control_lee_per_member_morale_recovery" in tags:
                    lee_count = self.count_global_group("lee_detective_agency", "control_center")
                    control_group_recovery += 0.05 * lee_count
                    for name in control_names:
                        if "lee_detective_agency" in operator_group_index().get(name, set()):
                            targeted_control_recovery[name] += 0.20 * lee_count
                if "demon_king_amiya_pair_morale_recovery_0.05" in tags:
                    if "魔王" in control_names and "阿米娅" in control_names:
                        targeted_control_recovery["魔王"] += 0.05
                        targeted_control_recovery["阿米娅"] += 0.05
                if "demon_king_amiya_pair_morale_recovery_0.10" in tags:
                    if "魔王" in control_names and "阿米娅" in control_names:
                        targeted_control_recovery["魔王"] += 0.10
                        targeted_control_recovery["阿米娅"] += 0.10
        room_names = {op["name"] for op in self.operators}
        sui_modifier_immunity = self.facility == "control_center" and any(
            "control_sui_self_morale_modifier_immunity" in skill.get("tags", [])
            for _, skill in self._global_control_skills()
        )
        for op in self.operators:
            for skill in self._skills(op):
                tags = set(skill.get("tags", []))
                for tag in tags:
                    if tag.startswith("room_morale_recovery_"):
                        room_recovery += float(tag.rsplit("_", 1)[1])
                    elif tag.startswith("room_morale_cost_plus_"):
                        room_morale_cost += float(tag.rsplit("_", 1)[1])
                    elif tag.startswith("morale_cost_minus_"):
                        if sui_modifier_immunity and "sui" in self._groups(op):
                            continue
                        rates[op["name"]] -= float(tag.rsplit("_", 1)[1])
                    elif tag.startswith("morale_cost_plus_") and "ave_trade_per_8_heat_1" not in tags:
                        if sui_modifier_immunity and "sui" in self._groups(op):
                            continue
                        rates[op["name"]] += float(tag.rsplit("_", 1)[1])
                if "vigil_same_room_morale_reduction_0.1" in tags and "伺夜" in room_names:
                    rates[op["name"]] -= 0.1
                if "ave_trade_per_8_heat_1" in tags:
                    extra = math.floor(self._global_control_heat() / 8.0) * 0.01
                    if "丰川祥子" in room_names and any(
                        "cancel_ave_morale_cost_with_sakiko" in other.get("tags", [])
                        for other in self._skills(op)
                    ):
                        extra = 0.0
                    rates[op["name"]] += extra
        return {
            name: max(
                0.0,
                value
                + room_morale_cost
                - room_recovery
                - staffed_reduction
                - global_control_reduction
                - mlynar_direct_recovery
                - compared_other_facility_recovery
                - control_room_recovery
                - control_group_recovery
                - targeted_control_recovery.get(name, 0.0),
            )
            for name, value in rates.items()
        }

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
            "staffing_base_bonus_pct": (
                float(len(self.operators))
                if self.facility in {"trading_post", "factory"}
                else 0.0
            ),
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
        jaye_special_bonus = 0.0
        jaye_amplifier_exclusion = False

        whisper_operators = {
            op["name"]
            for op in self.operators
            if any(
                "shamare_whisper_per_other_worker_45" in skill.get("tags", [])
                for skill in self._skills(op)
            )
        }
        whisper_active = bool(whisper_operators)

        durin_count = min(self.count_global_group("durin"), 4)
        hongxue_active = any(
            "hongxue_line_source" in skill.get("tags", [])
            for op in self.operators
            for skill in self._skills(op)
        )
        production_lines = durin_count if hongxue_active else 0
        if any(
            "qiliang_virtual_lines" in skill.get("tags", [])
            for op in self.operators
            for skill in self._skills(op)
        ):
            production_lines += (production_lines // 2) * 2

        control_tags = self._global_control_tags()
        control_effects, control_effect_sources = self._resolved_control_effects()
        room_glasgow = self.count_room_group("glasgow")
        room_siracusa = self.count_room_group("siracusa")
        room_laterano = self.count_room_group("laterano")
        room_names = {op["name"] for op in self.operators}
        room_order_capacity_increase = 0
        room_order_capacity_decrease = 0
        if "赫德雷" in room_names:
            for _control_op, control_skill in self._global_control_skills():
                control_skill_tags = set(control_skill.get("tags", []))
                if "control_hoederer_order_capacity_2" in control_skill_tags:
                    room_order_capacity_increase += 2
                elif "control_hoederer_order_capacity_1" in control_skill_tags:
                    room_order_capacity_increase += 1
        for room_op in self.operators:
            for room_skill in self._skills(room_op):
                for room_tag in room_skill.get("tags", []):
                    if room_tag == "order_capacity_per_room_level_1":
                        room_order_capacity_increase += self.facility_level
                        continue
                    if room_tag == "vigil_same_room_order_capacity_2":
                        if "伺夜" in room_names:
                            room_order_capacity_increase += 2
                        continue
                    if isinstance(room_tag, str) and room_tag.startswith("order_capacity_minus_"):
                        try:
                            room_order_capacity_decrease += int(float(room_tag.rsplit("_", 1)[1]))
                        except ValueError:
                            pass
                        continue
                    if isinstance(room_tag, str) and room_tag.startswith("order_capacity_"):
                        try:
                            room_order_capacity_increase += int(float(room_tag.rsplit("_", 1)[1]))
                        except ValueError:
                            pass
        room_order_capacity = max(-9, room_order_capacity_increase - room_order_capacity_decrease)
        snowant_caps: list[float] = []
        jaye_e1_operators: set[str] = set()
        for room_op in self.operators:
            room_tags = {
                tag
                for room_skill in self._skills(room_op)
                for tag in room_skill.get("tags", [])
            }
            if {"jaye_order_gap_4", "jaye_order_count_4"} <= room_tags:
                jaye_e1_operators.add(room_op["name"])

        for op in self.operators:
            detail = {"name": op["name"], "elite": op["elite"], "notes": []}
            op_direct = 0.0
            op_facility = 0.0
            op_global = 0.0
            cleared_by_whisper = whisper_active and op["name"] not in whisper_operators
            skills = self._skills(op)
            if not skills:
                detail["notes"].append("当前精英等级未解锁适配技能，或技能数据缺失")
            for skill in skills:
                tags = set(skill.get("tags", []))
                bonus = float(skill.get("base_bonus_pct", 0))
                if "shamare_whisper_per_other_worker_45" in tags:
                    workers = max(0, len(self.operators) - 1)
                    value = workers * 45.0
                    op_direct += value
                    special_flags.append("shamare_whisper_reset")
                    detail["notes"].append(
                        f"同站其他工作干员 {workers} 人 ×45% = +{value:g}%"
                    )
                    continue
                if "tequila_investment_order" in tags:
                    special_flags.append("tequila_investment_order")
                    detail["notes"].append("仅对符合条件的4赤金订单加价，由订单模型结算")
                    continue
                if "override_room_direct_bonus" in tags:
                    override_values.append(bonus)
                    detail["notes"].append(f"直接订单效率替换为 +{bonus:.0f}%")
                    continue
                if "multiplier_1_556" in tags:
                    special_flags.append("legacy_proviso_order_model")
                    detail["notes"].append("违约订单收益由订单模型单独计算")
                    continue
                if "independent_order_lmd_500" in tags:
                    fixed_order_lmd += 500
                    op_direct += bonus
                    detail["notes"].append(f"直接效率 +{bonus:.0f}%；独立订单价值 +500 龙门币/触发")
                    continue
                if "hongxue_line_source" in tags:
                    detail["notes"].append(f"赤金生产线来源：{durin_count} 条")
                    continue
                if "hongxue_per_line_5" in tags or "hongxue_per_line" in tags:
                    per_line = 5.0 if "hongxue_per_line_5" in tags else bonus
                    value = production_lines * per_line
                    op_global += value
                    detail["notes"].append(f"{production_lines} 条生产线 × {per_line:.0f}% = +{value:.0f}%")
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
                if "morgan_glasgow_compass" in tags:
                    glasgow_bonus = room_glasgow * 20
                    siege_bonus = 35 if "推进之王" in room_names else 0
                    value = glasgow_bonus + siege_bonus
                    op_global += value
                    detail["notes"].append(
                        f"同站格拉斯哥 {room_glasgow} 人 ×20% + 推进之王同站 {siege_bonus}% = +{value}%"
                    )
                    continue
                if "laterano_per_member_15" in tags:
                    value = room_laterano * 15
                    op_global += value
                    detail["notes"].append(f"同站拉特兰成员 {room_laterano} 人：+{value:.0f}%")
                    continue
                if "trade_per_other_worker_10" in tags:
                    value = max(0, len(self.operators) - 1) * 10
                    op_direct += value
                    detail["notes"].append(f"同站其他工作干员 {max(0, len(self.operators) - 1)} 人：+{value:g}%")
                    continue
                if "trade_per_other_worker_20" in tags:
                    value = max(0, len(self.operators) - 1) * 20
                    op_direct += value
                    detail["notes"].append(f"同站其他工作干员 {max(0, len(self.operators) - 1)} 人：+{value:g}%")
                    continue
                if "trade_per_other_worker_15" in tags:
                    workers = max(0, len(self.operators) - 1)
                    value = workers * 15
                    op_direct += value
                    detail["notes"].append(f"同站其他工作干员 {workers} 人：+{value:g}%")
                    continue
                if "trade_per_sui_occupied_facility_4_cap_20" in tags:
                    occupied = self.count_global_group_facilities(
                        "sui",
                        excluded_facilities={"assistant", "activity_room"},
                    )
                    counted = min(5, occupied)
                    value = counted * 4
                    op_global += value
                    detail["notes"].append(
                        f"进驻岁干员的有效设施 {occupied} 间，计入 {counted} 间 ×4% = +{value}%"
                    )
                    continue
                if "trade_per_elite_operator_facility_2_cap_20" in tags:
                    occupied = self.count_global_group_facilities(
                        "elite_operator",
                        excluded_facilities={"assistant", "activity_room"},
                    )
                    counted = min(10, occupied)
                    value = counted * 2.0
                    op_direct += bonus
                    op_global += value
                    detail["notes"].append(
                        f"基础 +{bonus:g}%；进驻精英小队干员的有效设施 {occupied} 间，"
                        f"计入 {counted} 间 ×2% = +{value:g}%"
                    )
                    continue
                if "trade_per_human_fireworks_1" in tags:
                    value = self._global_human_fireworks()
                    op_global += value
                    detail["notes"].append(f"人间烟火 {value:g}：订单效率 +{value:g}%")
                    continue
                if "trade_per_monster_cooking_1" in tags:
                    value = self._global_monster_cooking()
                    op_global += value
                    detail["notes"].append(f"魔物料理 {value:g}：订单效率 +{value:g}%")
                    continue
                if "trade_per_dorm_level_sum_1" in tags or "trade_per_dorm_level_sum_2" in tags:
                    per_level = 2.0 if "trade_per_dorm_level_sum_2" in tags else 1.0
                    value = sum(self.dormitory_levels) * per_level
                    op_facility += value
                    detail["notes"].append(f"宿舍等级总和 {sum(self.dormitory_levels)} ×{per_level:g}% = +{value:g}%")
                    continue
                if "trade_per_positive_order_capacity_1_4" in tags:
                    value = room_order_capacity_increase * 4.0
                    op_global += value
                    detail["notes"].append(f"正订单上限加成 {room_order_capacity_increase} ×4% = +{value:g}%")
                    continue
                if "trade_per_positive_order_capacity_5_25_cap_100" in tags:
                    value = min(100.0, math.floor(room_order_capacity_increase / 5.0) * 25.0)
                    op_global += value
                    detail["notes"].append(f"正订单上限加成 {room_order_capacity_increase}，每5点+25%：+{value:g}%")
                    continue
                if "trade_per_catnip_3" in tags:
                    catnip = self._global_catnip()
                    value = catnip * 3.0
                    op_direct += bonus
                    op_global += value
                    detail["notes"].append(f"基础 +{bonus:g}%；木天蓼 {catnip:g}：订单效率 +{value:g}%")
                    continue
                if "trade_reception_room_level_5_cap_40" in tags:
                    value = min(40.0, bonus + self.reception_room_level * 5.0)
                    op_direct += value
                    detail["notes"].append(
                        f"基础 +{bonus:g}%；会客室 {self.reception_room_level} 级 ×5%，合计 +{value:g}%"
                    )
                    continue
                if "vigil_anywhere_trade_bonus_5" in tags or "vigil_anywhere_trade_bonus_10" in tags:
                    extra = 10.0 if "vigil_anywhere_trade_bonus_10" in tags else 5.0
                    vigil_active = any(
                        other["name"] == "伺夜"
                        and other.get("assigned_facility") not in {None, "", "assistant", "activity_room"}
                        for other in self.global_operators
                    )
                    op_direct += bonus + (extra if vigil_active else 0.0)
                    detail["notes"].append(
                        f"基础 +{bonus:g}%；伺夜在基建内：+{extra if vigil_active else 0:g}%"
                    )
                    continue
                if "trade_per_silent_resonance_4_1" in tags or "trade_per_silent_resonance_2_1" in tags:
                    step = 2.0 if "trade_per_silent_resonance_2_1" in tags else 4.0
                    resonance = self._global_silent_resonance()
                    value = math.floor(resonance / step)
                    op_global += value
                    detail["notes"].append(
                        f"无声共鸣 {resonance:g}，每 {step:g} 点 +1%：+{value:g}%"
                    )
                    continue
                if "lemuen_with_exusiai_25" in tags:
                    op_direct += bonus
                    extra = 25 if any("能天使" in name for name in room_names if name != op["name"]) else 0
                    op_global += extra
                    detail["notes"].append(f"基础 +{bonus:.0f}%；能天使同站附加 +{extra:.0f}%")
                    continue
                if "texas_with_lappland_65" in tags:
                    value = 65.0 if "拉普兰德" in room_names else 0.0
                    op_direct += value
                    detail["notes"].append(f"拉普兰德同站：订单效率 +{value:.0f}%")
                    continue
                if "jaye_order_gap_4" in tags:
                    if op["name"] in jaye_e1_operators:
                        detail["notes"].append("摊贩经济与市井之道按有效订单上限共同结算")
                        continue
                    value = (10 + room_order_capacity) * 4
                    op_global += value
                    jaye_special_bonus += value
                    detail["notes"].append(f"按空订单代理：基础上限10+附加{room_order_capacity}，估算 +{value:.0f}%")
                    result["warnings"].append("孑E0效率按空订单代理值估算，实际随订单堆积下降。")
                    continue
                if "jaye_order_count_4" in tags:
                    jaye_amplifier_exclusion = any(
                        rule.get("type") == "amplifier_exclusion"
                        for rule in (skill.get("special_rules") or [])
                    )
                    if op["name"] in jaye_e1_operators:
                        detail["notes"].append("市井之道与摊贩经济按有效订单上限共同结算")
                        continue
                    detail["notes"].append("市井之道缺少摊贩经济，无法形成完整联动")
                    result["warnings"].append("孑E1技能数据不完整：缺少摊贩经济。")
                    continue
                if "snowant_amplifier_cap_25" in tags:
                    if not cleared_by_whisper:
                        snowant_caps.append(25.0)
                    detail["notes"].append("雪雉放大上限 +25%")
                    continue
                if "snowant_amplifier_cap_35" in tags:
                    if not cleared_by_whisper:
                        snowant_caps.append(35.0)
                    detail["notes"].append("雪雉放大上限 +35%")
                    continue
                if "special_order" in tags:
                    op_direct += bonus
                    special_flags.append("special_order")
                    detail["notes"].append(f"直接效率 +{bonus:.0f}%；特别订单由订单模型单独计算")
                    continue
                op_direct += bonus
                if bonus:
                    detail["notes"].append(f"直接效率 +{bonus:.0f}%")

            if cleared_by_whisper:
                removed = op_direct + op_facility + op_global
                op_direct = 0.0
                op_facility = 0.0
                op_global = 0.0
                detail["cleared_efficiency_pct"] = removed
                detail["efficiency_cleared_by"] = "shamare_whisper"
                detail["notes"].append(
                    f"巫恋·低语清零该干员提供的订单效率 {removed:g}%；订单类型与心情效果保留"
                )

            direct_bonus += op_direct
            facility_bonus += op_facility
            global_bonus += op_global
            detail.update({
                "direct_bonus_pct": op_direct,
                "facility_bonus_pct": op_facility,
                "global_bonus_pct": op_global,
            })
            result["operator_details"].append(detail)

        if jaye_e1_operators and not whisper_active:
            teammate_efficiency = sum(
                max(0.0, float(detail.get("direct_bonus_pct", 0.0)))
                + max(0.0, float(detail.get("facility_bonus_pct", 0.0)))
                + max(0.0, float(detail.get("global_bonus_pct", 0.0)))
                for detail in result["operator_details"]
                if detail["name"] not in jaye_e1_operators
            )
            capacity_loss = math.floor(teammate_efficiency / 10.0)
            effective_order_limit = max(1, 10 + room_order_capacity - capacity_loss)
            value = effective_order_limit * 4.0
            global_bonus += value
            jaye_special_bonus += value
            special_flags.append("jaye_e1_combined_order_limit")
            for detail in result["operator_details"]:
                if detail["name"] in jaye_e1_operators:
                    detail["global_bonus_pct"] += value
                    detail["notes"].append(
                        f"队友技能效率 {teammate_efficiency:g}%：订单上限 "
                        f"10+{room_order_capacity}-{capacity_loss}={effective_order_limit}，"
                        f"两技能合计 +{value:g}%"
                    )

        if override_values:
            direct_bonus = max(override_values)
            result["warnings"].append("巫恋类替换技能仅替换直接订单效率层，设施与全局联动层继续保留。")

        if "glasgow_center" in control_tags:
            global_bonus += room_glasgow * 10
        if "siracusa_center" in control_tags:
            global_bonus += room_siracusa * 5
        if "karlan_full_trade_10" in control_tags and self.count_room_group("karlan_trade") >= 3:
            global_bonus += 10
        control_trade_bonus = float(control_effects.get("global_trading_order_efficiency_pct", 0.0))
        global_bonus += control_trade_bonus
        if control_trade_bonus:
            winners = "、".join(control_effect_sources.get("global_trading_order_efficiency_pct", []))
            result["warnings"].append(f"控制中枢全局贸易效率按同种效果取最高：+{control_trade_bonus:g}%（{winners}）")

        additive_before_amplifier = direct_bonus + facility_bonus + global_bonus
        generic_amplifier_count = sum(
            1
            for op in self.operators
            for skill in self._skills(op)
            if "amplifier_equal_additive" in skill.get("tags", [])
            and not any(tag.startswith("snowant_amplifier_cap_") for tag in skill.get("tags", []))
            and not (whisper_active and op["name"] not in whisper_operators)
        )
        amplifier_bonus = additive_before_amplifier * generic_amplifier_count
        snowant_input = max(
            0.0,
            additive_before_amplifier - (jaye_special_bonus if jaye_amplifier_exclusion else 0.0),
        )
        for cap in snowant_caps:
            amplifier_bonus += min(cap, snowant_input)
        if jaye_amplifier_exclusion and jaye_special_bonus and snowant_caps:
            result["warnings"].append("市井之道的动态效率不进入天道酬勤放大基数；其他技能仍按天道酬勤上限放大。")
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
            "order_capacity": 10 + room_order_capacity,
            "positive_order_capacity_increase": room_order_capacity_increase,
            "order_capacity_decrease": room_order_capacity_decrease,
            "jaye_e0_proxy_bonus_pct": (
                jaye_special_bonus
                if "孑" in room_names and not jaye_e1_operators and not whisper_active
                else 0.0
            ),
            "production_lines": production_lines,
            "intermediate_products": {
                "perception_information": self._global_perception_information(),
                "silent_resonance": self._global_silent_resonance(),
            },
        })
        return result

    def _compute_factory(self) -> dict[str, Any]:
        result = self._base_result()
        direct_bonus = 0.0
        facility_bonus = 0.0
        global_bonus = 0.0
        dongshi_values: list[float] = []
        time_profiles: list[dict[str, Any]] = []
        control_tags = self._global_control_tags()
        control_effects, control_effect_sources = self._resolved_control_effects()
        gladiia_rule = self._active_gladiia_rule()
        abyssal_factory_count = 0
        if gladiia_rule:
            abyssal_factory_count = self.count_global_group(
                str(gladiia_rule.get("target_group") or "abyssal_hunter"),
                str(gladiia_rule.get("count_facility") or "factory"),
            )
        room_names = {op["name"] for op in self.operators}
        work_platform_count = self.count_global_group("work_platform", "power_plant")
        automation_power_plant_count = self._automation_power_plant_count()
        automation_reset_names = {
            op["name"]
            for op in self.operators
            if any(
                any(str(tag).startswith("automation_reset_others_per_power_plant_") for tag in skill.get("tags", []))
                for skill in self._skills(op)
            )
        }
        if (
            "野鬃" in room_names
            and not automation_reset_names
            and any(
                "justice_wild_mane_factory_5" in skill.get("tags", [])
                for _, skill in self._global_power_skills()
            )
        ):
            global_bonus += 5.0
            result["warnings"].append("正义骑士号/“滴滴，启动！”：野鬃所在制造站生产力 +5%")
        bubble_active = any(
            "bubble_capacity_conversion" in skill.get("tags", [])
            for room_op in self.operators
            for skill in self._skills(room_op)
        )
        room_skill_names = {
            str(skill.get("skill_name") or "")
            for room_op in self.operators
            for skill in self._skills(room_op)
        }
        gladiia_room_bonus = 0.0
        if (
            gladiia_rule
            and abyssal_factory_count > 0
            and any(
                str(gladiia_rule.get("target_group") or "abyssal_hunter") in self._groups(op)
                for op in self.operators
            )
            and not room_skill_names.intersection(gladiia_rule.get("non_stacking_skill_names") or [])
        ):
            gladiia_room_bonus = min(
                float(
                    gladiia_rule.get(
                        "cap_pct_per_room",
                        gladiia_rule.get("cap_pct_per_operator", 0.0),
                    ) or 0.0
                ),
                abyssal_factory_count * float(gladiia_rule.get("bonus_pct_per_member", 0.0) or 0.0),
            )
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
            op_facility_count = 0.0
            op_global = 0.0
            reset_by_teammate = bool(automation_reset_names) and op["name"] not in automation_reset_names
            skills = self._skills(op)
            if not skills:
                detail["notes"].append("当前精英等级未解锁适配技能，或产品/技能数据不匹配")
            for skill in skills:
                tags = set(skill.get("tags", []))
                bonus = float(skill.get("base_bonus_pct", 0))
                if (
                    gladiia_room_bonus > 0
                    and str(skill.get("skill_name") or "")
                    in set(gladiia_rule.get("non_amplifying_skill_names") or [])
                ):
                    detail["notes"].append("集群狩猎优先生效，配合意识不叠加")
                    continue
                if "hourly_growth_15_to_25" in tags:
                    if reset_by_teammate:
                        detail["notes"].append("自动化·α使该干员的时变生产力归零")
                        continue
                    time_profiles.append({
                        "operator": op["name"],
                        "skill_name": skill.get("skill_name", ""),
                        "start_bonus_pct": 15.0,
                        "increment_pct_per_hour": 2.0,
                        "final_bonus_pct": 25.0,
                    })
                    detail["notes"].append("首小时 +15%，每小时 +2%，上限 +25%，按班次时长积分")
                    continue
                if "hourly_growth_20_to_25" in tags:
                    if reset_by_teammate:
                        detail["notes"].append("自动化·α使该干员的时变生产力归零")
                        continue
                    time_profiles.append({
                        "operator": op["name"],
                        "skill_name": skill.get("skill_name", ""),
                        "start_bonus_pct": 20.0,
                        "increment_pct_per_hour": 1.0,
                        "final_bonus_pct": 25.0,
                    })
                    detail["notes"].append("首小时 +20%，每小时 +1%，上限 +25%，按班次时长积分")
                    continue
                if "dongshi_reset" in tags:
                    if reset_by_teammate:
                        detail["notes"].append("自动化·α使该干员的生产力替换效果归零")
                        continue
                    value = len(self.operators) * 10
                    dongshi_values.append(value)
                    detail["notes"].append(f"直接生产力替换为站内 {len(self.operators)} 人 ×10% = +{value}%")
                    continue
                if "qingliu_per_trading_post" in tags:
                    value = self.trading_post_count * 20
                    op_facility += value
                    op_facility_count += value
                    detail["notes"].append(f"{self.trading_post_count} 个贸易站 ×20% = +{value}%")
                    continue
                automation_tags = [
                    tag for tag in tags
                    if str(tag).startswith("automation_reset_others_per_power_plant_")
                ]
                if automation_tags:
                    per_plant = max(float(str(tag).rsplit("_", 1)[1]) for tag in automation_tags)
                    value = automation_power_plant_count * per_plant
                    op_facility += value
                    op_facility_count += value
                    detail["notes"].append(
                        f"自动化读取 {automation_power_plant_count} 个发电站 ×{per_plant:g}% = +{value:g}%"
                    )
                    continue
                if "nasti_per_rhine" in tags:
                    value = min(self.count_global_group("rhine_lab"), 5) * 3
                    op_global += value
                    detail["notes"].append(f"莱茵生命全局计数：+{value}%")
                    continue
                if "dorothy_rhine_room" in tags:
                    value = self._room_skill_category_count("莱茵科技") * 5
                    op_global += value
                    detail["notes"].append(f"同站莱茵科技类技能：+{value}%")
                    continue
                if "cangtai_per_other_metalcraft" in tags:
                    value = self._room_skill_category_count("金属工艺") * 5
                    op_global += value
                    detail["notes"].append(f"同站金属工艺类技能：+{value}%")
                    continue
                if "factory_per_rhine_skill_5" in tags:
                    value = self._room_skill_category_count("莱茵科技") * 5
                    op_global += value
                    detail["notes"].append(f"同站莱茵科技类技能：+{value}%")
                    continue
                if "factory_per_metalcraft_skill_5" in tags:
                    value = self._room_skill_category_count("金属工艺") * 5
                    op_global += value
                    detail["notes"].append(f"同站金属工艺类技能：+{value}%")
                    continue
                if "factory_per_standardization_skill_5" in tags:
                    value = self._room_skill_category_count("标准化") * 5
                    op_global += value
                    detail["notes"].append(f"同站标准化类技能：+{value}%")
                    continue
                if "yinji_per_trading_post" in tags:
                    value = self.trading_post_count * 3
                    op_facility += value
                    op_facility_count += value
                    detail["notes"].append(f"{self.trading_post_count} 个贸易站 ×3% = +{value}%")
                    continue
                if "with_jiushen_battle_record_30" in tags:
                    value = 30 if self.product == "battle_record" and "酒神" in room_names else 0
                    op_global += value
                    detail["notes"].append(f"酒神同站且生产作战记录：+{value}%")
                    continue
                if "fen_per_a1" in tags:
                    value = self.count_room_group("a1") * 10
                    op_global += value
                    detail["notes"].append(f"同站 A1 成员 {self.count_room_group('a1')} 人：+{value}%")
                    continue
                if "factory_per_a1_skill_10" in tags:
                    value = self.count_room_group("a1") * 10
                    op_global += value
                    detail["notes"].append(f"同站 A1 小队干员：+{value}%")
                    continue
                if "work_platform_per_member_5" in tags:
                    value = work_platform_count * 5
                    op_facility += value
                    detail["notes"].append(f"实际进驻发电站的作业平台 {work_platform_count} 台：+{value}%")
                    continue
                if "work_platform_per_member_10" in tags:
                    value = work_platform_count * 10
                    op_facility += value
                    detail["notes"].append(f"实际进驻发电站的作业平台 {work_platform_count} 台：+{value}%")
                    continue
                if "factory_per_catnip_1" in tags:
                    catnip = self._global_catnip()
                    op_direct += bonus
                    op_global += catnip
                    detail["notes"].append(f"基础 +{bonus:g}%；木天蓼 {catnip:g}：生产力 +{catnip:g}%")
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
                if "redcloud_capacity_conversion_2" in tags:
                    if bubble_active:
                        detail["notes"].append("大就是好！优先生效，回收利用按特殊叠加规则归零")
                    else:
                        value = sum(max(0.0, capacity) for capacity in capacity_by_operator.values()) * 2
                        op_global += value
                        detail["notes"].append(f"最终仓库容量增量 ×2%：+{value:.0f}%")
                    continue
                if "training_room_level_10_cap_30" in tags:
                    value = min(30, self.training_room_level * 10)
                    op_facility += value
                    op_facility_count += value
                    detail["notes"].append(f"训练室 {self.training_room_level} 级 ×10% = +{value}%")
                    continue
                if "dorm_level_sum_gold_1" in tags:
                    value = sum(self.dormitory_levels) if self.product == "pure_gold" else 0
                    op_facility += value
                    op_facility_count += value
                    detail["notes"].append(f"宿舍等级总和 {sum(self.dormitory_levels)}：贵金属 +{value:g}%")
                    continue
                if "factory_per_3_human_fireworks_1" in tags:
                    fireworks = self._global_human_fireworks()
                    value = math.floor(fireworks / 3.0)
                    op_global += value
                    detail["notes"].append(f"人间烟火 {fireworks:g}：生产力 +{value:g}%")
                    continue
                if "factory_per_witchcraft_1" in tags or "factory_per_witchcraft_2" in tags:
                    per_crystal = 2.0 if "factory_per_witchcraft_2" in tags else 1.0
                    crystals = self._global_witchcraft_crystal()
                    value = crystals * per_crystal
                    op_global += value
                    detail["notes"].append(f"巫术结晶 {crystals:g} ×{per_crystal:g}% = +{value:g}%")
                    continue
                if "factory_per_thought_chain_2_1" in tags or "factory_per_thought_chain_1_1" in tags:
                    step = 1.0 if "factory_per_thought_chain_1_1" in tags else 2.0
                    chain = self._global_thought_chain()
                    value = math.floor(chain / step)
                    op_global += value
                    detail["notes"].append(f"思维链环 {chain:g}，每{step:g}点+1%：+{value:g}%")
                    continue
                if "factory_per_monster_cooking_1" in tags:
                    value = self._global_monster_cooking()
                    op_global += value
                    detail["notes"].append(f"魔物料理 {value:g}：生产力 +{value:g}%")
                    continue
                if "factory_per_engineering_robot_8_5" in tags or "factory_per_engineering_robot_16_5" in tags:
                    step = 8.0 if "factory_per_engineering_robot_8_5" in tags else 16.0
                    robots = self._global_engineering_robots()
                    value = math.floor(robots / step) * 5.0
                    op_global += value
                    detail["notes"].append(f"工程机器人 {robots:g}，每{step:g}个+5%：+{value:g}%")
                    continue
                if "factory_gold_per_trading_post_3" in tags:
                    value = self.trading_post_count * 3.0 if self.product == "pure_gold" else 0.0
                    op_facility += value
                    op_facility_count += value
                    detail["notes"].append(f"贸易站数量联动：+{value:g}%")
                    continue
                op_direct += bonus
                if bonus:
                    detail["notes"].append(f"直接生产力 +{bonus:.0f}%")

            if reset_by_teammate:
                removed = op_direct + op_global + max(0.0, op_facility - op_facility_count)
                op_direct = 0.0
                op_global = 0.0
                op_facility = op_facility_count
                detail["notes"].append(
                    f"自动化·α清除队友提供的非设施数量生产力 {removed:g}%，"
                    f"保留设施数量生产力 {op_facility_count:g}%"
                )

            direct_bonus += op_direct
            facility_bonus += op_facility
            global_bonus += op_global
            detail.update({
                "direct_bonus_pct": op_direct,
                "facility_bonus_pct": op_facility,
                "facility_count_bonus_pct": op_facility_count,
                "global_bonus_pct": op_global,
            })
            result["operator_details"].append(detail)

        if gladiia_room_bonus:
            global_bonus += gladiia_room_bonus
            result["warnings"].append(
                f"集群狩猎：全局制造站深海猎人 {abyssal_factory_count} 人，当前制造站 +{gladiia_room_bonus:g}%"
            )

        if dongshi_values:
            direct_bonus = max(dongshi_values)
            result["warnings"].append("冬时类技能仅替换直接生产力层，设施与全局联动层继续保留。")

        control_factory_bonus = float(control_effects.get("global_factory_productivity_pct", 0.0))
        global_bonus += control_factory_bonus
        if control_factory_bonus:
            winners = "、".join(control_effect_sources.get("global_factory_productivity_pct", []))
            result["warnings"].append(f"控制中枢全局制造效率按同种效果取最高：+{control_factory_bonus:g}%（{winners}）")
        if self.product == "pure_gold" and "ave_gold_base_1_per_20_heat_1" in control_tags:
            heat = self._global_control_heat()
            value = 1 + int(heat // 20)
            global_bonus += value
            result["warnings"].append(f"Ave Mujica热情值按满员宿舍代理为 {heat:.0f}，赤金 +{value}%")
        red_pine_count = self.count_room_group("red_pine")
        if "red_pine_factory_record_10_gold_minus_10" in control_tags and red_pine_count:
            value = red_pine_count * (10.0 if self.product == "battle_record" else -10.0 if self.product == "pure_gold" else 0.0)
            global_bonus += value
            result["warnings"].append(f"红松的骑士：当前站红松骑士团 {red_pine_count} 人，生产力 {value:+g}%")
        knight_count = self.count_room_group("knight")
        if "knight_factory_productivity_7" in control_tags and knight_count:
            value = knight_count * 7.0
            global_bonus += value
            result["warnings"].append(f"烛骑士微光：当前站骑士 {knight_count} 人，生产力 +{value:g}%")

        static_bonus = direct_bonus + facility_bonus + global_bonus
        final_time_bonus = sum(float(item["final_bonus_pct"]) for item in time_profiles)
        paper_bonus = static_bonus + final_time_bonus
        result.update({
            "layers": {
                "direct_bonus_pct": round(direct_bonus, 3),
                "facility_bonus_pct": round(facility_bonus, 3),
                "global_bonus_pct": round(global_bonus, 3),
                "time_dependent_final_bonus_pct": round(final_time_bonus, 3),
                "multiplier": 1.0,
            },
            "paper_bonus_pct": round(paper_bonus, 3),
            "estimated_efficiency_bonus_pct": round(paper_bonus, 3),
            "time_dependent_static_bonus_pct": round(static_bonus, 3),
            "time_dependent_bonus_profiles": time_profiles,
            "fixed_order_value_lmd_per_trigger": 0,
            "special_flags": sorted(
                ({"dongshi_reset"} if dongshi_values else set())
                | ({"automation_reset_others"} if automation_reset_names else set())
            ),
            "intermediate_products": {
                "human_fireworks": self._global_human_fireworks(),
                "witchcraft_crystal": self._global_witchcraft_crystal(),
                "perception_information": self._global_perception_information(),
                "thought_chain": self._global_thought_chain(),
                "silent_resonance": self._global_silent_resonance(),
                "monster_cooking": self._global_monster_cooking(),
                "engineering_robot": self._global_engineering_robots(),
            },
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
                mechanism_bonus = self._mechanism_bonus_pct(skill)
                if base:
                    drone_bonus += base
                    detail["notes"].append(f"无人机恢复 +{base:.0f}%")
                if mechanism_bonus:
                    drone_bonus += mechanism_bonus
                    detail["notes"].append(f"动态机制计算：无人机恢复 +{mechanism_bonus:.0f}%")
                if "muelsyse_drone_per_rhine" in tags:
                    value = self.count_global_group("rhine_lab") * 3
                    drone_bonus += value
                    detail["notes"].append(f"莱茵生命全局计数：无人机恢复 +{value}%")
                if "power_per_dorm_level_sum_0.5" in tags:
                    value = sum(self.dormitory_levels) * 0.5
                    drone_bonus += value
                    detail["notes"].append(f"宿舍等级总和 {sum(self.dormitory_levels)} ×0.5%：无人机恢复 +{value:g}%")
                if "red_pine_power" in tags:
                    flags.append("red_pine_power")
                    detail["notes"].append("红松骑士团能源联动已记录")
                if "power_with_kaltsit_control_5" in tags:
                    active = any(
                        other["name"] == "凯尔希"
                        and other.get("assigned_facility") == "control_center"
                        for other in self.global_operators
                    )
                    value = 5 if active else 0
                    drone_bonus += value
                    detail["notes"].append(f"凯尔希进驻控制中枢：无人机恢复 +{value}%")
                if "power_with_other_work_platform_5" in tags:
                    others = sum(
                        1
                        for other in self.global_operators
                        if other["name"] != op["name"]
                        and other.get("assigned_facility") == "power_plant"
                        and "work_platform" in self._groups(other)
                    )
                    value = 5 if others > 0 else 0
                    drone_bonus += value
                    detail["notes"].append(f"其他作业平台进驻发电站 {others} 台：无人机恢复 +{value}%")
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
        control_effects, control_effect_sources = self._resolved_control_effects()
        proxy = float(control_effects.get("global_trading_order_efficiency_pct", 0.0))
        proxy += float(control_effects.get("global_factory_productivity_pct", 0.0))
        if "all_factory_bonus_2" in flag_set:
            # Legacy records without structured effects are handled by the
            # resolver. Keeping the flag in output preserves diagnostics.
            pass
        heat = self._global_control_heat()
        if "ave_gold_base_1_per_20_heat_1" in flag_set:
            proxy += 1 + int(heat // 20)
        result.update({
            "layers":{"direct_bonus_pct":proxy,"facility_bonus_pct":0,"global_bonus_pct":0,"multiplier":1.0},
            "paper_bonus_pct":proxy,
            "estimated_efficiency_bonus_pct":proxy,
            "fixed_order_value_lmd_per_trigger":0,
            "special_flags":sorted(flag_set),
            "resolved_effect_sources": control_effect_sources,
            "resolved_effect_values": control_effects,
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
        f"进驻基础加成：+{result.get('staffing_base_bonus_pct', 0):.1f}%",
        f"直接加成：+{layers.get('direct_bonus_pct', 0):.1f}%",
        f"设施加成：+{layers.get('facility_bonus_pct', 0):.1f}%",
        f"全局加成：+{layers.get('global_bonus_pct', 0):.1f}%",
    ])
    if layers.get("time_dependent_final_bonus_pct"):
        lines.append(f"时间递增加成（最终）：+{layers['time_dependent_final_bonus_pct']:.1f}%")
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
