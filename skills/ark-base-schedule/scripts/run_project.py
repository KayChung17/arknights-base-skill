#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a complete optimization project from one Chinese JSON configuration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from audit_result import audit_result
from build_combinations import build_library
from generate_report import generate_report
from coverage_report import build_coverage_report
from normalize_input import build_decision_packet
from optimizer_common import write_json
from reproducibility import build_manifest
from search_layouts import search_layouts
from search_upgrades import run_upgrade_search
from solve_schedule import solve_hybrid


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
    }


def _layout_kwargs(config: dict[str, Any], roster: Path) -> dict[str, Any]:
    objective = config.get("objective") or {}
    base_state = config.get("base_state") or {}
    search = config.get("search") or {}
    profile = config.get("profiles") or {}
    online_times = list(objective.get("online_times") or ["08:00", "14:00", "20:00"])
    kwargs = _solver_options(config)
    kwargs.update({
        "roster_path": roster,
        "online_times": online_times,
        "lmd_floor": float(objective.get("minimum_net_lmd_per_day", 0.0)),
        "minimum_shard_balance": float(objective.get("minimum_orundum_shard_balance", 0.0)),
        "minimum_gold_balance": float(objective.get("minimum_pure_gold_balance", 0.0)),
        "max_daily_work_hours": float(objective.get("max_daily_work_hours", 18.0)),
        "profiles": profile.get("ids"),
        "profile_mode": str(profile.get("mode", "representative")),
        "profiles_file": profile.get("file"),
        "grid_layouts": profile.get("layouts"),
        "max_profiles": profile.get("max_profiles"),
        "dorm_levels": base_state.get("dormitory_levels"),
        "right_side_levels": base_state.get("right_side_levels"),
        "drone_capacity": float(base_state.get("drone_capacity", 235.0)),
        "initial_drone_stock": base_state.get("initial_drone_stock"),
        "max_orundum_trading_posts": objective.get("max_orundum_trading_posts"),
        "max_shard_factories": objective.get("max_shard_factories"),
        "lmd_proxy_floor_slack": float(search.get("lmd_proxy_floor_slack", 0.0)),
    })
    return kwargs


def _fixed_solve(config: dict[str, Any], roster: Path) -> dict[str, Any]:
    objective = config.get("objective") or {}
    layout = str(_require(config, "layout"))
    online_times = list(objective.get("online_times") or ["08:00", "14:00", "20:00"])
    preferences = dict(config.get("preferences") or {})
    solver = dict(preferences.get("solver") or {})
    base_state = config.get("base_state") or {}
    solver.setdefault("max_daily_work_hours", float(objective.get("max_daily_work_hours", 18.0)))
    solver.setdefault("drone_capacity", float(base_state.get("drone_capacity", 235.0)))
    solver.setdefault("initial_drone_stock", float(base_state.get("initial_drone_stock", solver["drone_capacity"])))
    solver.setdefault("allocate_drones", True)
    solver.setdefault("drone_repeating_day_balance", True)
    preferences["solver"] = solver
    context = build_decision_packet(
        roster,
        str(objective.get("goal", "赚钱+搓玉")),
        layout,
        len(online_times),
        preferences,
        online_times,
    )
    if config.get("facility_configuration"):
        context["facility_configuration"] = config["facility_configuration"]
    context["base_state"] = base_state
    search = _solver_options(config)
    library = build_library(
        context,
        top_k=search["top_k"],
        operator_pool_size=search["operator_pool_size"],
        allow_partial=False,
    )
    return solve_hybrid(
        context,
        library=library,
        top_k=search["top_k"],
        operator_pool_size=search["operator_pool_size"],
        top_solutions=int((config.get("search") or {}).get("top_solutions", 5)),
        time_limit=search["time_limit"],
        mip_rel_gap=float((config.get("search") or {}).get("mip_rel_gap", 0.001)),
        max_proxy_attempts=search["max_proxy_attempts"],
    )


def run_project(config_path: str | Path, *, output_dir: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    if int(config.get("schema_version", 1)) != 1:
        raise ValueError("当前只支持 schema_version=1")
    base = path.parent
    roster = _resolve(base, _require(config, "roster"))
    if roster is None or not roster.exists():
        raise FileNotFoundError(f"干员表不存在: {roster}")
    destination = Path(output_dir or config.get("output_dir") or (base / "arkbase-output")).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    mode = str(config.get("mode", "layout_search"))

    if mode == "layout_search":
        kwargs = _layout_kwargs(config, roster)
        if kwargs.get("profiles_file"):
            kwargs["profiles_file"] = _resolve(base, kwargs["profiles_file"])
        result = search_layouts(**kwargs)
    elif mode == "upgrade_search":
        kwargs = _layout_kwargs(config, roster)
        work_dir = destination / "upgrade-work"
        result = run_upgrade_search(
            roster,
            online_times=kwargs["online_times"],
            lmd_floor=kwargs["lmd_floor"],
            profiles=kwargs["profiles"],
            max_daily_work_hours=kwargs["max_daily_work_hours"],
            top_k=kwargs["top_k"],
            operator_pool_size=kwargs["operator_pool_size"],
            time_limit=kwargs["time_limit"],
            max_proxy_attempts=kwargs["max_proxy_attempts"],
            work_dir=work_dir,
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
            marginal_limit=int((config.get("upgrades") or {}).get("marginal_limit", 0)),
        )
    elif mode == "fixed_schedule":
        result = _fixed_solve(config, roster)
    else:
        raise ValueError("mode 必须是 layout_search、upgrade_search 或 fixed_schedule")

    result["project"] = {
        "name": config.get("project_name") or path.stem,
        "mode": mode,
        "configuration_file": path.name,
    }
    result["project_reproducibility"] = build_manifest(
        run_type=f"project:{mode}",
        extra={"output_dir": destination.name},
    )
    coverage = build_coverage_report(roster)
    result["project_data_coverage"] = {
        "roster": coverage["roster"],
        "unlocked_skill_coverage": coverage["unlocked_skill_coverage"],
    }
    audit = audit_result(result)
    report = generate_report(result)
    result_path = destination / "result.json"
    audit_path = destination / "audit.json"
    coverage_path = destination / "coverage.json"
    report_path = destination / "report.md"
    config_copy = destination / "config.resolved.json"
    write_json(result_path, result)
    write_json(audit_path, audit)
    write_json(coverage_path, coverage)
    write_json(config_copy, config)
    report_path.write_text(report, encoding="utf-8")
    summary = {
        "project_name": result["project"]["name"],
        "mode": mode,
        "result": str(result_path),
        "report": str(report_path),
        "audit": str(audit_path),
        "coverage": str(coverage_path),
        "audit_status": audit["status"],
        "operator_data_coverage_ratio": coverage["roster"]["operator_coverage_ratio"],
    }
    write_json(destination / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="从单个JSON配置运行完整基建优化项目")
    parser.add_argument("config")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    summary = run_project(args.config, output_dir=args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["audit_status"] != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
