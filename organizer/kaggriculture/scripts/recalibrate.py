# -*- coding: utf-8 -*-
"""Kaggriculture N-opponent recalibration and profile freeze tool.

Dynamically reads arbitrary N >= 1 opponents registered in config/scoring_v2.json["opponents"]
(supporting single .py files and multi-file .zip packages; multi-file directories containing
main.py can be deterministically packed to .zip via --pack-dirs), runs full 720-step
calibration games in isolated subprocesses (loader.load_agent by default, avoiding cross-bot
sys.modules collisions), computes margin_scale = round(median(abs(nonzero cash_gap)) / atanh(0.6), 2),
writes config/calibration_v2.json, and (with --freeze) updates config/format.json +
config/scoring_v2.json hashes and verifies strategy_scoring.load_profile("kaggriculture").

Usage (to add crowdsourced opponents later):
  1. Drop new opponent (.py, .zip, or multi-file <dir>/ containing main.py) into
     organizer/kaggriculture/agents/reference/
  2. Add {"id": "<name>", "path": "agents/reference/<file_or_dir>"} to
     config/scoring_v2.json["opponents"]
  3. Run:
     python organizer/kaggriculture/scripts/recalibrate.py --pack-dirs --freeze --revision <rev>
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import statistics
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ORGANIZER_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "engine"))
import arena  # noqa: E402
import loader  # noqa: E402

PUBLIC_ANCHORS = {
    "passive": "agents/baselines/passive.py",
    "random_legal": "agents/baselines/random_legal.py",
    "greedy_farm": "agents/baselines/greedy_farm.py",
}

SKIP_DIRS = {"__pycache__", ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pack_opponent_dir(src_dir: Path) -> Path:
    """Deterministically pack a multi-file opponent directory (with root main.py) into <name>.zip.

    Preserves dotted directory names (e.g. team_v1.2/ -> team_v1.2.zip) and validates via
    loader.check_archive.
    """
    abs_dir = src_dir.resolve()
    if not abs_dir.is_dir() or not (abs_dir / "main.py").is_file():
        raise ValueError(f"Opponent directory must exist and contain root main.py: {src_dir}")

    entries: list[tuple[Path, str]] = []
    for p in sorted(abs_dir.rglob("*"), key=lambda x: x.relative_to(abs_dir).as_posix()):
        rel = p.relative_to(abs_dir)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.is_dir():
            continue
        if p.name in SKIP_NAMES or p.suffix.lower() in SKIP_SUFFIXES:
            continue
        entries.append((p, rel.as_posix()))

    out_zip = abs_dir.parent / f"{abs_dir.name}.zip"
    tmp_zip = abs_dir.parent / f".{abs_dir.name}.zip.tmp.{os.getpid()}"
    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path, arcname in entries:
                info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zf.writestr(info, file_path.read_bytes())
        problems = loader.check_archive(str(tmp_zip))
        if problems:
            raise ValueError(f"Packed opponent archive failed loader.check_archive ({abs_dir.name}): {'; '.join(problems)}")
        tmp_zip.replace(out_zip)
    finally:
        if tmp_zip.exists():
            tmp_zip.unlink()
    return out_zip


def resolve_opponents(opponents: list[dict], root: Path = ROOT, pack_dirs: bool = False) -> tuple[list[dict], bool]:
    """Validate and resolve opponent entries; optionally pack multi-file directories to .zip.

    Returns (resolved_opponents, changed_any_path).
    Fails fast on raw directories or subdirectory main.py paths unless pack_dirs=True, because
    strategy_scoring.py requires a single frozen file and engine/loader.py only puts package
    roots on sys.path for .zip or directory targets.
    """
    root = Path(root).resolve()
    resolved: list[dict] = []
    changed = False
    ref_root = (root / "agents" / "reference").resolve()
    seen_ids: set[str] = set()

    for opp in opponents:
        oid = str(opp["id"])
        if oid in seen_ids:
            raise ValueError(f"Duplicate opponent id in scoring_v2.json: {oid}")
        seen_ids.add(oid)
        rel = str(opp["path"])
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"Opponent path escapes problem root: {rel}")

        if target.is_dir():
            if not pack_dirs:
                raise ValueError(
                    f"Opponent {oid!r} points to directory {rel!r}. Official scoring requires a single "
                    f"frozen .py or .zip file (pass --pack-dirs to deterministically pack it to "
                    f"{(target.parent / (target.name + '.zip')).relative_to(root).as_posix()!r}, "
                    f"or pack with scripts/make_submission.py)."
                )
            packed = pack_opponent_dir(target)
            new_rel = packed.relative_to(root).as_posix()
            print(f"[PACKED DIR] {oid}: {rel} -> {new_rel}", flush=True)
            resolved.append({**opp, "path": new_rel})
            changed = True
            continue

        if not target.is_file():
            raise FileNotFoundError(f"Registered opponent file not found: {rel}")

        # Detect multi-file subdirectory <dir>/main.py (unsupported directly by load_trusted sys.path)
        if target.name == "main.py" and target.parent != ref_root:
            if not pack_dirs:
                raise ValueError(
                    f"Opponent {oid!r} points to subdirectory entrypoint {rel!r}. engine/loader.load_trusted "
                    f"does not add subdirectory main.py parents to sys.path (local imports like 'planner' will "
                    f"fail during official scoring). Re-run with --pack-dirs to deterministically pack "
                    f"{target.parent.relative_to(root).as_posix()!r} into a .zip, or pack with scripts/make_submission.py."
                )
            packed = pack_opponent_dir(target.parent)
            new_rel = packed.relative_to(root).as_posix()
            print(f"[PACKED PKG] {oid}: {rel} -> {new_rel}", flush=True)
            resolved.append({**opp, "path": new_rel})
            changed = True
            continue

        low = target.name.lower()
        if low.endswith(".zip"):
            problems = loader.check_archive(str(target))
            if problems:
                raise ValueError(f"Opponent archive {rel!r} failed validation: {'; '.join(problems)}")
        elif not low.endswith(".py"):
            raise ValueError(f"Unsupported opponent file extension (need .py or .zip): {rel}")

        resolved.append(dict(opp))

    return resolved, changed


def build_matchups(opponent_ids: list[str]) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    """Build a balanced calibration schedule for arbitrary N >= 1 registered opponents.

    Each endpoint is tagged as ('anchor', name) or ('opponent', oid) so public anchor
    keys ('passive', 'greedy_farm') can never be clobbered even if an opponent shares
    an anchor's id.
    """
    if not opponent_ids:
        raise ValueError("scoring_v2.json must register at least 1 opponent")
    matchups: list[tuple[tuple[str, str], tuple[str, str]]] = [
        (("anchor", "passive"), ("anchor", "greedy_farm")),
    ]
    for oid in opponent_ids:
        matchups.append((("anchor", "greedy_farm"), ("opponent", oid)))
    n = len(opponent_ids)
    if n == 1:
        matchups.append((("anchor", "passive"), ("opponent", opponent_ids[0])))
    elif n <= 6:
        for i in range(n):
            for j in range(i + 1, n):
                matchups.append((("opponent", opponent_ids[i]), ("opponent", opponent_ids[j])))
    else:
        # Linear O(N) deterministic ring pairing when scaling to large crowdsourced pools
        seen = set()
        for step in (1, 2):
            for i in range(n):
                a, b = opponent_ids[i], opponent_ids[(i + step) % n]
                key = (a, b) if a < b else (b, a)
                if a != b and key not in seen:
                    seen.add(key)
                    matchups.append((("opponent", key[0]), ("opponent", key[1])))
    return matchups


def _run_single(item):
    cand_name, cand_path, opp_name, opp_path, seed, seat, match_cfg, ticks, inproc = item
    paths = [cand_path, opp_path] if seat == 0 else [opp_path, cand_path]
    raw_fns = []
    policies = []
    try:
        for i, path in enumerate(paths):
            random.seed(seed * 2 + i)
            load_fn = loader.load_trusted if inproc else loader.load_agent
            _, fn = load_fn(path)
            raw_fns.append(fn)
            policies.append(arena.isolate_policy_rng(fn, seed * 2 + i))

        cfg = dict(match_cfg)
        cfg["episodeSteps"] = ticks
        t0 = time.perf_counter()
        res = arena.play_game(policies, seed, cfg=cfg)
        wall_s = time.perf_counter() - t0
    finally:
        for fn in raw_fns:
            if hasattr(fn, "close"):
                try:
                    fn.close()
                except Exception:
                    pass
            cdir = getattr(fn, "_cleanup_dir", None)
            if cdir:
                shutil.rmtree(cdir, ignore_errors=True)

    my_cash = float(res["cash"][seat])
    opp_cash = float(res["cash"][1 - seat])
    cash_gap = my_cash - opp_cash
    stats = res["stats"]
    dead = res["forfeited"]

    return {
        "candidate": cand_name,
        "opponent": opp_name,
        "seed": seed,
        "seat": seat,
        "wall_s": wall_s,
        "raw": {
            "cash": my_cash,
            "opponent_cash": opp_cash,
            "cash_gap": cash_gap,
        },
        "candidate_fault": bool(dead[seat] or stats[seat]["errors"] or stats[seat]["timeout"]),
        "opponent_fault": bool(dead[1 - seat] or stats[1 - seat]["errors"] or stats[1 - seat]["timeout"]),
        "faults": [stats[seat], stats[1 - seat]],
        "result": res,
        "component": "adv",
        "config_id": "0",
    }


def collect_frozen_files(cfg: dict, root: Path = ROOT) -> dict[str, str]:
    """Fingerprint engine, champion, format, public baselines, and all N registered opponents."""
    root = Path(root).resolve()
    rel_paths: set[str] = {
        "config/format.json",
        "agents/baselines/greedy_farm.py",
        "agents/baselines/random_legal.py",
        "agents/reference/champion.py",
        "engine/arena.py",
        "engine/loader.py",
        "engine/policy.py",
        "engine/replay.py",
        "engine/rules.py",
    }
    champion_dir = root / "engine" / "champion"
    skip_cache_dirs = {"__pycache__", "_native_cache", "logs", "shadow_cache"}
    for p in champion_dir.rglob("*"):
        if p.is_file() and not (skip_cache_dirs & set(p.parts)) and p.suffix not in (".pyc", ".pyo"):
            rel_paths.add(p.relative_to(root).as_posix())

    for opp in cfg["opponents"]:
        rel = opp["path"]
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise FileNotFoundError(f"Registered opponent file missing or outside problem root: {rel}")
        rel_paths.add(rel)

    return {rel: sha256_file(root / rel) for rel in sorted(rel_paths)}


def freeze_profile(
    margin_scale: float,
    seeds: list[int],
    opponents: list[dict] | None = None,
    revision: str | None = None,
) -> dict:
    scoring_path = ROOT / "config" / "scoring_v2.json"
    format_path = ROOT / "config" / "format.json"
    calib_path = ROOT / "config" / "calibration_v2.json"

    cfg = json.loads(scoring_path.read_text(encoding="utf-8"))
    if opponents is not None:
        cfg["opponents"] = opponents
    fmt = json.loads(format_path.read_text(encoding="utf-8"))

    fmt["cash_scale"] = margin_scale
    fmt["scoring"]["margin_scale"] = margin_scale
    fmt["scoring"]["opponent_count"] = len(cfg["opponents"])
    format_path.write_text(json.dumps(fmt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    cfg["margin_scale"] = margin_scale
    cfg["calibration_seeds"] = list(seeds)
    cfg["calibration"] = {
        "method": "median(abs(nonzero full-match gap)) / atanh(0.6), rounded to 2 decimals",
        "evidence": "config/calibration_v2.json",
        "evidence_sha256": sha256_file(calib_path),
    }
    if revision:
        cfg["profile_revision"] = revision
    cfg["frozen_files"] = collect_frozen_files(cfg)

    scoring_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    if str(ORGANIZER_ROOT) not in sys.path:
        sys.path.insert(0, str(ORGANIZER_ROOT))
    import strategy_scoring  # noqa: E402

    verified = strategy_scoring.load_profile("kaggriculture")
    print(
        f"[FROZEN OK] revision={verified['profile_revision']} opponents={len(verified['opponents'])} "
        f"margin_scale={verified['margin_scale']} frozen_files={len(verified['frozen_files'])}",
        flush=True,
    )
    return verified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", help="Override calibration seeds (default: scoring_v2.json)")
    parser.add_argument("--ticks", type=int, default=None, help="Override episodeSteps for dry smoke runs (forbidden with --freeze)")
    parser.add_argument("--out", type=Path, default=None, help="Output calibration JSON path (default: config/calibration_v2.json)")
    parser.add_argument("--jobs", type=int, default=None, help="Parallel worker count")
    parser.add_argument("--pack-dirs", action="store_true", help="Deterministically pack multi-file opponent dirs into .zip files")
    parser.add_argument("--inproc", action="store_true", help="Opt-in in-process loading via load_trusted (default: isolated subprocess load_agent)")
    parser.add_argument("--freeze", action="store_true", help="Update format.json & scoring_v2.json and verify load_profile")
    parser.add_argument("--freeze-only", action="store_true", help="Skip game runs; re-freeze from existing calibration_v2.json")
    parser.add_argument("--revision", type=str, default=None, help="New profile_revision string when freezing")
    args = parser.parse_args()

    if args.freeze and args.ticks is not None:
        parser.error("--ticks override cannot be combined with --freeze (official calibration must run full ticks)")
    if args.freeze and args.out is not None and args.out.resolve() != (ROOT / "config" / "calibration_v2.json").resolve():
        parser.error("--freeze requires writing to config/calibration_v2.json")

    scoring_path = ROOT / "config" / "scoring_v2.json"
    cfg = json.loads(scoring_path.read_text(encoding="utf-8"))
    raw_opponents = cfg.get("opponents") or []
    if not raw_opponents:
        raise SystemExit("config/scoring_v2.json has no opponents registered")

    try:
        resolved_opponents, _ = resolve_opponents(raw_opponents, root=ROOT, pack_dirs=args.pack_dirs)
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc))

    if args.freeze_only:
        calib = json.loads((ROOT / "config" / "calibration_v2.json").read_text(encoding="utf-8"))
        seeds = list(calib.get("seeds") or sorted({g["seed"] for g in calib["games"]}))
        freeze_profile(float(calib["margin_scale"]), seeds, opponents=resolved_opponents, revision=args.revision)
        return

    seeds = list(args.seeds) if args.seeds is not None else list(cfg.get("calibration_seeds") or [11, 23])
    if not seeds or len(set(seeds)) != len(seeds):
        raise SystemExit("Calibration seeds must be non-empty and unique")

    # Separate anchor and registered opponent namespaces so anchors can never be clobbered
    anchor_paths = {name: str((ROOT / rel).resolve()) for name, rel in PUBLIC_ANCHORS.items()}
    opp_paths = {opp["id"]: str((ROOT / opp["path"]).resolve()) for opp in resolved_opponents}
    opp_ids = [opp["id"] for opp in resolved_opponents]

    def resolve_endpoint(ep: tuple[str, str]) -> tuple[str, str]:
        kind, name = ep
        if kind == "anchor":
            return name, anchor_paths[name]
        return name, opp_paths[name]

    matchups = build_matchups(opp_ids)
    match_cfg = dict(cfg.get("match") or {})
    ticks = int(cfg.get("ticks", 720)) if args.ticks is None else int(args.ticks)

    items = []
    for ep_a, ep_b in matchups:
        c_name, c_path = resolve_endpoint(ep_a)
        o_name, o_path = resolve_endpoint(ep_b)
        for seed in seeds:
            for seat in (0, 1):
                items.append((c_name, c_path, o_name, o_path, seed, seat, match_cfg, ticks, args.inproc))

    workers = args.jobs or min(os.cpu_count() or 4, max(1, len(items)), 12)
    mode = "inproc(load_trusted)" if args.inproc else "isolated_subprocess(load_agent)"
    print(
        f"Running {len(items)} calibration games ({len(matchups)} matchups x {len(seeds)} seeds x 2 seats, "
        f"ticks={ticks}, {mode}) for {len(opp_ids)} registered opponents ({opp_ids}) with {workers} workers...",
        flush=True,
    )
    with ProcessPoolExecutor(max_workers=workers) as ex:
        games = list(ex.map(_run_single, items))

    any_fault = [g for g in games if g["candidate_fault"] or g["opponent_fault"]]
    if any_fault:
        print(f"WARNING: {len(any_fault)} calibration game(s) recorded faults/errors!", flush=True)

    gaps = [abs(g["raw"]["cash_gap"]) for g in games if g["raw"]["cash_gap"] != 0]
    if not gaps:
        raise SystemExit("All calibration games ended with zero cash gap")
    med = statistics.median(gaps)
    margin_scale = round(med / math.atanh(0.6), 2)
    print(f"Nonzero gaps: {len(gaps)}/{len(games)} | median={med:.2f} | margin_scale={margin_scale}", flush=True)

    for g in games:
        gap = g["raw"]["cash_gap"]
        dead_c = g["result"]["forfeited"][g["seat"]]
        dead_o = g["result"]["forfeited"][1 - g["seat"]]
        if dead_c:
            s = 0.0
        elif dead_o:
            s = 100.0
        else:
            s = round(50.0 + 50.0 * math.tanh(gap / margin_scale), 2)
        g["score"] = s

    out_data = {
        "purpose": "Independent full-length calibration with isolated policy RNG; seeds disjoint from official scoring_v2",
        "method": "median absolute nonzero gap / atanh(0.6)",
        "seeds": seeds,
        "opponents": opp_ids,
        "margin_scale": margin_scale,
        "games": games,
    }
    out_path = args.out or (ROOT / "config" / "calibration_v2.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Saved calibration evidence to {out_path}", flush=True)

    if args.freeze:
        freeze_profile(margin_scale, seeds, opponents=resolved_opponents, revision=args.revision)


if __name__ == "__main__":
    main()
