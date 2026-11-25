"""裂陆纪元评测流水线（组委会侧，入口/IPC 层）。

本包只做宿主调度：选手进程启动（bwrap 或无沙箱回退）、多 seed 批量运行、
断点续跑、scoring_v2 计分与选手包导出。它不修改 ``engine/`` 中的任何规则逻辑，
也不参与 ``engine_source_fingerprint``；所有对局仍经引擎自己的
``StrategySession`` + ``MatchDriver`` 驱动（窗口标签、分页、time_pool/3600 s 计时）。
"""

PIPELINE_VERSION = 'rift_pipeline_v1'
