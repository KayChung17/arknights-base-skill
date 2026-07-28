# 数据来源记录规则

仓库中的本地技能数据保留原有版本信息。攻略适配过程中的外部资料不直接写入计算器，先写成证据记录。

每条外部资料至少包含：

- `source_id`
- `url`
- `retrieved_at`
- `publication_date` 或适用游戏版本
- `source_type`：官方、社区实测、攻略、用户提供图片或推断
- `structured_conclusion`
- `verified`

`assets/strategy-templates.json` 中的 `guide_342_orundum_3_login` 来自用户提供攻略图的结构化摘要。仓库不包含原图，数值均标为 approximate。具体干员队列在进入最终方案前，需要使用当前资料重新核验。

## 无人机与生产机制来源

- PRTS 罗德岛基建：基础无人机恢复速度和无人机容量。
  - https://prts.wiki/w/罗德岛基建
- PRTS 发电站：每架无人机减少3分钟基础时间、发电站5%基础加成和干员充能加成。
  - https://prts.wiki/w/罗德岛基建/发电站
- PRTS 贸易站：普通龙门币订单、合成玉订单、订单概率分布和特殊订单表。
  - https://prts.wiki/w/罗德岛基建/贸易站
- PRTS 制造站：赤金、源石碎片和作战记录的基础生产时间、仓库容量与配方成本。
  - https://prts.wiki/w/罗德岛基建/制造站

采用日期：2026-07-28。结构化数值写入 `assets/mechanics.json`，计算实现位于 `scripts/drone_model.py`。
