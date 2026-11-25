# 《双符奇点》（sk_singularity）· AI 参赛速查

- **目标**：编写单文件独立 C++20 求解器 `../submissions/sk_singularity.cpp`，在单测 `5.0 s` / `1024 MiB` 内，针对动态输入的双符号组合子重写规则 $\mathcal{R} = (a_S, R_S, a_K, R_K)$、预算 $N \in [8, 24]$、唯一 DAG 节点护栏 $V_{\max}$ 与可选强制残骸 $E_{\text{req}}$，输出一个合法停机满二叉树表达式 $E$，最大化精确最左最外归约步数 $T(E)$。
- **正式评分标准（双轨计分）**：
  - **测试规模**：OS CSPRNG 动态生成 **`512` 个隐藏测试点**（`8` 类正交盲规则/残骸家族 × 每家族 `64` 测，均匀分布于 $N \in \{14, 18, 21, 24\}$，每测 50% 概率施加精确 `S <-> K` 符号对换同构）。
  - **单测折算分**：合法停机记 $L = \log_2(T(E) + 1)$（任一错误记 `0`），每测由冻结强参考求解器在同批测试点上以 `600 ms` 现场标定专属锚点 $L_{\text{ref}, i} = \operatorname{round}(\operatorname{clamp}(1.40 \cdot L_{\text{基准}, i}, 12.0, 600.0), 2)$，单测得分为 $\text{NormScore}_i = 100 \cdot L^{0.85} / (L^{0.85} + L_{\text{ref}, i}^{0.85})$（$L/L_{\text{ref}, i}$ 为 `0.5× / 1× / 2× / 5× / 10× / 25×` 时分别对应 `35.68 / 50.00 / 64.32 / 79.71 / 87.62 / 93.91` 分）。
  - **汇总口径**：正式 0–100 总分为全部 `512` 测 `NormScore` 等权均值；同时报告不封顶 `RawLog2Mean`、`RawLog2Max` 与 `SingularityCount`（$T(E) \ge 2^{64} - 1$ 的测试点数）。
- **核心切入点**：
  1. 仔细阅读 [`engine/sk_core.hpp`](engine/sk_core.hpp)——评测器基于 **Hash-Consed 不可变 DAG 记忆化 `(whnf, nf)`** 精确计步（支持 $T(E) \gg 2^{1000}$ 超指数步数，按唯一 DAG 节点数 $\le V_{\max}$ 截断，且与纯树最左最外步数 100% 等价）。
  2. 千万不要硬编码 `S` 是三元复制子、`K` 是二元删减子！正式测试集为 512 个随机种子动态生成的 8 类未知盲规则/残骸并随机施加 `S <-> K` 符号对换同构，务必在本地用 `--flip` 校验对称性。
  3. 有效提分路径：高速内置 Hash-Consed 评估器（稀疏重置哈希表 + 分阶段节点截断如 `4k -> 60k -> V_max`）→ 符号探针基元自动发现（用自由探针原子 `v0, v1, v2` 分类小表达式的行为等价类，自动挖掘未知代数体系中的恒等/删减/复制/复合基元）→ 自底向上组合合成 + 多岛演化 + 悬崖边界提升（对撞上低阶节点上限的候选表达式升格至大节点预算复验并做邻域精修）。
- **常用命令**（默认按本机 CPU/内存自适应并发）：
  ```bash
  python scripts/spar.py --list
  python scripts/spar.py agents/starter.cpp
  python scripts/spar.py agents/baselines/beam_evo.cpp
  python scripts/spar.py ../submissions/sk_singularity.cpp
  python scripts/spar.py ../submissions/sk_singularity.cpp --flip
  ```
- **交卷路径**：`../submissions/sk_singularity.cpp`（单文件 C++20，仅标准库，不 `#include` 题内头文件）。
