#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract a conservative multi-objective frontier from solver/search outputs."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metrics(candidate: dict[str, Any]) -> dict[str, float] | None:
    simulation = candidate.get("simulation") or {}
    aggregate = simulation.get("aggregate_metrics") or candidate.get("aggregate_metrics") or {}
    orundum = _number(candidate.get("orundum_per_day", aggregate.get("orundum")))
    lmd = _number(candidate.get("net_lmd_per_day", simulation.get("net_lmd_balance")))
    shard = _number(candidate.get("originium_shard_balance", candidate.get("orundum_shard_balance", simulation.get("originium_shard_balance", simulation.get("orundum_shard_balance")))))
    gold = _number(candidate.get("pure_gold_balance", simulation.get("pure_gold_balance")))
    operations = _number(candidate.get("operation_count", candidate.get("switch_count", simulation.get("operation_count", simulation.get("switch_count", 0.0)))))
    if orundum is None and lmd is None:
        return None
    return {
        "orundum_per_day": orundum or 0.0,
        "net_lmd_per_day": lmd or 0.0,
        "originium_shard_balance": shard or 0.0,
        "pure_gold_balance": gold or 0.0,
        "operation_count": operations or 0.0,
    }


def _candidate_lists(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 6:
        return []
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and _metrics(item) is not None:
                found.append(item)
            elif isinstance(item, (dict, list)):
                found.extend(_candidate_lists(item, depth + 1))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"candidates", "ranked_candidates", "ranked_results", "solutions", "top_solutions", "results", "evaluated_profiles"}:
                found.extend(_candidate_lists(item, depth + 1))
            elif depth < 2 and isinstance(item, (dict, list)):
                found.extend(_candidate_lists(item, depth + 1))
    return found


def _dominates(left: dict[str, float], right: dict[str, float]) -> bool:
    at_least = (
        left["orundum_per_day"] >= right["orundum_per_day"]
        and left["net_lmd_per_day"] >= right["net_lmd_per_day"]
        and left["operation_count"] <= right["operation_count"]
    )
    strict = (
        left["orundum_per_day"] > right["orundum_per_day"]
        or left["net_lmd_per_day"] > right["net_lmd_per_day"]
        or left["operation_count"] < right["operation_count"]
    )
    return at_least and strict


def build_pareto_frontier(result: dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    candidates = _candidate_lists(result)
    selected = result.get("selected") or result.get("selected_solution")
    if isinstance(selected, dict) and _metrics(selected) is not None:
        candidates.append(selected)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, candidate in enumerate(candidates):
        metrics = _metrics(candidate)
        if metrics is None:
            continue
        signature = sha256(json.dumps(metrics, sort_keys=True).encode("utf-8")).hexdigest()
        if signature in seen:
            continue
        seen.add(signature)
        unique.append({
            "candidate_index": index,
            "metrics": metrics,
            "layout": candidate.get("layout") or candidate.get("profile_id") or candidate.get("profile"),
            "optimality_claim": candidate.get("optimality_claim"),
        })
    frontier = [
        item for item in unique
        if not any(_dominates(other["metrics"], item["metrics"]) for other in unique if other is not item)
    ]
    frontier.sort(key=lambda item: (-item["metrics"]["orundum_per_day"], -item["metrics"]["net_lmd_per_day"], item["metrics"]["operation_count"]))
    return {
        "pareto_schema_version": 1,
        "objectives": {
            "maximize": ["orundum_per_day", "net_lmd_per_day"],
            "minimize": ["operation_count"],
        },
        "candidate_count_detected": len(unique),
        "frontier_count": len(frontier),
        "frontier": frontier[:limit],
        "scope_note": "前沿只覆盖本次求解输出中可识别的候选，不扩大候选库，也不构成实际全局最优证明。",
    }
