# 选手公开赛制

本文件仅包含公开规则，不包含其他选手的成绩、日志或历史战报。完整规则与数值细节见 `README.md`、`handbook/` 与 `engine/rules.py`。

- **游戏**：Kaggriculture，1v1 农场经营，`30` 天 × `24` 小时，`episodeSteps = 720`（实际执行 `step = 0 .. 718` 共 `719` 步；第 `29` 天无日终结算，未售库存不折算现金）。规则引擎 `engine/rules.py` 与官方 Kaggle Kaggriculture 精确一致。
- **胜负与正式计分**：
  - 终局比较双方现金 `money`；在本地单场对抗与 PvP 赛制中，双方现金差不超过较高现金的 `1%` 时判平；若常规 8 种子（16 局）得分率恰好 50.0%，按 `config/format.json` 的 `tiebreakSeeds` 自动加赛 8 种子（+16 局）。
  - 正式单模型评分采用 `scoring_v2` 百分制（满分 `100`），固定对阵 `3` 个隐藏参考对手，默认随机抽取 `64` 个场景并交换座位（共 `384` 局）。正常结束的单局按 `50 + 50 * tanh((money_T - money_R) / 80096.26)` 连续计分并等权平均；技术判负优先（本方判负得 `0` 分，仅对手判负得 `100` 分，双方均判负各 `0` 分）。
- **交付**：单个 **UTF-8 `.zip` 压缩包**（根目录必须包含 `main.py`，可附带任意辅助 `.py` 子模块与包内只读数据文件；压缩后 `<= 64 MiB`，解压后 `<= 256 MiB`，条目数 `<= 2048`）。综合工作区最终统一提交路径为 `../submissions/kaggriculture.zip`，独立工作区默认路径为 `agents/submissions/my_agent.zip`（可用 `scripts/make_submission.py` 打包并校验；本地调试工具同时兼容含 `main.py` 的目录与单文件 `.py`）。
- **入口**：`main.py` 定义模块级 `act(observation) -> dict`，或类 `Agent` 实现 `act(self, observation) -> dict`（加载与初始化限时 `60` 秒）。
- **依赖与运行环境**：允许完整 Python 3.10+ 标准库、导入包内子模块及只读读取包内数据文件；取消单文件 9 模块静态扫描限制。每局在独立子进程中加载并在正式评测时叠加系统级沙箱，禁止联网、禁止访问包外文件系统、内存上限 `1 GiB`。
- **限时**：每步基础时限 `1.0` 秒，超出部分消耗每局 `60.0` 秒超时池（`remainingOverageTime`）；超时池耗尽后任意一步超 `1.0` 秒立即终止并判超时负。
- **异常与结构错误**：单局累计超过 `30` 次异常或结构错误（含 `hands` 长度不符、未知 `op`、参数类型错误、子进程退出）立即判负。
- **本地自测与回放**：公开陪练仅限 `passive`、`random`、`greedy`、`champion`（正式评分使用独立的 3 个隐藏参考对手，不含本地公开陪练）；允许自身镜像自搏。可用 `scripts/record_replay.py` 录制 `k4u-arena-replay/1` 回放并在离线单文件播放器 `viz/player.html` 中查看（选手工作区禁用 `--serve`）。
- **工作区边界**：私有产出仅保存在当前工作区的提交目录、`results/`、`replays/`、`logs/`。禁止读取其他工作区、正式评测目录、其他选手提交或成绩，不得跨选手对战。正式评测由裁判方在正式评测环境中运行；选手工作区禁止使用 `--unrestricted`，禁止修改引擎、裁判、脚本、基线与配置文件。

规则源码以 `engine/rules.py` 为准，接口与数值见 `handbook/player-guide.md` 与 `handbook/game-facts.md`。参赛 AI 应直接读入口、编写代码、打包自测并交付。
