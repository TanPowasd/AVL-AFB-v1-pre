# AVL Final Bench · 参赛工作区说明

本工作区有 9 道独立赛题和 1 个统一交卷目录 `submissions/`。赛题按字典序排列，没有题号，也没有预设顺序，做题顺序和时间分配由你决定。AI 参赛者请同时阅读 [`AGENTS.md`](AGENTS.md)。

## 1. 赛题与交卷文件

| 赛题目录 | 赛题名称 | 语言与环境 | 交卷文件 |
|---|---|---|---|
| `ai_training/` | 前沿实验室（Frontier Lab） | Python 3.10+，标准库（打包为 `.zip`，根目录含 `main.py`，可含子模块与只读数据） | `submissions/ai_training.zip` |
| `cache_duel/` | 缓存攻防（Cache Duel） | Python 3.10+，仅标准库 | `submissions/cache_duel.py` |
| `compression_singularity/` | 压缩奇点（The Compression Singularity） | C++20（标准输入输出，两阶段盲测 Special Judge） | `submissions/compression_singularity.cpp` |
| `ghost_rabbit/` | 天翼幽灵兔：四重奇点追截战（The Winged Ghost Rabbit） | C++20（标准输入输出在线交互题，单局 5.0 s / 512 MiB） | `submissions/ghost_rabbit.cpp` |
| `kaggriculture/` | 农场经营（Kaggriculture） | Python 3.10+，标准库（打包为 `.zip`，根目录含 `main.py`，可含子模块与只读数据） | `submissions/kaggriculture.zip` |
| `mem_recall/` | 记忆检索（Memory Recall） | Python 3.10+，仅标准库 | `submissions/mem_recall.py` |
| `minimum_effort/` | 最小努力（Minimum Effort） | C++20（标准输入输出，按点时间/空间效率加权计分，128 MiB，单点 1.0–2.0 s） | `submissions/minimum_effort.cpp` |
| `rift_era/` | 裂陆纪元（Rift Era） | Python 3.11+，仅标准库（打包为 `.zip`，根目录含 `main.py`） | `submissions/rift_era.zip` |
| `sk_singularity/` | 双符奇点（The SK Singularity） | C++20（标准输入输出，盲规则组合子综合与确定性 DAG 裁判，单测 5.0 s / 1024 MiB） | `submissions/sk_singularity.cpp` |

C++ 题的实际编译命令是 `g++ -std=gnu++20 -O2 -pipe`。

## 2. 答题流程

1. 阅读 `<problem>/README.md` 和起步模板：Python 单文件题为 `<problem>/agents/starter.py`；`ai_training`、`kaggriculture`、`rift_era` 为 `agents/template/`；`compression_singularity`、`ghost_rabbit`、`minimum_effort`、`sk_singularity` 为 `agents/starter.cpp`。
2. 调试提交：Python 单文件题在 `agents/submissions/my_agent.py`；`ai_training`、`kaggriculture`、`rift_era` 用 `scripts/make_submission.py` 打包为 `.zip`；`compression_singularity`、`ghost_rabbit`、`minimum_effort`、`sk_singularity` 在 `agents/submissions/my_solution.cpp`，或直接输出至顶层 `submissions/`。
3. 进入 `<problem>/` 运行本地自测：

   ```bash
   python scripts/spar.py --list
   python scripts/spar.py ../submissions/<problem>.<后缀>
   ```

4. 把最终代码按上表的文件名保存到顶层 `submissions/`。正式评分只读取这里，题内 `agents/submissions/` 不会被评分。

示例命令都写作 `python`。如果系统只有 `python3`，请自行替换。

## 3. 计分

