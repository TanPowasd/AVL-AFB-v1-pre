# 《双符奇点》（sk_singularity）接口与文件格式规范

## 1. 求解器输入格式（`stdin`）

固定 4 行纯文本（Unix `\n` 换行）：

```text
<N> <a_S> <a_K> <V_max> <TL_ms>
<RHS_S>
<RHS_K>
<E_req>
```

| 字段 | 类型与范围 | 说明 |
|---|---|---|
| `N` | 整数，$8 \le N \le 24$ | 允许输出表达式的最大叶子总数 $|E| \le N$ |
| `a_S` | 整数，$1 \le a_S \le 4$ | 符号 `S` 的元数（需要连续接受 $a_S$ 个参数才触发根部红式） |
| `a_K` | 整数，$1 \le a_K \le 4$ | 符号 `K` 的元数 |
| `V_max` | 整数，默认 `10000000` | 白盒 Hash-Consed DAG 归约引擎允许创建的唯一节点数上限 |
| `TL_ms` | 整数，默认 `5000` | 单测求解器 CPU/墙钟时间上限（毫秒） |
| `RHS_S` | 满二叉树模板字符串 | `S` 触发根部红式时的右端替换模板，叶记号为 `0`..`a_S - 1`、`S`、`K` |
| `RHS_K` | 满二叉树模板字符串 | `K` 触发根部红式时的右端替换模板，叶记号为 `0`..`a_K - 1`、`S`、`K` |
| `E_req` | 合法 SK 表达式或 `-` | 若不为 `-`，则输出 $E$ 须包含 $E_{\text{req}}$ 作为完整子树（真子树或自身） |

### 模板文法（`RHS`）
```ebnf
RHS ::= '0' | ... | (arity - 1) | 'S' | 'K' | '(' RHS RHS ')'
```
ASCII 空格（`0x20`）为可选分隔符。所有二元函数应用均必须显式带括号。

## 2. 求解器输出格式（`stdout`）

- 仅输出 **1 行**合法的 SK 满二叉树表达式 $E$（末尾允许可选换行 `\n` 或 `\r\n`）。
- 严格词法：仅允许字符 `S`、`K`、`(`、`)` 与可选 ASCII 空格 `0x20`，总字节数 $\le 4096$。
- 严格文法：
  ```ebnf
  E ::= 'S' | 'K' | '(' E E ')'
  ```
- 示例：
  - 合法：`S`、`(S K)`、`((S K) K)`、`((((S(S((SS)S)))S)S)K)`
  - 非法：`S K K`（缺外层括号）、`(S)`（一元括号）、`(S K K)`（三元括号）、空输出、含额外注释或多余行。

## 3. 裁判判定状态码

| 状态码 | 含义 | 单测得分 |
|---|---|---|
| `AC` | 语法合法、$|E| \le N$、满足 $E_{\text{req}}$、在 $V_{\max}$ 唯一 DAG 节点内停机 | $\text{NormScore}(L, L_{\text{ref}})$ |
| `PE` | 输出超过 4096 字节、含非法字符或不符合满二叉树文法 | `0` |
| `WA` | 叶子数 $|E| > N$ 或未包含强制残骸子树 $E_{\text{req}}$ | `0` |
| `DIV` | 归约过程中 `visiting_whnf` 或 `visiting_nf` 检测到确定性环路（证明发散） | `0` |
| `NL` | 创建唯一 DAG 节点数超过 $V_{\max}$ 或显式求值栈深度超过 `40000000` | `0` |
| `TLE` | 求解器超过单测时间上限 `TL_ms` | `0` |
| `MLE` | 求解器峰值内存超过 `1024 MiB` | `0` |
| `OLE` | 求解器标准输出超出上限 | `0` |
| `RE` | 求解器异常退出（非零返回码） | `0` |
| `CE` | C++20 编译失败 | 全套 `0` |

## 4. `engine/judge_cli.cpp` 与回放 JSON 格式

- `scripts/spar.py --json` 与 `scripts/spar.py --out results/<dir>` 输出完整评测 JSON（含 `score`、`raw_log2_mean`、`raw_log2_max`、`singularity_count`、`by_family` 与逐测明细）。
- `scripts/record_replay.py` 输出 `sk-singularity-replay/1` 格式回放 JSON（含显式前 $K$ 步最左最外归约轨迹 `trace`、唯一 DAG 节点步数爆炸曲线 `dag_growth` 及最热共享节点 `hot_dag_nodes`），可直接拖入 [`viz/player.html`](viz/player.html) 浏览。
