# 农场经营（Kaggriculture） · 组委会环境（`organizer/kaggriculture`）

- 选手公开规则源文档：`config/contestant_readme.md`（导出后为 `contestant/kaggriculture/README.md`）
- 独立选手引导与手册：`AGENTS.md`、`AI_README.md`、`handbook/{contest-rules,player-guide,game-facts}.md`（`contest-rules.md` 同时导出为 `FORMAT.md`）与聊天模式题面 `prompts/task.txt`
- 导出独立选手环境：在本目录运行 `python scripts/pack_contestant.py --force`（加 `--out <路径>` 导出新副本，加 `--zip` 打包），只刷新本题。全量导出见根目录 `README.md`
- 刷新聊天题面：改动 `AI_README.md` 或 `handbook/` 后运行 `python scripts/pack_prompt.py` 重新生成 `prompts/task.txt`
- 运行正式评分：在本目录运行 `python scripts/score.py`

## 正式评分 scoring_v2

`python scripts/score.py` 独立评测 `agents/submissions/*.zip`（兼容 `--bots` 指定 `.zip` / 目录 / `.py`），输出 `results/leaderboard.json`。规则引擎 `engine/rules.py` 严格对齐官方 Kaggle Kaggriculture 规则；正式配置与参考源码指纹在 `config/scoring_v2.json`；完整选手规则见 `config/contestant_readme.md`。不得修改冻结配置后仍把新旧成绩当成同一次评测。

```bash
# 打包多文件策略目录为提交压缩包（根目录含 main.py）
python scripts/make_submission.py agents/template --out agents/submissions/model_a.zip
# 正式：每次随机抽取场景，与固定对手池对战，多局取平均
python scripts/score.py --bots agents/submissions/model_a.zip
# 练习：缩短对局验证接口（结果明确标为非正式）
python scripts/score.py --bots agents/baselines/passive.py --seeds 1 --ticks 24 --out results/smoke_v2.json
```

默认抽取 64 个场景，可用 `--seed-count N` 增加样本。报告中的 `sampling.source = "os_csprng"` 标记随机源，`seeds` 保存完整抽样列表；使用 `--seeds <原列表>` 可以重放，重放标为非正式，避免挑选种子作为正式成绩。

报告保留逐局原始指标、分项、故障统计和按种子块重采样的 95% 区间。旧 `baseline.json` 等历史记录保持原口径；覆盖旧正式榜单前会自动生成带内容指纹的 `.legacy_v1.*.json` 备份。采样区间只覆盖当前冻结场景分布，不代表所有可能环境。

重复评测覆盖同名输出前，会把上一轮报告保存为带内容指纹的 `.scoring_v2.*.json`，保留该轮随机种子和平均成绩。

默认检测 CPU 与可用内存，预留系统余量后并行调度独立对局进程。多核至少预留 1 个、约 25% 的逻辑核心；内存不足时进一步降低并发或暂停启动新对局。`--jobs N` 也是受资源预算限制的期望上限。支持的平台降低 worker 优先级并设置亲和性。`execution` 记录资源预算、实际并发和内存等待情况；内存估算不是硬内存上限。

## 对局回放与可视化

使用 `scripts/record_replay.py` 录制 `k4u-arena-replay/1` 格式回放至 `replays/`，并用单文件离线播放器 `viz/player.html`（无外部 CDN 依赖）查看 10×10 棋盘、现金曲线、市场行情与逐帧动作：

```bash
# 录制单局并自动做引擎校验与同种子复算校验
python scripts/record_replay.py --a greedy --b passive --seed 11 --out replays
# 录制并交换座位
python scripts/record_replay.py --a agents/submissions/model_a.py --b greedy --seed 11 --swap
# 在组委会目录直接起本地 HTTP 服务播放（选手工作区禁用 --serve）
python scripts/record_replay.py --a greedy --b passive --seed 11 --serve
# 快速预览前 N 步或强制增量帧
python scripts/record_replay.py --a greedy --b passive --seed 11 --steps 240 --delta
```

- 录制回调位于每步结算后、计时区间外，不影响 `1s/步 + 60s 超时池` 判罚统计。
- `replays/` 中已生成样例回放（`replay_seed11_fw.json`），可直接拖入 `viz/player.html` 或以 `#src=/replays/replay_seed11_fw.json&t=430` 深链打开；`pack_contestant.py` 不会导出 `replays/` 中的文件。

## 辅助对战与脚本说明

- **选手工具（随选手包导出）**：
  - `scripts/make_submission.py`：把策略目录（根目录含 `main.py`）或单 `.py` 打包为 `.zip` 并做安全校验与 3 步冒烟；
  - `scripts/spar.py`：对阵公开基线（`passive` / `random` / `greedy` / `champion`）并输出 `scoring_v2` 参考分（兼容 `.zip` / 目录 / `.py`）；
  - `scripts/run_match.py`：单场指定种子/镜像自搏（`--b --self`），选手区禁用 `--unrestricted`；
  - `scripts/record_replay.py` + `viz/player.html`：回放录制与离线单文件播放。
- **打包与题面工具（仅组委会目录）**：
  - `scripts/pack_contestant.py`：导出干净选手包（含完整文档、手册、`prompts/task.txt`、公开基线、`champion` 运行时与播放器，不泄露私有参考对手、评分配置或回放）；
  - `scripts/pack_prompt.py`：从 `AI_README.md`、`handbook/`、`agents/starter.py` 与 `engine/rules.py` 拼装聊天模式完整题面 `prompts/task.txt`。
- **辅助分析（不进入正式 `scoring_v2` 百分制）**：
  - `scripts/duel.py`：双提交一键对抗（默认 8 种子 × 双座位 = 16 局，50% 得分率时自动加赛 8 种子 16 局，可加 `--no-tiebreak` 关闭）；
  - `scripts/run_league.py`：多提交全轮环 Elo 联赛（依赖 `score/elo.py`），输出 `leaderboard.md` 与 `league.json`。
- **单测**：`python -m unittest discover -s tests` 覆盖回放编码一致性（`tests/test_replay.py`）与选手包导出纯净度/隔离防线（`tests/test_contestant_kit.py`）。
- **旧赛事脚本**：`scripts/publish_round.py`、`scripts/make_eval_docs.py`、`scripts/export_exec_data.py`、`scripts/sync_web_player.py` 来自独立农场赛，未随 `scoring_v2` 更新；`scripts/launch_contestant.py`、`scripts/contestant_access.py` 仅支持单题工作区，见 `../OPEN_ISSUES.md` 的 LAUNCH-1。
