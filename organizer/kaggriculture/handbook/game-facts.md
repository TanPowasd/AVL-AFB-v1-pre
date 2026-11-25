# 游戏事实卡（全部核对自权威规则源码 `engine/rules.py`）

## 对局参数

- `episodeSteps = 720`（`30` 天 × `24` 小时；实际调用 `act` 共 `719` 次：`step = 0 .. 718`，`day = step // 24`，`hour = step % 24`）
- `boardSize = 10`（坐标 `0 <= x < 10, 0 <= y < 10`，`tiles[y][x]`），`turnsPerDay = 24`
- `startingMoney = 3000.0`，`maxMarketOrdersPerTurn = 10`，`shedCapacity = 100`，`weedSpawnChance = 0.005`
- 终局得分 = `step = 718` 结算完毕后各农场的 `money`（未售库存、种子、建筑与动物均不折算现金）。**第 `29` 天没有日终结算**（`step = 719` 不执行）。

## 作物 `CROPS`（`seed` = 固定买种价）

| 作物 | 种价 | 首收天 `first_yield_day` | 最高产天 `max_yield_day` | 间隔 `interval` | 持续产 `ongoing` | 产量上限 `max_yield` |
|---|---:|---:|---:|---:|:---:|---:|
| `WHEAT` | 10 | 2 | 4 | 0 | 否 | 6 |
| `CARROT` | 20 | 2 | 3 | 0 | 否 | 4 |
| `TOMATO` | 50 | 8 | 8 | 1 | 是 | 4 |
| `STRAWBERRY` | 100 | 10 | 10 | 2 | 是 | 4 |
| `MELON` | 80 | 10 | 12 | 0 | 否 | 6 |

- **批量种植检查**：若同一步内全员提交的 `PLANT <CROP>` 次数大于 `private["seeds"][crop]`，本步针对该作物的**全部** `PLANT` 动作统一变 `PASS`。
- **初始状态**：种下时 `planted_day = day`、`watered_today = False`、`consecutive_unwatered = 1`、`fertilized_until_day = -1`。**种下当天必须浇水，否则当日日终 `consecutive_unwatered` 达到 `2` 直接变 `WEED`。** 浇过水后归 `0`，此后需连续 2 天不浇水才枯萎。
- **非持续作物（`WHEAT` / `CARROT` / `MELON`）**：初始 `yield_units = 1`，`max_lifespan_step = (day + max_yield_day + 1) * 24`。在天龄 `age = day - planted_day` 满足 `(max_yield_day + 1) // 2 <= age <= max_yield_day` 的当天首次 `WATER` 时，`yield_units` 增加 `2`（若 `fertilized_until_day >= day`）或 `1`（封顶 `max_yield`）。`HARVEST` 需 `age >= first_yield_day` 且 `yield_units > 0`，收割后格子变 `None`。
- **持续作物（`TOMATO` / `STRAWBERRY`）**：初始 `yield_units = 0`，`max_lifespan_step = -1`。在日终结算（记 `next_day = day + 1`、`d = next_day - planted_day - first_yield_day`）时，若 `d >= 0`、`d % interval == 0` 且累计产果次数 `c = d // interval + 1 <= max_yield`，则 `yield_units` 增加 `2`（若当日浇过水且 `fertilized_until_day >= day`）或 `1`（封顶 `max_yield`）；当 `c == max_yield` 时设 `max_lifespan_step = (next_day + 1) * 24`。`HARVEST` 将 `yield_units` 收入背包并清零，植株保留。
- **过熟衰减**：对 `max_lifespan_step >= 0` 的作物，当 `step >= max_lifespan_step` 且 `(step - max_lifespan_step) % 2 == 0` 时 `yield_units -= 1`；减至 `<= 0` 变 `{"kind": "WEED"}`。
- **施肥 `FERTILIZE`**：消耗背包 `1` 个 `FERTILIZER`，令 `fertilized_until_day = max(fertilized_until_day, day + 2)`。

## 动物 `ANIMALS`

| 动物 | 购买价 | 所需建筑 | 首产天 `first_yield_day` | 间隔 `interval` | 存量上限 `max_held` | 产出商品 |
|---|---:|:---:|---:|---:|---:|:---:|
| `GOOSE` | 300 | `COOP` | 4 | 1 | 4 | `EGG` |
| `COW` | 400 | `PASTURE` | 8 | 2 | 6 | `MILK` |
| `SHEEP` | 500 | `PASTURE` | 6 | 3 | 6 | `WOOL` |

