# 天翼幽灵兔：四重奇点追截战（The Winged Ghost Rabbit / `ghost_rabbit`）

> 在无限艾森斯坦蜂窝图上，以尽可能低的陷阱补给速率 $q = P/Q$、借助声呐测距与素数跃迁闪光，在局部无三连共线的几何约束下截死拥有 $p=2$ 素数天使翼的隐藏幽灵兔。

---

## 一、背景与任务概述

你需要在无限二维蜂窝图 $\mathcal{H}_\omega$ 上与一只隐藏位置的**天翼幽灵兔**展开实时在线交互追截：

- **提交形式**：单文件 C++20 在线交互程序，统一交卷路径 `../submissions/ghost_rabbit.cpp`（由 `g++ -std=gnu++20 -O2 -pipe` 编译，含标准 `main`，单线程，无网络）。
- **四重核心机制耦合**：
  1. **不完全信息声呐追逃**：兔子位置全程隐藏（仅已知初始位置 $r_0 = (1, 0)$），你每轮可向全图任意合法格点发射一枚无成本声呐，获得兔子移动后到声呐点的精确蜂窝图距离；
  2. **艾森斯坦素数天使翼**：当兔子起跳点属于艾森斯坦素数洞穴集合 $\mathcal{P}_\omega$ 时，它可展开 $p=2$ 天使翼直接跨越距离 $\le 2$（无视中间障碍），同时触发 `flash = 1` 跃迁闪光；
  3. **幽灵免疫弹开**：若你本轮将陷阱直接放在兔子当前所处位置 $r_{t-1}$，该枚陷阱会被弹开失效（仍消耗代币但不固化），并向你暴露 `bounce_idx`；
  4. **局部等距无三连共线（Local 3-AP-Free）**：全部已固化陷阱与本轮落子不得在图距离 $\le 6$ 范围内形成三点等差共线（禁止 3 格直线高墙），迫使你在交错锯齿几何与防“3-AP 免死区”之间精妙权衡。

---

## 二、精确数学规则

### 2.1 艾森斯坦蜂窝图 $\mathcal{H}_\omega$ 与 $O(1)$ 距离公式

定义蜂窝图点集为所有模 3 非零的艾森斯坦整数格点：

$$V(\mathcal{H}_\omega) = \{(a, b) \in \mathbb{Z}^2 \mid (a + b) \bmod 3 \in \{1, 2\}\}$$

其中 $(x \bmod 3) \in \{0, 1, 2\}$ 取非负最小余数。$\mathcal{H}_\omega$ 为 3-正则无限二分图，邻域定义如下：

- **1-步邻域 $N_1(a, b)$**（图距离恰好为 1 的 3 个邻点）：
  - 若 $(a + b) \bmod 3 = 1$：$N_1(a, b) = \{(a+1, b),\; (a, b+1),\; (a-1, b-1)\}$；
  - 若 $(a + b) \bmod 3 = 2$：$N_1(a, b) = \{(a-1, b),\; (a, b-1),\; (a+1, b+1)\}$。
- **2-步邻域 $N_2(a, b)$**（图距离恰好为 2 的 6 个同部邻点）：
  $$N_2(a, b) = \{(a, b) + d \mid d \in N_2(0, 0)\}$$
  其中六个 2-步基向量集合为：
  $$N_2(0, 0) = \{(1, -1),\; (-1, 1),\; (2, 1),\; (-2, -1),\; (1, 2),\; (-1, -2)\}$$
- **$O(1)$ 精确三维等距嵌入与图距离**：
  令 $\delta = (a + b) \bmod 3 \in \{1, 2\}$，定义三维坐标映射 $\Phi: V(\mathcal{H}_\omega) \to \mathbb{Z}^3$：
  $$\Phi(a, b) = (c_1, c_2, c_3) = \left(\frac{2a - b + \delta}{3},\; \frac{2b - a + \delta}{3},\; \frac{-a - b + \delta}{3}\right)$$
  恒满足 $c_1 + c_2 + c_3 = \delta$，逆映射为 $(a, b) = (c_1 - c_3,\; c_2 - c_3)$。任意两点 $u, v \in V(\mathcal{H}_\omega)$ 的精确蜂窝最短路距离为：
  $$\operatorname{dist}_{\mathcal{H}_\omega}(u, v) = |c_1(u) - c_1(v)| + |c_2(u) - c_2(v)| + |c_3(u) - c_3(v)|$$

### 2.2 艾森斯坦素数洞穴集合 $\mathcal{P}_\omega$

