# 裂陆纪元状态

2026-11-15，规则版本 rift_era_rules_r9（用户批准，[决策0016](decisions/0016_r9_world_pool_spread_warehouse_priority_and_balance.md)）：世界池双向点差（买 11.5·w_cat、卖 8.5·w_cat RU）；同枢纽公共仓容非采掘制造线优先、采掘线次之，且 `industry/plant_toggle` 支持采掘场开关；开局非回填禀赋余量永久豁免 eta/chi 库存分子；开局 P033 0.2→0.5 批、耕地肥力 1.1→1.25、medicine 120→160、food 180→210、grain 90→120，未装机工业空壳 Kinfra 按 0.25 计入；补齐在途截击接触别名/射程/可见性/域通道、被占领区居民异币储蓄真实换汇消费、和约割让底层物理归属移交与跨境电力退款 `reserves` 兜底。规则指纹（引擎 `Catalog().fingerprint`）`184f4c681049219166e196c11ecbddbf21e08885eb6dcce60fd025258c5c42b1`。下文 r8/r7/r6 记录保留为历史。

2026-11-06，规则版本 rift_era_rules_r8（用户批准，[决策0015](decisions/0015_r8_emissions_and_endowment_phase_in.md)）：P001/P002/P005/P007 排放系数各 ×0.55（0.5/0.3/0.4/0.3→0.275/0.165/0.22/0.165）；开局禀赋库存在前 20 周期线性退出 eta/chi 的库存计数（第 k 周期只计 k/20）。规则指纹（引擎 `Catalog().fingerprint`）`53e5619e66651c6c7c897e52fb291d27de88250d096c2e990a268e3ed6c6189c`。下文 r7 记录保留为历史。

2026-11-06，规则版本 rift_era_rules_r7（用户批准，[决策0014](decisions/0014_r7_opening_balance_and_world_pool.md)）：P070 排放 1→0.2；开局加 1 条 P033（S2，0.2 批）、medicine 30→120；eta 消纳分母只对已有周期取平均；仓库按实际占用计 Kinfra；世界池实物买卖实装。规则指纹见引擎 `Catalog().fingerprint`。下文 r6 记录保留为历史。

2026-11-04，规则已冻结，版本rift_era_rules_r6，一切以Round 6为准（用户确认）。规则指纹：`4527218afd4b2eddc4783d408c6162e156d8d1734cb52f55322f444f7c549716`（覆盖六份规则JSON）。

- 审核链：Gemini Round5通过 → Opus5.5终审不通过，提出OR-001～017 → Astra Round 6修订 → Gemini Round 6复审17项全部CLOSED，审核通过。Round5及更早的通过或冻结声明不再作为依据。LOG中没有Opus对Round 6的复核记录；冻结依据是Gemini复审与用户确认。
- 终审报告：[FINAL_REVIEW_opus55_2026-11-03.md](review/FINAL_REVIEW_opus55_2026-11-03.md)（保持原样）。逐条处理见[LOG.md](review/LOG.md)，用户明确的敌对行动损耗要求已写入README和07。
- Round 6新增行动损耗、战争/封锁/修复状态、历史RU账面NFA、完整NetTransit及迁居/CPU批次契约。目录/算术检查与正式引擎执行证据分开。
- 冻结后不再就地修改规则或参数；确需变更时发布新规则版本。
- 冻结范围是规则文档与六份规则JSON。地图分布`rift_spatial_stratified_v1`和开局禀赋`rift_opening_endowments_v1`是引擎侧独立版本，不在此次冻结内。
- 冻结不等于已验收：原型仍prototype_v13_5pillar_approx，没有实现Round6规则；data/round6_scenarios.json是正式引擎待执行场景及独立算术预期，不是已跑通的游戏测试报告；空间引擎、沙箱、动态自举、并发压力和64seed平衡仍待实现/实测。
- data/review_cases.json顶层rules_version保持r4：其49个cases是Round 4的检查记录，Round 6的检查在round6_checks。

## 2026-11-04 bot 更新（按 2dfb5e8 规则，原型五支柱 V）

- Super 改为 `ref5.Super14`（优化原型五支柱 V，仍 8/8 六阶）；Advanced 只修被拒命令；Medium 未改。详见 prototype/README.md 末节。
- 单席 8 局：TP Medium 0.140×、Advanced 0.412×；V Medium 0.66×、Advanced 0.94×（Super14 V 170.1，旧 Super13 在新计分下 V = −48.5）。
- 待用户决定：V 梯度目标；现金在 NFA 中不封顶计入、库存不计值使 V 与 TP 梯度不同向；sim 跨国电力自动买电与 AI 事故罚款不查现金、可使国库为负（违反 11 号“只转移实有钱”，未改 sim）。
- 混坐 3:3:2 seed 1000：TP 梯度正常（Adv 0.29×、Med 0.11×），但 V 倒挂（Super 78.8 < Advanced 139.2 < … Medium 101.1）。
- 未跑：exploit.py、shock.py、score.py、verify_nochange.py（用户要求加快）。
