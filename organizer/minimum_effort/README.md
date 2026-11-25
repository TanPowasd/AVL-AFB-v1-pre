# 最小努力（`minimum_effort` / U755456）维护与评测说明

本目录为《最小努力》（U755456）的完整题库与评测环境。选手可见题面位于 `config/contestant_readme.md`。

## 结构概览

- `config/contestant_readme.md`：原题题面（`U755456 最小努力.md`）+ 选手本地自测/交卷说明。
- `config/format.json`、`config/scoring_v2.json`：题目规格与冻结校验清单（锁定 36 个官方测试点、引擎与官方参考解 SHA-256）。
- `data/samples/`：题面 3 组公开样例（`01`–`03`）。
- `data/official/`：36 个正式测试点（`01.in/out`–`36.in/out`）、`subtasks.json`、`manifest.json`、`SHA256SUMS`、`config.yml`、`测试点说明.csv`（不导出至选手包）。
- `agents/starter.cpp`：公开 C++20 起步模板。
- `agents/reference/leaderboard_profile_search.cpp`：官方参考解（不导出至选手包）。
- `tools/random_tests/`：随机与历史回归测试套件（不导出至选手包）。

## 常用维护命令

```bash
# 冻结 config/scoring_v2.json
python organizer/minimum_effort/scripts/freeze_profile.py --revision <new-revision>

# 正式 36 点评测官方参考解
python organizer/minimum_effort/scripts/score.py --src organizer/minimum_effort/agents/reference/leaderboard_profile_search.cpp --runs 1

# 导出选手工作区并校验纯净性
python organizer/minimum_effort/scripts/pack_contestant.py --force

# 运行本题单元测试
python -m unittest discover -s organizer/minimum_effort/tests -v
```
