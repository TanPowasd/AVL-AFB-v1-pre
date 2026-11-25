# 裂陆纪元 tier bots：r8 校准（rift-bots-r8）

- 规则/引擎：`rift_era_rules_r8`，rift-core `1deca3f`；bots 来自 rift-bots `d37f254`，移植见分支提交。
- 方法：开发 runner 同局对比（不是正式分，没有影子局/沙箱）：
  `python3 -m organizer.rift_era.bots.runner --legacy-map --ticks 400 --seed S --seats super,advanced,medium,none,super,advanced,medium,none`，
  S = 1000/2000/3000，每档每局 2 席，n = 6；95% CI = 1.96·sd/√n（种子间与席位间差异）。
- 原始数据：`organizer/rift_era/validation/bots_r8_calibration_20261106.json`（逐席 V、支柱均值、决策耗时、文件 sha256）。
- 旧校准（r6/1e1ee85 时的 Super 34.5 vs Advanced 31.9 等）在 r8 下作废。

## 400 tick 短局

**加强前（rift-super-3 + r8 最小适配）**

| 档 | 平均 V | 95% CI | 决策 CPU ms/窗口 | 决策 CPU s/tick |
|---|---|---|---|---|
| super | 41.05 | ±0.16 | 0.56 | 0.00011 |
| advanced | 39.95 | ±0.13 | 0.28 | 0.00006 |
| medium | 36.32 | ±0.31 | 0.16 | 0.00003 |
| none | 24.55 | ±0.55 | — | — |

引擎模拟约 0.71 s/tick（8 席同局，两局并行占满 2 核）。Super 只领先 Advanced **1.10**。

**加强后（rift-super-4）**

| 档 | 平均 V | 95% CI | 决策 CPU ms/窗口 | 决策 CPU s/tick |
|---|---|---|---|---|
| super | 50.62 | ±0.09 | 1.49 | 0.00030 |
| advanced | 39.95 | ±0.13 | 0.29 | 0.00006 |
| medium | 36.32 | ±0.31 | 0.19 | 0.00004 |
| none | 24.55 | ±0.55 | — | — |

**二轮加强后（rift-super-5，当前分支头）**

| 档 | 平均 V | 95% CI | 决策 CPU ms/窗口 | 决策 CPU s/tick |
|---|---|---|---|---|
| super | 53.74 | ±0.33 | 0.61 | 0.00012 |
| advanced | 39.95 | ±0.13 | 0.11 | 0.00002 |
| medium | 36.32 | ±0.31 | 0.05 | 0.00001 |
| none | 24.55 | ±0.55 | — | — |

Super 较 `rift-super-4` 再提升 **+3.12**（较 `rift-super-3` 提升 **+12.69**），领先 Advanced **13.79**。

## 1200 tick（seed 1000，同席位）

| Super 版本 | Super | Advanced | Medium | none |
|---|---|---|---|---|
| rift-super-4 中间版 b51378e（无 RU 预算） | 49.91 | 38.13 | 23.81 | 20.11 |
| rift-super-4 | 51.21 | 38.16 | 23.74 | 20.11 |
| rift-super-5（当前分支头） | 56.63 | 38.16 | 23.77 | 20.10 |

`rift-super-5` 在 1200 tick 下两席分别为 `56.38`（`n0`）与 `56.87`（`n4`），较 `rift-super-4` 提升 **+5.42**，可持续性系数 `phi = 0.9995`。

## 改动带来的增益（seed 1000/2000/3000，Super 两席配对差）

| 步骤 | Super V (400t / 1200t) | 说明 |
|---|---|---|
| rift-super-3（r8 移植） | 41.05 / — | |
| + 世界池卖出超量库存、建材按需买入 | 46.01 / — | 工业 +32（主要是 NFA 与 eta=1），人口 −10（药品被卖光） |
| + 按净值开线（无排放正净值线满负荷，排放冶炼线由世界池供货） | 48.44 / — | 污染下降，人口 +6 |
| + 民生品买回 | 50.87 / — | 人口 +9 |
| + eta 分母口径的保留量 | 52.00 / — | 玻璃等不再 eta<1 |
| + RU 预算/药品配给（rift-super-4） | 50.62 / 51.21 | 短局约 −1，换长局不崩 |
| + 高阶级医用耗材满足度、精准库存上限、户籍现金倒推预算、电站错峰检修与备机预启动（rift-super-5） | 53.74 / 56.63 | 人口支柱满医用耗材 +6.5，工业支柱满 eta/NFA +3.1，长局消除 Tick 800 户籍现金枯竭与 Tick 1000 同步检修断电（phi=0.9995） |
