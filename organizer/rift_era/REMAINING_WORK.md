# 裂陆纪元待实现清单（历史审查归档）

> **当前状态（2026-11-24 更新）**：本文件记录的是 2026-11-05 至 2026-11-07 引擎研发与合入期间的待办审查过程。截至现行冻结版本（`rift_era_rules_r9`、`profile_revision = 2026-11-21-rift-3style-v2`），[`engine/release.py`](engine/release.py) 中全部 10 项正式发布门禁已关闭（`PENDING_RELEASE_GATES = ()`），完整 8 席 × 28,800 Tick 对局、83 条实时注册命令（[`COMMAND_SURFACE.md`](COMMAND_SURFACE.md)）、五支柱采集、17 项 `round6_scenarios` 回归、3 风格强参考对手池（`hegemon` / `arbiter` / `verdant`）与正式评测流水线均已接通。现行状态请以 [`README.md`](README.md)、[`ENGINE_VERSION.md`](ENGINE_VERSION.md) 与 [`config/scoring_v2.json`](config/scoring_v2.json) 为准；下文保留原始历史清单供溯源。

本文件是接手实现时的工作清单，按优先级排列。背景和已接入内容见 [HANDOFF.md](HANDOFF.md)，完整对局验收见 [FULL_MATCH_TEST_TODO.md](FULL_MATCH_TEST_TODO.md)。

审查基线：提交 `06afab4`，引擎指纹 `28011425…a016`，规则 `rift_era_rules_r6`。

## 结论

- **引擎：** 已完成的部分质量可靠，但还不能跑完整对局。现在只有开局经济入口，61/75 条规则命令已注册，五支柱采集和批次评分都没有装配。
- **交接文档：** 主要说法经核对属实，可以作为入口。但遗漏了本文件 P0-4、P1 的部分条目，也有几处过期，见最后一节。

## 已核对通过

| 项目 | 结果 |
|---|---|
| 回归证据 | `validation/handoff_full_20261105.json` 记录 536 项 0 失败，其引擎指纹与当前源码一致 |
| 确定性 | seed 1000 跑 150 Tick，`PYTHONHASHSEED=0` 和 `12345` 的最终状态哈希相同 |
| 开局自举 | seed 1000 通过核心检查 |
| 新候选地图 | `world_atlas` 的 seed 1000 无玩家命令跑 150 Tick，全局审计通过；这只是组件探测，不是地图验收 |
| 命令字段 | 已注册命令与 06 表做了双向比对，字段冲突只有下面的 `corridor_policy` |
| 主线接线 | 交接中已声明接入的系统都有阶段调用；`OperatingWorld` 的系统都进入快照或属于缓存 |
| 活跃挂单上限 | 现货、债券、证券、AI 服务都执行了 256 单上限 |

## P0：完整对局的前置条件

### P0-1 完整对局入口

> 2026-11-06 已实现（分支 `rift-core`）：`engine/match.py` 完整装配（与开局共用 `assemble_economy`，另加国力采集系统）、`python3 -m organizer.rift_era match|strategy-match`、`finalize_match`（终局不结算不免债；仅正式时钟＋全周期＋无待定指标才认证 `full_rules_accepted`）。发布门禁未动。口径见 `IMPLEMENTATION_CONTRACT.md` 的 `rift_full_match_v1`。剩余：完整 28,800 Tick 实跑受性能限制（P0-4）。

`opening_economy` 限制最多 150 Tick，且没有正式运行命令。需要交付：

- 28,800 Tick 的完整装配和命令行入口，继续保留发布门禁。
- 真实的终局处理和正式 finalizer。`driver.py` 已要求 finalizer 认证 `full_rules_accepted`，目前没有实现。
- 探测结果：临时脚本绕过限制后，旧地图 seed 1000 无玩家命令跑到 600 Tick，全局审计通过，每 50 Tick 耗时保持 49–65 秒。这只说明空局前 2% 没崩，不能只删除 150 Tick 限制就算完成。

### P0-2 五支柱采集与评分

