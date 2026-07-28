# 迁移到 1.1.0：严格输入与可验证运行

## 行为变化

`arkbase run` 默认启用严格输入门禁。旧配置中依赖以下隐式默认值的项目会返回 `needs_input`：上线时刻、经济下限、每日工时、无人机容量和库存、宿舍等级、右侧设施等级、求解周期。

需要继续采用默认值时，在配置中逐项写入 `input_policy.authorized_defaults`，然后使用 `--allow-defaults`。生产使用建议直接填写真实值。

## 固定排班

`fixed_schedule` 现在要求 `facility_configuration`。仅提供 `layout: "342"` 无法确定每座房间的等级与产品，因此不能进行逐区间复算。

## 资源字段

将：

```json
"minimum_orundum_shard_balance": 0
```

迁移为：

```json
"minimum_originium_shard_balance": 0
```

旧字段在一个兼容周期内继续读取。

## 新增命令

```bash
python arkbase.py preflight project.json
python arkbase.py verify output/my-project
```

完整运行会新增 `preflight.json`、`run-manifest.json` 和 `verification.json`。
