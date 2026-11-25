# 选手手册：通信协议、观测、动作与自检

你的任务是编写一个 Python 3.10+ 策略包（打包为 `.zip`，根目录含 `main.py`，可拆分多模块并携带只读数据文件），通过 `stdin / stdout` 逐行 JSON 协议控制前沿 AI 实验室运行 `26280` tick（3 年），兼顾**单人赛最高真实能力 `C`**与**对抗赛终局创始人股权估值 `Value`**。

---

## 1. 子进程生命周期与打包方式

1. 推荐在 `agents/submissions/<name>/` 下组织代码（入口 `main.py`，辅以任意子模块和只读数据文件，参考 `agents/template/`），然后打包并校验：
   ```bash
   python scripts/make_submission.py agents/submissions/<name> --out ../submissions/ai_training.zip
   ```
2. 裁判将 `.zip` 安全解压至临时包目录并启动 `python -u engine/pkg_entry.py <pkg_dir>`（将 `<pkg_dir>` 置入 `sys.path[0]`、切换 `cwd=<pkg_dir>`、`PYTHONHASHSEED=0`、`PYTHONUTF8=1`、`stderr` 丢弃，再执行 `main.py`）。单文件 `.py` 亦兼容用于本地调试。
3. 从 `stdin` 读取第 1 行 `init` JSON，在 `10.0s` 内向 `stdout` 输出 `{}` 并 `flush`。
4. 随后循环读取每 tick 的观测 JSON（`t = 0 .. ticks - 1`），每次输出一行紧凑动作 JSON（`<= 1 MiB`）并 `flush`（第 `t` tick 输出的动作在第 `t + 1` tick 生效；`t = 0` 前双方均为空动作 `{}`）。
5. 收到 `{"type": "end", "t": ...}` 后在 `1.0s` 内输出 `{}` 并正常退出。

起步多文件模板见 `agents/template/`，最小单文件骨架见 `agents/starter.py`。注意：程序内部任何未捕获异常、输出超长行（`> 1 MiB`）或无节制刷写 `stdout`（未消费行 `> 64`）都会导致进程被终止并判为 `dead`，务必在主循环外层做好异常兜底。

---

## 2. `init` 握手消息字段

```json
{
  "type": "init",
  "seat": 0,
  "ticks": 26280,
  "rules": {"DAY": 24, "WEEK": 168, "KC": 300.0, "SK": 25.0, "MTBF": 50000.0, "API_MIN_C": 0.15, "U_PUB": 6.0, "...": "..."},
  "gpus": [{"model": "G1", "power": 1.0, "kw": 0.7, "launch_q": 0, "supply_q": 8192, "price": 0.05}],
  "regions": [{"id": 0, "price": 0.0006, "cap_mw": 12.0, "build_weeks": 12, "build_cost": 25.0, "reg": 1.1}],
  "domains": [{"name": "web", "public_b": 1824.7, "q_hint": 1.42}],
  "pools": ["web", "code", "math", "books", "science", "dialog", "multiling", "synth", "distill"],
  "real": ["web", "code", "math", "books", "science", "dialog", "multiling"],
  "techs": {"algo_eff_1": {"cost": 20000, "pre": null}},
  "start": {"cash": 200, "gpus": {"G1": 512}, "site": {"region": 0, "mw": 2}, "staff": [{"id": 0, "ab": 2, "sal": 0.00016}]},
  "npcs": ["N1", "N2"],
  "models": [{"id": "N1-1", "owner": "N1", "pub": 0.412, "price": 9.12, "N": null}]
}
```

- `seat = 0` 对应实验室标签 `"A"`（发布模型 `A1, A2, ...`）；`seat = 1` 对应 `"B"`（`B1, B2, ...`）。
- `rules` 字典给出全部公开规则常量，推荐直接读取而非硬编码。

---

## 3. 每 tick 观测 `obs` 字段

