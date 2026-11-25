# 裂陆纪元 tier bots（正式引擎对手）

三档对手把原型（`../designs/prototype/`）的**策略思想**落到正式引擎的观测与 06 命令上，
不照抄原型 API。三档相互独立：不互相 import、不共享代码文件；每档目录就是一个合法的选手提交
（zip 根目录含 `main.py`，只用 Python 标准库，`sdk.py` 是 `organizer/rift_era/sdk.py` 的原样副本）。

| 档 | 目录 | 是否公开给选手 | 评分对手池 | 原型对应 |
|---|---|---|---|---|
| 中等 Medium | `medium/` | **公开**（练习对手，可直接作为样例提交） | 否 | `prototype/medium.py`（广度学者） |
| 高级 Advanced | `advanced/` | **公开**（练习对手） | 否 | `prototype/advanced.py`（固定系数 + 实验室突击） |
| 超级 Super | `super/` | **不公开** | 保留对照（旧单对手基准） | `prototype/ref5.py`（`tiers.Super`） |
| 工业霸权 Hegemon | `hegemon/` | **不公开** | **是（多风格评分池：重工业与多枢纽实体资产）** | 独立 r9 设计 |
| 商贸套利 Arbiter | `arbiter/` | **不公开** | **是（多风格评分池：世界池点差、城邦商贸与流动性）** | 独立 r9 设计 |
| 清洁科技 Verdant | `verdant/` | **不公开** | **是（多风格评分池：极速科研落地、零排放能源与反手交易）** | 独立 r9 设计 |
| SimpleBot | `../sdk.py` | 公开 | 否 | 仅开发参考，强度≈无命令 |

评分池支持任意 `N >= 1` 位不公开对手（目录 `<id>/main.py` 或单包 `<id>.zip`）：新增众包对手放入 `bots/` 并加入 `config/scoring_v2.json["opponents"]` 后，运行一行命令重冻：
`python3 organizer/rift_era/scripts/freeze_profile.py --revision <new-rev>`。

Medium / Advanced 已冻结（r8 起 `rift-medium-3` / `rift-advanced-3`：相对 `-2` 只跳过开局 P033 线的
target_stock 设置——r7 起该线免 A04 运行，但改线须 A04，原来每窗口都被 `ERR_TECH_LOCKED` 拒绝）；
加强 Super 不改动它们。r8 校准见 `CALIBRATION_r8.md`。

## 每档策略要点

### Medium（`rift-medium-1`，“广度学者 + 库存上限”）
1. 研究：开局那座基础实验室研究当前可研究的最低阶科技（同阶按编号），standard 档；不新建实验室。
2. 生产：每 10 个窗口按（户需 + 现有产线满负荷投入）估一次需求，给每条非电站/非实验室产线设
   `target_stock_mU = 8 周期需求`，库存够了就自动停产（BASE 持续计划）。
3. 民生补短：首都食品低于下限时在本地市场挂买单。
4. 不调税、不建设、不管电站，不碰金融/外交/军事。

### Advanced（`rift-advanced-1`，“固定系数计划 + 实验室突击”）
1. 生产：每 4 个窗口线性叠加需求，所有产线目标库存 = 5 周期需求（统一系数）。
2. 研究：固定冲刺路线 L01→I02→C01→C02→R02→E02→R01→R04→I01→I03→C03→R05→C05→R07，之后广度补齐；
   按工程师容量（3 名/座）逐座新建 S1 基础实验室。
3. 财政：税率降到 10%。
4. 投资：钢/水泥超过固定储备的部分扩建仓容（转成资产账面 K_infra）。

### Super（`rift-super-5`，r8，“世界池 eta 管理 + 户籍现金/电站检修闭环 + 民生 > 投资 > 实验室”，评分集）
r8 新增（在下列 rift-super-3 骨架之上）：
- **世界池 eta 管理**：每周期把超过约 2 周期用量（按引擎 eta 分母口径估计：户需 + 产线实测开工率×负荷的投入
  + 电站耗煤水 + 验证/建设拉动）的库存卖给世界池（买卖同价 10×w_cat），产出物 chi 保持在 6 以下（eta=1），
  卖得的 RU 记入 NFA。
