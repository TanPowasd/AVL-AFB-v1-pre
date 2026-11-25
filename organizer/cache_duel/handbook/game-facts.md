# 游戏事实卡（Cache Duel，全部核对自 `engine/engine.py`、`config/format.json` 与公开基线源码）

## 1. 官方三组评测配置（`config/format.json`）

| 配置索引 | 缓存容量 `C` | 键空间 `K` | 总步数 `N` | 空间比 `K / C` |
|---:|---:|---:|---:|---:|
| `0` | `8` | `64` | `3000` | `8` |
| `1` | `32` | `256` | `5000` | `8` |
| `2` | `64` | `4096` | `5000` | `64` |

## 2. 引擎与计时常量（`engine/engine.py`）

- `STEP_TIMEOUT = 1.0` 秒：单步基础时限。
- `TIME_POOL = 5.0` 秒：每局共享时间池，每次 `dt > 1.0` 扣除 `dt - 1.0`，不回补。
- `TOTAL_BUDGET = 20.0` 秒：每个 bot 单局累计思考时间上限；每次询问的实际超时阈值为 `min(STEP_TIMEOUT + pool, max(0.0, TOTAL_BUDGET - used))`。
- 角色随机种子派生：
  `role_seed(seed, role) = int(sha256(f"{seed}|{role}").hexdigest()[:8], 16)`
  攻击方与防守方拿到的 `seed` 相互独立，无法互相反推。
- 合法 key 类型判定：`is_key(v)` 要求 `isinstance(v, int) and not isinstance(v, bool)`。
- 犯规替代与技术判负：
  - 攻击方非法输出 / 死亡后：该步 key 替换为常量 `0`。
  - 防守方在 `need_evict == True` 时非法输出 / 死亡后：驱逐 `insert_order[-1]`（即当前缓存中**最晚装入**的 key；注意 `insert_order` 按插入顺序维护，命中不改变其位置）。
  - 正式评分（`scoring_v2`）与 `spar.py` 对犯规/超时/崩溃实行零容忍技术判负：本方该局一旦出现 `faults > 0` 或 `dead == True`，该局直接计 `0` 分。

## 3. 离线最优（Belady / MIN）与计分公式

给定实际请求序列 `R = (r_0, ..., r_{N-1})` 与容量 `C`：
- 对每个位置 `i`，定义下一次出现位置 `nxt[i]`；若 `r_i` 之后不再出现，引擎赋以唯一大值 `n + i`（平手按索引更大者优先驱逐）。
- 每次缺失且缓存已满时，Belady 算法驱逐当前缓存中 `nxt` 最大的 key，得到最小可能缺失次数 `OPT = belady_misses(R, C)`。
- 记防守方实际缺失数为 `M_def`：
  - 若 `M_def == 0`，`def_score = 1.0`；否则 `def_score = OPT / M_def ∈ (0, 1]`。
  - 攻击方该局得分：`att_score = 1 - def_score ∈ [0, 1)`。
- 重要推论：
  - 攻击方想拿高分，必须拉大 `M_def` 与 `OPT` 的比值（降低 `OPT / M_def`），而不是单纯提高 `M_def`。若攻击序列毫无规律且覆盖远大于 `C` 的键空间（如在 `K=4096` 上均匀随机），`OPT` 也会接近 `N`，导致 `def_score → 1`、`att_score → 0`。
  - 经典离线-在线竞争比理论界：确定性缓存算法的最坏竞争比下界为 `C`（即 `def_score` 可低至 `1/C`），随机化算法（如 Randomized Marking）对不知情对手（oblivious adversary）可达 `O(H_C) = O(log C)` 竞争比；但本题中攻击方每步都能收到 `last_hit`（自适应对手），因此兼具随机化与自适应检测/反侦察至关重要。

## 4. 四个公开基线精确行为（`agents/baselines/`）

| 短名 | 文件 | 防守策略 | 攻击策略 |
|---|---|---|---|
| `fifo` | `agents/baselines/fifo.py` | 先进先出队列（FIFO），驱逐最早装入的 key | 80% 概率均匀随机访问 `[0, max(1, C//2))` 热键，20% 顺序扫描 `[C//2, K)` 冷键 |
| `lru` | `agents/baselines/lru.py` | 最近最少使用（LRU，`OrderedDict`） | 固定循环扫描 `t % (C + 1)` |
| `marking` | `agents/baselines/marking.py` | 随机化 Marking：命中/装入打标记，驱逐时从无标记集合均匀随机选出；若全有标记则清空重来 | 在 `0..C` 共 `C+1` 个 key 上做随机排列扫描，每轮打乱一次 |
| `rand` | `agents/baselines/rand.py` | 缓存内等概率随机驱逐 | 在 `[0, min(K, 2*C))` 上均匀随机 |

## 5. 正式百分制评测口径（`scoring_v2`）

- 固定参考对手池：`7` 个固定对手（不随参赛提交改变），每份提交在 `3` 组配置 × `64` 个 OS 安全随机种子（`os_csprng`）上分别执攻、执守，共 `7 * 3 * 64 * 2 = 2688` 局。
- 等权聚合：
  - `防守分 = 100 * mean(def_score)`
  - `攻击分 = 100 * mean(1 - 对手 def_score)`
  - `总分 = 0.5 * 防守分 + 0.5 * 攻击分 ∈ [0, 100]`
- 本地 `scripts/spar.py` 与 `scripts/run_match.py` 采用完全一致的单局计分公式，仅对手池替换为公开基线并标记 `official = false`。