- 每题满分 100 分。`mem_recall` 的成绩单显示 0–1 的宏平均，计入总分时乘以 100。
- **总分 = 9 题得分的等权平均。** 未提交、编译失败或没有有效成绩的题按 0 分计入。
- 策略题每次重新随机抽取场景，取多局平均，不取多次运行的最高分。计分对手是固定的参考对手，`spar.py` 只能对阵公开基线，本地分数只作参考。
- 计时类 C++ 题对同一份编译产物连续完整测 3 遍，取总分最高的一遍；同分时取最大用时较小的一遍（`minimum_effort` 按 36 个官方测试点独立计分，内存限制 128 MiB，单点时限 1.0–2.0 s，AC 点再乘时间/空间对数效率系数，时间权重 0.70 > 空间权重 0.30，免扣阈值 50 ms / 16 MiB）。
- `compression_singularity` 为两阶段盲测 C++20 Special Judge 题：探索期提供匿名标本，正式评测在离线环境动态生成新参数测试点并由确定性虚拟机校验；单题榜单保留不封顶原分，套题总分按单调饱和折算分 0–100 计入。
- `ghost_rabbit` 为 C++20 在线交互几何追截题：单局求解器时限 5.0 s、内存上限 512 MiB，正式评测在 4 类互补隐藏对手家族 × 8 个全新随机种子（共 32 局）上动态对抗；套题 0–100 总分取 32 局折算分等权均值，单题榜单同时保留全通最劣不封顶原分。
- `sk_singularity` 为 C++20 盲规则组合子综合题：单测求解器时限 5.0 s、内存上限 1024 MiB，正式评测在 512 个动态生成隐藏测试点（8 类正交盲规则/强制残骸家族 × 64 个随机种子，均匀覆盖 4 档预算，每测随机施加 50% `S<->K` 对换同构并现场动态标定参考锚点）上由确定性哈希共享 DAG 裁判精确计量停机步数；套题 0–100 总分取全部 512 测幂有理折算分等权均值，单题榜单同时保留不封顶 `RawLog2Mean` 与奇点突破计数。

### 各题正式评分标准速查表

| 赛题 | 正式评测规模与采样 | 正式 0–100 计分公式与聚合口径 | 附加指标 / 零分红线 |
|---|---|---|---|
| `ai_training` | OS CSPRNG `64` 种子；单人赛 `64` 局 + 对阵 `4` 个参考对手双座 `512` 局（共 `576` 局） | `0.5 * solo + 0.5 * adv`；`solo = mean(100 * C)`，`adv = mean(50 + 50 * tanh((Value_T - Value_R) / 12710.23))` | 非有限估值/能力计 `0` 分；超时/崩溃后剩余 tick 填 `{}` 继续结算 |
| `cache_duel` | OS CSPRNG `64` 种子 × `3` 组配置 × `7` 个参考对手 × 攻守双角（共 `2688` 局） | `0.5 * 防守均分 + 0.5 * 攻击均分`；`def_score = OPT / M_def`（`M_def=0` 时 `1.0`），`att_score = 1 - def_score`，各乘 `100` 等权均值 | **犯规零容忍**：本方任一犯规/超时/死亡该局直接 `0` 分 |
| `compression_singularity` | OS CSPRNG `512` 测（`8` 隐藏家族 × `[32×10^5 + 32×10^6]`） | 单点原分 `r = 100 * B_ref / (|P| * log2(6))`；单点正式分 `s = 100 * r^0.75 / (r^0.75 + 1000^0.75)`（`r=1000` 为 `50` 分）；总分取 `512` 测 `s` 等权平均 | 单题榜单附报不封顶 `RawScore` 均值；高分段 `s` 强饱和，优先覆盖多家族；任意错/超时/越界该点 `0` 分 |
| `ghost_rabbit` | OS CSPRNG `32` 局（`4` 隐藏对手家族 × `8` 种子） | 单局声明速率 `q = P'/Q'`，AC 时 `NormScore = 100 * (2Q'-P') / (P'+Q')`（`q<=1/2` 得 `100`，`q>=2` 得 `0`）；总分取 `32` 局等权平均 | 单题附报全通最劣 `RawScore = 100*(4Q'-2P')/(2P'-Q')`（全通且 `q<=1/2` 记奇点）；未捕获/违规该局 `0` 分 |
| `kaggriculture` | OS CSPRNG `64` 种子 × `3` 个隐藏参考对手 × 双座（共 `384` 局） | 单局 `50 + 50 * tanh((money_T - money_R) / 80096.26)`，对全部对手、种子与座位等权平均 | 本方技术判负（超时/异常超 30 次/加载失败）该局 `0` 分，仅对手判负得 `100` 分 |
| `mem_recall` | `18` 个隐藏正式评测集（`9` 记忆集 + `4` 英文检索 + `5` 中文检索；协议匿名化与洗牌） | 记忆集算 `r@10`，检索集算 `nDCG@10`（`rel>0` 截断上限 `30`），对 `18` 集等权宏平均（每集固定占 `1/18`）后乘 `100` | 拒答题不计入均值；超时/崩溃/格式错未答题目按空排序记 `0` 分 |
| `minimum_effort` | `36` 个官方隐藏测试点（`4` 子任务：`8 / 12 / 60 / 20` 分，按点独立计分）；完整测 `3` 遍取最高总分 | AC 点得分 = `p * (1 - 0.70 * ft) * (1 - 0.30 * fm)`；`ft = clamp(ln(t/50)/ln(TL/50),0,1)`，`fm = clamp(ln(m/16)/ln(128/16),0,1)`；总分求和 | `t <= 50 ms` 且 `m <= 16 MiB` 免扣分（`E=1.0`）；非 AC 点（WA/TLE/MLE/RE/OLE）得 `0` 分 |
| `rift_era` | OS CSPRNG `64` 种子（每局 `28,800` tick，8 席对阵 `3` 位参考对手确定性混编影子局，席位 `k mod 8` 轮换） | `vtilde(V) = V+10`（`V>=0`）或 `100/(10-V)`（`V<0`），`R = vtilde(V_player)/vtilde(V_shadow)`，单局 `50 + 50 * tanh(0.6 * ln R)`；总分取 `64` 局均值 | 精确有理数与自适应精度区间计算；超时/崩溃后剩余窗口托管为空命令并照常按终局 `V` 计分 |
| `sk_singularity` | OS CSPRNG `512` 测（`8` 隐藏家族 × `64` 种子，预算 `N ∈ {14,18,21,24}`，50% `S<->K` 对换同构） | 合法停机记 `L = log2(T(E)+1)`；每测现场标定 `L_ref,i = round(clamp(1.40 * L_基准,i, 12, 600), 2)`；单测 `100 * L^0.85 / (L^0.85 + L_ref,i^0.85)`，取 `512` 测等权均值 | 附报不封顶 `RawLog2Mean` 与奇点计数（`T >= 2^64-1`）；`DIV / NL / TLE / MLE / WA` 等该测 `0` 分 |