记艾森斯坦整数环 $\mathbb{Z}[\omega]$（$\omega = e^{2\pi i / 3}$）中的元素 $a + b\omega$ 对应格点 $(a, b)$，其代数范数为 $N(a, b) = a^2 - ab + b^2$。点 $(a, b) \in V(\mathcal{H}_\omega)$ 属于素数洞穴集合 $\mathcal{P}_\omega$，**当且仅当**满足以下两条之一：

1. **惰性素数（落在六条相伴坐标轴上）**：$a = 0$、$b = 0$ 或 $a = b$，且 $r = \max(|a|, |b|)$ 为满足 $r \equiv 2 \pmod 3$ 的有理素数（如 $(2,0), (2,2), (0,2), (-2,0), (-2,-2), (0,-2), (5,0), \dots$）；
2. **分裂素数（其余格点）**：$a \ne 0,\; b \ne 0,\; a \ne b$，且 $N(a, b) = a^2 - ab + b^2$ 为满足 $p \equiv 1 \pmod 3$ 的有理素数（如 $(3,1)$ 范数为 $7$，$(3,2)$ 范数为 $7$，$(4,1)$ 范数为 $13$ 等）。

初始位置 $r_0 = (1, 0)$ 的 $r = 1$ 不是素数，因此 $r_0 \notin \mathcal{P}_\omega$。

### 2.3 精确有理漏桶（Exact Rational Leaky Bucket）

对局开始前，选手自报两个正整数 $P, Q$，满足 $1 \le Q \le 10^6$ 且 $1 \le P \le 2Q$。评测器将其化为最简分数 $P' = P / \gcd(P, Q)$、$Q' = Q / \gcd(P, Q)$，对应速率 $q = P'/Q' \in (0, 2]$。

- 初始代币 $\text{tokens} = 0$，漏桶上限 $\text{cap} = 2Q'$。
- 第 $t$ 轮开始时，先补给：$\text{tokens} \leftarrow \min(\text{tokens} + P',\; 2Q')$。
- 选手本轮可尝试放置 $k_t \in \{0, 1, 2\}$ 枚陷阱，须满足 $k_t \cdot Q' \le \text{tokens}$；结算后扣除 $\text{tokens} \leftarrow \text{tokens} - k_t Q'$。

### 2.4 局部等距无三连共线约束（Local 3-AP-Free）与公开状态优先校验

记 $F_{\le t-1}$ 为前 $t-1$ 轮累计已固化的陷阱集合，$A_t = \{(a_1, b_1), \dots, (a_{k_t}, b_{k_t})\}$ 为第 $t$ 轮尝试落子集合，临时并集 $S_t = F_{\le t-1} \cup A_t$。

**在判定幽灵弹开之前**，评测器仅基于公开信息对本轮输出做严格合法性校验（任何一项不通过即直接判 `WA`）：
1. $k_t \in \{0, 1, 2\}$ 且 $k_t Q' \le \text{tokens}$；
2. $A_t$ 中所有坐标与声呐点 $(p_a, p_b)$ 均位于 $[-10^5, 10^5]^2$ 且属于 $V(\mathcal{H}_\omega)$；
3. $A_t$ 内部互不相同，且均不在 $F_{\le t-1}$ 中；
4. **$S_t$ 满足 Local 3-AP-Free**：$S_t$ 中不得存在三个互异格点 $u, v, w$ 满足 $u + w = 2v$ 且 $\operatorname{dist}_{\mathcal{H}_\omega}(u, w) \le 6$。

> **等价 $O(1)$ 判定定理**：在二分蜂窝图 $\mathcal{H}_\omega$ 上，满足 $u + w = 2v$ 且 $\operatorname{dist}_{\mathcal{H}_\omega}(u, w) \le 6$ 的共线三元组 $(u, v, w)$，**严格等价于**存在 $d \in N_2(0, 0)$ 使 $v - d, v, v + d \in S_t$（步距恰好为 $\operatorname{dist}(v-d, v) = 2$、$\operatorname{dist}(v-d, v+d) = 4$）。每枚新落子只需检查 3 条轴向上的 9 次哈希查询即可精确判定。

### 2.5 幽灵免疫弹开、素数天使翼与捕获判定

公开校验通过并扣减代币后，评测器依次执行：