> 2026-11-06 已接入生产（分支 `rift-core`）：`engine/national_power.py` 每周期采集五支柱并提交 `Trajectory`（保留全部周期行），读取所列只读 book，NFA 不读实时汇率（`fx_revaluation_invariance` 测试 −200 恒定），`Loss_action` 唯一登记表，可持续性、`active_scale` 接入。`relative_ratio/score_interval` 由批次运行器（P2）使用，单局只出 V。~~剩余：E12/L11/L12/G02/G07/G12/V07 七个指标无实体，记 0 并列入 `pending_rule_inputs`。~~ 已接（2026-11-06，`active_scale_entities_v1`）：七项均有引擎计数，`pending_rule_inputs` 为空；L12 巨构、G02 野战站的物理效果和 G07/G12/V07 的规则效果仍未做（见下“有定义但没有效果的科技”）。

`scoring.py` 的 `industrial_pillar`、`control_pillar`、`technology_pillar`、`international_pillar`、`relative_ratio`、`score_interval` 都没有生产调用。

- 每周期统一采集国力，保留 2,880 周期轨迹。
- 接入已有的只读接口：`asset_book`、`debt_book`、`long_book`、`external_equity_book`、`short_liability_book`、`creditor_book`、`scoring_inputs`、`population_scoring_rows`。保证金已在现金账内，不要重复计入。
- 完成 `active_scale.py` 的待定指标后再接入主线。它目前未被任何模块导入。
- 实现 NFA、可持续性、Loss_action 的唯一归属，以及 `fx_revaluation_invariance` 要求的汇率变化不重估。

### P0-3 正式地图接线

> 2026-11-06 已完成（分支 `rift-core`）：`match`/`strategy-match` 入口用 `world_atlas.generate_world`，接受只来自 `match.bootstrap_check` 的真实 150 Tick 自举，失败重抽（seed 1000 attempt 0 实跑 150 Tick 通过，272.8 s）；500 个远端矿点注册为 `resource_sites/resource_sources/deposits`（水矿共用 `basin:<hub>`，非水矿建自然储量池），起始矿储量读 atlas；`Hydrology` 读 `world.river_downstream`（旧地图无河网时沿用原生成）；`world`/`bootstrap` 命令默认 atlas，`--legacy-map` 保留旧候选；城邦经济直接使用城邦 Hub 上的 84 个 atlas 矿点，吞并后移交同一套矿藏（旧地图无 atlas 时回退用 `city_states_v1`）。

- 入口仍调用旧 `world.generate_candidate`。`world_atlas.generate_world` 没有被主线使用。
- 572 条 `resource_atlas` 中只有 72 个起始 deposit 可开采，其余远端矿点要注册进 `resource_sites/resource_sources/deposits`。
- 起始储量统一读 atlas，`Hydrology` 改读 `world.river_downstream`。
- 地图接受必须来自真实 150 Tick 自举，失败后重抽。

### P0-4 引擎性能（交接未提及）

现状：无玩家命令时约 **1 秒/Tick**，新旧地图接近。按此线性估算，一局约 8 小时，64 seed 不含影子局也要约 21 CPU 天。实体规模增长后还会更慢，所以这是下限。

20 Tick 剖面的主要耗时（含剖面器开销）：

- `materials` 的 `intersection_update`（约 11 秒/20 Tick）
- `determinism` 的键生成，约 32 万次调用（约 11 秒）
- `Fraction` 运算（约 8 秒）

目标：单局墙钟上限由接手 AI 决定，用户已授权，不用再询问。定下后写明数值和依据，要能跑完 64 seed 正式批次（含影子局）。优化不能改变任何状态哈希，必须用同 seed 新旧哈希对比验证。

**2026-11-06 进展（分支 rift-perf，11 步，84ddfb0）：** 同机同 seed、单进程、150 Tick 无命令：seed 1000 1.322→0.614 s/Tick，seed 2024 1.403→0.646 s/Tick（约 2.15×，aarch64 2 vCPU 沙箱）。11 步每步在 seed 1000/2024、PYTHONHASHSEED 0/12345 下 Tick 50/100/150 哈希与 bee811c 完全一致。

