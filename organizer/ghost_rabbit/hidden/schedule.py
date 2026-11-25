# -*- coding: utf-8 -*-
"""《天翼幽灵兔》（ghost_rabbit）正式隐藏赛程与种子调度（仅存于 hidden/，绝不导出至选手包）。"""
from __future__ import annotations

from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "engine") not in sys.path:
    sys.path.insert(0, str(ROOT / "engine"))
from common import ensure_interactor_binary  # noqa: E402


def ensure_official_interactor(root_dir: str | Path = ROOT) -> str:
    root = Path(root_dir).resolve()
    return ensure_interactor_binary(
        root / "hidden" / "official_interactor.cpp",
        extra_deps=[root / "engine" / "judge_core.hpp"],
    )

OFFICIAL_FAMILIES: tuple[str, ...] = (
    "F1_PrimeCone",
    "F2_WallHugger",
    "F3_QuantumAdv",
    "F4_Stochastic",
)

# 正式评测：32 局（4 类互补强交互器 × 8 随机种子，交错排布）
OFFICIAL_CASES_PER_FAMILY = 8
OFFICIAL_SUITE_SPEC: tuple[tuple[str, str], ...] = tuple(
    (f"gr{idx + 1:02d}", OFFICIAL_FAMILIES[idx % len(OFFICIAL_FAMILIES)])
    for idx in range(len(OFFICIAL_FAMILIES) * OFFICIAL_CASES_PER_FAMILY)
)

# 快速冒烟档（--quick，非正式）：8 局（每家族 2 局）
QUICK_SUITE_SPEC: tuple[tuple[str, str], ...] = tuple(
    (f"qk{idx + 1:02d}", OFFICIAL_FAMILIES[idx % len(OFFICIAL_FAMILIES)])
    for idx in range(len(OFFICIAL_FAMILIES) * 2)
)

# 公开练习用固定种子（与正式种子隔离）
PUBLIC_PRACTICE_SEEDS: tuple[int, ...] = (
    20261101, 20261102, 20261103, 20261104, 20261105, 20261106,
    20261107, 20261108, 20261109, 20261110, 20261111, 20261112,
)


def sample_official_seeds(n_cases: int, *, explicit_seeds: list[int] | None = None) -> tuple[list[int], str]:
    if explicit_seeds is not None:
        if len(explicit_seeds) == 1:
            base = int(explicit_seeds[0])
            return [base + i * 1000003 for i in range(n_cases)], "replay"
        if len(explicit_seeds) == n_cases:
            return [int(s) for s in explicit_seeds], "replay"
        raise ValueError(f"--seeds 需要 1 个主种子或恰好 {n_cases} 个种子，收到 {len(explicit_seeds)} 个")

    used = set(PUBLIC_PRACTICE_SEEDS)
    seeds: list[int] = []
    while len(seeds) < n_cases:
        val = secrets.randbits(63) + 1
        if val not in used:
            used.add(val)
            seeds.append(val)
    return seeds, "os_csprng"
