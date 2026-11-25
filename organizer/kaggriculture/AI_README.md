# 给 AI 的入口：读完这个文件夹，写出你的农场对抗策略包

你是参赛模型。读完本文件和下列 4 个必读文件后，直接编写并交付参赛代码包。常规实现选择自行决定，不要只给计划或策略说明。

**文件夹模式（默认）**：有文件读写能力时，推荐在 `agents/submissions/` 下以多文件目录开发（根目录含 `main.py`，可拆分辅助模块与只读查表/参数文件），用 `scripts/make_submission.py` 打包为 `.zip` 并自测、修复问题；最后简短报告提交包路径和真实测试结果。
**聊天模式**：无法读写文件时，使用裁判方提供的完整 `prompts/task.txt`，返回完整可执行代码。

**使用范围**：每个参赛 AI 使用独立工作区副本。初始文件夹只含公开材料，没有其他选手的提交、成绩、日志或回放。本目录不配置操作系统沙箱，也不会改变本机 AI 的读取权限；请自觉遵守边界，不要访问工作区外的外部文件。

---

## 0. 你的交付物

| 项 | 要求 |
|---|---|
| 提交格式 | **UTF-8 `.zip` 压缩包**（根目录必须含 `main.py`；可自由附带任意辅助 `.py` 子模块与包内只读数据文件如 `.json`/查表/权重） |
| 统一交卷路径（综合工作区必交） | `../submissions/kaggriculture.zip`（即顶层 `submissions/kaggriculture.zip`；正式评分只从顶层 `submissions/` 读取） |
| 题内开发 / 独立工作区默认路径 | `agents/submissions/my_agent.zip`（本地工具同时兼容 `.zip`、含 `main.py` 的目录及单文件 `.py`） |
| 入口 | `main.py` 定义模块级 `def act(observation) -> dict:`；或 `class Agent:` 实现 `def act(self, observation) -> dict:` |
| 体积配额 | 压缩后 `<= 64 MiB`，解压后 `<= 256 MiB`，条目数 `<= 2048`（不限代码行数） |
| 依赖与能力 | **完整 Python 3.10+ 标准库** + 导入压缩包内子模块 + 只读读取包内数据文件（如 `Path(__file__).resolve().parent / "params.json"`） |

工作区中已有自己的策略时可以继续改进。不得读取或覆盖其他选手的文件；不要为了猜模型名而询问用户。Python 要求 3.10+，陪练和裁判只依赖标准库，无须安装额外依赖。

## 1. 按顺序读这几个文件（读完即可动手）

| 顺序 | 文件 | 作用 |
|---|---|---|
| 1 | `handbook/player-guide.md` | 观测字段、动作字典、压缩包规范、自检清单 |
| 2 | `handbook/game-facts.md` | 作物/动物/市场/雇佣/土地/城镇的精确数值 |
| 3 | `agents/template/`（`main.py`、`planner.py`、`params.json`） | 多文件包 + 包内数据读取起步模板（极简参考见 `agents/starter.py`） |
| 4 | `engine/rules.py` | 官方规则源码，**唯一权威**；任何文档与它冲突以它为准 |
| 参考 | `README.md` | 完整题面规则、结算顺序与 `scoring_v2` 正式计分公式 |
| 参考 | `FORMAT.md` | 公开赛制与判罚摘要（同 `handbook/contest-rules.md`） |
| 参考 | `engine/loader.py` | 压缩包安全校验与子进程加载协议 |

沙箱隔离约束的是**参赛代码运行时**。编写代码的 AI 可以读取本工作区公开资料、使用终端运行打包与自测脚本，并查看自己本次自测生成的结果与回放。

## 2. 一句话规则与正式计分标准

1v1 农场经营，配置 `episodeSteps = 720`（`30` 天 × `24` 小时；实际执行 `step = 0 .. 718` 共 `719` 步），**终局现金高者胜**。终局未卖出的库存、种子、建筑与动物价值均为 `0`。

- **正式计分（`scoring_v2`，满分 `100`）**：固定对阵 `3` 个隐藏参考对手 × `64` 个 OS 随机场景 × 交换座位（共 `384` 局），正常结束单局按 $s = 50 + 50\tanh\big((money_T - money_R) / 80096.26\big)$ 连续计分并等权平均（现金相同为 `50` 分）。
- **技术判负优先**：本方超时、异常超 `30` 次或加载失败判负时该局直接得 `0` 分；仅对手技术判负得 `100` 分；双方均判负各得 `0` 分。

## 3. 硬约束（违反 = 拒赛或判负）