**定下的目标：** 单局（28,800 Tick）单核墙钟 ≤ 6 小时，即含策略 IPC 平均 ≤ 0.75 s/Tick。依据：一批 64 正式局 + 64 影子局 = 128 局 × 6 h = 768 核时，32 核约 24 小时跑完；无命令约 4.9 h/局，余量约 20%。回归门：本沙箱 seed 1000 开局 150 Tick 无命令 ≤ 0.65 s/Tick。待 P0-1 完整入口和真实实体增长后复核。剩余热点：power、production（_materials/take）、reserve_operations、settlement、reserve，突破 3× 需结构性改动。

## 口径审定后的待办（2026-11-06）

作者已审定 `IMPLEMENTATION_CONTRACT.md` 中待确认的口径。以下 5 项均已按审定结果在引擎中改完：

| 项目 | 要做什么 | 代码位置 | 状态 |
|---|---|---|---|
| ~~工业 VA 归属~~ | 按 `eff_share` 穿透分配，不再全额算给发行国 | `industry_activity.py`、`national_power.py` | 已完成（2026-11-06，`author_review_fixes_v1`） |
| ~~连通~~ | 实现替代指挥 Hub，路径读实际授权和损毁状态 | `national_power.py` 的 `command_hubs` / `connected_regions` | 已完成（2026-11-06，`author_review_fixes_v1`） |
| ~~城邦矿点~~ | 城邦经济改用 atlas 矿点，吞并后不能有两份矿藏 | `city_state_economy.py`、`industry_commands.py`、`data/city_states_v1.json` | 已完成（2026-11-06，`author_review_fixes_v1`） |
| ~~Kinfra 线性边~~ | 边资产登记两端，按规则各算一半 | `national_power.py` 的 `_kinfra` | 已完成（2026-11-06，`kinfra_edge_halves_v1`） |
| ~~ActiveScale~~ | 补齐 7 个待定指标的实体或回执，清空 `pending_rule_inputs` 后完整对局才能被认证 | `active_scale.py` 和对应业务模块 | 已完成（2026-11-06，`active_scale_entities_v1`） |

## P1：规则缺失

### 缺失的 14 条命令

06 中定义、引擎未注册。调用时返回 `ERR_SCHEMA`，不是空操作。

| 命令 | 依赖或说明 | 交接是否列出 |
|---|---|---|
| ~~`logistics/forward_contract`~~ 已接入（2026-11-06，`forward_strike_v1`） | F03，保证金 20%，违约扣押 | 是 |
| ~~`finance/secondary_offering`~~ 已接入（2026-11-06，`forward_strike_v1`） | C30，定向投向设施 | 是 |
| ~~`finance/fx_short`、`finance/fx_cover`~~ 已接入（2026-11-06，`forward_strike_v1`） | F03 | 是 |
| ~~`diplomacy/terminate_contract`~~ 已接入（2026-11-06，`cyber_climate_continuity_v1`） | 通用终止和归责 | 是 |
| ~~`military/hypersonic_strike`~~ 已接入（2026-11-06，`forward_strike_v1`） | N10，仅 G11 可拦截 | 是 |
| ~~`military/blockade`、`military/interdict`~~ 已接入（2026-11-06，`forward_strike_v1`） | `combat.blockade_fraction` 已有公式，尚未调用 | 是 |
| ~~`intelligence/steal_model`~~ 已接入（2026-11-06，`cyber_climate_continuity_v1`） | E84 | 是 |
| ~~`governance/climate_intervention`~~ 已接入（2026-11-06，`cyber_climate_continuity_v1`） | V08 | 是 |
| ~~`diplomacy/grant_climate_consent`~~ 已接入（2026-11-06，`cyber_climate_continuity_v1`） | V08 跨国授权 | **否** |
| ~~`intelligence/cyber_op`~~ 已接入（2026-11-06，`cyber_climate_continuity_v1`） | S03，E82，E85，C33 | 仅写了 S03 |
| ~~`governance/conditional_plan`~~ 已接入（2026-11-06，`cyber_climate_continuity_v1`） | 每窗口首个 P1 判定，有冷却 | **否** |
| ~~`research/set_backup_hub`~~ 已接入（2026-11-06，`cyber_climate_continuity_v1`） | 研究备份枢纽 | **否** |

### 与冻结规则不一致（交接未提及）