- **按净值开线（E2 考点）与户籍现金倒推预算**：目录增加值 − 电力（1.2/电）− 排放（35/单位污染）为正的线（农业/食品/民用品/机件/
  玻璃/实验耗材）满负荷，余量进世界池；排放型冶炼线（P001/P002/P005/P007）在世界池有货、RU 储备充足且首都户籍现金
  （`household_cash_mU`）按剩余周期倒推充裕时停产，钢/水泥/铜/化工/机件从池买入；若长局户籍现金接近安全预算下限，
  自动恢复冶炼线发薪补流，避免户籍池枯竭导致民生消费断供。
- **民生补缺与双阶医疗**：药品（开局 P033 只 0.2 批）、医用耗材（`medical_supplies`，在户籍现金预算允许时配给，使
  `med = medicine + advanced_medicine` 达 2.0 满值）、食品、民用品、水、煤、粮低于安全线时优先从世界池买回。
- **电站错峰检修与备机预启动**：开局通过 `logistics.plant_maintenance` 将 5 座燃煤电站的计划检修周期错开
  `(+20, +12, +4, -8, -16)`，消除 Tick 1000 全网同步检修断电；在运行电站进入检修前 1 周期或跳闸时自动唤醒离线备机。

### Super（`rift-super-1`..`-3` 骨架）
1. 民生：需求 = 户需 + 下游产线投入 × **实测开工率 EMA**（下游停着就不再为它囤货）+ 实验室/验证品/
   建设需求；目标库存 3 周期（食品 5、民用品/药品/水 4），把 `eta_sink` 维持在高位，工业 VA 由负转正。
   税率 5% + 有人口分区地方自治留成 50%，支持度拉满（合法性）。
2. 电力：按实测出力滞回关停/重启燃煤电站（避免 40% 最低出力白烧燃料与污染）。
3. 投资：只用“扣除在建工程未领材料 + 储备”后的余量建设；顺序 实验室 → 学院公共槽 → 试验包机械厂
   → 制药厂（A04+L01）→ 修复剂化工厂（V02+L01）→ 住宅区（槽位/住房）→ 仓容；首都污染低于阈值时
   拉动冶钢/水泥线为下一模块生产（账面长期累积），高于阈值只消化存量。
4. 实验室：工程师容量内补齐；路线 I02→C01→L01→V01→R02→V02→A04→R01→F01→…，open 档 +10%；
   理论完成且验证品够才验证，缺验证品就先研究别的；R02 后装学院、持续把技工培训成工程师。
5. 后期民生：A04 后开 S2 制药厂（P033），补开局药品耗尽的缺口；V02 后生产修复剂并每周期投放首都分区。

## 运行方式（开发用，永不计正式分）

引擎 CLI 的 `match --bot` 只认 `none/simple`，本分支不改 engine，因此用 `bots/runner.py`：

```bash
# 8 席位混合同局（逗号分隔 8 个：none/simple/medium/advanced/super；只写一个则 8 席同档）
python3 -m organizer.rift_era.bots.runner --ticks 400 --legacy-map \
    --seats super,advanced,medium,simple,none,super,advanced,medium --progress 40 --out run.json
# 正式地图（先跑 150 tick 世界验收，约 100 s）
python3 -m organizer.rift_era.bots.runner --ticks 400 --seats super,advanced,medium,simple,none,super,advanced,medium
# 真实 IPC：每席 `python3 main.py` 子进程 + 引擎 StrategySession/MatchDriver（无内核沙箱，仅开发）
python3 -m organizer.rift_era.bots.runner --ticks 40 --legacy-map --seats super,advanced,medium,none,none,none,none,none --transport pipe
```

打包成选手提交：`cd organizer/rift_era/bots/medium && zip -r ../medium.zip main.py bot.py sdk.py`，
即可交给 `strategy-match --submissions`（需要 cgroup 沙箱环境）。
