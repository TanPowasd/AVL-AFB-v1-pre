# 给 AI 的入口：前沿实验室（Frontier Lab）

你是参赛模型。读完本文件与下表中的必读资料后，直接编写、打包、自测并交付参赛策略。常规实现细节请自行决策，不要停留在方案说明或空模板。

- **文件夹模式（默认）**：具备文件读写与终端工具时，推荐在 `agents/submissions/<目录>/` 以多文件 Python 包开发（根目录含 `main.py`，可拆分模块与只读数据文件），用 `scripts/make_submission.py` 打包到 `../submissions/ai_training.zip`，运行本地陪练与回放校验、修复问题后交付，最后简短汇报提交路径与实测成绩。
- **聊天模式**：无文件工具时，使用自包含题面 `prompts/task.txt`，只返回一个包含完整策略代码的 Python 代码块，不要声称已写入文件或运行测试。

---

## 0. 你的交付物

| 项目 | 要求 |
|---|---|
| 文件形式 | `.zip` 多文件压缩包（根目录含 `main.py`，可附带任意子模块 `.py` 与只读数据文件；压缩 `<= 64 MiB`，解压 `<= 256 MiB`，条目 `<= 2048`） |
| 统一提交路径（必交） | `../submissions/ai_training.zip`（全题套件顶层 `submissions/` 目录；正式评测由此读取） |
| 题内开发路径（可选） | `agents/submissions/my_agent/` 或 `agents/submissions/my_agent.zip`（`spar.py`、`run_match.py`、`record_replay.py` 同时支持 `.zip`、目录与单文件 `.py`） |
| 打包校验命令 | `python scripts/make_submission.py <策略目录或.py> --out ../submissions/ai_training.zip` |
| 通信入口 | 子进程经 `engine/pkg_entry.py` 运行 `main.py`，通过 `stdin / stdout` 逐行交互 UTF-8 JSON（参考 `agents/template/` 与 `agents/starter.py`） |
| 依赖限制 | **仅限 Python 3.10+ 标准库**；禁止联网、禁止读写包外文件、禁止读取对手代码 |

---

## 1. 推荐阅读顺序

| 顺序 | 文件 | 作用 |
|---|---|---|
| 1 | `handbook/player-guide.md` | 通信协议、观测字段、动作字典、回执/事件与提交自检清单 |
| 2 | `handbook/game-facts.md` | GPU、机房电力、科技树、数据池、缩放律、API 市场与估值精确数值表 |
| 3 | `agents/template/`（及 `agents/starter.py`） | 多文件 `.zip` 起步模板（`main.py` / `planner.py` / `params.json`）与单文件最小骨架 |
| 4 | `engine/engine.py`、`engine/world.py`、`engine/archive.py` | 唯一权威规则与打包安全实现；任何文档有疑问时以引擎源码为准 |
| 参考 | `FORMAT.md`（同 `handbook/contest-rules.md`） | 双赛制 `scoring_v2` 公开计分口径与红线说明 |
| 参考 | `README.md` | 单题完整中文规则说明 |

---

## 2. 一句话规则与正式计分标准

模拟 `3` 年（`26280` tick = `1095` 天 × `24` 小时）的前沿 AI 实验室研发与商业化博弈：在每个随机种子独立生成的**隐藏缩放律与隐藏数据域质量/污染参数**下，竞拍/租用 5 代 GPU、建设机房与电力、招人研发科技树、获取/合成/蒸馏数据、调度训练任务并发布模型争夺 API 市场。同一份提交同时参加**单人赛（50%）**与**对抗赛（50%）**（协议不区分赛制）：

