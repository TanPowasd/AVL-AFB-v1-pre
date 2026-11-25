# 裂陆纪元正式引擎与评测说明

**当前状态（已正式发布并冻结）：[`engine/release.py`](engine/release.py) 中全部 10 项正式发布门禁已关闭（`PENDING_RELEASE_GATES = ()`），完整 8 席 × 28,800 Tick 正式对局、正式空间地图 `world_atlas` + 150 Tick 自举、83 条已注册命令（[`COMMAND_SURFACE.md`](COMMAND_SURFACE.md)）、五支柱国力采集与正式 `scoring_v2` 评测流水线均已接通并纳入总分。**

- **引擎与规则指纹**：引擎版本字符串仍保留 `rift_era_engine_0.4.0`（权威身份以指纹为准，详见 [ENGINE_VERSION.md](ENGINE_VERSION.md)），当前规则基线为 [`rift_era_rules_r9`](designs/README.md)（r6 冻结后经决策 0014、0015、0016 修订，六份机器数据从 `designs/data/` 只读加载并参与指纹；仅依赖 Python 3.11+ 标准库）。
- **正式评测配置**：[`config/scoring_v2.json`](config/scoring_v2.json)（修订 `2026-11-25-rift-3style-local-designs`）冻结了引擎、规则、流水线与 3 位互补强风格参考对手（`hegemon`、`arbiter`、`verdant`），正式参数为 **`64 seed × 28,800 tick`**（单局 5,760 决策窗口 / 2,880 工业周期 / 4 个 720 周期纪元，单局墙钟约 5 小时，超时上限 `43,200 s`，内存上限 `1 GiB`）。
- **历史文档说明**：[HANDOFF.md](HANDOFF.md)、[REMAINING_WORK.md](REMAINING_WORK.md)、[FULL_MATCH_TEST_TODO.md](FULL_MATCH_TEST_TODO.md) 与 [IMPLEMENTATION.md](IMPLEMENTATION.md) 保留了 2026-11-04 至 11-07 研发期的历史检查点记录，现行行为以本文件、[ENGINE_VERSION.md](ENGINE_VERSION.md) 与源码为准。

## 评测流水线（`pipeline/`，入口/IPC 层，不改引擎规则）

> 版本与指纹：[ENGINE_VERSION.md](ENGINE_VERSION.md)；命令表：[COMMAND_SURFACE.md](COMMAND_SURFACE.md)（83 条已注册命令，由注册表自动生成）。

| 文件 | 作用 |
|---|---|
| `pipeline/hosting.py` | 选手进程启动：Linux 支持 `cgroup` / `bwrap`，原生 Windows 支持 `windows_appcontainer` / `windows_job_isolated`，无隔离时回退 `none` 并拒绝 `official=True` |
| `pipeline/match_runner.py` | 单局 8 席：引擎 `StrategySession`+`MatchDriver`（窗口、分页、0.5 s 基础窗/450 s time_pool/30 s 单窗/3600 s 总时长按规则计时与托管）；`official=True` 强制校验沙盒、完整 `28,800` Tick 与正式 `world_atlas` 地图 |
| `pipeline/batch.py` | 多 seed 批量：冻结 manifest、每局独立解释器、原子写结果、断点续跑；并发按 07 §4 的 CPU/内存公式 |
| `pipeline/scoring.py` | scoring_v2 钩子：影子局（同 seed 8 席由 `hegemon` / `arbiter` / `verdant` 按确定性混编坐镇并缓存）+ 替换局（第 `k mod 8` 席换选手）→ `50+50·tanh(0.6·ln R)`；由 `organizer/strategy_scoring.py` 调度、bootstrap 95% 区间 |
| `pipeline/command_surface.py` | 从 `EngineCommands.dispatch` 生成 `COMMAND_SURFACE.md`、`kit/COMMANDS.md` 与 `validation/command_surface_<UTC>.json` |
| `config/scoring_v2.json` | 私有评分配置（3 风格对手与流水线文件哈希、引擎/规则指纹、64 seed、28,800 tick）；**不进选手包**；`scripts/freeze_profile.py` 重新冻结 |
| `scripts/score.py` | 本题正式/练习评分入口；已在 `organizer/score_all.py` 与 `Final/organizer/score_all.py` 正式注册（`.zip`）并计入总分 |
| `scripts/pack_contestant.py` | 导出 `contestant/rift_era/`（规则、sdk、模板、Medium/Advanced 公开陪练拷贝、同一引擎、本地脚本、手册/题面、回放录制与离线播放器），写 manifest 哈希 |
| `scripts/pack_prompt.py` | 生成聊天模式完整题面 `kit/prompts/task.txt` 并执行纯净检查 |
| `scripts/record_replay.py` | 开发/赛事端可视化回放录制（支持隐藏对手、外部路径、`--serve` 本地播放与引擎复算校验） |
| `scripts/run_match.py` | 单局 8 席对局运行器（支持档位与提交 `.zip`，保存战报至 `results/`） |
| `scripts/duel.py` | 双策略 PvP 对战工具（支持 `4v4` 镜像席位与 `1v1`+6 填充席位，含 ELO 与可选回放录制） |
| `scripts/run_league.py` | 多策略 ELO 联赛（支持 `4v4` 全轮环与 `ffa` 8 席轮转），输出 `results/<时间戳>/{leaderboard.md,league.json}` |
| `score/elo.py` | 8 席次两两虚拟对局 ELO + log-ratio V margin 计分与 Markdown 排行榜渲染 |
| `viz/player.html` | 完全离线单文件回放播放器（512×512 世界地图、八国实时榜、五支柱、宏观轨迹曲线与事件流） |
| `kit/` | 选手包公开源文件（`AGENTS.md`、`AI_README.md`、`README.md`、`GUIDE.md`、`handbook/`、`prompts/task.txt`、`replay_viz.py`、模板 bot 与本地脚本） |

