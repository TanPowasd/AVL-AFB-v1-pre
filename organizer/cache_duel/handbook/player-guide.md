# 选手手册（Cache Duel）

你的任务：编写一个单文件 Python 3.10+ 程序，使其既能作为**防守方（defender）**最大化相对离线最优（Belady/MIN）的防守比 `OPT / M_def`，又能作为**攻击方（attacker）**最大化对对手造成的攻击分 `1 - OPT / M_def`。

---

## 1. 提交规范与运行环境

- **最终提交路径**：`../submissions/cache_duel.py`（题内开发也可暂存于 `agents/submissions/my_agent.py`）。
- **依赖限制**：仅允许使用 Python 标准库（`json`, `sys`, `random`, `collections`, `math`, `heapq`, `bisect` 等）；严禁联网、严禁读写外部文件或探测进程/环境。
- **子进程隔离参数**：
  - 启动命令：`python -u <你的脚本绝对路径>`
  - 工作目录：提交文件所在目录
  - 环境变量：`PYTHONHASHSEED=0`
  - `stderr`：重定向至丢弃（不要用 `print(..., file=sys.stderr)` 调试，也绝不要向 `stdout` 打印除协议 JSON 外的任何文本！）

---

## 2. `stdin / stdout` 逐行 JSON 通信协议

每一条消息均为**单行合法 UTF-8 JSON**，以换行符 `\n` 结尾，每次回复必须立即刷新缓冲（`flush=True` 或 `sys.stdout.flush()`）。

### 阶段 1：初始化握手（启动后第 1 行）
- **裁判发送**：
  ```json
  {"role": "attacker", "cache_size": 8, "universe": 64, "length": 3000, "seed": 2847192014}
  ```
  - `role`：`"attacker"` 或 `"defender"`
  - `cache_size`（`C`）：缓存容量（正整数）
  - `universe`（`K`）：合法 key 范围 `0 .. K - 1`（`K > C`）
  - `length`（`N`）：本局总步数 `t = 0 .. N - 1`
  - `seed`：由场景种子按角色哈希派生，攻防双方不同；请用 `random.Random(init["seed"])` 保证可复现
- **Bot 回复**：任意合法 JSON 对象（如 `{}`）并 `flush`。

### 阶段 2：逐步交互（`t = 0, 1, ..., N - 1`）

#### 若 `role == "attacker"`
- **裁判发送**：
  ```json
  {"t": 0, "last_hit": null, "time_pool": 5.0}
  ```
  - `t = 0` 时 `last_hit` 为 `null`；`t > 0` 时为上一步请求是否命中的布尔值 `true` / `false`。
  - 攻击方**看不到**防守方当前缓存集合，也看不到被驱逐的 key。
- **Bot 回复**：
  ```json
  {"key": 7}
  ```
  - `key` 必须是整数（不能是 `bool` 或 `float`），且 `0 <= key < K`。

#### 若 `role == "defender"`
- **裁判发送**：
  ```json
  {"t": 12, "key": 7, "hit": false, "need_evict": true, "time_pool": 4.982}
  ```
  - `key`：本步攻击方实际请求的 key（已包含攻击方犯规时的替代结果）。
  - `hit`：`key` 当前是否已在缓存集合中。
  - `need_evict`：当且仅当 `hit == false` 且当前缓存已满（`len(cache) == C`）时为 `true`。
- **Bot 回复**：
  - 当 `need_evict == true`：`{"evict": e}`，`e` 必须是当前缓存集合内的整数（非 `bool`）。裁判随后移除 `e` 并将 `key` 加入缓存。
  - 当 `need_evict == false`：回复 `{}`（**不可省略**！裁判仍会等待这一行回复）。

### 阶段 3：对局结束
- **裁判发送**：`{"t": N, "end": true}`
- **Bot 回复**：回复 `{}` 并退出循环结束进程。

---

## 3. 时间限制与犯规处罚

| 指标 | 官方数值 | 说明 |
|---|---|---|
| 单步基础时限 | `1.0` 秒 | 握手、每一步、结束消息均计入 |
| 时间池 | `5.0` 秒 | 整局共享、不回补；单步超过 `1.0s` 的超出部分从池扣除；池空后恢复 `1.0s` 硬时限 |
| 单局总预算 | `20.0` 秒 | 累计思考时间达到上限立刻判超时终止 |

- **超时 / 崩溃 / 提前 EOF / 输出非合法 JSON / 单行超 64 KiB / 多行抢跑**：子进程被立刻终止，本局剩余所有步均判犯规。
- **攻击方犯规**：该步请求替换为常量 `0`。
- **防守方犯规**（仅在 `need_evict == true` 时判定）：裁判强制驱逐**最新放入缓存**的那个 key（`insert_order[-1]`）。
- **正式评分技术判负**：在正式评分与 `spar.py` 中，本方该局一旦发生任何犯规、超时或进程死亡，该局直接计 `0` 分；务必确保全配置 `faults == 0`。

---

## 4. 本地开发、自测与回放工具

```bash
# 1. 列出全部公开陪练
python scripts/spar.py --list

# 2. 对阵单个或全部公开基线（默认随机种子，可加 --seeds 固定重放）
python scripts/spar.py ../submissions/cache_duel.py lru --seed-count 2
python scripts/spar.py ../submissions/cache_duel.py --all --seed-count 4

# 3. 单场对抗（支持 --config-idx 0/1/2、--swap 双向、--self 或 --b self 镜像回归）
python scripts/run_match.py --a ../submissions/cache_duel.py --b marking --swap --seeds 11,23
python scripts/run_match.py --a ../submissions/cache_duel.py --self --swap --config-idx 0 --seed-count 2

# 4. 录制回放并用浏览器打开 viz/player.html 离线查看 Belady OPT 对比
python scripts/record_replay.py --a ../submissions/cache_duel.py --b lru --seed 11 --config 0 --swap
```

---

## 5. 提交前自检清单

1. 仅使用 Python 标准库，不读外部文件、不联网、不向 `stdout` 输出调试日志。
2. 握手、每步（含 `hit=true` 与未满装入）和 `end=true` 均输出单行 JSON 并 `flush=True`。
3. 返回的 `key` 与 `evict` 均为纯 `int`（`type(x) is int`），无 `bool` / `float` / 越界 / 不在缓存内的键。
4. 在 `C=8, 32, 64` 全部 3 组配置下对阵 `fifo / lru / marking / rand` 均为 `faults == 0`，且单局用时远低于 `20s`。
5. 同种子两次运行结果一致（用 `init["seed"]` 初始化局部 `random.Random`）。
6. 最终代码已写入 `../submissions/cache_duel.py`。
