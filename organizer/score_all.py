# -*- coding: utf-8 -*-
r"""Final 正式赛题自动分发与单模型统一评分工具（Final/organizer/score_all.py）。

只针对 Final/organizer/final_roster.json 中已正式入选的赛题进行分发与等权评分。

典型用法：
  # 1. 查看当前 Final 正式赛题与已分发文件状态
  python Final/organizer/score_all.py --list

  # 2. 分发默认 Final/contestant/submissions/ 下的提交并运行正式评分
  python Final/organizer/score_all.py

  # 3. 指定外部提交目录并命名模型
  python Final/organizer/score_all.py D:\runs\final_model_submissions --name gpt5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from roster import (
    ORGANIZER_ROOT,
    load_roster,
    problem_ext_map,
    problems_by_category,
    provisional_problems,
    selected_problems,
)

DEFAULT_SUBMISSIONS_DIR = ORGANIZER_ROOT.parent / "contestant" / "submissions"

_ROSTER = load_roster()
PROBLEMS: list[str] = selected_problems(_ROSTER)
PROBLEM_EXT: dict[str, str] = problem_ext_map(_ROSTER)
STRATEGY_PROBLEMS: tuple[str, ...] = problems_by_category("strategy", _ROSTER)
CPP_PROBLEMS: tuple[str, ...] = problems_by_category("cpp", _ROSTER)
PROVISIONAL: set[str] = provisional_problems(_ROSTER)
SCORED_PROBLEMS: list[str] = [p for p in PROBLEMS if p not in PROVISIONAL]

RESERVED_STEMS = {
    "blank", "ref", "strong", "hark_strong", "champion", "starter",
    "fifo", "lru", "marking", "rand",
    "basic", "greedy", "shortest",
    "passive", "random", "random_legal", "greedy_farm",
    "merchant", "scaling", "bm25", "charoverlap",
    "super", "advanced", "medium", "simple", "template", "none",
    "distill_strong", "mesh_strong", "adaptive_strong",
    "crop_rotation_strong", "hist_carbon", "hist_opus", "livestock_compound",
    "scaling_hegemon", "capital_arbitrageur", "adaptive_tactician",
    "agronomy_arbitrage", "ranch_compounder", "tactical_counterplay",
    "hegemon", "arbiter", "verdant",
    "frontier_control",
    "mass_baseline",
    "blitz_150",
    "staggered_125",
    "beam_evo",
    "algebra_synth",
    "leaderboard_profile_search",
    "candidate",
}


def sanitize_model_name(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", raw.strip()).strip("_")
    if not cleaned or cleaned.lower() in {"submissions", "contestant", "submission", "candidate"}:
        return "candidate"
    if cleaned.lower() in RESERVED_STEMS:
        return f"candidate_{cleaned.lower()}"
    return cleaned


def resolve_source_dir(raw_dir: Path) -> Path:
    resolved = raw_dir.resolve()
    if (resolved / "submissions").is_dir() and not any(
        (resolved / f"{prob}{ext}").is_file() for prob, ext in PROBLEM_EXT.items()
    ):
        return resolved / "submissions"
    return resolved


def clear_problem_submissions(problems: list[str]) -> None:
    for prob in problems:
        ext = PROBLEM_EXT[prob]
        sub_dir = ORGANIZER_ROOT / prob / "agents" / "submissions"
        if sub_dir.is_dir():
            for p in sub_dir.glob(f"*{ext}"):
                if p.is_file() and not p.name.startswith("_"):
                    p.unlink()


def dispatch_submissions(src_dir: Path, model_stem: str, problems: list[str]) -> dict[str, Path | None]:
    dispatched: dict[str, Path | None] = {}
    print(f"统一提交源目录：{src_dir}")
    print(f"分发目标标识名：{model_stem}")
    print("-" * 82)
    print(f"{'赛题':<18s} {'期望文件名':<24s} {'状态':<10s} 分发目标路径")
    print("-" * 82)
    for prob in problems:
        ext = PROBLEM_EXT[prob]
        expected_name = f"{prob}{ext}"
        src_file = src_dir / expected_name
        if not src_file.is_file():
            dispatched[prob] = None
            print(f"{prob:<18s} {expected_name:<24s} {'[未提交]':<10s} -")
            continue
        dst_dir = ORGANIZER_ROOT / prob / "agents" / "submissions"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_file = dst_dir / f"{model_stem}{ext}"
        shutil.copy2(src_file, dst_file)
        dispatched[prob] = dst_file
        print(f"{prob:<18s} {expected_name:<24s} {'[已分发]':<10s} {dst_file.relative_to(ORGANIZER_ROOT).as_posix()}")
    print("-" * 82)
    ok_cnt = sum(1 for v in dispatched.values() if v is not None)
    print(f"共分发 {ok_cnt} / {len(problems)} 道赛题。\n")
    return dispatched


def list_current_dispatched(problems: list[str]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    if not problems:
        print("当前 Final 正式赛题名单为空（0 题）。")
        print("请先使用：python Final/organizer/promote_problem.py --add <题目名...> --pack")
        return found
    print(f"{'赛题':<18s} {'类型':<6s} {'当前待评文件数':<14s} 文件列表")
    print("-" * 78)
    for prob in problems:
        ext = PROBLEM_EXT[prob]
        sub_dir = ORGANIZER_ROOT / prob / "agents" / "submissions"
        files = sorted(
            p for p in sub_dir.glob(f"*{ext}") if p.is_file() and not p.name.startswith("_")
        ) if sub_dir.is_dir() else []
        found[prob] = files
        names = ", ".join(p.name for p in files) if files else "(空)"
        print(f"{prob:<18s} {ext:<6s} {len(files):<14d} {names}")
    return found


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def extract_problem_metric(
    prob: str, model_stem: str, lb_path: Path | None = None, *, practice: bool = False
) -> dict[str, Any]:
    if lb_path is None:
        lb_path = ORGANIZER_ROOT / prob / "results" / "leaderboard.json"
        if prob in PROVISIONAL and not lb_path.is_file():
            lb_path = lb_path.with_name("practice_v2.json")
            practice = True
    expected_official = not practice
    if not lb_path.is_file():
        return {"status": "missing_result", "display": "无结果文件"}
    try:
        data = json.loads(lb_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "parse_error", "display": f"解析失败: {exc}"}

    if not isinstance(data, dict):
        return {"status": "invalid_result", "display": "成绩单不是 JSON 对象"}

    if prob in CPP_PROBLEMS:
        versions = {
            "compression_singularity": "compression_singularity_v1",
            "ghost_rabbit": "ghost_rabbit_v2",
            "sk_singularity": "sk_singularity_v2",
            "minimum_effort": "minimum_effort_v2",
        }
        if prob in versions:
            if data.get("official") is not True or data.get("scoring_version") != versions[prob]:
                return {"status": "incompatible_scoring", "display": "非正式或旧版成绩单，须正式重测"}
            profile = ORGANIZER_ROOT / prob / "config" / "scoring_v2.json"
            revision = json.loads(profile.read_text(encoding="utf-8"))["profile_revision"]
            if data.get("profile_revision") != revision or (
                prob == "compression_singularity" and data.get("profile_sha256") != _digest(profile)
            ):
                return {"status": "stale_profile", "display": "评分配置或裁判已变化，须重新评测"}
        rows = data.get("leaderboard", [data] if "submission" in data else [])
        for r in rows:
            if r.get("submission") == model_stem:
                sc = float(r.get("score", 0.0))
                mx = float(r.get("max_score", 100.0))
                st = r.get("status", "?")
                if not math.isfinite(sc) or not 0 <= sc <= 100 or mx != 100:
                    return {"status": "invalid_result", "display": f"成绩越界：{sc} / {mx}"}
                if prob in versions:
                    source = ORGANIZER_ROOT / prob / "agents/submissions" / f"{model_stem}{PROBLEM_EXT[prob]}"
                    if not source.is_file() or r.get("submission_sha256") != _digest(source):
                        return {"status": "stale_submission", "display": "提交源码与成绩指纹不符，须重新评测"}
                if prob == "compression_singularity" and r.get("raw_score") is not None:
                    raw_sc = float(r["raw_score"])
                    return {
                        "status": st,
                        "score": sc,
                        "max_score": mx,
                        "raw_score": raw_sc,
                        "display": f"{sc:.2f} / {mx:.0f} ({st}, 原分 {raw_sc:.2f})",
                    }
                if prob == "ghost_rabbit":
                    sing = bool(r.get("singularity", False))
                    raw_sc = float(r.get("raw_score", 0.0))
                    raw_disp = "奇点(+∞)" if sing else f"{raw_sc:.2f}"
                    return {
                        "status": st,
                        "score": sc,
                        "max_score": mx,
                        "raw_score": raw_sc,
                        "singularity": sing,
                        "display": f"{sc:.2f} / {mx:.0f} ({st}, 原分 {raw_disp})",
                    }
                if prob == "sk_singularity":
                    raw_l2 = float(r.get("raw_log2_mean", 0.0))
                    sing_cnt = int(r.get("singularity_count", 0))
                    return {
                        "status": st,
                        "score": sc,
                        "max_score": mx,
                        "raw_log2_mean": raw_l2,
                        "singularity_count": sing_cnt,
                        "display": f"{sc:.2f} / {mx:.0f} ({st}, log2均值 {raw_l2:.2f}, 奇点 {sing_cnt})",
                    }
                if prob == "minimum_effort":
                    ac_c = int(r.get("ac_count", 0))
                    tot_c = int(r.get("total_cases", 36))
                    raw_sc = float(r.get("raw_score", 0.0))
                    avg_eff = float(r.get("avg_efficiency", 0.0))
                    return {
                        "status": st,
                        "score": sc,
                        "max_score": mx,
                        "raw_score": raw_sc,
                        "avg_efficiency": avg_eff,
                        "ac_count": ac_c,
                        "total_cases": tot_c,
                        "display": f"{sc:.2f} / {mx:.0f} ({st}, {ac_c}/{tot_c} AC, 正确分 {raw_sc:.2f}, 效率 {avg_eff:.3f})",
                    }
                return {"status": st, "score": sc, "max_score": mx, "display": f"{sc:.2f} / {mx:.0f} ({st})"}

    elif prob in STRATEGY_PROBLEMS:
        from strategy_scoring import VERSION, digest, load_profile, official_seed_count, profile_fingerprint

        if data.get("scoring_version") != VERSION or (data.get("official") is not expected_official):
            return {"status": "incompatible_scoring", "display": "旧版或练习成绩，须使用 scoring_v2 正式重测"}
        try:
            profile = load_profile(prob)
            expected_profile = profile_fingerprint(profile)
        except (OSError, ValueError, KeyError) as exc:
            return {"status": "invalid_profile", "display": f"评分配置校验失败：{exc}"}
        if data.get("profile_id") != expected_profile:
            return {"status": "stale_profile", "display": "评分配置或引擎已变化，须重新评测"}
        seeds = data.get("seeds")
        if expected_official and (not isinstance(seeds, list)
                                  or not official_seed_count(prob, len(seeds), profile["seed_count"])):
            return {"status": "incompatible_scoring", "display": "正式场景数量不符合配置"}
        for row in data.get("table", []):
            if row.get("bot") != model_stem:
                continue
            try:
                score = float(row["score"])
                if not math.isfinite(score) or not 0 <= score <= 100:
                    raise ValueError("score outside [0,100]")
                source = ORGANIZER_ROOT / prob / "agents/submissions" / f"{model_stem}{PROBLEM_EXT[prob]}"
                if not source.is_file() or row.get("submission_sha256") != digest(source):
                    return {"status": "stale_submission", "display": "提交代码与成绩指纹不符，须重新评测"}
                components = row["components"]
                if not isinstance(components, dict) or not components:
                    raise ValueError("missing components")
                if any(
                    not isinstance(v, (float, int)) or not math.isfinite(v) or not 0 <= v <= 100
                    for v in components.values()
                ):
                    raise ValueError("invalid component score")
                detail = ", ".join(f"{k}={v:.2f}" for k, v in components.items())
                ci = row.get("ci95")
                if ci is not None and (
                    not isinstance(ci, list)
                    or len(ci) != 2
                    or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in ci)
                    or not 0 <= ci[0] <= ci[1] <= 100
                ):
                    raise ValueError("invalid confidence interval")
                interval = f"; 95% CI [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "; CI 样本不足"
                label = "" if data.get("official") is True else "【练习/非正式】"
                if prob == "rift_era":
                    label += f"[sandbox={','.join(row.get('sandbox', ['?']))}]"
                return {
                    "status": row.get("status", "OK"),
                    "score": score,
                    "max_score": 100,
                    "official": data.get("official") is True,
                    "sandbox": row.get("sandbox"),
                    "scoring_version": VERSION,
                    "profile_id": expected_profile,
                    "components": components,
                    "ci95": ci,
                    "raw": row.get("raw", {}),
                    "sampling": data.get("sampling"),
                    "seeds": data.get("seeds"),
                    "execution": data.get("execution"),
                    "fault_games": row.get("fault_games", 0),
                    "n_games": row.get("n_games", 0),
                    "display": f"{label}{score:.2f} / 100 ({detail}{interval})",
                }
            except (KeyError, ValueError, TypeError) as exc:
                return {"status": "invalid_result", "display": f"新版成绩字段非法：{exc}"}

    elif prob == "mem_recall":
        meta = data.get("_scoring") or {}
        if (meta.get("scoring_version") != "mem_recall_v2" or meta.get("official") is not expected_official
                or (practice and meta.get("data_mode") != "dev")):
            return {"status": "incompatible_scoring", "display": "非正式或旧版成绩单，须使用 score.py 正式重测"}
        prof_path = ORGANIZER_ROOT / prob / "config" / "scoring_v2.json"
        expected_sets = 18
        if prof_path.is_file():
            prof_cfg = json.loads(prof_path.read_text(encoding="utf-8"))
            expected_rev = prof_cfg.get("profile_revision")
            if expected_rev and meta.get("profile_revision") != expected_rev:
                return {"status": "stale_profile", "display": "评分配置或隐藏集已变化，须重新评测"}
            expected_sets = len(prof_cfg.get("expected_official_sets") or []) or 18
        if model_stem in data and isinstance(data[model_stem], dict):
            entry = data[model_stem]
            source = ORGANIZER_ROOT / prob / "agents/submissions" / f"{model_stem}{PROBLEM_EXT[prob]}"
            if not source.is_file() or entry.get("_submission_sha256") != _digest(source):
                return {"status": "stale_submission", "display": "提交源码与成绩指纹不符，须重新评测"}
            if practice:
                m_dev = entry.get("_macro_dev") or {}
                sc = float(m_dev.get("score", -1.0))
                if not math.isfinite(sc) or not 0 <= sc <= 1:
                    return {"status": "invalid_result", "display": "开发集宏平均缺失或越界"}
                return {"status": "OK", "official": False, "score": sc, "split": "dev",
                        "n_sets": m_dev.get("n_sets"), "groups": m_dev.get("groups", {}),
                        "display": f"【练习】{sc * 100:.2f} / 100（开发集）"}
            m_off = entry.get("_macro_official") or {}
            if not m_off or int(m_off.get("n_sets", 0)) != expected_sets:
                return {"status": "invalid_result", "display": f"正式评测集数不完整：{m_off.get('n_sets', 0)}/{expected_sets}"}
            sc = float(m_off.get("score", -1.0))
            if not math.isfinite(sc) or not 0.0 <= sc <= 1.0:
                return {"status": "invalid_result", "display": f"成绩越界：{sc}"}
            return {
                "status": "OK",
                "score": sc,
                "split": "official",
                "n_sets": expected_sets,
                "groups": m_off.get("groups", {}),
                "display": f"{sc:.4f} (official 宏平均, {expected_sets} 集, 折合 {sc * 100:.2f}/100)",
            }

    elif prob == "ms_elucidation":
        if data.get("format") != "avl-ms-leaderboard/2" or data.get("official") is not True:
            return {"status": "incompatible_scoring", "display": "非正式成绩单，须使用 score.py 正式重测"}
        source = ORGANIZER_ROOT / prob / "agents/submissions" / f"{model_stem}{PROBLEM_EXT[prob]}"
        base = ORGANIZER_ROOT / prob
        candidate_sha = (base / "config" / "official_candidates.sha256").read_text(encoding="ascii").strip()
        hidden = json.loads((base / "hidden" / "split_manifest.json").read_text(encoding="utf-8"))["hidden_test"]
        if (data.get("candidates_sha256") != candidate_sha
                or data.get("hidden_test_sha256") != hidden["test_sha256"]
                or data.get("hidden_answer_sha256") != hidden["answer_key_sha256"]):
            return {"status": "stale_profile", "display": "正式候选库或测试集已变化，须重新评测"}
        expected_sha = _digest(source) if source.is_file() else None
        for row in data.get("leaderboard", []):
            if row.get("submission") != model_stem:
                continue
            if expected_sha is None or row.get("submission_sha256") != expected_sha:
                return {"status": "stale_submission", "display": "提交压缩包与成绩指纹不符，须重新评测"}
            if row.get("status") == "OK" and row.get("sandbox_mode") != "docker_gpu":
                return {"status": "incompatible_scoring", "display": "成绩缺少正式推理隔离记录"}
            sc = float(row.get("score", 0.0))
            if not math.isfinite(sc) or not 0.0 <= sc <= 100.0:
                return {"status": "invalid_result", "display": f"成绩越界：{sc}"}
            st = str(row.get("status", "?"))
            mrr = float(row.get("mrr25", 0.0))
            top1 = float(row.get("top1", 0.0))
            r25 = float(row.get("recall25", 0.0))
            return {
                "status": st,
                "score": sc,
                "max_score": 100.0,
                "mrr25": mrr,
                "top1": top1,
                "recall25": r25,
                "by_stratum": row.get("by_stratum", {}),
                "display": f"{sc:.2f} / 100 ({st}, MRR@25={mrr:.4f}, Top1={top1*100:.1f}%, R@25={r25*100:.1f}%)",
            }

    return {"status": "not_found", "display": f"未在结果中找到 {model_stem}"}


def overall_score(problems: list[str], rows: dict[str, Any]) -> float:
    """总分：当前 Final 正式计分题百分制得分等权平均；未提交或无有效成绩的题计 0。"""
    if not SCORED_PROBLEMS:
        return 0.0
    total = 0.0
    for prob in dict.fromkeys(problems):
        if prob not in SCORED_PROBLEMS:
            continue
        score = rows[prob].get("score")
        if score is not None:
            points = float(score) * (100.0 if prob == "mem_recall" else 1.0)
            if math.isfinite(points) and 0 <= points <= 100:
                total += points
    return round(total / len(SCORED_PROBLEMS), 2)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="AVL Final Bench（Final 正式集）自动分发与单模型统一评分")
    ap.add_argument(
        "submissions_dir",
        nargs="?",
        type=Path,
        default=None,
        help="统一提交文件夹路径（默认 Final/contestant/submissions）",
    )
    ap.add_argument("--from", dest="from_dir", type=Path, default=None, help="同位置参数：指定统一提交文件夹路径")
    ap.add_argument("--name", default="", help="本次评测的模型标识名（默认自动取文件夹名或 candidate）")
    ap.add_argument("--problems", nargs="*", default=None, help="指定要分发与评测的赛题子集")
    ap.add_argument("--dispatch-only", "--collect-only", dest="dispatch_only", action="store_true", help="仅自动分发提交文件，不立即启动评分")
    ap.add_argument("--score-existing", action="store_true", help="跳过分发步骤，直接对各题 agents/submissions/ 中现有文件运行评分")
    ap.add_argument("--keep-existing", action="store_true", help="分发前不清空各题 agents/submissions/ 中的旧文件")
    ap.add_argument("--list", action="store_true", help="仅查看当前各题 agents/submissions/ 中已分发的文件")
    ap.add_argument("--strategy-seed-count", type=int, help="策略题随机场景数（默认为各题配置，数量不符合正式规则时按练习评测）")
    ap.add_argument("--jobs", "--strategy-jobs", dest="strategy_jobs", type=int, help="策略题期望并行对局数")
    ap.add_argument("--rift-ticks", type=int, help="rift_era 练习时钟（40 的倍数，如 200）")
    ap.add_argument("--mem-allow-dev", action="store_true", help="用公开开发集评测 mem_recall，标记为练习")
    ap.add_argument("--out", type=Path, help="总成绩单 JSON 保存路径")
    ns = ap.parse_args()

    if ns.strategy_seed_count is not None and ns.strategy_seed_count < 1:
        ap.error("--strategy-seed-count 必须为正整数")
    if ns.strategy_jobs is not None and ns.strategy_jobs < 1:
        ap.error("--jobs 必须为正整数")
    if ns.rift_ticks is not None and (ns.rift_ticks <= 0 or ns.rift_ticks % 40):
        ap.error("--rift-ticks 必须为 40 的正整数倍")

    if ns.problems is None:
        target_problems = list(PROBLEMS)
    else:
        invalid = [p for p in ns.problems if p not in PROBLEMS]
        if invalid:
            ap.error(
                f"以下赛题不在 Final 正式名单中：{', '.join(invalid)}"
                + (f"（当前已入选：{', '.join(PROBLEMS)}）" if PROBLEMS else "（当前正式名单为空）")
            )
        target_problems = list(ns.problems)
        if len(set(target_problems)) != len(target_problems):
            ap.error("--problems 中不得重复指定同一道题")

    if ns.list:
        list_current_dispatched(target_problems)
        return

    if not target_problems:
        print("当前 Final 正式赛题名单为空（0 题），无法执行评分。")
        print("请先使用：python Final/organizer/promote_problem.py --add <题目名...> --pack")
        return

    raw_src = ns.from_dir or ns.submissions_dir or DEFAULT_SUBMISSIONS_DIR
    src_dir = resolve_source_dir(raw_src)
    inferred_name = ns.name or raw_src.resolve().name
    model_stem = sanitize_model_name(inferred_name)

    if ns.score_existing:
        found = list_current_dispatched(target_problems)
        stems = {path.stem for paths in found.values() for path in paths}
        if not ns.name and len(stems) > 1:
            ap.error("已有多个模型的提交，请用 --name 指定本次评测的模型")
        if not ns.name and stems:
            model_stem = next(iter(stems))
        submissions = {
            prob: path for prob, paths in found.items() for path in paths if path.stem == model_stem
        }
    else:
        if not src_dir.is_dir():
            raise SystemExit(f"找不到统一提交文件夹：{src_dir}")
        submissions = {
            prob: src_dir / f"{prob}{PROBLEM_EXT[prob]}" for prob in target_problems
            if (src_dir / f"{prob}{PROBLEM_EXT[prob]}").is_file()
        }

    active_problems = [prob for prob in target_problems if prob in submissions]
    if not active_problems:
        print("没有任何已提交的赛题文件可供评测（请将 <题目名>.<后缀> 放入统一提交目录后再运行）。")
        return

    practice_problems = {
        prob for prob in active_problems
        if (prob == "mem_recall" and ns.mem_allow_dev) or (prob == "rift_era" and ns.rift_ticks is not None)
    }
    if ns.strategy_seed_count is not None:
        from strategy_scoring import official_seed_count

        for prob in active_problems:
            if prob in STRATEGY_PROBLEMS:
                profile_path = ORGANIZER_ROOT / prob / "config/scoring_v2.json"
                default = json.loads(profile_path.read_text(encoding="utf-8"))["seed_count"]
                if not official_seed_count(prob, ns.strategy_seed_count, default):
                    practice_problems.add(prob)
    official_summary = not practice_problems
    results_dir = ORGANIZER_ROOT / "results"
    official_summary_path = results_dir / "summary.json"
    summary_path = ns.out or (official_summary_path if official_summary else results_dir / "practice_summary.json")
    if not official_summary and summary_path.resolve() == official_summary_path.resolve():
        ap.error("练习汇总不能覆盖正式汇总")

    if not ns.score_existing:
        if not ns.keep_existing:
            clear_problem_submissions(target_problems)
        dispatched = dispatch_submissions(src_dir, model_stem, target_problems)
        submissions = {prob: path for prob, path in dispatched.items() if path is not None}
    if ns.dispatch_only:
        return

    summary_rows: dict[str, Any] = {}
    failures: list[tuple[str, Any]] = []
    t_all = time.time()

    for prob in target_problems:
        if prob not in active_problems:
            summary_rows[prob] = {"submitted": False, "status": "unsubmitted", "display": "未提交"}
            continue
        practice = prob in practice_problems
        mode = "练习" if practice else "正式"
        print(f"\n{'=' * 78}\n[Final {mode}评分] {prob}  (提交标识: {model_stem})\n{'=' * 78}")
        t0 = time.time()
        score_script = ORGANIZER_ROOT / prob / "scripts" / "score.py"
        command = [sys.executable, "-X", "utf8", str(score_script)]
        if ns.strategy_seed_count is not None and prob in STRATEGY_PROBLEMS:
            command.extend(["--seed-count", str(ns.strategy_seed_count)])
        if ns.strategy_jobs is not None and (prob in STRATEGY_PROBLEMS or prob in ("compression_singularity", "ghost_rabbit", "sk_singularity", "mem_recall")):
            command.extend(["--jobs", str(ns.strategy_jobs)])
        if prob == "rift_era" and ns.rift_ticks is not None:
            command.extend(["--ticks", str(ns.rift_ticks)])
        if prob == "mem_recall" and ns.mem_allow_dev:
            command.append("--allow-dev")
        problem_results = ORGANIZER_ROOT / prob / "results"
        problem_results.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="score-run-", dir=problem_results) as staging:
            stage = Path(staging)
            score_path = stage / "report.json"
            selected_dir = stage / "submission"
            selected_dir.mkdir()
            source = submissions[prob]
            selected = selected_dir / source.name
            shutil.copyfile(source, selected)
            selection = [str(selected)] if prob in ("ms_elucidation", "sk_singularity") else ["--submissions", str(selected_dir)]
            rc = subprocess.call([*command, *selection, "--out", str(score_path)], cwd=ORGANIZER_ROOT / prob,
                                 env={**os.environ, "PYTHONUTF8": "1"})
            if rc == 0:
                try:
                    metric = extract_problem_metric(prob, model_stem, score_path, practice=practice)
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    metric = {"status": "invalid_result", "display": f"成绩字段非法：{exc}"}
                if "score" in metric:
                    name = "leaderboard.json"
                    if practice:
                        name = "practice_dev.json" if prob == "mem_recall" else "practice_v2.json"
                    score_path.replace(problem_results / name)
        dt = round(time.time() - t0, 1)
        if rc != 0:
            if prob not in PROVISIONAL:
                failures.append((prob, rc))
            summary_rows[prob] = {
                "submitted": True,
                "status": f"error_rc_{rc}",
                "wall_s": dt,
                "display": f"评测异常 (exit={rc})",
            }
        else:
            err_statuses = {
                "missing_result", "parse_error", "incompatible_scoring",
                "invalid_profile", "stale_profile", "stale_submission",
                "invalid_result", "not_found",
            }
            if prob not in PROVISIONAL and (
                metric.get("status") in err_statuses
                or (prob in STRATEGY_PROBLEMS and metric.get("status") != "OK")
            ):
                failures.append((prob, metric.get("status", "invalid_result")))
            metric["submitted"] = True
            metric["wall_s"] = dt
            summary_rows[prob] = metric

    total_wall = round(time.time() - t_all, 1)
    print(f"\n{'=' * 78}")
    print(f"AVL Final Bench 评分汇总 · {'正式' if official_summary else '练习'} · 模型标识: {model_stem} · 总耗时: {total_wall:.1f}s")
    print("=" * 78)
    print(f"{'赛题':<18s} {'状态':<10s} {'耗时(s)':<9s} 成绩")
    print("-" * 78)
    for prob in target_problems:
        info = summary_rows[prob]
        st = "已评测" if info.get("submitted") else "未提交"
        ws = f"{info.get('wall_s', 0.0):.1f}" if info.get("submitted") else "-"
        print(f"{prob:<18s} {st:<10s} {ws:<9s} {info.get('display', '-')}")
    print("-" * 78)
    total_score = overall_score(target_problems, summary_rows)
    print(f"总分（{len(SCORED_PROBLEMS)} 题等权平均，满分 100）：{total_score:.2f}")
    for prob in target_problems:
        if prob in PROVISIONAL and summary_rows[prob].get("submitted"):
            print(f"注意：{prob} 已评测但暂不计入总分：{summary_rows[prob].get('display')}")
    if summary_rows.get("mem_recall", {}).get("split") == "dev":
        print("警告：mem_recall 用的是公开开发集，本次总分不是正式成绩。")
    print("=" * 78)

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite": "final",
        "official": official_summary and not failures,
        "model": model_stem,
        "source_dir": str(src_dir),
        "total_wall_s": total_wall,
        "total_score": total_score,
        "scored_problems": SCORED_PROBLEMS,
        "provisional_problems": sorted(PROVISIONAL),
        "problems": summary_rows,
    }
    if failures and summary_path.resolve() == official_summary_path.resolve():
        summary_path = summary_path.with_name("failed_summary.json")
    with tempfile.TemporaryDirectory(prefix="summary-", dir=summary_path.parent) as staging:
        staged_summary = Path(staging) / "summary.json"
        staged_summary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        staged_summary.replace(summary_path)
    print(f"汇总成绩已保存：{summary_path}")

    if failures:
        print("\n部分赛题评分返回非零退出码：", failures, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