参考与公开对手均位于 `organizer/rift_era/bots/`（公开陪练为 `medium`、`advanced`；正式隐藏评分对手池为 `hegemon`、`arbiter`、`verdant`，另有历史 `super`），也可用环境变量 `RIFT_ERA_BOTS_ROOT` 覆盖路径（正式评分仍校验 `scoring_v2.json` 哈希）。

```bash
# 引擎自带 CLI：完整或短时钟对局（--ticks 须为 40 的倍数）
python -m organizer.rift_era match --ticks 400 --legacy-map --bot simple
python -m organizer.rift_era match --ticks 400 --legacy-map --seats p0.zip,simple,none,none,none,none,none,none

# 流水线单局（席位可写 medium/advanced/hegemon/arbiter/verdant/super 或 .zip），--sandbox auto|bwrap|none|cgroup
python -m organizer.rift_era.pipeline.match_runner --seed 7 --ticks 200 --seats hegemon,arbiter,verdant,hegemon,arbiter,verdant,hegemon,my.zip --out m.json

# 批量：多 seed、断点续跑（重跑同一命令即续跑；manifest 不符则拒绝）
python -m organizer.rift_era.pipeline.batch --seeds 11 12 --seats hegemon --ticks 200 --out-dir runs/a
python -m organizer.rift_era.pipeline.batch --seed-count 64 --seats hegemon --rotate my.zip --out-dir runs/b

# 评分：--ticks 为快速练习模式（official=false，写 results/practice_v2.json）；不带 --ticks/--seeds 才是正式（64 seed × 28,800 tick + 沙盒 + 正式地图）
python organizer/rift_era/scripts/score.py --bots my.zip --seed-count 2 --ticks 200
python organizer/score_all.py <提交目录> --problems rift_era --rift-ticks 200 --strategy-seed-count 2

# 重新冻结评分配置 / 导出选手包 / 生成聊天题面 / 重新生成命令表
python organizer/rift_era/scripts/freeze_profile.py --revision 2026-11-21-rift-3style-v2
python organizer/rift_era/scripts/pack_prompt.py
python organizer/rift_era/scripts/pack_contestant.py --force
python -m organizer.rift_era.pipeline.command_surface

# 可视化回放录制与离线播放器（viz/player.html，支持 --serve 本地预览与默认引擎复算校验）
python organizer/rift_era/scripts/record_replay.py --a advanced --b medium --seed 7 --ticks 80 --legacy-map
python organizer/rift_era/scripts/record_replay.py --a my.zip --b hegemon --seed 7 --ticks 200 --legacy-map --serve

# 开发/赛事端单局、双策略 PvP（4v4 镜像 / 1v1+6 填充）与多策略 ELO 联赛
python organizer/rift_era/scripts/run_match.py --a advanced --b medium --seed 7 --ticks 80 --legacy-map
python organizer/rift_era/scripts/duel.py advanced medium --mode 4v4 --seeds 7,11 --ticks 80 --legacy-map
python organizer/rift_era/scripts/run_league.py medium advanced hegemon --mode 4v4 --seed-count 2 --ticks 80 --legacy-map
```

