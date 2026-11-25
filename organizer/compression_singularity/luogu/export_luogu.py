# -*- coding: utf-8 -*-
"""《压缩奇点》洛谷 SPJ 题包一键导出脚本（organizer/compression_singularity/luogu/export_luogu.py）。

生成可直接上传洛谷的 4 份产物：
  1. testdata_for_luogu.zip  —— 根目录完全平铺（无外层文件夹），含 checker.cpp、config.yml 与 16 组隐藏测试点 (*.in / *.out)
  2. samples_attachment.zip  —— 零剧透公开附件（4 组匿名标本 + 00_example + starter.cpp + rle_loop.cpp），供洛谷附加文件上传
  3. luogu_statement.md      —— 可直接粘贴至洛谷题面编辑器的零剧透 Markdown 题面
  4. UPLOAD_README.md        —— 洛谷后台上传配置清单（含参数说明与防数据泄漏提醒）
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import re
import secrets
import sys
import time
import zipfile

LUOGU_DIR = Path(__file__).resolve().parent
PROBLEM_ROOT = LUOGU_DIR.parent
sys.path.insert(0, str(PROBLEM_ROOT / "engine"))
sys.path.insert(0, str(PROBLEM_ROOT / "hidden"))

import generator  # noqa: E402
import vm  # noqa: E402

# 与公开标本种子完全隔离的洛谷专用固定种子（可通过 --random-seeds 换为全新 OS-CSPRNG 种子）
# 按打乱顺序交错 4 个隐藏家族，避免测试点编号顺序泄露家族分组！
LUOGU_DEFAULT_SCHEDULE: tuple[tuple[str, int, int], ...] = (
    # (family, length, seed)
    ("F2", 100_000, 91041001),
    ("F1", 100_000, 91041002),
    ("F4", 100_000, 91041003),
    ("F3", 100_000, 91041004),
    ("F3", 100_000, 91041005),
    ("F4", 100_000, 91041006),
    ("F1", 100_000, 91041007),
    ("F2", 100_000, 91041008),
    ("F4", 500_000, 91042001),
    ("F2", 500_000, 91042002),
    ("F3", 500_000, 91042003),
    ("F1", 500_000, 91042004),
    ("F1", 500_000, 91042005),
    ("F3", 500_000, 91042006),
    ("F2", 500_000, 91042007),
    ("F4", 500_000, 91042008),
)

SPOILER_PATTERN = re.compile(
    r"(L-System|twisted_slp|primitive_lfsr|framed_protocol|coupled_lfsr|streaming_maze|"
    r"Berlekamp|Massey|LFSR|\bECA\b|Elementary Cellular|元胞自动机|线性反馈移位|规则\s*30|规则\s*110|"
    r"\bF1\b|\bF2\b|\bF3\b|\bF4\b|组委会|\borganizer\b)",
    re.IGNORECASE,
)


def _check_zero_spoilers(text: str, label: str) -> None:
    m = SPOILER_PATTERN.search(text)
    if m:
        raise RuntimeError(f"Zero-spoiler check FAILED on {label}: matched {m.group(0)!r}")


def _build_cases(
    *,
    scales: list[int],
    random_seeds: bool,
    workers: int,
) -> list[dict]:
    used_seeds = set(generator.PUBLIC_SPECIMEN_SEEDS)
    if scales == [100_000, 100_000, 500_000, 500_000] and not random_seeds:
        specs = list(LUOGU_DEFAULT_SCHEDULE)
    else:
        # 交错排列 4 家族与指定长度档位
        fams = ["F2", "F1", "F4", "F3"]
        specs = []
        base_seed = 92050000
        idx = 0
        for scale_idx, length in enumerate(scales):
            rot = fams[scale_idx % 4:] + fams[:scale_idx % 4]
            for fam in rot:
                idx += 1
                if random_seeds:
                    while True:
                        s = secrets.randbits(63) + 1
                        if s not in used_seeds:
                            used_seeds.add(s)
                            break
                else:
                    s = base_seed + idx * 1009
                specs.append((fam, int(length), int(s)))

    def _gen_one(item: tuple[int, tuple[str, int, int]]) -> dict:
        i, (fam, length, seed) = item
        s, _meta = generator.generate_case(fam, length, seed)
        b_ref = vm.compute_b_ref(s)
        return {
            "idx": i + 1,
            "name": f"cs{i + 1:02d}",
            "family": fam,
            "length": length,
            "seed": seed,
            "b_ref_bits": b_ref,
            "target_str": s,
        }

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(specs)))) as pool:
        return list(pool.map(_gen_one, enumerate(specs)))


def _write_upload_readme(
    out_path: Path,
    *,
    n_cases: int,
    time_limit_ms: int,
    memory_limit_kb: int,
    score_per_case: int,
    step_limit: int,
    zip_bytes: int,
    zip_sha256: str,
) -> None:
    total_score = n_cases * score_per_case
    text = f"""# 《压缩奇点》洛谷 SPJ 上传配置清单

