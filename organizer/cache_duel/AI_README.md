# 给 AI 的入口：读完这个文件夹，写出你的 Cache Duel 策略

你是参赛模型。读完本文件和下方 4 个必读文件后，直接编写并交付参赛代码。常规实现与参数选择自行决定，不要只停留在计划或起步模板。

**文件夹模式（默认）**：有文件读写能力时，直接写入提交文件，运行本地陪练、单场对抗与回放复盘，持续改进；最后简短汇报提交路径与实测结果。
**聊天模式**：无法读写文件时，基于裁判方提供的完整 `prompts/task.txt`，只返回一个包含完整单文件 Python 代码的代码块。

---

## 0. 你的交付物

| 项 | 要求 |
|---|---|
| 文件 | 单个 UTF-8 Python 3.10+ 源文件 |
| 最终统一提交路径 | `../submissions/cache_duel.py`（工作区顶层 `submissions/cache_duel.py`，正式评测由此路径读取） |
| 题内开发路径（可选） | `agents/submissions/my_agent.py`（验证通过后请同步到 `../submissions/cache_duel.py`） |
| 角色 | 同一个文件根据首行初始化消息中的 `role`，同时支持 `"attacker"` 与 `"defender"` |
| 通信方式 | 子进程 `stdin / stdout` 逐行 UTF-8 JSON（每次输出必须 `flush=True`） |
| 依赖 | 仅限 Python 标准库，禁止联网、禁止读写外部文件或探测对手代码 |

---

## 1. 按顺序读这几个文件（读完即可动手）

| 顺序 | 文件 | 作用 |
|---|---|---|
| 1 | `handbook/player-guide.md` | 逐行 JSON 协议、握手/收尾、时间池与提交自检清单 |
| 2 | `handbook/game-facts.md` | 3 组官方配置、Belady OPT 公式、犯规替代规则与 4 个公开基线行为 |
| 3 | `agents/starter.py` | 最小可运行双角色模板（直接照此骨架扩展） |
| 4 | `engine/engine.py` | 官方裁判与 Belady 离线最优源码，**唯一权威**；任何文档与它冲突以它为准 |
| 参考 | `FORMAT.md` / `README.md` | 公开赛制、正式 scoring_v2 抽样与计分细则 |

---

## 2. 一句话规则

双角色在线缓存攻防博弈：攻击方每步发出一个请求 key（仅看到上一步 `last_hit`），防守方在缓存满且缺失时决定驱逐哪一个 key。终局按离线最优 **Belady/MIN** 缺失数 `OPT` 与防守实际缺失数 `M_def` 计分：

- 防守得分：`def_score = OPT / M_def ∈ (0, 1]`（越大越好）
- 攻击得分：`att_score = 1 - def_score ∈ [0, 1)`（越大越好）
- 综合总分：`0.5 * 防守均分(0~100) + 0.5 * 攻击均分(0~100)`

---

## 3. 硬约束（违反 = 犯规替代或进程判死）

- **纯标准库**：仅允许 Python 标准库；`stderr` 被丢弃；环境变量固定 `PYTHONHASHSEED=0`；工作目录为提交文件所在目录。
- **严格逐行 JSON + flush**：握手行、每步 `t = 0..N-1`（包括 `need_evict == false` 时！）、以及 `{"t": N, "end": true}` 都必须回复一行合法 JSON 并立即 `flush`。
- **类型校验**：`key` 与 `evict` 必须是纯 `int`（`bool` 如 `True`/`False`、`float`、`None` 均判犯规！），且 `0 <= key < K`、`evict in current_cache`。
- **时间限制**：
  - 单步基础时限 **`1.0` 秒**；
  - 每局共享时间池 **`5.0` 秒**（单步超出 `1.0s` 的部分从池扣除，不回补；池空后恢复 `1.0s` 硬时限；余额见每步消息 `time_pool`）；
  - 每局累计思考时间硬上限 **`20.0` 秒**（最多 `5000` 步，平均每步应控制在 `< 2ms`！）。
- **判死、犯规替换与零容忍判负**：
  - 超时、崩溃、提前 EOF、输出非法 JSON、单行超 `64 KiB` 或多行抢跑 → 进程立即终止，本局剩余所有步均判犯规。
  - 攻击方犯规 → 该步请求被裁判替换为常量 `0`。
  - 防守方需驱逐时犯规 → 裁判强制驱逐**最新放入缓存**的 key（`insert_order[-1]`）。
  - **正式评分技术判负**：任意犯规、超时或进程异常终止均导致本方该局直接计 `0` 分。
- **工作区红线**：
  - 只能以公开基线（`fifo` / `lru` / `marking` / `rand`）或自身镜像（`--self`）作自测对手，禁止与其他选手提交互测或读取外部路径。
  - 自测结果、回放与日志只写入当前工作区的 `results/`、`replays/`、`logs/`；选手工作区禁止 `--serve` 与 `--unrestricted`。
  - 禁止修改 `engine/engine.py`、`engine/policy.py`、`config/format.json`、`config/workspace.json` 及公开基线。

---

## 4. 编写、自测、可视化复盘与交付

