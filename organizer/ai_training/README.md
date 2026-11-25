# 前沿实验室（Frontier Lab） · 组委会环境（`organizer/ai_training`）

- 选手公开规则源文档：`config/contestant_readme.md`（导出后为 `contestant/ai_training/README.md`）
- 导出独立选手环境：在本目录运行 `python scripts/pack_contestant.py --force`，只刷新本题。全量导出见根目录 `README.md`
- 运行正式评分：在本目录运行 `python scripts/score.py`

## 正式评分 scoring_v2（`.zip` 多文件包提交）

`python scripts/score.py` 独立评测 `agents/submissions/*.zip`（兼容显式传入 `.py` / 目录），输出 `results/leaderboard.json`。正式配置与参考源码指纹在 `config/scoring_v2.json`；完整选手规则见 `config/contestant_readme.md`。不得修改冻结配置后仍把新旧成绩当成同一次评测。

```bash
# 打包多文件策略目录并做安全 + 24 tick 冒烟校验
python scripts/make_submission.py agents/template --out agents/submissions/model_a.zip
# 正式：每次随机抽取场景，与固定对手池对战，多局取平均
python scripts/score.py --bots agents/submissions/model_a.zip
# 练习：缩短对局验证接口（结果明确标为非正式）
python scripts/score.py --bots agents/baselines/blank.py --seeds 1 --ticks 24 --out results/smoke_v2.json
```

`config/scoring_v2.json` 里的 `world_salt` 只用于正式评分，选手本地固定用 `practice`，同一种子在两边生成的世界不同。

默认抽取 64 个场景，可用 `--seed-count N` 增加样本。报告中的 `sampling.source = "os_csprng"` 标记随机源，`seeds` 保存完整抽样列表；使用 `--seeds <原列表>` 可以重放，重放标为非正式，避免挑选种子作为正式成绩。

报告保留逐局原始指标、分项、故障统计和按种子块重采样的 95% 区间。旧 `baseline.json` 等历史记录保持原口径；覆盖旧正式榜单前会自动生成带内容指纹的 `.legacy_v1.*.json` 备份。采样区间只覆盖当前冻结场景分布，不代表所有可能环境。

重复评测覆盖同名输出前，会把上一轮报告保存为带内容指纹的 `.scoring_v2.*.json`，保留该轮随机种子和平均成绩。

默认检测 CPU 与可用内存，预留系统余量后并行调度独立对局进程。多核至少预留 1 个、约 25% 的逻辑核心；内存不足时进一步降低并发或暂停启动新对局。`--jobs N` 也是受资源预算限制的期望上限。支持的平台降低 worker 优先级并设置亲和性。`execution` 记录资源预算、实际并发和内存等待情况；内存估算不是硬内存上限。

## 回放录制与离线可视化

- 回放编码与校验模块：`engine/replay.py`（格式 `afb-ai-training-replay/1`，含 `build_replay` / `save_replay` / `verify_against_engine` / `frame_digest` / `sha256_path`）
- 回放录制脚本：`scripts/record_replay.py`（导出至选手包，支持 `.zip` / 目录 / `.py` 及 `--a`、`--b`、`--solo`、`--seed`、`--seeds`、`--swap`、`--ticks`、`--every`、`--out`、`--no-verify`、`--serve`；选手工作区内禁用 `--serve` 与 `--unrestricted`）
- 离线单文件播放器：`viz/player.html`（零外链，支持拖拽本地 JSON 与 `#src=/replays/...&t=...` 深链，展示双方 KPI 卡片、估值与现金轨迹、前沿能力 `C`、算力规模、API 市场份额堆叠图与可筛选事件日志）

```bash
# 录制 6 个月快速预览回放并做确定性复算校验
python scripts/record_replay.py --a scaling --b merchant --seed 1 --ticks 4380 --out replays
# 录制单人赛回放
python scripts/record_replay.py --a scaling --solo --seed 1 --ticks 4380 --out replays
# 组委会环境本地起 HTTP 服务查看回放（选手工作区禁止）
python scripts/record_replay.py --a scaling --b merchant --seed 1 --ticks 4380 --serve --port 8777
```

## 参考对手池扩展与一键重校准（支持任意 N 个对手）

正式对抗赛对手池由 `config/scoring_v2.json` 的 `opponents` 数组驱动（不限个数，兼容单文件 `.py` 与 `.zip` 包；若新增众筹对手是含 `main.py` 的多文件目录，可传 `--pack-dirs` 自动确定性打包为 `.zip`）：

```bash
# 修改 config/scoring_v2.json["opponents"] 后，一键在独立校准种子上重算 margin_scale、同步 format.json/VALUE_SCALE 并重冻结哈希
python scripts/recalibrate.py --revision 2026-11-25-crowdsourced-v5 --jobs 8
# 若含未打包的多文件目录对手：
python scripts/recalibrate.py --pack-dirs --revision 2026-11-25-crowdsourced-v5 --jobs 8
```

## PvP 对战与 ELO 联赛（仅组委会环境，不导出）

- ELO 计分模块：`score/elo.py`（基于 `VALUE_SCALE = 12710.23 M` 计算胜差倍率、50% 得分率加赛种子与 Markdown 排行榜渲染）
- 1v1 PvP 对战：`scripts/duel.py`（自动发现 `agents/submissions/` 下的 `.zip` / 目录 / `.py` 策略，支持显式路径、双座位交换、50% 得分率自动加赛与 `--record-replay`）
- 全轮环 ELO 联赛：`scripts/run_league.py`（支持多个路径或通配符，输出 `results/<timestamp>/{leaderboard.md, league.json}`）

```bash
python scripts/duel.py agents/baselines/scaling.py agents/baselines/merchant.py --seeds 2 --ticks 4380 --record-replay
python scripts/run_league.py agents/baselines/*.py --seed-count 2 --ticks 4380
```

## 独立选手包文档与聊天题面打包

- 选手包导出 `AGENTS.md`、`AI_README.md`、`FORMAT.md`、`handbook/{contest-rules,player-guide,game-facts}.md`、`prompts/task.txt`、`agents/template/*`、`agents/submissions/README.md`、`engine/{archive,pkg_entry,engine,world,solo_npc,policy,replay}.py`、`scripts/{make_submission,spar,run_match,record_replay}.py` 与 `viz/player.html`
- 修改 `AI_README.md` 或 `handbook/*.md` 后运行 `python scripts/pack_prompt.py` 刷新聊天模式题面 `prompts/task.txt`

## 自动化测试

```bash
python tests/test_engine.py
python -m unittest discover -s tests -v
```
