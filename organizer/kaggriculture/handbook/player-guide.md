# 选手手册（参赛模型必读）

你的任务：写出一个能在 Kaggriculture 农场 1v1 对抗中赢下对手的 Python 策略包（打包为 `.zip` 提交）。
你只写代码（及可选包内只读数据/查表）；对局全程你的模型不再参与。终局按农场现金决定胜负。

## 1. 提交格式与运行约束

- **压缩包形态（正式要求）**：单个 UTF-8 `.zip` 压缩包，根目录必须包含 `main.py`；可自由拆分任意辅助 `.py` 子模块/子包，并携带包内只读数据文件（如 `.json`、预计算参数、策略查表等）。
  - 综合工作区最终统一提交路径：`../submissions/kaggriculture.zip`
  - 独立工作区 / 题内自测默认路径：`agents/submissions/my_agent.zip`（开发阶段可在 `agents/submissions/<目录>/` 编写，使用 `python scripts/make_submission.py <目录> --out ../submissions/kaggriculture.zip` 打包校验；本地工具同时兼容 `.zip`、目录与单文件 `.py`）
  - 压缩包安全配额：压缩体积 `<= 64 MiB`，解压总大小 `<= 256 MiB`，条目数 `<= 2048`，禁止加密条目、符号链接、路径穿越（`..`）和异常压缩比（`> 100×`）。
- **入口**：`main.py` 定义模块级函数 `def act(observation): ...` 返回动作 `dict`（或 `class Agent: def act(self, observation): ...`，裁判自动实例化一次并在整局复用）；
- **标准库与包内数据**：允许使用**完整 Python 3.10+ 标准库**、导入包内子模块、以及通过 `Path(__file__).resolve().parent / ...` 只读读取包内数据文件；取消原单文件 9 模块白名单与正则静态扫描限制。
- **子进程隔离与加载时限**：每局在独立子进程中加载提交（工作目录与 `sys.path[0]` 均为解压后的包根目录），顶层代码、数据加载与 `Agent()` 初始化限时 `60` 秒；观测与动作通过 JSON 传递（元组会变成列表），`print` 输出被丢弃；调用 `exit()`、`quit()`、`raise SystemExit` 或进程崩溃后子进程不再恢复，之后每步计 1 次错误直到判负；正式评测在系统级沙箱内运行，禁止联网、禁止越界读写宿主机文件、内存上限 `1 GiB`。
- **错误预算**：抛出异常或动作结构非法（缺 `farmer`/`hands`/`market` 键、`hands` 长度不符、未知 `op`、或参数类型错误如 `["PICKUP", "WHEAT", "x"]`、`["PLANT", []]`、`["SELL", "WHEAT", float("inf")]`）时，该步替换为全员 `PASS` 并计 `1` 次错误；单局累计超过 `30` 次立即判负（该局 `0` 分）；
- **计时（正式口径）**：每步基础时限 **`1.0` 秒**；单步超出 `1.0` 秒的部分从**每局 `60.0` 秒超时池**中扣除；池耗尽后任意一步超过 `1.0` 秒立即判该局超时负（`0` 分）。池余额即观测中的 `remainingOverageTime`——偶尔长考被允许，但整局总共只有 `60` 秒可借，搜索规划需留余量。

## 2. 观测（每步给你的只读快照）

`observation` 同时支持属性访问 `obs.day` 与字典访问 `obs["day"]`（裁判保证两种都可用）。

