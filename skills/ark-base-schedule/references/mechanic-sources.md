# 2.0 机制来源

| 来源 | 获取日期 | 使用范围 | 验证状态 |
|---|---|---|---|
| [PRTS：八幡海铃](https://prts.wiki/w/%E5%85%AB%E5%B9%A1%E6%B5%B7%E9%93%83) | 2026-07-29 | “可靠伙伴”“家族认可”的解锁条件、描述和18名叙拉古计数名单 | 已与本地描述及 E1/E2 回归测试交叉核对 |
| [PRTS：术语](https://prts.wiki/w/%E6%9C%AF%E8%AF%AD) | 2026-07-29 | 阵营、热情值和技能术语 | 社区资料交叉核对 |
| [PRTS：会客室](https://prts.wiki/w/%E7%BD%97%E5%BE%B7%E5%B2%9B%E5%9F%BA%E5%BB%BA/%E4%BC%9A%E5%AE%A2%E5%AE%A4) | 2026-07-29 | 不可降级；Lv.3 耗电 60 | 已进入 190 右满电力回归 |
| [PRTS：办公室](https://prts.wiki/w/%E7%BD%97%E5%BE%B7%E5%B2%9B%E5%9F%BA%E5%BB%BA/%E5%8A%9E%E5%85%AC%E5%AE%A4) | 2026-07-29 | 不可降级；Lv.3 耗电 60 | 已进入 190 右满电力回归 |
| [PRTS：训练室](https://prts.wiki/w/%E7%BD%97%E5%BE%B7%E5%B2%9B%E5%9F%BA%E5%BB%BA/%E8%AE%AD%E7%BB%83%E5%AE%A4) | 2026-07-29 | 不可降级；Lv.3 耗电 60 | 已进入 190 右满电力回归 |
| [PRTS：加工站](https://prts.wiki/w/%E7%BD%97%E5%BE%B7%E5%B2%9B%E5%9F%BA%E5%BB%BA/%E5%8A%A0%E5%B7%A5%E7%AB%99) | 2026-07-29 | 不可降级；Lv.3 耗电 10 | 已进入 190 右满电力回归 |
| [Arknights Mower：排班教学](https://arkmowers.github.io/arknights-mower/manual/schedule/#_31) | 2026-07-29 | 宿舍基础恢复、工作站人数减耗、控制中枢全局减耗、工休比 | 仅采用游戏机制；自动化脚本、主替班字段和副表规则明确排除 |
| 用户提供的三张基建攻略图片 | 2026-07-29 | 同组/大组、动态换班、宿管协同和候选干员标注 | 仅作为候选结构启发；具体数值与资格不直接入模 |

内置 `operator-skills.json` 的原始描述来自脱敏社区快照。2.0 在保留原始描述的基础上增加 `effects`、`variant_group`、`special_rules`、动态 `mechanism` 和 `model_status`。结构化字段通过数据校验及代表性叠加回归测试发布。

PRTS 是玩家共同维护的社区 Wiki。来源页用于机制交叉核对，最终规则仍以游戏内当前版本描述和可复现实测为准。
