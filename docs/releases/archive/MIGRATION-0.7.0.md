# 迁移到 0.7.0

## 目标变化

0.7.0 将基建布局、房间等级和产品分配加入优化变量。此前先固定342再排班的调用方式仍可使用；当用户询问最适合其练度的布局时，应先运行 `search_layouts.py`。

## 无人机容量

`drone_capacity` 表示无人机持有上限，来源为基建区域清理进度。全部清理时为235。不要再根据发电站数量或等级计算持有上限。发电站等级用于供电，进驻状态和技能用于恢复速度。

## 新目标

`orundum_lmd_balance` 禁止经验书产线，使用源石碎片、赤金和龙门币净变化作为硬约束。`minimum_net_lmd_balance` 可设置为0，也可按用户接受程度设置为负数，例如-1000。

## 新命令

```bash
python skills/ark-base-schedule/scripts/search_layouts.py \
  --roster roster.xlsx \
  --online-times 08:00,14:00,20:00 \
  --lmd-floor -1000 \
  --max-daily-work-hours 18 \
  --top-k 30 \
  --operator-pool-size 12 \
  --time-limit 12 \
  --max-proxy-attempts 4 \
  --output layout-search.json
```

外层粗搜索后，应对排名第一和接近的布局扩大候选库重新求解。
