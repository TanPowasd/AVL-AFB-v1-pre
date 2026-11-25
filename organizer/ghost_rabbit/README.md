# 天翼幽灵兔：四重奇点追截战（`ghost_rabbit` · 维护文档）

本目录为 AVL Final Bench 正式题 `ghost_rabbit` 的评测源目录。选手版题面位于 `config/contestant_readme.md`，导出至 `contestant/ghost_rabbit/README.md`。

## 目录结构

- `engine/`：公开 C++20 规则核心（`judge_core.hpp`）、3 档公开练习交互器（`practice_interactor.cpp`）及 Python 双进程管道并发引擎（`common.py`, `policy.py`, `runner.py`, `replay.py`）。
- `hidden/`：正式隐藏强交互器（`official_interactor.cpp`：4 类互补强对抗策略 `F1_PrimeCone`、`F2_WallHugger`、`F3_QuantumAdv`、`F4_Stochastic`）与 32 局 OS-CSPRNG 赛程调度（`schedule.py`）。**严禁导出至选手包**。
- `agents/`：起步模板 `starter.cpp`（$q=7/4$）、公开基线 `baselines/blitz_150.cpp`（$q=3/2$）与 `baselines/staggered_125.cpp`（$q=5/4$）。
- `scripts/`：
  - `spar.py`：本地公开练习自测
  - `score.py`：32 局正式并行动态评测（自动启用 `submission_sandbox`）
  - `record_replay.py`：离线蜂窝回放录制
  - `pack_contestant.py`：单题纯净选手包导出（内置零剧透扫描）
  - `freeze_profile.py`：录制示范回放并冻结 `config/scoring_v2.json` 哈希
- `viz/player.html`：单文件 HTML5 Canvas 艾森斯坦蜂窝交互回放器。
- `luogu/`：洛谷 `testlib.h` 交互题一键导出包（`interactor.cpp`, `statement.md`, `export_luogu.py`）。
- `tests/test_ghost_rabbit.py`：数学核验、4 回合样例、量子见证路径一致性、恶意提交防御、纯净导出与洛谷包测试。

## 常用维护命令

```bash
# 生成示范回放并刷新 config/scoring_v2.json 冻结哈希
python organizer/ghost_rabbit/scripts/freeze_profile.py

# 快速 8 局冒烟评测（非正式）
python organizer/ghost_rabbit/scripts/score.py --src organizer/ghost_rabbit/agents/baselines/blitz_150.cpp --quick

# 正式 32 局 OS-CSPRNG 动态评测
python organizer/ghost_rabbit/scripts/score.py --src organizer/ghost_rabbit/agents/baselines/blitz_150.cpp

# 运行全套单元测试
python -m unittest discover -s organizer/ghost_rabbit/tests

# 导出洛谷交互题数据包
python organizer/ghost_rabbit/luogu/export_luogu.py --out <目标目录>
```
