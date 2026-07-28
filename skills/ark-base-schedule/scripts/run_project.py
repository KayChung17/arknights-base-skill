#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a complete optimization project from one Chinese JSON configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from audit_result import audit_result
from build_combinations import build_library
from coverage_report import build_coverage_report
from generate_report import generate_report
from normalize_input import build_decision_packet
from optimizer_common import write_json
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
    }


def _layout_kwargs(config: dict[str, Any], roster: Path) -> dict[str, Any]:
    objective = config.get("objective") or {}
    base_state = config.get("base_state") or {}
    search = config.get("search") or {}
    profile = config.get("profiles") or {}
    online_times = list(objective["online_times"])
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
        "max_orundum_trading_posts": objective.get("max_orundum_trading_posts"),
        "max_shard_factories": objective.get("max_shard_factories"),
        "lmd_proxy_floor_slack": float(search.get("lmd_proxy_floor_slack", 0.0)),
    })
    return kwargs


def _fixed_solve(config: dict[str, Any], roster: Path) -> dict[str, Any]:
    objective = config["objective"]
    layout = str(config.get("layout") or "custom")
    online_times = list(objective["online_times"])
    preferences = dict(config.get("preferences") or {})
    solver = dict(preferences.get("solver") or {})
    base_state = config["base_state"]
    solver["max_daily_work_hours"] = float(objective["max_daily_work_hours"])
    solver["drone_capacity"] = float(base_state["drone_capacity"])
    solver["initial_drone_stock"] = float(base_state["initial_drone_stock"])
    solver.setdefault("allocate_drones", True)
    solver.setdefault("drone_repeating_day_balance", True)
    preferences["solver"] = solver
    context = build_decision_packet(
        roster,
        str(objective["goal"]),
        layout,
        len(online_times),
        preferences,
        online_times,
    )
    context["facility_configuration"] = config["facility_configuration"]
    context["base_state"] = base_state
    context["horizon"] = config["horizon"]
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

    mode = str(config["mode"])
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
    else:  # preflight already rejects this; retain defensive branch.
        raise ValueError("mode 必须是 layout_search、upgrade_search 或 fixed_schedule")

    result["project"] = {
        "name": config.get("project_name") or path.stem,
        "mode": mode,
        "configuration_file": path.name,
        "horizon": config["horizon"],
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

    coverage = build_coverage_report(roster)
    coverage["_run"] = {"run_id": run_id, "config_sha256": config_hash}
    result["project_data_coverage"] = {
        "roster": coverage["roster"],
        "unlocked_skill_coverage": coverage["unlocked_skill_coverage"],
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
    report_path.write_text(report, encoding="utf-8")
    summary = {
        "project_name": result["project"]["name"],
        "mode": mode,
        "run_id": run_id,
        "config_sha256": config_hash,
        "result": str(result_path),
        "report": str(report_path),
        "audit": str(audit_path),
        "coverage": str(coverage_path),
        "pareto": str(pareto_path),
        "configuration": str(config_copy),
        "preflight": str(preflight_path),
        "manifest": str(manifest_path),
        "verification": str(verification_path),
        "audit_status": audit["status"],
        "operator_data_coverage_ratio": coverage["roster"]["operator_coverage_ratio"],
        "verification_status": "not_run",
    }
    write_json(summary_path, summary)
    manifest = _artifact_manifest(
        destination,
        run_id,
        config_hash,
        ["preflight.json", "result.json", "audit.json", "coverage.json", "pareto.json", "report.md", "config.resolved.json"],
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
    if not args.skip_verify and summary["verification_status"] != "passed":
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
