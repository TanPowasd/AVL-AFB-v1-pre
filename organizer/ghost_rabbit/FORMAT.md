# 接口与交互协议规范（FORMAT）· 天翼幽灵兔

## 1. 阶段一：速率声明（对局前仅输出一次）

向 `stdout` 输出一行两个正整数并立即 `flush`：

```text
P Q
```

约束：
- $1 \le Q \le 10^6$，$1 \le P \le 2Q$。
- 评测器自动约分 $g = \gcd(P, Q)$，$P' = P/g, Q' = Q/g$，速率 $q = P'/Q'$。
- 漏桶初始代币 $\text{tokens} = 0$，容量上限 $\text{cap} = 2Q'$。

## 2. 阶段二：逐回合在线交互（最多 $T_{\max} = 5\,000$ 轮）

第 $t$ 轮开始时，评测器先执行补给：$\text{tokens} \leftarrow \min(\text{tokens} + P', 2Q')$。

### 2.1 选手输出（单行，长度 $\le 1024$ 字节，输出后必须 `flush`）

```text
k_t a_1 b_1 ... a_{k_t} b_{k_t} pa pb
```

- $k_t \in \{0, 1, 2\}$，且 $k_t \cdot Q' \le \text{tokens}$；
- 若 $k_t = 0$，该行仅含 3 个整数：`0 pa pb`；
- 所有坐标 $(a_j, b_j)$ 与声呐点 $(p_a, p_b)$ 均须满足：
  - 范围 $[-10^5, 10^5]^2$；
  - 属于蜂窝图点集 $\mathcal{H}_\omega$，即 $(x + y) \bmod 3 \in \{1, 2\}$；
- 本轮全部尝试落子点与历史已固化陷阱 $F_{\le t-1}$ 构成临时集合 $S_t = F_{\le t-1} \cup \{(a_1, b_1), \dots, (a_{k_t}, b_{k_t})\}$，**在幽灵弹开判定之前**强制校验：
  1. 本轮落子互不相同且均不在 $F_{\le t-1}$ 中；
  2. $S_t$ 满足 **Local 3-AP-Free**：不存在 $v \in S_t$ 与 $d \in N_2(0,0) = \{(1,-1),(-1,1),(2,1),(-2,-1),(1,2),(-1,-2)\}$ 使 $v-d, v, v+d \in S_t$（等价于图距离 $\le 6$ 内无等差共线三点 $u+w=2v$）。

### 2.2 评测器结算与响应（4 个整数，行尾换行）

1. 扣除代币 $\text{tokens} \leftarrow \text{tokens} - k_t Q'$；
2. **幽灵免疫弹开**：若存在 $j \in \{1, \dots, k_t\}$ 使 $(a_j, b_j) = r_{t-1}$，该枚陷阱弹开失效（`bounce_idx = j`，不进入 $F_{\le t}$，不计入 $|F_{\le t}| \le 4000$，不参与后续 3-AP 校验，但仍消耗 $Q'$ 代币）；否则 `bounce_idx = 0`。其余落子永久固化至 $F_{\le t}$；
3. 检查累计固化陷阱总数 $|F_{\le t}| \le 4\,000$，超出直接判 `WA`；
4. 计算 $r_{t-1}$ 合法移动集 $M_t$：
   - 若 $r_{t-1} \in \mathcal{P}_\omega$，置 `flash = 1`，$M_t = (N_1(r_{t-1}) \cup N_2(r_{t-1})) \setminus F_{\le t}$；
   - 否则置 `flash = 0`，$M_t = N_1(r_{t-1}) \setminus F_{\le t}$；
5. **捕获或移动**：
   - 若 $M_t = \emptyset$：输出 `1 0 0 0\n` 并刷新，对局以 `AC` 结束。选手必须立即退出（返回码 `0`）且不得输出多余内容；
   - 若 $M_t \ne \emptyset$：幽灵兔选择 $r_t \in M_t$，输出 `0 dist flash bounce_idx\n` 并刷新，其中 $\text{dist} = \operatorname{dist}_{\mathcal{H}_\omega}((p_a, p_b), r_t)$。

## 3. 判定状态码

- `AC`：在 $T \le 5\,000$ 轮内触发 `status = 1` 且以退出码 `0` 干净退出
- `WA`：速率越界、代币不足、坐标越界/不在 $\mathcal{H}_\omega$、重复落子、违反 Local 3-AP-Free、累计固化陷阱 $> 4\,000$、或 5000 轮未能捕获
- `PE`：输出格式错误、意外 EOF、或在收到 `1 0 0 0` 后继续输出非空白内容
- `OLE`：单行输出超过 1024 字节
- `TLE`：求解器纯 CPU / 活跃时间超过 5.0 s
- `MLE`：求解器峰值内存超过 512 MiB
- `RE` / `CE`：运行期异常退出或编译失败

## 4. 双轨计分规则速查

设该局为 `AC`，选手声明的最简分数速率为 $q = P'/Q'$（任意非 `AC` 局直接计 `0` 分）：

- **单局不封顶原分 $\operatorname{RawScore}(q)$**：
  - $q \ge 2$：`0`；
  - $\frac{1}{2} < q < 2$：$\operatorname{RawScore}(q) = 100 \cdot \dfrac{4Q' - 2P'}{2P' - Q'}$；
  - $q \le \frac{1}{2}$：`Singularity`（$+\infty$）。
- **单局 0–100 折算分 $\operatorname{NormScore}(q)$**（等价于 $100 \cdot \dfrac{r}{r + 200}$）：
  - $q \ge 2$：`0`；
  - $\frac{1}{2} < q < 2$：$\operatorname{NormScore}(q) = 100 \cdot \dfrac{2Q' - P'}{P' + Q'}$；
  - $q \le \frac{1}{2}$：`100`。
- **正式多局汇总（`32` 局 = `4` 类隐藏对手家族 × `8` 个随机种子）**：
  - **套题 0–100 总分（`score`）**：全部 `32` 局 `NormScore` 的等权算术平均；
  - **单题不封顶原分（`raw_score` / `singularity`）**：仅在全部 `32` 局均为 `AC` 时取最劣（最小）单局 `RawScore`（全通且全员 $q \le \frac{1}{2}$ 时标记 `singularity = true`），存在任意未 `AC` 局则记 `0.00`。

## 5. 离线回放 JSON 结构（`ghost_rabbit_replay_bundle/1`）

由 `scripts/record_replay.py` 生成，可拖入 `viz/player.html` 回放，包含：
- `summary`：`ac_count`、`total_cases`、`score`、`raw_score`、`singularity`、`by_family`
- `matches[]`：每局 `strategy`、`seed`、`verdict`、`P_red`、`Q_red`、`turns`、`traps_placed`、`witness_path` 与逐回合 `frames[]`（`t`, `tok0`, `tok1`, `att`, `land`, `sonar`, `st`, `dist`, `fl`, `bn`, `r0`, `r1`, `bsz`, `bel`）
