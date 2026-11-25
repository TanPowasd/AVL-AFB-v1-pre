# AI 参赛指南 · 压缩奇点（The Compression Singularity）

## 一句话目标

本赛题采用**两阶段盲测机制**：
1. **阶段一（当前工作区探索）**：利用本地 Python / C++ 工具链和官方参考虚拟机，自主分析 `data/samples/specimen_1.in` ～ `specimen_8.in`（8 组不贴标签的匿名 $10^5$ 比特标本），反向识别它们背后隐藏的确定性计算机制。
2. **阶段二（离线泛化压测）**：向 `../submissions/compression_singularity.cpp` 提交一个独立、无网络、不调用大模型的单文件 C++20 自动化求解器。正式评测会在全部未知计算家族上动态更换全新随机种子与参数（涵盖 $10^5$ 与 $10^6$ 两种长度）逐一喂给你的求解器，校验其输出的 6 指令虚拟机程序 $P$，并计算：
   - **不封顶原分**：$\operatorname{RawScore}(P) = 100 \cdot \frac{B_{\text{ref}}}{|P| \cdot \log_2 6}$
   - **百分制折算分**：$\operatorname{NormScore}(r) = 100 \cdot \frac{r^{0.75}}{r^{0.75} + 1000^{0.75}}$

## 推荐工作流

1. 阅读 `README.md`、`FORMAT.md`、`handbook/vm-guide.md` 与 `handbook/contest-rules.md`，并通读参考虚拟机 `engine/vm.cpp` 与 `engine/vm_ref.py`。
2. 先将 `agents/starter.cpp` 或 `agents/baselines/rle_loop.cpp` 复制为 `../submissions/compression_singularity.cpp` 跑通 `python scripts/spar.py`，拿到 100% AC 的保底基线。
3. 对 `specimen_1.in` ～ `specimen_8.in` 做系统的探索性数据分析（如游程分布、n-gram 熵、不同步长自相关、线性与非线性关系检验、分层重复结构、差分残差与坐标变换分析等），找出每个匿名标本的低复杂度生成机理。
4. 为发现的每一类机制编写通用自动识别器与 6 指令虚拟机编译器，同时保留经校验的通用回退编码器（确保任何未命中或超步预算的输入都能稳定 AC，不拿 0 分）。
5. 注意泛化：切勿针对 `specimen_1..8` 的特定参数硬编码，务必在自造的同族新随机参数输入（写入 `logs/custom.in` 并用 `--input` 测试）上验证求解器的识别率与压缩比。
