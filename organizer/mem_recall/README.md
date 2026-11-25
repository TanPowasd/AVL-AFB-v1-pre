# 记忆检索（Memory Recall） · 组委会环境（`organizer/mem_recall`）

- 选手公开规则源文档：`config/contestant_readme.md`（导出后为 `contestant/mem_recall/README.md`）
- 导出独立选手环境：在本目录运行 `python scripts/pack_contestant.py --force`，只刷新本题。全量导出见根目录 `README.md`
- 运行正式评分：在本目录运行 `python scripts/score.py`

## 隐藏测试集（评分前必做）

仓库不含隐藏集。`.gitignore` 忽略 `organizer/*/hidden/`，数据需要单独保管，评分前放到本目录：

1. 把 18 个正式计分集放到 `organizer/mem_recall/hidden/`。
2. `scripts/score.py` 启动时按 `contestant/mem_recall/data/SHA256SUMS.json` 校验 19 个文件，缺文件或哈希不符时报错退出。
3. 运行评分，确认输出的是 official 宏平均且为 18 集。

`hidden/` 不存在时 `score.py` 报错退出。加 `--allow-dev` 才改用公开开发集，或用 `--data <目录>` 显式指定数据（不做校验）。这两种情况成绩单标为 `dev`，不能当作正式成绩。

本题成绩范围是 0–1，计入 `score_all.py` 总分时乘以 100。