- **单人赛（50%）**：固定坐 `0` 号位与内置 `engine/solo_npc.py` 同场，每局得分 `100 * C`（`C` 为三年内符合在线资格的最高已上线模型真实能力），取 `64` 个随机种子均值 `solo`；
- **对抗赛（50%）**：对阵 `4` 个固定参考对手 × `64` 个随机种子 × 交换座位（`512` 局），每局按终局股权价值 `Value` 计算 $s = 50 + 50\tanh\big((Value_T - Value_R)/12710.23\big)$ 并等权平均得 `adv`；
- **正式总分（`scoring_v2`，满分 `100`）**：`0.5 * solo + 0.5 * adv`（共 `576` 局，非有限数值局计 `0` 分，完整 `Value` 定义见 `README.md` 第 4 节）。

---

## 3. 硬约束（违反将触发判罚或被拒）

- **压缩包安全与标准库限定**：仅可使用 Python 标准库；包内只读数据请使用 `Path(__file__).resolve().parent / "..."` 读取；禁止路径穿越、加密条目、符号链接、Windows 保留名及非法字符；子进程工作目录为解压后的包目录，`stderr` 被丢弃，`PYTHONHASHSEED=0`、`PYTHONUTF8=1`。
- **交互时限与输出配额**：
  - `init` 握手限时 `10.0` 秒；
  - 每 tick 基础墙钟时限 `2.0` 秒，另有整局共享、不回补的 `120.0` 秒时间池（余额见 `obs["time_pool"]`），超出单 tick 2.0 秒的部分从池中扣除；池空后恢复单 tick 硬时限；
  - 单局累计思考总预算 `2400` 秒（40 分钟）；终局 `end` 消息限时 `1.0` 秒；
  - 单行输出上限 `1 MiB`，未读行队列上限 `64`，超限即终止子进程。
- **容错与非法动作**：
  - 超时或输出非 JSON 对象按空动作 `{}` 处理，迟到回复会被丢弃（累计见 `obs["missed"]`）；未答积压达到 `50` 条、进程崩溃或总预算耗尽后，剩余 tick 全按 `{}` 处理。
  - 动作中出现 `NaN`、`Infinity` 或绝对值 `> 1e15` 的数值，本 tick 整个动作作废；单 tick 每个列表型动作上限 `64` 条（`fire` 上限 `300` 条）；某字段类型错误引发异常时，该项及后续动作作废。
  - 协议中**没有**单人赛/对抗赛标记，同一份程序必须在两种赛制下稳健运行（在单人赛里对手位为内置 `engine/solo_npc.py`，最终只看你的最高有效真实能力 `C`）。
- **赛制与工作区红线**：
  - 仅可使用公开陪练（`blank`、`scaling`、`merchant`、`--solo`）或自身镜像自测；禁止读取其他选手文件或与其他提交互测。
  - 产出仅保存在当前工作区的 `results/`、`replays/`、`logs/` 目录；打包输出仅限 `agents/submissions/` 或 `../submissions/ai_training.zip`。
  - 禁止修改引擎、公开基线、`config/format.json` 和 `config/workspace.json`；禁止使用 `--unrestricted` 或 `--serve`。
  - 本地工具固定使用世界盐值 `practice`，正式评测使用独立私有盐值且每次从操作系统随机源抽取新种子，切勿针对特定种子硬编码。

---

## 4. 编写、打包、验证、回放与改进

```bash
# 查看公开陪练
python scripts/spar.py --list

# 将多文件策略目录（根目录含 main.py）打包并做安全 + 24 tick 冒烟校验
python scripts/make_submission.py agents/submissions/my_agent --out ../submissions/ai_training.zip

# 快速接口与早期策略冒烟（2000 tick ≈ 2.7 个月）
python scripts/spar.py ../submissions/ai_training.zip merchant --seeds 1 --ticks 2000

# 单人赛与对抗赛完整自测（先 1-2 种子验证，再扩大 --seed-count）
python scripts/spar.py ../submissions/ai_training.zip --solo --seed-count 2
python scripts/spar.py ../submissions/ai_training.zip --all --seed-count 2

# 单局逐月日志追踪
python scripts/run_match.py ../submissions/ai_training.zip agents/baselines/merchant.py --seed 1 --ticks 4380

# 录制标准回放并校验确定性（默认同时做结构自校验与同种子复算校验）
python scripts/record_replay.py --a ../submissions/ai_training.zip --b merchant --seed 1 --ticks 4380
python scripts/record_replay.py --a ../submissions/ai_training.zip --solo --seed 1 --ticks 4380
```

