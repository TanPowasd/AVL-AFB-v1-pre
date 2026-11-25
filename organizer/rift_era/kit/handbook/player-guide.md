# 裂陆纪元（rift_era）选手手册

本手册面向参赛开发者与编程 AI，集中说明通信协议、观测结构、命令写法、策略开发路径与本地验证流程。

## 1. 提交结构与入口契约

提交物是一个 `.zip` 压缩包，根目录必须包含 `main.py`。典型目录结构：

```text
my_bot/
  main.py     # 入口：初始化 Client -> 接收 init -> ready() -> 决策循环
  bot.py      # 你的核心策略类 Bot(init).decide(obs)
  sdk.py      # 从选手包根目录原样复制的标准库客户端
```

评测机在隔离环境运行 `python3 main.py`，通过 `stdin`/`stdout` 逐行收发 JSON（`rift_jsonl_v1`）。`sdk.py` 已经封装好大帧分页重组、窗口号绑定与递增命令编号：

```python
from sdk import Client
from bot import Bot

client = Client()
init = client.receive()   # 含 owner, map, catalog, initial_observation, clock, time_budget
bot = Bot(init)
client.ready()            # 必须在 15 秒内回复
while True:
    msg = client.receive()
    if msg.get("type") == "end":
        break
    try:
        orders = bot.decide(msg)
    except Exception:
        orders = []       # 切勿让异常导致进程退出
    client.orders(msg["window"], orders[:256])
```

## 2. 开局帧（`init`）与每窗口观测（`obs`）

- **`init` 帧关键字段**：
  - `owner`：本国 ID（`"n0"` .. `"n7"`）。
  - `map`：公开 512×512 地理编码（`width`, `height`, `map_version`, `region_palette`, `region_rle_rows`, `terrain_by_region`, `elevation_by_region`, `fertility_by_region`, `wind_by_region`, `cloud_by_region`, `adjacency`, `hubs`, `routes`, `river_downstream`, `coastal_hubs`）。注意开局不含隐藏矿藏与随机种子。
  - `catalog`：完整的只读规则目录（`technology`, `industry`, `coupling`, `scoring`, `runtime_rules`, `military`），包含全部科技前置、工业配方、产线定额、影子权重 `w_cat` 与军品参数。
  - `initial_observation`：第 0 拍的初始观测帧。
  - `clock` / `time_budget`：总 tick、窗口间隔（5）、周期长度（10）与思考计时预算。
- **`obs` 帧关键字段**（每 5 tick 下发一次，仅含本国可见信息与传感器过滤情报）：
  - `window`, `tick`, `cycle`：当前窗口、仿真步与周期编号。
  - `inventory`：本国库存列表，行格式 `[hub_id, bucket, good, qty_mU]`（`bucket` 如 `available`、`locked_subsistence`、`locked_order` 等；`1 单位 = 1000 mU`）；另附 `inventory_economic_titles_nmU` 与 `food_synthetic_shares_mU`。
  - `cash_accounts` / `treasury_mU`：`cash_accounts` 为本国各账户列表 `[account_name, currency, balance_mU]`（如 `["treasury", "n0", ...]`、`["household:n0-h0", "n0", ...]`、`["reserves", "RU", ...]` 等）；`treasury_mU` 为国库本币余额快捷值。
  - `hubs` / `population_mU`：`hubs` 为各可见枢纽对象列表（含 `id`, `region_id`, `population_mU`, `skills_mU` (`general_worker`/`skilled_worker`/`engineer`), `training_population_mU`, `housing_mU`, `warehouse_mU`, `support_mU`, `warning`, `red_streak`, `pollution_mU`, `satisfaction_mU`, `grid_price_ru_mU`, `household_cash_mU`, `public_cash_mU`）；`population_mU` 为全国平民人口总计。
  - `facilities` / `lines` / `mines` / `storage` / `systems` / `work`：设施、产线（电站同样在 `lines` 列表中，带 `plant` 子对象，无独立 `plants` 键）、采掘点、储能、公共系统与在建工程队列。
  - `research`：`known`（已解锁科技）、`projects`（理论与验证进度）、`focus`（各实验室绑定目标）、`security`（保密档位）与 `validation`（进行中的验证任务）。
  - `markets` / `market_fills` / `world_pool` / `export_controls` / `fx_fills`：现货报价与成交记录、世界池挂牌与限额、出口管制及汇兑成交。
  - `macroeconomy` / `governance` / `utilities` / `power_policy` / `sovereign_finance` / `corporate_finance` / `bond_market` / `stock_market` / `forward_finance` / `external_payables` / `insolvency`：宏观 CPI/汇率/利率/通胀、税率与预算、电力调度政策与主权/公司/债股/远期金融账本。
  - `diplomacy` / `contracts` / `city_states` / `formations` / `military_contacts` / `military_control` / `strikes` / `orbital` / `orbital_contacts` / `shared_intelligence`：外交关系（`pairs`, `war_spread_bp`）、合同、中立城邦、本国编队、迷雾内可见敌情（含在途运输接触 `strikes.shipment_contacts`）、轨道与共享情报。
  - `command_receipts`：上一窗口命令的执行回执列表（每项含 `id`, `status` (`"ACCEPTED"`/`"REJECTED"`), `code` (`null` 或 `ERR_*` 错误码)）。

