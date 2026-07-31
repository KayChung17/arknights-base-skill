#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Solve the global schedule with a room-combination MILP and simulation rerank."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csc_matrix, hstack

from build_combinations import build_library
from build_model import _solver_settings, build_milp
from reproducibility import build_manifest, canonical_json_hash

from optimizer_common import (
    context_rooms,
    context_segments,
    read_json,
    stable_id,
    utc_now,
    write_json,
)
from simulate_schedule import simulate_assignment
from timeline_utils import rotation_analysis
from right_side_schedule import fixed_work_by_segment


class ScheduleSolveError(RuntimeError):
    """Solver failure with a structured, user-facing diagnostic payload."""

    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


DIAGNOSTIC_CONSTRAINT_FAMILIES: dict[str, set[str]] = {
    "candidate_coverage": {"one_combination"},
    "product_requirements": {"product_room_count"},
    "operator_availability": {"operator_exclusivity"},
    "resource_floors": {
        "orundum_shard_balance_including_drones",
        "orundum_shard_hard_safety_floor",
        "net_lmd_balance_including_drones",
        "pure_gold_balance_including_drones",
        "pure_gold_hard_safety_floor",
    },
    "drone_policy": {
        "drone_allocation_link", "drone_target_link", "one_drone_target_per_node",
        "initial_drone_inventory", "drone_use_available_at_node",
        "empty_drone_inventory_at_node", "drone_inventory_flow",
    },
}


def _elastic_relaxation(
    bundle: Any,
    allowed_types: set[str],
    *,
    time_limit: float,
) -> dict[str, Any]:
    """Find the minimum relaxation of selected rows while all other rows stay hard."""
    records = list(bundle.constraint_records or [])
    matrix = bundle.constraints.A.tocsc()
    lower = np.asarray(bundle.constraints.lb, dtype=float)
    upper = np.asarray(bundle.constraints.ub, dtype=float)
    slack_columns: list[csc_matrix] = []
    slack_meta: list[dict[str, Any]] = []
    objective = list(np.zeros(len(bundle.c), dtype=float))
    for row, record in enumerate(records):
        if str(record.get("type") or "") not in allowed_types:
            continue
        scale = max(
            1.0,
            abs(float(lower[row])) if np.isfinite(lower[row]) else 0.0,
            abs(float(upper[row])) if np.isfinite(upper[row]) else 0.0,
        )
        if np.isfinite(lower[row]):
            column = csc_matrix(([1.0], ([row], [0])), shape=(matrix.shape[0], 1))
            slack_columns.append(column)
            objective.append(1.0 / scale)
            slack_meta.append({"row": row, "direction": "lower", "record": record})
        if np.isfinite(upper[row]):
            column = csc_matrix(([-1.0], ([row], [0])), shape=(matrix.shape[0], 1))
            slack_columns.append(column)
            objective.append(1.0 / scale)
            slack_meta.append({"row": row, "direction": "upper", "record": record})
    if not slack_columns:
        return {"feasible": False, "reason": "family_has_no_constraints", "relaxations": []}

    augmented = hstack([matrix, *slack_columns], format="csc")
    slack_count = len(slack_columns)
    bounds = Bounds(
        np.concatenate([np.asarray(bundle.bounds.lb, dtype=float), np.zeros(slack_count)]),
        np.concatenate([np.asarray(bundle.bounds.ub, dtype=float), np.full(slack_count, np.inf)]),
    )
    result = milp(
        np.asarray(objective, dtype=float),
        integrality=np.concatenate([np.asarray(bundle.integrality, dtype=int), np.zeros(slack_count, dtype=int)]),
        bounds=bounds,
        constraints=LinearConstraint(augmented, lower, upper),
        options={"time_limit": max(1.0, min(float(time_limit), 15.0)), "presolve": True},
    )
    if result.x is None:
        return {"feasible": False, "reason": str(result.message), "relaxations": []}
    slack_values = result.x[len(bundle.c):]
    relaxations = []
    for meta, value in zip(slack_meta, slack_values):
        if float(value) <= 1e-7:
            continue
        relaxations.append({
            "constraint_type": meta["record"].get("type"),
            "direction": meta["direction"],
            "minimum_relaxation": float(value),
            "constraint": meta["record"],
        })
    return {
        "feasible": True,
        "objective": float(result.fun),
        "relaxations": sorted(
            relaxations,
            key=lambda item: (-float(item["minimum_relaxation"]), str(item["constraint_type"])),
        ),
    }


def diagnose_infeasible_model(bundle: Any, *, time_limit: float) -> dict[str, Any]:
    """Identify constraint families whose smallest relaxation restores feasibility."""
    families: dict[str, Any] = {}
    for name, constraint_types in DIAGNOSTIC_CONSTRAINT_FAMILIES.items():
        result = _elastic_relaxation(bundle, constraint_types, time_limit=time_limit)
        if result.get("feasible"):
            families[name] = result
    combined = _elastic_relaxation(
        bundle,
        set().union(*DIAGNOSTIC_CONSTRAINT_FAMILIES.values()),
        time_limit=time_limit,
    )
    return {
        "failure_type": "milp_infeasible",
        "candidate_library_complete": bool(
            ((bundle.metadata or {}).get("search_completeness") or {}).get("all_rooms_untruncated")
        ),
        "constraint_families_individually_recovering_feasibility": families,
        "combined_minimum_relaxation": combined,
    }


