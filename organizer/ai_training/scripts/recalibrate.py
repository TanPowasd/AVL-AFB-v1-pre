# -*- coding: utf-8 -*-
"""通用 N 对手重新校准与冻结脚本（支持任意 N >= 1 个 .py / .zip / 目录对手）。

用法：
  # 修改 config/scoring_v2.json["opponents"] 后，一键校准 margin_scale 并重冻结：
  python scripts/recalibrate.py --revision 2026-11-21-ai-training-3style-v4

  # 若新增众筹对手是含 main.py 的多文件目录，可加 --pack-dirs 自动确定性打包为 .zip 并注册：
  python scripts/recalibrate.py --pack-dirs --revision 2026-11-25-crowdsourced-v5

  # 仅试算校准值（不改动 scoring_v2.json / format.json / engine 常量）：
  python scripts/recalibrate.py --no-freeze
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parent.parent
ORGANIZER_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ORGANIZER_ROOT))
import archive  # noqa: E402
import engine as E  # noqa: E402
import strategy_scoring  # noqa: E402

CORE_FROZEN_FILES = (
    "agents/baselines/merchant.py",
    "agents/baselines/scaling.py",
    "config/format.json",
    "engine/archive.py",
    "engine/engine.py",
    "engine/pkg_entry.py",
    "engine/policy.py",
    "engine/solo_npc.py",
    "engine/world.py",
)

ANCHOR_BASELINES = (
    ("scaling", "agents/baselines/scaling.py"),
    ("merchant", "agents/baselines/merchant.py"),
)

SKIP_DIRS = {"__pycache__", ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pack_dir_deterministic(src_dir: Path, out_zip: Path) -> Path:
    """将含 main.py 的多文件目录确定性打包为 .zip（固定时间戳与字典序），便于冻结哈希。"""
    src_dir = src_dir.resolve()
    if not (src_dir / "main.py").is_file():
        raise ValueError(f"Opponent directory missing main.py: {src_dir}")
    entries: list[tuple[Path, str]] = []
    for p in sorted(src_dir.rglob("*"), key=lambda x: x.relative_to(src_dir).as_posix()):
        rel = p.relative_to(src_dir)
        if any(part in SKIP_DIRS for part in rel.parts) or p.is_dir():
            continue
        if p.name in SKIP_NAMES or p.suffix.lower() in SKIP_SUFFIXES:
            continue
        entries.append((p, rel.as_posix()))
    tmp = out_zip.with_name(out_zip.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp, arc in entries:
                zi = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o100644 << 16
                zf.writestr(zi, fp.read_bytes())
        probs = archive.check_archive(str(tmp))
        if probs:
            raise ValueError(f"Packed opponent zip failed archive check ({src_dir}): {probs}")
        tmp.replace(out_zip)
    finally:
        if tmp.exists():
            tmp.unlink()
    return out_zip


def prepare_opponents(cfg: dict, pack_dirs: bool) -> list[dict]:
    opponents = cfg.get("opponents") or []
    if not opponents:
        raise ValueError("scoring_v2.json has no opponents configured")
    updated = []
    for opp in opponents:
        rel = str(opp["path"]).replace("\\", "/")
        abs_p = (ROOT / rel).resolve()
        if not abs_p.is_relative_to(ROOT):
            raise ValueError(f"Opponent path outside ai_training: {rel}")
        if abs_p.is_dir():
            if not pack_dirs:
                raise ValueError(
                    f"Opponent {opp['id']} points to directory '{rel}'. "
                    "Re-run with --pack-dirs (or pack with scripts/make_submission.py) so it is a hashable .zip file."
                )
            out_zip = abs_p.parent / f"{abs_p.name}.zip"
            pack_dir_deterministic(abs_p, out_zip)
            rel = out_zip.relative_to(ROOT).as_posix()
            print(f"[pack] {opp['id']}: {abs_p.name}/ -> {rel}")
            opp = {**opp, "path": rel}
        elif not abs_p.is_file():
            raise FileNotFoundError(f"Opponent file not found: {abs_p}")
        updated.append(opp)
    return updated


def build_matchups(opponents: list[dict], max_inter_pairs: int = 28) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """为任意 N >= 1 个对手构建校准对局集合（基线 vs 各对手 + 对手间两两对战）。"""
    bot_paths: dict[str, str] = {}
    opp_labels: list[str] = []
    for idx, opp in enumerate(opponents):
        rel = opp["path"]
        label = Path(rel).name
        if label in bot_paths:
            label = f"{opp['id']}{Path(rel).suffix}"
        if label in bot_paths:
            label = f"{opp['id']}_{idx}{Path(rel).suffix}"
        bot_paths[label] = str((ROOT / rel).resolve())
        opp_labels.append(label)

    base_labels: list[str] = []
    for bname, brel in ANCHOR_BASELINES:
        blabel = Path(brel).name
        babs = str((ROOT / brel).resolve())
        if babs not in bot_paths.values():
            if blabel in bot_paths:
                blabel = f"baseline_{bname}{Path(brel).suffix}"
            suffix_n = 1
            while blabel in bot_paths:
                blabel = f"baseline_{bname}_{suffix_n}{Path(brel).suffix}"
                suffix_n += 1
            bot_paths[blabel] = babs
            base_labels.append(blabel)

    pairs: list[tuple[str, str]] = []
    # 1. Public baselines vs every scoring opponent
    for bl in base_labels:
        for ol in opp_labels:
            pairs.append((bl, ol))
    # 2. All unordered opponent-vs-opponent pairs (subsampled deterministically if N is huge)
    inter_pairs = [
        (opp_labels[i], opp_labels[j])
        for i in range(len(opp_labels))
        for j in range(i + 1, len(opp_labels))
    ]
    if len(inter_pairs) > max_inter_pairs:
        step = len(inter_pairs) / float(max_inter_pairs)
        inter_pairs = [inter_pairs[int(k * step)] for k in range(max_inter_pairs)]
    pairs.extend(inter_pairs)
    return bot_paths, pairs


def _play_one(item: tuple) -> tuple:
    idx, total, seed, a_name, a_path, b_name, b_path, ticks, world_salt, inproc = item
    t0 = time.perf_counter()
    E.W.SALT = world_salt
    # Default to isolated subprocesses (inproc=False) so stdio-only bots and .zip bots with
    # same-named helper modules (e.g. planner.py) run identically to official strategy_scoring.
    res = E.play(a_path, b_path, seed=seed, ticks=ticks, inproc=inproc)
    val_a, val_b = float(res["value"][0]), float(res["value"][1])
    gap = round(val_a - val_b, 2)
    faults_a, faults_b = res["faults"][0], res["faults"][1]
    dt = time.perf_counter() - t0
    return idx, total, seed, a_name, b_name, val_a, val_b, gap, faults_a, faults_b, dt


def update_scale_literal(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"Expected 1 scale constant match in {path}, matched {count}")
    path.write_text(new_text, encoding="utf-8", newline="\n")


def run_calibration(
    cfg: dict,
    opponents: list[dict],
    seeds: list[int],
    ticks: int,
    world_salt: str,
    jobs: int,
    inproc: bool = False,
) -> dict:
    bot_paths, matchups = build_matchups(opponents)
    tasks = []
    idx = 0
    total = len(seeds) * len(matchups) * 2
    for seed in seeds:
        for b1, b2 in matchups:
            for a_name, b_name in ((b1, b2), (b2, b1)):
                idx += 1
                tasks.append(
                    (idx, total, seed, a_name, bot_paths[a_name], b_name, bot_paths[b_name], ticks, world_salt, inproc)
                )

    mode = "inproc" if inproc else "subprocess"
    print(
        f"Starting ai_training recalibration ({mode}): {len(opponents)} opponents, "
        f"{len(matchups)} matchups x {len(seeds)} seeds x 2 seats = {total} games "
        f"(ticks={ticks}, jobs={jobs})...",
        flush=True,
    )
    t0 = time.perf_counter()
    games = []
    gaps = []
    fault_total = 0

    with ProcessPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = [ex.submit(_play_one, task) for task in tasks]
        for fut in as_completed(futs):
            i, tot, seed, an, bn, va, vb, gap, fa, fb, dt = fut.result()
            ill = int(fa.get("illegal", 0)) + int(fb.get("illegal", 0))
            dead = int(bool(fa.get("dead"))) + int(bool(fb.get("dead")))
            fault_total += ill + dead
            print(
                f"[{i:>3}/{tot}] seed={seed} A={an:<22} B={bn:<22} -> "
                f"Val {va:>9.2f} vs {vb:>9.2f}  gap={gap:>9.2f}  ({dt:.1f}s)",
                flush=True,
            )
            games.append({"seed": seed, "A": an, "B": bn, "gap": gap})
            if abs(gap) > 1e-6:
                gaps.append(abs(gap))

    if not gaps:
        raise RuntimeError("No non-zero gaps recorded during calibration")

    games.sort(key=lambda g: (g["seed"], g["A"], g["B"]))
    med = round(float(statistics.median(gaps)), 4)
    margin_scale = round(med / math.atanh(0.6), 2)
    elapsed = time.perf_counter() - t0
    print(f"\nRecalibration finished in {elapsed:.1f}s (faults={fault_total}).")
    print(f"Non-zero gaps: {len(gaps)}/{len(games)}, median_gap={med:.4f}, margin_scale={margin_scale:.2f}")

    return {
        "purpose": "Independent full-length baseline & scoring-pool calibration; official seeds are disjoint",
        "seeds": list(seeds),
        "ticks": int(ticks),
        "opponent_ids": [o["id"] for o in opponents],
        "games_count": len(games),
        "median_gap": med,
        "margin_scale": margin_scale,
        "games": games,
    }


def apply_freeze(cfg: dict, opponents: list[dict], calib_data: dict, revision: str | None) -> None:
    scale = float(calib_data["margin_scale"])
    calib_path = ROOT / "config" / "calibration_v2.json"
    calib_path.write_text(json.dumps(calib_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    # Sync public format.json
    fmt_path = ROOT / "config" / "format.json"
    fmt = json.loads(fmt_path.read_text(encoding="utf-8"))
    fmt["value_scale"] = scale
    fmt.setdefault("scoring", {})["margin_scale"] = scale
    fmt["scoring"]["opponent_count"] = len(opponents)
    fmt_path.write_text(json.dumps(fmt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    # Sync VALUE_SCALE constants in engine, replay, and elo (keeps spar.py & test_replay.py aligned)
    update_scale_literal(
        ROOT / "engine" / "engine.py",
        r"^VALUE_SCALE\s*=\s*[0-9.]+.*$",
        f"VALUE_SCALE = {scale:.2f}  # scoring_v2: frozen independent baseline calibration",
    )
    update_scale_literal(
        ROOT / "engine" / "replay.py",
        r"^VALUE_SCALE\s*=\s*[0-9.]+.*$",
        f"VALUE_SCALE = {scale:.2f}",
    )
    update_scale_literal(
        ROOT / "score" / "elo.py",
        r"^DEFAULT_VALUE_SCALE\s*=\s*[0-9.]+.*$",
        f"DEFAULT_VALUE_SCALE = {scale:.2f}",
    )

    # Rebuild frozen_files hashes for core files + all registered N opponents
    frozen_rel = sorted(set(CORE_FROZEN_FILES) | {o["path"] for o in opponents})
    cfg["opponents"] = opponents
    cfg["margin_scale"] = scale
    cfg["calibration_seeds"] = list(calib_data["seeds"])
    cfg["frozen_files"] = {rel: sha256_file(ROOT / rel) for rel in frozen_rel}
    cfg.setdefault("calibration", {})["evidence"] = "config/calibration_v2.json"
    cfg["calibration"]["method"] = "median(abs(nonzero full-match gap)) / atanh(0.6), rounded to 2 decimals"
    cfg["calibration"]["evidence_sha256"] = sha256_file(calib_path)
    if revision:
        cfg["profile_revision"] = revision

    prof_path = ROOT / "config" / "scoring_v2.json"
    prof_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    # Self-verify using official loader
    loaded = strategy_scoring.load_profile("ai_training")
    fp = strategy_scoring.profile_fingerprint(loaded)
    print(
        f"[freeze OK] opponents={len(loaded['opponents'])} "
        f"margin_scale={loaded['margin_scale']:.2f} "
        f"revision={loaded.get('profile_revision')} fingerprint={fp[:12]}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, nargs="+", help="Override calibration seeds")
    ap.add_argument("--ticks", type=int, default=0, help="Override episode ticks (default from scoring_v2.json)")
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 4), help="Parallel worker processes")
    ap.add_argument("--revision", type=str, default="", help="Update profile_revision in scoring_v2.json")
    ap.add_argument("--pack-dirs", action="store_true", help="Pack directory opponents (with main.py) to .zip")
    ap.add_argument("--salt-mode", choices=("profile", "practice"), default="profile", help="World salt for calibration")
    ap.add_argument(
        "--inproc",
        action="store_true",
        help="Opt-in fast in-process mode (requires make_bot and unique module names; default uses isolated subprocesses)",
    )
    ap.add_argument("--no-freeze", action="store_true", help="Compute calibration only; do not write config/hashes")
    args = ap.parse_args()

    prof_path = ROOT / "config" / "scoring_v2.json"
    cfg = json.loads(prof_path.read_text(encoding="utf-8"))
    opponents = prepare_opponents(cfg, pack_dirs=args.pack_dirs)
    seeds = list(args.seeds) if args.seeds else list(cfg.get("calibration_seeds", [101, 102, 103, 104]))
    ticks = int(args.ticks) if args.ticks > 0 else int(cfg.get("ticks", E.TICKS))
    world_salt = str(cfg.get("world_salt", "practice")) if args.salt_mode == "profile" else "practice"

    calib_data = run_calibration(cfg, opponents, seeds, ticks, world_salt, args.jobs, inproc=args.inproc)
    if args.no_freeze:
        print("[preview only] --no-freeze set; no files modified.")
        return
    apply_freeze(cfg, opponents, calib_data, revision=(args.revision or None))


if __name__ == "__main__":
    main()
