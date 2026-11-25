# RSIdemo 当前验证优胜版本

本目录选用 **cfr**，来自 `elite_cfr_anytime_future_thirty_r40` 的冻结源码。
在该轮 30 个共同种子、双座位、每版 60 局中，按胜率优先、平均亏损次之排名第一：
**37胜 / 0平 / 23负，胜率61.67%，平均亏损-505.52。**
负数表示平均领先。平均亏损 = 对手终局现金 − 我方终局现金。

“最强”范围为本轮完整共同面板中的已验证候选；不是全局最优或稳定击败所有对手的保证。
早期不同种子的高胜率不能直接与本轮排名混合。

## 算法

- 原始 elite 底座、D15 切换、启发式联合结构提议、局部组合优化和完整路线认证
- 复用等价 DP 状态，降低重复计算
- 公开状态响应博弈＋CFR（256次迭代，风险系数0.5）

完整参数位于 [RSIdemo/params.json](RSIdemo/params.json)，已直接接入默认入口。
ADP/PDS、TD(λ)价值排名、网络流和价格信号没有取得本轮晋级证据，未启用。

## 运行

需要 Python 3.10+。在线代码使用标准库；包含原压缩包的原生加速内核与 Python 回退。
原生内核首次运行会在本目录下生成缓存，支持平台上自动启用。

在本目录导入：

```python
from main import agent
action = agent(observation, configuration)
```

也可执行 `python main.py`，每行输入 `{"observation": ..., "configuration": ...}`，每行输出一个动作JSON。
提交压缩包为 `submission.tar.gz`，入口为压缩包根目录的 `main.py`。
压缩包只含运行代码、参数、来源和本文；本目录的 `evidence/`、`selection.json`、`validation/` 是包外评测凭据。
本目录在线运行不依赖原工作区、对手源码、训练回放或实验数据。

## 完整比较与时限

对手是用户原始 `submission.tar.gz`，SHA256：
`cab6aa76e5376113028828aade4293bb905e91708ee0dcf564ae3100d3a74461`。
`ramp` 未作为对手。评测引擎为 `bundled`；开发结果不等于线上天梯成绩。
每局719次决策回调，双方完成；所有版本逐局核对参数、源码哈希、对手哈希和现金差。
本轮我方每步1秒＋整局累计额外60秒；优胜版本最大累计额外用时 **10.368秒**。
截止是协作式检查，机器负载和平台会影响搜索次数与时间。

| 版本 | 有效局 | 胜/平/负 | 平均亏损 | 最大累计额外用时 |
|---|---:|---|---:|---:|
| cfr | 60 | 37/0/23 | -505.52 | 10.368s |
| future | 60 | 33/0/27 | -524.70 | 11.369s |
| base | 60 | 29/0/31 | 263.95 | 9.021s |
| both | 60 | 29/0/31 | 464.10 | 39.511s |
| anytime | 60 | 24/0/36 | 472.53 | 38.083s |

## 文件与复核

- `main.py`、`RSIdemo/`：可独立运行的代码、参数和来源说明。
- `manifest.json`：包内逐文件 SHA256；`selection.json`：选择来源和冻结源码对应关系。
- `evidence/panel_audit.json`：完整面板审计、每局原始结果哈希、激活指标。
- `evidence/panel_manifest.json`、`evidence/panel_report.json`：种子、所有参数与原始报告。
- [validation/README.md](validation/README.md)：独立导入和完整对局验证摘要；同目录保存原始JSON。

代码直接复制自获胜实验的冻结 runtime；评测专用的数据采集器不进入提交包。
源码参考与改动归属见 `RSIdemo/REFERENCE_NOTES.md` 和 `RSIdemo/elite_base/PROVENANCE.json`。
