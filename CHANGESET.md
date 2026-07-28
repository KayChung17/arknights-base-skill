# 严格输入与可验证求解改动集

本目录是针对 `KayChung17/arknights-base-skill` 主分支生成的覆盖包。将目录内容复制到仓库根目录，或应用随附补丁。

实现范围：

- Skill 强制输入门禁、仓库执行门禁和输出验证门禁；
- `arkbase preflight`；
- `arkbase run` 默认严格输入，显式 `--allow-defaults`；
- `arkbase verify`，包含同次运行绑定、哈希校验和可选稳定性复算；
- `config.resolved.json` 字段来源记录；
- `minimum_originium_shard_balance` 字段迁移；
- `steady_state` 与 `finite_days` 配置区分，有限周期在未实现前明确阻塞；
- `pareto.json` 多目标候选前沿后处理；
- 输入门禁、字段迁移、文件篡改和 Skill 合同回归测试。

说明：有限天数库存轨迹尚未写入现有 MILP 和模拟器。本改动只建立配置与阻塞门禁，避免错误地按稳态执行。