一局正式 28,800 tick 约 5 小时；评分每个 seed 要影子局 + 替换局两局（影子局按 seed / 引擎 / 规则 / 参考池哈希缓存在 `results/shadow_cache/`，同批多个提交共用）。

## 运行与验证

在仓库根目录执行：

```bash
python3 -m organizer.rift_era check
python3 -m unittest discover -s organizer/rift_era/tests -v
python3 -m organizer.rift_era world --seed 1000
python3 -m organizer.rift_era bootstrap --seed 1000
python3 -m organizer.rift_era scenario --seed 1 --ticks 100
python3 -m organizer.rift_era scenario --seed 1 --ticks 100 --decision-ticks 1
```

`world` 构造 512×512 的空间候选及八大国开局模板，输出规模与状态指纹；`bootstrap_accepted=false`，没有把几何检查冒充 150 Tick 动态自举。`scenario` 执行两个枢纽之间的生产、运输、卸货和返程，是开发用集成场景；P3 电力/劳动额度是明确的外生测试条件，不计算正式国力。

`bootstrap` 实际执行 150 Tick 八国开局经济：P1 冻结民生、工资、工业原料及电力预算；真实有限清算池换汇；P3 消耗煤/水并付电费；P6 采掘与制造共同竞争仓容；P7 实付工资、居民采购、税收、宏观与人口/污染更新。它检查资金/物资守恒、粮食缓冲、工资欠款及自然供水，报告 `passed_core_checks`。该 profile 尚未装配完整金融合约、城邦经济、军事/外交和完整对局业务，因此 `full_rules_accepted` 固定为 false，也不允许借此 profile 跑超过 150 Tick。`--audit-every-phase` 可开启八阶段状态指纹与逐阶段审计。

开发 API `opening_economy(..., commerce=True)` 在相同经济系统内接收 `Kernel.step({nation: orders})`，已执行 `logistics/market_bid`、`market_ask`、`trade/export_offer`、`buy_from`、`cancel/cancel_by_id` 和 `finance/fx`。命令永久水位、字段校验、货款预锁、同 Hub 原地过户、P5 竞价、退款和 P8 本国成交回执均进入真实状态；默认 `commerce=False` 的自举仍拒绝任何玩家命令。此 API 是开发集成入口，不能绕过正式发布门禁。

工作区新增命令处理器覆盖已实现的施工/科研/能源/治理以及 `finance/central_bank`，统一注册于 [`engine/commands.py`](engine/commands.py)，未注册操作仍明确拒绝。央行政策下周期生效，替换使用 `replaces_id`；实际发行、干预和资本管制费进入现金账，观测提供本国宏观状态。

开发物流已支持本国公路上的 `route_shipment`（`manual` / `P036`）及公路 `build_corridor`（G1/G2），具备真实车队、尾批装载、返程、有限仓容和露天损耗。其余运输模式、跨国 ACL/收费和乘客迁移尚未接通；精确边界见 [实现口径](IMPLEMENTATION_CONTRACT.md)。

地区市场标识使用公开 Hub ID；仅库存品可交易，整机按 1000 mU 量子。买方须实际持有交易币种。`fx.amount_mU` 表示交出币种的含手续费预算，有限池统一分配后返还未用金额；跨币到账在该 P1 批结算时完成，不用另一条尚未结算的换汇请求为当前命令预锁出资。双边买单只引用上一窗口已有的公开报价，多国竞争同一报价按数量比例分配，未成交预算即时返还。同拍卖方撤单使该报价不成交；不会按国家遍历顺序抢先买走。出售和过户不产生末端消费回执。

回放必须使用一个尚不存在的路径：