| 字段 | 含义 |
|---|---|
| `step` / `day` / `hour` | 全局步（`0..718`，共 `719` 步） / 天（`0..29`） / 小时（`0..23`） |
| `player` | 你的座位号 `0` 或 `1` |
| `farms[2]` | 双方公开农场：`money`、`tiles[10][10]`（按 `tiles[y][x]` 索引）、`farmer: [x, y]`、`hands: [[x, y], ...]`、`unlocked_quadrants`、`hires_today` |
| `market` | `inventory`（9 种商品当前库存）、`prices`（9 种商品当前单价） |
| `town` | `unlocked_shops`（已解锁商店名称列表，可能含重复商店） |
| `private` | 仅己方可见：`shed`（仓库物品/动物数量）、`seeds`（5 种作物种子数量，不占仓库容量）、`inventories`（随身背包列表，无容量上限） |
| `remainingOverageTime` | 剩余超时池秒数（初始 `60.0`） |

- `tiles[y][x]` 单元取值：`None`（空地）、`"LOCKED"`（未解锁）、`{"kind": "WEED"}`、`{"kind": "PLANT", "crop", "planted_day", "watered_today", "consecutive_unwatered", "yield_units", "max_lifespan_step", "fertilized_until_day"}`、`{"kind": "COOP"|"PASTURE", ...}`（若含动物则额外带 `"animal", "placed_day", "yield_units", "consecutive_unfed", "fed_today", "cared_today", "fertilizer_available", "pending_care_bonus"`）。
- `private["inventories"]` 与单位一一对应：`inventories[0]` 是主农夫，`inventories[k]` 对应 `hands[k - 1]`。

## 3. 动作（每步返回）

```python
{
    "farmer": [op, *args],          # 主农夫动作
    "hands":  [[op, *args], ...],   # 长度必须严格等于当前 len(farms[player]["hands"])
    "market": [[op, *args], ...],   # 最多 10 条订单（超出截断）
}
```

- **单位动作 `op`**：`NORTH` / `SOUTH` / `EAST` / `WEST`、`PASS`、`PLANT <CROP>`、`WATER`、`HARVEST`、`DIG`、`FERTILIZE`、`BUILD_COOP`、`BUILD_PASTURE`、`PLACE <ITEM> [n]`、`PICKUP <ITEM> [n]`、`DROP`、`FEED`、`COLLECT_FERTILIZER`、`CARE`。
- **市场订单 `op`**：`BUY_SEED <CROP> <n>`、`BUY_PRODUCT <WHEAT|FERTILIZER> <n>`、`BUY_ANIMAL <ANIMAL> <n>`、`SELL <ITEM> <n>`、`HIRE`、`BUY_LAND`。
- 方向：`NORTH = (0, -1)`、`SOUTH = (0, +1)`、`EAST = (+1, 0)`、`WEST = (-1, 0)`。
- 参数类型合法但当前无法执行的子动作（如越界移动、对空地收割、钱不够买地）会被引擎**静默忽略**，不计错误；但参数类型错误会触发结构错误并扣错误预算。

## 4. 关键机制（精确数值见 `handbook/game-facts.md`）

- **结算顺序**：每步严格按 `单位行动 → 市场订单 → 城镇消耗 → 过熟作物衰减 → 日终结算（仅每日最后一步 (step + 1) % 24 == 0）` 执行。
- **种植与浇水**：
  - 本步全员计划 `PLANT <CROP>` 总数若超过 `private["seeds"][crop]`，本步针对该作物的**全部** `PLANT` 动作统一失效变 `PASS`；
  - 刚种下的作物 `consecutive_unwatered = 1`，**种植当天必须浇水**，否则当日日终直接枯萎变 `WEED`；浇过水后需连续 2 天不浇才会枯死；
  - `WATER` 每天每株只生效一次；非持续作物种下即有 `yield_units = 1`，但未达到 `first_yield_day` 前 `HARVEST` 会被忽略。
- **仓库与背包**：
  - 收割的农产品与动物产品进入随身背包；`SELL` 只卖 `shed` 里的商品；`shed` 总容量 `100`（所有农产品与未放置动物共享）；
  - 谷仓相邻格为棋盘中心 4 格 `(4, 4)`、`(5, 4)`、`(4, 5)`、`(5, 5)`；`DROP`、`PICKUP` 及向仓库存货的 `PLACE` 仅在这 4 格生效（即使格子处于 `LOCKED` 状态也能存取）；
  - `DROP` 清空整个背包入库，**超出 100 容量的部分直接销毁**；`PLACE <ITEM> [n]` 入库受剩余容量限制，放不下的保留在背包里。
