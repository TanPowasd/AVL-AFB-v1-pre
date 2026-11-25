# engine/rules.py 来源声明

- 复制自： C:/Users/TanPowasd/Desktop/df/kaggriculture/RSIdemo/rules.py（2026-09-19 版，未改动一字节）
- 该文件头注明：Adapted from joint-adaptive-fast-d15 package；
  SOURCE_SHA256 = 'bc8a54879ef02c7ea64b8b333d6a976f0ea65c4949149d01f463f23bccee653e'
  （指其上游：Kaggle kaggle-environments 安装的 kaggriculture 规则源码）
- 验证状态：与真实引擎逐步对拍通过（agents_lab/sim/fast_env.py 文件头与 tests/test_fast_env.py 记录）。
- 本仓库对 rules.py 的期望：只读引用。它是冻结文件，修改后必须更新评分配置。
- 与上游的差异：2026-11-05 起除草和商店解锁使用独立随机流（`_spawn_weeds`、`_end_of_day`），其余规则未改。

| 文件 | SHA256（本地副本，入库时计算） |
|---|---|
| engine/rules.py | 见 `config/scoring_v2.json` 的 `frozen_files` |

Python 要求：>= 3.10（bundled runtime 亦可）。