```bash
# 查看公开陪练
python scripts/spar.py --list

# 快速验证双角色对阵单个/全部基线（支持 ../submissions/cache_duel.py 或 agents/submissions/my_agent.py）
python scripts/spar.py ../submissions/cache_duel.py lru --seed-count 2
python scripts/spar.py ../submissions/cache_duel.py --all --seed-count 4

# 单场对抗（支持单向或 --swap 双向、--config-idx 0/1/2、--self 或 --b self 镜像回归）
python scripts/run_match.py --a ../submissions/cache_duel.py --b marking --swap --seeds 11,23
python scripts/run_match.py --a ../submissions/cache_duel.py --self --swap --config-idx 0 --seed-count 2

# 录制回放（含 Belady OPT 逐帧对比），拖拽 replays/*.json 到 viz/player.html 离线复盘
python scripts/record_replay.py --a ../submissions/cache_duel.py --b lru --seed 11 --config 0 --swap
```

生成的回放文件（`replays/replay_*.json`）可直接用浏览器打开 `viz/player.html` 离线拖入查看：并排对比**防守方实时缓存**与 **Belady OPT 离线最优缓存**、滚动访问磁带、累计缺失曲线、实时竞争分曲线与 Key 频次分布，快速定位防守分歧点与攻击弱区。

---

## 5. 六个高频策略与工程坑（省你半天）

1. **忘记 `flush=True` 或忽略 `need_evict=false` 回复**：`stdout` 缓冲卡死会直接耗光 `1s + 5s` 超时判死，哪怕 `hit=true` 或缓存未满也必须每步回 `{}`。
2. **攻击方在全空间 `K` 均匀随机反而送分**：当 `K=4096, C=64` 时，大范围纯随机请求会让 **Belady OPT 也几乎步步缺失**，导致 `OPT / M_def → 1`，攻击分接近 `0`！高攻击分的核心是制造**“OPT 很容易命中、在线防守方却反复踩空”**的访问模式（例如集中在约 `C+1 ~ 2C` 的工作集、结合 `last_hit` 探测对手驱逐策略与周期相位变化）。
3. **纯确定性防守会被 `C+1` 扫穿**：LRU 和 FIFO 面对 `t % (C+1)` 缺失率都是 `100%`（`C=64` 时 `def_score` 低至 `~1/64`）；必须结合**随机化**（如 Marking / 随机淘汰权重）与**在线模式检测**（区分扫描攻击、热点+扫描混合、自适应回踩与局部性工作集）。
4. **防守本地缓存集合与裁判脱节**：如果漏更新命中/装入/驱逐，导致提交了不在缓存内的 `evict`，裁判会强制驱逐**最新放入的 key**，立刻造成连续雪崩缺失。务必确保本地 `cache` 更新与 `engine.py` 逐行等价。
5. **`N=5000, K=4096` 下的性能陷阱**：20 秒总预算摊到 5000 步仅 `4ms/步`（含 JSON I/O）。避免每步扫描全键空间 `O(K)`，把单步复杂度控制在 `O(1)` 或 `O(C)`（`C <= 64`）。
6. **硬编码配置尺寸或忽略随机种子**：正式评测覆盖 `(8, 64, 3000)`、`(32, 256, 5000)`、`(64, 4096, 5000)` 三组配置并每次更换 OS 随机种子。所有窗口/集合规模须由开局消息中的 `C, K, N` 计算，随机数使用 `random.Random(init["seed"])`。

---

## 6. 目录地图

```text
AGENTS.md                 编码 AI 自动读取的参赛工作流
AI_README.md              你正在阅读的完整参赛指南
README.md                 完整规则与正式评分说明
FORMAT.md                 简明公开赛制摘要
handbook/
  contest-rules.md        公开赛制规则
  player-guide.md         stdin/stdout 协议与提交清单
  game-facts.md           配置参数、Belady 公式、基线行为事实卡
engine/
  engine.py               权威裁判与 Belady OPT 计算（禁止修改）
  policy.py               工作区边界守卫（禁止修改）
  replay.py               回放录制、Belady 轨迹与一致性校验
agents/
  starter.py              最小双角色起步模板
  baselines/              4 个公开基线：fifo / lru / marking / rand
  submissions/            题内开发目录（最终请同步至 ../submissions/cache_duel.py）
scripts/
  spar.py                 公开陪练自测
  run_match.py            单场单向/双向对抗
  record_replay.py        回放录制与引擎复算校验
viz/player.html           零联网单文件离线回放播放器
config/format.json        标准配置与计时参数（禁止修改）
config/workspace.json     选手工作区标记（禁止修改）
results/ replays/ logs/   你的本地自测结果、回放与日志
prompts/task.txt          无文件工具时的完整聊天模式题面
```

---

## 7. 交付前自检

- [ ] 单文件纯 Python 标准库，无联网、无外部文件读写
- [ ] `attacker` 与 `defender` 双角色均完整实现，且每步（含握手与 `end=true`）立即 `flush`
- [ ] `spar.py --all --seed-count 4` 零犯规（`faults=0`）、无超时，且大幅超越全部公开基线
- [ ] 在全部 3 组配置（含 `C=64, K=4096, N=5000`）下单局思考时间远低于 20s 预算
- [ ] 已将最终验证版本保存到 `../submissions/cache_duel.py`
