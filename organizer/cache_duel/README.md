# 缓存攻防（Cache Duel） · 组委会环境（`organizer/cache_duel`）

- 选手公开规则源文档：`config/contestant_readme.md`（导出后为 `contestant/cache_duel/README.md`）
- 独立选手包指引：`AGENTS.md`、`AI_README.md`、`handbook/{contest-rules,player-guide,game-facts}.md`、`prompts/task.txt`（由 `python scripts/pack_prompt.py` 生成）
- 导出独立选手环境：在本目录运行 `python scripts/pack_contestant.py --force`，只刷新本题。全量导出见根目录 `README.md`
- 运行正式评分：在本目录运行 `python scripts/score.py`

## 正式评分 scoring_v2

`python scripts/score.py` 独立评测 `agents/submissions/*.py`，输出 `results/leaderboard.json`。正式配置与参考源码指纹在 `config/scoring_v2.json`；完整选手规则见 `config/contestant_readme.md`。不得修改冻结配置后仍把新旧成绩当成同一次评测。

```bash
# 正式：每次随机抽取场景，与固定对手池对战，多局取平均
python scripts/score.py --bots agents/submissions/model_a.py
# 练习：缩短对局验证接口（结果明确标为非正式）
python scripts/score.py --bots agents/baselines/lru.py --seeds 1 --ticks 24 --out results/smoke_v2.json
```

默认抽取 64 个场景，可用 `--seed-count N` 增加样本。报告中的 `sampling.source = "os_csprng"` 标记随机源，`seeds` 保存完整抽样列表；使用 `--seeds <原列表>` 可以重放，重放标为非正式，避免挑选种子作为正式成绩。

报告保留逐局原始指标、分项、故障统计和按种子块重采样的 95% 区间。旧 `baseline.json` 等历史记录保持原口径；覆盖旧正式榜单前会自动生成带内容指纹的 `.legacy_v1.*.json` 备份。采样区间只覆盖当前冻结场景分布，不代表所有可能环境。

重复评测覆盖同名输出前，会把上一轮报告保存为带内容指纹的 `.scoring_v2.*.json`，保留该轮随机种子和平均成绩。

默认检测 CPU 与可用内存，预留系统余量后并行调度独立对局进程。多核至少预留 1 个、约 25% 的逻辑核心；内存不足时进一步降低并发或暂停启动新对局。`--jobs N` 也是受资源预算限制的期望上限。支持的平台降低 worker 优先级并设置亲和性。`execution` 记录资源预算、实际并发和内存等待情况；内存估算不是硬内存上限。

## 回放录制与离线可视化

`engine/replay.py` 在不修改冻结文件 `engine/engine.py` 的前提下录制 `afb-cache-duel-replay/1` 回放，同步记录防守方与 **Belady OPT 离线最优**的逐步命中与驱逐轨迹及周期检查点，并支持与 `engine.play()` 同种子复算校验。

```bash
# 录制双向回放并复算校验（输出至 replays/）
python scripts/record_replay.py --a lru --b fifo --seed 11 --config 0 --swap

# 录制并启动本地 HTTP 服务，在浏览器打开 viz/player.html 直接播放（选手工作区自动禁用 --serve）
python scripts/record_replay.py --a agents/submissions/model_a.py --b marking --seed 11 --serve --port 8777
```

离线单文件播放器位于 `viz/player.html`（零外链），支持拖拽 JSON 或 `#src=/replays/...&t=...` 定位播放，提供防守方 vs Belady OPT 实时缓存并排视图、滚动访问磁带、累计缺失曲线、实时竞争分/滑动命中率曲线与 Key 访问频次分布。

## 单场对抗、PvP 决斗与 ELO 轮环联赛

- **单场自测（随选手包导出）**：
  ```bash
  python scripts/run_match.py --a lru --b fifo --swap --config-idx 0 --seeds 11,23
  ```
- **组委会 1v1 PvP 对战（仅组委会环境，自动读取 `agents/submissions/*.py` 两个文件或显式传参，50% 得分率自动加赛，支持 `--record-replay`）**：
  ```bash
  python scripts/duel.py agents/submissions/model_a.py agents/submissions/model_b.py --seeds 4 --record-replay
  ```
- **组委会全轮环联赛 + ELO 排行榜（仅组委会环境，`score/elo.py`）**：
  ```bash
  python scripts/run_league.py agents/baselines/*.py agents/submissions/*.py --seed-count 2
  ```
  结果输出至 `results/<时间戳>/{leaderboard.md, league.json}`。

## 题面打包与自动化测试

```bash
# 重新生成聊天模式完整题面 prompts/task.txt
python scripts/pack_prompt.py

# 运行引擎与回放/导出/ELO 自动化测试
python tests/test_engine.py
python -m unittest discover -s tests
```
