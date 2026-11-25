# 《压缩奇点》（The Compression Singularity）

C++20 构造最优化 / 程序合成赛题（两阶段盲测 Special Judge）：
- **阶段一（实验室破译期）**：选手工作区不剧透隐藏生成家族类型，仅公布 6 指令虚拟机规范、评分公式，以及 8 组不贴标签的匿名标本（`data/samples/specimen_1.in` ～ `specimen_8.in`，每组 $L = 10^5$）。AI 需自行编写脚本破译标本背后的数学机制并构建通用合成器。
- **阶段二（暗箱泛化压测期）**：评测机使用 `hidden/generator.py` 在离线环境中针对全部 8 个隐藏计算家族（F1–F8）动态重新生成 **512 个测试点**（每家族 32 组 $10^5$ + 32 组 $10^6$，全新随机种子与内部参数），单点限 60 s CPU/墙钟运行选手 C++20 求解器，并用确定性参考虚拟机精确校验。

详细选手文档见 `config/contestant_readme.md`（导出至选手区顶层 `README.md`）。

## 常用命令

```bash
# 本地匿名标本编译自测
python scripts/spar.py --list
python scripts/spar.py agents/starter.cpp
python scripts/spar.py agents/baselines/rle_loop.cpp

# 正式动态盲测（512 点：8 隐藏家族 × [32×10^5 + 32×10^6]，自动多核并发；支持 --jobs N 与 --vm-mode fast|jit|auto）
python scripts/score.py --src agents/starter.cpp --quick --out results/practice.json

# 交互式高性能模拟器、热点剖析与虚拟机吞吐压测
python scripts/simulate.py -e "+>+>+>+[....+.<]" --profile
python scripts/bench_vm.py

# 录制离线可视化回放（用浏览器打开 viz/player.html）
python scripts/record_replay.py agents/baselines/rle_loop.cpp --case 00_example --out replays/demo.json
```
