"""把一个策略目录打成提交 zip（根目录必须有 main.py；只能用标准库）。

    python3 scripts/make_submission.py agents/template --out ../submissions/rift_era.zip
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys
import zipfile

MAX_COMPRESSED = 512 * 1024**2
MAX_EXTRACTED = 2 * 1024**3
MAX_ENTRIES = 4096
STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
_WIN_RESERVED = frozenset({'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                           *(f'LPT{i}' for i in range(1, 10))})
_WIN_ILLEGAL = frozenset('<>:"|?*')


def third_party_imports(path: Path, local: set[str]) -> set[str]:
    found = set()
    tree = ast.parse(path.read_text(encoding='utf-8'), str(path))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for name in names:
            top = name.split('.')[0]
            if STDLIB and top not in STDLIB and top not in local:
                found.add(top)
    return found


def _validate_archive_paths(rel_paths: list[str]) -> None:
    seen: set[str] = set()
    regular: set[str] = set()
    parsed = []
    for rel in rel_paths:
        parts = rel.split('/')
        if (not rel or '\\' in rel or '\x00' in rel or ':' in rel or rel.startswith('/') or
                any(c in _WIN_ILLEGAL or ord(c) < 32 for c in rel) or
                any(p in ('', '.', '..') or p.endswith((' ', '.')) or
                    p.split('.', 1)[0].rstrip(' ').upper() in _WIN_RESERVED for p in parts)):
            raise SystemExit(f'非法或不安全的归档路径（含 Windows 保留名或非法字符）：{rel!r}')
        key = rel.casefold()
        if key in seen:
            raise SystemExit(f'存在大小写冲突的文件路径：{rel!r}')
        seen.add(key)
        regular.add(key)
        parsed.append(parts)
    for parts in parsed:
        for i in range(1, len(parts)):
            if '/'.join(parts[:i]).casefold() in regular:
                raise SystemExit(f'文件路径与目录重名冲突：{"/".join(parts)!r}')


def build(source: Path, out: Path) -> dict:
    source = source.resolve()
    if not (source / 'main.py').is_file():
        raise SystemExit(f'{source} 根目录没有 main.py')
    files = sorted(p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts
                   and p.suffix != '.pyc' and not p.name.startswith('.'))
    if len(files) > MAX_ENTRIES or sum(p.stat().st_size for p in files) > MAX_EXTRACTED:
        raise SystemExit('文件数或总大小超限（4096 个 / 2 GiB）')
    rel_paths = [p.relative_to(source).as_posix() for p in files]
    _validate_archive_paths(rel_paths)
    local = {p.stem for p in files if p.suffix == '.py'} | {p.name for p in source.iterdir() if p.is_dir()}
    bad = set()
    for p in files:
        if p.suffix == '.py':
            bad |= third_party_imports(p, local)
    if 'sdk' in bad and not (source / 'sdk.py').is_file():
        raise SystemExit('import sdk 但目录里没有 sdk.py（从选手包根目录复制一份）')
    if bad:
        raise SystemExit(f'用到了非标准库模块 {sorted(bad)}：评测沙箱里没有第三方包')
    if 'sdk' in local and not (source / 'sdk.py').is_file():
        raise SystemExit('import sdk 但目录里没有 sdk.py（从选手包根目录复制一份）')
    out.parent.mkdir(parents=True, exist_ok=True)
    over_ratio = None
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p, rel in zip(files, rel_paths):
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, p.read_bytes())
        for info in zf.infolist():
            if info.file_size > 100 * max(1, info.compress_size):
                over_ratio = info.filename
                break
    if over_ratio is not None:
        out.unlink(missing_ok=True)
        raise SystemExit(f'文件压缩比超过 100 倍（会被评测解包器拒绝）：{over_ratio!r}')
    if out.stat().st_size > MAX_COMPRESSED:
        out.unlink(missing_ok=True)
        raise SystemExit('zip 超过 512 MiB')
    return {'zip': str(out), 'files': rel_paths, 'bytes': out.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, default=Path('../submissions/rift_era.zip'))
    args = parser.parse_args()
    result = build(args.source, args.out)
    print(f"已生成 {result['zip']}（{result['bytes']} 字节）：{', '.join(result['files'])}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
