# 引擎问题与规则校准观察（bots 分支记录，不在本分支改引擎）

本文件由 tier bot 移植工作记录（rift-bots 分支，基于 rift-core 6581aa1）。这里只记录现象、复现方式和影响；
引擎修改留给 rift-core 的引擎子代理/作者裁决。目前**没有阻塞 bot 的引擎 bug**；E10 是长局里最重的规则陷阱。

| # | 类别 | 现象 | 复现 | 对 bot 的影响 |
|---|---|---|---|---|
| E1 | CLI 能力 | `python3 -m organizer.rift_era match --bot` 只接受 `none/simple`，没有外部 bot 入口 | `__main__.py` match 子命令 | 不改 engine：在 `bots/runner.py` 用同一批公开入口（`match_economy`/`economic_development_init`/`finalize_match`，pipe 模式用 `StrategySession`+`MatchDriver`）驱动 |
| E2 | 规则校准 | 开局各国初始库存远大于 6 周期消耗，`consumption20` 窗口从空开始，`eta_sink`≈0：开局全部工业线 VA≤0。无命令基线工业支柱≈−57（总分 ≈20→14），只要把产线按目标库存停掉总分就 +15 左右；SimpleBot 与无命令只差 1e-5 量级也源于此 | `runner --seats none` 与任一 tier 对比；`industry_activity.last_asset_va` | 三档 bot 都以“库存上限停产”为第一杠杆；这让“会不会停产”压过其余策略差异，建议作者确认这是期望的开局（07 §1） |
| E3 | 规则校准 | 开局没有任何药品产线，药品库存约 14 周期耗尽（tick≈140），所有国家人口支柱在 tick 150–250 下跌约 20%；基础制药 A04 是 2 阶（需 I02+C01，S2 厂房还需 L01），以 1 研究点/周期的基础实验室最快也要约 2000 tick | 任意 400 tick 对局的 `population` 支柱 | 超级 bot 把 I02/C01/L01/R02→A04 放在路线最前并建 S2 制药厂；短局（≤1000 tick）校准里看不到这条收益 |
| E4 | 规则/地图 | 城邦陆路走廊对国家默认通行档 0，城邦不能下 `corridor_policy`；只有 `port_lease`（须城邦有海港）给通行权。旧地图最近城邦 city-33 只有陆路，`route_shipment` 返回 `ERR_CORRIDOR_BLOCKED`，城邦现货市场在陆路上无法实物交割 | n0 `route_shipment n0-h0→city-33-h0`（path `road:n0-h0:n0-h3`,`road:city-33-h0:n0-h3`, carrier manual） | 国际支柱（出口）对三档都暂时为 0；超级 bot 未实现出口 |
| E5 | 地图 | `--legacy-map` 下本国矿脉全部在首都枢纽，其他 19 个受控分区没有 `resource_sites`，控制支柱 σ_r 只能靠“有人有电的产线槽位”拿到，需要迁人+住房 | `state.resource_sites` 按 hub 统计 | 超级 bot 只做首都 K_infra（住宅/仓容/新设施），未做跨区迁人 |
| E6 | 电力 | P070 最低出力 40%，开局 5 座燃煤电站在停产后的负荷下全部压在最低出力，多余燃料白烧（VA 与污染双损） | 停产后观察 `lines[].plant.generation_mU` | 超级 bot 按实测出力滞回 `plant_toggle`；需要作者确认 40% 最低出力 + 无自动停机是期望行为 |
| E7 | 观测 | 新建设施的空产线在 `obs.lines` 里 `recipe` 为 `None`（不是 `__idle__`），需要用 `facilities[].line_ids` 认领 | 建 S1 实验室后看观测 | 只是文档缺口，bot 已按 `None` 处理 |
| E8 | 规则校准 | 控制支柱 `0.01 × connected_book` 没有上限，`warehouse_expand` 可无限重复（每模块 steel 4 + cement 6 → 账面约 46.7 → 控制 +0.47、总分 +0.093/周期，永久）。矿石/煤/石料开局储量巨大，长局里“冶钢+水泥→仓容”可让控制支柱线性增长、压过其他支柱 | 任一 bot 反复 `warehouse_expand`，看 `_kinfra` | rift-super-3 起三档都不再把钢/水泥余量转成仓容账面（rift-super-2/rift-advanced-1 曾这样做）；仓容只按占用扩建（见 E10），账面随真实需要增长；建议作者给 K_infra 设饱和或按人口/槽位封顶 |
| E9 | 规则校准 | 污染 = 排放/2% 衰减的累积，燃煤 P070 每 10 电排 1，开局负荷下首都均衡污染约 10 万 mU（份额 0.5），人口支柱安全项 ×0.75、支持度每周期 −5。无命令国家 400 tick 时首都污染 16 万 mU | `obs.hubs[].pollution_mU` | 停产/关电站是减污的主要手段；V02 修复剂要到长局中段 |
| E10 | 规则校准 | 矿井不受任何命令节流（`set_line` 只认设施产线，`target_stock_mU` 不作用于矿井，没有暂停矿井的命令，只能拆除），9 座矿井每 tick 约 4.4k mU 进首都公共仓库；旧地图开局仓库占用已约 1.8M/3.3M，**约 tick 350 仓满**，之后“一个共享仓库配额同时覆盖矿井和产线”（economy.py P6），食品厂/轻工厂产出放不进仓库，食品与民用品库存归零 → 饥荒、支持度崩、φ 掉到 0.45 | `bots/runner --legacy-map --seed 2000 --ticks 1000`，rift-medium-1 席位约 tick 450–700 从 ~28 掉到 ~10；trace 里首都 `sum(inventory)` 贴住 `warehouse_mU` 后 food 线 `output_accum` 不再增长 | rift-medium-1/rift-advanced-1/rift-super-2 都会中招（Super-2 只在污染低时扩仓）。三档现在都按**仓库占用**扩建仓容（占用 > 70–75%），这是生存需要，不是刷 E8；建议作者给矿井加目标库存/暂停，或让仓满时矿井先停、民生产线优先入库 |
| E11 | 规则校准 | 开局民生产能贴着开局户需：4 条 P031 满负荷 12000 mU 食品/周期 ≈ 户需（人口 194k × 6%），2 条 P032 6000 ≈ 民用品户需，3 座农场 13200 粮/周期刚够 4 条食品线；人口只要涨 3% 就开始吃库存 | `obs.lines` 计数 × 配方产出 vs `hub.population_mU × BASKET` | “管得好 → 人口涨 → 断粮”的反向激励；三档都加了“户需超过产能 92–98% 就补厂（先农场后食品厂）” |
| E12 | 规则校准（r8，rift-bots-r8 记录） | 世界池买卖同价（10×w_cat）且 RU 储备计入工业支柱 `nfa_ru/1000`：把任何超过约 6 周期用量的库存（含开局禀赋、矿井产出）卖给世界池，既抬高本国同品 eta（产线 VA 转正）又永久增加 NFA；需要时再按原价买回。rift-super-4 只靠这一点在 400 tick 就领先约 +5 | `--seats super,...` 看 `nfa_ru` 与 `industry` | 这是 Super 的主要杠杆；8 席 Super 的影子局里世界池 RU（1000 万）会被逐步卖空，之后卖单按比例缩量，Super 自动退回自产（见 bot `poolable`）。建议作者确认 NFA 权重与“卖池抬 eta”是否为期望 |
| E13 | 规则校准（r8） | 开局 P033 只 0.2 批、药品无其他产线（A04 很晚），药品只能消耗库存或从世界池买；每周期户需约 2 单位≈314 RU，是长局 RU 的主要流出 | 1200 tick trace：RU 每周期约 −310 | Super 在 RU<1.5 万时按户需一半配给药品；长局（>2000 tick）仍可能断药，取决于 A04 进度 |
| E14 | 观测/规则 | seed 3000 legacy 图上 Super 与 Medium 都有 `build_facility` 返回 `ERR_SITE_INCOMPATIBLE`（各 3 次后放弃该项，不影响运行）；Advanced 有 `ERR_CAPACITY_EXCEEDED`（槽位满仍下建厂） | `diag` 包装统计回执 | 未修（Medium/Advanced 只做最小适配） |
