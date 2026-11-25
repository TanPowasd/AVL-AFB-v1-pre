# 引擎版本与指纹

| 项 | 当前值 | 来源 |
|---|---|---|
| 引擎版本字符串 | `rift_era_engine_0.4.0` | `engine/__init__.py` 的 `ENGINE_VERSION` |
| 规则版本 | `rift_era_rules_r9`（冻结，决策 0016） | `designs/data/runtime_rules.json` 的 `version` |
| 引擎指纹 `engine_hash` | `ad750c9960693f53d98f982878b103027a909d24a49637711d36ac797e82fc45` | `engine/determinism.py: engine_source_fingerprint()` |
| 规则指纹 `rules_hash` | `184f4c681049219166e196c11ecbddbf21e08885eb6dcce60fd025258c5c42b1` | `engine/catalog.py: Catalog().fingerprint` |
| 评分配置修订 `profile_revision` | `2026-11-25-rift-3style-local-designs`（3 风格对手：`hegemon` / `arbiter` / `verdant`） | `config/scoring_v2.json` |
| 正式发布门禁 | 全部 10 项已关闭（`PENDING_RELEASE_GATES = ()`） | `engine/release.py` |

**权威身份是指纹，不是版本字符串。** `ENGINE_VERSION` 自 0.4.0 检查点以来未随后续功能（完整 28,800 Tick 对局装配、正式地图、城邦/金融/军政耦合、r9 规则与 3 风格正式评分池）递增，因此判断一致性一律以 `engine_hash` 与 `rules_hash` 为准。

`engine_hash` 覆盖 `organizer/rift_era/engine/*.py`、`organizer/rift_era/data/*.json`、`__init__.py`、`__main__.py`、`sdk.py` 的逐文件 sha256；`rules_hash` 覆盖 `organizer/rift_era/designs/data/` 的六份规则 JSON。流水线写出的每个结果都带：

- `engine_version`、`engine_hash`、`rules_version`、`rules_hash`、`pipeline_version`、`runtime`（Python/平台/libc）
- 每席 `bot_sha256`（zip 为文件 sha256；目录为 `tree:` 前缀的确定性树哈希）
- `sandbox`（`cgroup` / `bwrap` / `windows_appcontainer` / `windows_job_isolated` / `none`）与原因

查看当前值：

```bash
python -c "from organizer.rift_era.pipeline.match_runner import versions; import json; print(json.dumps(versions(), indent=2))"
```

历史修订记录：

- 2026-11-11（r8，原生 Windows 与跨平台传输/进程树兼容修正，`profile_revision = 2026-11-11-r8-windows`）：
  - `engine_hash = e5ac8ca2fa63c2ab22b07aa87b98dbc5580012aa7bdb9b1cd695d7045ffb82e1`
  - `rules_hash = 53e5619e66651c6c7c897e52fb291d27de88250d096c2e990a268e3ed6c6189c`

引擎或规则变化后：`config/scoring_v2.json` 中冻结的 `engine_hash/rules_hash` 不再匹配，评分拒绝运行，
须执行 `scripts/freeze_profile.py --revision <新修订>` 并重新导出选手包（`manifest.json` 同时记录 `engine_hash`）。