- **日终结算与第 29 天清仓**：
  - 第 `0..28` 天日终（`hour == 23`）会自动把所有单位背包转入 `shed`（溢出销毁）、主农夫重置回 `(4, 4)`、**所有当日雇工自动解散**并清零 `hires_today`；
  - **第 `29` 天没有日终结算**（引擎在 `step = 718` 后结束，`step = 719` 不执行），第 29 天背包里的货不会自动入库！必须手动走到谷仓相邻格执行 `DROP` 或 `PLACE` 后再 `SELL`（因单位行动先于市场订单，最晚可在 `step 718` 同一步完成 `DROP` 和 `SELL`）；
  - 终局只看 `money`：未卖出的库存、种子、建筑与动物价值全部为 `0`。
- **雇佣与买地**：
  - `HIRE` 当日第 `k` 次费用为斐波那契数 `fib(k)`（`1, 1, 2, 3, 5, 8, 13, 21, ...`），每日重置；新雇工在当步市场阶段出生在谷仓相邻 4 格中人数最少的第一格，下一步开始行动；
  - `BUY_LAND` 依次解锁 `NE(1000) → SW(2000) → SE(4000)`；初始只有 `NW` 解锁。单位可走入 `LOCKED` 格但不能在其上耕作或建造。
- **市场与城镇**：
  - 卖货压低市场价（地板价 `1`），买货（`WHEAT`/`FERTILIZER`）推高市场价；双方同槽位订单按逐件锁步成交，同卖同品会互相砸价；
  - 城镇商店每 4 步消耗需求品（单品店消耗 2 件），镇中心每 24 步消耗除 `FERTILIZER` 外的其余 8 种商品各 1 件，推动价格回升。

## 5. 允许的做法与本地自测

允许并鼓励：多模块架构、预计算策略表/开局参数（只读放在 `.zip` 内）、精确市场模拟、前瞻搜索/动态规划（在时间预算内）、仅基于公开观测的对手建模（看不到对手的 `seeds`/`shed`/`inventories`）。

本地可使用：
- `python scripts/make_submission.py <策略目录> --out ../submissions/kaggriculture.zip`：打包并做安全与冒烟校验；
- `python scripts/spar.py --list`：查看公开陪练（`passive` / `random` / `greedy` / `champion`）；
- `python scripts/spar.py ../submissions/kaggriculture.zip greedy --seed-count 2`：按正式计分口径对阵公开基线；
- `python scripts/run_match.py --a ../submissions/kaggriculture.zip --b --self --seed-count 2`：镜像自搏回归测试；
- `python scripts/record_replay.py --a ../submissions/kaggriculture.zip --b greedy --seed 11`：录制回放并用 `viz/player.html` 离线查看。

禁止针对特定种子或对手硬编码逻辑；禁止读取外部目录或其他选手文件。

## 6. 提交前自检清单

1. 已用 `scripts/make_submission.py` 打包为 `.zip`，根目录含 `main.py`，无越界路径或外部文件依赖；
2. 仅使用 Python 标准库与包内子模块/只读数据文件，不联网、不写文件；
3. 每步 `hands` 数组长度等于当前雇工数，`market` ≤ 10 条，无参数类型错误或非有限数值；
4. 极端情形（`money = 0`、`hands = []`、全图无空地、`shed` 已满、第 29 天手动清仓）均不抛异常；
5. 已保存至目标路径（综合工作区：`../submissions/kaggriculture.zip`；独立工作区：`agents/submissions/my_agent.zip`），并在本地至少稳定击败 `greedy` 基线。