### 3.1 每 tick 必含字段
| 字段 | 类型与含义 |
|---|---|
| `t` | `int`，当前 tick（`0 .. 26279`） |
| `cash` | `float`，当前现金（M，可为负；连续 `720` tick `< 0` 破产） |
| `eq` | `float`，创始人当前股权比例（初始 `1.0`） |
| `rep` | `float`，声誉（范围 `[-3.0, 0.0]`） |
| `bk` | `bool`，是否已破产 |
| `gpu` | `{model: [usable_own, broken, inv_uninstalled, rented, used]}`（全为 0 的型号不列出） |
| `runs` | `[[name, status, trained_b, target_b, tok_per_tick, eff, last_ckpt_id, cards], ...]` |
| `api` | `[demand_b, served_b, revenue_m]`，本 tick 需求、送达量与 API 收入 |
| `missed` | `int`，累计超时与坏 JSON 次数 |
| `time_pool` | `float`，剩余共享时间池秒数（初始 `120.0`） |

`runs` 状态取值为 `running` / `paused` / `review` / `diverged` / `done` / `stopped`；`done` 与 `stopped` 任务仅在产生关联事件的那个 tick 出现一次。

### 3.2 按需附带字段（用 `obs.get(...)` 读取）
- `ev`：本 tick 公共事件与己方私有事件列表：
  - 公共：`release`、`open_source`、`export_ctrl`、`curtail`、`raise`、`audit_fail`、`bankrupt`（仅记入回放/私有通知）
  - 私有：`done`（含 `run, ckpt, loss`）、`failure`、`preempt`（训练任务因抢占回退）、`preempted`（`spot` 租约被抢占结束）、`spike`、`diverged`、`slow_node`、`review`（含 `until`）、`review_done`、`delivered`、`expanded`、`site_ready`、`tech`（`filter:*` 附带真实 `q` 与减半后 `c`）、`quit`、`poached`、`lawsuit`（`kind: "distill"|"copyright"`）、`bankrupt`
- `res`：本 tick 执行动作的回执列表（`{"act": ..., "ok": true/false, ...}`），其中 `rent` 返回租约 `id`，`release` 返回模型 `id` 与上线时刻 `live`，`poach` 成功返回 `signing_bonus`。

### 3.3 每日附带字段（`t % 24 == 23`）
- `loss`：`{run_name: [L_0, ..., L_6]}`，所有处于 `running`/`paused`/`diverged` 且已训量 `> 0` 任务的 7 个真实域带噪 loss（独立乘 `1 + 0.01 * N(0, 1)`）。
- `rent`：`{model: ondemand_price}`，已上市型号的当前按需租价（M / 卡·tick）。
- `elec`：`[p0, p1, p2, p3, p4]`，5 个地区当前电价（M / MWh）。
- `pts`：`{tech: accumulated_pts}`，尚未完成但已有进度的科技累计研究点数。

### 3.4 每周附带字段（`t % 168 == 0`）
`obs["wk"]` 包含：
- `week`、`heat`（AI 热度）、`demand`（当前全市场需求 B token/tick）；
- `auction`（上周拍卖出清价与中标者）、`deal_results`（上周数据招标结果）、`hires`（上周招聘结果）；
- `offer`（本周 GPU 拍卖 `{model: {lots, reserve, early}}`）；
- `deals`（本周独家数据合同 `[{id, domain, tokens_b, q_hint, reserve}]`）；
- `talent`（本周人才池 `[{id, lo, hi, ask}]`）；
- `shares`（各方 API 市场份额）；
- `models`（全市场未下架模型 `[{id, owner, pub, price, N}]`）；
- `staff`（`{opp, N1, N2}` 各方人数）；
- `my`：`staff`（`[[id, ab, sal], ...]`）、`sites`（`[{id, region, mw, ready, pend_mw, cards}]`）、`data`（各池去重量 `U`）、`tech`（已解锁列表）、`share`、`drop`（上周丢单率）、`deliv`（在途卡 `[[arrive_t, model, n], ...]`）。