1. **幽灵免疫弹开**：检查是否存在 $j \in \{1, \dots, k_t\}$ 使 $(a_j, b_j) = r_{t-1}$。若命中，则令 `bounce_idx = j`，该枚陷阱被弹开失效（**不加入 $F_{\le t}$，不计入 $|F_{\le t}| \le 4\,000$，不参与后续 3-AP 校验，但已扣除的 $Q'$ 代币不退还**）；若未命中，令 `bounce_idx = 0`。其余落子永久固化至 $F_{\le t}$。
2. **封锁总数上限**：校验 $|F_{\le t}| \le 4\,000$，超出直接判 `WA`。
3. **合法移动集与跃迁闪光**：
   - 若 $r_{t-1} \in \mathcal{P}_\omega$：令 `flash = 1`，合法移动集 $M_t = (N_1(r_{t-1}) \cup N_2(r_{t-1})) \setminus F_{\le t}$（距离为 2 的跃迁无视中间格点是否被封锁）；
   - 若 $r_{t-1} \notin \mathcal{P}_\omega$：令 `flash = 0`，合法移动集 $M_t = N_1(r_{t-1}) \setminus F_{\le t}$。
4. **捕获或移动**：
   - 若 $M_t = \emptyset$：追截成功！评测器发送 `1 0 0 0\n` 并刷新，本局判 `AC`。选手收到后必须立即正常退出（返回码 `0`）且不得输出多余非空白字符；
   - 若 $M_t \ne \emptyset$：兔子选择 $r_t \in M_t$（兔子移动不受 $[-10^5, 10^5]^2$ 限制，可跑向无限蜂窝任意格点），评测器计算 $\text{dist} = \operatorname{dist}_{\mathcal{H}_\omega}((p_a, p_b), r_t)$ 并发送 `0 dist flash bounce_idx\n`。
5. 若完成 $T_{\max} = 5\,000$ 轮后仍未捕获，本局判 `WA`。

---

## 三、交互协议与示例

### 3.1 协议格式

- **阶段一（选手 → 评测器，1 行）**：`P Q\n`（后接 `std::flush`）。
- **阶段二第 $t$ 轮（选手 → 评测器，1 行，长度 $\le 1024$ 字节）**：`k_t a_1 b_1 ... a_{k_t} b_{k_t} pa pb\n`（后接 `std::flush`）。
- **阶段二第 $t$ 轮（评测器 → 选手，1 行）**：
  - 未捕获：`0 dist flash bounce_idx\n`
  - 已捕获：`1 0 0 0\n`

### 3.2 经过引擎核验的 4 回合完整交互样例（$q = 3/2 = 1.50$）

| 回合 | 漏桶补给后 | 选手输出（`k_t ... pa pb`） | 评测器回传 | 说明 |
|---|---|---|---|---|
| 阶段一 | — | `3 2` | — | 声明 $P'=3, Q'=2, q=1.50,\; \text{cap}=4$ |
| 第 1 轮 | `tokens=3` | `1 2 0 0 100000` | `0 133333 0 0` | 封锁 $(2,0) \in \mathcal{P}_\omega$；远场声呐 $(0,10^5)$ 返回 `133333` 唯一锁定 $r_1 = (1,1)$ |
| 第 2 轮 | `tokens=4` | `2 2 2 0 1 1 0` | `0 0 0 0` | 封锁 $(2,2)$ 与 $(0,1)$（无 3-AP 冲突），$r_1=(1,1)$ 仅剩 $(1,0)$ 可走，故 $r_2=(1,0)$ |
| 第 3 轮 | `tokens=3` | `1 0 -1 1 1` | `0 0 0 0` | 封锁 $(0,-1)$，$r_2=(1,0)$ 仅剩 $(1,1)$ 可走，故 $r_3=(1,1)$ |
| 第 4 轮 | `tokens=4` | `1 1 0 1 0` | `1 0 0 0` | 封锁 $(1,0)$，$r_3=(1,1)$ 全部 3 个邻点已被封锁，$M_4=\emptyset$，捕获 `AC`！选手立刻退出 |

该样例在 4 回合用 5 枚陷阱捕获，`RawScore = 50.00`，`NormScore = 20.00`。

---

## 四、双轨计分规则与限制

### 4.1 单局得分公式

设某局成功捕获（`AC`），声明的最简分数速率为 $q = P'/Q'$（未捕获或违规为 `0` 分）：

- **单局不封顶题面原分 $\operatorname{RawScore}(q)$**：
  $$\operatorname{RawScore}(q) = \begin{cases} 0, & q \ge 2 \\ 100 \cdot \dfrac{4Q' - 2P'}{2P' - Q'}, & \dfrac{1}{2} < q < 2 \\ \text{Singularity } (+\infty), & q \le \dfrac{1}{2} \end{cases}$$
