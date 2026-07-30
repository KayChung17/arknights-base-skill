#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chinese command entry point for the repository."""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="arkbase", description="明日方舟基建布局、排班、无人机和培养优化")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="从项目JSON配置执行完整流程")
    run.add_argument("config")
    run.add_argument("--output-dir")
    run_mode = run.add_mutually_exclusive_group()
    run_mode.add_argument("--strict-input", action="store_true", default=True, help="缺少关键输入时停止，默认启用")
    run_mode.add_argument("--allow-defaults", action="store_true", help="使用配置中明确授权的仓库默认值")
    run.add_argument("--skip-verify", action="store_true", help="仅供调试，不生成发布级验证结论")

    preflight = sub.add_parser("preflight", help="检查输入完整性并生成解析配置")
    preflight.add_argument("config")
    preflight.add_argument("--output")
    preflight.add_argument("--allow-defaults", action="store_true")

    sub.add_parser("doctor", help="检查环境和数据")
    audit = sub.add_parser("audit", help="审计求解结果")
    audit.add_argument("input")
    audit.add_argument("--output")
    audit.add_argument("--strict-warnings", action="store_true")

    verify = sub.add_parser("verify", help="验证完整项目输出并检查同次运行绑定")
    verify.add_argument("output_dir")
    verify.add_argument("--output")
    verify.add_argument("--allow-warnings", action="store_true")
    verify.add_argument("--stability-check", action="store_true")
    verify.add_argument("--expanded-factor", type=float, default=2.0)

    report = sub.add_parser("report", help="生成中文Markdown报告")
    report.add_argument("input")
    report.add_argument("--output", required=True)

    coverage = sub.add_parser("coverage", help="统计干员池技能数据覆盖")
    coverage.add_argument("--roster", required=True)
    coverage.add_argument("--output")
    args, _ = parser.parse_known_args()

    if args.command == "run":
        from preflight import PreflightError
        from run_project import run_project
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
    if args.command == "preflight":
        from preflight import preflight_project
        value = preflight_project(args.config, strict=not args.allow_defaults)
        if args.output:
            from pathlib import Path
            Path(args.output).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if value["status"] == "ready" else 4
    if args.command == "doctor":
        from doctor import doctor
        value = doctor()
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if value["status"] == "passed" else 2
    if args.command == "audit":
        from audit_result import main as audit_main
        sys.argv = [sys.argv[0], args.input] + (["--output", args.output] if args.output else []) + (["--strict-warnings"] if args.strict_warnings else [])
        return audit_main()
    if args.command == "verify":
        from verify_output import main as verify_main
        sys.argv = [sys.argv[0], args.output_dir]
        if args.output:
            sys.argv += ["--output", args.output]
        if args.allow_warnings:
            sys.argv += ["--allow-warnings"]
        if args.stability_check:
            sys.argv += ["--stability-check"]
        sys.argv += ["--expanded-factor", str(args.expanded_factor)]
        return verify_main()
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
