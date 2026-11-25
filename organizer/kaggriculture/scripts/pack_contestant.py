"""Export clean contestant workspace for kaggriculture."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DESTINATION = ROOT.parent.parent / "contestant" / 'kaggriculture'
EMPTY_DIRS = ('results', 'replays', 'logs')
PRESERVE_DATA = False

PUBLIC_MAP = (
    ('config/contestant_readme.md', 'README.md'),
    ('AGENTS.md', 'AGENTS.md'),
    ('AI_README.md', 'AI_README.md'),
    ('handbook/contest-rules.md', 'handbook/contest-rules.md'),
    ('handbook/contest-rules.md', 'FORMAT.md'),
    ('handbook/player-guide.md', 'handbook/player-guide.md'),
    ('handbook/game-facts.md', 'handbook/game-facts.md'),
    ('prompts/task.txt', 'prompts/task.txt'),
    ('agents/submissions/.gitkeep', 'agents/submissions/.gitkeep'),
    ('agents/submissions/README.md', 'agents/submissions/README.md'),
    ('agents/starter.py', 'agents/starter.py'),
    ('agents/template/main.py', 'agents/template/main.py'),
    ('agents/template/planner.py', 'agents/template/planner.py'),
    ('agents/template/params.json', 'agents/template/params.json'),
    ('agents/baselines/passive.py', 'agents/baselines/passive.py'),
    ('agents/baselines/random_legal.py', 'agents/baselines/random_legal.py'),
    ('agents/baselines/greedy_farm.py', 'agents/baselines/greedy_farm.py'),
    ('engine/rules.py', 'engine/rules.py'),
    ('engine/arena.py', 'engine/arena.py'),
    ('engine/loader.py', 'engine/loader.py'),
    ('engine/policy.py', 'engine/policy.py'),
    ('engine/replay.py', 'engine/replay.py'),
    ('config/format.json', 'config/format.json'),
    ('scripts/make_submission.py', 'scripts/make_submission.py'),
    ('scripts/spar.py', 'scripts/spar.py'),
    ('scripts/run_match.py', 'scripts/run_match.py'),
    ('scripts/record_replay.py', 'scripts/record_replay.py'),
    ('viz/player.html', 'viz/player.html'),
    ('agents/reference/champion.py', 'agents/reference/champion.py'),
)


def public_files(root: Path) -> tuple:
    """公开 champion 的运行代码。说明文档和来源记录含本机路径，不导出。"""
    package = root / 'engine' / 'champion'
    files = sorted(p.relative_to(root).as_posix() for p in package.rglob('*') if p.is_file()
                   and p.suffix not in ('.md', '.pyc', '.pyo', '.so')
                   and not ({'references', '__pycache__', '_native_cache'} & set(p.parts)))
    return PUBLIC_MAP + tuple((name, name) for name in files)


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
        raise ValueError(f"Contestant folder must be outside organizer directory: {destination}")

    sources = [(src_rel, dst_rel, source_file(root, src_rel)) for src_rel, dst_rel in public_files(root)]

    saved_data_dir = None
    canonical_data = DEFAULT_DESTINATION.resolve() / "data"

    if destination.exists():
        if not force:
            raise ValueError(f"Output already exists; choose a fresh folder or pass --force: {destination}")
        if PRESERVE_DATA and (destination / "data").is_dir():
            saved_data_dir = destination.parent / f".preserve_data_{destination.name}"
            if saved_data_dir.exists():
                shutil.rmtree(saved_data_dir)
            shutil.move(str(destination / "data"), str(saved_data_dir))
        shutil.rmtree(destination)

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

        (staging / "config/workspace.json").write_text(
            json.dumps({"format": "afb-contestant-workspace/1", "mode": "contestant"}, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        for d in EMPTY_DIRS:
            (staging / d).mkdir(exist_ok=True)
            (staging / d / ".gitkeep").write_text("", encoding="utf-8", newline="\n")

        if PRESERVE_DATA:
            if saved_data_dir is not None and saved_data_dir.is_dir():
                shutil.move(str(saved_data_dir), str(staging / "data"))
            elif canonical_data.is_dir():
                shutil.copytree(canonical_data, staging / "data")

        hashes = {
            p.relative_to(staging).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(staging.rglob("*"))
            if p.is_file() and not p.relative_to(staging).as_posix().startswith("data/dev/")
        }
        inventory = {
            "format": "afb-contestant-kit/2",
            "submission": 'agents/submissions/my_agent.zip（根目录 main.py）',
            "unified_submission": '../submissions/kaggriculture.zip',
            "files": hashes,
        }
        (staging / "manifest.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        if make_zip:
            with zipfile.ZipFile(Path(tmp) / "kit.zip", "w", zipfile.ZIP_DEFLATED) as zf:
                for p in sorted(staging.rglob("*")):
                    if p.is_file():
                        zf.write(p, p.relative_to(staging).as_posix())
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
    print("Submission: 目录含 main.py → python scripts/make_submission.py <dir> --out ../submissions/kaggriculture.zip")


if __name__ == "__main__":
    main()