def _selected_variables(bundle, vector) -> tuple[list[dict[str, Any]], list[int], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    assignments: list[dict[str, Any]] = []
    selected_indices: list[int] = []
    drone_allocations: list[dict[str, Any]] = []
    drone_inventory: list[dict[str, Any]] = []
    drone_waste: list[dict[str, Any]] = []
    for index, (record, value) in enumerate(zip(bundle.variable_records, vector)):
        kind = record.get("kind")
        if kind == "assignment" and value >= 0.5:
            selected_indices.append(index)
            assignments.append({
                "segment_id": record["segment_id"],
                "room_id": record["room_id"],
                "combination_id": record["combination_id"],
            })
        elif kind == "drone_allocation" and value >= 0.5:
            drone_allocations.append({
                "segment_id": record["segment_id"],
                "room_id": record["room_id"],
                "combination_id": record.get("combination_id"),
                "profile_id": record.get("profile_id"),
                "eligible_combination_ids": record.get("eligible_combination_ids") or [],
                "drones": int(round(float(value))),
                "metrics_per_drone": record.get("metrics_per_drone") or {},
            })
        elif kind == "drone_inventory":
            drone_inventory.append({
                "node_index": int(record["node_index"]),
                "segment_id": record.get("segment_id"),
                "inventory": float(value),
            })
        elif kind == "drone_waste" and value > 1e-7:
            drone_waste.append({
                "segment_id": record["segment_id"],
                "waste": float(value),
            })
    assignments.sort(key=lambda item: (item["segment_id"], item["room_id"]))
    drone_allocations.sort(key=lambda item: (item["segment_id"], item["room_id"]))
    drone_inventory.sort(key=lambda item: item["node_index"])
    drone_waste.sort(key=lambda item: item["segment_id"])
    return assignments, selected_indices, drone_allocations, drone_inventory, drone_waste


def _selected_resource_shortfalls(bundle, vector) -> dict[str, float]:
    return {
        str(record.get("resource") or ""): float(value)
        for record, value in zip(bundle.variable_records, vector)
        if record.get("kind") == "resource_shortfall" and float(value) > 1e-7
    }



def _simulation_constraint_violations(context: dict[str, Any], simulation: dict[str, Any]) -> list[str]:
    """Check hard constraints again using globally recalculated metrics.

    The MILP uses room-local proxy coefficients. Control-center and other
    simultaneous global effects are applied by ``simulate_assignment`` and can
    change resource flows. A candidate that misses the user's actual inventory
    floor is rejected and a no-good cut asks the solver for another schedule.
    """

    settings = _solver_settings(context)
    violations: list[str] = []
    if settings.get("require_resource_balance"):
        actual = float(simulation.get("orundum_shard_balance", 0.0))
        mode = str(settings.get("orundum_shard_balance_mode", "hard"))
        minimum_value = settings.get("hard_minimum_orundum_shard_balance") if mode == "soft" else settings.get("minimum_orundum_shard_balance", 0.0)
        if minimum_value is not None and actual < float(minimum_value) - 1e-6:
            violations.append(
                f"actual_orundum_shard_balance:{actual:.6f} < minimum:{float(minimum_value):.6f}"
            )
    if settings.get("require_lmd_balance"):
        actual = float(simulation.get("net_lmd_balance", 0.0))
        minimum = float(settings.get("minimum_net_lmd_balance", 0.0))
        if actual < minimum - 1e-6:
            violations.append(f"actual_net_lmd_balance:{actual:.6f} < minimum:{minimum:.6f}")
    if settings.get("require_pure_gold_balance"):
        actual = float(simulation.get("pure_gold_balance", 0.0))
        mode = str(settings.get("pure_gold_balance_mode", "hard"))
        minimum_value = settings.get("hard_minimum_pure_gold_balance") if mode == "soft" else settings.get("minimum_pure_gold_balance", 0.0)
        if minimum_value is not None and actual < float(minimum_value) - 1e-6:
            violations.append(f"actual_pure_gold_balance:{actual:.6f} < minimum:{float(minimum_value):.6f}")
    drone_plan = simulation.get("drone_plan")
    if settings.get("allocate_drones") and drone_plan is not None and not bool(drone_plan.get("feasible", False)):
        violations.append("drone_inventory_flow_not_feasible")
    dormitory_plan = simulation.get("dormitory_plan") or {}
    if settings.get("require_dormitory_cycle") and not bool(dormitory_plan.get("repeating_day_verified", False)):
        violations.append("dormitory_repeating_day_morale_not_feasible")
    return violations


def _battle_record_exp(simulation: dict[str, Any]) -> float:
    return float((simulation.get("aggregate_metrics") or {}).get("battle_record_exp", 0.0) or 0.0)


def _primary_metrics_not_worse(before: dict[str, Any], after: dict[str, Any]) -> bool:
    pairs = (
        ((before.get("aggregate_metrics") or {}).get("orundum", 0.0), (after.get("aggregate_metrics") or {}).get("orundum", 0.0)),
        (before.get("net_lmd_balance", 0.0), after.get("net_lmd_balance", 0.0)),
        (before.get("orundum_shard_balance", 0.0), after.get("orundum_shard_balance", 0.0)),
        (before.get("pure_gold_balance", 0.0), after.get("pure_gold_balance", 0.0)),
    )
    return all(float(new or 0.0) >= float(old or 0.0) - 1e-6 for old, new in pairs)


def _apply_free_secondary_improvements(
    context: dict[str, Any],
    library: dict[str, Any],
    assignments: list[dict[str, Any]],
    simulation: dict[str, Any],
    drone_allocations: list[dict[str, Any]],
    drone_inventory: list[dict[str, Any]],
    drone_waste: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Lexicographically improve battle-record output without reducing primary metrics."""
    current_assignments = [dict(item) for item in assignments]
    current_simulation = simulation
    drone_rooms = {(str(item.get("segment_id")), str(item.get("room_id"))) for item in drone_allocations}
    improvements: list[dict[str, Any]] = []

    for _ in range(max(1, len(current_assignments))):
        assignment_map = {(item["segment_id"], item["room_id"]): item for item in current_assignments}
        selected_ops: dict[str, set[str]] = {}
        for key, item in assignment_map.items():
            room = (library.get("rooms") or {}).get(item["room_id"]) or {}
            combo = next((c for c in room.get("combinations") or [] if c.get("combination_id") == item.get("combination_id")), None)
            selected_ops[key[0]] = selected_ops.get(key[0], set()) | {
                str(op.get("name")) for op in (combo or {}).get("operators") or []
            }
        best: dict[str, Any] | None = None

        for key, item in assignment_map.items():
            segment_id, room_id = key
            room_result = (library.get("rooms") or {}).get(room_id) or {}
            room = room_result.get("room") or {}
            if room.get("facility_id") != "factory" or room.get("product_id") != "battle_record":
                continue
            if key in drone_rooms:
                continue
            combos = room_result.get("combinations") or []
            current_combo = next((c for c in combos if c.get("combination_id") == item.get("combination_id")), None)
            if not current_combo:
                continue
            current_names = {str(op.get("name")) for op in current_combo.get("operators") or []}
            busy_elsewhere = selected_ops.get(segment_id, set()) - current_names
            alternatives = sorted(
                combos,
                key=lambda combo: -float((combo.get("metrics_per_hour") or {}).get("battle_record_exp", 0.0) or 0.0),
            )
            for alternative in alternatives:
                old_rate = float((current_combo.get("metrics_per_hour") or {}).get("battle_record_exp", 0.0) or 0.0)
                new_rate = float((alternative.get("metrics_per_hour") or {}).get("battle_record_exp", 0.0) or 0.0)
                if new_rate <= old_rate + 1e-9:
                    break
                alternative_names = {str(op.get("name")) for op in alternative.get("operators") or []}
                if alternative_names & busy_elsewhere:
                    continue
                mutated = [dict(record) for record in current_assignments]
                for record in mutated:
                    if record["segment_id"] == segment_id and record["room_id"] == room_id:
                        record["combination_id"] = alternative["combination_id"]
                        break
                candidate_simulation = simulate_assignment(
                    context, library, mutated, drone_allocations, drone_inventory, drone_waste,
                )
                if _simulation_constraint_violations(context, candidate_simulation):
                    continue
                if any(float((state or {}).get("minimum", 0.0) or 0.0) < -1e-6 for state in (candidate_simulation.get("morale") or {}).values()):
                    continue
                if not _primary_metrics_not_worse(current_simulation, candidate_simulation):
                    continue
                gain = _battle_record_exp(candidate_simulation) - _battle_record_exp(current_simulation)
                if gain <= 1e-6:
                    continue
                candidate = {
                    "assignments": mutated,
                    "simulation": candidate_simulation,
                    "gain": gain,
                    "segment_id": segment_id,
                    "room_id": room_id,
                    "from_combination_id": current_combo["combination_id"],
                    "to_combination_id": alternative["combination_id"],
                    "from_operators": sorted(current_names),
                    "to_operators": sorted(alternative_names),
                }
                if best is None or gain > float(best["gain"]) + 1e-6:
                    best = candidate
                break
        if best is None:
            break
        current_assignments = best.pop("assignments")
        current_simulation = best.pop("simulation")
        improvements.append(best)

    return current_assignments, current_simulation, {
        "checked": True,
        "scope": {
            "facility_id": "factory",
            "product_id": "battle_record",
            "drone_target_rooms": "skipped",
        },
        "skipped_drone_target_rooms": [
            {"segment_id": segment_id, "room_id": room_id}
            for segment_id, room_id in sorted(drone_rooms)
        ],
        "policy": "preserve_or_improve_orundum_lmd_shard_and_gold_then_maximize_battle_record_exp",
        "improvement_count": len(improvements),
        "battle_record_exp_gain": sum(float(item["gain"]) for item in improvements),
        "improvements": improvements,
        "remaining_dominated_empty_slots": [],
    }


def _combo_lookup(library: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(room_id), str(combo.get("combination_id"))): combo
        for room_id, room in (library.get("rooms") or {}).items()
        for combo in (room.get("combinations") or [])
    }


def _assignment_opportunity_risks(
    library: dict[str, Any], assignments: list[dict[str, Any]],
) -> list[dict[str, str]]:
    lookup = _combo_lookup(library)
    risks: list[dict[str, str]] = []
    for assignment in assignments:
        combo = lookup.get((str(assignment.get("room_id")), str(assignment.get("combination_id")))) or {}
        for operator in ((combo.get("effect_resolution") or {}).get("opportunity_risk_operators") or []):
            risks.append({
                "segment_id": str(assignment.get("segment_id") or ""),
                "room_id": str(assignment.get("room_id") or ""),
                "operator": str(operator),
            })
    return risks


def _assignments_respect_exclusivity(
    context: dict[str, Any], library: dict[str, Any], assignments: list[dict[str, Any]],
) -> bool:
    lookup = _combo_lookup(library)
    segments = {item.segment_id: item for item in context_segments(context)}
    fixed_by_segment = fixed_work_by_segment(context)
    used_by_segment = {segment_id: set(names) for segment_id, names in fixed_by_segment.items()}
    for assignment in assignments:
        segment_id = str(assignment.get("segment_id") or "")
        room_id = str(assignment.get("room_id") or "")
        combo = lookup.get((room_id, str(assignment.get("combination_id"))))
        segment = segments.get(segment_id)
        if combo is None or segment is None:
            return False
        names = {str(item.get("name") or "") for item in combo.get("operators") or []}
        if names & used_by_segment.setdefault(segment_id, set()):
            return False
        used_by_segment[segment_id].update(names)
    return True


def _active_order_signature(combo: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(
        str(item.get("effect") or "")
        for item in ((((combo.get("effect_resolution") or {}).get("special_order") or {}).get("active") or []))
    ))


def _combo_metrics_not_worse(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    candidate_metrics = candidate.get("metrics_per_hour") or {}
    baseline_metrics = baseline.get("metrics_per_hour") or {}
    return all(
        float(candidate_metrics.get(key, 0.0) or 0.0) >= float(value or 0.0) - 1e-9
        for key, value in baseline_metrics.items()
    )


def _metric_value(simulation: dict[str, Any], key: str) -> float:
    if key == "net_lmd":
        return float(simulation.get("net_lmd_balance", 0.0) or 0.0)
    if key == "orundum_shard_balance":
        return float(simulation.get("orundum_shard_balance", 0.0) or 0.0)
    if key == "pure_gold_balance":
        return float(simulation.get("pure_gold_balance", 0.0) or 0.0)
    return float((simulation.get("aggregate_metrics") or {}).get(key, 0.0) or 0.0)


def _release_is_neutral(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return all(
        _metric_value(after, key) >= _metric_value(before, key) - 1e-6
        for key in ("orundum", "battle_record_exp", "net_lmd")
    ) and float(after.get("actual_objective_score", 0.0)) >= float(before.get("actual_objective_score", 0.0)) - 1e-6


def _efficiency_better(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_score = float(before.get("actual_objective_score", 0.0))
    after_score = float(after.get("actual_objective_score", 0.0))
    if after_score > before_score + 1e-6:
        return True
    if after_score < before_score - 1e-6:
        return False
    for key in ("orundum", "battle_record_exp", "net_lmd"):
        old = _metric_value(before, key)
        new = _metric_value(after, key)
        if new > old + 1e-6:
            return True
        if new < old - 1e-6:
            return False
    return False


def _replace_assignment(
    assignments: list[dict[str, Any]], segment_id: str, room_id: str, combination_id: str,
) -> list[dict[str, Any]]:
    mutated = [dict(item) for item in assignments]
    for item in mutated:
        if item.get("segment_id") == segment_id and item.get("room_id") == room_id:
            item["combination_id"] = combination_id
            break
    return mutated


def _apply_opportunity_cost_improvements(
    context: dict[str, Any],
    library: dict[str, Any],
    assignments: list[dict[str, Any]],
    simulation: dict[str, Any],
    drone_allocations: list[dict[str, Any]],
    drone_inventory: list[dict[str, Any]],
    drone_waste: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Release suppressed high-value skills, then seek productive reassignment."""

    settings = _solver_settings(context)
    max_iterations = max(0, int(settings.get("opportunity_postprocess_max_iterations", 0)))
    current_assignments = [dict(item) for item in assignments]
    current_simulation = simulation
    lookup = _combo_lookup(library)
    initial_risks = _assignment_opportunity_risks(library, current_assignments)
    released: set[str] = set()
    changes: list[dict[str, Any]] = []
    drone_rooms = {
        (str(item.get("segment_id") or ""), str(item.get("room_id") or ""))
        for item in drone_allocations
        if float(item.get("drones", 0.0) or 0.0) > 0.0
    }

    for _ in range(max_iterations):
        risks = _assignment_opportunity_risks(library, current_assignments)
        best_release: dict[str, Any] | None = None
        for risk in risks:
            if (risk["segment_id"], risk["room_id"]) in drone_rooms:
                continue
            key = (risk["room_id"], next(
                str(item.get("combination_id"))
                for item in current_assignments
                if item.get("segment_id") == risk["segment_id"] and item.get("room_id") == risk["room_id"]
            ))
            current_combo = lookup.get(key) or {}
            room = (library.get("rooms") or {}).get(risk["room_id"]) or {}
            alternatives = sorted(
                (
                    combo for combo in (room.get("combinations") or [])
                    if risk["operator"] not in {str(op.get("name") or "") for op in combo.get("operators") or []}
                    and _active_order_signature(combo) == _active_order_signature(current_combo)
                    and _combo_metrics_not_worse(combo, current_combo)
                ),
                key=lambda combo: (
                    len((combo.get("effect_resolution") or {}).get("opportunity_risk_operators") or []),
                    -float(combo.get("proxy_score_per_hour", 0.0)),
                ),
            )[:16]
            for alternative in alternatives:
                mutated = _replace_assignment(
                    current_assignments, risk["segment_id"], risk["room_id"], str(alternative["combination_id"]),
                )
                if not _assignments_respect_exclusivity(context, library, mutated):
                    continue
                candidate_simulation = simulate_assignment(
                    context, library, mutated, drone_allocations, drone_inventory, drone_waste,
                )
                if _simulation_constraint_violations(context, candidate_simulation):
                    continue
                old_count = len(risks)
                new_count = len(_assignment_opportunity_risks(library, mutated))
                if new_count >= old_count or not _release_is_neutral(current_simulation, candidate_simulation):
                    continue
                best_release = {
                    "assignments": mutated,
                    "simulation": candidate_simulation,
                    "kind": "release_suppressed_operator",
                    "operator": risk["operator"],
                    "segment_id": risk["segment_id"],
                    "room_id": risk["room_id"],
                    "from_operators": [str(op.get("name") or "") for op in current_combo.get("operators") or []],
                    "to_operators": [str(op.get("name") or "") for op in alternative.get("operators") or []],
                    "opportunity_risk_reduction": old_count - new_count,
                }
                break
            if best_release:
                break
        if best_release is None:
            break
        current_assignments = best_release.pop("assignments")
        current_simulation = best_release.pop("simulation")
        released.add(str(best_release["operator"]))
        changes.append(best_release)

    # Once a valuable operator has been released from a suppressed slot, try
    # every retained room candidate that uses it. Each mutation is checked
    # against the full-day simulation and all remaining hard constraints.
    for _ in range(max_iterations):
        best: dict[str, Any] | None = None
        for operator in sorted(released):
            for assignment in current_assignments:
                segment_id = str(assignment.get("segment_id") or "")
                room_id = str(assignment.get("room_id") or "")
                if (segment_id, room_id) in drone_rooms:
                    continue
                current_combo = lookup.get((room_id, str(assignment.get("combination_id")))) or {}
                if operator in {str(op.get("name") or "") for op in current_combo.get("operators") or []}:
                    continue
                room = (library.get("rooms") or {}).get(room_id) or {}
                alternatives = sorted(
                    (
                        combo for combo in (room.get("combinations") or [])
                        if operator in {str(op.get("name") or "") for op in combo.get("operators") or []}
                    ),
                    key=lambda combo: -float(combo.get("proxy_score_per_hour", 0.0)),
                )[:16]
                for alternative in alternatives:
                    mutated = _replace_assignment(current_assignments, segment_id, room_id, str(alternative["combination_id"]))
                    if not _assignments_respect_exclusivity(context, library, mutated):
                        continue
                    candidate_simulation = simulate_assignment(
                        context, library, mutated, drone_allocations, drone_inventory, drone_waste,
                    )
                    if _simulation_constraint_violations(context, candidate_simulation):
                        continue
                    if not _efficiency_better(current_simulation, candidate_simulation):
                        continue
                    gain = float(candidate_simulation.get("actual_objective_score", 0.0)) - float(current_simulation.get("actual_objective_score", 0.0))
                    candidate = {
                        "assignments": mutated,
                        "simulation": candidate_simulation,
                        "kind": "reuse_released_operator",
                        "operator": operator,
                        "segment_id": segment_id,
                        "room_id": room_id,
                        "from_operators": [str(op.get("name") or "") for op in current_combo.get("operators") or []],
                        "to_operators": [str(op.get("name") or "") for op in alternative.get("operators") or []],
                        "actual_objective_gain": gain,
                    }
                    if best is None or gain > float(best.get("actual_objective_gain", 0.0)) + 1e-6:
                        best = candidate
        if best is None:
            break
        current_assignments = best.pop("assignments")
        current_simulation = best.pop("simulation")
        changes.append(best)

    remaining = _assignment_opportunity_risks(library, current_assignments)
    return current_assignments, current_simulation, {
        "checked": True,
        "policy": "release_suppressed_high_value_effects_then_full_day_counterfactual_reassignment",
        "max_iterations": max_iterations,
        "initial_opportunity_risks": initial_risks,
        "released_operators": sorted(released),
        "changes": changes,
        "remaining_opportunity_risks": remaining,
        "neighborhood_exhausted": not remaining,
        "skipped_drone_target_rooms": [
            {"segment_id": segment_id, "room_id": room_id}
            for segment_id, room_id in sorted(drone_rooms)
        ],
    }

def _candidate_plan(
    context: dict[str, Any],
    library: dict[str, Any],
    assignments: list[dict[str, Any]],
    simulation: dict[str, Any],
    solver_meta: dict[str, Any],
) -> dict[str, Any]:
    segments = {item.segment_id: item for item in context_segments(context)}
    rooms = context_rooms(context)
    combo_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for room_id, room_result in (library.get("rooms") or {}).items():
        for combo in room_result.get("combinations") or []:
            combo_lookup[(room_id, combo["combination_id"])] = combo

    plan_segments: dict[str, Any] = {}
    for segment_id, segment in segments.items():
        plan_segments[segment_id] = {
            "name": segment.name,
            "start": segment.start,
            "end": segment.end,
            "hours": segment.hours,
            "rooms": {},
        }
    for item in assignments:
        room = rooms[item["room_id"]]
        combo = combo_lookup[(item["room_id"], item["combination_id"])]
        plan_segments[item["segment_id"]]["rooms"][item["room_id"]] = {
            "facility_id": room["facility_id"],
            "product_id": combo.get("product_id", room["product_id"]),
            "operators": combo["operators"],
            "combination_id": combo["combination_id"],
        }

    objective = context.get("objective") or {}
    baseline = context.get("baseline") or {}
    warnings = simulation.get("warnings") or []
    economy = simulation.get("aggregate_metrics") or {}
    return {
        "schema_version": 4,
        "plan_id": stable_id("milp_plan", assignments),
        "title": "混合枚举与MILP求解候选",
        "plan_status": "candidate",
        "layout": objective.get("layout"),
        "goal": objective.get("goal_id"),
        "cross_shift_reuse_policy": "allowed",
        "rotation_analysis": rotation_analysis({
            "segments": plan_segments,
            "assumptions": {"repeating_daily": True},
        }),
        "decision": {
            "strategy": "单房间组合枚举 + 全局MILP选择 + 同时基建复算",
            "rationale": [
                "枚举阶段只保留有验证技能数据的房间组合。",
                "MILP同时处理房间容量、同一时间重复进驻和每日工时。",
                "求解后使用所有同时工作干员重新计算全局联动、仓库和无人机库存。",
                "无人机在操作节点分配，每架按3分钟基础工时折算，发电站恢复形成重复日闭环。",
            ],
            "tradeoffs": [
                "候选库可能因operator_pool_size或top_k截断。",
                "龙门币无人机收益在未知当前订单时使用订单概率期望；未结构化特殊技能仍需复算。",
            ],
            "external_evidence_ids": [],
        },
        "baseline": {
            "reference_id": baseline.get("reference_id"),
            "comparison": {},
            "deviations": [],
        },
        "facility_configuration": context.get("facility_configuration"),
        "operation_nodes": context.get("operation_nodes") or [],
        "segments": plan_segments,
        "recovery_plan": {
            "events": (simulation.get("dormitory_plan") or {}).get("assignments") or [],
            "repeating_day_verified": bool((simulation.get("dormitory_plan") or {}).get("repeating_day_verified", False)),
            "model": (simulation.get("dormitory_plan") or {}).get("model"),
            "automation_rules_used": False,
        },
        "right_side_plan": simulation.get("right_side_plan") or {},
        "economy_projection": {
            "source": "script",
            "daily": economy,
            "costs": {
                "lmd": economy.get("lmd_cost", 0.0),
                "orirock_cube": economy.get("orirock_cube_consumption", 0.0),
                "pure_gold_for_drone_orders": economy.get("pure_gold_consumption", 0.0),
            },
            "inventory_delta": {
                "orundum_shard": simulation.get("orundum_shard_balance"),
                "pure_gold": simulation.get("pure_gold_balance"),
                "lmd": simulation.get("net_lmd_balance"),
            },
            "warehouse_overflow_checked": not any(item.get("warehouse_overflow") for item in simulation.get("room_results") or []),
            "drone_policy": simulation.get("drone_plan") or {},
        },
        "assumptions": simulation.get("assumptions") or {},
        "solver": solver_meta,
        "simulation": simulation,
    }


def solve_hybrid(
    context: dict[str, Any],
    *,
    library: dict[str, Any] | None = None,
    top_k: int = 60,
    operator_pool_size: int = 14,
    top_solutions: int = 5,
    time_limit: float = 30.0,
    mip_rel_gap: float = 0.001,
    max_proxy_attempts: int | None = None,
    _rejection_retry_depth: int = 0,
) -> dict[str, Any]:
    settings = ((context.get("objective") or {}).get("preferences") or {}).get("solver") or {}
    expansion_rounds = max(0, int(settings.get("adaptive_candidate_expansion_rounds", 2)))
    expansion_factor = max(1.1, float(settings.get("adaptive_candidate_expansion_factor", 2.0)))
    maximum_top_k = max(top_k, int(settings.get("adaptive_candidate_max_top_k", top_k * 4)))
    maximum_pool = max(operator_pool_size, int(settings.get("adaptive_candidate_max_operator_pool_size", operator_pool_size + 8)))
    library = library or build_library(
        context,
        top_k=top_k,
        operator_pool_size=operator_pool_size,
        allow_partial=False,
    )
    expansion_trace: list[dict[str, Any]] = []
    current_top_k = int((library.get("parameters") or {}).get("top_k_per_room", top_k))
    current_pool = int((library.get("parameters") or {}).get("operator_pool_size", operator_pool_size))
    previous_signature: tuple[tuple[str, int], ...] | None = None
    for round_index in range(expansion_rounds + 1):
        completeness = library.get("search_completeness") or {}
        signature = tuple(sorted(
            (str(room_id), int(room.get("kept_count", 0) or 0))
            for room_id, room in (library.get("rooms") or {}).items()
        ))
        expansion_trace.append({
            "round": round_index,
            "top_k_per_room": current_top_k,
            "operator_pool_size": current_pool,
            "all_rooms_untruncated": bool(completeness.get("all_rooms_untruncated")),
            "room_kept_counts": dict(signature),
        })
        if completeness.get("all_rooms_untruncated"):
            expansion_trace[-1]["stop_reason"] = "candidate_library_complete"
            break
        if round_index >= expansion_rounds:
            expansion_trace[-1]["stop_reason"] = "expansion_round_limit"
            break
        next_top_k = min(maximum_top_k, max(current_top_k + 1, int(current_top_k * expansion_factor)))
        next_pool = min(maximum_pool, max(current_pool + 2, int(current_pool * 1.25)))
        if (next_top_k, next_pool) == (current_top_k, current_pool):
            expansion_trace[-1]["stop_reason"] = "configured_size_limit"
            break
        if previous_signature == signature:
            expansion_trace[-1]["stop_reason"] = "candidate_count_stable"
            break
        previous_signature = signature
        current_top_k, current_pool = next_top_k, next_pool
        library = build_library(
            context,
            top_k=current_top_k,
            operator_pool_size=current_pool,
            allow_partial=False,
        )
    no_good: list[list[int]] = []
    solutions: list[dict[str, Any]] = []
    rejected_after_simulation: list[dict[str, Any]] = []
    model_metadata: dict[str, Any] | None = None

    requested = max(1, top_solutions)
    # Proxy coefficients and global recalculation are intentionally separated.
    # Try additional proxy optima when a candidate fails a hard constraint only
    # after simultaneous effects are recalculated.
    configured_attempts = int(max_proxy_attempts) if max_proxy_attempts is not None else 0
    max_attempts = max(requested * 8, requested, configured_attempts)
    for attempt in range(max_attempts):
        if len(solutions) >= requested:
            break
        try:
            bundle = build_milp(context, library, no_good_solutions=no_good)
        except ValueError as exc:
            raise ScheduleSolveError(
                f"求解输入约束冲突: {exc}",
                {
                    "failure_type": "input_constraint_conflict",
                    "message": str(exc),
                    "candidate_library_complete": bool(
                        ((library.get("search_completeness") or {}).get("all_rooms_untruncated"))
                    ),
                },
            ) from exc
        model_metadata = bundle.metadata
        result = milp(
            bundle.c,
            integrality=bundle.integrality,
            bounds=bundle.bounds,
            constraints=bundle.constraints,
            options={
                "time_limit": float(time_limit),
                "mip_rel_gap": float(mip_rel_gap),
                "presolve": True,
            },
        )
        # HiGHS can return a valid incumbent when the time limit is reached.
        # Treat that incumbent as a feasible candidate; only fail when no
        # primal vector is available. Optimality metadata remains explicit.
        if result.x is None:
            if attempt == 0:
                diagnostics = diagnose_infeasible_model(bundle, time_limit=time_limit)
                diagnostics["solver_message"] = str(result.message)
                diagnostics["candidate_expansion"] = expansion_trace
                raise ScheduleSolveError(
                    f"MILP无可行解: {result.message}",
                    diagnostics,
                )
            break
        assignments, selected_indices, drone_allocations, drone_inventory, drone_waste = _selected_variables(bundle, result.x)
        resource_shortfalls = _selected_resource_shortfalls(bundle, result.x)
        no_good.append(selected_indices)
        simulation = simulate_assignment(
            context,
            library,
            assignments,
            drone_allocations,
            drone_inventory,
            drone_waste,
        )
        violations = _simulation_constraint_violations(context, simulation)
        secondary_postprocess: dict[str, Any] | None = None
        opportunity_postprocess: dict[str, Any] | None = None
        if not violations:
            assignments, simulation, secondary_postprocess = _apply_free_secondary_improvements(
                context, library, assignments, simulation,
                drone_allocations, drone_inventory, drone_waste,
            )
            violations = _simulation_constraint_violations(context, simulation)
        if not violations:
            assignments, simulation, opportunity_postprocess = _apply_opportunity_cost_improvements(
                context, library, assignments, simulation,
                drone_allocations, drone_inventory, drone_waste,
            )
            violations = _simulation_constraint_violations(context, simulation)
        record = {
            "proxy_rank": attempt + 1,
            "proxy_objective": -float(result.fun),
            "proxy_dual_bound": -float(getattr(result, "mip_dual_bound", result.fun)),
            "mip_gap": float(getattr(result, "mip_gap", 0.0)),
            "mip_node_count": int(getattr(result, "mip_node_count", 0)),
            "status": int(result.status),
            "message": str(result.message),
            "termination_success": bool(result.success),
            "incumbent_from_time_limit": bool((not result.success) and result.x is not None),
            "assignments": assignments,
            "drone_allocations": drone_allocations,
            "drone_inventory": drone_inventory,
            "drone_waste": drone_waste,
            "resource_shortfalls": resource_shortfalls,
            "simulation": simulation,
            "actual_constraint_violations": violations,
            "secondary_output_postprocess": secondary_postprocess,
            "opportunity_cost_postprocess": opportunity_postprocess,
        }
        if violations:
            rejected_after_simulation.append({
                "proxy_rank": attempt + 1,
                "proxy_objective": record["proxy_objective"],
                "violations": violations,
            })
            continue
        solutions.append(record)

    if not solutions:
        rejection_expansion_rounds = max(0, int(settings.get("adaptive_rejection_expansion_rounds", 1)))
        library_complete = bool((library.get("search_completeness") or {}).get("all_rooms_untruncated"))
        if not library_complete and _rejection_retry_depth < rejection_expansion_rounds:
            rejection_max_top_k = max(
                current_top_k,
                int(settings.get("adaptive_rejection_max_top_k", maximum_top_k * 2)),
            )
            rejection_max_pool = max(
                current_pool,
                int(settings.get("adaptive_rejection_max_operator_pool_size", maximum_pool + 8)),
            )
            next_top_k = min(rejection_max_top_k, max(current_top_k + 1, int(current_top_k * expansion_factor)))
            next_pool = min(rejection_max_pool, max(current_pool + 2, int(current_pool * 1.25)))
            if (next_top_k, next_pool) != (current_top_k, current_pool):
                expanded_library = build_library(
                    context,
                    top_k=next_top_k,
                    operator_pool_size=next_pool,
                    allow_partial=False,
                )
                try:
                    return solve_hybrid(
                        context,
                        library=expanded_library,
                        top_k=next_top_k,
                        operator_pool_size=next_pool,
                        top_solutions=top_solutions,
                        time_limit=time_limit,
                        mip_rel_gap=mip_rel_gap,
                        max_proxy_attempts=max_proxy_attempts,
                        _rejection_retry_depth=_rejection_retry_depth + 1,
                    )
                except ScheduleSolveError as exc:
                    exc.diagnostics.setdefault("post_rejection_candidate_expansion", []).insert(0, {
                        "retry_depth": _rejection_retry_depth + 1,
                        "previous_top_k": current_top_k,
                        "previous_operator_pool_size": current_pool,
                        "expanded_top_k": next_top_k,
                        "expanded_operator_pool_size": next_pool,
                        "trigger": "all_proxy_candidates_rejected_after_simulation",
                    })
                    raise
        violation_counts: dict[str, int] = {}
        for rejected in rejected_after_simulation:
            for violation in rejected.get("violations") or []:
                key = str(violation).split(":", 1)[0]
                violation_counts[key] = violation_counts.get(key, 0) + 1
        detail = rejected_after_simulation[:20]
        diagnostics = {
            "failure_type": "post_simulation_rejection_exhausted",
            "model": model_metadata or {},
            "proxy_attempt_limit": max_attempts,
            "proxy_attempts_completed": len(no_good),
            "rejected_after_simulation": detail,
            "violation_counts": violation_counts,
            "candidate_library_complete": library_complete,
            "candidate_expansion": expansion_trace,
            "recommended_action": (
                "expand_candidate_library"
                if not (library.get("search_completeness") or {}).get("all_rooms_untruncated")
                else "align_cross_facility_proxy_or_soften_user_authorized_resource_floor"
            ),
        }
        raise ScheduleSolveError(
            "求解器没有返回通过全局复算硬约束的候选方案"
            + (f": {detail}" if detail else "")
            , diagnostics
        )
    solutions.sort(
        key=lambda item: (
            -float(item["simulation"]["actual_objective_score"]),
            -float(item["proxy_objective"]),
        )
    )
    selected = solutions[0]
    search_complete = bool((library.get("search_completeness") or {}).get("all_rooms_untruncated"))
    proxy_optimal = all(
        bool(item.get("termination_success"))
        and item["mip_gap"] <= mip_rel_gap + 1e-12
        for item in solutions
    )
    accepted_time_limit_incumbents = sum(
        1 for item in solutions if item.get("incumbent_from_time_limit")
    )
    solver_meta = {
        "architecture": "enumerated_room_combinations_plus_global_milp_with_drones_plus_simulation_rerank",
        "backend": "scipy.optimize.milp_highs",
        "model": model_metadata,
        "candidate_library_complete": search_complete,
        "proxy_models_solved_to_gap": proxy_optimal,
        "accepted_time_limit_incumbents": accepted_time_limit_incumbents,
        "top_solutions_requested": top_solutions,
        "top_solutions_returned": len(solutions),
        "proxy_attempts": len(no_good),
        "proxy_attempt_minimum_policy": max(requested * 8, requested),
        "post_rejection_candidate_expansion_depth": _rejection_retry_depth,
        "synergy_bundle_ids_considered": [
            str(bundle.get("id") or "")
            for bundle in (library.get("synergy_bundles") or [])
            if bundle.get("id")
        ],
        "synergy_bundle_candidate_preservation": True,
        "adaptive_candidate_expansion": expansion_trace,
        "rejected_after_simulation": rejected_after_simulation,
        "optimality_claim": (
            "proxy_optimal_within_complete_candidate_library"
            if search_complete and proxy_optimal
            else "best_found_within_truncated_candidate_library"
        ),
        "actual_simulation_global_optimality_proven": False,
        "limitations": [
            "全局联动在求解后复算，实际模拟目标与MILP代理目标不完全相同。",
            "无人机已进入恢复、库存和分配模型；未知的当前龙门币订单仍使用期望值。",
            "未结构化特殊技能和随机订单序列不参与实际全局最优性证明。",
        ],
        "secondary_output_policy": "primary_metrics_first_then_free_battle_record_exp",
        "opportunity_cost_policy": "release_suppressed_effects_then_full_day_counterfactual_reassignment",
    }
    plan = _candidate_plan(context, library, selected["assignments"], selected["simulation"], solver_meta)
    output = {
        "schema_version": 2,
        "result_type": "hybrid_schedule_solution",
        "solved_at": utc_now(),
        "solver": solver_meta,
        "combination_library_summary": {
            "parameters": library.get("parameters"),
            "search_completeness": library.get("search_completeness"),
            "room_counts": {
                room_id: {
                    "enumerated": value.get("enumerated_count"),
                    "kept": value.get("kept_count"),
                    "truncated": value.get("truncated"),
                }
                for room_id, value in (library.get("rooms") or {}).items()
            },
        },
        "selected_solution": selected,
        "candidate_plan": plan,
        "alternatives": solutions,
    }
    output["reproducibility"] = build_manifest(
        run_type="hybrid_schedule_solve",
        extra={
            "context_sha256": canonical_json_hash(context),
            "combination_library_sha256": canonical_json_hash(library),
            "selected_plan_id": plan.get("plan_id"),
            "optimality_claim": solver_meta.get("optimality_claim"),
        },
    )
    plan["reproducibility"] = output["reproducibility"]
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="混合枚举与MILP全局求解")
    parser.add_argument("context")
    parser.add_argument("--combination-library")
    parser.add_argument("--write-combination-library")
    parser.add_argument("--output", required=True)
    parser.add_argument("--plan-output")
    parser.add_argument("--top-k", type=int, default=60)
    parser.add_argument("--operator-pool-size", type=int, default=14)
    parser.add_argument("--top-solutions", type=int, default=5)
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--mip-rel-gap", type=float, default=0.001)
    parser.add_argument("--max-proxy-attempts", type=int)
    args = parser.parse_args()

    context = read_json(args.context)
    library = read_json(args.combination_library) if args.combination_library else build_library(
        context,
        top_k=max(1, args.top_k),
        operator_pool_size=max(1, args.operator_pool_size),
        allow_partial=False,
    )
    if args.write_combination_library:
        write_json(args.write_combination_library, library)
    value = solve_hybrid(
        context,
        library=library,
        top_solutions=max(1, args.top_solutions),
        time_limit=max(0.01, args.time_limit),
        mip_rel_gap=max(0.0, args.mip_rel_gap),
        max_proxy_attempts=args.max_proxy_attempts,
    )
    write_json(args.output, value)
    if args.plan_output:
        write_json(args.plan_output, value["candidate_plan"])
    print(json.dumps({
        "output": str(Path(args.output)),
        "plan_output": str(Path(args.plan_output)) if args.plan_output else None,
        "optimality_claim": value["solver"]["optimality_claim"],
        "actual_objective_score": value["selected_solution"]["simulation"]["actual_objective_score"],
        "alternatives": len(value["alternatives"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