```bash
python3 -m organizer.rift_era scenario --ticks 100 --replay /tmp/rift-new-run.jsonl
python3 -m organizer.rift_era verify-replay /tmp/rift-new-run.jsonl
python3 -m organizer.rift_era replay /tmp/rift-new-run.jsonl
```

`verify-replay` 验证哈希链、完整性和终止记录；`replay` 重新执行供应链场景，逐 Tick、逐阶段比较状态哈希。规则、源码、运行时不匹配会拒绝重执行。回放以排他创建方式写入，不覆盖已有记录。

## 策略进程开发入口

[`sdk.py`](sdk.py) 是可复制到提交 zip 的纯标准库客户端，提供分页校验、`ready()`、单调命令 ID 和带窗口号的 `orders()`。真实进程调度模块为 [`engine/ipc.py`](engine/ipc.py) 和 [`engine/driver.py`](engine/driver.py)。例如在满足硬隔离与完整输入缓存要求的宿主上：

```bash
python3 -m organizer.rift_era strategy-opening --seed 1000 --ticks 150 \
  --submissions p0.zip p1.zip p2.zip p3.zip p4.zip p5.zip p6.zip p7.zip \
  --cgroup /path/to/delegated-cgroup --replay /tmp/rift-strategy-new.jsonl
python3 -m organizer.rift_era replay-strategy-opening /tmp/rift-strategy-new.jsonl
```

