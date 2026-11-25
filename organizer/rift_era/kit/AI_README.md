# 给 AI 的参赛入口：裂陆纪元（rift_era）

你是参赛模型。读完本文件与下列必读资料后，直接编写策略、跑通本地测试、按数据迭代改进并打包交付。不要停留在方案说明或原封不动的模板。

**文件夹模式（默认）**：直接基于 `agents/template/` 或 `agents/advanced/` 复制并开发 `agents/my_bot/`，用 `scripts/spar.py`、`scripts/local_match.py` 和 `scripts/record_replay.py` 验证，再用 `scripts/make_submission.py` 打包提交。
**聊天模式**：若无法读写文件，使用 `prompts/task.txt` 中的完整题面，直接输出完整可运行的 `bot.py`（搭配模板 `main.py` 与 `sdk.py`）。

---

## 0. 你的交付物

| 项 | 要求 |
|---|---|
| 交付形式 | 一个 `.zip` 压缩包，**根目录必须包含 `main.py`**（连同 `sdk.py` 及你自己的模块） |
| 交付路径 | 统一交卷：`../submissions/rift_era.zip`；或本目录：`agents/submissions/my_bot.zip` |
| 启动方式 | 评测机在隔离沙箱内执行 `python3 main.py`，经标准输入输出（JSONL）通信 |
| 依赖限制 | **仅 Python 3.11+ 标准库**，禁止第三方库，无网络访问 |
| 体积与资源 | 压缩包 ≤ 512 MiB、解压 ≤ 2 GiB / 4096 文件；内存 1 GiB；可写目录 64 MiB |

---

## 1. 推荐阅读顺序

| 顺序 | 文件 | 作用 |
|---|---|---|
| 1 | `GUIDE.md` | 五分钟跑通教程、改进优先级、计分算例 |
| 2 | `handbook/player-guide.md` | JSONL 协议、`sdk.py` 用法、观测字段、提交与调试清单 |
| 3 | `handbook/game-facts.md` | 时钟、四纪元、开局禀赋、五支柱、`V` 与 `Phi`、`eta` 与 `Loss_action` 核心数值 |
| 4 | `COMMANDS.md` | 引擎接受的全部 `kind/op` 命令表面（自动生成，唯一命令参考） |
| 5 | `agents/template/bot.py` 与 `agents/advanced/bot.py` | 起步模板与公开参考策略源码（可直接作为开发起点） |
| 深入 | `rules/06_agent_interface.md`、`rules/07_scoring_and_resources.md` | 观测过滤规则、全部五支柱与评分精确公式 |
| 参考 | `FORMAT.md`（与 `handbook/contest-rules.md` 同源） | 公开赛制、时限与合规边界 |

---

## 2. 一句话规则

8 个大国在一张 **512×512** 大陆地图上实时经营 **28,800 tick**（4 纪元 × 720 周期 × 10 tick，每 5 tick 一个决策窗口），统筹民生、工业、能源、物流、科研、金融、外交与军事；终局按四纪元加权与可持续性系数 `Φ` 算出精确国力 **`V`**，与同地图同席位不公开多风格强评分对手混编影子局的 `V_shadow` 比较：

```text
vtilde(V) = V + 10            (V >= 0)
          = 100 / (10 - V)    (V < 0)
R         = vtilde(V_player) / vtilde(V_shadow)
score     = 50 + 50 * tanh(0.6 * ln R)
```

---

## 3. 硬约束（违反 = 托管或判零）

- **标准库唯一**：`make_submission.py` 会静态检查第三方 `import`；`sdk.py` 必须原样复制进提交包。
- **窗口协议**：开局收到 `init` 后 **15 秒内**调用 `client.ready()`；每个 `obs` 窗口必须回复相同 `window` 编号，每窗口最多 **256 条**命令。
- **严禁进程崩溃**：策略内部任何异常必须 `try/except` 捕获并返回 `[]`；进程退出即本局剩余窗口全部托管为空命令。
- **思考时间预算**：每窗口基础 **0.5 秒**，超出部分扣 **450 秒 `time_pool`**；单窗口硬上限 **30 秒**；整局总思考上限 **3600 秒**；超时达 **50 次**或预算耗尽即被托管为空命令。
- **合法命令拒绝**：非法命令仅返回 `ERR_*` 并被忽略，不影响同窗口其他合法命令，但请勿刷无效指令浪费配额。
- **公开对手与自建对手自测**：选手包仅提供少量公开练习基线（`none`、`simple`、`template`、`medium`、`advanced`），凑不齐 8 席对局所需的 7 个不同对手；**需要你自行制作多个不同风格或历史迭代版本的对手 bot 进行本地 8 席混编自测与影子对照**（严禁读取他人代码；不得修改 `runtime/`、`rules/`、`config/workspace.json` 与 `manifest.json`；本工作区禁止启用 HTTP 共享服务）。

---

## 4. 本地开发、测试与可视化复盘

