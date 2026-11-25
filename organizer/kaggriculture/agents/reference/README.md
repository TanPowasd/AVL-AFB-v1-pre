# 参考对手（仅组委会环境）

## 正式评分对手池（`config/scoring_v2.json`）

当前正式评分（`profile_revision = 2026-11-21-kaggriculture-3style-v3`）使用 3 个独立风格的私有强参考对手（均不随选手包导出）：

1. `agronomy_arbitrage.py`：精密农艺与仓储/择时套利型（多周期动态规划作物轮作、精确浇水/施肥窗口对齐、小规模有机肥支撑圈与高弹性作物仓储套利、极角扇区 2-opt 路由）。
2. `ranch_compounder.py`：畜牧复利与资本扩张型（精确曲线年金 IRR 扩群、同心圆畜棚布局与动物原位库存管理、外圈自供饲料小麦闭环与平滑出货、象限蛇形链批量路由）。
3. `tactical_counterplay.py`：对手推断与市场博弈反制型（实时解析对手公开棋盘与隐含仓库推断、基于相对现金差目标的生态位反选、0 号槽位锁步抢跑与城镇稀缺拐点狙击、截止期优先插入+跨工位重排路由）。

## 公开陪练对手

- `champion.py`（策略来自 `engine/champion/`，RSIdemo 冻结包）保留为公开陪练对手，随选手包分发，选手可用 `scripts/spar.py <提交> champion` 对练；不再进入正式 `scoring_v2` 对手池。
- 历史参考文件（`hist_carbon.py`、`hist_opus.py`、`livestock_compound.py`、`crop_rotation_strong.py`）仅留作对照分析，均不在正式评分对手池中，也不随选手包导出。

## 扩展外部众包对手（支持任意 N >= 1）

评分、校准与冻结链路均不硬编码对手数量。后续加入外部众包强对手（单文件 `.py`、多文件 `.zip` 压缩包，或根目录含 `main.py` 的目录）：

1. 将新对手放入 `agents/reference/`（不要加入 `scripts/pack_contestant.py` 的 `PUBLIC_MAP`）；
2. 在 `config/scoring_v2.json` 的 `opponents` 数组中登记 `{"id": "<名称>", "path": "agents/reference/<文件或目录>"}`；
3. 运行单命令完成目录确定性打包（`--pack-dirs` 会将多文件目录打包为同名 `.zip` 并通过 `loader.check_archive` 校验、同步更新 `opponents[].path`）、独立子进程校准、更新 `config/format.json` 与重冻结校验：

```bash
python organizer/kaggriculture/scripts/recalibrate.py --pack-dirs --freeze --revision <新版本号>
```
