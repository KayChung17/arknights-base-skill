#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Enumerate and score legal single-room operator combinations.

This stage deliberately stops at room-local combinations. The global MILP
solver selects combinations across every room and segment while enforcing
operator exclusivity and work-hour constraints.
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any

from data_loader import load_operator_data, operator_index, select_available_skills
from drone_model import drone_rules, natural_lmd_metrics_per_hour, special_order_resolution
from efficiency_calculator import EfficiencyCalculator, production_bonus_for_duration
from optimizer_common import (
    context_rooms,
    context_roster,
    eligible_operators,
    factory_base_metrics,
    metric_score,
    objective_profile,
    read_json,
    stable_id,
    trading_base_metrics,
    utc_now,
    warehouse_capacity,
    write_json,
)

SYNERGY_BUNDLE_PATH = Path(__file__).resolve().parents[1] / "assets" / "synergy-bundles.json"


def load_synergy_bundles() -> list[dict[str, Any]]:
    try:
        value = json.loads(SYNERGY_BUNDLE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [
        item for item in value.get("bundles", [])
        if isinstance(item, dict) and item.get("confidence") == "verified_formula"
    ]


def _bundle_facility_spec(bundle: dict[str, Any], facility: str) -> dict[str, Any]:
    value = (bundle.get("placements") or {}).get(facility) or {}
    return value if isinstance(value, dict) else {}


def _bundle_operator_names(bundle: dict[str, Any], facility: str) -> set[str]:
    spec = _bundle_facility_spec(bundle, facility)
    names = {str(name) for name in spec.get("all_of", [])}
    names.update(str(name) for name in (spec.get("one_of") or []) if isinstance(name, str))
    return names


def _bundle_ids_for_selection(facility: str, selected_names: set[str]) -> list[str]:
    result: list[str] = []
    index = operator_index()
    for bundle in load_synergy_bundles():
        spec = _bundle_facility_spec(bundle, facility)
        explicit = _bundle_operator_names(bundle, facility)
        group = str(spec.get("group") or "")
        group_selected = any(
            group and group in set(index.get(name, {}).get("groups") or [])
            for name in selected_names
        )
        if explicit.intersection(selected_names) or group_selected:
            result.append(str(bundle.get("id") or ""))
    return sorted(item for item in result if item)




def _compute_efficiency(
    facility: str,
    product: str,
    operators: list[dict[str, Any]],
    trading_count: int,
    power_count: int,
    drone_capacity: float = 235.0,
    facility_level: int = 1,
    dormitory_levels: list[int] | None = None,
    training_room_level: int = 3,
    reception_room_level: int = 3,
    facility_level_sum: int = 0,
) -> dict[str, Any]:
    if facility not in {"trading_post", "factory", "power_plant", "control_center"}:
        return {"error": f"calculator_unsupported:{facility}", "warnings": []}
    assigned = [dict(op, assigned_facility=facility) for op in operators]
    calculator = EfficiencyCalculator(
        facility,
        assigned,
        product,
        trading_post_count=trading_count,
        power_plant_count=power_count,
        drone_capacity=drone_capacity,
        facility_level=facility_level,
        training_room_level=training_room_level,
        reception_room_level=reception_room_level,
        dormitory_levels=dormitory_levels,
        global_operators=assigned,
        facility_level_sum=facility_level_sum,
    )
    result = calculator.compute()
    result["morale_cost_rates"] = calculator.morale_cost_rates()
    return result


def recompute_combo_with_global_operators(
    context: dict[str, Any],
    room: dict[str, Any],
    combo: dict[str, Any],
    global_operators: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recalculate one room combination under an explicit simultaneous base state."""
    facility = str(room.get("facility_id") or combo.get("facility_id") or "")
    product = str(combo.get("product_id") or room.get("product_id") or "")
    operators = [
        dict(item, assigned_facility=facility, assigned_room_id=str(room.get("room_id") or combo.get("room_id") or ""))
        for item in (combo.get("operators") or [])
    ]
    rooms = context_rooms(context)
    base_state = context.get("base_state") or {}
    right_levels = base_state.get("right_side_levels") or {}
    dormitory_levels = list(base_state.get("dormitory_levels") or [1, 1, 1, 1])
    facility_level_sum = (
        sum(int(value.get("level", 0) or 0) for value in rooms.values())
        + sum(int(value or 0) for value in dormitory_levels)
        + sum(int(value or 0) for value in right_levels.values())
    )
    calculator = EfficiencyCalculator(
        facility,
        operators,
        product,
        trading_post_count=sum(1 for value in rooms.values() if value.get("facility_id") == "trading_post"),
        power_plant_count=sum(1 for value in rooms.values() if value.get("facility_id") == "power_plant"),
        drone_capacity=float(
            (((context.get("objective") or {}).get("preferences") or {}).get("solver") or {}).get("drone_capacity", 235.0)
        ),
        facility_level=int(room.get("level", combo.get("level", 1)) or 1),
        training_room_level=int(right_levels.get("training_room", 3) or 3),
        reception_room_level=int(right_levels.get("reception_room", 3) or 3),
        dormitory_levels=dormitory_levels,
        global_operators=global_operators,
        facility_level_sum=facility_level_sum,
    )
    result = calculator.compute()
    result["morale_cost_rates"] = calculator.morale_cost_rates()
    metrics, fixed = _metrics_per_hour(room, result, operators)
    return {
        **combo,
        "metrics_per_hour": metrics,
        "fixed_metrics": fixed,
        "morale_cost_rates": {
            name: float(rate)
            for name, rate in (result.get("morale_cost_rates") or {}).items()
        },
        "efficiency_result": result,
    }

def _effective_bonus(result: dict[str, Any]) -> float:
    for key in ("estimated_efficiency_bonus_pct", "effective_efficiency_bonus_pct", "paper_bonus_pct"):
        if result.get(key) is not None:
            return float(result[key])
    layers = result.get("layers") or {}
    return float(layers.get("direct_bonus_pct", 0)) + float(layers.get("facility_bonus_pct", 0))


def _external_bonus(operators: list[dict[str, Any]], facility: str, product: str) -> float:
    value = 0.0
    for op in operators:
        evidence = op.get("external_evidence") or {}
        if evidence.get("facility_id") != facility or evidence.get("product_id") != product:
            continue
        structured = evidence.get("structured_rule") or evidence
        try:
            value += float(structured.get("base_bonus_pct", 0))
        except (TypeError, ValueError):
            pass
    return value


def _metrics_per_hour(
    room: dict[str, Any],
    result: dict[str, Any],
    operators: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, float]]:
    facility = room["facility_id"]
    product = room["product_id"]
    if not operators and facility in {"factory", "trading_post", "power_plant", "control_center"}:
        return {}, {"fixed_lmd_per_trigger": 0.0}
    # Room libraries score the standard 8-hour segment. The simulator
    # re-evaluates time-dependent profiles at each actual segment duration.
    bonus = production_bonus_for_duration(result, 8.0) + _external_bonus(operators, facility, product)
    if result.get("error"):
        index = operator_index()
        bonus += sum(
            float(skill.get("base_bonus_pct", 0) or 0)
            for op in operators
            for skill in select_available_skills(
                index.get(op["name"], {}), facility, int(op.get("elite", 0)), product, int(op.get("level", 90) or 90)
            )
        )
    multiplier = max(0.0, 1.0 + bonus / 100.0)
    if facility == "factory":
        base = factory_base_metrics(product)
    elif facility == "trading_post" and product == "lmd_order":
        combo_view = {"operators": operators}
        return natural_lmd_metrics_per_hour(int(room.get("level", 1)), combo_view, bonus), {
            "fixed_lmd_per_trigger": float(result.get("fixed_order_value_lmd_per_trigger", 0) or 0),
        }
    elif facility == "trading_post":
        base = trading_base_metrics(product)
    else:
        base = factory_base_metrics(product)
    metrics = {key: value * multiplier for key, value in base.items()}
    if facility == "factory" and product == "orundum_shard":
        units = float(metrics.get("orundum_shard", 0.0))
        costs = (drone_rules().get("factory_costs_per_unit") or {}).get(product) or {}
        for key, value in costs.items():
            metrics[str(key)] = units * float(value)
    fixed = {
        "fixed_lmd_per_trigger": float(result.get("fixed_order_value_lmd_per_trigger", 0) or 0),
    }
    return metrics, fixed


def _effect_resolution(
    facility: str,
    product: str,
    operators: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    details = {
        str(item.get("name") or ""): item
        for item in (result.get("operator_details") or [])
    }
    order = (
        special_order_resolution({"operators": operators})
        if facility == "trading_post" and product == "lmd_order"
        else {"active": [], "suppressed": [], "has_suppressed_high_value_effect": False}
    )
    suppressed_by_name: dict[str, list[str]] = {}
    for item in order.get("suppressed") or []:
        suppressed_by_name.setdefault(str(item.get("operator") or ""), []).append(str(item.get("effect") or ""))
    operator_effects: list[dict[str, Any]] = []
    for operator in operators:
        name = str(operator.get("name") or "")
        detail = details.get(name) or {}
        suppressed = suppressed_by_name.get(name, [])
        operator_effects.append({
            "operator": name,
            "efficiency_contribution_pct": sum(
                float(detail.get(key, 0.0) or 0.0)
                for key in ("direct_bonus_pct", "facility_bonus_pct", "global_bonus_pct")
            ),
            "cleared_efficiency_pct": float(detail.get("cleared_efficiency_pct", 0.0) or 0.0),
            "efficiency_cleared_by": detail.get("efficiency_cleared_by"),
            "active_special_order_effects": [
                str(item.get("effect") or "")
                for item in (order.get("active") or [])
                if str(item.get("operator") or "") == name
            ],
            "suppressed_special_order_effects": suppressed,
            "fungible_support_slot": bool(
                "shamare_whisper_reset" in (result.get("special_flags") or []) and name != "巫恋"
            ),
            "opportunity_risk": bool(suppressed),
        })
    return {
        "special_order": order,
        "operators": operator_effects,
        "opportunity_risk_operators": sorted(
            item["operator"] for item in operator_effects if item["opportunity_risk"]
        ),
    }


def _known_dominated_lmd_crew(operator_names: set[str]) -> str | None:
    """Reject trade crews whose special-order skills overwrite or fail to chain."""

    if "U-Official" in operator_names and "但书" in operator_names:
        return "u_official_overrides_proviso"
    if "可露希尔" in operator_names and "龙舌兰" in operator_names:
        return "closure_fixed_order_disables_tequila"
    if "但书" in operator_names and "龙舌兰" in operator_names:
        return "proviso_orders_do_not_trigger_tequila"
    return None


def _cross_facility_proxy_score(context: dict[str, Any], result: dict[str, Any]) -> float:
    """Value structured control-center effects against downstream base rates."""
    effects = result.get("resolved_effect_values") or {}
    trade_pct = float(effects.get("global_trading_order_efficiency_pct", 0.0) or 0.0)
    factory_pct = float(effects.get("global_factory_productivity_pct", 0.0) or 0.0)
    if not trade_pct and not factory_pct:
        return 0.0
    weights = objective_profile(context)
    score = 0.0
    for room in context_rooms(context).values():
        facility = str(room.get("facility_id") or "")
        products = room.get("product_options") or [room.get("product_id")]
        if facility == "trading_post" and trade_pct:
            base_scores = [metric_score(trading_base_metrics(str(product)), weights) for product in products]
            score += max(base_scores or [0.0]) * trade_pct / 100.0
        elif facility == "factory" and factory_pct:
            base_scores = [metric_score(factory_base_metrics(str(product)), weights) for product in products]
            score += max(base_scores or [0.0]) * factory_pct / 100.0
    return score


def build_room_combinations(
    context: dict[str, Any],
    room: dict[str, Any],
    *,
    top_k: int = 60,
    operator_pool_size: int = 14,
    allow_partial: bool = False,
    minimum_staffed_slots: int = 0,
) -> dict[str, Any]:
    roster = context_roster(context)
    facility = room["facility_id"]
    product = room["product_id"]
    capacity = int(room["capacity"])
    eligible = eligible_operators(context, facility, product)
    trading_count = sum(1 for value in context_rooms(context).values() if value["facility_id"] == "trading_post")
    power_count = sum(1 for value in context_rooms(context).values() if value["facility_id"] == "power_plant")
    drone_capacity = float(
        (((context.get("objective") or {}).get("preferences") or {}).get("solver") or {}).get("drone_capacity", 235.0)
    )
    dormitory_levels = list((context.get("base_state") or {}).get("dormitory_levels") or [1, 1, 1, 1])
    training_room_level = int(
        (((context.get("base_state") or {}).get("right_side_levels") or {}).get("training_room", 3))
    )
    reception_room_level = int(
        (((context.get("base_state") or {}).get("right_side_levels") or {}).get("reception_room", 3))
    )
    base_state = context.get("base_state") or {}
    right_levels = base_state.get("right_side_levels") or {}
    facility_level_sum = (
        sum(int(value.get("level", 0) or 0) for value in context_rooms(context).values())
        + sum(int(value or 0) for value in dormitory_levels)
        + sum(int(value or 0) for value in right_levels.values())
    )

    individual_records: list[dict[str, Any]] = []
    for op in eligible:
        result = _compute_efficiency(
            facility, product, [op], trading_count, power_count, drone_capacity,
            int(room.get("level", 1)), dormitory_levels,
            training_room_level, reception_room_level,
            facility_level_sum,
        )
        metrics, fixed = _metrics_per_hour(room, result, [op])
        score = metric_score(metrics, objective_profile(context)) + fixed["fixed_lmd_per_trigger"] * 0.001
        individual_records.append({"score": score, "operator": op, "metrics": metrics})
    individual_records.sort(key=lambda item: (-float(item["score"]), item["operator"]["name"]))
    index = operator_index()
    bundle_preserved_names: set[str] = set()
    for bundle in load_synergy_bundles():
        spec = _bundle_facility_spec(bundle, facility)
        required = _bundle_operator_names(bundle, facility)
        group = str(spec.get("group") or "")
        for item in individual_records:
            name = str(item["operator"].get("name") or "")
            if name in required or (group and group in set(index.get(name, {}).get("groups") or [])):
                bundle_preserved_names.add(name)
    if capacity == 1:
        # Single-slot rooms are cheap to enumerate; retain every verified
        # operator so special-order workers such as Proviso/Closure are never
        # removed by individual-score pruning.
        pool = [item["operator"] for item in individual_records]
    else:
        # A weighted objective can rank a required resource producer poorly
        # because its input cost is also represented. Preserve resource-flow
        # extrema before truncating the operator pool; otherwise a shard or
        # pure-gold specialist can disappear and make the global model falsely
        # infeasible.
        pool_records = list(individual_records[: max(capacity, operator_pool_size)])
        pool_names = {item["operator"]["name"] for item in pool_records}
        for item in individual_records:
            op = item["operator"]
            skills = select_available_skills(
                index.get(op["name"], {}), facility, int(op.get("elite", 0)),
                product, int(op.get("level", 90) or 90),
            )
            if any(
                any(str(tag).startswith("trade_per_silent_resonance_") for tag in skill.get("tags", []))
                for skill in skills
            ) or op["name"] in bundle_preserved_names:
                if op["name"] in pool_names:
                    continue
                pool_records.append(item)
                pool_names.add(op["name"])
        metric_keys = sorted({key for item in individual_records for key in item["metrics"]})
        preserve_per_metric = max(capacity * 2, 4)
        for key in metric_keys:
            ranked = sorted(
                individual_records,
                key=lambda item: (-float(item["metrics"].get(key, 0.0)), item["operator"]["name"]),
            )
            for item in ranked[:preserve_per_metric]:
                name = item["operator"]["name"]
                if name not in pool_names:
                    pool_records.append(item)
                    pool_names.add(name)
        pool = [item["operator"] for item in pool_records]

    support_facilities = {"power_plant", "office", "dormitory"}
    if allow_partial:
        maximum = min(capacity, len(pool))
        # Production and support facilities continue their base function while
        # unstaffed. Keep the zero-staff option so the global model can trade
        # room speed against resource balance and morale.
        minimum = max(0, min(int(minimum_staffed_slots), maximum))
        sizes = list(range(minimum, maximum + 1))
    elif facility in support_facilities:
        maximum = min(capacity, len(pool))
        sizes = list(range(1, maximum + 1)) if maximum else [0]
    else:
        sizes = [capacity] if len(pool) >= capacity else []

    combinations: list[dict[str, Any]] = []
    enumerated = 0
    for size in sizes:
        for selected_tuple in itertools.combinations(pool, size):
            enumerated += 1
            selected = [dict(op) for op in selected_tuple]
            selected_names = {str(op.get("name") or "") for op in selected}
            if (
                facility == "trading_post"
                and product == "lmd_order"
                and _known_dominated_lmd_crew(selected_names) is not None
            ):
                continue
            result = _compute_efficiency(
                facility, product, selected, trading_count, power_count, drone_capacity,
                int(room.get("level", 1)), dormitory_levels,
                training_room_level, reception_room_level,
                facility_level_sum,
            )
            metrics, fixed = _metrics_per_hour(room, result, selected)
            effect_resolution = _effect_resolution(facility, product, selected, result)
            score = metric_score(metrics, objective_profile(context))
            score += fixed["fixed_lmd_per_trigger"] * objective_profile(context).get("fixed_lmd", 0)
            cross_facility_proxy = (
                _cross_facility_proxy_score(context, result)
                if facility == "control_center" else 0.0
            )
            score += cross_facility_proxy
            source_quality = 1.0 if all(op.get("skill_source") == "local_versioned_data" for op in selected) else 0.9
            score *= source_quality
            payload = {
                "room_id": room["room_id"],
                "operators": [op["name"] for op in selected],
                "facility_id": facility,
                "product_id": product,
            }
            combinations.append(
                {
                    "combination_id": stable_id("combo", payload),
                    "room_id": room["room_id"],
                    "facility_id": facility,
                    "product_id": product,
                    "synergy_bundle_ids": _bundle_ids_for_selection(
                        facility, {op["name"] for op in selected},
                    ),
                    "level": room["level"],
                    "capacity": capacity,
                    "staffed_slots": size,
                    "operators": [
                        {
                            "name": op["name"],
                            "elite": int(op["elite"]),
                            "level": int(op.get("level", 1)),
                            "skill_source": op.get("skill_source"),
                        }
                        for op in selected
                    ],
                    "proxy_score_per_hour": score,
                    "cross_facility_proxy_score_per_hour": cross_facility_proxy,
                    "metrics_per_hour": metrics,
                    "fixed_metrics": fixed,
                    "warehouse_capacity": warehouse_capacity(room, selected),
                    "morale_cost_rates": {
                        name: float(rate)
                        for name, rate in (result.get("morale_cost_rates") or {}).items()
                    },
                    "efficiency_result": result,
                    "effect_resolution": effect_resolution,
                    "warnings": list(result.get("warnings") or []),
                    "source_quality": source_quality,
                }
            )

    combinations.sort(
        key=lambda item: (
            -float(item["proxy_score_per_hour"]),
            tuple(op["name"] for op in item["operators"]),
        )
    )

    # Preserve high-quality combinations and add a deterministic diversity
    # tail. The former O(N*K) greedy scan became expensive for five-person
    # control-center rooms. This version guarantees operator coverage and
    # core-operator exclusion, then samples the full score range.
    kept = list(combinations[:top_k])
    selected_ids = {item["combination_id"] for item in kept}
    bundle_pattern_count = sum(
        max(1, len((_bundle_facility_spec(bundle, facility).get("one_of") or [])))
        for bundle in load_synergy_bundles()
        if _bundle_facility_spec(bundle, facility)
    )
    target = min(len(combinations), max(top_k, top_k * 3, top_k + bundle_pattern_count + 4))

    def add_item(item: dict[str, Any] | None) -> None:
        if item is None or len(kept) >= target:
            return
        combo_id = item["combination_id"]
        if combo_id not in selected_ids:
            kept.append(item)
            selected_ids.add(combo_id)

    if combinations and len(kept) < target:
        # Preserve the unstaffed/base-speed option and resource-flow extrema.
        # These candidates can be essential for LMD, pure-gold, and shard
        # balance even when their weighted proxy score is low.
        add_item(next((item for item in combinations if int(item.get("staffed_slots", 0)) == 0), None))
        # Preserve equal-yield substitutes for workers whose high-value order
        # effect is suppressed. This gives the global post-solve pass a way to
        # release them for another room instead of losing the alternative to
        # deterministic name ordering or top-k truncation.
        for anchor in list(kept[:top_k]):
            risk_names = set((anchor.get("effect_resolution") or {}).get("opportunity_risk_operators") or [])
            if not risk_names:
                continue
            active_effects = {
                str(item.get("effect") or "")
                for item in (((anchor.get("effect_resolution") or {}).get("special_order") or {}).get("active") or [])
            }
            for risk_name in sorted(risk_names):
                add_item(next((
                    item for item in combinations
                    if risk_name not in {op["name"] for op in item.get("operators") or []}
                    and {
                        str(effect.get("effect") or "")
                        for effect in ((((item.get("effect_resolution") or {}).get("special_order") or {}).get("active") or []))
                    } == active_effects
                    and abs(float(item.get("proxy_score_per_hour", 0.0)) - float(anchor.get("proxy_score_per_hour", 0.0))) <= 1e-9
                ), None))
        for bundle in load_synergy_bundles():
            spec = _bundle_facility_spec(bundle, facility)
            required = {str(name) for name in spec.get("all_of", [])}
            alternatives = [str(name) for name in (spec.get("one_of") or [])]
            targets = [required | {name} for name in alternatives] if alternatives else [required]
            for target_names in targets:
                if not target_names:
                    continue
                add_item(next((
                    item for item in combinations
                    if target_names.issubset({op["name"] for op in item["operators"]})
                ), None))
        metric_keys = {
            key
            for item in combinations
            for key in (item.get("metrics_per_hour") or {})
        }
        for key in sorted(metric_keys):
            add_item(max(combinations, key=lambda item: float((item.get("metrics_per_hour") or {}).get(key, 0.0))))
            add_item(min(combinations, key=lambda item: float((item.get("metrics_per_hour") or {}).get(key, 0.0))))
        if facility == "trading_post" and product == "lmd_order":
            tailoring_names = {"巫恋", "柏喙", "卡夫卡", "贝娜", "明椒", "折光"}
            for minimum_count in (1, 2, 3):
                add_item(next((
                    item for item in combinations
                    if len(tailoring_names.intersection(
                        {op["name"] for op in item["operators"]}
                    )) >= minimum_count
                ), None))
        pool_names = [op["name"] for op in pool]
        # Best combination containing each operator.
        for name in pool_names:
            add_item(next((item for item in combinations if name in {op["name"] for op in item["operators"]}), None))
        # Best combination excluding each operator. This prevents one high-score
        # core operator from becoming mandatory in every retained candidate.
        for name in pool_names:
            add_item(next((item for item in combinations if name not in {op["name"] for op in item["operators"]}), None))
        # Score-range stratification reaches low-overlap alternatives even when
        # many combinations tie on the proxy objective.
        remaining = target - len(kept)
        if remaining > 0:
            step = max(1, len(combinations) // remaining)
            for index in range(0, len(combinations), step):
                add_item(combinations[index])
                if len(kept) >= target:
                    break
        # Final deterministic fill.
        if len(kept) < target:
            for item in combinations:
                add_item(item)
                if len(kept) >= target:
                    break

    return {
        "room": room,
        "eligible_operator_count": len(eligible),
        "operator_pool": [op["name"] for op in pool],
        "preserved_synergy_operators": sorted(bundle_preserved_names.intersection(
            {op["name"] for op in pool}
        )),
        "enumerated_count": enumerated,
        "kept_count": len(kept),
        "quality_top_k": min(top_k, len(combinations)),
        "diversity_tail_count": max(0, len(kept) - min(top_k, len(combinations))),
        "truncated": len(combinations) > len(kept) or len(eligible) > len(pool),
        "combinations": kept,
    }


def build_library(
    context: dict[str, Any],
    *,
    top_k: int = 60,
    operator_pool_size: int = 14,
    allow_partial: bool = False,
    minimum_staffed_slots_by_facility: dict[str, int] | None = None,
) -> dict[str, Any]:
    minimum_staffed_slots_by_facility = minimum_staffed_slots_by_facility or {}
    room_results: dict[str, Any] = {}
    for room_id, room in context_rooms(context).items():
        product_options = list(dict.fromkeys(
            str(value)
            for value in (room.get("product_options") or [room.get("product_id")])
            if value
        ))
        product_results = []
        for product_id in product_options:
            product_room = dict(room, product_id=product_id)
            product_results.append(build_room_combinations(
                context,
                product_room,
                top_k=top_k,
                operator_pool_size=operator_pool_size,
                allow_partial=allow_partial,
                minimum_staffed_slots=int(minimum_staffed_slots_by_facility.get(str(room.get("facility_id") or ""), 0)),
            ))
        if len(product_results) == 1:
            room_results[room_id] = product_results[0]
        else:
            combinations = [combo for value in product_results for combo in value.get("combinations", [])]
            combinations.sort(key=lambda item: (-float(item.get("proxy_score_per_hour", 0.0)), item["combination_id"]))
            room_results[room_id] = {
                "room": dict(room, product_options=product_options),
                "combinations": combinations,
                "enumerated_count": sum(int(value.get("enumerated_count", 0)) for value in product_results),
                "kept_count": len(combinations),
                "truncated": any(bool(value.get("truncated")) for value in product_results),
                "product_results": {
                    product_id: {
                        "enumerated_count": value.get("enumerated_count"),
                        "kept_count": value.get("kept_count"),
                        "truncated": value.get("truncated"),
                    }
                    for product_id, value in zip(product_options, product_results)
                },
            }
        if not room_results[room_id]["combinations"]:
            raise ValueError(f"房间 {room_id} 没有可行组合；请补充技能数据或外部证据")
    return {
        "schema_version": 1,
        "library_type": "room_combination_library",
        "generated_at": utc_now(),
        "data_version": load_operator_data().get("data_version"),
        "parameters": {
            "top_k_per_room": top_k,
            "operator_pool_size": operator_pool_size,
            "allow_partial": allow_partial,
            "minimum_staffed_slots_by_facility": dict(minimum_staffed_slots_by_facility),
        },
        "objective_weights": objective_profile(context),
        "synergy_bundles": load_synergy_bundles(),
        "rooms": room_results,
        "search_completeness": {
            "all_rooms_untruncated": all(not value["truncated"] for value in room_results.values()),
            "truncated_rooms": [key for key, value in room_results.items() if value["truncated"]],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="枚举单房间合法组合")
    parser.add_argument("context", help="normalize_input.py 生成的 decision context JSON")
    parser.add_argument("--output", required=True)
    parser.add_argument("--top-k", type=int, default=60)
    parser.add_argument("--operator-pool-size", type=int, default=14)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    context = read_json(args.context)
    library = build_library(
        context,
        top_k=max(1, args.top_k),
        operator_pool_size=max(1, args.operator_pool_size),
        allow_partial=args.allow_partial,
    )
    write_json(args.output, library)
    print(json.dumps({
        "output": str(Path(args.output)),
        "rooms": len(library["rooms"]),
        "combinations": sum(item["kept_count"] for item in library["rooms"].values()),
        "truncated_rooms": library["search_completeness"]["truncated_rooms"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