```bash
# 0) 查看公开对手列表
python3 scripts/spar.py --list

# 1) 复制公开策略作为开发起点
cp -r agents/advanced agents/my_bot

# 2) 快速冒烟自测（40 tick 快速验收接口与零故障）
python3 scripts/spar.py agents/my_bot

# 3) 多 seed / 轮换席位练习计分（建议至少测 3 个 seed、400~2000 tick）
python3 scripts/local_match.py score --bot agents/my_bot --opponent advanced --seat 0 --seed 7 --ticks 400 --legacy-map
python3 scripts/local_match.py match --seats agents/my_bot,advanced,advanced,advanced,advanced,advanced,advanced,advanced --seed 7 --ticks 400 --legacy-map

# 4) 录制可视化回放，拖进 viz/player.html 查看 512x512 地图、八国实时榜、五支柱与事件流
python3 scripts/record_replay.py --a agents/my_bot --b advanced --seed 7 --ticks 200 --legacy-map

# 5) 打包提交（统一交卷路径）
python3 scripts/make_submission.py agents/my_bot --out ../submissions/rift_era.zip
```

> 提示：`--legacy-map` 跳过正式地图约 100 秒的 150-tick 开局自举验收，适合快速迭代；定型前请去掉 `--legacy-map` 在正式地图上至少跑通一次。另外，由于 8 席对局需要 7 个对手而选手包仅提供少量公开基线，**请自行制作并维护多个不同风格或历史版本的对手 bot**，传入 `local_match.py match --seats ...` 或 `score --opponent <自制对手目录/.zip>` 进行多对手混编自测，避免对单一公开对手过拟合。

---

## 5. 六个高频踩坑点（先看省半天）

1. **首都断粮 / 断水 / 断药**：开局药品缓冲区约 160 周期耗尽，且缺粮断水会同时重创人口支柱与可持续性系数 `Φ`（最低跌至 `0.35`，直接腰斩终局权重）。
2. **仓库爆满卡死全链**：公共仓容满时制造与民生产出都放不进去；务必用 `industry/set_line` 设合理的 `target_stock_mU`，并用余料适度扩仓（注意空置扩仓不加 `Kinfra`）。
3. **无脑囤货触发 `eta` 折损**：逐国家逐物品计算库存/20期均耗比 `chi`，`chi > 6` 时有效增加值折损直至归零（且军事消耗/损毁会永久累入只读 `SinkDebt`）。
4. **盲目盖空壳厂与电站空烧**：已建成但全部产线未装机空置的工厂 `Kinfra` 只按 `0.25` 计；煤电最低出力 40%，富余电站不关（`industry/plant_toggle`）会白烧煤、增污染。
5. **科研只做理论不做验证 / 单实验室排队**：科技理论进度满后必须发 `research/validate_project` 才会真正解锁；尽早利用富裕工程师多建实验室并行冲刺关键科技。
6. **无收益的敌对行动**：任何军事射击、行军、封锁、网络/反卫星等敌对消耗都强制按目录值计入 `Loss_action` 严格扣减自身 `V`，只有带来更大独立实益（领土、资源、贸易）时才值得发动。

---

## 6. 工作区目录地图

```text
AGENTS.md               编码 AI 自动读取的任务入口
AI_README.md            你在读的完整 AI 参赛指引
README.md               选手包总览
GUIDE.md                从零上手教程与计分算例
FORMAT.md               公开赛制说明（同 handbook/contest-rules.md）
COMMANDS.md             全量引擎命令表面
handbook/               contest-rules.md / player-guide.md / game-facts.md
rules/                  正式规则全文（00–07、10、11）与冻结数据 rules/data/*.json
sdk.py                  标准库通信客户端（必须原样复制到提交包内）
agents/
  template/             最小可运行模板
  medium/               公开练习对手（广度学者）
  advanced/             公开练习对手（固定计划 + 实验室突击）
  submissions/          本地打包输出目录
runtime/                与评测机一致的引擎（禁止修改）
scripts/
  spar.py               统一快速自测入口
  local_match.py        8 席对局与本地练习计分
  record_replay.py      录制可视化回放（调用 scripts/replay_viz.py）
  make_submission.py    打包并静态检查提交 zip
viz/player.html         完全离线 512x512 地图与宏观仪表盘回放播放器
prompts/task.txt        无文件工具时的聊天模式完整题面
results/ replays/ logs/ 本地输出目录
```

---

## 7. 交付前自检清单

- [ ] 提交 `.zip` 根目录存在 `main.py` 与原样 `sdk.py`，通过 `make_submission.py` 检查
- [ ] 仅使用 Python 标准库；无网络与外部路径依赖
- [ ] 所有决策异常均被捕获并返回合法订单列表（≤ 256 条/窗口）
- [ ] 多 seed / 多席位测试中 `faults == 0`，单窗口思考耗时远低于 0.5 s
- [ ] 已录制至少一局回放并在 `viz/player.html` 或指标中确认民生、能源、仓容与科研运转正常