本目录由 `organizer/compression_singularity/luogu/export_luogu.py` 自动生成，包含直接上传洛谷所需的全部文件：

| 文件 | 用途 | 能否发给选手 |
|---|---|---|
| `testdata_for_luogu.zip` | 洛谷后台“测试数据管理”直接上传（根目录平铺，含 `checker.cpp`、`config.yml`、{n_cases} 组隐藏 `.in`/`.out`） | **否（保密测试点）** |
| `samples_attachment.zip` | 洛谷后台“附加文件”上传（含 `00_example`、4 组 $L=10^5$ 匿名标本、`starter.cpp`、`rle_loop.cpp`） | **是（公开附件）** |
| `luogu_statement.md` | 直接粘贴至洛谷题面编辑器的零剧透 Markdown 题面 | **是（公开题面）** |

## 洛谷后台配置步骤

1. **新建题目（建议放在私有团队题库或比赛中，关闭“允许下载测试数据”）**：
   - 将 `luogu_statement.md` 粘贴至题面，并把样例 #1 填入样例栏。
2. **上传测试数据**：
   - 进入题目后台 → **测试数据管理** → 直接上传 `testdata_for_luogu.zip`（大小 `{zip_bytes / (1024 * 1024):.2f} MiB`，SHA-256 `{zip_sha256[:16]}...`）。
   - 该压缩包已按洛谷要求**根目录完全平铺**（无外层文件夹），包含：
     - `checker.cpp`：纯 C++14（`-fno-asm -std=c++14 -O2` 兼容）`testlib.h` Special Judge，内置 32B 直跳宏步 VM 与 3.5s 软兜底（防 UKE），用 `quitp(NormScore / 100)` 给部分分并在提示信息中返回精确 `RawScore`。
     - `config.yml`：统一配置全部 {n_cases} 个测试点：`timeLimit: {time_limit_ms}`（ms）、`memoryLimit: {memory_limit_kb}`（KB，即 {memory_limit_kb // 1024} MiB）、`score: {score_per_case}`（总分 `{total_score}`）。
     - `cs01.in` ～ `cs{n_cases:02d}.in`：4 个隐藏家族打乱顺序生成的目标串；对应的 `.out` 文件仅存放预计算的 `B_ref`（及可选步数上限）。
   - 确认后台已自动识别 **Special Judge (`checker.cpp`)** 且测试点配置生效。
3. **上传公开附件**：
   - 在题目“附加文件”中上传 `samples_attachment.zip`，供选手在本地分析 4 组匿名标本和起步模板。
4. **语言与计分提示**：
   - 建议开放 C++20（允许选手用标准库与 `-O2`）。
   - 为避免洛谷对单点 `quitp` 四舍五入为整数造成精度损失，默认每点满分设为 `{score_per_case}`（总分 `{total_score}`，保留 1% 连续精度）；若你希望总分正好为 100，可重新运行：
     ```bash
     python organizer/compression_singularity/luogu/export_luogu.py --scales 100000 100000 500000 500000 500000 --score-per-case 5 --out <目录>
     ```
   - 若希望更换全新随机种子数据，添加 `--random-seeds` 参数重新生成即可。
   - 洛谷版默认 VM 步数上限为 `{step_limit:,}` 步（可在 `.out` 第二整数位置覆盖，或在 `checker.cpp` 顶部修改 `DEFAULT_STEP_LIMIT`）。
"""
    out_path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《压缩奇点》洛谷 SPJ 数据包与题面导出工具")
    ap.add_argument(
        "--out",
        default=str(Path.home() / "Desktop" / "luogu_compression_singularity"),
        help="输出目录（默认桌面 luogu_compression_singularity/）",
    )
    ap.add_argument(
        "--scales",
        type=int,
        nargs="+",
        default=[100_000, 100_000, 500_000, 500_000],
        help="每个隐藏家族生成的测试长度列表（默认 100000 100000 500000 500000 -> 共 16 点）",
    )
    ap.add_argument("--time-limit-ms", type=int, default=5000, help="config.yml 单点时限 ms（默认 5000）")
    ap.add_argument("--memory-limit-kb", type=int, default=524288, help="config.yml 单点内存 KB（默认 524288 = 512 MiB）")
    ap.add_argument("--score-per-case", type=int, default=100, help="config.yml 单点分值（默认 100 以保留 quitp 精度）")
    ap.add_argument("--step-limit", type=int, default=2_000_000_000, help="VM 步数上限（默认 2×10^9）")
    ap.add_argument("--random-seeds", action="store_true", help="使用 OS-CSPRNG 生成全新随机种子")
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 4), help="并行生成线程数")
    ns = ap.parse_args()

    out_dir = Path(ns.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    cases = _build_cases(scales=ns.scales, random_seeds=ns.random_seeds, workers=ns.jobs)
    gen_s = time.perf_counter() - t0

    # 1. 构建 config.yml（严格 2 空格缩进，零 Tab）
    yml_lines: list[str] = []
    for c in cases:
        yml_lines.append(f"{c['name']}.in:")
        yml_lines.append(f"  timeLimit: {ns.time_limit_ms}")
        yml_lines.append(f"  memoryLimit: {ns.memory_limit_kb}")
        yml_lines.append(f"  score: {ns.score_per_case}")
        yml_lines.append("")
    config_yml = "\n".join(yml_lines)
    assert "\t" not in config_yml

    # 2. 打包平铺的 testdata_for_luogu.zip（根目录零子文件夹！）
    checker_src = (LUOGU_DIR / "checker.cpp").read_bytes()
    testdata_zip = out_dir / "testdata_for_luogu.zip"
    with zipfile.ZipFile(testdata_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("checker.cpp", checker_src)
        zf.writestr("config.yml", config_yml.encode("utf-8"))
        for c in cases:
            zf.writestr(f"{c['name']}.in", f"{c['length']}\n{c['target_str']}\n".encode("ascii"))
            if ns.step_limit != 2_000_000_000:
                ans_text = f"{c['b_ref_bits']} {ns.step_limit}\n"
            else:
                ans_text = f"{c['b_ref_bits']}\n"
            zf.writestr(f"{c['name']}.out", ans_text.encode("ascii"))

    zip_bytes = testdata_zip.stat().st_size
    zip_sha = hashlib.sha256(testdata_zip.read_bytes()).hexdigest()

    # 3. 打包零剧透公开附件 samples_attachment.zip
    samples_dir = PROBLEM_ROOT / "data" / "samples"
    samples_zip = out_dir / "samples_attachment.zip"
    pub_files = [
        (samples_dir / "00_example.in", "00_example.in"),
        (samples_dir / "00_example.out", "00_example.out"),
        (samples_dir / "specimen_1.in", "specimen_1.in"),
        (samples_dir / "specimen_2.in", "specimen_2.in"),
        (samples_dir / "specimen_3.in", "specimen_3.in"),
        (samples_dir / "specimen_4.in", "specimen_4.in"),
        (samples_dir / "manifest.json", "manifest.json"),
        (PROBLEM_ROOT / "agents" / "starter.cpp", "starter.cpp"),
        (PROBLEM_ROOT / "agents" / "baselines" / "rle_loop.cpp", "rle_loop.cpp"),
    ]
    with zipfile.ZipFile(samples_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src, arcname in pub_files:
            raw = src.read_text(encoding="utf-8")
            _check_zero_spoilers(raw, arcname)
            zf.writestr(arcname, raw.encode("utf-8"))

    # 4. 导出零剧透洛谷题面 luogu_statement.md
    stmt_text = (LUOGU_DIR / "statement.md").read_text(encoding="utf-8")
    _check_zero_spoilers(stmt_text, "luogu_statement.md")
    (out_dir / "luogu_statement.md").write_text(stmt_text, encoding="utf-8", newline="\n")

    # 5. 导出 UPLOAD_README.md
    _write_upload_readme(
        out_dir / "UPLOAD_README.md",
        n_cases=len(cases),
        time_limit_ms=ns.time_limit_ms,
        memory_limit_kb=ns.memory_limit_kb,
        score_per_case=ns.score_per_case,
        step_limit=ns.step_limit,
        zip_bytes=zip_bytes,
        zip_sha256=zip_sha,
    )

    print(f"Exported Luogu SPJ package to: {out_dir} ({gen_s:.2f}s)")
    print(f"  testdata_for_luogu.zip : {zip_bytes / (1024 * 1024):.2f} MiB ({len(cases)} cases, flat root, SHA256={zip_sha[:16]})")
    print(f"  samples_attachment.zip : {samples_zip.stat().st_size / 1024:.1f} KiB ({len(pub_files)} public files, 0 spoilers)")
    print(f"  luogu_statement.md     : ready to paste (0 spoilers)")
    print(f"  UPLOAD_README.md       : upload instructions")
    print()
    print(f"  {'Case':<6s} {'Fam':<4s} {'L':>8s} {'B_ref(bit)':>10s} {'Seed':>12s}")
    for c in cases:
        print(f"  {c['name']:<6s} {c['family']:<4s} {c['length']:>8d} {c['b_ref_bits']:>10d} {c['seed']:>12d}")


if __name__ == "__main__":
    main()
