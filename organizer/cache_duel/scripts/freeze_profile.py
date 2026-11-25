# -*- coding: utf-8 -*-
"""重新计算并冻结 cache_duel 的 config/scoring_v2.json 文件指纹。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "scoring_v2.json"

FROZEN_LIST = (
    "agents/baselines/fifo.py",
    "agents/baselines/lru.py",
    "agents/baselines/marking.py",
    "agents/baselines/rand.py",
    "agents/reference/adaptive_strong.py",
    "agents/reference/hark_strong.py",
    "agents/reference/strong.py",
    "config/format.json",
    "engine/engine.py",
    "engine/policy.py",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description="重新冻结 cache_duel scoring_v2.json")
    ap.add_argument("--revision", required=True, help="新的 profile_revision 标识")
    args = ap.parse_args()

    cfg = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    frozen = {}
    for rel in FROZEN_LIST:
        p = ROOT / rel
        if not p.is_file():
            raise SystemExit(f"缺少冻结文件：{p}")
        frozen[rel] = sha256_file(p)

    cfg["frozen_files"] = frozen
    cfg["game_memory_bytes"] = 134217728  # 128 MiB 估算/对局，避免大内存宿主机误判
    cfg["profile_revision"] = args.revision
    PROFILE_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"已冻结 {len(frozen)} 个文件 -> {PROFILE_PATH} (revision={args.revision})")


if __name__ == "__main__":
    main()