1. ~~**`finance/stock_order` 没有检查 F03。**~~ 已修（2026-11-05）：融资买入和融券卖空未解锁 F03 时返回 `ERR_TECH_LOCKED`。 06 要求融资和融券先解锁 F03，但整个引擎里没有 `F03` 判断，开局即可融资融券。应返回 `ERR_TECH_LOCKED`。
2. ~~**`logistics/corridor_policy` 接口不符。**~~ 已改（2026-11-05），口径见 `IMPLEMENTATION_CONTRACT.md` 的 `corridor_policy_tiers_v1`；~~预锁费用和在途掉头仍未做~~ 已做（2026-11-06，`corridor_toll_lock_turnback_v1`；客运预锁也已做，客运遇关边掉头未做）。
   - 规则：`access_tier: 0..3` 和 `toll_rate_bp: 0..2000`，费用按实际载货批次的基数计算。
   - 引擎：`allowed: bool` 和固定的 `toll_ru_mU`，见 `transport_operations.py:696`。
   - 费率还写在走廊上，改一国费率会影响所有国家。`blockade_food_substitution` 依赖 `access_tier`。
3. ~~**本国地区的支持度事件几乎没接。**~~ 已接（2026-11-05），口径见 `IMPLEMENTATION_CONTRACT.md` 的 `support_events_v1`；~~C05 对医疗系数的 B1 修正仍未接~~ 已接（2026-11-06）。 `local_economy.py:44` 把 `occupied` 写死为 `False`，`events_mU` 只有经济学说切换一项。因此下列规则未生效：敌占 −12、C04 黑网 −20、E26 B1 欠供、E71 bombard −300。

### 未找到实现的耦合

以下是按公式特征检索代码的结果，接手时先核对一次。

| 编号 | 内容 |
|---|---|
| ~~C06、C07~~ | 已接（2026-11-06，分支 rift-gaps，`coupling_gaps_v1`）：进口平价进入调度边际成本、CPI 与加速器补充成本 |
| ~~C15~~ | 已接（2026-11-06，`coupling_gaps_v1`）：线损 ×(1−min(.3,.01·Cap·s)) |
| ~~C16~~ | 已有实现，2026-11-06 补测试 |
| ~~C18~~ | 已接（2026-11-06，`coupling_gaps_v1`；C08 ×0.45 需在线融合传感器） |
| ~~C19~~ | 已接（2026-11-06，`coupling_gaps_v1`） |
| ~~C24~~ | 已接（2026-11-06，`reserve_life_factor`） |
| ~~C25~~ | 已接（2026-11-06，`coupling_gaps_v1`） |
| ~~C28~~ | 已接（2026-11-06）：跨国线容量（`coupling_gaps_v1`）与 Credit<0.3 禁签电力合同（`e12_power_contract_v1`） |
| ~~C33~~ | 已接（2026-11-06，`cyber_climate_continuity_v1`）：S03 命中后暂停发行国下一次证券竞价 |

C03、C05、C09、C13、C14、C22、C23、C29、C31、C32 已确认有实现。

### 有定义但没有效果的科技

代码和配方数据都没有引用：

- ~~`A07` 精确农业~~ 2026-11-07：农场装 `sensor` 且解锁 `A07+C08` 并有在线 `fusion_sensor` 时水/肥减 25%、恶劣天气降水系数 +0.15（封顶 1.0），`p1_tail_hooks_v1`
- ~~`E12` 洲际能源互联~~ 2026-11-06：海底超导线可建、跨洲线损封顶 8%、计量已接；跨国电力合同按实际潮流结算已接（`e12_power_contract_v1`）
- ~~`F04` 主权基金~~ 2026-11-07：工业 JV 外资份额上限 49%，条款 4 支持 `mode="acquire_shares"` 收购既有建成设施 ≤49% 份额，战时冻结敌国股东分红/分摊，`p1_tail_hooks_v1`
- ~~`G07` 协同作战~~ 2026-11-06：计量与战斗效果（×1.2、共享 q .92）已接，`military_doctrine_v1`
- ~~`G12` 全域联合指挥~~ 2026-11-07：计量与 ×1.1、10 tick 冻结、跨战区行军路径接力与补给源断供自动切换均已接，`p1_tail_hooks_v1`
- ~~`I12` 分子级制造~~ 2026-11-07：S5 设施 + `P013/P016/P087` + `ai_core` 原料减 30%（40% 下限）、免除无 `sensor` 的 6% 次品率、欠电当拍在制品报废记 `Loss_destroy`，`p1_tail_hooks_v1`
- ~~`L12` 巨构工程~~ 2026-11-06：桥隧 800 载重走廊＋同额定高压线、人工岛近海 Hub 已接（`l12_megaproject_effects_v1`）
- ~~`V07` 生态修复~~ 2026-11-06：关停满 120 周期区域 biomass/水再生 +50% 已接（`v07_regeneration_v1`）
- ~~`V08` 区域气候干预~~ 2026-11-06：`climate.py` 已实现降水与风光出力修正

