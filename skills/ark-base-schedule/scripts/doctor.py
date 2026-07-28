#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Environment and data-health diagnostics for first-time users."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from typing import Any

from data_loader import ASSETS_DIR, load_mechanics, load_operator_data


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def doctor() -> dict[str, Any]:
    operator_data = load_operator_data()
    mechanics = load_mechanics()
    warnings_path = ASSETS_DIR / "raw" / "import-warnings.txt"
    warnings = warnings_path.read_text(encoding="utf-8").strip().splitlines() if warnings_path.exists() else []
    checks = [
        {
            "name": "python_version",
            "ok": sys.version_info >= (3, 10),
            "value": platform.python_version(),
            "requirement": ">=3.10",
        },
        {
            "name": "scipy",
            "ok": _version("scipy") is not None,
            "value": _version("scipy"),
            "requirement": ">=1.11",
        },
        {
            "name": "openpyxl",
            "ok": _version("openpyxl") is not None,
            "value": _version("openpyxl"),
            "requirement": ">=3.1（仅XLSX需要）",
        },
        {
            "name": "operator_data",
            "ok": bool(operator_data.get("operators")),
            "value": len(operator_data.get("operators") or []),
            "requirement": ">0",
        },
        {
            "name": "mechanics_data",
            "ok": bool(mechanics.get("facilities") and mechanics.get("products")),
            "value": mechanics.get("data_version") or mechanics.get("source_version"),
            "requirement": "设施与产品表存在",
        },
        {
            "name": "import_warnings",
            "ok": not warnings,
            "value": len(warnings),
            "requirement": "0",
        },
    ]
    return {
        "doctor_schema_version": 1,
        "status": "passed" if all(item["ok"] for item in checks) else "failed",
        "checks": checks,
        "data": {
            "operator_count": len(operator_data.get("operators") or []),
            "skill_count": sum(len(item.get("skills") or []) for item in operator_data.get("operators") or []),
            "data_version": operator_data.get("data_version"),
            "mechanics_source_version": (mechanics.get("drone_model") or {}).get("source_version"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查运行环境、依赖和技能数据")
    parser.add_argument("--output")
    args = parser.parse_args()
    value = doctor()
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0 if value["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
