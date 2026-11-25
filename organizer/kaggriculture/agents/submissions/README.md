参赛 AI 推荐在 `agents/submissions/` 下以多文件目录（根目录含 `main.py`）开发，并用 `python scripts/make_submission.py <目录> --out ../submissions/kaggriculture.zip` 打包提交（独立模式可输出到 `agents/submissions/my_agent.zip`）。

只读取和改进自己的提交，不得读取或覆盖其他选手文件。自测使用 `scripts/spar.py` 提供的四个公开陪练（`passive`、`random`、`greedy`、`champion`）。
