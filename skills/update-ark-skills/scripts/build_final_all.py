#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_final_all.py — 合并干员练度表和技能数据，生成全干员基建技能一览

输入：
  1. 干员练度表.txt（tab 分隔：干员名称、是否已招募、星级、等级、精英化等级……）
  2. skills_parsed.txt（可选，parse_skills.py 的输出，格式：干员名|精等级|设施|技能名|技能描述）
输出：基建技能一览_全干员.txt

Usage:
    python build_final_all.py [--operators 干员练度表.txt] [--skills skills_parsed.txt] [--output 基建技能一览_全干员.txt]
"""

import sys


def load_operator_list(path: str) -> dict[str, dict]:
    operators: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f.readlines()[1:]:
            parts = line.strip().split("\t")
            if len(parts) < 5:
                continue
            name = parts[0].strip()
            operators[name] = {
                "star": parts[2].strip(),
                "level": parts[3].strip(),
                "elite": parts[4].strip(),
                "owned": parts[1].strip().upper() == "TRUE",
            }
    return operators


def load_skills(path: str) -> dict[str, list[tuple[str, str, str, str]]]:
    skills: dict[str, list[tuple[str, str, str, str]]] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 5:
                    name = parts[0].strip()
                    if name not in skills:
                        skills[name] = []
                    skills[name].append((parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[4].strip()))
    except FileNotFoundError:
        print(f"[WARN] {path} 不存在，跳过技能数据加载")
    return skills


def build_output(operators: dict[str, dict], skills: dict[str, list[tuple[str, str, str, str]]], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("基建技能一览（全干员）\n")
        f.write(f"共 {len(operators)} 名干员\n")
        f.write("=" * 80 + "\n\n")
        for name in sorted(operators.keys()):
            info = operators[name]
            tag = "[已招募]" if info["owned"] else "[未招募]"
            f.write(f"{tag} 【{name}】星级{info['star']} Lv{info['level']} E{info['elite'] or '0'}\n")
            if name in skills:
                for sk in skills[name]:
                    f.write(f"  {sk[0]} | {sk[1]} | {sk[2]} | {sk[3]}\n")
            else:
                f.write("  (无基建技能数据)\n")
            f.write("\n")
    print(f"[OK] 生成完成: {output_path}")
    print(f"     共 {len(operators)} 名干员，{sum(len(v) for v in skills.values())} 条技能")


def main():
    op_path = "干员练度表.txt"
    sk_path = "skills_parsed.txt"
    out_path = "基建技能一览_全干员.txt"

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--operators" and i + 1 < len(args):
            op_path = args[i + 1]
            i += 2
        elif args[i] == "--skills" and i + 1 < len(args):
            sk_path = args[i + 1]
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            out_path = args[i + 1]
            i += 2
        else:
            i += 1

    try:
        operators = load_operator_list(op_path)
    except FileNotFoundError:
        print(f"[ERR] 干员练度表不存在: {op_path}")
        sys.exit(1)

    skills = load_skills(sk_path)
    build_output(operators, skills, out_path)


if __name__ == "__main__":
    main()