`F03` 同样已接入。其他 43 个科技有配方或军事数据门控。

### 已写好函数但生产路径未调用

| 函数 | 待接入的规则 |
|---|---|
| ~~`Authority.military_operating_factor`~~ | 敌占区军事运行系数 0.6（产线、采掘与电站均已接入，`p1_tail_hooks_v1`） |
| ~~`FacilityOwnership.prepare_cost_payment`~~ | JV 经营成本按份额分摊：2026-11-06 由 `share_costs` 接入，`split_payment` 键修复（`jv_cost_share_v1`） |
| ~~`CityStateEconomy.record_coercion`~~ | 胁迫城邦的关系后果（已于 `cyber_operations.py` `S08_paralyze` 接入） |
| ~~`Consumption.synthetic_share`~~（`eta` 已于 2026-11-06 接入工业 VA） | 评分采集（已于 `national_power.py` 接入人口支柱） |

### 交接已列出的其余项

- ~~破产重整和强制拍卖~~ 已接（2026-11-06，`sovereign_insolvency_v1`；拍卖货物经 r7 世界池非合格运输腿转入世界池实物库存，可被 `trade/pool_buy` 买走；国储/公司库存区分依赖产权标记，见合同节“未做”）。
- ~~投降和俘虏~~ 已接（2026-11-06，`surrender_prisoners_v1`，含停战送返与割让国有资产份额移交；割让后产线对象整体移交未做）。
- 完整野战补给和航空航程。
- 占领后的居民、工资和税币归属：已接征税方/税币/工资结转/人口观测（2026-11-06，`occupation_residents_v1`）；居民非原主币种存款的消费未做。
- 设备维修、残值和抵押的经济所有权。
- 客运跨模态换乘，Cargo 与客运同档统一容量仲裁，水渠 40% 上限。
- 电力合同的专用负荷和交付。
- 城邦转口的封堵补偿。
- 融资融券的强平连锁、费用并入本金和跨托管卖出，见 [HANDOFF_FINANCE_20261105.md](HANDOFF_FINANCE_20261105.md)。

## P2：比赛交付

- **参考策略：** 没有能走正式协议的参考池。`designs/rift_era/prototype/` 的 bot 基于原型模拟器，不能直接用。
- **批次评分：** `evaluation.py` 的 `Batch.write_new`、`shadow_key`、`ResourceBudget.concurrency` 没有运行器调用。需要实现 64 seed、影子基准和 `INVALID_EVALUATION`。
- **题库接入（OPEN_ISSUES RE-1）：** `contestant_readme.md`、starter、公开基线、`spar.py`、`score.py`，以及 `score_all.py`、`pack_contestant.py` 接入。
- **计分校准（OPEN_ISSUES RE-2）：** V 梯度目标已定：
  - 混坐 64 seed 的平均 V 满足 `Super > Advanced > Medium`，相邻两档的 95% 置信区间不重叠。
  - 具体差距暂不定，等正式参考策略跑出数据后再定。
  - 现状：原型混坐 seed 1000 时 V 倒挂（Super 78.8 < Advanced 139.2），这只是单个 seed 的原型数据。
  - 先处理现金在 NFA 中不封顶、库存不计值这两个主因。两项都要等 P0-2 的真实 NFA 采集接上后才能验证。
  - 如需改评分规则，发新规则版本，不要就地改现行规则（`rift_era_rules_r8`）的文件。
