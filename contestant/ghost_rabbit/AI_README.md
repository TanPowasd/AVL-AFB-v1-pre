# AI 参赛指南 · 天翼幽灵兔：四重奇点追截战（The Winged Ghost Rabbit）

## 一句话目标

在无限艾森斯坦蜂窝图 $\mathcal{H}_\omega$ 上，提交一个单文件 C++20 在线交互追截程序至 `../submissions/ghost_rabbit.cpp`。对局开始前你先自报陷阱补给速率 $q = P/Q \in (0, 2]$（漏桶上限 $2Q'$），随后在最多 $5\,000$ 回合、累计封锁不超过 $4\,000$ 格且满足**局部等距无三连共线（Local 3-AP-Free）**的前提下，凭借每轮一次任意位置声呐测距与**艾森斯坦素数跃迁闪光 / 幽灵弹开**反馈，彻底截死拥有 $p=2$ 素数天使翼的隐藏幽灵兔。

你报出的 $q$ 越低，成功捕获后的得分越高：
- **单局不封顶原分**：$q \in (\frac{1}{2}, 2)$ 时 $\operatorname{RawScore}(q) = 100 \cdot \frac{4Q'-2P'}{2P'-Q'}$；$q \le \frac{1}{2}$ 且捕获标记奇点（`singularity = true`）；$q \ge 2$ 或未捕获为 `0`。
- **单局百分制折算分**：$\operatorname{NormScore}(q) = 100 \cdot \frac{r}{r + 200} = 100 \cdot \frac{2Q'-P'}{P'+Q'}$（在 $q \le \frac{1}{2}$ 封顶 `100.00`）。
- **套题总分**：32 局正式随机对抗（4 类互补隐藏策略 × 8 随机种子）的 `NormScore` 等权均值；单题不封顶 `RawScore` 仅在全部 32 局 AC 时取最劣单局。

## 推荐工作流

1. 通读 `README.md`、`FORMAT.md`、`handbook/contest-rules.md` 与 `handbook/geometry-guide.md`，重点掌握 $\mathcal{H}_\omega$ 的 $O(1)$ 三维等距嵌入公式、精确艾森斯坦素数判定、Local 3-AP-Free 的 9 次查表判定与“免死区（3-AP Sanctuary）”规避。
2. 先运行公开模板与基线熟悉自测与可视化回放链路：
   ```bash
   python scripts/spar.py --list
   python scripts/spar.py agents/starter.cpp
   python scripts/spar.py agents/baselines/blitz_150.cpp
   python scripts/spar.py agents/baselines/staggered_125.cpp
   python scripts/record_replay.py agents/baselines/staggered_125.cpp --out replays/my_run.json
   ```
   用浏览器打开 `viz/player.html` 加载 `replays/my_run.json` 或 `replays/replay_demo.json`，直观观察素数跳板、锯齿防跃围墙、声呐距环与信念云演化。
3. 理解不同速率区间的算法阶梯：
   - **$q = 1.50$（`Norm=20.00, Raw=50`）**：开局闪电合围保底档，利用精确信念跟踪与极小极大声呐可在 4 回合内锁定必杀。
   - **$q \in [1.00, 1.25]$（`Norm=33.33~50.00, Raw=100~200`）**：突破开局步数瓶颈，结合三远场投影声呐反演 + 素数优先锯齿 Local 3-AP-Free 宏围墙 + 防 3-AP 免死区腔体收口。
   - **$q < 1.00$（`Norm > 50.00, Raw > 200`）**：需要兼顾对抗性信念空间收束、远场与局部混合声呐调度、高效素数拦截网以及严密的残局防逃逸几何设计。
4. 每次改动后务必同时针对全部 3 档公开练习对手（含 `P3_LiteBelief`）及多个自定义种子压测，确认无 `WA/PE/TLE/MLE` 且不依赖特定随机种子。
