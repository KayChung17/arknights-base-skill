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
from drone_model import drone_rules, natural_lmd_metrics_per_hour
from efficiency_calculator import EfficiencyCalculator
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




def _compute_efficiency(
    facility: str,
    product: str,
    operators: list[dict[str, Any]],
    trading_count: int,
    power_count: int,
) -> dict[str, Any]:
    if facility not in {"trading_post", "factory", "power_plant", "control_center"}:
        return {"error": f"calculator_unsupported:{facility}", "warnings": []}
    return EfficiencyCalculator(
        facility,
        operators,
        product,
        trading_post_count=trading_count,
        power_plant_count=power_count,
        global_operators=operators,
    ).compute()

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
    bonus = _effective_bonus(result) + _external_bonus(operators, facility, product)
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


def build_room_combinations(
    context: dict[str, Any],
    room: dict[str, Any],
    *,
    top_k: int = 60,
    operator_pool_size: int = 14,
    allow_partial: bool = False,
) -> dict[str, Any]:
    roster = context_roster(context)
    facility = room["facility_id"]
    product = room["product_id"]
    capacity = int(room["capacity"])
    eligible = eligible_operators(context, facility, product)
    trading_count = sum(1 for value in context_rooms(context).values() if value["facility_id"] == "trading_post")
    power_count = sum(1 for value in context_rooms(context).values() if value["facility_id"] == "power_plant")

    individual_records: list[dict[str, Any]] = []
    for op in eligible:
        result = _compute_efficiency(facility, product, [op], trading_count, power_count)
        metrics, fixed = _metrics_per_hour(room, result, [op])
        score = metric_score(metrics, objective_profile(context)) + fixed["fixed_lmd_per_trigger"] * 0.001
        individual_records.append({"score": score, "operator": op, "metrics": metrics})
    individual_records.sort(key=lambda item: (-float(item["score"]), item["operator"]["name"]))
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
        sizes = list(range(0, maximum + 1))
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
            result = _compute_efficiency(facility, product, selected, trading_count, power_count)
            metrics, fixed = _metrics_per_hour(room, result, selected)
            score = metric_score(metrics, objective_profile(context))
            score += fixed["fixed_lmd_per_trigger"] * objective_profile(context).get("fixed_lmd", 0)
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
                    "metrics_per_hour": metrics,
                    "fixed_metrics": fixed,
                    "warehouse_capacity": warehouse_capacity(room, selected),
                    "morale_cost_per_operator_hour": 1.0,
                    "efficiency_result": result,
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
    target = min(len(combinations), max(top_k, top_k * 3))

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
        metric_keys = {
            key
            for item in combinations
            for key in (item.get("metrics_per_hour") or {})
        }
        for key in sorted(metric_keys):
            add_item(max(combinations, key=lambda item: float((item.get("metrics_per_hour") or {}).get(key, 0.0))))
            add_item(min(combinations, key=lambda item: float((item.get("metrics_per_hour") or {}).get(key, 0.0))))
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
) -> dict[str, Any]:
    room_results: dict[str, Any] = {}
    for room_id, room in context_rooms(context).items():
        room_results[room_id] = build_room_combinations(
            context,
            room,
            top_k=top_k,
            operator_pool_size=operator_pool_size,
            allow_partial=allow_partial,
        )
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
        },
        "objective_weights": objective_profile(context),
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