- **快照缺口：** ~~`EquityFinance.sequence` 影响强平单 ID，但不在 `snapshot()` 里，应补上。~~ 已补（2026-11-06）。~~`Macroeconomy`、`Payments`、`ForeignExchange`、`DebtFunding`、`Relations`、`Battles` 等没有 `audit()`~~ 已补 12 个（2026-11-06，`subsystem_audits_v1`）。
- **宿主：** 512 MiB 管道容量和 cgroup 委派的问题仍未解决，见 `IMPLEMENTATION_CONTRACT.md` 的策略 IPC 一节。

## 2026-11-06 r7 平衡修改（决策0014）

规则升为 `rift_era_rules_r7`（用户 2026-11-06 批准）。逐条状态见 `IMPLEMENTATION_CONTRACT.md` 的 `balance_findings_v1` 处理状态表，验证记录 `validation/r7_balance_20261106.json`。

- 已做：E9（P070 排放 0.2）、E3（开局 P033 0.2 批 + medicine 120）、E2（eta 的 C 只对已有周期平均）、E8（仓库按占用计 Kinfra）、02 §NPC 市场世界池实物买卖（`world_pool.py`，`trade/pool_buy`/`pool_sell`）。
- 未达目标、需作者再定：E9 只降到约 0.52 倍（非发电线排放 2.30/周期未授权修改）；E2 开局 VA 仍 ≤0（开局库存本身超过 12 倍消纳，需选 (b)(c)(d)）；E3 只保证约 780 tick 不断药（劳动力余量只容 0.2 批）。
- 仍未做：E6 最低稳燃空烧、E5 旧地图矿脉（按原判定不改）；SimpleBot 未使用世界池；`world_pool_pressure` 场景现已可写（机制已有）。

## 2026-11-06 r8 平衡补改（决策0015）

规则升为 `rift_era_rules_r8`（用户 2026-11-06 18:10 CST 批准）。验证记录 `validation/r7_balance_20261106.json`。

- 已做：E9 P001/P002/P005/P007 排放×0.55（首都排放约为 r6 的 1/3）；E2 开局禀赋 20 周期线性退出 eta 库存计数（开局制造线 VA 转正）。
- 未达目标：E2 只在开局约 5 个周期使正常经营优于闲置；之后开局库存（food 180 等）仍使 chi>12，tick 400 正常比闲置低约 57 分，需作者另定 `balance_findings_v1` E2 的 (c) 或 (d)，并在 64 seed 中比较。

## 2026-11-06 第 3 块收尾后仍未完成（按重要性）

本轮验证：`match --ticks 400 --legacy-map --bot simple` 在 `PYTHONHASHSEED=0/12345` 下状态哈希一致（`9178c2c9…b143`），`pending_rule_inputs=[]`，仅因练习时钟不认证，见 `validation/e2e_match_block3_20261106.json`。

本轮已做：过境费预锁/掉头（货运＋客运预锁）、C05、七个 ActiveScale 计量实体、G07/G12 战斗条令、敌占军管 0.6、`EquityFinance.sequence` 快照。以下仍未做：

1. **P1 规则效果**：L12 桥隧走廊（800 载重＋同容量高压线）与人工岛新增 Hub；G02 补给半径 2/5 跳与野战补给运输单；V07 再生率恢复；E12 跨国合同按潮流自动结算；G12 跨战区接力与补给源自动切换；A07、F04、I12、V08 等其余无效果科技；客运遇关边掉头。
2. **P1 耦合**：C06/C07 进口平价、C15 线损、C16、C18、C19、C24、C25、C28。
3. **P1 未调用函数**：`FacilityOwnership.prepare_cost_payment`（JV 成本分摊）、`CityStateEconomy.record_coercion`、`Consumption.synthetic_share/eta`；电站出力未乘军管 0.6。
4. **P1 交接项**（破产拍卖、投降俘虏、占领税币已于 2026-11-06 rift-war 分支接入，余项见各合同节“未做”；400 tick 旧地图无命令/`--bot simple` 在 `PYTHONHASHSEED=0/12345` 下哈希一致、审计通过，见 `validation/e2e_war_20261106.json`）：完整野战补给与航空航程、设备维修残值抵押、客运换乘与统一容量仲裁、水渠 40% 上限、电力合同专用负荷、城邦转口封堵补偿、融资融券强平连锁。
5. **P2**：参考策略池、批次运行器（64 seed、影子局、`INVALID_EVALUATION`）、题库接入、计分校准、`Macroeconomy/Payments/ForeignExchange/DebtFunding/Relations/Battles` 的 `audit()`、宿主 IPC 管道/cgroup、场景运行器。
6. **文档**：README 0.4.0 说法与 `ENGINE_VERSION`、`command_surface` 记录、HANDOFF 补充。

