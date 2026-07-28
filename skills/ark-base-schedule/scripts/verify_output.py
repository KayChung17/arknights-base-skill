#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify that one arkbase output directory is complete, bound and release-safe."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any

CORE_FILES = (
    "preflight.json",
    "result.json",
    "audit.json",
    "coverage.json",
    "pareto.json",
    "report.md",
    "config.resolved.json",
    "summary.json",
    "run-manifest.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _check(checks: list[dict[str, Any]], code: str, ok: bool, message: str, severity: str = "error") -> None:
    checks.append({"code": code, "ok": bool(ok), "severity": severity, "message": message})


def extract_core_metrics(result: dict[str, Any]) -> dict[str, float]:
    selected = result.get("selected") or {}
    if not selected and result.get("selected_solution"):
        simulation = (result.get("selected_solution") or {}).get("simulation") or {}
        aggregate = simulation.get("aggregate_metrics") or {}
        selected = {
            "orundum_per_day": aggregate.get("orundum", 0.0),
            "net_lmd_per_day": simulation.get("net_lmd_balance", 0.0),
            "originium_shard_balance": simulation.get("originium_shard_balance", simulation.get("orundum_shard_balance", 0.0)),
            "pure_gold_balance": simulation.get("pure_gold_balance", 0.0),
        }
    metrics = {
        "orundum_per_day": selected.get("orundum_per_day", 0.0),
        "net_lmd_per_day": selected.get("net_lmd_per_day", 0.0),
        "originium_shard_balance": selected.get("originium_shard_balance", selected.get("orundum_shard_balance", 0.0)),
        "pure_gold_balance": selected.get("pure_gold_balance", 0.0),
    }
    output: dict[str, float] = {}
    for key, value in metrics.items():
        try:
            output[key] = float(value or 0.0)
        except (TypeError, ValueError):
            output[key] = math.nan
    return output



def _collect_selected_operator_names(result: dict[str, Any]) -> set[str]:
    root = result.get("selected") or result.get("selected_solution") or ((result.get("search_details") or {}).get("targeted_minimum_unlocks") or {}).get("selected") or {}
    names: set[str] = set()

    def walk(value: Any, key_hint: str = "", depth: int = 0) -> None:
        if depth > 12:
            return
        hint = key_hint.lower()
        if isinstance(value, str):
            if any(token in hint for token in ("operator", "member", "worker", "staff")) and value.strip():
                names.add(value.strip())
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and any(token in hint for token in ("operator", "member", "worker", "staff", "team")):
                    names.add(item.strip())
                else:
                    walk(item, key_hint, depth + 1)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, str(key), depth + 1)

    walk(root)
    return {name for name in names if name}

def _selected_present(result: dict[str, Any]) -> bool:
    if result.get("selected") is not None or result.get("selected_solution") is not None:
        return True
    details = result.get("search_details") or {}
    return ((details.get("targeted_minimum_unlocks") or {}).get("selected")) is not None


def _stability_check(output_dir: Path, factor: float) -> dict[str, Any]:
    config_path = output_dir / "config.resolved.json"
    config = _load(config_path)
    config.pop("_resolution", None)
    search = config.setdefault("search", {})
    search["top_k"] = max(1, int(math.ceil(float(search.get("top_k", 30)) * factor)))
    search["operator_pool_size"] = max(1, int(math.ceil(float(search.get("operator_pool_size", 12)) * factor)))
    search["time_limit_seconds"] = max(1.0, float(search.get("time_limit_seconds", 12.0)) * factor)
    with tempfile.TemporaryDirectory(prefix="arkbase-stability-") as tmp:
        tmp_path = Path(tmp)
        rerun_config = tmp_path / "project.json"
        roster = Path(config["roster"])
        if not roster.is_absolute():
            original = Path((_load(output_dir / "preflight.json")).get("configuration_file", "")).resolve()
            config["roster"] = str((original.parent / roster).resolve())
        rerun_config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            from run_project import run_project
            summary = run_project(rerun_config, output_dir=tmp_path / "output", strict_input=True, auto_verify=False)
            rerun = _load(Path(summary["result"]))
        except Exception as exc:  # pragma: no cover - integration path
            return {"status": "failed", "error": f"扩大候选复算失败: {exc}"}
    original = extract_core_metrics(_load(output_dir / "result.json"))
    expanded = extract_core_metrics(rerun)
    deltas = {key: expanded[key] - original[key] for key in original}
    tolerance = 1e-6
    ranking_stable = all(abs(value) <= tolerance for value in deltas.values())
    return {
        "status": "passed" if ranking_stable else "changed",
        "expanded_factor": factor,
        "ranking_stable": ranking_stable,
        "original_metrics": original,
        "expanded_metrics": expanded,
        "metric_deltas": deltas,
    }


