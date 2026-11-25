# 《双符奇点》（The SK Singularity）

| 题目类型 | 提交形态 | 语言与资源限制 | 校验引擎 |
| :--- | :--- | :--- | :--- |
| 盲规则组合子程序合成 / 忙碌海狸最优化 | 单文件 C++20 求解程序（`submissions/sk_singularity.cpp`） | C++20（`g++ -std=gnu++20 -O2 -pipe`），单测 `5.0 s` / `1024 MiB` | 白盒确定性 Hash-Consed DAG 精确归约引擎（`engine/sk_core.hpp`） |

---

## 一、形式化问题定义

### 1. 双符号满二叉树语法与规模
定义由二元组合子符号 `S`、`K` 与函数应用构成的满二叉树集合 $\mathcal{E}$，其上下文无关文法（BNF）为：

$$E ::= \texttt{S} \mid \texttt{K} \mid \texttt{(} E \; E \texttt{)}$$

对任意表达式 $E \in \mathcal{E}$，定义其**规模（叶子节点总数）** $|E| \in \mathbb{Z}^+$ 为：

$$|\texttt{S}| = 1, \qquad |\texttt{K}| = 1, \qquad |\texttt{(} E_1 \; E_2 \texttt{)}| = |E_1| + |E_2|$$

### 2. 动态双符号重写规则（Blind Combinator Basis）
每组测试点动态下发一套封闭的**双符号组合子重写系统** $\mathcal{R} = (a_S, R_S, a_K, R_K)$：
- 符号 `S` 的元数（参数个数）为 $a_S \in \{1, 2, 3, 4\}$，右端模板 $R_S(x_0, \dots, x_{a_S - 1})$ 是一棵满二叉树，叶子为参数编号 `0`..`a_S - 1` 或字面常量 `S`、`K`；
- 符号 `K` 的元数（参数个数）为 $a_K \in \{1, 2, 3, 4\}$，右端模板 $R_K(x_0, \dots, x_{a_K - 1})$ 同理。

例如，经典的标准 SK 组合逻辑表示为：$a_S = 3$，$R_S = \texttt{((0 2) (1 2))}$；$a_K = 2$，$R_K = \texttt{0}$，即：
- $\texttt{(((S } x_0 \texttt{) } x_1 \texttt{) } x_2 \texttt{)} \to \texttt{((} x_0 \; x_2 \texttt{) (} x_1 \; x_2 \texttt{))}$
- $\texttt{((K } x_0 \texttt{) } x_1 \texttt{)} \to x_0$

> **重要提示**：测试点中的 `S` 与 `K` 仅为两个抽象符文名称。`S` **不一定**是复制子，`K` **不一定**是删减子；两者的元数（可为 `(3,2)`、`(3,3)`、`(4,2)`、`(2,3)` 等）、参数置换/嵌套拓扑乃至模板内嵌字面常量均由输入动态指定，且正式评测中可能施加精确的 `S <-> K` 符号对换同构。你的求解器必须完全依据输入规则进行通用推理与合成。

### 3. 最左最外归约语义（Normal-Order Reduction）
任取表达式 $E \in \mathcal{E}$，沿最左链唯一分解为 $E = (\cdots((h \, u_0) \, u_1)\cdots u_{k-1})$，其中头部符号 $h \in \{\texttt{S}, \texttt{K}\}$，$k \ge 0$ 为其脊柱上的参数个数。称 $E$ 为**根部红式（Root Redex）**当且仅当 $k = a_h$。