- `BUILD_COOP` 与 `BUILD_PASTURE` 在空地 `None` 上免费建造。
- `BUY_ANIMAL` 购得动物进入 `shed`（占 `1` 格容量），需在谷仓旁 `PICKUP <ANIMAL> 1` 拿到背包，走到空建筑格执行 `PLACE <ANIMAL>` 安置。
- 动物产品用 `HARVEST` 站在动物格上收取（全部 `yield_units` 入背包并清零）。
- `FEED` 消耗背包 `1` 个 `WHEAT`（每只每天限一次）；`CARE` 不耗物（每只每天限一次）。
- **动物日终结算**：未喂则 `consecutive_unfed += 1`，达到 `2` 时动物消失、退回空建筑；存活时若 `(next_day - placed_day - first_yield_day) >= 0` 且能被 `interval` 整除，则产出 `1 + (pending_care_bonus if fed_today else 0)`（封顶 `max_held`）并清零 `pending_care_bonus`；随后若当日 `cared_today and fed_today`，下一轮 `pending_care_bonus += 1`；最后所有存活的动物均置 `fertilizer_available = True`、`fed_today = False`、`cared_today = False`。
- `COLLECT_FERTILIZER` 在 `fertilizer_available == True` 的动物格收取 `1` 个 `FERTILIZER` 到背包（安置当天初始为 `False`，次日起每日可收 1 次）。

## 市场定价 `MARKET_PARAMS`

9 种商品：`WHEAT, CARROT, TOMATO, STRAWBERRY, MELON, EGG, MILK, WOOL, FERTILIZER`。
初始库存均为 `I0 = 10000`（库存无下限，可低于 `0`），初始价等于 `base`，地板价 `PRICE_FLOOR = 1`（`round` 为 Python 四舍六入五成双）。

- 当 `I < I0`：`price = max(1, round(base + amp_below * shape(below_func, I0 - I, T)))`，`amp_below = below_target * base / shape(below_func, T, T)`
- 当 `I >= I0`：`price = max(1, round(base - amp_above * shape(above_func, I - I0, T)))`，`amp_above = above_target * base / shape(above_func, T, T)`
- `shape(func, x, T)`：`linear = x`，`sq = x^2`，`sqrt = sqrt(x)`，`log = ln(1 + x)`，`hinge = (x / T) + 8.0 * max(0, x / T - 1)^2`。

| 商品 | `base` | `T` | `below_func` | `below_target` | `above_func` | `above_target` |
|---|---:|---:|:---:|---:|:---:|---:|
| `WHEAT` | 25 | 400 | `sqrt` | 0.8 | `log` | 0.2 |
| `CARROT` | 35 | 450 | `hinge` | 1.0 | `sqrt` | 0.7 |
| `TOMATO` | 60 | 200 | `hinge` | 0.4 | `sqrt` | 0.6 |
| `STRAWBERRY` | 120 | 100 | `sqrt` | 0.7 | `linear` | 1.6 |
| `MELON` | 250 | 300 | `log` | 0.2 | `sq` | 3.6 |
| `EGG` | 50 | 332 | `hinge` | 0.4 | `log` | 0.2 |
| `MILK` | 160 | 122 | `sqrt` | 0.6 | `linear` | 1.6 |
| `WOOL` | 200 | 105 | `log` | 0.2 | `sq` | 3.2 |
| `FERTILIZER` | 100 | 200 | `linear` | 0.4 | `linear` | 0.4 |

- `SELL <ITEM> <n>`：仅卖 `shed` 内 9 种商品；按当前报价得钱，仅当成交价 `> 1` 时市场库存 `+1`（地板价 `1` 时库存不再增加）。
- `BUY_PRODUCT <ITEM> <n>`：仅限 `WHEAT` / `FERTILIZER`，报价按 `market_price(item, I - 1)`，存入 `shed`（受 `100` 容量限制），市场库存 `-1`。
- `BUY_SEED <CROP> <n>` 与 `BUY_ANIMAL <ANIMAL> <n>`：按固定表价成交，不影响市场库存（种子进 `seeds`，动物进 `shed` 占容量）。
- **逐件同步锁步（per-unit lockstep）**：按订单槽位 `i = 0 .. 9` 推进；`HIRE`/`BUY_LAND` 立即执行，其余买卖订单逐件同步报价（双方基于同一当前库存同时报价）、逐件扣库存成交，单槽最多循环 `99999` 轮，随后刷新公开市场价。

