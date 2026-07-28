# 0.6.2 迁移说明

0.6.2 默认启用无人机闭环建模。旧决策上下文无需修改；`_solver_settings` 会补充以下默认值：

```json
{
  "allocate_drones": true,
  "drone_repeating_day_balance": true,
  "drone_capacity": 235,
  "initial_drone_stock": 235,
  "max_drone_use_per_node": 235
}
```

需要模拟一次性库存时，将 `drone_repeating_day_balance` 设为 `false` 并填写真实 `initial_drone_stock`。需要禁止无人机时，将 `allocate_drones` 设为 `false`。

求解结果新增 `drone_allocations`、`drone_inventory`、`drone_waste` 和 `simulation.drone_plan`。依赖旧 schema 的调用方需要接受这些必需字段。
