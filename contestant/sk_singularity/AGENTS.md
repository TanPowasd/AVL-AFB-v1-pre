# AI 参赛者须知（sk_singularity）

1. 本目录为《双符奇点》独立工作区。请阅读 [`README.md`](README.md)、[`FORMAT.md`](FORMAT.md) 与 [`engine/sk_core.hpp`](engine/sk_core.hpp) 了解完整规则与白盒校验内核。
2. 你的最终答案必须是**单文件独立 C++20 程序**，写入顶层 `../submissions/sk_singularity.cpp`（调试期间也可暂存于 `agents/submissions/my_solution.cpp`）。
3. 严禁修改 `engine/`、`scripts/`、`config/`、`agents/baselines/`、`README.md`、`manifest.json` 等只读文件。
4. 本地自测使用 `python scripts/spar.py ../submissions/sk_singularity.cpp --workers 4`，并建议追加 `--flip` 验证 `S <-> K` 对称同构不变性。
5. 提交程序在沙盒中运行：禁止读写外部文件、联网或启动子进程；请读取输入第 1 行的 `TL_ms` 并留出安全余量（建议在 `0.80 * TL_ms` 前收敛输出）以防 TLE。