单步**最左最外归约关系** $\to_{\mathrm{no}}$ 由以下互斥规则唯一归纳定义：
1. **根部归约（Root Redex）**：若 $E = h \, u_0 \dots u_{a_h - 1}$ 是根部红式，则将 $R_h$ 中的变量 `i` 替换为 $u_i$（字面常量 `S`/`K` 保持不变），一步归约至实例化结果 $R_h[u_0, \dots, u_{a_h - 1}]$。
2. **左子树优先（App-Left）**：若 $E = \texttt{(} E_1 \; E_2 \texttt{)}$ 不是根部红式，且存在 $E_1'$ 使 $E_1 \to_{\mathrm{no}} E_1'$，则 $\texttt{(} E_1 \; E_2 \texttt{)} \to_{\mathrm{no}} \texttt{(} E_1' \; E_2 \texttt{)}$。
3. **右子树兜底（App-Right）**：若 $E = \texttt{(} E_1 \; E_2 \texttt{)}$ 不是根部红式，$E_1$ 已是范式（无任何后继），且存在 $E_2'$ 使 $E_2 \to_{\mathrm{no}} E_2'$，则 $\texttt{(} E_1 \; E_2 \texttt{)} \to_{\mathrm{no}} \texttt{(} E_1 \; E_2' \texttt{)}$。

若不存在 $E'$ 满足 $E \to_{\mathrm{no}} E'$，称 $E$ 为**范式（Normal Form, $\operatorname{NF}(E)$）**。

从 $E_0 = E$ 出发沿 $\to_{\mathrm{no}}$ 唯一演化 $E_0 \to_{\mathrm{no}} E_1 \to_{\mathrm{no}} \cdots$：若存在最小非负整数 $t \in \mathbb{N}$ 使 $\operatorname{NF}(E_t)$ 成立，则称 $E$ **停机**，其精确停机步数为 $T(E) = t$；否则称 $E$ 发散（$T(E) = +\infty$）。

### 4. 优化目标与结构约束
给定规则 $\mathcal{R}$、微缩规模上限 $N$（$8 \le N \le 24$）、唯一 DAG 节点护栏 $V_{\max}$ 以及可选的**强制残骸子树** $E_{\text{req}}$（无约束时为 `-`），你的程序需在时限 `TL_ms` 内输出一个合法表达式 $E \in \mathcal{E}$，满足：
1. 叶子节点总数 $|E| \le N$；
2. 若 $E_{\text{req}} \ne \texttt{-}$，则 $E$ 必须包含 $E_{\text{req}}$ 作为其完整子树（真子树或自身）；
3. $E$ 在最左最外归约下合法停机，且在白盒 Hash-Consed 记忆化归约引擎中累计创建的**唯一 DAG 节点总数**不超过 $V_{\max}$（默认 $10^7$）。

在满足上述约束的前提下，**最大化精确停机步数 $T(E)$**。

---

## 二、白盒 Hash-Consed 精确归约引擎与超指数突破

完整校验内核开源位于 [`engine/sk_core.hpp`](engine/sk_core.hpp)。关键数学性质如下：

1. **与纯树最左最外步数 100% 严格等价**：对任意封闭双组合子系统（无自由变量），表达式达到弱头范式（WHNF，即 $k < a_h$）的步数与继续归约至范式（NF）的步数均严格可组合：
   - 丢弃的发散分支（如 `K x Ω -> x`）不会被求值；
   - 被复制的共享子树（如 `S x y z -> (x z)(y z)` 中的两个 `z` 副本）在记忆化命中时，其步数会被**严格累加两次**，绝不犯 Call-by-Need 少算步数的错误。
2. **原生支持超指数步数（突破 $10^8$ 物理模拟墙）**：
   - 引擎以**去重后的唯一 DAG 节点数 $\le V_{\max} = 10^7$**（及显式栈上限 $4 \times 10^7$）作为确定性护栏；
   - 步数计数器采用纯整数规格化表示（$T < 2^{62}$ 为精确 64 位无符号整数，$T \ge 2^{62}$ 为 63 位纯整数尾数与 64 位指数，零跨平台浮点漂移）；
   - 只要你在微缩预算 $N \le 24$ 内构造出高共享放大器（让少量 DAG 节点嵌套倍增出天文数字级的纯树归约步数），即使 $T(E) = 2^{500}$ 甚至 $2^{40000}$，引擎也能在毫秒内精确验算并授予对应 $\log_2(T(E)+1)$ 高分！
3. **$O(1)$ 确定性环路判死**：`visiting_whnf` / `visiting_nf` 栈上出现回边即数学证明发散，直接判定为 `DIV`（0 分）。

---

## 三、输入输出格式

### 输入（标准输入 `stdin`，固定 4 行）
```text
N a_S a_K V_max TL_ms
RHS_S
RHS_K
E_req
```
- 第 1 行：五个正整数，分别为规模上限 $N \in [8, 24]$、`S` 元数 $a_S \in [1, 4]$、`K` 元数 $a_K \in [1, 4]$、裁判唯一 DAG 节点上限 $V_{\max}$（正式评测为 `10000000`）、单测求解时限 `TL_ms`（单位毫秒，正式评测为 `5000`）。
- 第 2 行：`S` 的右端满二叉树模板 `RHS_S`（词法含 `0`..`a_S - 1`、`S`、`K`、`(`、`)` 及可选空格）。
- 第 3 行：`K` 的右端满二叉树模板 `RHS_K`。
- 第 4 行：强制残骸子树 `E_req`（合法满二叉树 SK 表达式），无此约束时为单个连字符 `-`。

### 输出（标准输出 `stdout`，单行）
- 仅输出一行合法的 SK 满二叉树表达式 $E$，总字节数不超过 `4096` 字节；
- 仅允许出现字符 `S`、`K`、`(`、`)` 及可选 ASCII 空格（`0x20`），禁止注释、多余文本或省略二元应用括号（例如 `((S K) K)` 合法，而 `S K K` 非法）。

### 输入输出样例
**样例输入**
```text
8 3 2 10000000 5000
((0 2) (1 2))
0
-
```
**样例输出（标准 SK 在 $N=8$ 的精确全局最优解，$T(E) = 1958$ 步）**
```text
((((S (S ((S S) S))) S) S) K)
```

---

## 四、评分规则

- 单测合法停机时，记 $L = \log_2\big(T(E) + 1\big)$；若出现 `CE / TLE / MLE / RE / OLE / PE / WA / DIV / NL` 任一错误，则该测 $L = 0$、得分为 `0`。
- **动态专属锚点标定**：正式评测每批次运行前，系统先以一份冻结强参考求解器在**同一批动态测试点**上按短时限 **`600 ms`** 现场求得该测参考对数步数 $L_{\text{基准}, i}$，按如下规则确定该测专属锚点：
  $$L_{\text{ref}, i} = \operatorname{round}\!\big(\operatorname{clamp}(1.40 \cdot L_{\text{基准}, i},\, 12.0,\, 600.0),\, 2\big)$$
- **单测 0–100 折算分**：采用严格单调饱和曲线：

$$\text{NormScore}_i(L) = 100 \cdot \frac{L^{0.85}}{L^{0.85} + L_{\text{ref}, i}^{0.85}} = 100 \cdot \frac{r^{0.85}}{r^{0.85} + 1}, \qquad r = \frac{L}{L_{\text{ref}, i}}$$

| 相对锚点倍率 $r = L / L_{\text{ref}, i}$ | 单测折算分 $\text{NormScore}_i$ |
|---:|---:|
| `0.5×` | `35.68` |
| `1.0×` | **`50.00`** |
| `2.0×` | `64.32` |
| `5.0×` | `79.71` |
| `10.0×` | `87.62` |
| `25.0×` | `93.91` |

- **正式总分（0–100）**：正式评测集共 `512` 个动态生成隐藏测试点（通过操作系统 CSPRNG 抽取 `512` 个互异随机种子，覆盖 `8` 类正交组合子/残骸家族 × 每家族 `64` 测，均匀分布于 4 档预算 $N \in \{14, 18, 21, 24\}$，严格排除全部公开练习规则并全批去重，每测再以 50% 概率施加精确 `S <-> K` 符号对换同构），总分为全部 `512` 测 `NormScore` 的等权算术平均值（防止个别测试点爆炸淹没其余家族）。
- **附加不封顶原始指标**：成绩单同时记录 `RawLog2Mean`（全部测试点 $\log_2(T+1)$ 均值）、`RawLog2Max` 以及 `SingularityCount`（停机步数突破 $T(E) \ge 2^{64} - 1$ 的奇点测试点数）。

---

## 五、本地开发、自测与可视化

1. **查看公开练习集（14 测，涵盖全部语法特性）**：
   ```bash
   python scripts/spar.py --list
   ```
2. **评测起步模板与公开中阶基线**（默认按本机 CPU/内存自适应并发，也可用 `--workers`/`--jobs` 指定）：
   ```bash
   python scripts/spar.py agents/starter.cpp
   python scripts/spar.py agents/baselines/beam_evo.cpp
   ```
3. **验证 `S <-> K` 同构对称鲁棒性**（强烈建议每次提交前运行！）：
   ```bash
   python scripts/spar.py ../submissions/sk_singularity.cpp --flip
   ```
4. **录制归约追踪与 DAG 爆炸回放，并在浏览器打开 [`viz/player.html`](viz/player.html)**：
   ```bash
   python scripts/record_replay.py agents/baselines/beam_evo.cpp --case P1_03 --out replays/my_demo.json
   ```
5. **交卷位置**：将最终单文件 C++20 代码保存至工作区顶层 `../submissions/sk_singularity.cpp`（提交文件必须独立可编译，不得 `#include` 工作区内私有头文件，运行期不得读写外部文件、联网或启动子进程）。

进一步说明请参阅 [`FORMAT.md`](FORMAT.md)、[`handbook/contest-rules.md`](handbook/contest-rules.md) 与 [`handbook/combinator-guide.md`](handbook/combinator-guide.md)。