录制完成后，直接用本地浏览器打开 `viz/player.html` 并将 `replays/replay_*.json` 拖入页面，即可离线查看双方股权估值、现金流、能力 `C`、算力爬坡、API 市场份额堆叠图以及关键事件时间线（无须启动 HTTP 服务，选手工作区已禁用 `--serve`）。

每次测试后务必检查 `faults` 中的 `timeouts`、`bad_json`、`dead`、`illegal` 是否全为 `0`，并同时关注对抗分与单人赛 `C`。

---

## 5. 八个必踩的坑

1. **显式 `serve` 与自动服务互斥**：一旦发送非空 `serve`，引擎**只**用留出的卡服务 API，不再拿闲置训练卡补足！一旦留卡不足，丢单率 `drop` 飙升，声誉 `rep` 暴跌（最低 `-3.0`）且份额雪崩。没有主训练在跑或想用剩余卡自动兜底时，务必发 `serve: []` 恢复自动分配。
2. **默认学习率偏低、过高则尖峰/发散；手动 `ckpt` 同样扣 `20%` 吞吐**：默认 `lr = 0.0010 * N^-0.3` 低于真实最优 $\eta^* \in [0.0012, 0.0028] \cdot N^{-0.3}$ 会吃二次惩罚；但 $\eta > \eta^*$ 会触发指数级尖峰（每次 `×1.02` loss，10% 概率 `diverged`）。开局做小算力 LR/缩放律探针收益极高；训练中收到 `spike`/`diverged` 立即 `rollback`，收到 `slow_node` 立即 `diagnose`，并按卡数动态调整 `ckpt_every`（约 $\sqrt{0.4 / (\text{cards}/50000)}$）和 `batch`。不要每 tick 刷手动 `ckpt`（同样扣 20% 吞吐）。
3. **机房电力卡脖子导致买卡变库存**：装机硬约束 `sum(cards * kW) * PUE <= MW * 1000`。GPU 拍卖交付需 `4..10` 周，而机房扩容需 `8` 周、新建需 `8..20` 周！必须在买卡前提前扩容 `0` 号机房并择优新建低电价/大容量地区机房，同时早研 `cooling`（PUE `1.30 → 1.15`）直接释放 13% 装机空间。注意拍卖出清前会按 `cash / (price * 256)` 裁剪虚高报价。
4. **监管停审（`20,000,000 G1·h`）白白停机 2–3 周**：单任务累计算力越过 `20M G1·h` 且未获批时强制停审 `336 * reg` tick。正确做法是在上一代结束前提前新建下一代任务（`paused: true`）并提交 `audit_request`，让审核期与当前训练并行重叠！
5. **数据重复衰减与污染复测处罚**：单个数据池反复训练按 $4U(1 - e^{-S/(4U)})$ 衰减；各真实域存在 `0..12%` 隐藏污染，若发布模型 `pub - C > 0.05`，月度复测直接扣 `rep -= 0.3` 并打回 `pub = C`。早研高权重域的 `filter:<domain>` 可揭示真实 `q`/`c` 并将此后该域污染减半。
6. **商业 API 能力门槛与反价格震荡**：玩家模型须达到 `C >= 0.15` 才可参与 API 竞争，效用权重为 `6.0 * pub - 1.5 * ln(max(price, 1.0))`；周内突然无能力提升提价超过 `1.5×` 会立即流失份额，且周结算按周内最差挂牌效用与峰值价计算，低价拉份额再暴涨套利已完全失效。同时融资投前估值封顶于真实 `value()`，融资只用于高 ROI 投资而非套利。
7. **终局有效能力门槛与精确发布时点**：模型计入 `best_c()` 必须满足**累计在线满 730 tick（1 个月）**，或者**终局未下架且在线满 72 tick**（另加 24 tick 发布延迟，故最晚须在 **`t = 26183`** 的观测后提交 `release` 才够 72 tick，切勿秒下架刚发布的高分模型！）。若使用 `wsd` 退火，须在进入最后 `15%` 后实际训练完 `0.15 * target` token，下调目标触发退火后将永久锁定不得回调。
8. **破产线、合成/蒸馏红线与挖角/解雇成本**：现金连续 `720` tick（30 天）`< 0` 直接不可逆破产清算（含取消待上线模型）；单任务合成占比 `> 0.30` 触发坍缩惩罚；存档蒸馏占比 `>= 0.10` 封顶真实能力 `C` 为 `最强老师 C - 0.03`，凡本周推进过训练或周一在跑/暂停/审核中且蒸馏占比 `> 0.20` 者，每周一 `10%` 概率被诉罚没 `15%` 现金且 `rep -= 0.2`；挖对手限每周 `2` 次、成功后 `2` 周冷却并扣 `336×` 薪资签约金，解雇扣 `168×` 薪资遣散费。

