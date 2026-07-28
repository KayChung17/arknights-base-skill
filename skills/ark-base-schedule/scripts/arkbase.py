#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chinese command entry point for the repository."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="arkbase", description="明日方舟基建布局、排班、无人机和培养优化")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="从项目JSON配置执行完整流程")
    run.add_argument("config")
    run.add_argument("--output-dir")

    sub.add_parser("doctor", help="检查环境和数据")

    audit = sub.add_parser("audit", help="审计求解结果")
    audit.add_argument("input")
    audit.add_argument("--output")
    audit.add_argument("--strict-warnings", action="store_true")

    report = sub.add_parser("report", help="生成中文Markdown报告")
    report.add_argument("input")
    report.add_argument("--output", required=True)

    coverage = sub.add_parser("coverage", help="统计干员池技能数据覆盖")
    coverage.add_argument("--roster", required=True)
    coverage.add_argument("--output")

    args, _ = parser.parse_known_args()
    if args.command == "run":
        from run_project import run_project
        import json
        summary = run_project(args.config, output_dir=args.output_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["audit_status"] != "failed" else 2
    if args.command == "doctor":
        from doctor import doctor
        import json
        value = doctor()
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if value["status"] == "passed" else 2
    if args.command == "audit":
        from audit_result import main as audit_main
        sys.argv = [sys.argv[0], args.input] + (["--output", args.output] if args.output else []) + (["--strict-warnings"] if args.strict_warnings else [])
        return audit_main()
    if args.command == "report":
        from generate_report import main as report_main
        sys.argv = [sys.argv[0], args.input, "--output", args.output]
        return report_main()
    if args.command == "coverage":
        from coverage_report import main as coverage_main
        sys.argv = [sys.argv[0], "--roster", args.roster] + (["--output", args.output] if args.output else [])
        return coverage_main()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
