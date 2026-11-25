# -*- coding: utf-8 -*-
"""Export clean contestant workspace for ghost_rabbit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DESTINATION = ROOT.parent.parent / "contestant" / "ghost_rabbit"
EMPTY_DIRS = ("results", "logs", "replays")

# 零剧透门禁：禁止任何隐藏正式交互器家族名、内部文件或维护者敏感词流入选手包
SPOILER_PATTERN = re.compile(
    r"F1_PrimeCone|F2_WallHugger|F3_QuantumAdv|F4_Stochastic|"
    r"official_interactor|official_judge|ConcreteOfficialRabbit|QuantumAdversaryRabbit|"
    r"\borganizer\b|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd",
    re.IGNORECASE,
)
TEXT_SUFFIXES = {".md", ".txt", ".json", ".py", ".cpp", ".hpp", ".h", ".html", ".js"}

PUBLIC_MAP = (
    ("config/contestant_readme.md", "README.md"),
    ("AI_README.md", "AI_README.md"),
    ("AGENTS.md", "AGENTS.md"),
    ("FORMAT.md", "FORMAT.md"),
    ("handbook/contest-rules.md", "handbook/contest-rules.md"),
    ("handbook/geometry-guide.md", "handbook/geometry-guide.md"),
    ("prompts/task.txt", "prompts/task.txt"),
    ("config/format.json", "config/format.json"),
    ("agents/submissions/.gitkeep", "agents/submissions/.gitkeep"),
    ("agents/starter.cpp", "agents/starter.cpp"),
    ("agents/baselines/blitz_150.cpp", "agents/baselines/blitz_150.cpp"),
    ("agents/baselines/staggered_125.cpp", "agents/baselines/staggered_125.cpp"),
    ("engine/judge_core.hpp", "engine/judge_core.hpp"),
    ("engine/practice_interactor.cpp", "engine/practice_interactor.cpp"),
    ("engine/common.py", "engine/common.py"),
    ("engine/policy.py", "engine/policy.py"),
    ("engine/runner.py", "engine/runner.py"),
    ("engine/replay.py", "engine/replay.py"),
    ("scripts/spar.py", "scripts/spar.py"),
    ("scripts/record_replay.py", "scripts/record_replay.py"),
    ("viz/player.html", "viz/player.html"),
    ("replays/replay_demo.json", "replays/replay_demo.json"),
)


def source_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Invalid or missing public file: {relative}")
    return path


def export_kit(destination: Path, root: Path = ROOT, make_zip: bool = False, force: bool = False) -> Path:
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    archive = destination.with_name(destination.name + ".zip")
    if destination.is_relative_to(root):
        raise ValueError(f"Contestant folder must be outside source directory: {destination}")

    sources = [(src_rel, dst_rel, source_file(root, src_rel)) for src_rel, dst_rel in PUBLIC_MAP]

    in_place = False
    if destination.exists():
        if not force:
            raise ValueError(f"Output already exists; choose a fresh folder or pass --force: {destination}")
        try:
            shutil.rmtree(destination)
        except OSError:
            # Windows 上若外部进程（如资源管理器/终端）持有空目录句柄，回退为清空内容并就地覆盖
            for child in list(destination.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            in_place = True

    if make_zip and archive.exists():
        if not force:
            raise ValueError(f"Archive already exists: {archive}")
        archive.unlink()

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".contestant-kit-", dir=destination.parent) as tmp:
        staging = Path(tmp) / "kit"
        for _, dst_rel, src_path in sources:
            target = staging / dst_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_path, target)
            if target.suffix.lower() in TEXT_SUFFIXES:
                text = target.read_text(encoding="utf-8", errors="replace")
                m = SPOILER_PATTERN.search(text)
                if m:
                    raise ValueError(f"Spoiler/purity leak {m.group(0)!r} detected in exported file: {dst_rel}")

        (staging / "config/workspace.json").write_text(
            json.dumps({"format": "afb-contestant-workspace/1", "mode": "contestant"}, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        for d in EMPTY_DIRS:
            (staging / d).mkdir(exist_ok=True)
            gk = staging / d / ".gitkeep"
            if not gk.exists():
                gk.write_text("", encoding="utf-8", newline="\n")

        hashes = {
            p.relative_to(staging).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(staging.rglob("*"))
            if p.is_file()
        }
        inventory = {
            "format": "afb-contestant-kit/2",
            "submission": "agents/submissions/my_solution.cpp",
            "unified_submission": "../submissions/ghost_rabbit.cpp",
            "files": hashes,
        }
        (staging / "manifest.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if make_zip:
            with zipfile.ZipFile(Path(tmp) / "kit.zip", "w", zipfile.ZIP_DEFLATED) as zf:
                for p in sorted(staging.rglob("*")):
                    if p.is_file():
                        zf.write(p, p.relative_to(staging).as_posix())
        if in_place:
            shutil.copytree(staging, destination, dirs_exist_ok=True)
        else:
            shutil.move(str(staging), str(destination))
        if make_zip:
            shutil.move(str(Path(tmp) / "kit.zip"), str(archive))
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--zip", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        dest = export_kit(args.out, make_zip=args.zip, force=args.force)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    print("Contestant folder:", dest)
    print("Submission:", "../submissions/ghost_rabbit.cpp")


if __name__ == "__main__":
    main()