- **压缩包结构与安全校验**：根目录必须包含 `main.py`；禁止路径穿越（`..`）、绝对路径、符号链接、加密条目与异常压缩比（`> 100×`）；压缩体积 `<= 64 MiB`，解压总大小 `<= 256 MiB`，条目数 `<= 2048`。
- **子进程与系统沙箱隔离**：每局在独立子进程中加载提交（工作目录与 `sys.path[0]` 均为解压后的包根目录），顶层代码、数据读取与 `Agent()` 初始化限时 `60` 秒；正式评测在系统级沙箱中运行，禁止联网、禁止读写包外文件、内存上限 `1 GiB`。观测与动作经 JSON 传递（元组变列表），`print` 输出被丢弃；调用 `exit()`、`quit()`、`raise SystemExit` 或进程崩溃后子进程不再恢复，后续每步计 1 次错误直至判负。
- **结构与参数约束**：`farmer`、`hands`、`market` 三键必填；**`hands` 长度必须严格等于当前工人数**（`len(obs["farms"][me]["hands"])`）；`market` 每步最多 10 条订单（超出截断）；未知 `op` 或参数类型错误（如 `["PICKUP", "WHEAT", "x"]`、`["PLANT", []]`、`["SELL", "WHEAT", float("inf")]`）均算结构错误，替换为全员 `PASS` 并计 `1` 次错误。
- **计时（正式口径）**：每步基础时限 **`1.0` 秒**；单步超出 `1.0` 秒的部分从**每局 `60.0` 秒超时池**（`observation["remainingOverageTime"]`）中扣除；池耗尽后任意一步超过 `1.0` 秒立即杀掉子进程并判超时负（该局 `0` 分）。
- **错误上限**：单局累计异常/结构错误超过 `30` 次立即判负（该局 `0` 分）。
- **公平性红线**：不得针对特定种子或对手写死逻辑；不得读取其他选手文件，不得与其他选手对战。公开陪练仅限 `passive` / `random` / `greedy` / `champion`（可自身镜像自搏）。
- **工作区边界**：只能访问当前工作区（及综合工作区顶层的 `../submissions/kaggriculture.zip`）；自测成绩、调试 trace、回放与日志只保存在 `results/`、`replays/`、`logs/`。自测工具会拒绝外部路径（含软链接越界）、非公开陪练与 `--unrestricted`；禁止修改 `config/workspace.json`、规则引擎、裁判、陪练脚本与 `config/format.json`。

## 4. 编写、打包、验证、改进、交付

充分利用压缩包上限：可将规划器、市场预测、路径优化、畜牧调度拆分为多个模块，并在包内附带只读查表/参数文件。`agents/template/` 与 `agents/starter.py` 仅用于理解接口，不算完成参赛任务。

```bash
# 1. 把多文件策略目录（根目录含 main.py）打包并做 3 步冒烟校验
python scripts/make_submission.py agents/submissions/my_bot --out ../submissions/kaggriculture.zip
# （独立工作区可输出至 agents/submissions/my_agent.zip）

# 2. 查看公开陪练列表
python scripts/spar.py --list

# 3. 与公开基线自测（同样支持直接传开发目录 agents/submissions/my_bot 或 .zip）
python scripts/spar.py ../submissions/kaggriculture.zip passive --seed-count 1
python scripts/spar.py ../submissions/kaggriculture.zip greedy --seed-count 2
python scripts/spar.py ../submissions/kaggriculture.zip --all --seed-count 2

# 4. 单场详细对局 / 镜像自搏回归测试
python scripts/run_match.py --a ../submissions/kaggriculture.zip --b agents/baselines/greedy_farm.py --seed-count 4
python scripts/run_match.py --a ../submissions/kaggriculture.zip --b --self --seed-count 2

# 5. 挑战公开冠军陪练（运行较慢，--all 不包含它，建议先用 --seed-count 1）
python scripts/spar.py ../submissions/kaggriculture.zip champion --seed-count 1
```

- 短名对手：`passive`（全员待机）/ `random`（随机合法）/ `greedy`（基础贪心）/ `champion`（公开强陪练对手，正式评分使用独立的 3 个隐藏参考对手）。
- 检查自己生成的 `results/<时间戳>/spar.json` 与 `results/<时间戳>/match.json`：重点排查 `stats` 中己方的 `errors`、`first_error`、`timeout` 与 `overage_used_s`（`seat_a` 为己方座位）。命令退出码为 0 不代表没有静默错误或已经赢下对手。
- 正式评测由裁判方在正式评测环境中按 `scoring_v2` 百分制运行（固定对阵 3 个隐藏参考对手，默认 64 个随机场景 × 双座位 = 384 局，详见 `README.md` 第 6 节）。

录制对局回放并用浏览器肉眼复盘（强烈推荐，定位路径与经济瓶颈远快于看纯数字）：

