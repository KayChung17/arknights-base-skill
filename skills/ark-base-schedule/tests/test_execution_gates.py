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
        path.write_text(
            "干员名称\t是否招募\t精英化\t等级\n"
            + "".join(f"测试{i}\t是\t0\t1\n" for i in range(1, 10)),
            encoding="utf-8",
        )
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
                "minimum_pure_gold_balance": 0
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
                },
                "right_side_levels_confirmed": True,
            },
            "horizon": {"mode": "steady_state"},
            "profiles": {"mode": "representative"},
            "right_side_schedule": [
                {"meeting": ["测试1", "测试2"], "hire": ["测试3"]},
                {"meeting": ["测试4", "测试5"], "hire": ["测试6"]},
                {"meeting": ["测试7", "测试8"], "hire": ["测试9"]}
            ]
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
            self.assertIn("/base_state/dormitory_levels", missing_paths)

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

    def test_resource_balances_default_to_steady_state_zero_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["objective"].pop("minimum_originium_shard_balance")
            config["objective"].pop("minimum_pure_gold_balance")
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "ready")
            objective = report["resolved_config"]["objective"]
            self.assertEqual(objective["minimum_originium_shard_balance"], 0.0)
            self.assertEqual(objective["minimum_pure_gold_balance"], 0.0)
            self.assertEqual(report["source_map"]["/objective/minimum_originium_shard_balance"], "repository_default")
            self.assertEqual(report["source_map"]["/objective/minimum_pure_gold_balance"], "repository_default")

    def test_steady_state_does_not_require_account_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["base_state"].pop("initial_drone_stock")
            config["base_state"].pop("inventory", None)
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "ready")
            self.assertNotIn("initial_drone_stock", report["resolved_config"]["base_state"])
            self.assertEqual(report["resolved_config"]["horizon"]["initial_state_policy"], "cyclic_phase_free")

    def test_dormitory_ambience_defaults_to_level_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["base_state"]["dormitory_levels"] = [1, 3, 4, 5]
            config["base_state"].pop("dormitory_ambience", None)
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["resolved_config"]["base_state"]["dormitory_ambience"], [1000.0, 3000.0, 4000.0, 5000.0])
            self.assertEqual(report["source_map"]["/base_state/dormitory_ambience"], "mechanics_default_max_ambience_for_dormitory_level")

    def test_orundum_requires_shard_factory_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["objective"]["goal"] = "搓玉并平衡资源"
            config["objective"]["max_orundum_trading_posts"] = 1
            config["objective"]["max_shard_factories"] = 0
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "conflict")
            self.assertTrue(any(item["code"] == "orundum_requires_shard_factory" for item in report["conflicts"]))

    def test_finite_days_requires_account_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["horizon"] = {"mode": "finite_days", "days": 7}
            config["base_state"].pop("initial_drone_stock")
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "needs_input")
            missing = {item["path"] for item in report["missing"]}
            self.assertIn("/base_state/initial_drone_stock", missing)
            self.assertIn("/base_state/initial_resources", missing)
            self.assertIn("/roster/current_morale", missing)
            self.assertEqual(report["resolved_config"]["horizon"]["initial_state_policy"], "account_snapshot_required")

    def test_right_side_levels_use_endgame_mechanics_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["base_state"].pop("right_side_levels_confirmed")
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["resolved_config"]["base_state"]["right_side_levels_confirmed"], True)
            self.assertEqual(report["source_map"]["/base_state/right_side_levels_confirmed"], "mechanics_default_full_irreversible_right_side")

    def test_non_cleared_base_requires_explicit_mechanics_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["account_state"] = {"fully_cleared": False}
            for key in ("drone_capacity", "right_side_levels", "right_side_levels_confirmed"):
                config["base_state"].pop(key, None)
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "needs_input")
            self.assertIn("/base_state/drone_capacity", {item["path"] for item in report["missing"]})

    def test_right_side_schedule_is_required_and_roster_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config.pop("right_side_schedule")
            path = root / "missing.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertIn("/right_side_schedule", {item["path"] for item in report["missing"]})

            config = self._complete(root)
            config["right_side_schedule"][0]["hire"] = ["不存在"]
            path = root / "unknown.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "conflict")
            self.assertTrue(any("练度表外干员" in item["message"] for item in report["conflicts"]))

    def test_conflicting_shard_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["objective"]["minimum_orundum_shard_balance"] = -1
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "conflict")

    def test_fixed_daily_work_hour_limit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["objective"]["max_daily_work_hours"] = 18
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "conflict")
            self.assertTrue(any(
                item["code"] == "obsolete_work_hour_limit"
                for item in report["conflicts"]
            ))

    def test_fixed_orundum_lmd_rate_cannot_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._complete(root)
            config["objective"]["economic_values"] = {"orundum_lmd": 200}
            path = root / "project.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            report = preflight_project(path, strict=True)
            self.assertEqual(report["status"], "conflict")
            self.assertTrue(any(
                item["code"] == "fixed_orundum_lmd_rate"
                for item in report["conflicts"]
            ))

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
            self._write_json(root / "unmodeled-relevant-skills.json", {
                "blocking_count": 0,
                "unmodeled_count": 0,
                "skills": [],
                "_run": {"run_id": run_id, "config_sha256": config_hash}
            })
            self._write_json(root / "pareto.json", {"frontier": []})
            self._write_json(root / "schedule.json", {
                "author": "test", "description": "test", "id": 1, "title": "test", "planTimes": "1班",
                "plans": [{
                    "name": "第01班", "description": "", "description_post": "",
                    "Fiammetta": {"enable": False, "target": "", "order": "pre"},
                    "drones": {"room": "trading", "index": 1, "enable": False, "order": "pre"},
                    "rooms": {
                        "trading": [], "manufacture": [], "power": [], "dormitory": [],
                        "control": [], "meeting": [], "hire": [], "processing": [],
                    },
                }],
                "scheduleType": {"planTimes": 1, "trading": 0, "manufacture": 0, "power": 0, "dormitory": 0},
            })
            (root / "report.md").write_text("# 报告\n" + "有效内容" * 60, encoding="utf-8")
            self._write_json(root / "config.resolved.json", {"verification": {}, "_resolution": {"run_id": run_id, "config_sha256": config_hash}})
            summary = {
                "run_id": run_id,
                "config_sha256": config_hash,
                "result": str(root / "result.json"),
                "report": str(root / "report.md"),
                "schedule": str(root / "schedule.json"),
                "audit": str(root / "audit.json"),
                "coverage": str(root / "coverage.json"),
                "pareto": str(root / "pareto.json"),
                "configuration": str(root / "config.resolved.json"),
                "preflight": str(root / "preflight.json"),
                "unmodeled_relevant_skills": str(root / "unmodeled-relevant-skills.json")
            }
            self._write_json(root / "summary.json", summary)
            names = ["preflight.json", "result.json", "audit.json", "coverage.json", "pareto.json", "report.md", "schedule.json", "config.resolved.json", "unmodeled-relevant-skills.json"]
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
            "schedule.json",
            "assets/template.json",
            "needs_input",
            "execution_blocked",
        ):
            self.assertIn(phrase, skill)


if __name__ == "__main__":
    unittest.main()
