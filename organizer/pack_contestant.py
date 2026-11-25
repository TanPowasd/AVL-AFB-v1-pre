# -*- coding: utf-8 -*-
"""导出干净的 Final 正式赛题参赛选手工作区（默认输出至 Final/contestant）。

只导出 Final/organizer/final_roster.json 中已正式入选的赛题。

用法：
  python Final/organizer/pack_contestant.py --force
  python Final/organizer/pack_contestant.py --out ../players/final_model_a
  python Final/organizer/pack_contestant.py --force --zip
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from roster import (
    ORGANIZER_ROOT,
    RESERVE_ROOT,
    SUITE_DOCS_DIR,
    load_roster,
    selected_problems,
    sync_suite_docs,
)

DEFAULT_DEST = ORGANIZER_ROOT.parent / "contestant"
SUITE_DOCS = ("README.md", "AGENTS.md")
SUITE_RESERVED_DIRS = {"submissions", "logs", ".scratch", "results", "replays"}

# data/dev 是公开语料，文本里可能自然出现这些词，不做词语检查。
TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".html", ".js", ".cpp", ".hpp", ".h"}
PUBLIC_REFERENCE = "kaggriculture/agents/reference/champion.py"  # 公开对手，随选手包分发
FORBIDDEN_WORDS = re.compile(r"organizer|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd|/data/ws|/data/home")
FORBIDDEN_NAMES = re.compile(r"(^|/)(\.git|__pycache__|hidden|official|reference)(/|$)|\.(pyc|so|log)$|problem_mod\.py$", re.IGNORECASE)


def prepare_suite_root(dest: Path, reset_submissions: bool = False) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in SUITE_DOCS:
        shutil.copyfile(SUITE_DOCS_DIR / name, dest / name)
    if reset_submissions:
        for extra in ("logs", ".scratch", "results", "replays"):
            extra_path = dest / extra
            if extra_path.is_dir():
                shutil.rmtree(extra_path)
            elif extra_path.exists():
                extra_path.unlink()
    sub_dir = dest / "submissions"
    if reset_submissions and sub_dir.exists():
        shutil.rmtree(sub_dir)
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / ".gitkeep").write_text("", encoding="utf-8", newline="\n")


def _prune_stale_problems(dest: Path, active: set[str]) -> None:
    if not dest.is_dir():
        return
    for entry in dest.iterdir():
        if entry.is_dir() and entry.name not in SUITE_RESERVED_DIRS and entry.name not in active:
            shutil.rmtree(entry)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protected_hashes(problems: list[str]) -> dict[str, Path]:
    """隐藏数据、参考对手等不得进入选手包的文件。公开样例除外。"""
    hashes: dict[str, Path] = {}
    for prob in problems:
        base = ORGANIZER_ROOT / prob
        for directory in (base / "data" / "official", base / "hidden", base / "agents" / "reference"):
            for path in directory.rglob("*") if directory.is_dir() else ():
                if (
                    path.is_file()
                    and path.stat().st_size > 32
                    and not path.name.startswith("sample")
                    and path.relative_to(ORGANIZER_ROOT).as_posix() != PUBLIC_REFERENCE
                ):
                    hashes[_sha256(path)] = path
        problem_mod = base / "engine" / "problem_mod.py"
        if problem_mod.is_file():
            hashes[_sha256(problem_mod)] = problem_mod
        bots_dir = base / "bots"
        if bots_dir.is_dir():
            for entry in bots_dir.iterdir():
                if entry.name.startswith((".", "_")) or entry.name in ("medium", "advanced"):
                    continue
                if entry.is_dir():
                    for bot_file in entry.rglob("*"):
                        if (
                            bot_file.is_file()
                            and bot_file.stat().st_size > 32
                            and bot_file.name not in ("sdk.py", "main.py")
                            and "__pycache__" not in bot_file.parts
                            and bot_file.suffix != ".pyc"
                        ):
                            hashes[_sha256(bot_file)] = bot_file
                elif entry.is_file() and entry.suffix == ".zip" and entry.stat().st_size > 32:
                    hashes[_sha256(entry)] = entry
    return hashes


def check_purity(dest: Path, problems: list[str] | None = None) -> list[str]:
    """返回选手包的纯净性问题。空列表表示通过。"""
    roster_probs = selected_problems() if problems is None else problems
    protected = _protected_hashes(roster_probs)
    protected_sizes = {p.stat().st_size for p in protected.values()}
    issues: list[str] = []
    for path in sorted(dest.rglob("*")):
        relative = path.relative_to(dest)
        rel_posix = relative.as_posix()
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            issues.append(f"禁止的链接：{relative}")
            continue
        if not path.is_file():
            continue
        if FORBIDDEN_NAMES.search(rel_posix) and rel_posix != PUBLIC_REFERENCE:
            issues.append(f"禁止的文件：{relative}")
        size = path.stat().st_size
        if size > 32 and size in protected_sizes and _sha256(path) in protected:
            issues.append(f"与保密文件相同：{relative}")
        if path.suffix in TEXT_SUFFIXES and "data/dev" not in rel_posix:
            match = FORBIDDEN_WORDS.search(path.read_text(encoding="utf-8", errors="replace"))
            if match:
                issues.append(f"含禁用词 {match[0]}：{relative}")
    return issues


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    roster = load_roster()
    problems = selected_problems(roster)

    ap = argparse.ArgumentParser(description="导出干净的 AVL Final Bench（Final 正式入选集）参赛选手工作区")
    ap.add_argument("--out", type=Path, default=DEFAULT_DEST, help="输出目标目录（默认 Final/contestant）")
    ap.add_argument("--problems", nargs="*", default=None, help="可选：只导出当前正式名单中的部分赛题")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--root-only", action="store_true")
    ap.add_argument("--zip", action="store_true")
    ns = ap.parse_args()

    if ns.problems is None:
        target_problems = list(problems)
        full_export = True
    else:
        invalid = [p for p in ns.problems if p not in problems]
        if invalid:
            ap.error(
                f"以下赛题不在 Final 正式名单中：{', '.join(invalid)}"
                + (f"（当前已入选：{', '.join(problems)}）" if problems else "（当前正式名单为空）")
            )
        target_problems = list(ns.problems)
        full_export = False

    dest = ns.out.resolve()
    if dest.is_relative_to(ORGANIZER_ROOT):
        raise SystemExit(f"选手目录不能位于 Final/organizer/ 内部：{dest}")
    if dest == (RESERVE_ROOT / "contestant").resolve():
        raise SystemExit(f"禁止覆盖仓库根目录的备选选手工作区：{dest}")
    if dest.exists() and not ns.force and not ns.root_only:
        raise SystemExit(f"目标目录已存在：{dest}（如需覆盖刷新请加 --force，或用 --out 指定新目录）")

    # 始终先根据 final_roster.json 刷新顶层 README.md 与 AGENTS.md
    sync_suite_docs(roster)

    prepare_suite_root(dest, reset_submissions=(ns.force and not ns.root_only))
    if ns.root_only:
        print(f"已刷新顶层 README.md、AGENTS.md 与 submissions/：{dest}")
        return

    if ns.force and full_export:
        _prune_stale_problems(dest, set(target_problems))

    if not target_problems:
        print("提示：当前 Final 正式赛题名单为空（0 题），已仅初始化顶层说明与 submissions/。")
    else:
        for prob in target_problems:
            pack_script = ORGANIZER_ROOT / prob / "scripts" / "pack_contestant.py"
            if not pack_script.is_file():
                raise SystemExit(
                    f"缺少赛题导出脚本：{pack_script}（请先用 promote_problem.py --add {prob} 选入）"
                )
            prob_dest = dest / prob
            cmd = [sys.executable, "-X", "utf8", str(pack_script), "--out", str(prob_dest)]
            if ns.force:
                cmd.append("--force")
            subprocess.run(cmd, cwd=ORGANIZER_ROOT / prob, check=True)
            print(f"  [OK] {prob:<18s} -> {prob_dest}")

    issues = check_purity(dest, problems)
    if issues:
        raise SystemExit("选手包未通过纯净检查，未打包：\n" + "\n".join(issues))
    print(f"纯净检查通过：{dest}")

    if ns.zip:
        archive = dest.with_name(dest.name + ".zip")
        if archive.exists() and not ns.force:
            raise SystemExit(f"压缩包已存在：{archive}")
        no_compress = {".parquet", ".gz", ".zst", ".zip", ".pt", ".pth", ".safetensors", ".npz"}
        with tempfile.TemporaryDirectory(prefix="contestant-zip-", dir=archive.parent) as staging:
            archive_temp = Path(staging) / archive.name
            expected_members = []
            with zipfile.ZipFile(archive_temp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for p in sorted(dest.rglob("*")):
                    if p.is_file():
                        member = p.relative_to(dest).as_posix()
                        expected_members.append(member)
                        comp = zipfile.ZIP_STORED if p.suffix.lower() in no_compress else zipfile.ZIP_DEFLATED
                        zf.write(p, member, compress_type=comp)
            with zipfile.ZipFile(archive_temp) as zf:
                if zf.namelist() != expected_members or any(
                    FORBIDDEN_NAMES.search(name) and name != PUBLIC_REFERENCE for name in zf.namelist()
                ):
                    raise SystemExit("压缩包条目未通过纯净检查")
            archive_temp.replace(archive)
        print(f"已生成压缩包：{archive}")

    print(f"\nFinal 选手工作区已就绪：{dest}（已导出 {len(target_problems)} 题，统一交卷目录：{dest / 'submissions'}）")


if __name__ == "__main__":
    main()
