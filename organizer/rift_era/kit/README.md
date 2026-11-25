# 裂陆纪元（rift_era）选手包

八个大国在一张 512×512 的大陆上实时经营：采矿、生产、运输、科研、财政、金融、外交与军事。你写一个程序控制其中一国，
目标是让本国的**国力 V** 尽量高。完整规则在 `rules/`（00–07、10、11 章），命令表在 `COMMANDS.md`，
从零上手请先读 **`GUIDE.md`**（有逐步例子、计分算例和可直接交给 AI 的任务说法）。

> 本包附带的引擎（`runtime/pkg/rift_era`，版本见 `manifest.json` 的 `engine_version`/`engine_hash`）与正式评测机完全一致；
> 规则文本以 `rules/` 为准，引擎行为以本包附带的同一份引擎为准。

## 目录

| 路径 | 内容 |
|---|---|
| `AGENTS.md` / `AI_README.md` | 编程 AI 自动入口与完整参赛指引 |
| `GUIDE.md` | 通俗教程：协议、第一个 bot、本地对局、计分算例、给 AI 的任务说法 |
| `FORMAT.md` / `handbook/` | 公开赛制、选手手册与游戏事实卡（`contest-rules` / `player-guide` / `game-facts`） |
| `COMMANDS.md` | 引擎当前接受的全部 `kind/op` 命令（自动生成） |
| `prompts/task.txt` | 无文件工具时的聊天模式完整题面 |
| `rules/` | 正式规则文档与冻结规则数据 `rules/data/*.json` |
| `sdk.py` | 标准库客户端：读写协议、分页校验、命令 id。**必须**复制进你的提交 |
| `agents/template/` | 可直接运行的模板 bot（`main.py` + `bot.py` + `sdk.py`） |
| `agents/medium/`、`agents/advanced/` | 公开练习对手（见 `agents/PUBLIC_BOTS.md`），也可作为提交样例 |
| `runtime/` | 与评测机相同的引擎（只用 Python 3.11+ 标准库） |
| `scripts/spar.py` | 统一自测入口（支持 `--list` 与提交目录/`.zip` 快速对局） |
| `scripts/local_match.py` | 本地 8 席对局、本地练习计分 |
| `scripts/record_replay.py` | 录制可视化对局回放（支持引擎独立复算校验） |
| `scripts/make_submission.py` | 把策略目录打成提交 zip 并做基本检查 |
| `viz/player.html` | 完全离线单文件 512×512 世界地图与八国宏观回放播放器 |
| `results/`、`replays/`、`logs/` | 本地输出 |

## 提交格式

- 一个 **zip**，根目录有 `main.py`；评测机在沙箱里执行 `python3 main.py`，工作目录可写空间 64 MiB，无网络，
  只能用 Python 标准库，内存上限 1 GiB。
- 统一交卷：放到 `../submissions/rift_era.zip`（`python3 scripts/make_submission.py agents/template --out ../submissions/rift_era.zip`）。

## 时间与故障

- 正式局 **28,800 tick**（4 个纪元 × 720 周期 × 10 tick），每 5 tick 一个决策窗口，单局约 5 小时墙钟。
- 每窗口基础思考时间 0.5 s，超出部分从 **time_pool 450 s** 扣；单窗口最多 30 s；整局思考总计 **3600 s**。
  开局握手另有 15 s。超时 50 次、time_pool 用尽、总时长用尽或进程崩溃 → 本局剩余窗口**托管为空命令**（你的国家照常运转但不再下令）。
- 非法命令只被拒绝（返回 `ERR_*`），不会让其他命令失效。

## 计分（每局 0–100，连续）

正式评测由操作系统安全随机源抽取 **64 个互异随机 seed**（共 64 局完整 `28,800` tick 对局），每个 seed 先跑一局**影子局**（同一地图、同一时钟，8 席由不公开的 3 位参考对手按确定性混编阵容坐镇），再跑**正式局**（仅把第 `seat = k mod 8` 席替换为你的提交，其余 7 席保持与影子局完全一致）。影子席位被替换的参考对手按 `shadow_idx = (k + k // lcm(N, 8)) mod N`（当前 `N = 3`）在 3 位参考对手间轮换，其余 7 席从 `shadow_idx` 起按席位循环均布，64 局中每席恰有 8 局，三位指定影子对手分别出现 21、21、22 次，每个席位与指定影子对手的组合出现 2 或 3 次。比较你这一席与影子局同一席的精确国力有理数 `V`：

```
vtilde(V) = V + 10            (V ≥ 0)
          = 100 / (10 − V)    (V < 0)
R     = vtilde(V_你) / vtilde(V_影子)
score = 50 + 50 · tanh(0.6 · ln R)
```

- **得分含义与聚合**：与该席位被替换的参考对手持平 = `50.00` 分，更好 > 50，更差 < 50（由引擎以精确有理数 `Fraction` 与自适应精度区间外扩计算，避免大数溢出与舍入误差）。最终正式成绩为全部 **64 个 seed 的等权算术平均**（满分 `100`），并附 95% 种子块 bootstrap 置信区间。
- **国力 `V` 构成摘要**：`V = 0.08·U_I + 0.14·U_II + 0.24·U_III + 0.29·U_IV + 0.25·U_end_adj`，其中四纪元均值各取 `720` 周期、期末项取末 `40` 周期并乘可持续性系数 `Φ ∈ [0.35, 1]`；单周期 `U = 0.25·人口 + 0.25·工业与守恒净值 + 0.20·控制与基建 + 0.15·科技落地 + 0.15·国际贡献`。完整公式与常数表见 `rules/07_scoring_and_resources.md` 与 `rules/data/scoring.json`。
- **故障与托管结算**：触发超时 50 次、`time_pool` / 总思考时长耗尽或进程崩溃后，该局不中断也不直接判零，而是由引擎接管剩余窗口（提交空命令）跑完全局并照常按终局 `V` 计分。
- 正式评分池包含 `3` 位参考对手；`medium` / `advanced` 仅用于本地练习。

## 本地练习（非正式、无沙箱）

> **自测对手提示**：本局为 8 国同图对抗，但选手包仅提供少量公开基线（`none` / `simple` / `template` / `medium` / `advanced`），无法凑齐 7 个各不相同的强对手。为避免针对单一公开基线过拟合、充分验证多国混战与博弈鲁棒性，**请自行编写或维护多个不同风格/历史版本的对手 bot**，通过 `--seats` 自由混编 8 席阵容或通过 `--opponent` 作为影子局基准进行自测。

```bash
python3 scripts/local_match.py match --seats agents/template,advanced,advanced,advanced,advanced,advanced,advanced,advanced --ticks 400 --legacy-map
python3 scripts/local_match.py score --bot agents/template --opponent advanced --seat 0 --ticks 400 --legacy-map
```

`--ticks` 必须是 40 的倍数。短局只能看趋势：V 是四个纪元均值加期末项的加权，短局里每个纪元只有几十个周期。
