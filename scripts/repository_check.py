#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repository-level checks that are cheap enough for every CI run."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors: list[str] = []
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    version = version_match.group(1) if version_match else None
    marketplace = json.loads((ROOT / "marketplace.json").read_text(encoding="utf-8"))
    if marketplace.get("version") != version:
        errors.append(f"marketplace版本{marketplace.get('version')}与pyproject版本{version}不一致")
    if not (ROOT / f"MIGRATION-{version}.md").exists():
        errors.append(f"缺少 MIGRATION-{version}.md")
    for required in (
        "README.md", "LICENSE", "NOTICE.md", "SECURITY.md", "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md", "docs/实际问题复盘.md", "docs/发布检查清单.md",
    ):
        if not (ROOT / required).exists():
            errors.append(f"缺少 {required}")
    for path in (ROOT / "examples" / "configs").glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    schema = ROOT / "skills" / "ark-base-schedule" / "schemas" / "project-config.schema.json"
    json.loads(schema.read_text(encoding="utf-8"))
    forbidden = []
    for pattern in ("roster.xlsx", "roster.tsv", "干员练度表.xlsx"):
        if (ROOT / pattern).exists():
            forbidden.append(pattern)
    if forbidden:
        errors.append(f"仓库根目录包含用户数据文件: {forbidden}")

    raw_private = [
        path.relative_to(ROOT).as_posix()
        for pattern in ("*owned-operator-base-skills*", "*干员基建技能全表*")
        for path in ROOT.rglob(pattern)
        if "tests/fixtures" not in path.as_posix()
    ]
    if raw_private:
        errors.append(f"公共仓库包含真实账号原始导出疑似文件: {sorted(set(raw_private))}")

    operator_data_path = ROOT / "skills" / "ark-base-schedule" / "assets" / "operator-skills.json"
    operator_data = json.loads(operator_data_path.read_text(encoding="utf-8"))
    leaked_fields: list[str] = []
    for operator in operator_data.get("operators") or []:
        for field in ("source_level", "source_elite", "owned", "recruited"):
            if field in operator:
                leaked_fields.append(f"{operator.get('name')}:{field}")
        for skill in operator.get("skills") or []:
            if "source_line" in skill:
                leaked_fields.append(f"{operator.get('name')}:source_line")
    if leaked_fields:
        errors.append(f"公共技能快照仍包含来源账号字段: {leaked_fields[:10]}")

    if errors:
        print("仓库检查失败：")
        for item in errors:
            print(f"- {item}")
        return 2
    print(f"仓库检查通过：版本 {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
