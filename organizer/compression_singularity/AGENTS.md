# 参赛 AI 工作说明 · 压缩奇点（compression_singularity）

## 交卷位置与格式

- 最终提交路径：`../submissions/compression_singularity.cpp`（单文件 C++20 自动化求解器，含标准 `main`，由 `g++ -std=gnu++20 -O2 -pipe` 编译）。
- 本地调试可暂存于 `agents/submissions/my_solution.cpp`，但正式评测仅读取顶层 `../submissions/compression_singularity.cpp`。

## 硬性边界

- 禁止多线程、联网、启动子进程、读取工作区以外的任何路径。
- 禁止修改 `engine/`、`scripts/`、`config/`、`data/`、`agents/baselines/`、`README.md`、`manifest.json`。
- 禁止针对 `data/samples/specimen_1..4.in` 硬编码答案——阶段二正式评测会在离线环境动态更换全新随机种子与内部参数重新生成全部测试点（规模覆盖 $10^5$ 与 $10^6$）。

## 自测命令

```bash
python scripts/spar.py --list
python scripts/spar.py ../submissions/compression_singularity.cpp
python scripts/verify_program.py data/samples/00_example.in data/samples/00_example.out
```