## 2026-11-06 rift-gaps 分支收尾后（需作者确认各合同节）

已做（分支 `rift-gaps`）：`subsystem_audits_v1`、`coupling_gaps_v1`（C06/C07/C15/C16/C18/C19/C24/C25/C28，C08 ×0.45）、`jv_cost_share_v1`、`g02_field_supply_v1`、`v07_regeneration_v1`、`l12_megaproject_effects_v1`、`e12_power_contract_v1`、`grid_dispatch_v1`（有高压线时调度不再退化到 1 mU 参考算法，逐字段与参考一致）。

仍未做：

1. **电网调度剩余瓶颈**：跨枢纽送电仍按 1 mU 逐步（负荷间轮转），大范围电网＋大缺口时（压力测试 46 枢纽链、约 2 500 mU 跨枢纽）约 2.3 s/调度。下一步可把非本地阶段按“同一最优选项的负荷集合”做水位批量（需证明与参考一致）。
2. **E12 整局端到端**：经济循环收集回执计量没有整局测试；退款账户（reserves vs `corp:energy`）与价格上限是否含过网费待作者定。
3. ~~**C08 轨迹 +10 tick**、G12 跨战区接力与补给源自动切换、A07/F04/I12/V08 无效果科技、客运遇关边掉头、`CityStateEconomy.record_coercion`、`Consumption.synthetic_share`、电站出力未乘军管 0.6。~~ 已于 2026-11-07 全部补齐（`p1_tail_hooks_v1`，见 `tests/test_wired_functions.py`）。
4. **长局测试与场景验收**：正式测评保持 **64 seed × 28,800 Tick**（`seed_count: 64`），本机长局验证测试跑 **1 seed × 28,800 Tick**；17 个 `round6_scenarios.json` 场景已由 `tests/test_round6_scenarios.py` 纳入自动回归。

## 15 个动态场景的就绪情况

这是静态判断，场景都没有执行过。引擎侧没有场景运行器，`round6_scenarios.json` 只被文档引用。

| 状态 | 场景 |
|---|---|
| 机制已有，可以先写场景测试 | `patrol_loop`、`repair_single_count`、`healthy_relocation`、`cpu_budget`、`zero_material_hostility`、`damage_power_and_repair`、`relations_and_neutrality`、`barrage_no_gains`（缺 bombard −300） |
| 需要评分采集（P0-2） | `fx_revaluation_invariance`、`transit_formula`、`world_pool_pressure`（世界池实物买卖已于 r7 装配） |
| 需要缺失命令或修正接口 | `cyber_credit_floor`（`cyber_op`）、`blockade_food_substitution`（`corridor_policy`）、`energy_substitution`（`corridor_policy`） |
| 需要批次运行器 | `batch_consistency` |

## 文档待修

- `README.md`：多处仍是 0.4.0 时的说法，例如“尚未接入”和“下一项实质性交付”。`ENGINE_VERSION` 也还是 0.4.0，冻结新版本时要一起升。
- `validation/command_surface_20261104T180634Z.json`：记录 42 条已注册，当前是 67 条，需要重新生成或标为历史记录。
- `HANDOFF.md`：第 3、4 项应补上表中标“否”的命令；第 8 项应补上性能。

## 建议顺序

1. 先修两处规则不一致（F03、`corridor_policy`）和支持度事件，改动小，能解除场景阻塞。
2. 同时做 P0-3 和 P0-4。地图接线后实体规模才接近真实，这时做性能优化才有意义。
3. 完成 P0-1 和 P0-2，让引擎能跑完一局并出分。
4. 补缺失命令和耦合，每项都写场景测试。
5. 做参考策略和批次运行器，再把 `FULL_MATCH_TEST_TODO.md` 交给测试方。