- **单局 0–100 折算分 $\operatorname{NormScore}(q)$**（严格等于 $100 \cdot \frac{r}{r + 200}$）：
  $$\operatorname{NormScore}(q) = \begin{cases} 0, & q \ge 2 \\ 100 \cdot \dfrac{2Q' - P'}{P' + Q'}, & \dfrac{1}{2} < q < 2 \\ 100, & q \le \dfrac{1}{2} \end{cases}$$

常见速率锚点对照：

| 速率 $q = P'/Q'$ | 题面原分 $\operatorname{RawScore}$ | 百分制折算分 $\operatorname{NormScore}$ | 典型战术层级 |
|---|---:|---:|---|
| `7/4 = 1.75` | `20.00` | `9.09` | 起步模板（`agents/starter.cpp`） |
| `3/2 = 1.50` | `50.00` | `20.00` | 闪电开局合围（`agents/baselines/blitz_150.cpp`） |
| `5/4 = 1.25` | `100.00` | `33.33` | 远场投影 + 素数防跃与防地堡交错合围（`agents/baselines/staggered_125.cpp`） |
| `1/1 = 1.00` | `200.00` | `50.00` | 亚速率交错围猎与精确信念收束 |
| `9/10 = 0.90` | `275.00` | `57.89` | 高阶几何封堵与对抗残局 |
| `4/5 = 0.80` | `400.00` | `66.67` | 低速率长程围猎与多阶段防跃 |
| `7/10 = 0.70` | `650.00` | `76.47` | 极限低速率几何压缩 |
| `3/5 = 0.60` | `1400.00` | `87.50` | 近奇点级精密控制 |
| `<= 1/2` | `Singularity (+inf)` | `100.00` | 奇点级理论突破 |

### 4.2 多局正式评测汇总（双轨制）

正式评测会在 **4 类互补隐藏对手家族 × 8 个全新随机种子 = 32 局**动态交互对抗上运行你的提交：

- **套题 0–100 总分（`score`）**：全部 32 局 `NormScore` 的等权算术平均，防止单一测试点偶然性或冒险压低速率“买彩票”。
- **单题不封顶原分（`raw_score` / `singularity`）**：仅在全部 32 局均为 `AC` 时取最劣单局 `RawScore`（全通且全员 $q \le 1/2$ 时标记 `singularity = true`），存在任意未 `AC` 局则记 `0.00`。

### 4.3 运行资源硬限

- **时间限制**：单局求解器纯 CPU / 活跃等待时间上限 **`5.0 s`**（不含交互器自身计算时间）。
- **内存限制**：单局峰值内存硬限 **`512 MiB`**。
- **单行长度限制**：每行输出不得超过 `1024` 字节。
- **约束规模**：$T_{\max} = 5\,000$ 轮，累计固化陷阱 $|F_{\le T}| \le 4\,000$，坐标 $[-10^5, 10^5]^2$，$1 \le Q \le 10^6$。

---

## 五、本地工具、公开基线与可视化回放

本工作区提供 3 档公开练习对手（`engine/practice_interactor.cpp`：`P1_BasicEscape`、`P2_PrimeGreedy`、`P3_LiteBelief`）、3 个递增基线实现与交互式蜂窝可视化回放器：

```bash
# 查看公开练习对手与内置基线
python scripts/spar.py --list

# 自测起步模板、公开基线或你自己的提交
python scripts/spar.py agents/starter.cpp
python scripts/spar.py agents/baselines/blitz_150.cpp
python scripts/spar.py agents/baselines/staggered_125.cpp
python scripts/spar.py ../submissions/ghost_rabbit.cpp

# 针对指定公开对手与多组自定义种子定向压测
python scripts/spar.py ../submissions/ghost_rabbit.cpp --opponent P3_LiteBelief --seed 11 22 33 44 55

# 录制完整逐回合蜂窝回放 JSON（可用浏览器打开 viz/player.html 查看）
python scripts/record_replay.py ../submissions/ghost_rabbit.cpp --out replays/my_run.json
```

你可直接用浏览器打开 `viz/player.html`，拖入 `replays/replay_demo.json` 或自己录制的 `replays/my_run.json`，逐回合观察艾森斯坦素数分布、陷阱固化与弹开闪烁、声呐距离环、兔子见证轨迹与信念候选云。

更多细节请参阅：
- [FORMAT.md](FORMAT.md)：完整协议字段与状态码说明
- [handbook/contest-rules.md](handbook/contest-rules.md)：规则边界与常见踩坑清单
- [handbook/geometry-guide.md](handbook/geometry-guide.md)：艾森斯坦蜂窝几何、远场投影反演与 Local 3-AP-Free 免死区规避指南
