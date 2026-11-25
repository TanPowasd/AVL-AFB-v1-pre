# -*- coding: utf-8 -*-
"""组委会正式检索评分（mem_recall_v2）：
- 基于 organizer 内部冻结配置 config/scoring_v2.json 校验引擎、基线与全部 19 个隐藏集文件（禁止缺失、篡改或额外文件）；
- 非正式运行（--sets / --data / --allow-dev / --bot / --seed）禁止覆写 results/leaderboard.json；
- 空提交目录 fail-closed（绝不清空 leaderboard.json）；
- 正式评测采用 OS CSPRNG 随机种子执行协议匿名化与语料洗牌，并记录提交 SHA-256 与沙箱状态，原子写入并备份历史榜单。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HIDDEN = ROOT / "hidden"
PROFILE_PATH = ROOT / "config" / "scoring_v2.json"
DEFAULT_LEADERBOARD = ROOT / "results" / "leaderboard.json"
DEFAULT_PRACTICE = ROOT / "results" / "practice_dev.json"
DEV = (ROOT.parent.parent / "contestant" / "mem_recall" / "data" / "dev").resolve()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_and_verify_profile(verify_hidden_files: bool = True) -> dict[str, Any]:
    if not PROFILE_PATH.is_file():
        sys.exit(f"缺少冻结评分配置：{PROFILE_PATH}")
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    if profile.get("version") != "mem_recall_v2" or profile.get("problem") != "mem_recall":
        sys.exit(f"评分配置版本非法：{PROFILE_PATH}")

    problems: list[str] = []
    frozen = profile.get("frozen_files") or {}
    if not frozen:
        problems.append("frozen_files 为空")
    for rel, expected in sorted(frozen.items()):
        p = (ROOT / rel).resolve()
        if not p.is_relative_to(ROOT) or not p.is_file():
            problems.append(f"缺冻结文件 {rel}")
        elif sha256_file(p) != expected:
            problems.append(f"冻结文件已改动 {rel}")

    if verify_hidden_files:
        expected_hidden = profile.get("hidden_files") or {}
        if not expected_hidden:
            problems.append("hidden_files 为空")
        if not HIDDEN.is_dir():
            sys.exit(f"缺少隐藏集目录：{HIDDEN}。放好数据后再评分；只想看开发集成绩时加 --allow-dev。")
        actual_files = {
            p.name: p
            for p in HIDDEN.iterdir()
            if p.is_file() and not p.name.startswith(".")
        }
        extra = sorted(set(actual_files) - set(expected_hidden))
        if extra:
            problems.append(f"隐藏集存在未授权额外文件：{', '.join(extra)}")
        for name, expected in sorted(expected_hidden.items()):
            p = actual_files.get(name)
            if p is None:
                problems.append(f"缺隐藏文件 {name}")
            elif sha256_file(p) != expected:
                problems.append(f"隐藏文件哈希不符 {name}")

    if problems:
        sys.exit("mem_recall 冻结校验失败：" + "；".join(problems))
    if verify_hidden_files:
        print(f"隐藏集与冻结配置校验通过：{len(profile['hidden_files'])} 个隐藏文件，revision={profile['profile_revision']}")
    return profile


def write_report_atomic(path: Path, report: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            tag = (prev.get("_scoring") or {}).get("scoring_version", "legacy_v1")
        except Exception:
            tag = "legacy_v1"
        backup = path.with_name(f"{path.stem}.{tag}.{sha256_file(path)[:12]}.json")
        if not backup.exists():
            shutil.copy2(path, backup)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as fh:
        tmp_path = Path(fh.name)
        fh.write(text)
    try:
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="mem_recall_v2 正式检索评分入口")
    ap.add_argument("--submissions", default=str(ROOT / "agents" / "submissions"))
    ap.add_argument("--bot", default="")
    ap.add_argument("--data", default="", help="显式指定数据目录（标记为非正式）")
    ap.add_argument("--allow-dev", action="store_true", help="使用公开开发集运行（非正式，不可覆写 leaderboard.json）")
    ap.add_argument("--sets", default="", help="仅运行指定评测集子集（非正式）")
    ap.add_argument("--seed", type=int, default=None, help="显式固定匿名化/洗牌种子（非正式重放）")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--out", default="")
    ns = ap.parse_args()
    if ns.jobs < 1:
        ap.error("--jobs 必须为正整数")

    use_hidden = (not ns.data) and (not ns.allow_dev)
    if ns.data:
        data_path = Path(ns.data).resolve()
    elif ns.allow_dev:
        print("警告：使用公开开发集，本次成绩不是正式成绩。", file=sys.stderr)
        data_path = DEV
    else:
        data_path = HIDDEN.resolve()

    profile = load_and_verify_profile(verify_hidden_files=use_hidden)

    official = (
        use_hidden
        and data_path == HIDDEN.resolve()
        and not ns.sets
        and not ns.bot
        and ns.seed is None
    )

    out_path = Path(ns.out).resolve() if ns.out else (DEFAULT_LEADERBOARD if official else DEFAULT_PRACTICE).resolve()
    if not official and out_path == DEFAULT_LEADERBOARD.resolve():
        ap.error("非正式运行（--sets/--data/--allow-dev/--bot/--seed）禁止覆写正式榜单 results/leaderboard.json")

    if ns.bot:
        cand_files = [Path(ns.bot).resolve()]
        if not cand_files[0].is_file():
            sys.exit(f"找不到提交文件：{cand_files[0]}")
    else:
        sub_dir = Path(ns.submissions).resolve()
        if not sub_dir.is_dir():
            sys.exit(f"找不到提交目录：{sub_dir}")
        cand_files = sorted(
            p.resolve() for p in sub_dir.glob("*.py") if p.is_file() and not p.name.startswith("_")
        )
        if not cand_files:
            sys.exit(f"提交目录为空，拒绝覆写榜单：{sub_dir}")

    before_sha = {p.stem: sha256_file(p) for p in cand_files}
    run_seed = ns.seed if ns.seed is not None else (secrets.randbelow(2**31 - 1) + 1)

    organizer = ROOT.parent
    sys.path.insert(0, str(organizer))
    import submission_sandbox  # noqa: E402

    sandboxed = submission_sandbox.official_ready() if official else submission_sandbox.available()
    launch = [str(organizer / "submission_sandbox.py"), "--launch", "mem_recall"] if sandboxed else []

    with tempfile.TemporaryDirectory(prefix="avl_mr_run_") as tmp_dir:
        raw_out = Path(tmp_dir) / "raw.json"
        cmd = [
            sys.executable, "-X", "utf8", *launch,
            str(ROOT / "engine" / "runner.py"),
            "--data", str(data_path),
            "--jobs", str(ns.jobs),
            "--seed", str(run_seed),
            "--out", str(raw_out),
        ]
        if ns.sets:
            cmd += ["--sets", ns.sets]
        if ns.bot:
            cmd += ["--bot", str(cand_files[0])]
        else:
            cmd += ["--bots", str(Path(ns.submissions).resolve())]

        rc = subprocess.call(cmd, cwd=str(ROOT), env={**os.environ, "PYTHONUTF8": "1"})
        if rc != 0:
            sys.exit(rc)
        if not raw_out.is_file():
            sys.exit("评测进程未生成输出文件")
        report = json.loads(raw_out.read_text(encoding="utf-8"))

    # 复验提交与冻结配置在评测期间未被篡改
    for p in cand_files:
        if sha256_file(p) != before_sha[p.stem]:
            sys.exit(f"提交文件在评测期间发生变化：{p}")
    load_and_verify_profile(verify_hidden_files=use_hidden)

    for stem, digest_hex in before_sha.items():
        if stem in report and isinstance(report[stem], dict):
            report[stem]["_submission_sha256"] = digest_hex

    report["_scoring"] = {
        "scoring_version": "mem_recall_v2",
        "problem": "mem_recall",
        "official": official,
        "profile_revision": profile["profile_revision"],
        "profile_sha256": sha256_file(PROFILE_PATH),
        "seed": run_seed,
        "seed_source": "os_csprng" if ns.seed is None else "explicit",
        "sandbox": str(sandboxed),
        "expected_official_sets": len(profile["expected_official_sets"]),
        "data_mode": "hidden" if use_hidden else ("dev" if ns.allow_dev else "custom"),
    }

    write_report_atomic(out_path, report)
    print(f"成绩单已保存：{out_path}（official={official}, sandbox={sandboxed}, seed={run_seed}）")


if __name__ == "__main__":
    main()
