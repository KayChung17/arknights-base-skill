# 迁移到 2.0.0：结构化叠加与独立排班文件

## 输出目录

2.0 完整运行新增必需文件：

```text
schedule.json
```

它是面向排班工具的正式产物。旧目录只有 `result.json` 时，可以运行：

```bash
python skills/ark-base-schedule/scripts/export_schedule_template.py \
  output/old-run/result.json \
  --output output/old-run/schedule.json
```

若要让旧目录通过 2.0 的清单验证，建议使用原配置重新运行完整项目；手工加入文件无法自动恢复原运行的产物哈希绑定。

## 项目配置

配置 `schema_version` 仍为 1。2.0 的严格预检会要求显式提供以下内容：

- `objective.goal`、上线时间、资源下限和单人工时上限；
- 无人机容量与初始库存、四间宿舍等级、右侧设施等级；
- `base_state.right_side_levels_confirmed: true`；右侧功能设施升级不可逆，严格预检要求这些等级来自用户当前基建；
- `horizon.mode`，当前可发布的稳态求解使用 `steady_state`；
- `fixed_schedule` 模式的 `layout` 和 `facility_configuration`。

可以使用以下场景覆盖在不改动 roster 的情况下试算精英化：

```json
{
  "operator_overrides": {
    "八幡海铃": {"elite": 2, "level": 70}
  }
}
```

覆盖只作用于本次标准化、求解和覆盖率报告，原始 roster 文件保持原样。

源石碎片下限继续使用：

```json
"minimum_originium_shard_balance": 0
```

旧字段 `minimum_orundum_shard_balance` 在兼容期内会被读取，同时产生弃用信息。发布配置应迁移到新字段。

## 技能数据扩展

自定义技能数据需要支持以下字段：

- `variant_group`：同一技能槽的升级阶段。
- `effects[].effect_key` 与 `effects[].stacking`：同类效果结算。
- `special_rules`：特殊排斥、覆盖或协同。
- `model_status`：包括 `structured`、`verified_zero`、`conservative_zero`、`description_only`、`unsupported`。

生产相关文案包含“同种效果取最高”或“与部分技能有特殊叠加规则”时，缺少相应结构会进入阻断门禁。

## 行为变化

- 经验制造站会在主资源指标保持或改善的前提下继续提高经验产量。
- 选中干员的未结构化技能按实际进驻设施判断。
- 2.0 验证器要求 `schedule.json` 存在且兼容模板。
- `finite_days` 仍处于显式阻断状态；稳态项目继续使用 `horizon.mode: steady_state`。