这仍是开发开局装配，报告不含正式分数，也不接受超过 150 Tick。所有席位通过同一沙箱入口运行；不提供普通子进程降级选项。当前宿主尚不满足 cpu/pids 委派和默认 512 MiB 管道容量申请，命令会报基础设施故障。输入容量的推导、计时边界及内存验收要求见 [实现口径](IMPLEMENTATION_CONTRACT.md#策略-ipc-与调度)。已有八席位可信 fixture 的真实管道/回放测试，但它不是目标宿主沙箱验收。

## 历史模块边界（0.4.0 研发检查点归档）

> 下表为 2026-11-05 研发检查点（`0.4.0` 早期状态）留存，其中多数“待接通/待实现”项已在后续合入并关闭全部 10 项发布门禁（见 [`engine/release.py`](engine/release.py) 与 [`COMMAND_SURFACE.md`](COMMAND_SURFACE.md)）。

| 模块 | 已实现的职责 | 当前边界 |
|---|---|---|
| `catalog.py` | 不可变目录、引用/DAG、整机与军品跨表检查、数据指纹 | 科技文本效果仍需要各系统显式实现 |
| `numeric.py`、`determinism.py` | mU/nmU、精确有理数、两种分配舍入、独立哈希随机流 | 超越函数须记录运行时；不能保证跨所有平台逐位相同 |
| `ledger.py` | 原子库存转移、显式源汇、多币现金、历史 RU 成本、两腿 FX、费用残差、只读守恒审计；跨库存/现金 DVP、无副作用预检及过期事务拒绝 | 业务层仍须检查科技、权限、发行上限、抵押和清算优先级；账本只由单线程 Kernel 修改 |
| `materials.py`、`consumption.py` | 原产/消环路径/合成食品属性随实物原子转移；真实输入信用和在制品对账；使用时信用冻结、20 周期实耗和末端回执、永久逐国逐品 SinkDebt | 已接开局生产/军粮/居民、商品交易及运输组件；建设投产限额、赠与援助、跨境服务和完整五支柱指标仍待接通 |
| `clock.py`、`kernel.py` | 三类独立时钟、P1–P8、系统依赖排序、阶段审计和错误隔离 | 完整国家模拟的系统装配尚未完成 |
| `world.py`、`initialization.py` | 候选地图、连通领土、枢纽/路线、27 S1/9 G1 的大国开局模板、人口/军职/现金分账 | 完整流域、岛链和城邦实体经济尚待接通 |
| `economy_state.py`、`economy.py`、`payments.py`、`bootstrap.py` | 八国 150 Tick 真实经济；预算预留、有限 FX、物理资源池、工资/消费/税收、冷启动与民生断供 | 仅固定开局计划；全局长、全命令与全部 P7 宏观耦合尚未完成 |
| `production.py` | 同阶段共享池、仓容二次分配、纳单位预扣信用、整机释放、产出缓冲、技能劳动分配 | 调用方必须先提供真实 P3 电量和当拍劳动上限；不自动套用全部科技/模式 |
| `power.py` | 机组状态、核冷却、黑启动迟滞、储能方向、整数路径配电与费用凭证；孤立电网批量算法已与逐 mU 参考算法随机对照 | 跨枢纽网络仍使用参考调度器，全规模性能未验收 |
| `logistics.py` | 独占运具、真实航程、全程预付油、卸货等待、空车返程、断边回退、缺油搁浅 | 尚需模态/ACL/拥塞准入层、部分装载、途中腐坏和凭证联动 |
| `assets.py` | 剩余成本向量、战损、修复资本化、整机修复量子拒绝 | 修复资源托管与施工指令装配未完成 |
| `market.py` | 集合竞价、同价比例分配、统一有限池、债限和偿付分配 | 商品托管/DVP 已由 spot 装配；完整证券/远期/违约状态机仍待实现 |
| `spot.py`、`commerce_commands.py` | 商品挂单托管、双边出口托管、全额现金预锁、实际 DVP、撤单/到期、有限池换汇命令、只读本国成交视图 | 商品链路与原产明细已接八阶段经济；世界池实物买卖已由 `world_pool.py` 装配（r7，`trade/pool_buy`/`pool_sell`）；完整宏观统计仍待装配 |
| `diplomacy.py`、`combat.py` | 双边关系状态、同时宣战、停战到期、四域火力、同时伤亡、行动损耗、封锁比例 | 占领、合同、城邦效用、军队移动/后勤、附伤、情报和轨道的完整装配未完成 |
| `research.py`、`ai.py` | 项目绑定、跨 Hub 递减、验证、局地算力预算、批次寿命、训练、数据去重、里程碑 | 科研/AI 实际资源支付、设施装机、事故与服务交易未全部接通 |
| `population.py`、`environment.py` | 分技能人口、住房、生育/死亡、治理预警、迁居到达、有限矿、河网守恒、污染 | 民生采购、工资税收、社会保障和全部 C01–C33 耦合尚未装配 |
| `provenance.py`、`scoring.py` | 原产标签、全路径消环、末端回执、行动损耗、五支柱函数、四纪元、精确比值、分数区间 | 实物消费/回执已采集；其余国力指标、正式完整轨迹仍待装配，当前没有正式对局成绩 |
| `protocol.py`、`observation.py` | 有界 JSONL、窗口校验、命令水位/替换、分块快照、观测者别名、冻结轨迹 | 尚需完整命令 Schema、真实传感器与进程 IPC 驱动；轨迹老化待接通 |
| `archive.py`、`evaluation.py`、`replay.py` | 安全解包、批次 manifest、资源准入、思考预算、只增回放 | 完整对手运行器与正式评分装配待实现 |
| `sandbox.py`、`sandbox_entry.py`、`seccomp_filter.py` | bwrap 隔离、真实 cgroup 文件系统校验、1 GiB/1 CPU/5任务配置、执行用户代码前安装 seccomp、64 MiB 工作区、只读运行时 | 当前宿主未委派 cpu/pids；完整组合隔离、OOM 和并发压力验收尚未通过 |

## 扩展约束

1. 业务拒绝使用 `RuleViolation`，在 P1 逐条捕获；异常、守恒失效和宿主故障使用 `InfrastructureFault`，不能计作选手失败。失败的 Kernel 禁止继续运行。
2. 系统必须显式声明所属阶段及依赖。P7 的完整子阶段应按设计第 10 章装配，不能用函数注册顺序暗示依赖。
3. 现金只从真实账户支付，不能以产值充当收入。锁仓、到货、生产和末端使用必须是不同事件；不能直接修改余额绕过账本。
4. 同一共享池先统一收集需求，再分配、提交。实体遍历顺序不得改变结果。输入先预扣，产出进入 `next_available`，P7 后才能供下一 Tick 使用。
5. 库存使用 mU，产线信用/在制品使用 nmU；输入信用已经离开公共库存。`Line.audit()` 验证领料 = 实耗 + 信用；换线报废由调用方记损耗。
6. 真值、合法观测、序列化输出分层。传感器向 `Observer` 提供过滤后的 `Sighting`，不将整个 World 传入选手代码。未知/不可见目标的错误码还需在命令层统一。
7. 评分函数只读。损耗和正项用 `Fraction` 保存；内部比较用精确 `relative_ratio`，展示使用向外舍入的分数区间，不能用展示值排序或判断微小损耗。
8. 新机制同时交付：明确资源来源/去向、所属阶段、可观察字段、反例测试。没有实现的操作不得以成功回执或空函数代替。

## 实现解释与候选分布

**电网的累计路径舍入**：路径输电采用累计接收量反推累计发送量，再扣相邻累计值的差。若每发送 1mU 都独立执行 `ceil(1/0.9)`，100mU 只能送达 50mU，与 90% 线路效率示例冲突。当前实现使 100mU 送达 90mU，且每笔库存与费用守恒。对应测试 `test_grid_receipt_cash_and_energy_balance`。此口径已写入规则 05 章第 4 节。

**地图分布 `rift_spatial_stratified_v1`**：24×20 个战略分区，20 个陆地列、4 个海洋列，按独立哈希流分成 2–4 块大陆；每国 20 个相连初始分区、5 个核心 Hub。其余枢纽、城邦和路线从显式种子流生成。该版本用于验证尺度、编码与初始化接口；没有宣称其地理形态、资源分布或座位公平已达到正式比赛标准。地图版本独立于规则与引擎版本。

**开局资源参数 `rift_opening_endowments_v1`**：初始矿藏储量、局地自然来水、降水到农业产出的映射、默认开局税率单独记录在 [`data/opening_endowments_v1.json`](data/opening_endowments_v1.json)，参与源码/状态指纹。它们是实现候选参数，不冒称设计目录已经冻结的数值；不得根据某次自举的短缺临时增发资源。自然资源使用独立 `nature/deposit` 实物池，通过同一生产预扣信用机制转换为玩家库存；未开采水流显式流出，不能累成免费水库。

**原产分配精度**：来源相同且消环路径相同的实物合并存储，不按生产 Tick 永久新增批次 ID。混合池拆分采用 1 nmU 的比例向下取整与哈希尾差，转出份额从原池同额扣减，保持每个来源守恒；实际燃油消耗不足 nmU 的有理余量保留在实体信用中，不作为比例混合时新生成的小数。这样避免循环转仓让分母指数膨胀。此实现解释随引擎版本冻结，尚不代表全部数值/全规模发布门槛已通过。

沙箱预检使用 `python3 -m organizer.rift_era sandbox-check --cgroup <已委派的cgroup目录>`。启动器不会自动修改宿主的上级控制器，也不会在硬限制缺失时退化为普通子进程。仅有伪造的同名配置文件不能通过预检，目录必须位于真实 cgroup v2 文件系统。post-exec seccomp 已用独立可信测试进程验证 fork/socket/exec 拒绝、线程正常运行；这不替代 cgroup 与命名空间组合验收。

**数值实现**：物资/现金用 Python 任意精度整数，金额成本与评分用 `Fraction`。研究/AI 的幂对数及福祉平方根目前采用 50 位 Decimal；正式规范的冻结 binary64 语义与这些计算的逐阶段舍入还需统一验收，不能宣称已逐位复现正式规则。环境随机噪声使用冻结运行时的 `math`/Box–Muller。

## 验收状态（2026-11-24 核验）

- [`engine/release.py`](engine/release.py) 中的全部 10 项正式发布门禁均已关闭（`PENDING_RELEASE_GATES = ()`），完整 8 席 × 28,800 Tick 对局、正式地图 `world_atlas`、83 条已注册命令、17 项 `round6_scenarios.json` 回归（`tests/test_round6_scenarios.py`）与 3 风格正式评分池（`hegemon` / `arbiter` / `verdant`）均已装配完成并冻结于 [`config/scoring_v2.json`](config/scoring_v2.json)（`2026-11-21-rift-3style-v2`）。
- 历史开发交接与分阶段 TODO 归档于 [HANDOFF.md](HANDOFF.md)、[REMAINING_WORK.md](REMAINING_WORK.md) 与 [FULL_MATCH_TEST_TODO.md](FULL_MATCH_TEST_TODO.md)。
