#!/usr/bin/env python3
"""
parse_skills.py — 解析 cells_clean.txt 基建技能数据

输入格式:
    cells_clean.txt，每4~5行一组（干员名、精等级、设施、技能名、技能描述）
    但有时精等级行会前移，导致干员名缺失（4行一组，干员名继承上一组）

输出格式:
    skills_parsed.txt，管道分隔: 干员名|精等级|设施|技能名|技能描述

策略:
    以技能描述行（匹配指定正则）为锚点，向前取3~4个字段作为一组。
"""

import re
import sys

# 技能描述行必须以此正则开头
DESC_PATTERN = re.compile(r'^(进驻|当与|宿舍|如果)')
# 最短描述长度：用于过滤"宿舍"设施字段（2字符）而非真正的描述行
MIN_DESC_LEN = 5

INPUT_FILE = 'cells_clean.txt'
OUTPUT_FILE = 'skills_parsed.txt'
# 文件头占5行（干员, 解锁, 设施, 技能, 描述）
NUM_HEADER_LINES = 5


def main():
    # 1. 读取所有行
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            lines = [line.rstrip('\n\r') for line in f]
    except FileNotFoundError:
        print(f"错误: 未找到 {INPUT_FILE}", file=sys.stderr)
        sys.exit(1)

    if not lines:
        print("错误: 文件为空", file=sys.stderr)
        sys.exit(1)

    # 2. 定位所有技能描述行
    desc_indices = []
    for i, line in enumerate(lines):
        if i < NUM_HEADER_LINES:
            continue
        if len(line) >= MIN_DESC_LEN and DESC_PATTERN.match(line):
            desc_indices.append(i)

    if not desc_indices:
        print("错误: 未找到匹配的技能描述行", file=sys.stderr)
        sys.exit(1)

    print(f"共找到 {len(desc_indices)} 个技能描述行")

    # 3. 以描述行为锚点，向前取字段
    results = []
    last_op = ''
    prev_desc_idx = NUM_HEADER_LINES - 1  # 初始为头尾边界

    for desc_idx in desc_indices:
        field_start = prev_desc_idx + 1
        fields = lines[field_start:desc_idx]

        if len(fields) == 4:
            # 完整组: [干员名, 精等级, 设施, 技能名]
            op, elite, fac, name = fields
            last_op = op
        elif len(fields) == 3:
            # 缺干员名组: [精等级, 设施, 技能名]，干员名继承上行
            elite, fac, name = fields
            op = last_op
        else:
            # 异常情况: 记录警告并跳过
            print(
                f"警告: 描述行 #{desc_idx + 1} 前有 {len(fields)} 个字段 "
                f"(期望3或4) — 跳过",
                file=sys.stderr,
            )
            # 尝试用偏移法(规则4)恢复: 自 prev_desc_idx 后逐个偏移重试
            recovered = False
            for offset in range(1, min(5, len(lines) - desc_idx)):
                candidate_start = prev_desc_idx + 1 + offset
                if candidate_start >= desc_idx:
                    break
                sub_fields = lines[candidate_start:desc_idx]
                if len(sub_fields) == 4:
                    op, elite, fac, name = sub_fields
                    last_op = op
                    recovered = True
                    break
                elif len(sub_fields) == 3:
                    elite, fac, name = sub_fields
                    op = last_op
                    recovered = True
                    break
            if not recovered:
                continue  # 跳过此组

        results.append(f"{op}|{elite}|{fac}|{name}|{lines[desc_idx]}")
        prev_desc_idx = desc_idx

    # 4. 写入输出文件
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for line in results:
            f.write(line + '\n')

    print(f"成功解析 {len(results)} 个技能")
    print(f"输出文件: {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
