# 可复现与审计约定

## 可复现清单

每次项目运行记录：

- 运行类型；
- Python、SciPy、NumPy 和 openpyxl 版本；
- Git 提交（可获取时）；
- 求解范围与截断信息。

项目必须在求解前以 `right_side_schedule` 固定每班会客室与办公室人员。右侧人员进入同班互斥、每日工时、全局联动和宿舍恢复计算，导出后不得人工补写。

## 审计级别

- `passed`：已实现的硬约束和一致性检查全部通过。
- `passed_with_warnings`：没有已知硬约束违规，但存在数据覆盖、复现信息或未建模机制提示。
- `failed`：资源下限、无人机流、容量、数值有限性或最优性措辞至少一项不成立。

## 搜索范围声明

外层 profile、单房间组合和 MILP 都可能截断。任何一层截断后，结果必须使用 `best_found_within_truncated_candidate_library` 或等价中文措辞。

`proxy_optimal_within_complete_candidate_library` 只说明代理模型在完整候选库内达到声明 gap。除非代理目标与最终模拟完全一致，并且所有相关机制都在模型内，否则 `actual_simulation_global_optimality_proven` 必须为 `false`。

## 发布门禁

最终 `schedule.json` 必须同时满足：

- 与 `result.json` 的确定性导出结果逐值一致；
- 文件哈希与 `run-manifest.json` 一致；
- `meeting`、`hire`、`dormitory` 和生产房间都来自同次运行。

任何导出后修改都会使发布验证失败。

发布前至少执行：

```bash
bash scripts/validate-all.sh
bash scripts/build-release.sh release
```

端到端测试必须通过公开的 `arkbase.py run` 入口，不能使用发布用户无法复现的隐藏参数。
