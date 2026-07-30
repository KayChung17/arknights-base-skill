#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a complete optimization project from one Chinese JSON configuration."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from audit_result import audit_result
from build_combinations import build_library
from coverage_report import build_coverage_report, build_relevant_unmodeled_report
from export_schedule_template import export_schedule
from generate_report import generate_report
from layout_profiles import facility_configuration_power_summary, fixed_right_power_consumption
from normalize_input import build_decision_packet
from online_schedule import candidate_online_times, format_minutes
from optimizer_common import read_json, write_json
from pareto_frontier import build_pareto_frontier
from preflight import PreflightError, canonical_config, config_sha256, preflight_project
from reproducibility import build_manifest
from search_layouts import search_layouts
from search_upgrades import run_upgrade_search
from solve_schedule import solve_hybrid
from verify_output import verify_output


def _resolve(base: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _require(config: dict[str, Any], key: str) -> Any:
    if key not in config:
        raise ValueError(f"配置缺少必填字段: {key}")
    return config[key]


def _solver_options(config: dict[str, Any]) -> dict[str, Any]:
    search = config.get("search") or {}
    return {
        "top_k": int(search.get("top_k", 30)),
        "operator_pool_size": int(search.get("operator_pool_size", 12)),
        "time_limit": float(search.get("time_limit_seconds", 12.0)),
        "max_proxy_attempts": int(search.get("max_proxy_attempts", 4)),
        "mip_rel_gap": float(search.get("mip_rel_gap", 0.01)),
    }


def _layout_kwargs(config: dict[str, Any], roster: Path) -> dict[str, Any]:
    objective = config.get("objective") or {}
    base_state = config.get("base_state") or {}
    search = config.get("search") or {}
    profile = config.get("profiles") or {}
    online_times = list(objective["online_times"])
    balance_policy = objective.get("balance_policy") or {}
    kwargs = _solver_options(config)
    kwargs.update({
        "roster_path": roster,
        "online_times": online_times,
        "lmd_floor": float(objective["minimum_net_lmd_per_day"]),
        "minimum_shard_balance": float(objective["minimum_originium_shard_balance"]),
        "minimum_gold_balance": float(objective["minimum_pure_gold_balance"]),
        "max_daily_work_hours": float(objective["max_daily_work_hours"]),
        "profiles": profile.get("ids"),
        "profile_mode": str(profile.get("mode", "representative")),
        "profiles_file": profile.get("file"),
        "grid_layouts": profile.get("layouts"),
        "max_profiles": profile.get("max_profiles"),
        "dorm_levels": base_state["dormitory_levels"],
        "right_side_levels": base_state["right_side_levels"],
        "drone_capacity": float(base_state["drone_capacity"]),
        "initial_drone_stock": float(base_state["initial_drone_stock"]),
        "inventory": dict(base_state.get("inventory") or {}),
        "horizon": dict(config.get("horizon") or {}),
        "economic_values": dict(objective.get("economic_values") or {}),
        "max_orundum_trading_posts": objective.get("max_orundum_trading_posts"),
        "max_shard_factories": objective.get("max_shard_factories"),
        "minimum_battle_record_factories": int(objective.get("minimum_battle_record_factories", 0)),
        "lmd_proxy_floor_slack": float(search.get("lmd_proxy_floor_slack", 0.0)),
        "proxy_shard_consumption_factor": float(search.get("proxy_shard_consumption_factor", 1.0)),
        "proxy_gold_consumption_factor": float(search.get("proxy_gold_consumption_factor", 1.0)),
        "proxy_lmd_cost_factor": float(search.get("proxy_lmd_cost_factor", 1.0)),
        "opportunity_postprocess_max_iterations": int(search.get("opportunity_postprocess_max_iterations", 4)),
        "operator_overrides": config.get("operator_overrides"),
        "right_side_schedule": config["right_side_schedule"],
        "shard_balance_policy": dict(balance_policy.get("originium_shard") or {"mode": "hard"}),
        "gold_balance_policy": dict(balance_policy.get("pure_gold") or {"mode": "hard"}),
        "allow_product_switching": bool(search.get("allow_product_switching", True)),
        "adaptive_candidate_expansion_rounds": int(search.get("adaptive_candidate_expansion_rounds", 2)),
        "adaptive_candidate_expansion_factor": float(search.get("adaptive_candidate_expansion_factor", 2.0)),
        "adaptive_candidate_max_top_k": search.get("adaptive_candidate_max_top_k"),
        "adaptive_candidate_max_operator_pool_size": search.get("adaptive_candidate_max_operator_pool_size"),
        "random_order_trials": int(search.get("random_order_trials", 0)),
        "random_order_seed": int(search.get("random_order_seed", 20260730)),
    })
    return kwargs


def _fixed_solve(config: dict[str, Any], roster: Path) -> dict[str, Any]:
    objective = config["objective"]
    layout = str(config.get("layout") or "custom")
    online_times = list(objective["online_times"])
    preferences = dict(config.get("preferences") or {})
    solver = dict(preferences.get("solver") or {})
    base_state = config["base_state"]
    power = facility_configuration_power_summary(
        config["facility_configuration"],
        right_side_levels=base_state["right_side_levels"],
        expected_layout=layout,
    )
    if power["spare_power"] < -1e-9:
        raise ValueError(f"固定排班缺电 {-power['spare_power']:.0f}，不能进入求解器")
    solver["max_daily_work_hours"] = float(objective["max_daily_work_hours"])
    balance_policy = objective.get("balance_policy") or {}
    shard_policy = dict(balance_policy.get("originium_shard") or {"mode": "hard"})
    gold_policy = dict(balance_policy.get("pure_gold") or {"mode": "hard"})
    solver["orundum_shard_balance_mode"] = str(shard_policy.get("mode", "hard"))
    solver["orundum_shard_shortfall_penalty"] = float(shard_policy.get("shortfall_penalty", 0.0))
    solver["hard_minimum_orundum_shard_balance"] = shard_policy.get("hard_minimum")
    solver["pure_gold_balance_mode"] = str(gold_policy.get("mode", "hard"))
    solver["pure_gold_shortfall_penalty"] = float(gold_policy.get("shortfall_penalty", 0.0))
    solver["hard_minimum_pure_gold_balance"] = gold_policy.get("hard_minimum")
    solver["drone_capacity"] = float(base_state["drone_capacity"])
    solver["initial_drone_stock"] = float(base_state["initial_drone_stock"])
    solver.setdefault("allocate_drones", True)
    solver.setdefault("drone_repeating_day_balance", True)
    solver.setdefault("require_dormitory_cycle", True)
    solver.setdefault("forbid_drone_waste", True)
    solver.setdefault("empty_drone_inventory_at_each_node", True)
    preferences["solver"] = solver
    context = build_decision_packet(
        roster,
        str(objective["goal"]),
        layout,
        len(online_times),
        preferences,
        online_times,
        config.get("operator_overrides"),
        online_schedule=objective.get("online_schedule"),
    )
    context["facility_configuration"] = config["facility_configuration"]
    context["base_state"] = {
        **base_state,
        "power": power,
        "fixed_right_side_levels": dict(base_state["right_side_levels"]),
        "right_side_levels_immutable": True,
    }
    context["horizon"] = config["horizon"]
    context["right_side_schedule"] = config["right_side_schedule"]
    search = _solver_options(config)
    library = build_library(
        context,
        top_k=search["top_k"],
        operator_pool_size=search["operator_pool_size"],
        allow_partial=False,
    )
    result = solve_hybrid(
        context,
        library=library,
        top_k=search["top_k"],
        operator_pool_size=search["operator_pool_size"],
        top_solutions=int((config.get("search") or {}).get("top_solutions", 5)),
        time_limit=search["time_limit"],
        mip_rel_gap=float((config.get("search") or {}).get("mip_rel_gap", 0.001)),
        max_proxy_attempts=search["max_proxy_attempts"],
    )
    result["fixed_schedule_power"] = power
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_manifest(destination: Path, run_id: str, config_hash: str, names: list[str]) -> dict[str, Any]:
    return {
        "manifest_schema_version": 1,
        "run_id": run_id,
        "config_sha256": config_hash,
        "artifacts": {name: _sha256(destination / name) for name in names},
    }


def _execute_mode(config: dict[str, Any], roster: Path, destination: Path, base: Path) -> dict[str, Any]:
    mode = str(config["mode"])
    if mode == "layout_search":
        kwargs = _layout_kwargs(config, roster)
        if kwargs.get("profiles_file"):
            kwargs["profiles_file"] = _resolve(base, kwargs["profiles_file"])
        return search_layouts(**kwargs)
    if mode == "upgrade_search":
        kwargs = _layout_kwargs(config, roster)
        return run_upgrade_search(
            roster,
            online_times=kwargs["online_times"],
            lmd_floor=kwargs["lmd_floor"],
            profiles=kwargs["profiles"],
            max_daily_work_hours=kwargs["max_daily_work_hours"],
            top_k=kwargs["top_k"],
            operator_pool_size=kwargs["operator_pool_size"],
            time_limit=kwargs["time_limit"],
            max_proxy_attempts=kwargs["max_proxy_attempts"],
            work_dir=destination / "upgrade-work",
            profile_mode=kwargs["profile_mode"],
            profiles_file=_resolve(base, kwargs["profiles_file"]) if kwargs.get("profiles_file") else None,
            grid_layouts=kwargs["grid_layouts"],
            max_profiles=kwargs["max_profiles"],
            dorm_levels=kwargs["dorm_levels"],
            right_side_levels=kwargs["right_side_levels"],
            drone_capacity=kwargs["drone_capacity"],
            initial_drone_stock=kwargs["initial_drone_stock"],
            minimum_shard_balance=kwargs["minimum_shard_balance"],
            minimum_gold_balance=kwargs["minimum_gold_balance"],
            proxy_shard_consumption_factor=kwargs["proxy_shard_consumption_factor"],
            proxy_gold_consumption_factor=kwargs["proxy_gold_consumption_factor"],
            proxy_lmd_cost_factor=kwargs["proxy_lmd_cost_factor"],
            opportunity_postprocess_max_iterations=kwargs["opportunity_postprocess_max_iterations"],
            right_side_schedule=kwargs["right_side_schedule"],
            marginal_limit=int((config.get("upgrades") or {}).get("marginal_limit", 0)),
        )
    if mode == "fixed_schedule":
        return _fixed_solve(config, roster)
    raise ValueError("mode 必须是 layout_search、upgrade_search 或 fixed_schedule")


def _result_score(result: dict[str, Any]) -> tuple[float, float, float]:
    selected = result.get("selected") or {}
    if selected:
        return (
            float(selected.get("economic_utility_lmd_per_day", selected.get("actual_objective_score", 0.0)) or 0.0),
            float(selected.get("net_lmd_per_day", 0.0) or 0.0),
            float(selected.get("orundum_per_day", 0.0) or 0.0),
        )
    simulation = ((result.get("selected_solution") or {}).get("simulation") or {})
    aggregate = simulation.get("aggregate_metrics") or {}
    return (
        float(simulation.get("actual_objective_score", 0.0) or 0.0),
        float(aggregate.get("lmd", 0.0) or 0.0) - float(aggregate.get("lmd_cost", 0.0) or 0.0),
        float(aggregate.get("orundum", 0.0) or 0.0),
    )


def run_project(
    config_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    strict_input: bool = True,
    auto_verify: bool = True,
) -> dict[str, Any]:
    path = Path(config_path).resolve()
    preflight = preflight_project(path, strict=strict_input)
    config = preflight["resolved_config"]
    base = path.parent
    destination = Path(output_dir or config.get("output_dir") or (base / "arkbase-output")).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    preflight_path = destination / "preflight.json"
    write_json(preflight_path, preflight)
    if preflight["status"] != "ready":
        raise PreflightError(preflight)
    if (config.get("horizon") or {}).get("mode") != "steady_state":
        blocked = dict(preflight)
        blocked["status"] = "execution_blocked"
        blocked.setdefault("conflicts", []).append({
            "path": "/horizon/mode",
            "code": "finite_days_solver_not_implemented",
            "message": "当前版本尚未实现有限天数库存轨迹求解，已阻止把 finite_days 当作重复日稳态执行。",
        })
        write_json(preflight_path, blocked)
        raise PreflightError(blocked)

    roster = _resolve(base, _require(config, "roster"))
    if roster is None or not roster.exists():
        raise FileNotFoundError(f"干员表不存在: {roster}")

    run_id = uuid.uuid4().hex
    config_hash = config_sha256(config)
    preflight["_run"] = {"run_id": run_id, "config_sha256": config_hash}
    write_json(preflight_path, preflight)

    unmodeled = build_relevant_unmodeled_report(roster, config)
    unmodeled["_run"] = {"run_id": run_id, "config_sha256": config_hash}
    unmodeled_path = destination / "unmodeled-relevant-skills.json"
    write_json(unmodeled_path, unmodeled)
    if unmodeled.get("policy") == "block" and int(unmodeled.get("blocking_count", 0) or 0) > 0:
        blocked = dict(preflight)
        blocked["status"] = "execution_blocked"
        blocked.setdefault("conflicts", []).append({
            "path": "/verification/relevant_unmodeled_skill_policy",
            "code": "relevant_unmodeled_skills",
            "message": f"本次设施与产品范围仍有 {unmodeled['blocking_count']} 条高风险未结构化技能。",
            "report": str(unmodeled_path),
        })
        write_json(preflight_path, blocked)
        raise PreflightError(blocked)

    mode = str(config["mode"])
    schedule_policy = dict((config.get("objective") or {}).get("online_schedule") or {})
    if str(schedule_policy.get("mode", "fixed")) == "optimize":
        candidates = candidate_online_times(
            int(schedule_policy.get("count", len(config["objective"]["online_times"]))),
            mode="optimize",
            step_minutes=int(schedule_policy.get("candidate_step_minutes", 60)),
            max_candidates=int(schedule_policy.get("max_candidates", 48)),
        )
        evaluated: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        best: tuple[tuple[float, float, float], dict[str, Any], list[str]] | None = None
        evaluated_times: set[tuple[str, ...]] = set()

        def evaluate_times(times: list[str], source: str) -> None:
            nonlocal best
            key = tuple(times)
            if key in evaluated_times:
                return
            evaluated_times.add(key)
            candidate_config = copy.deepcopy(config)
            candidate_config["objective"]["online_times"] = list(times)
            try:
                candidate_result = _execute_mode(candidate_config, roster, destination, base)
                score = _result_score(candidate_result)
                evaluated.append({"online_times": list(times), "score": score[0], "tie_break": list(score[1:]), "source": source})
                if best is None or score > best[0]:
                    best = (score, candidate_result, list(times))
            except Exception as exc:
                failures.append({"online_times": list(times), "reason": str(exc), "source": source})

        for times in candidates:
            evaluate_times(list(times), "global_grid")
        if best is None:
            raise RuntimeError(f"所有上线时间候选均求解失败: {failures[:5]}")
        refinement_budget = max(0, int(schedule_policy.get("refinement_max_candidates", 36)))
        refinement_count = 0
        refinement_step = max(1, int(schedule_policy.get("candidate_step_minutes", 60)) // 2)
        while bool(schedule_policy.get("continuous_refinement", True)) and refinement_budget > 0:
            center = [int(value[:2]) * 60 + int(value[3:]) for value in best[2]]
            neighbors: list[list[str]] = []
            for index in range(len(center)):
                for direction in (-1, 1):
                    shifted = list(center)
                    shifted[index] = (shifted[index] + direction * refinement_step) % (24 * 60)
                    formatted = [format_minutes(value) for value in shifted]
                    cyclic_span = sum(
                        (shifted[(item + 1) % len(shifted)] - shifted[item]) % (24 * 60)
                        for item in range(len(shifted))
                    )
                    if len(set(formatted)) == len(formatted) and cyclic_span == 24 * 60:
                        neighbors.append(formatted)
            for times in neighbors:
                if refinement_budget <= 0:
                    break
                before = len(evaluated_times)
                evaluate_times(times, f"local_refinement_{refinement_step}m")
                if len(evaluated_times) > before:
                    refinement_budget -= 1
                    refinement_count += 1
            if refinement_step == 1:
                break
            refinement_step = max(1, refinement_step // 2)
        result = best[1]
        config["objective"]["online_times"] = best[2]
        result["online_time_search"] = {
            "mode": "discrete_outer_search",
            "selected_online_times": best[2],
            "candidate_step_minutes": int(schedule_policy.get("candidate_step_minutes", 60)),
            "evaluated_count": len(evaluated),
            "failed_count": len(failures),
            "candidate_limit": int(schedule_policy.get("max_candidates", 48)),
            "minute_level_refinement_enabled": bool(schedule_policy.get("continuous_refinement", True)),
            "refinement_evaluated_count": refinement_count,
            "time_resolution_minutes": refinement_step,
            "evaluated": sorted(evaluated, key=lambda item: (item["score"], item["tie_break"]), reverse=True),
            "failures": failures,
        }
    else:
        result = _execute_mode(config, roster, destination, base)

    selected_power = (
        ((result.get("selected") or {}).get("power") or {})
        if mode != "fixed_schedule"
        else (result.get("fixed_schedule_power") or {})
    )
    result["project"] = {
        "name": config.get("project_name") or path.stem,
        "mode": mode,
        "configuration_file": path.name,
        "horizon": config["horizon"],
        "inventory": dict((config.get("base_state") or {}).get("inventory") or {}),
        "right_side_levels": dict(config["base_state"]["right_side_levels"]),
        "right_side_levels_confirmed": bool(config["base_state"]["right_side_levels_confirmed"]),
        "right_side_levels_immutable": True,
        "fixed_right_power_consumption": fixed_right_power_consumption(config["base_state"]["right_side_levels"]),
        "selected_power": selected_power,
        "right_side_schedule": config["right_side_schedule"],
    }
    result["project_execution"] = {
        "run_id": run_id,
        "config_sha256": config_hash,
        "strict_input": bool(strict_input),
        "preflight_status": preflight["status"],
    }
    result["project_reproducibility"] = build_manifest(
        run_type=f"project:{mode}",
        extra={"output_dir": destination.name, "run_id": run_id, "config_sha256": config_hash},
    )

    coverage = build_coverage_report(roster, config.get("operator_overrides"))
    coverage["_run"] = {"run_id": run_id, "config_sha256": config_hash}
    result["project_data_coverage"] = {
        "roster": coverage["roster"],
        "unlocked_skill_coverage": coverage["unlocked_skill_coverage"],
        "relevant_unmodeled_skills": {
            "policy": unmodeled["policy"],
            "unmodeled_count": unmodeled["unmodeled_count"],
            "blocking_count": unmodeled["blocking_count"],
            "warning_count": unmodeled["warning_count"],
        },
    }
    pareto = build_pareto_frontier(result)
    audit = audit_result(result)
    audit["_run"] = {"run_id": run_id, "config_sha256": config_hash}
    report = generate_report(result)

    result_path = destination / "result.json"
    audit_path = destination / "audit.json"
    coverage_path = destination / "coverage.json"
    report_path = destination / "report.md"
    pareto_path = destination / "pareto.json"
    config_copy = destination / "config.resolved.json"
    summary_path = destination / "summary.json"
    manifest_path = destination / "run-manifest.json"
    verification_path = destination / "verification.json"
    schedule_path = destination / "schedule.json"

    resolved = canonical_config(config)
    resolved["_resolution"] = {
        "run_id": run_id,
        "config_sha256": config_hash,
        "strict_input": bool(strict_input),
        "source_map": preflight["source_map"],
        "assumptions": preflight["assumptions"],
        "deprecations": preflight["deprecations"],
    }

    write_json(result_path, result)
    write_json(audit_path, audit)
    write_json(coverage_path, coverage)
    write_json(pareto_path, pareto)
    write_json(config_copy, resolved)
    write_json(schedule_path, export_schedule(result))
    report_path.write_text(report, encoding="utf-8")
    summary = {
        "project_name": result["project"]["name"],
        "mode": mode,
        "run_id": run_id,
        "config_sha256": config_hash,
        "result": str(result_path),
        "report": str(report_path),
        "schedule": str(schedule_path),
        "audit": str(audit_path),
        "coverage": str(coverage_path),
        "pareto": str(pareto_path),
        "configuration": str(config_copy),
        "preflight": str(preflight_path),
        "manifest": str(manifest_path),
        "verification": str(verification_path),
        "unmodeled_relevant_skills": str(unmodeled_path),
        "audit_status": audit["status"],
        "operator_data_coverage_ratio": coverage["roster"]["operator_coverage_ratio"],
        "verification_status": "not_run",
    }
    write_json(summary_path, summary)
    manifest = _artifact_manifest(
        destination,
        run_id,
        config_hash,
        [
            "preflight.json", "result.json", "audit.json", "coverage.json", "pareto.json",
            "report.md", "schedule.json", "config.resolved.json", "unmodeled-relevant-skills.json",
        ],
    )
    write_json(manifest_path, manifest)

    if auto_verify:
        verification_policy = config.get("verification") or {}
        verification = verify_output(
            destination,
            strict_warnings=bool(verification_policy.get("strict_warnings", True)),
            stability_check=bool(verification_policy.get("stability_check", False)),
            expanded_factor=float(verification_policy.get("expanded_factor", 2.0)),
        )
        write_json(verification_path, verification)
        summary["verification_status"] = verification["status"]
        write_json(summary_path, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="从单个JSON配置运行完整基建优化项目")
    parser.add_argument("config")
    parser.add_argument("--output-dir")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--strict-input", action="store_true", default=True)
    mode.add_argument("--allow-defaults", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    args = parser.parse_args()
    try:
        summary = run_project(
            args.config,
            output_dir=args.output_dir,
            strict_input=not args.allow_defaults,
            auto_verify=not args.skip_verify,
        )
    except PreflightError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        return 4
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["audit_status"] == "failed":
        return 2
    if not args.skip_verify:
        verification_policy = (read_json(args.config).get("verification") or {})
        allowed = {"passed"}
        if not bool(verification_policy.get("strict_warnings", True)):
            allowed.add("passed_with_warnings")
        if summary["verification_status"] not in allowed:
            return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
