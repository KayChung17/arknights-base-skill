# 0.6.1 迁移说明

> 1.0.0 起公共仓库不再附带真实账号原始导出。下面的路径只适用于历史 0.6.1 包；新版本请把自己的导出文件通过 `--input` 显式传入。

0.6.1 接入此前遗漏的 roster 范围基建技能全表。来源文件位于：

```text
skills/ark-base-schedule/assets/raw/owned-operator-base-skills.txt
```

## 数据变化

- 来源表：228 名已拥有干员、483 条技能。
- 规范库：248 名干员、506 条技能。
- 额外 20 名为 0.6.0 保留的手工结构化记录，用于未拥有干员和通用测试。
- 每条导入技能增加 `required_level`、`variant_group` 和 `source_line`。
- α、β 等升级技能通过变体组选择当前练度可用的最高版本，停止重复叠加。

重新生成数据：

```bash
cd skills/ark-base-schedule
python ../update-ark-skills/scripts/import_owned_skill_table.py \
  --input assets/raw/owned-operator-base-skills.txt \
  --existing assets/operator-skills.json \
  --output assets/operator-skills.json \
  --data-version 2026-07-27-owned-roster-228 \
  --warnings-output assets/raw/import-warnings.txt
python scripts/validate_data.py
```

## 求解变化

- 发电站、办公室、控制中枢和制造站使用完整 roster 数据构造候选。
- 赚钱加搓玉默认资源安全系数调整为 `1.07`，覆盖控制中枢对贸易站最高常见全局加成造成的碎片消耗增长。
- MILP 候选在全局联动复算后再次检查硬约束；复算不合格的候选会加入无好割并继续搜索。
- 攻略基线中的赤金数值改用 `pure_gold_lmd_equivalent`，避免把龙门币等价值与赤金件数直接比较。

## 兼容性

旧版 `operator-skills.json` 仍可读取。使用新导入器后，数据结构增加可选字段，旧调用方忽略这些字段即可。排班结果的最优性措辞仍保持 0.6.0 的范围定义。
