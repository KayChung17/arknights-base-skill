---
name: update-ark-skills
description: 导入、解析、规范化、合并并校验明日方舟基建技能数据，为 ark-base-schedule 提供版本化结构化数据；适用于拥有干员技能全表、通用文本表格、机制标签补充、数据版本更新和发布前检查。
---

# 明日方舟基建技能数据维护

本 Skill 维护 `ark-base-schedule` 使用的规范技能数据。

## 原则

- 保留完整中文原始描述。
- 记录来源行、获取日期、适用版本和数据版本。
- 只提取能够确定的数值。
- 无法解析或存在歧义的记录必须进入 warning，不得静默丢弃。
- α/β、精英化升级和同组替换使用稳定 `variant_group`。
- 非线性技能必须使用机制标签，并在效率计算器中实现对应规则。
- 内置拥有干员表只是回归快照，不得宣传为游戏全部干员数据。

## 导入拥有干员技能表

```bash
python scripts/import_owned_skill_table.py \
  --input input.txt \
  --existing ../ark-base-schedule/assets/operator-skills.json \
  --output ../ark-base-schedule/assets/operator-skills.json \
  --data-version YYYY-MM-DD-source \
  --warnings-output import-warnings.txt
```

导入器应保留：

- 干员名、星级、来源等级和精英化。
- 技能解锁条件。
- 设施、产品、技能名和完整描述。
- 可确定的直接效率、仓库和心情字段。
- 机制标签、来源行和变体组。

## 通用文本解析

```bash
python scripts/parse_skills.py \
  --input cells_clean.txt \
  --output skills_parsed.json \
  --format json
```

支持管道分隔、制表符分隔和松散文本块。

## 合并规范数据

```bash
python scripts/export_operator_skills.py \
  --parsed skills_parsed.json \
  --existing ../ark-base-schedule/assets/operator-skills.json \
  --output ../ark-base-schedule/assets/operator-skills.json \
  --data-version YYYY-MM-DD
```

## 发布前检查

```bash
python ../ark-base-schedule/scripts/validate_data.py
python -m unittest discover -s ../ark-base-schedule/tests
python -m unittest discover -s tests
```

检查：

- 干员名和 ID 唯一。
- 精英化为 0、1、2。
- 产品与设施匹配。
- 机制标签已被效率计算器支持，或明确标记为复算/待核验。
- 数据版本发生变化。
- warning 已处理或写入发布说明。
