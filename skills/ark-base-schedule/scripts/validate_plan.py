#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a model-authored candidate plan against deterministic hard rules."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from plan_utils import normalize_plan_file, write_json
from schedule_validator import format_validation_report, validate_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="校验大模型提出的基建候选方案")
    parser.add_argument("plan")
    parser.add_argument("--roster", help="提供后，练度和心情以用户干员表为准")
    parser.add_argument("--output", help="写入标准化方案和校验结果")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    normalized = normalize_plan_file(args.plan, args.roster)
    report = validate_schedule(normalized)
    payload = {
        "plan_id": normalized.get("plan_id"),
        "normalized_plan": normalized,
        "validation": report,
    }
    if args.output:
        write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else format_validation_report(report))
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