def verify_output(
    output_dir: str | Path,
    *,
    strict_warnings: bool = True,
    stability_check: bool = False,
    expanded_factor: float = 2.0,
) -> dict[str, Any]:
    root = Path(output_dir).resolve()
    checks: list[dict[str, Any]] = []
    for name in CORE_FILES:
        _check(checks, f"file:{name}", (root / name).is_file(), f"输出目录必须包含 {name}。")
    if any(not item["ok"] for item in checks):
        return _finish(checks, root, stability=None)

    preflight = _load(root / "preflight.json")
    result = _load(root / "result.json")
    audit = _load(root / "audit.json")
    coverage = _load(root / "coverage.json")
    config = _load(root / "config.resolved.json")
    summary = _load(root / "summary.json")
    manifest = _load(root / "run-manifest.json")

    _check(checks, "preflight_ready", preflight.get("status") == "ready", "预检状态必须为 ready。")
    _check(checks, "selected_candidate_present", _selected_present(result), "结果必须包含求解器选中的候选。")
    allowed_audit = {"passed"} if strict_warnings else {"passed", "passed_with_warnings"}
    _check(checks, "audit_release_gate", audit.get("status") in allowed_audit, f"审计状态 {audit.get('status')} 未通过发布门禁。")

    run_ids = {
        (result.get("project_execution") or {}).get("run_id"),
        (audit.get("_run") or {}).get("run_id"),
        (coverage.get("_run") or {}).get("run_id"),
        (config.get("_resolution") or {}).get("run_id"),
        summary.get("run_id"),
        manifest.get("run_id"),
    }
    run_ids.discard(None)
    _check(checks, "single_run_binding", len(run_ids) == 1, f"运行产物必须绑定同一 run_id，当前为 {sorted(run_ids)}。")

    config_hashes = {
        (result.get("project_execution") or {}).get("config_sha256"),
        (config.get("_resolution") or {}).get("config_sha256"),
        summary.get("config_sha256"),
        manifest.get("config_sha256"),
        preflight.get("config_sha256"),
    }
    config_hashes.discard(None)
    _check(checks, "single_config_binding", len(config_hashes) == 1, "所有产物必须绑定同一配置哈希。")

    for name, expected in (manifest.get("artifacts") or {}).items():
        artifact = root / name
        _check(checks, f"hash:{name}", artifact.is_file() and _sha256(artifact) == expected, f"{name} 的内容哈希必须与运行清单一致。")

    roster = coverage.get("roster") or {}
    ratio = float(roster.get("operator_coverage_ratio", 0.0) or 0.0)
    _check(checks, "operator_name_coverage", ratio >= 1.0 - 1e-9, f"干员名称覆盖率必须为100%，当前为 {ratio:.2%}。")
    policy = config.get("verification") or {}
    selected_names = _collect_selected_operator_names(result)
    known_rows = {str(item.get("operator")): bool(item.get("known")) for item in coverage.get("operators") or []}
    unknown_selected = sorted(name for name in selected_names if name in known_rows and not known_rows[name])
    _check(checks, "selected_operator_coverage", not unknown_selected, f"入选方案含未收录干员: {unknown_selected}")
    description_ops = {str(item.get("operator")) for item in coverage.get("description_only_examples") or []}
    unstructured_selected = sorted(selected_names & description_ops)
    _check(checks, "selected_skill_structure_coverage", not unstructured_selected, f"入选方案涉及仅有描述、未结构化技能的干员: {unstructured_selected}")
    if not selected_names:
        _check(checks, "selected_operator_names_detected", False, "未能从结果结构中提取入选干员，无法执行选中范围技能覆盖门禁。", severity="warning")
    if bool(policy.get("require_all_unlocked_skills_structured", False)):
        description_only = int((coverage.get("unlocked_skill_coverage") or {}).get("description_only_skill_count", 0) or 0)
        _check(checks, "all_unlocked_skills_structured", description_only == 0, f"仍有 {description_only} 个仅描述、未结构化技能。")

    for key in ("result", "report", "audit", "coverage", "pareto", "configuration", "preflight"):
        value = summary.get(key)
        _check(checks, f"summary_path:{key}", bool(value) and Path(value).is_file(), f"summary.{key} 必须指向存在的文件。")

    metrics = extract_core_metrics(result)
    _check(checks, "finite_core_metrics", all(math.isfinite(value) for value in metrics.values()), "核心收益指标必须是有限数值。")
    report_text = (root / "report.md").read_text(encoding="utf-8")
    _check(checks, "report_nonempty", len(report_text.strip()) > 100, "中文报告内容过短或为空。")

    stability = _stability_check(root, expanded_factor) if stability_check else None
    if stability is not None:
        _check(checks, "candidate_stability", stability.get("status") == "passed", "扩大候选库复算后选中结果发生变化。", severity="warning")
    return _finish(checks, root, stability=stability, metrics=metrics)


def _finish(
    checks: list[dict[str, Any]],
    root: Path,
    *,
    stability: dict[str, Any] | None,
    metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    errors = [item for item in checks if not item["ok"] and item["severity"] == "error"]
    warnings = [item for item in checks if not item["ok"] and item["severity"] == "warning"]
    return {
        "verification_schema_version": 1,
        "output_dir": str(root),
        "status": "failed" if errors else ("passed_with_warnings" if warnings else "passed"),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "core_metrics": metrics or {},
        "stability": stability,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="验证一次项目运行的文件绑定、审计、覆盖率和内容完整性")
    parser.add_argument("output_dir")
    parser.add_argument("--allow-warnings", action="store_true")
    parser.add_argument("--stability-check", action="store_true")
    parser.add_argument("--expanded-factor", type=float, default=2.0)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = verify_output(
        args.output_dir,
        strict_warnings=not args.allow_warnings,
        stability_check=args.stability_check,
        expanded_factor=args.expanded_factor,
    )
    destination = Path(args.output) if args.output else Path(args.output_dir) / "verification.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 5


if __name__ == "__main__":
    raise SystemExit(main())
