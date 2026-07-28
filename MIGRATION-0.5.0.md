# 0.4.x → 0.5.0 迁移说明

## 候选方案字段

- 新方案使用 `schema_version: 4`。
- 增加 `plan_status`，候选使用 `candidate`，通过全部门禁后改为 `final`。
- 增加 `facility_configuration`，显式记录每个房间的等级和产品。
- 使用 `operation_nodes` 和 `segments` 替代把上线次数解释为等长 `shifts`。
- 增加 `baseline`、`external_skill_evidence`、`recovery_plan` 和 `economy_projection`。

0.4.x 的 `shifts` 文件仍可作为 candidate 读取，标准化脚本会推断设施配置。推断配置不能直接升级为 final。

## 赚钱加搓玉

默认布局由 243 调整为 342，默认攻略结构为：

- 贸易站 3、3、1 级。
- 制造站 3、3、2、2 级。
- 两龙门币贸易、一源石贸易。
- 两赤金、一源石碎片、一作战记录。
- 三次上线默认形成 6、6、12 小时区间。

## 新增命令

```bash
python skills/ark-base-schedule/scripts/compare_to_baseline.py candidate.json \
  --baseline guide_342_orundum_3_login \
  --embed-plan candidate.compared.json
```

## final 门禁

以下状态会阻止 final：

- 设施等级由脚本推断。
- 生产干员技能数据未经验证。
- 时间区间不覆盖 24 小时。
- 循环日恢复未验证。
- 赚钱加搓玉缺少经济投影或仓库检查。
- 未完成攻略基线比较。
