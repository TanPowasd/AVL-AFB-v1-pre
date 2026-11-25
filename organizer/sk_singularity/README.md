# 《双符奇点》（sk_singularity）维护与评测说明

本目录为《双符奇点》的完整题库与评测环境。选手可见题面位于 `config/contestant_readme.md`。

## 结构概览

- `engine/sk_core.hpp`、`engine/judge_cli.cpp`：白盒 C++20 高速 Hash-Consed 记忆化精确最左最外归约引擎与常驻 `--batch` 裁判 CLI。
- `config/practice_suite.json`：14 个公开练习测试点（覆盖标准 SK、强制残骸与 5 套公开盲规则）。
- `hidden/schedule.py`：512 种子动态盲规则/残骸生成器与调度器（8 类正交家族 × 64 测/家族，均匀覆盖 $N \in \{14, 18, 21, 24\}$，OS CSPRNG 512 个互异 64-bit 种子 + 50% `S <-> K` 精确同构翻转 + 严格排除公开练习规则）；`hidden/official_suite.json` 保留供洛谷静态导出工具兼容使用。
- `agents/starter.cpp`、`agents/baselines/beam_evo.cpp`：公开起步与中阶基线。
- `agents/reference/algebra_synth.cpp`：隐藏强参考求解器（用于正式评测现场动态标定每测专属 `L_ref` 锚点，不随选手包导出）。
- `luogu/`：洛谷兼容独立题面、testlib SPJ 校验器与打包脚本。
- `viz/player.html`、`replays/replay_demo.json`：零依赖交互式归约轨迹与 DAG 共享爆炸可视化回放器。

## 常用维护命令

```bash
# 重新校准静态练习/洛谷 L_ref 锚点（仅在规则或引擎变更时执行）
python organizer/sk_singularity/scripts/calibrate_anchors.py --write --workers 6

# 录制示范回放并冻结 config/scoring_v2.json
python organizer/sk_singularity/scripts/freeze_profile.py

# 正式 512 测动态隐藏集评测（默认自适应并发；加 --quick 可跑 32 测冒烟）
python organizer/sk_singularity/scripts/score.py organizer/sk_singularity/agents/baselines/beam_evo.cpp

# 导出并纯净检查选手工作区
python organizer/sk_singularity/scripts/pack_contestant.py --force

# 运行完整单元测试
python -m unittest discover -s organizer/sk_singularity/tests -v
```
