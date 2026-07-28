#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from preflight import preflight_project  # noqa: E402
from verify_output import verify_output  # noqa: E402


class PreflightTests(unittest.TestCase):
    def _write_roster(self, root: Path) -> Path:
        path = root / "roster.tsv"
        path.write_text("干员名称\t是否招募\t精英化\t等级\n测试\t是\t0\t1\n", encoding="utf-8")
        return path

    def _complete(self, root: Path) -> dict:
        roster = self._write_roster(root)
        return {
            "schema_version": 1,
            "mode": "layout_search",
            "roster": str(roster),
            "objective": {
                "goal": "合成玉优先并保持龙门币下限",
                "online_times": ["08:00", "14:00", "20:00"],
                "minimum_net_lmd_per_day": -10000,
                "minimum_originium_shard_balance": 0,
                "minimum_pure_gold_balance": 0,
                "max_daily_work_hours": 18
            },
            "base_state": {
                "drone_capacity": 235,
                "initial_drone_stock": 0,
                "dormitory_levels": [5, 5, 5, 5],
                "right_side_levels": {
                    "reception_room": 3,
                    "office": 3,
                    "training_room": 3,
                    "workshop": 3
                }
            },
            "horizon": {"mode": "steady_state"},
            "profiles": {"mode": "representative"}
        }

    def test_missing_inputs_stop_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"schema_version": 1, "mode": "layout_search", "roster": str(self._write_roster(root)), "objective": {}}
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "needs_input")
            missing_paths = {item["path"] for item in report["missing"]}
            self.assertIn("/objective/online_times", missing_paths)
            self.assertIn("/base_state", missing_paths)

    def test_deprecated_shard_field_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            value = config["objective"].pop("minimum_originium_shard_balance")
            config["objective"]["minimum_orundum_shard_balance"] = value
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["resolved_config"]["objective"]["minimum_originium_shard_balance"], value)
            self.assertTrue(report["deprecations"])

    def test_conflicting_shard_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["objective"]["minimum_orundum_shard_balance"] = -1
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "conflict")

    def test_fixed_schedule_requires_room_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["mode"] = "fixed_schedule"
            config["layout"] = "342"
            config.pop("profiles")
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "needs_input")
            self.assertIn("/facility_configuration", {item["path"] for item in report["missing"]})


class VerificationTests(unittest.TestCase):
    def _hash(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_json(self, path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_tampered_artifact_fails_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run-1"
            config_hash = "config-1"
            self._write_json(root / "preflight.json", {"status": "ready", "config_sha256": config_hash, "_run": {"run_id": run_id}})
            self._write_json(root / "result.json", {
                "selected": {"orundum_per_day": 1, "net_lmd_per_day": 0, "originium_shard_balance": 0, "pure_gold_balance": 0, "operators": ["测试"]},
                "project_execution": {"run_id": run_id, "config_sha256": config_hash}
            })
            self._write_json(root / "audit.json", {"status": "passed", "_run": {"run_id": run_id}})
            self._write_json(root / "coverage.json", {
                "roster": {"operator_coverage_ratio": 1.0},
                "unlocked_skill_coverage": {"description_only_skill_count": 0},
                "description_only_examples": [],
                "operators": [{"operator": "测试", "known": True}],
                "_run": {"run_id": run_id}
            })
            self._write_json(root / "pareto.json", {"frontier": []})
            (root / "report.md").write_text("# 报告\n" + "有效内容" * 60, encoding="utf-8")
            self._write_json(root / "config.resolved.json", {"verification": {}, "_resolution": {"run_id": run_id, "config_sha256": config_hash}})
            summary = {
                "run_id": run_id,
                "config_sha256": config_hash,
                "result": str(root / "result.json"),
                "report": str(root / "report.md"),
                "audit": str(root / "audit.json"),
                "coverage": str(root / "coverage.json"),
                "pareto": str(root / "pareto.json"),
                "configuration": str(root / "config.resolved.json"),
                "preflight": str(root / "preflight.json")
            }
            self._write_json(root / "summary.json", summary)
            names = ["preflight.json", "result.json", "audit.json", "coverage.json", "pareto.json", "report.md", "config.resolved.json"]
            self._write_json(root / "run-manifest.json", {"run_id": run_id, "config_sha256": config_hash, "artifacts": {name: self._hash(root / name) for name in names}})
            passed = verify_output(root, strict_warnings=False)
            self.assertIn(passed["status"], {"passed", "passed_with_warnings"})
            (root / "report.md").write_text("已篡改", encoding="utf-8")
            failed = verify_output(root, strict_warnings=False)
            self.assertEqual(failed["status"], "failed")
            self.assertTrue(any(item["code"] == "hash:report.md" and not item["ok"] for item in failed["checks"]))


class SkillContractTests(unittest.TestCase):
    def test_skill_contains_execution_gates(self) -> None:
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "先检查输入完整性",
            "最终方案必须来自仓库完整入口",
            "求解后必须验证输出",
            "执行失败时停止给结论",
            "needs_input",
            "execution_blocked",
        ):
            self.assertIn(phrase, skill)


if __name__ == "__main__":
    unittest.main()
