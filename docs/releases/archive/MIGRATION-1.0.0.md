# 迁移到 1.0.0

## 推荐入口

旧版可以直接调用 `search_layouts.py`、`search_upgrades.py` 和 `solve_schedule.py`。1.0.0 仍保留这些命令，同时新增统一入口：

```bash
python arkbase.py run project.json
```

## 主要变化

- 布局 profile 从脚本硬编码扩展为代表配置、自定义文件和房间等级网格。
- 无人机容量、初始库存、宿舍和右侧设施等级可以从项目配置输入。
- 求解结果增加环境、配置和输入哈希的可复现清单。
- 新增硬约束与最优性措辞审计。
- 新增中文 Markdown 报告生成。
- 培养搜索支持留一法边际复算。
- 输出 schema 版本提高；旧脚本字段仍尽量兼容。

## 注意

`level_grid` 只表示房间等级多重集枚举。若设置 `max_profiles`，外层 profile 会发生截断；报告必须保留该范围说明。