## 城镇商店 `SHOPS`

- `BAKERY`: `["EGG", "WHEAT"]`
- `PIZZA_SHOP`: `["MILK", "TOMATO", "WHEAT"]`
- `BRUNCH_SPOT`: `["EGG", "WHEAT", "STRAWBERRY"]`
- `YARN_STORE`: `["WOOL"]`（单品店，每次消耗 2 件）
- `ICE_CREAM_SHOP`: `["STRAWBERRY", "MILK", "WHEAT"]`
- `PET_CAFE`: `["CARROT"]`（单品店，每次消耗 2 件）
- `SMOOTHIE_SHOP`: `["STRAWBERRY", "MILK"]`
- `FARMERS_MARKET`: `["WHEAT", "CARROT", "TOMATO", "STRAWBERRY"]`

- 每 `4` 步（`step % 4 == 0`）：遍历每家已解锁商店消耗库存（单品店减 `2`，其余每种减 `1`）。
- 每 `24` 步（`step % 24 == 0`）：镇中心消耗除 `FERTILIZER` 外的其余 `8` 种商品库存各 `1` 件。
- 解锁商店：日终若 `(day + 1) % 3 == 0` 且已解锁商店总数 `< 8`，使用日结随机流 `rng = random.Random(seed * 1000003 ^ day)`（在双方杂草抽签之后）从 8 类商店中等概率**可重复**随机解锁 1 家。

## 雇佣、土地与日终重置

- **`HIRE`**：当日第 `k` 次（`k = hires_today >= 0`）费用为 `fib(k)`：`1, 1, 2, 3, 5, 8, 13, 21, 34, ...`；新雇工在当步市场阶段出生在谷仓相邻 4 格 `[(4,4), (5,4), (4,5), (5,5)]` 中当前本方人数最少的第一格，下一步开始行动。
- **`BUY_LAND`**：按 `NE (1000) → SW (2000) → SE (4000)` 顺序解锁象限，将象限内 `"LOCKED"` 格变为 `None`。
- **日终重置（第 `0..28` 天 `step % 24 == 23`）**：
  1. 初始化日结 `rng = random.Random(seed * 1000003 ^ day)`，按座位 `0 → 1` 依次执行：作物日结 → 动物日结 → 空地杂草生长（每空地 `None` 以 `0.005` 概率变 `WEED`，与官方 Kaggle 环境一致）；
  2. 所有单位背包按顺序自动转入 `shed`（超出 `100` 容量的部分销毁）；
  3. 主农夫重置回 `(4, 4)`，**所有雇工解散**（`hands = []`，`hires_today = 0`，`inventories = [{}]`）；
  4. 按需使用同一日结 `rng` 解锁新商店。

## 空间、单位与已实测事实

- **谷仓相邻格**：`(4, 4)`、`(5, 4)`、`(4, 5)`、`(5, 5)`。`DROP`（溢出销毁）、`PICKUP` 及向仓库存货的 `PLACE`（溢出留在背包）在这 4 格均生效，即便该格处于 `"LOCKED"` 状态。
- 移动方向：`NORTH = (0, -1)`、`SOUTH = (0, +1)`、`EAST = (+1, 0)`、`WEST = (-1, 0)`。允许走进 `"LOCKED"` 格，但不能在其上耕作或建造。
- `DIG` 清除脚下作物、杂草或无动物的空建筑（有动物的建筑不能 `DIG`）。
- 实测要点：
  - 单押 `MELON` 极易把自身市场价砸到地板价 `1`；高需求品（如多店需求的 `STRAWBERRY` / `MILK` / `WHEAT`）常随城镇消耗拉升；
  - 单日无节制 `HIRE`（如 16+ 次）会因斐波那契费用迅速吃光现金，须设雇佣护栏；
  - 终局未售库存价值为 `0`，末期（尤其第 29 天，因无日终自动入库）必须主动 `DROP`/`PLACE` 并清仓卖出。