---

## 6. 目录地图

```text
README.md               单题完整规则说明
AGENTS.md               编码 AI 自动读取的参赛工作流
AI_README.md            本文件（AI 快速上手指南）
FORMAT.md               公开赛制与计分规则（同 handbook/contest-rules.md）
handbook/
  contest-rules.md      公开赛制与判罚口径
  player-guide.md       通信协议、观测/动作字段与自检清单
  game-facts.md         全量精确数值与公式速查卡
engine/
  archive.py            .zip 压缩包安全校验、流式解压与哈希工具
  pkg_entry.py          多文件策略包入口启动器
  engine.py             官方仿真引擎与子进程协议（唯一权威）
  world.py              按种子生成隐藏缩放律、数据与全局事件
  solo_npc.py           单人赛内置固定背景实验室
  policy.py             工作区边界守卫
  replay.py             回放构建与一致性校验（afb-ai-training-replay/1）
agents/
  template/             多文件 .zip 起步模板（main.py / planner.py / params.json）
  starter.py            最小单文件协议起步模板
  baselines/            公开基线：blank / scaling / merchant
  submissions/          题内开发目录（统一交卷打包至 ../submissions/ai_training.zip）
scripts/
  make_submission.py    打包并校验 .zip 提交压缩包（含 24 tick 冒烟测试）
  spar.py               公开陪练自测（支持 --solo / --all）
  run_match.py          单局对局与逐月日志
  record_replay.py      标准回放录制与复算校验
viz/
  player.html           单文件离线交互式回放播放器
config/
  format.json           默认局长、提交压缩包限制、估值尺度与计分参数
  workspace.json        选手工作区标记（禁止修改）
prompts/
  task.txt              聊天模式自包含完整题面
results/                本地自测结果输出目录
replays/                本地回放输出目录
logs/                   本地调试日志目录
```

---

## 7. 交付前自检

- [ ] 仅使用 Python 标准库；无联网、无读写包外文件；包内数据用 `Path(__file__).resolve().parent` 相对读取
- [ ] 已使用 `python scripts/make_submission.py <dir> --out ../submissions/ai_training.zip` 打包并通过安全与 24 tick 冒烟校验（独立单题包可输出至 `agents/submissions/my_agent.zip`）
- [ ] `spar.py --all` 与 `spar.py --solo` 均完成零故障运行（`timeouts=0, bad_json=0, dead=False, illegal=0`）
- [ ] 终局发布模型满足 `online_ticks >= 72`（最晚 `t <= 26183` 提交 `release` 且未下架）或 `>= 730`，未触发破产或高丢单崩盘
- [ ] 在对抗赛稳定战胜 `blank`、`scaling`、`merchant`，且在单人赛取得有竞争力的真实能力 `C`
