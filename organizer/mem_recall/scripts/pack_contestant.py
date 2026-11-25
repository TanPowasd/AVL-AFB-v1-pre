"""Export clean contestant workspace for mem_recall."""
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
DEFAULT_DESTINATION = ROOT.parent.parent / "contestant" / "mem_recall"
EMPTY_DIRS = ("results", "logs")
PRESERVE_DATA = True
EXPECTED_DEV_FILES = 23

SPOILER_PATTERN = re.compile(
    r"\borganizer\b|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd",
    re.IGNORECASE,
)
TEXT_SUFFIXES = {".md", ".txt", ".json", ".py", ".html", ".js"}

PUBLIC_MAP = (
    ("config/contestant_readme.md", "README.md"),
    ("agents/submissions/.gitkeep", "agents/submissions/.gitkeep"),
    ("agents/starter.py", "agents/starter.py"),
    ("agents/baselines/bm25.py", "agents/baselines/bm25.py"),
    ("agents/baselines/charoverlap.py", "agents/baselines/charoverlap.py"),
    ("config/format.json", "config/format.json"),
    ("engine/runner.py", "engine/runner.py"),
    ("engine/policy.py", "engine/policy.py"),
    ("scripts/spar.py", "scripts/spar.py"),
)


def source_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Invalid or missing public file: {relative}")
    return path


def _clear_dir_except(directory: Path, keep_names: set[str]) -> None:
    for child in list(directory.iterdir()):
        if child.name in keep_names:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def export_kit(destination: Path, root: Path = ROOT, make_zip: bool = False, force: bool = False) -> Path:
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    archive = destination.with_name(destination.name + ".zip")
    if destination.is_relative_to(root):
        raise ValueError(f"Contestant folder must be outside organizer directory: {destination}")

    sources = [(src_rel, dst_rel, source_file(root, src_rel)) for src_rel, dst_rel in PUBLIC_MAP]

    saved_data_dir: Path | None = None
    canonical_candidates = [
        DEFAULT_DESTINATION.resolve() / "data",
        # 当从 Final/organizer/mem_recall 导出到外部临时目录时，回退至仓库根 contestant/mem_recall/data
        root.parent.parent.parent / "contestant" / "mem_recall" / "data",
    ]
    in_place = False

    if destination.exists():
        if not force:
            raise ValueError(f"Output already exists; choose a fresh folder or pass --force: {destination}")
        if PRESERVE_DATA and (destination / "data").is_dir():
            saved_data_dir = destination.parent / f".preserve_data_{destination.name}"
            if saved_data_dir.exists():
                shutil.rmtree(saved_data_dir)
            shutil.move(str(destination / "data"), str(saved_data_dir))
        try:
            shutil.rmtree(destination)
        except OSError:
            _clear_dir_except(destination, set())
            in_place = True

    try:
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
                encoding="utf-8",
                newline="\n",
            )
            for d in EMPTY_DIRS:
                (staging / d).mkdir(exist_ok=True)
                (staging / d / ".gitkeep").write_text("", encoding="utf-8", newline="\n")

            if PRESERVE_DATA:
                if saved_data_dir is not None and saved_data_dir.is_dir():
                    shutil.move(str(saved_data_dir), str(staging / "data"))
                    saved_data_dir = None
                else:
                    src_data = next((c for c in canonical_candidates if (c / "dev").is_dir()), None)
                    if src_data is None:
                        raise ValueError("Missing public dev dataset directory (data/dev) for mem_recall export")
                    shutil.copytree(src_data, staging / "data")

            dev_dir = staging / "data" / "dev"
            dev_files = [p for p in dev_dir.iterdir() if p.is_file() and p.name.endswith((".json", ".json.gz"))] if dev_dir.is_dir() else []
            if len(dev_files) < EXPECTED_DEV_FILES:
                raise ValueError(f"Incomplete mem_recall data/dev: expected >= {EXPECTED_DEV_FILES} files, got {len(dev_files)}")

            # 内置纯净检查（跳过 data/dev 公开语料内容）
            leaks: list[str] = []
            for p in sorted(staging.rglob("*")):
                if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                rel = p.relative_to(staging).as_posix()
                if rel.startswith("data/dev/"):
                    continue
                m = SPOILER_PATTERN.search(p.read_text(encoding="utf-8", errors="replace"))
                if m:
                    leaks.append(f"{rel}: 命中敏感词 {m.group(0)!r}")
            if leaks:
                raise ValueError("mem_recall 选手包未通过纯净检查：\n" + "\n".join(leaks))

            hashes = {
                p.relative_to(staging).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(staging.rglob("*"))
                if p.is_file() and not p.relative_to(staging).as_posix().startswith("data/dev/")
            }
            inventory = {
                "format": "afb-contestant-kit/2",
                "submission": "agents/submissions/my_agent.py",
                "unified_submission": "../submissions/mem_recall.py",
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
    except BaseException:
        # 出错时归还已暂存的 data/ 目录，避免数据孤悬丢失
        if saved_data_dir is not None and saved_data_dir.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            if not (destination / "data").exists():
                shutil.move(str(saved_data_dir), str(destination / "data"))
        raise
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
    print("Submission:", "agents/submissions/my_agent.py")


if __name__ == "__main__":
    main()
