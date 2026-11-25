# -*- coding: utf-8 -*-
"""《最小努力》（minimum_effort / U755456）官方 36 点正式评测脚本（scripts/score.py）。

特性：
  - 校验 config/scoring_v2.json 全部冻结文件 SHA-256（含 36 个正式测试点、引擎与官方参考解）
  - 严格执行按点独立计分 + 时间/空间对数效率加权（时间权重 0.70 > 空间权重 0.30；分档时限 1000/1500/2000 ms，128 MiB）
  - 连续完整串行评测 runs 次（默认 3，环境变量 AVL_RUNS 可调），取总分最高的一次（同分取最大 CPU 用时更小者）
  - 写入 results/leaderboard.json（含 official 标记、submission_sha256、raw_score、avg_efficiency 与 profile_revision）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import common  # noqa: E402
import runner  # noqa: E402

SCORING_VERSION = "minimum_effort_v2"
PROFILE_PATH = ROOT / "config" / "scoring_v2.json"
OFFICIAL_RUNS = 3
DEFAULT_RUNS = int(os.environ.get("AVL_RUNS", str(OFFICIAL_RUNS)))


def verify_frozen_profile(profile_path: Path = PROFILE_PATH) -> dict[str, Any]:
    if not profile_path.is_file():
        raise FileNotFoundError(f"找不到冻结评分配置：{profile_path}")
    cfg = json.loads(profile_path.read_text(encoding="utf-8"))
    frozen = cfg.get("frozen_files", {})
    if not isinstance(frozen, dict) or not frozen:
        raise ValueError(f"冻结配置缺少 frozen_files 清单：{profile_path}")
    mismatches: list[str] = []
    for rel, expected_sha in frozen.items():
        p = ROOT / rel
        if not p.is_file():
            mismatches.append(f"缺失文件 {rel}")
            continue
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual != expected_sha:
            mismatches.append(f"哈希不匹配 {rel} (actual={actual[:12]} != expected={expected_sha[:12]})")
    if mismatches:
        raise RuntimeError("冻结评分校验失败：\n  - " + "\n  - ".join(mismatches))
    return cfg


def evaluate_submission(
    src_path: str,
    tests_dir: str,
    *,
    tl_override_ms: int | None = None,
    runs: int = DEFAULT_RUNS,
    stream_progress: bool = False,
) -> dict[str, Any]:
    manifest = json.loads((Path(tests_dir) / "manifest.json").read_text(encoding="utf-8"))
    cases = list(manifest.get("cases", []))
    sub_name = Path(src_path).stem
    sub_sha = runner.digest_file(Path(src_path))
    t0 = time.perf_counter()

    tmp_root = str(common.writable_temp_root(ROOT))
    with tempfile.TemporaryDirectory(prefix="afb_me_judge_", dir=tmp_root, ignore_cleanup_errors=True) as build_dir:
        comp = runner.compile_submission(src_path, build_dir, str(ROOT))
        if not comp.ok:
            return {
                "problem": runner.PROBLEM_ID,
                "title": runner.TITLE,
                "submission": sub_name,
                "submission_sha256": sub_sha,
                "src": os.path.abspath(src_path),
                "status": "CE",
                "compile_ms": round(comp.elapsed_ms, 1),
                "compile_log": comp.log,
                "score": 0.0,
                "raw_score": 0.0,
                "max_score": 100.0,
                "avg_efficiency": 0.0,
                "weights": {
                    "time": runner.TIME_WEIGHT,
                    "mem": runner.MEM_WEIGHT,
                    "time_free_ms": runner.TIME_FREE_MS,
                    "mem_free_mb": runner.MEM_FREE_MB,
                    "memory_limit_mb": runner.MEMORY_LIMIT_MB,
                },
                "ac_count": 0,
                "total_cases": len(cases),
                "max_time_ms": 0.0,
                "max_mem_mb": 0.0,
                "wall_s": round(time.perf_counter() - t0, 3),
                "subtasks": [],
                "cases": [],
                "runs": [],
                "best_run": 0,
            }

        attempts = []
        for run_idx in range(max(1, runs)):
            case_results = []
            for idx, c in enumerate(cases, start=1):
                res = runner.run_case(comp.exe_path, c, tests_dir, tl_override_ms=tl_override_ms)
                case_results.append(res)
                if stream_progress and run_idx == 0:
                    eff_str = f"E={res.get('efficiency', 0.0):.3f}" if res["verdict"] == "AC" else "E=0.000"
                    print(
                        f"  [{idx:>2d}/{len(cases):<2d}] {res['name']:<8s} (ST{res['subtask']}, {res['points']:>2.0f}pt, TL={res['time_limit_ms']}ms) "
                        f"{res['verdict']:<4s} {res['time_ms']:7.1f}ms {res['mem_mb']:5.1f}MiB {eff_str}  {res['msg']}",
                        file=sys.stderr,
                        flush=True,
                    )
            scored = runner.score_results(case_results, manifest)
            mt = max((float(r.get("time_ms", 0.0)) for r in case_results), default=0.0)
            attempts.append((scored, case_results, mt))
            if runs > 1:
                print(
                    f"  [minimum_effort] 第 {run_idx + 1}/{runs} 轮：{scored['score']:.2f} / {scored['max_score']:.0f} "
                    f"(正确分 {scored['raw_score']:.2f}, 效率 {scored['avg_efficiency']:.3f}, "
                    f"AC {scored['ac_count']}/{scored['total_cases']}，最大 CPU {mt:.1f} ms)",
                    file=sys.stderr,
                    flush=True,
                )

        best_idx = max(range(len(attempts)), key=lambda i: (attempts[i][0]["score"], -attempts[i][2]))
        scored, case_results, _ = attempts[best_idx]
        run_summary = [
            {
                "run": i + 1,
                "score": a[0]["score"],
                "raw_score": a[0]["raw_score"],
                "avg_efficiency": a[0]["avg_efficiency"],
                "ac_count": a[0]["ac_count"],
                "max_time_ms": round(a[2], 2),
            }
            for i, a in enumerate(attempts)
        ]

        verdicts = {r["verdict"] for r in case_results}
        if verdicts == {"AC"} or (scored["max_score"] > 0 and scored["raw_score"] >= scored["max_score"] - 1e-6):
            overall = "AC"
        elif scored["score"] > 0:
            overall = "PC"
        else:
            overall = next((v for v in ("TLE", "MLE", "RE", "OLE", "WA") if v in verdicts), "WA")

        return {
            "problem": runner.PROBLEM_ID,
            "title": runner.TITLE,
            "submission": sub_name,
            "submission_sha256": sub_sha,
            "src": os.path.abspath(src_path),
            "status": overall,
            "compile_ms": round(comp.elapsed_ms, 1),
            "compile_log": comp.log,
            "score": scored["score"],
            "raw_score": scored["raw_score"],
            "max_score": scored["max_score"],
            "avg_efficiency": scored["avg_efficiency"],
            "weights": scored["weights"],
            "ac_count": scored["ac_count"],
            "total_cases": scored["total_cases"],
            "max_time_ms": round(max((float(r.get("time_ms", 0.0)) for r in case_results), default=0.0), 2),
            "max_mem_mb": round(max((float(r.get("mem_mb", 0.0)) for r in case_results), default=0.0), 2),
            "wall_s": round(time.perf_counter() - t0, 3),
            "subtasks": scored["subtasks"],
            "cases": case_results,
            "runs": run_summary,
            "best_run": best_idx + 1,
        }


def print_report(rep: dict[str, Any]) -> None:
    print(f"=== {rep['title']} ({rep['problem']}) ===")
    print(f"Submission : {rep['submission']} ({rep['src']})")
    if rep["status"] == "CE":
        print(f"Status     : CE ({rep['compile_ms']} ms)\n{rep['compile_log']}")
        return
    print(
        f"Status     : {rep['status']}  |  Score: {rep['score']:.2f} / {rep['max_score']:.0f} "
        f"(AC {rep['ac_count']}/{rep['total_cases']})  |  Max CPU: {rep['max_time_ms']:.1f} ms  |  "
        f"Max Mem: {rep['max_mem_mb']:.1f} MiB  |  Wall: {rep['wall_s']:.2f} s"
    )
    if rep.get("raw_score") is not None:
        w = rep.get("weights") or {}
        print(
            f"Efficiency : 正确性分 {rep['raw_score']:.2f}，加权平均效率系数 {rep.get('avg_efficiency', 0.0):.4f} "
            f"（时间权重 {w.get('time', runner.TIME_WEIGHT):.2f} > 空间权重 {w.get('mem', runner.MEM_WEIGHT):.2f}，"
            f"免扣阈值 {w.get('time_free_ms', runner.TIME_FREE_MS):.0f} ms / {w.get('mem_free_mb', runner.MEM_FREE_MB):.0f} MiB）"
        )
    if rep.get("runs") and len(rep["runs"]) > 1:
        print(
            "Runs       : "
            + "  ".join(f"#{r['run']} {r['score']:.2f}" for r in rep["runs"])
            + f"  -> 取第 {rep['best_run']} 轮（最高分）"
        )
    for st in rep.get("subtasks", []):
        print(
            f"  Subtask {st['id']}: {st['score']:5.2f} / {st['points']:5.1f} "
            f"(raw {st.get('raw_score', st['score']):5.1f}, E={st.get('avg_efficiency', 1.0):.3f})  "
            f"[{st['passed']:>2d}/{st['cases']:<2d} AC, max {st['max_time_ms']:6.1f} ms, {st.get('max_mem_mb', 0.0):5.1f} MiB]  {st['name']}"
        )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《最小努力》官方 36 点正式评测脚本")
    ap.add_argument("--src", default="", help="单个待测 .cpp 文件路径")
    ap.add_argument("--submissions", default=str(ROOT / "agents" / "submissions"), help="批量评测目录")
    ap.add_argument("--tests", default=str(ROOT / "data" / "official"), help="正式测试集目录")
    ap.add_argument("--tl", type=int, default=0, help="可选：覆盖全测试点时限 ms（仅调试用，将标记为非正式）")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="连续完整评测次数，取最高分（默认 3）")
    ap.add_argument("--out", default="", help="输出排行榜 JSON 路径（正式默认 results/leaderboard.json）")
    ap.add_argument("--quiet-cases", action="store_true", help="不逐点打印实时进度")
    ap.add_argument("--skip-freeze-check", action="store_true", help="跳过冻结哈希检查（仅调试用，将标记为非正式）")
    ns = ap.parse_args()
    if ns.runs < 1:
        ap.error("--runs 必须为正整数")

    profile_cfg: dict[str, Any] = {}
    if not ns.skip_freeze_check:
        profile_cfg = verify_frozen_profile()

    default_tests = (ROOT / "data" / "official").resolve()
    official = (
        not ns.skip_freeze_check
        and Path(ns.tests).resolve() == default_tests
        and ns.tl == 0
        and ns.runs == OFFICIAL_RUNS
    )

    default_out = ROOT / "results" / ("leaderboard.json" if official else "practice.json")
    out_path = Path(ns.out) if ns.out else default_out
    if not official and out_path.resolve() == (ROOT / "results" / "leaderboard.json").resolve():
        raise SystemExit("非正式调试模式禁止覆写 results/leaderboard.json，请用 --out 指定其他路径。")

    tl_override = ns.tl if ns.tl > 0 else None

    if ns.src:
        sources = [ns.src]
    else:
        sub_dir = Path(ns.submissions)
        sources = (
            [str(p) for p in sorted(sub_dir.glob("*.cpp")) if p.is_file() and not p.name.startswith("_")]
            if sub_dir.is_dir()
            else []
        )
    if not sources:
        raise SystemExit(f"未找到待评提交：请将 .cpp 放入 {ns.submissions} 或通过 --src 指定。")

    sys.path.insert(0, str(ROOT.parent))
    import submission_sandbox

    sandbox = submission_sandbox.official_ready() if official else submission_sandbox.available()
    if sandbox:
        submission_sandbox.enable_cpp()

    reports = []
    for src in sources:
        rep = evaluate_submission(
            src,
            ns.tests,
            tl_override_ms=tl_override,
            runs=ns.runs,
            stream_progress=(not ns.quiet_cases),
        )
        rep["official"] = official
        rep["scoring_version"] = SCORING_VERSION
        rep["profile_revision"] = profile_cfg.get("profile_revision", "unfrozen")
        print_report(rep)
        reports.append(rep)

    reports.sort(key=lambda r: (-r["score"], r["max_time_ms"]))
    payload = {
        "problem": runner.PROBLEM_ID,
        "official": official,
        "scoring_version": SCORING_VERSION,
        "runs": ns.runs,
        "profile_revision": profile_cfg.get("profile_revision", "unfrozen"),
        "leaderboard": reports,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