---

## 4. 动作字典与执行顺序

引擎按下列**固定顺序**逐项执行动作（全部键可选；列表型动作单 tick 上限 `64` 条，`fire` 上限 `300` 条；任一数字非法导致整步作废，类型异常导致当前及后续项作废）：

1. `bid_gpu`: `[{"model": "G2", "lots": 2, "price": 0.14}]` —— 同型号覆盖旧报价，`lots == 0` 撤单，`lots < 0` 或 `price <= 0` 记非法；周一结算前按 `cash / (price * 256)` 截断可参与批数，结算后清空。
2. `rent`: `[{"model": "G1", "n": 256, "kind": "ondemand"|"spot", "ticks": 168}]` —— 超出池余量时截断。
3. `end_rent`: `[rent_id, ...]` —— 提前结束租约。
4. `sell_gpu`: `[{"model": "G1", "n": 256}]` —— 优先卖库存再卖已装机好卡，`48` tick 后回款；单卡回款受市场热度折价与剩余硬件账面双重封顶。
5. `build_site`: `[{"region": 3}]` —— 每方最多 `8` 座，同一地区可重复建设。
6. `expand_site`: `[{"site": 0, "mw": 4.0}]` —— `2 M/MW`，`8` 周生效，单座上限 `cap_mw`。
7. `ppa`: `[{"site": 0, "mwh": 20000}]` —— 锁当前电价 `×0.8` 持续 `8760` tick，照付不议，单座机房同时生效上限 `64` 份。
8. `hire`: `[{"cand": 17, "salary": 0.0003}]` —— 单位 M/tick，须 `> 0` 且不低于 `ask`，周一密封最高薪得人。
9. `poach`: `[{"target": "opp"|"N1"|"N2", "idx": 0, "salary": 0.0006}]` —— 每周最多处理前 `5` 条，`salary <= 0` 记非法；挖对手每周最多 `2` 次且成功后冷却 `2` 周；成功挖走须现金支付签约金（对手 `336×` / NPC `168×`）；受 `MAX_STAFF = 300` 约束。
10. `fire`: `[staff_id, ...]` —— 解雇己方研究员，每人立即扣除 `168 * sal` 遣散费。
11. `research`: `[{"tech": "algo_eff_1", "share": 0.6}, ...]` —— 持续生效、整体覆盖，自动在可研项间归一化。
12. `bid_data`: `[{"deal": 9, "price": 12.0}]` —— 周一密封首价开标，中标独享 1 年。
13. `synth`: `[{"tokens_b": 20.0}]` —— 需解锁 `synth` 且有已上线模型，`24` tick 后入库。
14. `distill`: `[{"model": "N1-1", "tokens_b": 15.0}]` —— 立即进入 `distill` 池，不可蒸馏己方或已下架模型。
15. `run`: `[{"name": "r1", "params_b": 20, "tokens_b": 400, "gpus": {...}, "lr": ..., "batch": 512, "sched": "cosine"|"wsd"|"const", "mix": {...}, "ckpt_every": 24, "paused": false}]` —— 最多 `12` 个未完成任务，整局上限 `400` 个，不可重名（名截断至 32 字符）。
16. `run_ctl`: `[{"name": "r1", "op": "...", "arg": ...}]` —— 支持 `pause`、`resume`、`stop`、`rollback`（arg 可省=最新存档）、`set_lr`、`set_mix`、`ckpt`（立即存档，并在下次训练推进时扣除 `20%` 吞吐开销）、`diagnose`（清除慢节点，停 1 tick）、`set_gpus`、`set_tokens`（仅非 `done`/`stopped` 任务，须 `trained_b < arg <= 1e6`，`wsd` 退火锁定后禁止上调，`cosine` 接近完成 `>=98%` 时禁止上调超 5%）、`set_batch`、`set_ckpt`。
17. `audit_request`: `[{"run": "r1"}]` —— 提前申请监管审核，审核期间不占 GPU。
18. `release`: `[{"run": "r1", "ckpt": 16, "price": 10.0}]` —— `24` tick 后正式上线；`ckpt` 省略取最新存档。
19. `price`: `[{"model": "A1", "value": 12.0}]` —— 截断至 `[0.5, 100]`，每个模型每天限改 1 次；无能力提升提价超 `1.5×` 将即时流失份额。
20. `delist`: `["A1"]` —— 下架己方已上线模型（注意 `online_ticks < 730` 时下架将失去 `best_c()` 资格！）。
21. `serve`: `[{"gpus": {"G1": 256}}]` —— 显式留卡服务（持续生效，与自动分配互斥；传 `[]` 或 `null` 恢复自动分配）。
22. `raise`: `[{"amount": 300.0}]` —— 每季度（`2190` tick）限 `1` 次，投前估值与真实 `value()` 对齐且不超过 `V_true`，超投前估值时截断至 `V_pre`。

