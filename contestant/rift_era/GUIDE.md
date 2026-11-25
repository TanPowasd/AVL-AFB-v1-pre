# 裂陆纪元上手指南（通俗版）

这份指南用最少的概念带你从“能跑”到“能比较”。所有命令在选手包根目录执行，需要 Python 3.11+。

## 0. 一句话理解这道题

你控制八个大国中的一个。引擎每 tick 结算一次经济（生产、运输、工资、税收、人口……），
每 **5 tick** 给你发一帧“本国能看到的东西”（观测），你回一批命令。整局 28,800 tick 结束后，
按全局表现算出国力 **V**；你的分数取决于你的 V 比“同一席位换成评分对手时的 V”高还是低。

## 1. 程序和评测机怎么说话

评测机用标准输入/输出逐行传 JSON。`sdk.py` 已经替你处理了分页、校验和命令编号，你只需要：

```python
from sdk import Client
client = Client()
init = client.receive()   # 开局一帧：owner（你是哪国，如 "n3"）、map、catalog（科技/配方/军品表）、initial_observation、clock、time_budget
client.ready()            # 15 秒内回复
while True:
    obs = client.receive()            # {"type": "obs", "window": 0, "tick": 0, "cycle": 0, ...本国观测}
    if obs["type"] == "end":
        break
    client.orders(obs["window"], [])  # 必须回同一个 window 号；空列表 = 本窗口不下令
```

命令长这样（`id` 用递增字符串；同一窗口最多 256 条）：

```json
{"id": "7", "kind": "research", "op": "set_focus", "line_id": "n3-line-12", "tech_id": "I02", "sec_mode": "standard"}
```

全部命令见 `COMMANDS.md`，字段的准确定义见 `rules/06_agent_interface.md`。命令写错只会被拒绝（`ERR_*`），不会影响别的命令。

## 2. 第一个 bot：五分钟跑起来

1. 复制模板：`cp -r agents/template agents/my_bot`
2. 看 `agents/my_bot/bot.py`：它只做两件事——实验室不断档研究最低阶可研科技；首都粮食低于 400 单位时去本地市场买粮。
3. 跑一局短的 8 席本地对局（你坐 n0，其余 7 席是公开的 Advanced；`--legacy-map` 跳过约 100 秒的开局地图验收）：

```bash
python3 scripts/local_match.py match --seats agents/my_bot,advanced,advanced,advanced,advanced,advanced,advanced,advanced --ticks 400 --legacy-map
```

输出里 `V` 是每国国力，`faults` 是你这席的协议/超时故障次数（应为 0），完整结果在 `results/`。

4. 打包提交：

```bash
python3 scripts/make_submission.py agents/my_bot --out ../submissions/rift_era.zip
```

它会检查根目录有 `main.py`、没有用到第三方库、`import sdk` 时 `sdk.py` 在包里。

## 3. 一步步改进（建议顺序）

| 步骤 | 做什么 | 为什么 |
|---|---|---|
| ① 民生 | 盯住首都食品、水、药品、民用品库存，按户需留几周期缓冲 | 缺粮/断水会扣人口支柱，还会触发可持续性系数 Φ 惩罚（Φ 最低 0.35） |
| ② 产线目标库存 | `industry/set_line` 的 `target_stock_mU`：够用就停，避免把原料烧成卖不掉的库存 | 仓库满了新产出放不进去，连食品厂也会停 |
| ③ 电力 | 观测里看电站出力；煤电最低出力 40%，闲着要关（`industry/plant_toggle`） | 白烧燃料、加污染 |
| ④ 科研 | 多座实验室（`industry/build_facility` 建 S1 实验室）并行攻关键路线；理论完成就 `validate_project` | 科技解锁更高效配方和设施，影响工业与科技支柱 |
| ⑤ 投资 | 用富余钢/水泥建厂、住宅、仓容 | 产能与住房决定人口和工业的上限 |
| ⑥ 其他 | 税率与地方自治（`governance/fiscal_policy`、`autonomy`）、贸易、金融、外交 | 规则 02/03/07 章；敌对行动本身会让自己的 V 下降，只有带来独立实益时才值得 |

