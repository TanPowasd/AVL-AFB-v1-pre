# AVL Final Bench

9 道独立赛题的评测集，包含选手工作区与评分环境。

## 目录结构

- `contestant/`：选手工作区（外发给受测模型；压缩包为 `contestant.zip`）
- `organizer/`：评分引擎、隐藏测试数据与评测脚本（不得外发给选手）

## 常用命令

以下命令在本目录根下执行：

```bash
# 刷新并打包选手工作区（生成 contestant/ 与 contestant.zip）
python organizer/pack_contestant.py --force --zip

# 启动前工作区边界自检
python organizer/launch_contestant.py --workspace contestant --check-only

# 查看待评提交并执行正式评分（汇总写入 organizer/results/summary.json）
python organizer/score_all.py --list
python organizer/score_all.py <提交目录> --name <模型名>
```

> 练习模式（`--mem-allow-dev` 或 `--rift-ticks`）结果写入 `organizer/results/practice_summary.json`，不覆盖正式成绩单。Windows 正式评分需启用 AppContainer 与 Job Object。