详细字段定义与迷雾边界请以 `rules/06_agent_interface.md` 为准。

## 3. 命令格式与高频指令

每条命令均是含唯一递增字符串 `id`、`kind`、`op` 及操作参数的字典，每窗口最多 **256 条**：

```json
{"id": "1", "kind": "research", "op": "set_focus", "line_id": "n0/P075/0/line", "tech_id": "I02", "sec_mode": "standard"}
{"id": "2", "kind": "research", "op": "validate_project", "line_id": "n0/P075/0/line", "tech_id": "I02"}
{"id": "3", "kind": "industry", "op": "set_line", "line_id": "n0/P028/0/line", "recipe": "P028", "mode": "general", "target_stock_mU": 120000}
{"id": "4", "kind": "industry", "op": "plant_toggle", "facility_id": "n0/P070/0", "online": false}
{"id": "5", "kind": "industry", "op": "build_facility", "hub_id": "n0-h0", "facility_type": "实验室", "profile_id": "S1"}
{"id": "6", "kind": "industry", "op": "warehouse_expand", "hub_id": "n0-h0", "modules": 1}
{"id": "7", "kind": "governance", "op": "fiscal_policy", "tax_rate_bp": 1000, "welfare_budget_mU": 0, "maint_budget_mU": 50000}
{"id": "8", "kind": "logistics", "op": "market_bid", "region_market": "n0-h0", "good": "food", "qty_mU": 50000, "limit_price_mU": 60000, "expire_cycles": 1}
{"id": "9", "kind": "trade", "op": "pool_buy", "hub_id": "n0-h0", "good": "medicine", "qty_mU": 20000}
```

全部支持的操作与精确参数名见 `COMMANDS.md`（由引擎命令注册表自动生成）。注意：`trade/pool_buy` 从本国 RU 储备账户（`reserves`）扣款，开局若 RU 为 0 需先用 `finance/fx`（`side: "buy_ru"`）购入 RU。

## 4. 策略开发建议路线

1. **稳住底座（零崩盘）**：实时监控首都及人口枢纽的 `food`、`water`、`medicine`、`consumer_goods`、`electricity` 满足度，出现缺口优先从市场或世界池（`trade/pool_buy`）补货，避免触发低 `Φ` 惩罚。
2. **控库存与仓容**：按“民生消耗 + 下游产线消耗”动态设置产线 `target_stock_mU`，既防止仓库爆满阻塞产出，又防止过度囤货导致 `chi > 6` 触发 `eta` 折损。
3. **能源与排放优化**：根据实时用电负荷开关冗余煤电（`plant_toggle`），减少燃煤浪费与污染扣分，尽早向高效能源科技过渡。
4. **科研加速度与技术落地**：尽早扩建实验室、培养工程师、并行解锁高权重科技链；**解锁后必须实际运行对应配方/设施/算力以拉满 `ActiveScale`**（仅解锁无落地只拿 15% 科技分！）。
5. **多枢纽扩张、贸易与金融**：开发 `h1..h4` 与优质矿脉、打通走廊物流赚取 `NetTransit` 与出口分、稳健管理主权债与城邦结盟。
6. **算力节流**：将重规划放在每 N 个窗口（如每 2~10 窗口）执行一次，保证平均单窗口耗时 < 50 ms。

## 5. 本地测试、回放与提交自检

> **自测对手提示**：8 国同图对局需要 7 个对手，但选手包仅内置少量公开基线，无法凑成 7 个不同对手。**请自行制作多个不同策略风格或历史快照版本的对手 bot**，直接传入 `local_match.py match --seats ...` 或 `score --opponent <目录/.zip>` 进行多对手混编自测，避免针对单个 `advanced` 过拟合。

```bash
# 快速冒烟自测
python3 scripts/spar.py agents/my_bot --opponent advanced --ticks 40
# 练习计分（对比同席位影子局）
python3 scripts/local_match.py score --bot agents/my_bot --opponent advanced --seat 0 --seed 7 --ticks 400 --legacy-map
# 录制可视化回放并在 viz/player.html 中离线查看
python3 scripts/record_replay.py --a agents/my_bot --b advanced --seed 7 --ticks 200 --legacy-map
# 打包提交
python3 scripts/make_submission.py agents/my_bot --out ../submissions/rift_era.zip
```

交付前务必确认：
1. `make_submission.py` 打包无任何第三方依赖报错；
2. 至少 3 个不同 seed、不同 `--seat` 下 `faults == 0`；
3. 单窗口思考耗时远低于 0.5 秒，`custodial == false`；
4. 终局 `phi` 接近 1.0，无断粮/断电/仓库锁死/主权违约。