每改一步就和上一个版本在**相同 seed、相同席位**下各跑一局对比 V；短局波动大，至少换 3 个 seed。

> **自建对手提示**：选手包提供的公开基线凑不成 7 个不同对手，长期只打 7 个相同的 `advanced` 容易过拟合。迭代到中后期时，**请自行制作多个不同风格或保留历史版本的对手 bot**，放入 `local_match.py match --seats ...` 组满 8 席多风格对局或用 `--opponent <自制对手>` 检验泛化能力。

## 4. 计分算例

正式计分对每个 seed 跑两局：**影子局**（八席由不公开的多风格强评分对手池按确定性混编阵容坐镇）和**你的局**（第 `k mod 8` 席换成你，其余 7 席不变）。只比较同一席：

```
vtilde(V) = V + 10          (V ≥ 0)
          = 100 / (10 − V)  (V < 0)
R = vtilde(V_你) / vtilde(V_影子),   score = 50 + 50·tanh(0.6·ln R)
```

| V_你 | V_影子 | vtilde 你 | vtilde 影子 | R | ln R | 本局分 |
|---|---|---|---|---|---|---|
| 36 | 36 | 46 | 46 | 1.0000 | 0.0000 | **50.00** |
| 42 | 36 | 52 | 46 | 1.1304 | 0.1226 | **53.67** |
| 30 | 36 | 40 | 46 | 0.8696 | −0.1398 | **45.82** |
| 72 | 36 | 82 | 46 | 1.7826 | 0.5781 | **66.68** |
| −5 | 36 | 6.6667 | 46 | 0.1449 | −1.9315 | **8.97** |

读法：V 每多 6 分（在 36 附近）大约 +3.7 分；V 掉到负数（例如崩盘欠债）几乎拿不到分。
最终成绩 = 所有 seed 的本局分平均（正式 64 个 seed，席位轮换 k mod 8），并给出 95% 区间。

本地练习计分用同一公式，但对手换成公开的 Advanced：

```bash
python3 scripts/local_match.py score --bot agents/my_bot --opponent advanced --seat 3 --seed 7 --ticks 400 --legacy-map
```

> 练习分只说明趋势：正式评分对手比 Advanced 强，正式分通常更低。

## 5. 资源与时限（违反会被托管）

- 每窗口 0.5 s 基础思考时间，超出从 450 s 的 time_pool 扣，单窗口最多 30 s，整局总计 3600 s；开局握手 15 s。
- 超时 50 次、time_pool 或总时长用尽、进程崩溃 → 剩余窗口自动交空命令（国家继续运转但你不再控制）。
- 内存 1 GiB，可写临时空间 64 MiB，无网络，只有标准库。

建议：把重计算放在每 N 个窗口做一次（模板和公开 bot 都是这样），每帧只做增量。

## 6. 可以直接复制给 AI 的任务说法

```text
你在开发“裂陆纪元”策略 bot。工作目录是这个选手包。
1. 先读 README.md、GUIDE.md、COMMANDS.md，以及 rules/06_agent_interface.md（协议与观测字段）和 rules/07_scoring_and_resources.md（V 的计算）。
2. 以 agents/advanced/ 为起点复制出 agents/my_bot/，只用 Python 标准库，入口 main.py，sdk.py 原样保留。
3. 每次改动后，用 scripts/local_match.py score --bot agents/my_bot --opponent advanced --ticks 400 --legacy-map
   在至少 3 个不同 --seed、不同 --seat 上测，记录每次的 V_player、V_shadow、score 和 faults，faults 必须为 0。
   同时注意选手包凑不成 7 个不同对手，中后期请自行制作多个不同风格或历史版本的对手 bot，通过 local_match.py match --seats ... 或 score --opponent <自制对手> 进行多元自测，避免针对单个 advanced 过拟合。
4. 保持每窗口平均思考时间远低于 0.5 s；任何异常都要在 bot 内捕获并返回空命令，不能让进程退出。
5. 先保证民生（食品/水/药品/民用品）不断供、仓库不爆满、电站不空烧，再做科研与投资。
6. 完成后运行 python3 scripts/make_submission.py agents/my_bot --out ../submissions/rift_era.zip，
   并用一段话说明策略要点和本地测得的分数区间。
```