各题的完整规则与公式细节见该题 `README.md`。

## 4. 规则优先级

各题 `README.md` 说明规则，题内 `engine/` 是实际裁判。两者不一致时，按 `engine/` 的实际行为评分。发现不一致时，以引擎行为为准继续答题，并在最终说明中指出。

## 5. 竞赛边界

- **可读取**：本工作区内的全部文件（含已打包在题内 `data/` 的公开数据集）。
- **可写入**：顶层 `submissions/`，以及各题的 `agents/submissions/`、`results/`、`replays/`、`logs/`。
- **不可修改**：各题的 `engine/`、`scripts/`、`config/`、`data/`、`agents/baselines/`、`README.md`、`manifest.json`。
- **禁止**：访问本工作区之外的任何文件或目录，联网，调用外部模型服务，读取其他选手的代码。
- **提交运行时**：除题目 README 明确允许的内容外，提交程序不得读写文件、联网或启动子进程。
- **正式评测环境**：提交可能在隔离环境中运行，只能看到系统运行库和提交文件本身，没有网络，文件系统只读（C++ 程序只能写自己的临时工作目录）。这时常规 Python 提交的地址空间上限为 1 GiB（`mem_recall` 为 2 GiB），超出时按进程崩溃处理。不要让提交依赖未授权文件、网络或更大的内存。
- **自测工具**：`spar.py` 等脚本只加载你的提交和公开基线，`--unrestricted` 参数在选手工作区不可用。
- **`kaggriculture` 特别说明**：提交为根目录含 `main.py` 的 `.zip` 压缩包（允许完整标准库、包内子模块与包内只读数据），不得卡死或调用 `exit()`、`quit()`、`raise SystemExit`。
- **`rift_era` 自测对手提示**：8 国对局需要 7 个对手，但选手包仅内置少量公开基线，凑不成 7 个不同对手；请自行制作多个不同风格或历史版本的对手 bot，配合 `scripts/local_match.py --seats` / `--opponent` 进行多对手自测。
