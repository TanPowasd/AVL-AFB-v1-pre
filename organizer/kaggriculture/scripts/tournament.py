"""循环赛：把一份提交和 kaggle 历史目录里的其他 agent 各打一场（默认 8 种子、双方换座）。

用法：
  # 把新提交加入历史目录，并对目录内其他 agent 跑循环赛
  python scripts/tournament.py --add F:/new/my_agent.py --as gemini-3.8-flash-1107.py

  # 已在目录里的提交，直接跑
  python scripts/tournament.py --candidate F:/kaggle-history/my_agent.py

  # 只加不跑
  python scripts/tournament.py --add F:/new/agent.py --as xxx.py --no-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
DEFAULT_HISTORY = Path("F:/kaggle-history")
DEFAULT_SEEDS = "11,23,47,83,101,127,131,157"

SUMMARY_RE = re.compile(r"(\d+)胜 (\d+)平 (\d+)负 \| 平均现金 ([\d.]+) vs ([\d.]+) \| (\d+) 局")
GAME_RE = re.compile(r"seed=\s*(\d+) (fwd |swap) A@seat(\d) -> cash\s+([\d.]+) vs\s+([\d.]+)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_to_history(source: Path, history: Path, name: str | None) -> Path:
    """把提交复制进历史目录；同内容已存在时复用，不重复投放。"""
    source = source.resolve()
    if not source.is_file():
        raise SystemExit(f"找不到提交文件：{source}")
    digest = sha256(source)
    for existing in sorted(history.glob("*.py")):
        if existing.stat().st_size == source.stat().st_size and sha256(existing) == digest:
            print(f"[复用] 目录内已存在同一份：{existing.name}")
            return existing
    target = history / (name or source.name)
    if target.exists():
        raise SystemExit(f"目标文件已存在且内容不同：{target}（换一个 --as 名字）")
    shutil.copyfile(source, target)
    print(f"[加入] {source} -> {target}")
    return target


def run_one(candidate: Path, opponent: Path, seeds: str) -> dict:
    cmd = [sys.executable, "-X", "utf8", str(HERE / "run_match.py"),
           "--a", str(candidate), "--b", str(opponent), "--seeds", seeds]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=7200)
    out = proc.stdout or ""
    row = {"opponent": opponent.stem, "returncode": proc.returncode,
           "wall_s": round(time.time() - t0, 1)}
    match = SUMMARY_RE.search(out)
    if proc.returncode != 0 or not match:
        row["error"] = (proc.stderr or out).strip().splitlines()[-3:]
        return row
    row.update(win=int(match.group(1)), draw=int(match.group(2)), loss=int(match.group(3)),
               cash_a=float(match.group(4)), cash_b=float(match.group(5)), games=int(match.group(6)))
    games = []
    for g in GAME_RE.finditer(out):
        seed, seat_kind, seat, ca, cb = g.groups()
        games.append({"seed": int(seed), "swap": seat_kind.strip() == "swap",
                      "seat_a": int(seat), "cash_a": float(ca), "cash_b": float(cb)})
    row["per_seed"] = games
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="kaggriculture 历史对手循环赛")
    ap.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    ap.add_argument("--add", type=Path, help="把这份提交复制进历史目录")
    ap.add_argument("--as", dest="as_name", help="复制时的文件名（默认沿用原名）")
    ap.add_argument("--candidate", type=Path, help="参赛文件（默认用 --add 的那份）")
    ap.add_argument("--seeds", default=DEFAULT_SEEDS, help=f"固定种子，默认 {DEFAULT_SEEDS}")
    ap.add_argument("--jobs", type=int, default=8, help="并行对局数，默认 8")
    ap.add_argument("--no-run", action="store_true", help="只复制，不打比赛")
    ap.add_argument("--out", type=Path, help="汇总 JSON 路径（默认写入 results/）")
    ns = ap.parse_args()

    history = ns.history.resolve()
    if not history.is_dir():
        raise SystemExit(f"找不到历史目录：{history}")
    candidate = ns.candidate
    if ns.add:
        candidate = add_to_history(ns.add, history, ns.as_name)
        if ns.no_run:
            return
    if candidate is None:
        raise SystemExit("需要 --candidate 或 --add")
    candidate = candidate.resolve()

    opponents = sorted(p for p in history.glob("*.py") if p.resolve() != candidate)
    if not opponents:
        raise SystemExit("历史目录里没有其他 agent")
    seeds = ",".join(str(int(s)) for s in ns.seeds.split(","))
    n_games = 2 * len([s for s in seeds.split(",") if s.strip()])
    print(f"参赛：{candidate.name}")
    print(f"对手：{len(opponents)} 个，每个 {n_games} 局（{seeds} × 双方换座）\n")

    rows: list[dict] = []
    t_all = time.time()
    with ThreadPoolExecutor(max_workers=ns.jobs) as pool:
        futures = {pool.submit(run_one, candidate, opp, seeds): opp for opp in opponents}
        done = 0
        for future, opp in futures.items():
            row = future.result()
            rows.append(row)
            done += 1
            if "error" in row:
                print(f"  [{done}/{len(opponents)}] {opp.stem:<34s} 失败")
            else:
                print(f"  [{done}/{len(opponents)}] {opp.stem:<34s} "
                      f"{row['win']}胜 {row['draw']}平 {row['loss']}负  "
                      f"均现金 {row['cash_a']:.0f} vs {row['cash_b']:.0f}")

    ok = [r for r in rows if "error" not in r]
    failed = [r for r in rows if "error" in r]
    total_w = sum(r["win"] for r in ok)
    total_d = sum(r["draw"] for r in ok)
    total_l = sum(r["loss"] for r in ok)
    total_g = sum(r["games"] for r in ok)

    print("\n" + "=" * 96)
    print(f"循环赛汇总 · {candidate.name} · 总耗时 {time.time() - t_all:.0f}s")
    print("=" * 96)
    print(f"{'对手':<34s}{'胜-平-负':>12s}{'胜率':>8s}{'均现金差':>12s}  备注")
    print("-" * 96)
    for r in sorted(ok, key=lambda x: (-x["win"] / max(x["games"], 1), -x["cash_a"])):
        games = max(r["games"], 1)
        rate = r["win"] / games
        margin = r["cash_a"] - r["cash_b"]
        note = "全胜" if r["win"] == games else ("全负" if r["loss"] == games else "")
        print(f"{r['opponent']:<34s}{r['win']:>5d}-{r['draw']}-{r['loss']:<4d}"
              f"{rate:>7.0%}{margin:>+12.0f}  {note}")
    for r in failed:
        print(f"{r['opponent']:<34s}{'加载/对局失败':>12s}{'-':>8s}{'-':>12s}  {r.get('error')}")
    print("-" * 96)
    if total_g:
        print(f"合计：{total_w}胜 {total_d}平 {total_l}负 / {total_g} 局，胜率 {total_w / total_g:.1%}")
    if failed:
        print(f"失败 {len(failed)} 场：{', '.join(r['opponent'] for r in failed)}")

    out = ns.out or (ROOT / "results" / f"tournament-{time.strftime('%Y%m%d-%H%M%S')}" / "summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "candidate": str(candidate), "seeds": seeds, "total_wall_s": round(time.time() - t_all, 1),
        "totals": {"win": total_w, "draw": total_d, "loss": total_l, "games": total_g},
        "matches": rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"汇总已保存：{out}")


if __name__ == "__main__":
    main()
