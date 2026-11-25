# 参赛 AI 工作说明 · 天翼幽灵兔（ghost_rabbit）

## 交卷位置与格式

- 最终提交路径：`../submissions/ghost_rabbit.cpp`（单文件 C++20 在线交互求解器，含标准 `main`，由 `g++ -std=gnu++20 -O2 -pipe` 编译）。
- 本地调试可暂存于 `agents/submissions/my_solution.cpp`，正式评测仅读取顶层 `../submissions/ghost_rabbit.cpp`。
- 每轮交互输出后**必须立即刷新输出缓冲区**（`std::cout << ... << '\n' << std::flush;`）；收到 `1 0 0 0` 捕获信号后**立即以退出码 `0` 正常结束**，禁止再输出任何多余非空白字符。

## 硬性边界

- 单局求解器纯 CPU / 活跃时间上限 **`5.0 s`**（不含交互器自身计算耗时），内存上限 **`512 MiB`**，禁止多线程、联网、启动子进程、读写工作区外路径。
- 禁止修改 `engine/`、`scripts/`、`config/`、`agents/baselines/`、`viz/`、`README.md`、`manifest.json`。
- 禁止试图针对公开练习对手硬编码路径——正式评测在 4 类互补隐藏对手家族上以全新随机种子实时交互对抗。

## 本地自测与回放命令

```bash
python scripts/spar.py --list
python scripts/spar.py ../submissions/ghost_rabbit.cpp
python scripts/spar.py ../submissions/ghost_rabbit.cpp --opponent P3_LiteBelief --seed 1 2 3 4 5
python scripts/record_replay.py ../submissions/ghost_rabbit.cpp --out replays/my_run.json
```
