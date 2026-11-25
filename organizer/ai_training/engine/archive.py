# -*- coding: utf-8 -*-
"""前沿实验室提交压缩包安全校验、受限流式解压与哈希工具（仅用 Python 标准库）。

支持三种提交形态：
  1. .zip 压缩包（推荐，根目录含 main.py，可附带子模块与只读数据文件）
  2. 根目录含 main.py 的策略目录
  3. 兼容单文件 .py
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

MAX_COMPRESSED_BYTES = 64 * 1024 * 1024    # 64 MiB
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024    # 256 MiB
MAX_ARCHIVE_ENTRIES = 2048
MAX_COMPRESSION_RATIO = 100

WIN_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
WIN_ILLEGAL_CHARS = frozenset('<>:"|?*')


class ArchiveError(Exception):
    """压缩包格式或安全性校验错误。"""


def is_submission_path(path: str) -> bool:
    """判断路径是否为合法提交形态（.zip 压缩包、含 main.py 的目录、或 .py 单文件）。"""
    p = os.path.abspath(path)
    if os.path.isfile(p):
        low = p.lower()
        return low.endswith(".zip") or low.endswith(".py")
    if os.path.isdir(p):
        return os.path.isfile(os.path.join(p, "main.py"))
    return False


def submission_label(path: str) -> str:
    """提取提交显示名。"""
    p = os.path.normpath(os.path.abspath(path))
    base = os.path.basename(p)
    if os.path.isdir(p):
        return base
    return os.path.splitext(base)[0]


def sha256_path(path: str) -> str:
    """计算单个文件（.zip/.py）或策略目录（按排序相对路径）的 SHA-256。"""
    p = Path(path).resolve()
    if p.is_file():
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    if p.is_dir():
        h = hashlib.sha256()
        files = sorted(
            (
                x
                for x in p.rglob("*")
                if x.is_file()
                and "__pycache__" not in x.parts
                and x.suffix.lower() not in (".pyc", ".pyo")
            ),
            key=lambda x: x.relative_to(p).as_posix(),
        )
        for f in files:
            rel = f.relative_to(p).as_posix().encode("utf-8")
            h.update(rel + b"\0" + hashlib.sha256(f.read_bytes()).hexdigest().encode("ascii") + b"\n")
        return h.hexdigest()
    raise FileNotFoundError(f"找不到提交路径：{path}")


def _validate_zip_info(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if "\x00" in info.orig_filename or "\x00" in name:
        raise ArchiveError("压缩包条目含非法空字符 NUL")
    if not (info.flag_bits & 0x800) and not name.isascii():
        raise ArchiveError(f"压缩包条目含非 UTF-8 路径：{name!r}")
    if info.flag_bits & 1:
        raise ArchiveError(f"压缩包不支持加密条目：{name}")
    parts = name.rstrip("/").split("/")
    if (
        not name
        or "\\" in name
        or "\\" in info.orig_filename
        or ":" in name
        or ":" in info.orig_filename
        or name.startswith("/")
        or info.orig_filename.startswith("/")
        or any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in parts)
    ):
        raise ArchiveError(f"压缩包条目路径不安全：{info.orig_filename!r}")
    for part in parts:
        if any(ord(ch) < 32 or ch in WIN_ILLEGAL_CHARS for ch in part):
            raise ArchiveError(f"压缩包条目含非法字符：{name!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in WIN_RESERVED_NAMES:
            raise ArchiveError(f"压缩包条目含系统保留名：{name!r}")
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ArchiveError(f"压缩包禁止非常规文件/符号链接：{name}")
    if not info.is_dir() and info.file_size > MAX_COMPRESSION_RATIO * max(1, info.compress_size):
        raise ArchiveError(f"压缩包条目压缩比超限（>{MAX_COMPRESSION_RATIO}x）：{name}")
    return PurePosixPath(*parts)


def check_archive(path: str) -> list[str]:
    """校验 .zip 压缩包安全性与完整性，返回问题列表（空列表表示通过）。"""
    problems: list[str] = []
    p = Path(path)
    if not p.is_file():
        return [f"找不到压缩包：{path}"]
    size = p.stat().st_size
    if size > MAX_COMPRESSED_BYTES:
        return [f"压缩包体积 {size} 字节超过上限 {MAX_COMPRESSED_BYTES} 字节（64 MiB）"]
    try:
        with zipfile.ZipFile(p, "r") as zf:
            infos = zf.infolist()
            if not infos:
                return ["压缩包为空"]
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                return [f"压缩包条目数 {len(infos)} 超过上限 {MAX_ARCHIVE_ENTRIES}"]
            total_size = 0
            names_cf: set[str] = set()
            regular_cf: set[str] = set()
            parsed: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            for info in infos:
                try:
                    rel = _validate_zip_info(info)
                except ArchiveError as exc:
                    problems.append(str(exc))
                    continue
                key = str(rel).casefold()
                if key in names_cf:
                    problems.append(f"压缩包存在大小写冲突路径：{rel}")
                    continue
                names_cf.add(key)
                if not info.is_dir():
                    regular_cf.add(key)
                    total_size += info.file_size
                parsed.append((info, rel))
            if total_size > MAX_EXTRACTED_BYTES:
                problems.append(
                    f"压缩包解压总大小 {total_size} 字节超过上限 {MAX_EXTRACTED_BYTES} 字节（256 MiB）"
                )
            for _, rel in parsed:
                if any(str(parent).casefold() in regular_cf for parent in rel.parents if str(parent) != "."):
                    problems.append(f"压缩包文件遮蔽目录：{rel}")
            has_main = any((not info.is_dir()) and str(rel) == "main.py" for info, rel in parsed)
            if not has_main:
                problems.append("压缩包根目录缺少入口文件 main.py")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError, OSError) as exc:
        problems.append(f"无法解析压缩包：{exc}")
    return problems


def extract_archive(archive_path: str, dest_dir: str) -> str:
    """校验并将 .zip 安全流式解压至 dest_dir（必须为空或新建目录）。"""
    problems = check_archive(archive_path)
    if problems:
        raise ArchiveError("; ".join(problems))
    root = Path(dest_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                rel = _validate_zip_info(info)
                target = root.joinpath(*rel.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with zf.open(info, "r") as src, target.open("xb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        written += len(chunk)
                        if total > MAX_EXTRACTED_BYTES or written > info.file_size:
                            raise ArchiveError("压缩包流式解压超出体积配额")
                        dst.write(chunk)
                if written != info.file_size:
                    raise ArchiveError(f"压缩包条目解压大小不匹配：{rel}")
    except ArchiveError:
        raise
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError, OSError) as exc:
        raise ArchiveError(f"解压失败：{exc}") from exc
    if not (root / "main.py").is_file():
        raise ArchiveError("压缩包根目录缺少入口文件 main.py")
    return str(root)