```bash
python scripts/record_replay.py --a ../submissions/kaggriculture.zip --b greedy --seed 11
```

生成的回放 JSON 落在 `replays/`，可直接拖入离线单文件播放器 `viz/player.html` 查看逐帧 10×10 棋盘、现金曲线、市场行情与双方动作。选手工作区禁用 `--serve`，录制在裁判计时区间之外，不影响超时统计。

## 5. 六个必踩的坑（省你半天）

1. **雇工出生点可能落在未解锁格（`LOCKED`）**：谷仓相邻 4 格中 `(5,4)`、`(4,5)`、`(5,5)` 开局未解锁；单位可以走进或出生在 `LOCKED` 格（仍可执行 `DROP`/`PICKUP`/入库 `PLACE`），但不能耕作或建造，必须写移回已解锁区的逻辑；
2. **新种作物当天必须浇水，且非持续作物初产 `yield_units = 1`**：刚种下时 `consecutive_unwatered = 1`，当天不浇水日终直接枯成 `WEED`；另外非持续作物种下即有 `yield_units = 1`，但未达 `first_yield_day` 时 `HARVEST` 会被静默忽略，判断收割必须同时检查天龄；
3. **第 29 天没有日终结算**：`step = 719` 不执行，第 29 天背包物品**不会自动入库**；必须在谷仓相邻格手动 `DROP` 或 `PLACE` 后再 `SELL`（单位行动先于市场订单，最晚可在 `step 718` 同一步完成 `DROP` 与 `SELL`）；
4. **雇工每日自动解散且雇佣费当日斐波那契递增**：当日第 `k` 次雇佣费 `fib(k) = 1, 1, 2, 3, 5, 8, 13, 21, ...`，日终（第 0..28 天）自动把背包转入谷仓、主农夫重置回 `(4, 4)`、解散所有雇工并清零 `hires_today`；单日狂雇会迅速烧光现金；
5. **卖货砸价与同槽锁步竞争**：市场按槽位逐件锁步成交，同品卖越多价格越低，双方同卖会互相压价；结合城镇商店消耗错峰/分散出货收益更高；
6. **谷仓容量上限 `100`**：`DROP` 把整个背包倒入谷仓，超出 `100` 的部分**直接销毁**；若需避免丢货，可在谷仓旁改用 `PLACE <ITEM> <n>`（装不下的部分留在背包）。

## 6. 目录地图

```text
README.md             完整权威规则与 scoring_v2 正式计分说明
AGENTS.md             编码 AI 自动读取的参赛工作流入口
AI_README.md          本文件（快速参赛指南）
FORMAT.md             公开赛制与判罚摘要（同 handbook/contest-rules.md）
handbook/             选手手册（player-guide.md）、游戏事实卡（game-facts.md）与公开赛制
engine/               官方规则 rules.py、裁判 arena.py、压缩包加载器 loader.py、回放编码 replay.py
  champion/           公开冠军陪练运行代码（禁止修改）
agents/
  template/           多文件压缩包起步模板（main.py + planner.py + params.json）
  starter.py          极简单文件参考模板
  baselines/          三个公开基线：passive / random_legal / greedy_farm
  reference/
    champion.py       公开冠军陪练适配器
  submissions/        题内开发自测目录（综合工作区终版须打包至 ../submissions/kaggriculture.zip）
scripts/              make_submission.py 打包 / spar.py 陪练 / run_match.py 对局 / record_replay.py 回放
viz/player.html       离线单文件回放播放器（无外部 CDN 依赖）
config/format.json    比赛时间、预算、加赛种子与计分尺度参数
config/workspace.json 独立选手工作区标记（禁止修改）
prompts/task.txt      无文件工具时的聊天模式完整题面
results/              本地自测结果输出目录
replays/              本地录制回放输出目录
logs/                 本地运行日志目录
manifest.json         选手包文件清单与校验哈希
```

## 7. 交付前自检

- [ ] 已使用 `scripts/make_submission.py` 打包为合法 `.zip`（综合工作区：`../submissions/kaggriculture.zip`；独立工作区：`agents/submissions/my_agent.zip`），根目录含 `main.py`
- [ ] 所有包内数据均通过 `Path(__file__).resolve().parent` 相对路径只读读取，不写文件系统、不联网
- [ ] 每步 `hands` 长度与当前雇工数一致，`market` ≤ 10 条，无参数类型错误或 `inf`/`NaN`
- [ ] `money = 0`、`hands = []`、无空地、谷仓满、第 29 天手动清仓均不抛异常且无库存残留
- [ ] 已用打包后的 `.zip` 与 `greedy`（及可选 `champion`）实测，并如实报告终局现金、得分、错误数与超时情况
