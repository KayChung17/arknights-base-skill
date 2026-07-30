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
- `variant_group` 表示技能槽：同组只启用已解锁的最高阶段，不同组技能同时生效；改名升级必须维护显式映射。
- 非线性技能必须使用机制标签，并在效率计算器中实现对应规则。
- 依赖用户配置数值的动态技能使用结构化 `mechanism`；不得以 `base_bonus_pct: 0` 代替待实现公式。
- 带“同种效果取最高”的生产效果必须写入 `effects`，包含稳定 `effect_key` 和 `stacking: max`。
- 带“与部分技能有特殊叠加规则”的技能必须写入 `special_rules`，记录排斥对象、优先级和结算类型。
- 由其他干员或全局体系授予的效果必须建模在授予者上，并以 `granted_effect_skill_names` 记录效果别名；目标成员不得保留相同别名的独立固定收益。
- 合并旧数据时，带显式零值 `model_status` 的记录必须允许清除历史数值；禁止把旧的非零占位值静默带回。
- 同一干员的单例联动标签只能出现在一个独立技能槽；发现旧占位与正式技能并存时停止发布。
- `model_status` 必须区分 `structured`、`verified_zero`、`description_only` 和 `unsupported`。
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
- 授予型效果别名没有作为成员独立技能重复出现。
- 单例联动标签没有跨多个独立技能槽重复。
- 数据版本发生变化。
- warning 已处理或写入发布说明。