---

## 5. 每 tick 结算顺序（9 步）

1. 全局事件（NPC 发布、开源、出口管制预告、限电、满 24 tick 玩家模型上线、在线时长 `+1`）；
2. 执行上一 tick 提交的动作（双方执行顺序按 `(t + seed) % 2` 交替）；
3. **每周一（`t % 168 == 0`）**：GPU 拍卖（资金预检后出清）→ 数据招标 → 招聘 → 挖角 → API 份额迁移 → 刷新本周供给/合同/人才 → 独家数据到期公开 → 蒸馏诉讼（覆盖本周实际训练过或仍活跃任务，双座位对称随机）；
4. 到货与基建完工（中标卡到货、机房扩容/新建完工、坏卡修复、卖卡回款、合成入库、自动装机、到期租约清理）；
5. 显式 `serve` 预留卡 → 云租 `spot` 抢占 → 累加研究点 → 按创建序给在跑任务分卡 → 抢占中断 → 推进训练（故障、重启、慢节点、吞吐、审核触发、存档、尖峰/发散、完成）；
6. 价格/效用跳水即时份额流失判定 → API 请求服务与收入结算（未设 `serve` 时用训练剩余卡从最旧型号自动服务）；
7. 扣工资、云租费、电费，记录毛利滑动窗口与破产计数（破产时同步清空 `pending_models`）；
8. **每月（`t % 730 == 0` 且 `t > 0`）**：污染独立复测、核心研究员离职抽签、版权诉讼；
9. 生成并发送 `obs`。

---

## 6. 提交前自检清单

1. **压缩包合规**：已通过 `python scripts/make_submission.py <dir> --out ../submissions/ai_training.zip` 打包与 24 tick 冒烟测试；包内数据用 `Path(__file__).resolve().parent` 加载。
2. **协议健壮性**：任何分支返回合法 `dict`，所有数值均为有限浮点数或整数且 `abs(x) <= 1e15`；捕获异常不崩溃；单行输出远小于 `1 MiB`。
3. **零违规动作**：运行 `spar.py` 和 `record_replay.py` 后检查 `faults` 中 `timeouts == 0, bad_json == 0, dead == False, illegal == 0`。
4. **双赛制兼容**：未对对手名或种子硬编码；既能在单人赛推高 `C`，也能在对抗赛优化 `equity * V`（含末季 API 毛利）。
5. **终局时点安全**：最后一代旗舰模型最晚于 **`t = 26183`** 观测后提交 `release`（保证上线后在线满 `72` tick），且不轻易下架在线未满 `730` tick 的最高能力模型。
6. **性能余量**：单步平均耗时远小于 `10ms`，完整 26280 tick 总思考时间控制在数秒内，远离 `120s` 时间池与 `2400s` 总预算。
