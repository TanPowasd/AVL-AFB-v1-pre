# 裂陆纪元命令表（选手版）

由 `pipeline/command_surface.py` 从引擎实时注册表生成（2026-11-20T18:21:13Z）。引擎 `rift_era_engine_0.4.0`，规则 `rift_era_rules_r9`；共 **83** 条 `kind/op`。

字段以实时引擎校验与 `rules/06_agent_interface.md` 为准；本表已覆盖全部实时注册命令的准确参数名。

每条命令都是 JSON 对象：`{"id": "唯一递增字符串", "kind": ..., "op": ..., 其余字段}`；每窗口最多 256 条，单行 ≤ 256 KiB。被拒绝的命令只影响它自己（返回 ERR_* 码），不扣分也不影响其他命令。

| kind | 数量 |
|---|---|
| `ai` | 8 |
| `cancel` | 1 |
| `diplomacy` | 10 |
| `finance` | 11 |
| `governance` | 10 |
| `industry` | 13 |
| `intelligence` | 5 |
| `logistics` | 8 |
| `military` | 9 |
| `research` | 3 |
| `trade` | 5 |

| # | kind/op | 字段（06） | 语义（06） |
|---|---|---|---|
| 1 | `ai/deploy` | `hub_id: str`, `lines: list[str]`（必填，可为 `[]`，非空时须全在 `hub_id` 且可自动化）, `research: int`, `production: int`, `finance: int`, `cyber: int`, `market: int`, `align: int`, `grid: int`（各为非负算力 `mU`）, `align_engineers_mU?: int (>=0)`, `replaces_id?: str` | 与本 Hub 的 `train.alloc_compute_mU` 合计 ≤ Cbar[hub_id]（超上限返回 `ERR_CAPACITY_EXCEEDED`）；无对应里程碑的项须为 `0`（`align_engineers_mU > 0` 需 `AI03`），否则返回 `ERR_TECH_LOCKED`；持续计划，可用 `replaces_id` 原位替换 |
| 2 | `ai/license_accept` | `seller: str`, `license_kind: "weights"\|"data"`, `max_price_ru_mU: int (>0)` | 需 `AI01`；接受卖方挂牌的权重或人工精标数据授权（数据授权同来源冷却 `100` 周期，按来源国去重高水位计入） |
| 3 | `ai/license_offer` | `license_kind: "weights"\|"data"`, `price_ru_mU: int (>=0)`, `target: str\|null`, `replaces_id?: str` | 需 `AI01`；持续挂牌（可用 `replaces_id` 替换）；数据授权仅限本国人工精标数据，权重授权须已有激活模型 |
| 4 | `ai/model` | `active_model: str`（`model_id`） | 指定当前用于推理/运营部署的模型版本 |
| 5 | `ai/prune_model` | `model_id: str` | 删除指定非激活历史模型版本以释放 `16` 个模型槽位上限 |
| 6 | `ai/service_bid` | `qty_mU: int (>0)`、`max_price_ru_mU: int (>0)`、`use: "research"\|"production"\|"finance"\|"cyber"` | 买入他国推理服务（需买方自身已解锁目标 `use` 对应里程碑） |
| 7 | `ai/service_offer` | `qty_mU: int (>0)`、`price_ru_mU: int (>0)` | 需 `AI04`；每单位占用 `1000 mU` `market` 部署算力 |
| 8 | `ai/train` | `hub_id: str`, `alloc_compute_mU: int (>0)`、`cycles: int`（$\ge 5$）、`base_model: str\|null`、`max_price_mU: int (>0)` | 需 `AI02`；纳入算力总预算，中途 `cancel` 将 `50%` $C_{\text{run}}$ 存为检查点模型 |
| 9 | `cancel/cancel_by_id` | `target_id: str` | 按 `target_id` 终止本国有权取消的未成交挂单、在途训练、施工或持续计划（不存在或已完结返 `ERR_UNKNOWN_TARGET_ID`） |
| 10 | `diplomacy/accept_contract` | `proposal_id: str` | 接受对方发出的未过期合同提案，在 `P1` 锁定首期交割义务后正式生效 |
| 11 | `diplomacy/city_state_aid` | `city_state_id: str`, `good: "food"\|"water"\|"medicine"\|"consumer_goods"`, `qty_mU: int (>0)` | 将已运抵该城邦枢纽的本国国有可用民生库存无偿援助给中立城邦，居民消耗后计入亲和度与援助指标 |
| 12 | `diplomacy/city_state_treaty` | `city_state_id: str`, `treaty_kind: "resource_jv"\|"port_lease"\|"protectorate"`, `aid_ru_mU: int (>=0)` | 与中立城邦签署资源合资、港务租借或保护国协议（须满足对应亲和度阈值 `0.35 / 0.50 / 0.65`） |
| 13 | `diplomacy/declare_war` | `target_nation: str` | 按03§0统一P1关系状态机向国家或独立城邦宣战；同拍双方声明均为aggressor，盟友不自动参战；违约与C26/C27同该节 |
| 14 | `diplomacy/grant_climate_consent` | target_nation:str; region_ids:list[str]; duration_cycles:int(1..720) | 仅授权本国控制区域，期限内撤销从下一周期起生效；V08区域/下风向均需覆盖才算合资格非敌对干预，不许替第三国签字 |
| 15 | `diplomacy/leave_joint_venture` | `venture_id: str` | 主动退出本国参与的既有合资企业（移除自身活跃成员资格，不再承担后续投入与分红） |
| 16 | `diplomacy/military_aid` | `recipient: str`, `hub_id: str`, `good: str`（军品或 `food`/`water`/`fuel`/`battery`/`medical_supplies`）, `qty_mU: int (>0)` | 向存在待履行共同防御义务（`defense_obligations` 状态 `PENDING`）的受援国在其受控枢纽移交国有可用军需物资，受援方实际消耗后折算履约额度 |
| 17 | `diplomacy/propose_contract` | `counterparty: str`, `clause_type: 1..9`, `params: object（仅03固定合同schema，不接受任意字段）`, `duration_cycles: int (10..720)` | 提交九类有限谓词结构化合同提案（含商品互换、通行权、主权贷款、研发合资、停火、防务等） |
| 18 | `diplomacy/reject_contract` | `proposal_id: str` | 拒绝未生效提案（无信用惩罚） |
| 19 | `diplomacy/terminate_contract` | `contract_id: str` | 终止既有合同；若未满足合同约定的免责终止谓词，按单方撕约扣减 `Credit_p` |
| 20 | `finance/bond_issue` | `term: 20\|50\|100`、`face_mU: int (>0)`、`min_price_mU: int (>0)` | 需 `F02`；受 `40 × max(mean_tax_20, 25)` 主权债上限约束，超限返回 `ERR_NO_PERMISSION` |
| 21 | `finance/bond_order` | `bond_id: str`、`side: "buy"\|"sell"`、`qty_mU: int (>0)`、`limit_price_mU: int (>0)` | 二级市场交易，预锁 100% 资金或债券面值份额 |
| 22 | `finance/central_bank` | `rate_bp: int`（`0..2000`，储备国下限 `-100`）、`issue_mU: int`（0≤数值≤2%全球本币存量M_p）、`intervene_ru_mU: int`、`peg: int (>0, RU每本币的mU)\|null`、`capital_control_bp: int`（`0` 或 `100..500`） | 需 `F02`；持续计划，直至被 `replaces_id` 更新覆盖 |
| 23 | `finance/dividend` | `company: str`、`payout_mU: int`（`0..1000`，代表 `0..100%`） | 仅控股国（国有股 $\ge 51\%$）；仅对当期实现现金流正利润 $\Pi_{k,\text{cash}} > 0$ 分红 |
| 24 | `finance/export_control` | `goods: list[str]`（加速器、高端芯片 ID 列表）、`targets: list[str]` | 即时生效；若导致对守约方的既有合同断供，按主观违约扣减信用 |
| 25 | `finance/fx` | `side: "buy_ru"\|"sell_ru"`、`amount_mU: int (>0)`、`currency: str` | 即时按当周期汇率与 `WorldClearPool` 结算（扣除手续费与资本管制费，受池余额上限约束） |
| 26 | `finance/fx_cover` | `currency: str`, `amount_mU: int (>0)` | 需 `F03`；买入并归还指定外币空头负债，按归还比例释放保证金（同窗口累计申请不得超过剩余未平空头余额） |
| 27 | `finance/fx_short` | `currency: str`, `amount_mU: int (>0)` | 需 `F03`；从世界池借入他国货币并当期卖出换取 RU（不得做空本币，预锁 `50%` RU 保证金，总空头不超过世界池期初余额 `25%`） |
| 28 | `finance/ipo` | `company: str`、`qty: int`（累计 $\le 490,000$ 股）、`min_price_mU: int (>0)` | 需 `F01`；仅限本国公司，从国库所持 `1,000,000` 原始股中向市场公开发售，募资入国库 |
| 29 | `finance/secondary_offering` | `company: str`、`qty: int`（每 100 周期 $\le 100,000$ 股，外部总持股累计 $\le 490,000$ 股）、`min_price_mU: int (>0)`、`target_facilities: list[str]` | `C30`；需 `F01` 且该公司已 IPO；总股本保持 `1,000,000` 股不变，从国有股释出，募资锁入 `CapexEscrow[p, k]` 专户，仅可用于所列本行业设施建设 |
| 30 | `finance/stock_order` | `company: str`（`<国>-<行业>`）、`side: "buy"\|"sell"\|"short"\|"cover"`、`qty: int (>0)`（股）、`limit_price_mU: int (>0)`、`margin: bool`、`expire_cycles: int (1..20)` | 进入下一次证券集合竞价：`margin=false` 的现货买单（`buy`）按 **`100%` 挂单本金（`qty × limit_price_mU` + 预估手续费）** 全额预锁买方现金（现货卖单 `sell` 100% 预锁自由持仓股份）；仅当 `margin=true`（融资买入）或 `side="short"`（融券卖空，均需本国已解锁 `F03`）时，按 **`50%` 初始保证金**预锁现金，资金或股份不足返回 `ERR_INSUFFICIENT_CASH` 或 `ERR_INSUFFICIENT_STOCK` |
| 31 | `governance/academy_train` | `hub_id: str`, `track: "general_to_skilled"\|"skilled_to_engineer"`, `trainees_mU: int (0..5000)` | 需 `R02` 学院设施；培训人员实际退出岗位并锁入training人口池，不能同时生产/迁移/征兵；单座学院并发培训上限 `5.0` 人口单位（`5000 mU`），耗时 `6` 或 `12` 周期 |
| 32 | `governance/autonomy` | `region_id: str`, `autonomy_mU: int (0..500)` | 设定分区地方财政留成比例 `Autonomy_r ∈ [0, 50%]`（提升地方支持度恢复，同比例减少上缴国库税基） |
| 33 | `governance/climate_intervention` | `target_region: str`, `mode: "enhance"\|"suppress"` | 需 `V08`；持续 `60` 周期（冷却 `120` 周期），每周期扣 `remediation_agent×2 + 10.0 B3电` |
| 34 | `governance/conditional_plan` | `hub_id: str`, `metric: "market_price"\|"grid_price"\|"hub_stock"`, `ref_id: str`, `comparator: "<="\|">="`, `threshold_mU: int`, `action: dict`, `cooldown_ticks: int (>=5)` | 需 `C10` 且枢纽装有 `ai_core×1`；单窗口最多触发 `1` 次，冷却至少 `5 tick`（`1` 窗口） |
| 35 | `governance/fiscal_policy` | `tax_rate_bp: int (500..5000)`, `welfare_budget_mU: int (>=0)`, `maint_budget_mU: int (>=0)` | 设定名义税率tau_tax（5%..50%）；F01与封顶后的有效税率按02计算、`F05` 社会保障实付预算与公共设施维护预算 |
| 36 | `governance/mobilize` | `enabled: bool` | 全国紧急劳动动员，2周期爬坡、至少4周期；军职征入由deploy_formation固定crew处理，不能重复征召 |
| 37 | `governance/power_price_cap` | `cap_price_mU: int (5000..50000)` | 设定本国用户侧电价上限 $\bar{\lambda}_{\text{cap},p} \in [5.0, 50.0]$（本币/电） |
| 38 | `governance/power_priority` | `priority_order: list["B1"\|"B2"\|"B3"\|"B4"]` | 必须为 `["B1","B2","B3","B4"]` 的 4 元全排列置换（否则返 `ERR_SCHEMA`）；设定本国第 `1..4` 电力调度秩次 |
| 39 | `governance/relocate_population` | origin_hub,dest_hub:str; skills_mU:object{general_worker,skilled_worker,engineer:nonnegative_int}; path:list[str]; allow_unhoused?:bool=false | 复用Passenger Shipment、各技能不超过可用人口；10本币/人服务费、5周期口粮预装、住房或明确无房选择，见11 |
| 40 | `governance/set_regime` | `group: "science_doctrine"\|"economic_doctrine"`, `choice_tech_id: str` | 切换互斥制度或路线组（需已解锁目标科技；`science_doctrine` 过渡 `40` 周期，`economic_doctrine` 过渡 `60` 周期） |
| 41 | `industry/build_facility` | `hub_id: str`, `facility_type: str`（industry.json厂类或public）, `profile_id: "S1".."S5"\|"G1"\|"G2"\|"P110"\|"L06_canal"\|"L06_tunnel"\|"L06_dam"\|"L12_bridge"\|"L12_island"`, `good?: str`, `use_modular_mU?: int (0..500)` | 校验科技前置、分区地形/槽位上限与 `512` 座设施硬上限；在 `P1` 预锁首期施工建材入 `locked_build` |
| 42 | `industry/build_mine` | hub_id,deposit_id,good: str; level:G1/G2 | 必须本地真实矿脉；runtime_rules定额及02建设维护 |
| 43 | `industry/demolish_facility` | `facility_id: str` | 校验所有权且本地无本tick战斗；拆解耗时 `2` 周期，返还直接消耗钢/铜的 `50%` 并记入 `Loss_demolish` |
| 44 | `industry/install_storage` | `facility_id: str`, `storage_kind: "battery"\|"pumped"`, `expected_version?: int` | 在已投产 `S2..S5` 设施空闲槽位安装储能（`battery` 需 `E04`、耗 `battery×8`、2 周期；`pumped` 需 `E02+L01` 与抽水地形、耗 `steel×30+cement×60+basic_parts×6`、30 周期） |
| 45 | `industry/install_system` | hub_id:str; system_id:str（见下表） | 本地公共槽装机，按固定成本扣库存，2周期完工，未满足前置拒绝 |
| 46 | `industry/plant_toggle` | facility_id:str; online:bool | 变更本国电站设施（05状态机处理）或采掘场（`mine_id`，r9支持暂停/恢复采掘与取水）运行开关 |
| 47 | `industry/refill_accelerators` | facility_id:str; qty:int(1..50) | 同代际空位装整台，保留各批日期，不重置原芯片寿命 |
| 48 | `industry/refill_storage` | `facility_id: str`, `expected_version?: int` | 对健康度 `< 800 mU` 的电化学储能（`battery`）更换电芯，消耗本地 `battery×2` 并将电池健康度恢复至 `1000 mU` |
| 49 | `industry/remediate_pollution` | `region_id: str`, `amount_mU: int (>=0)` | 需 `V02`；从所属枢纽 `available` 预锁并投放 `remediation_agent`（足额 `1000 mU` 触发额外 `+2.0%/周期` 衰减与污染惩罚减半） |
| 50 | `industry/repair_asset` | asset_id:str; target_health_mU:int(1..1000); work_hub_id:str | 自有/获授权本地设施、线、边或仓；目标>现状，按05预锁材料/工/电及工期；不得超book或绕过整机精度 |
| 51 | `industry/set_line` | `line_id: str`, `recipe: str\|null`, `mode: "general"\|"specialized"\|"automated"\|"flexible"\|"additive"`, `high_temp?: bool`, `gene_edited?: bool`, `target_stock_mU?: int (>=0)`, `addons?: list["sensor"\|"carbon_capture_unit"\|"ai_core"\|"quantum_module"]` | 校验配方/模式科技前置与设施等级上限；首次装机从 `available` 一次性扣除 `installed` 及模式/附属模块装机件 |
| 52 | `industry/upgrade_facility` | `facility_id: str`, `target_profile: "S2".."S5"\|"G2"` | 校验所有权、科技前置与升级建材库存，预锁建材后转入改造工期 |
| 53 | `industry/warehouse_expand` | `hub_id: str`, `modules: int (1..10)` | 每模块消耗 `steel×4 + cement×6`，完工后该枢纽公共仓容 `+500,000 mU`（`+500` 单位） |
| 54 | `intelligence/cyber_op` | `target_nation: str`, `target_hub: str`, `op_kind: "S03_infiltrate"\|"S03_steal_tech"\|"S08_paralyze"` | 按03执行S03渗透/窃密或S08瘫痪；S07只有被动防御，不提供攻击命令。其他op_kind返回ERR_SCHEMA。 |
| 55 | `intelligence/intel_op` | `target_region: str`, `op_kind: "S06_decoy"\|"C12_cryptanalysis"`, `reported_strength_mU?: int (0..10000000)`, `reported_type?: str`（military.json unit_id） | 仅S06假目标（cyber_suite .1）或C12密码分析，条件和成本见03/11；S02是被动监听。其他op_kind返回ERR_SCHEMA。 |
| 56 | `intelligence/orbital_launch` | `hub_id: str`, `payload: "satellite"\|"orbital_platform"`, `orbit_plane: 0..2`, `slot: 0..7`, `berth: 0..1`, `use_reusable: bool` | 从 `S3/S5` 发射场扣除载荷整机与燃料（或 `P096 reusable_launcher`）发射入轨；基础仅berth=0，berth=1须全球已扩容且本国N12，同位争抢按03哈希，未获位不扣载荷 |
| 57 | `intelligence/radar_toggle` | `entity_id: str`, `active: bool`, `ew_mode?: "passive"\|"jamming"` | 切换雷达或 `G09` 电子战模块主动开机/静默状态（主动开机提升侦测但暴露自身辐射源） |
| 58 | `intelligence/steal_model` | `target_nation: str`, `target_hub: str` | 需 `S03`；对敌方数据中心发起模型权重窃取，成功获最高模型 $\gamma = 0.70$ 能力副本 |
| 59 | `logistics/build_corridor` | origin_hub,dest_hub: str; mode: road/rail/river/sea/air/grid/water; level:1或2; path_cells: list[int] | 两端权限、02科技/材料/工期校验，按路径格长扣料；不是仅设置既有边通行权 |
| 60 | `logistics/corridor_policy` | `corridor_id: str`, `target_nation: str`, `access_tier: 0..3`, `toll_rate_bp: int (0..2000)` | 设定本国控制走廊对他国的通行权档位（`0` 禁行、`1` 商贸、`2` 军需、`3` 部队过境）与过境费率 |
| 61 | `logistics/forward_contract` | `counterparty: str`, `good: str`, `qty_mU: int (>0)`, `strike_price_ru_mU: int (>0)`, `delivery_cycle: int (c+10\|c+20\|c+50)`, `delivery_hub: str` | 需双方解锁 `F03`；签署时双边各预锁合同名义价值 `20%` 的初始保证金入 `ForwardMargin[p]` |
| 62 | `logistics/market_ask` | `hub_id: str`, `good: str`, `qty_mU: int (>0)`, `limit_price_mU: int (>0)`, `expire_cycles: int (1..20)` | 区域商品集合竞价卖单；在 `P1` 从 `available` 全额预锁商品入 `locked_order` |
| 63 | `logistics/market_bid` | `region_market: str`, `good: str`, `qty_mU: int (>0)`, `limit_price_mU: int (>0)`, `expire_cycles: int (1..20)` | 区域商品集合竞价买单；在 `P1` 按 `qty_mU * limit_price_mU / 1000` 全额预锁买方现金入 `locked_order` |
| 64 | `logistics/plant_maintenance` | `facility_id: str`, `shift_cycles: int (-20..20)` | 调整电站计划检修窗口偏移量（推迟超 `20` 周期后强迫停运率 `×3`） |
| 65 | `logistics/route_shipment` | `origin_hub: str`, `dest_hub: str`, `corridor_path: list[str]`, `good: str`, `qty_mU: int (>0)`, `carrier_recipe: "manual"\|"P036"\|"P037"\|"P052"\|"P053"\|"P059"\|"P060"`, `repeat_cycles?: int (0..100)` | 校验路径连通性、走廊通行权、整数台运载工具及全程燃料/电力，在 `P1` 预锁货物与全程燃料后发车 |
| 66 | `logistics/storage_mode` | `facility_id: str`, `mode: "auto"\|"charge"\|"discharge"\|"idle"`, `power_mU?: int (>=0)` | 配置 `E04` 电化学储能或抽水蓄能运行模式（`auto` 引用截至上一周期末近 10 周期中位电价） |
| 67 | `military/asat_strike` | `origin_hub: str`, `target_sat_id: str` | 需 `N12`；消耗 `hypersonic_missile×1 + guidance_module×1` 拦截敌方在轨节点（产生轨道碎片惩罚） |
| 68 | `military/blockade` | formation_id,edge_id:str; target_nations:list[str]（1..8） | 03§7合法位置/域/补给、必须WAR目标，每编队一边；生成持续计划，实际每tick按07付损耗 |
| 69 | `military/deploy_formation` | `hub_id: str`, `domain: "Land"\|"Air"\|"Sea-Surface"\|"Sea-Sub"`, `units: dict[str,int]`, `crew_mU?: int (>0)` | domain枚举只取military.json的domain_channel键；unit_id只取units键，整数台且全属指定域。crew可选断言值须等于单元定额和；混域拒绝ERR_MIXED_DOMAIN_FORMATION，未知域/单元拒绝ERR_SCHEMA；实际扣整机和兵员。 |
| 70 | `military/disband` | formation_id:str | 仅友好Hub，幸存crew归原技能池、完好装备保留HP回库，不能刷新健康 |
| 71 | `military/formation_task` | `formation_id: str`, `task: "move"\|"garrison"\|"attack"\|"escort"\|"patrol"`, `target_region: str`, `posture: "preserve"\|"assault"\|"bombard"`, `supply_hub: str`, `retreat_loss_mU: int (100..900)` | 下达编队机动/作战指令、交战姿态、补给源枢纽与自动撤退战损阈值 |
| 72 | `military/hypersonic_strike` | `origin_hub: str`, `target_id: str`, `missile_qty: int (1..20)` | 需 `N10`；从发射枢纽扣除 `hypersonic_missile × missile_qty`（`P098`），`1 tick` 跨区打击高价值目标 |
| 73 | `military/interdict` | `formation_id: str`, `contact_id\|shipment_track_id: str` | 只接受本国当前可见（`q >= 0.2`，见观测 `strikes.shipment_contacts`）且在编队有效射程跳数内的在途接触别名，运输者及货主均须 `WAR`；共享射击预算，不打中立第三方 |
| 74 | `military/refit_formation` | `formation_id: str`, `slot_a: str\|null`, `slot_b: str\|null` | 编队须驻停友方枢纽；从枢纽库存扣除改装模块并停机改装 `2` 周期 |
| 75 | `military/reinforce` | `formation_id: str`, `unit_index: int`, `crew_mU: int (>0)`, `expected_version?: int` | 为驻停本国受控枢纽编队的指定单元征召补充兵员；人员先转入训练池，训毕且编队仍在同枢纽时入列补编 |
| 76 | `research/set_backup_hub` | `primary_hub: str`, `backup_hub: str` | 需 `R08` 且备份枢纽装有 `computing_equipment×1 + communication_equipment×1` |
| 77 | `research/set_focus` | `line_id: str`, `tech_id: str\|null`, `sec_mode: "open"\|"standard"\|"classified"` | 绑定实验室产线在研项目（`null` 为待机）；校验 `requires_all/any`（未满足返 `ERR_TECH_LOCKED`） |
| 78 | `research/validate_project` | `line_id: str`, `tech_id: str` | 启动已完成理论阶段的科技工程验证；`6` 阶须在装有 `R09` 的 `S5` 枢纽进行（否则返 `ERR_SITE_INCOMPATIBLE`） |
| 79 | `trade/buy_from` | `offer_id: str`, `seller: str`、`good: str`、`qty_mU: int (>0)`、`max_price_ru_mU: int (>0)` | 接受双边卖单，按RU付款并在卖单Hub原地过户，买方须另安排Shipment |
| 80 | `trade/city_reexport` | `city_state_id: str`, `good: str`, `qty_mU: int (>0)`, `price_ru_mU: int`（须等于 `10 * w_cat * 1000`） | 通过中立城邦转口挂单采购受控货物，报价须严格等于城邦固定公开挂牌价（`10 * w_cat * 1000` mU RU） |
| 81 | `trade/export_offer` | `hub_id: str`, `good: str`、`qty_mU: int (>=0)`、`price_ru_mU: int (>0)` | 国家间双边卖单；立即从 `available` 锁入 `locked_escrow` |
| 82 | `trade/pool_buy` | `hub_id: str`（本国主权Hub）、`good: str`（库存货）、`qty_mU: int (>0，整件货为1000的倍数)` | 从世界池买（r9 单位买价 `ceil(11.5·w_cat·1000)` mU RU），P1锁本国RU `reserves`，同tick P5按P5开始时的世界库存出清，未成交退款；每国每货每周期买入≤40单位（超出返回 `ERR_CAPACITY_EXCEEDED`）；货到该Hub `next_available`，原产标签保留且不得含对本国禁运的原产批次 |
| 83 | `trade/pool_sell` | `hub_id: str`（本国主权Hub）、`good: str`、`qty_mU: int (>0)` | 卖给世界池（r9 单位卖价 `floor(8.5·w_cat·1000)` mU RU），P1锁该Hub真实可用库存，P5按世界池RU余额与全部国家全部货物同批同比缩量成交，RU入本国 `reserves`（公司产权批次入对应公司账户）；每国每货每周期卖出≤40单位 |
