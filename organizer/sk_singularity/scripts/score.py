# -*- coding: utf-8 -*-
"""《双符奇点》（sk_singularity）512 种子动态盲规则并发正式评测脚本（scripts/score.py）。

特性：
  - 校验 config/scoring_v2.json 全部冻结文件 SHA-256（含动态生成器、参考求解器与裁判核心）
  - 正式评测规模：512 个动态生成隐藏测试点（8 类正交盲规则/残骸家族 × 64 测/家族，均匀覆盖 N ∈ {14, 18, 21, 24}）
  - 每次评测通过 OS CSPRNG 抽取 512 个互异 64-bit 种子，并对每测以 50% 概率施加精确 S<->K 符号对换同构
  - 整批评测前调用冻结参考求解器（agents/reference/algebra_synth.cpp）以短时限现场标定每测专属动态锚点 L_ref
  - 双轨计分：套题 0-100 折算分（512 测 NormScore 等权均值）+ 不封顶 RawLog2Mean / SingularityCount
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "hidden"))
import common  # noqa: E402
import runner  # noqa: E402
import schedule  # noqa: E402

SCORING_VERSION = "sk_singularity_v2"
PROFILE_PATH = ROOT / "config" / "scoring_v2.json"
REF_SOLVER_PATH = ROOT / "agents" / "reference" / "algebra_synth.cpp"

DEFAULT_REF_TL_MS = 600
DEFAULT_ANCHOR_SCALE = 1.40
ANCHOR_MIN_LREF = 12.0
ANCHOR_MAX_LREF = 600.0

_REF_BUILD_LOCK = threading.Lock()
_REF_CACHE: dict[str, str] = {}


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


def ensure_reference_binary(root: Path = ROOT) -> str:
    """编译并缓存冻结参考求解器（供动态 L_ref 锚点标定使用，不经过选手沙盒编译包装）。"""
    ref_src = (root / "agents" / "reference" / "algebra_synth.cpp").resolve()
    if not ref_src.is_file():
        raise FileNotFoundError(f"找不到冻结参考求解器：{ref_src}")
    digest = hashlib.sha256(ref_src.read_bytes()).hexdigest()[:16]
    cache_key = f"sk_ref_{digest}"
    with _REF_BUILD_LOCK:
        cached = _REF_CACHE.get(cache_key)
        if cached and os.path.isfile(cached):
            return cached
        cache_dir = common.writable_temp_root(root) / "afb_sk_singularity_bin"
        cache_dir.mkdir(parents=True, exist_ok=True)
        ext = ".exe" if common.IS_WIN else ""
        target = cache_dir / f"{cache_key}{ext}"
        if target.is_file():
            _REF_CACHE[cache_key] = str(target)
            return str(target)
        tmp_out = cache_dir / f".tmp_{cache_key}_{os.getpid()}_{time.time_ns()}{ext}"
        res = common.compile_cpp(
            [str(ref_src)],
            str(tmp_out),
            include_dirs=None,
            stack_mb=256,
            use_sandbox_wrap=False,
        )
        if not res.ok:
            raise RuntimeError(f"Failed to compile reference solver {ref_src.name}:\n{res.log}")
        try:
            os.replace(res.exe_path, target)
        except OSError:
            if not target.is_file():
                shutil.copyfile(res.exe_path, target)
            try:
                os.unlink(res.exe_path)
            except OSError:
                pass
        _REF_CACHE[cache_key] = str(target)
        return str(target)


def compute_dynamic_lref(r_ref: float, scale: float = DEFAULT_ANCHOR_SCALE) -> float:
    return round(min(ANCHOR_MAX_LREF, max(ANCHOR_MIN_LREF, float(r_ref) * float(scale))), 2)


def calibrate_dynamic_anchors(
    judge_exe: str,
    cases: list[dict[str, Any]],
    *,
    workers: int,
    ref_tl_ms: int = DEFAULT_REF_TL_MS,
    anchor_scale: float = DEFAULT_ANCHOR_SCALE,
    root: Path = ROOT,
    stream_progress: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """在本轮动态生成的测试集上运行冻结参考求解器，现场计算每测专属 L_ref 锚点。"""
    t0 = time.perf_counter()
    ref_exe = ensure_reference_binary(root)
    if stream_progress:
        print(
            f"[Anchor] 正在使用冻结参考求解器动态标定 {len(cases)} 测专属 L_ref "
            f"(ref_tl={ref_tl_ms}ms, scale={anchor_scale:.2f}, workers={workers})...",
            file=sys.stderr,
            flush=True,
        )

    # Trusted frozen reference solver does not need contestant sandbox overhead
    old_wrap = os.environ.pop("AVL_SANDBOX_WRAP", None)
    try:
        ref_results = runner.evaluate_cases_parallel(
            judge_exe,
            ref_exe,
            cases,
            workers=workers,
            solver_tl_ms=ref_tl_ms,
        )
    finally:
        if old_wrap is not None:
            os.environ["AVL_SANDBOX_WRAP"] = old_wrap

    calibrated: list[dict[str, Any]] = []
    lrefs: list[float] = []
    ref_ac = 0
    for cs, rr in zip(cases, ref_results):
        r_val = float(rr.get("log2_steps", 0.0)) if rr.get("verdict") == "AC" else 0.0
        if rr.get("verdict") == "AC":
            ref_ac += 1
        l_ref = compute_dynamic_lref(r_val, scale=anchor_scale)
        c_new = dict(cs)
        c_new["l_ref"] = l_ref
        calibrated.append(c_new)
        lrefs.append(l_ref)

    anchor_wall_s = round(time.perf_counter() - t0, 3)
    sorted_lr = sorted(lrefs) if lrefs else [0.0]
    info = {
        "ref_solver": "agents/reference/algebra_synth.cpp",
        "ref_tl_ms": int(ref_tl_ms),
        "anchor_scale": float(anchor_scale),
        "anchor_min": ANCHOR_MIN_LREF,
        "anchor_max": ANCHOR_MAX_LREF,
        "anchor_wall_s": anchor_wall_s,
        "ref_ac_count": ref_ac,
        "total_cases": len(cases),
        "lref_mean": round(sum(lrefs) / max(1, len(lrefs)), 2),
        "lref_median": round(sorted_lr[len(sorted_lr) // 2], 2),
        "lref_min": round(sorted_lr[0], 2),
        "lref_max": round(sorted_lr[-1], 2),
    }
    if stream_progress:
        print(
            f"[Anchor] 标定完成 ({anchor_wall_s:.2f}s): ref_AC={ref_ac}/{len(cases)}, "
            f"L_ref mean={info['lref_mean']:.2f}, median={info['lref_median']:.2f} "
            f"[{info['lref_min']:.2f}, {info['lref_max']:.2f}]",
            file=sys.stderr,
            flush=True,
        )
    return calibrated, info


def evaluate_submission(
    src_path: str,
    judge_exe: str,
    cases: list[dict[str, Any]],
    *,
    solver_tl_ms: int = runner.SOLVER_TIME_LIMIT_MS,
    workers: int = 1,
    stream_progress: bool = False,
) -> dict[str, Any]:
    sub_name = Path(src_path).stem
    sub_sha = runner.digest_file(Path(src_path))
    t0 = time.perf_counter()
    tmp_root = str(common.writable_temp_root(ROOT))
    with tempfile.TemporaryDirectory(prefix="afb_sk_judge_", dir=tmp_root, ignore_cleanup_errors=True) as build_dir:
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
                "max_score": 100.0,
                "raw_log2_mean": 0.0,
                "raw_log2_max": 0.0,
                "singularity_count": 0,
                "ac_count": 0,
                "total_cases": len(cases),
                "max_time_ms": 0.0,
                "max_mem_mb": 0.0,
                "total_judge_ms": 0.0,
                "wall_s": round(time.perf_counter() - t0, 3),
                "by_family": {},
                "cases": [],
            }

        print_lock = threading.Lock()
        # For large runs (512 cases), stream compact progress every 16 cases (or on non-AC / final case)
        compact_stream = len(cases) > 64

        def _on_done(done_i: int, total_n: int, res: dict[str, Any]) -> None:
            if not stream_progress:
                return
            is_err = res.get("verdict") != "AC"
            if compact_stream and (not is_err) and (done_i % 32 != 0) and (done_i != total_n):
                return
            sing_tag = " *SING*" if res.get("singularity") else ""
            flip_tag = "F" if res.get("flipped") else "-"
            with print_lock:
                print(
                    f"  [{done_i:>3d}/{total_n:<3d}] {res['name']:<6s} ({res['family']:<14s} N={res['N']:>2d} {flip_tag}) "
                    f"{res['verdict']:<4s} |E|={res['leaves']:<2d} log2T={res['log2_steps']:8.3f} "
                    f"Lref={res['l_ref']:6.2f} DAG={res['dag_nodes']:<7d} Norm={res['norm_score']:6.2f} "
                    f"(cpu {res['cpu_ms']:.0f}ms, jdg {res['judge_ms']:.1f}ms){sing_tag}",
                    file=sys.stderr,
                    flush=True,
                )

        case_results = runner.evaluate_cases_parallel(
            judge_exe,
            comp.exe_path,
            cases,
            workers=workers,
            solver_tl_ms=solver_tl_ms,
            on_case_done=_on_done,
        )
        agg = runner.aggregate_cases(case_results)
        return {
            "problem": runner.PROBLEM_ID,
            "title": runner.TITLE,
            "submission": sub_name,
            "submission_sha256": sub_sha,
            "src": os.path.abspath(src_path),
            "status": agg["status"],
            "compile_ms": round(comp.elapsed_ms, 1),
            "compile_log": comp.log,
            "score": agg["score"],
            "max_score": 100.0,
            "raw_log2_mean": agg["raw_log2_mean"],
            "raw_log2_max": agg["raw_log2_max"],
            "singularity_count": agg["singularity_count"],
            "ac_count": agg["ac_count"],
            "total_cases": agg["total_cases"],
            "max_time_ms": round(max((r.get("cpu_ms", 0.0) for r in case_results), default=0.0), 2),
            "max_mem_mb": round(max((r.get("mem_mb", 0.0) for r in case_results), default=0.0), 2),
            "total_judge_ms": round(sum(float(r.get("judge_ms", 0.0)) for r in case_results), 2),
            "wall_s": round(time.perf_counter() - t0, 3),
            "by_family": agg["by_family"],
            "cases": case_results,
        }


def find_submissions() -> list[str]:
    sub_dir = ROOT / "agents" / "submissions"
    if not sub_dir.is_dir():
        return []
    return [str(p) for p in sorted(sub_dir.glob("*.cpp")) if p.is_file() and not p.name.startswith("_")]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《双符奇点》512 种子动态盲规则官方隐藏集并发正式评测脚本")
    ap.add_argument("sources", nargs="*", help="待测 C++20 文件路径（留空则自动扫描 agents/submissions/*.cpp）")
    ap.add_argument("--seed", type=int, default=None, help="固定主种子确定性展开 512 测（非正式复现）")
    ap.add_argument("--seeds", type=int, nargs="+", default=None, help="显式指定全部测试点种子列表（非正式复现）")
    ap.add_argument("--quick", action="store_true", help="快速冒烟档（32 测 = 8 家族 × 4 测，非正式）")
    ap.add_argument("--cases-per-family", type=int, default=None, help="每家族测试点数（正式默认 64，非默认标为非正式）")
    ap.add_argument("--no-flip", action="store_true", help="禁用 S<->K 随机对换同构（仅调试用，非正式）")
    ap.add_argument("--families", nargs="*", help="仅评测指定隐藏家族（非正式）")
    ap.add_argument("--tl", type=int, default=runner.SOLVER_TIME_LIMIT_MS, help="单测求解时限（ms，默认 5000）")
    ap.add_argument("--ref-tl", type=int, default=DEFAULT_REF_TL_MS, help="动态锚点参考标定时限（ms，默认 600）")
    ap.add_argument(
        "--workers", "--jobs", dest="workers", type=int,
        default=None,
        help="并发线程数（默认按 CPU/内存余量自动选择）",
    )
    ap.add_argument(
        "--out", type=str,
        default="",
        help="将结果 JSON 写入指定文件（正式默认 results/leaderboard.json，非正式默认 results/practice.json）",
    )
    ap.add_argument("--json", action="store_true", help="输出纯 JSON")
    ap.add_argument("--quiet-cases", action="store_true", help="隐藏逐测进度输出")
    ap.add_argument("--skip-freeze-check", action="store_true", help="跳过冻结哈希检查（仅内部校准用）")
    args = ap.parse_args()

    profile_cfg: dict[str, Any] = {}
    if not args.skip_freeze_check:
        profile_cfg = verify_frozen_profile()

    sources = list(args.sources) if args.sources else find_submissions()
    if not sources:
        raise SystemExit("未找到待评提交：请传入 .cpp 路径或在 agents/submissions/ 下放置提交文件。")

    cpf = args.cases_per_family if args.cases_per_family is not None else schedule.DEFAULT_CASES_PER_FAMILY
    try:
        sampling_info, cases = schedule.build_official_schedule(
            seed=args.seed,
            explicit_seeds=args.seeds,
            enable_flip=not args.no_flip,
            families=args.families,
            cases_per_family=cpf,
            quick=args.quick,
        )
    except ValueError as exc:
        ap.error(str(exc))

    official = (
        not args.skip_freeze_check
        and args.seed is None
        and args.seeds is None
        and not args.quick
        and (args.cases_per_family is None or args.cases_per_family == schedule.DEFAULT_CASES_PER_FAMILY)
        and not args.no_flip
        and not args.families
        and args.tl == runner.SOLVER_TIME_LIMIT_MS
        and args.ref_tl == DEFAULT_REF_TL_MS
        and len(cases) == schedule.OFFICIAL_TOTAL_CASES
    )
    official_lb = (ROOT / "results" / "leaderboard.json").resolve()
    out_path = Path(args.out).resolve() if args.out else (official_lb if official else (ROOT / "results" / "practice.json").resolve())
    if not official and out_path == official_lb:
        ap.error("practice/non-official results must not overwrite the official leaderboard")

    try:
        exec_info = runner.detect_parallel_workers(args.workers, len(cases))
    except ValueError as exc:
        ap.error(str(exc))
    workers = exec_info["workers"]

    judge_exe = common.ensure_judge_binary(ROOT)

    # 整批共享一次：在当前动态生成的测试集上现场标定每测专属 L_ref
    cases, anchor_info = calibrate_dynamic_anchors(
        judge_exe,
        cases,
        workers=workers,
        ref_tl_ms=args.ref_tl,
        anchor_scale=DEFAULT_ANCHOR_SCALE,
        root=ROOT,
        stream_progress=(not args.json),
    )

    sys.path.insert(0, str(ROOT.parent))
    import submission_sandbox
    if official:
        if submission_sandbox.official_ready():
            submission_sandbox.enable_cpp()
    elif submission_sandbox.available():
        submission_sandbox.enable_cpp()
    os.environ["AVL_SANDBOX_MEM_MB"] = str(runner.SOLVER_MEMORY_LIMIT_MB)

    results: list[dict[str, Any]] = []
    for src in sources:
        if not args.json:
            print(
                f"\n=== 《双符奇点》{'正式动态评测' if official else '练习/复现评测'}：{Path(src).name} "
                f"（{len(cases)} 测，mode={sampling_info['mode']}，TL={args.tl}ms，workers={workers} [{exec_info['mode']}]） ===",
                file=sys.stderr,
                flush=True,
            )
        rep = evaluate_submission(
            src,
            judge_exe,
            cases,
            solver_tl_ms=args.tl,
            workers=workers,
            stream_progress=(not args.json and not args.quiet_cases),
        )
        rep["official"] = official
        rep["scoring_version"] = SCORING_VERSION
        rep["profile_revision"] = profile_cfg.get("profile_revision", "unfrozen")
        rep["sampling"] = {k: v for k, v in sampling_info.items() if k != "seeds"}
        rep["anchor_calibration"] = anchor_info
        rep["execution"] = exec_info
        results.append(rep)

    results.sort(key=lambda r: (-r["score"], -r["raw_log2_mean"], r["max_time_ms"]))
    payload: dict[str, Any] = {
        "problem": runner.PROBLEM_ID,
        "official": official,
        "scoring_version": SCORING_VERSION,
        "profile_revision": profile_cfg.get("profile_revision", "unfrozen"),
        "sampling": sampling_info,
        "anchor_calibration": anchor_info,
        "execution": exec_info,
        "leaderboard": results,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"\n=== 《双符奇点》{'正式动态' if official else '练习/复现'}评测汇总（{len(cases)} 测，锚点标定 {anchor_info['anchor_wall_s']:.2f}s） ===")
        for r in results:
            print(
                f"  {r['submission']:<20s} status={r['status']:<7s} AC={r['ac_count']:>3d}/{r['total_cases']:<3d} "
                f"Score={r['score']:6.2f}/100  RawLog2Mean={r['raw_log2_mean']:8.3f}  "
                f"MaxLog2={r['raw_log2_max']:9.3f}  Sing={r['singularity_count']:>3d}  "
                f"PeakCPU={r['max_time_ms']:.0f}ms  Jdg={r['total_judge_ms']:.1f}ms  Wall={r['wall_s']:.2f}s"
            )
            for fam, fm in r.get("by_family", {}).items():
                print(
                    f"    - {fam:<15s}: AC={fm['ac']:>2d}/{fm['cases']:<2d}  "
                    f"MeanNorm={fm['mean_norm']:6.2f}  MeanLog2={fm['mean_log2']:8.3f}  "
                    f"MaxLog2={fm['max_log2']:9.3f}  Sing={fm['singularities']}"
                )


if __name__ == "__main__":
    main()
