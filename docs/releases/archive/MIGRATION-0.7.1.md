# 迁移到 0.7.1

0.7.1 不改变既有排班JSON结构。

- 时间限制不再等同于无可行解；有可行incumbent时会返回候选，并把 `accepted_time_limit_incumbents` 写入求解元数据。
- `search_layouts.py` 新增 `--lmd-proxy-floor-slack`。经济边界严格时建议使用0。
- 新增 `search_upgrades.py`，用于比较当前练度、已拥有干员基建技能全解锁上限和定向最低解锁方案。
