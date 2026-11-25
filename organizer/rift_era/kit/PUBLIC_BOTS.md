# 公开练习对手：Medium 与 Advanced

这两个目录是官方公开练习对手的**原样拷贝**（`main.py` + `bot.py` + `sdk.py`，`sdk.py` 与选手包根目录的 `sdk.py` 逐字节相同）。
它们相互独立实现，只用标准库，只读开局目录和本国过滤观测，每个目录本身就是一个合法提交。

| 目录 | 画像 | 强度 |
|---|---|---|
| `medium/` | 广度学者：开局实验室按最低阶科技研究；产线按“户需 + 下游满负荷投入”设 8 周期目标库存；首都缺粮就买；仓库快满扩仓；户需逼近产能补厂 | 练习下限 |
| `advanced/` | 固定冲刺科研路线 + 按工程师容量逐座新建实验室；统一 5 周期目标库存；税率 10%；多余钢/水泥扩仓 | 比 Medium 强 |

用途：

1. **练习对手**：`python3 scripts/local_match.py match --seats agents/template,advanced,...` 或 `score --opponent advanced`。
2. **起点**：复制一份 `agents/advanced/` 改成你自己的策略，再用 `scripts/make_submission.py` 打包。

说明：

- 它们**不在评分对手池里**。正式计分在不公开的多风格强评分对手池（当前 3 位不同风格强对手按确定性 8 席混编轮换）中进行对比（见 README“计分”），所以本地对 Advanced 的练习分只说明趋势，不等于正式分。
- **自测对手提示（重要）**：一局共有 8 个国家席位，而选手包仅附带有限的公开练习对手，无法凑齐 7 个各具特色的强对手。建议**自行编写并维护多个不同风格（如工业扩张、科研突击、贸易金融、军工防御等）或历史迭代版本的自制对手 bot**，用 `scripts/local_match.py match --seats my_bot,opp1,opp2,opp3,opp4,opp5,opp6,opp7` 或 `score --opponent <自制对手目录/.zip>` 进行充分的多元对抗自测。
- 版本号写在各自 `bot.py` 的 `VERSION`；版本更新时会重新导出选手包，`manifest.json` 里的哈希随之变化。
