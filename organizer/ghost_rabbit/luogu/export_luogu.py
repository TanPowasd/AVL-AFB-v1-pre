# -*- coding: utf-8 -*-
"""《天翼幽灵兔：四重奇点追截战》洛谷交互题数据包一键导出脚本（luogu/export_luogu.py）。

生成可直接上传洛谷的 4 份产物：
  1. testdata_for_luogu.zip  —— 根目录完全平铺（无外层文件夹），含 interactor.cpp、config.yml 与 16 组隐藏测试点 (*.in / *.out)
  2. samples_attachment.zip  —— 零剧透公开附件（starter.cpp、blitz_150.cpp、staggered_125.cpp、judge_core.hpp、practice_interactor.cpp）
  3. luogu_statement.md      —— 可直接粘贴至洛谷题面编辑器的零剧透 Markdown 题面
  4. UPLOAD_README.md        —— 洛谷交互题后台上传配置清单
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import secrets
import sys
import zipfile

LUOGU_DIR = Path(__file__).resolve().parent
PROBLEM_ROOT = LUOGU_DIR.parent

# 打乱交错 4 类隐藏强交互器家族（family_id ∈ {1,2,3,4}），避免点号泄露家族分组
LUOGU_DEFAULT_SCHEDULE: tuple[tuple[int, int], ...] = (
    (2, 81041001),
    (1, 81041002),
    (4, 81041003),
    (3, 81041004),
    (3, 81041005),
    (4, 81041006),
    (1, 81041007),
    (2, 81041008),
    (4, 81042001),
    (2, 81042002),
    (3, 81042003),
    (1, 81042004),
    (1, 81042005),
    (3, 81042006),
    (2, 81042007),
    (4, 81042008),
)

SPOILER_PATTERN = re.compile(
    r"F1_PrimeCone|F2_WallHugger|F3_QuantumAdv|F4_Stochastic|"
    r"official_interactor|official_judge|ConcreteOfficialRabbit|QuantumAdversaryRabbit|"
    r"组委会|\borganizer\b|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd",
    re.IGNORECASE,
)


def _check_zero_spoilers(text: str, label: str) -> None:
    m = SPOILER_PATTERN.search(text)
    if m:
        raise RuntimeError(f"Zero-spoiler check FAILED on {label}: matched {m.group(0)!r}")


def _write_upload_readme(
    out_path: Path,
    *,
    n_cases: int,
    time_limit_ms: int,
    memory_limit_kb: int,
    score_per_case: int,
    max_turns: int,
    max_traps: int,
    zip_bytes: int,
    zip_sha256: str,
) -> None:
    total_score = n_cases * score_per_case
    text = f"""# 《天翼幽灵兔：四重奇点追截战》洛谷交互题上传配置清单

本目录由 `organizer/ghost_rabbit/luogu/export_luogu.py` 自动生成：

| 文件 | 用途 | 能否发给选手 |
|---|---|---|
| `testdata_for_luogu.zip` | 洛谷后台“测试数据管理”直接上传（根目录平铺，含 `interactor.cpp`、`config.yml`、{n_cases} 组隐藏 `.in`/`.out`） | **否（保密测试点）** |
| `samples_attachment.zip` | 洛谷后台“附加文件”上传（含起步模板、2 个公开基线与本地练习交互器源码） | **是（公开附件）** |
| `luogu_statement.md` | 直接粘贴至洛谷题面编辑器的零剧透 Markdown 题面 | **是（公开题面）** |

## 洛谷后台配置步骤

1. **新建题目（选择“交互题 / 通信题”模式，关闭“允许下载测试数据”）**：
   - 将 `luogu_statement.md` 粘贴至题面，在样例栏展示 4 回合样例交互。
2. **上传测试数据**：
   - 进入题目后台 → **测试数据管理** → 上传 `testdata_for_luogu.zip`（大小 `{zip_bytes / 1024:.1f} KiB`，SHA-256 `{zip_sha256[:16]}...`）。
   - 该压缩包根目录完全平铺，包含：
     - `interactor.cpp`：严格 C++14（`-std=c++14 -fno-asm -O2` 兼容）`testlib.h` 交互器，内置 4 类强对抗策略与 8.5s 软兜底（防 UKE），通过 `quitp(NormScore / 100)` 给连续部分分并在评语中返回精确 `RawScore`。
     - `config.yml`：全部 {n_cases} 个测试点统一配置 `timeLimit: {time_limit_ms}`（ms）、`memoryLimit: {memory_limit_kb}`（KB = {memory_limit_kb // 1024} MiB）、`score: {score_per_case}`（总分 `{total_score}`）。
     - `gr01.in` ～ `gr{n_cases:02d}.in`：每行 4 个整数 `<family_id> <seed> {max_turns} {max_traps}`（已打乱交错排布）。
   - 确认洛谷后台已识别 **交互器 (`interactor.cpp`)**。
3. **上传公开附件**：
   - 在“附加文件”中上传 `samples_attachment.zip`。
4. **语言与精度说明**：
   - 建议开启 C++20（`-O2`）。
   - 每点分值默认设为 `{score_per_case}` 以保留 `quitp` 1% 连续精度；若希望总分恰好为 100，可运行 `--cases 20 --score-per-case 5`。
   - 如需更换全新随机种子，添加 `--random-seeds` 重新导出即可。
"""
    out_path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="《天翼幽灵兔》洛谷交互题数据包与题面一键导出工具")
    ap.add_argument(
        "--out",
        default=str(Path.home() / "Desktop" / "luogu_ghost_rabbit"),
        help="输出目录（默认桌面 luogu_ghost_rabbit/）",
    )
    ap.add_argument("--cases", type=int, default=16, help="测试点总数（默认 16，建议为 4 的倍数）")
    ap.add_argument("--max-turns", type=int, default=5000, help="单局回合上限（默认 5000）")
    ap.add_argument("--max-traps", type=int, default=4000, help="单局累计固化陷阱上限（默认 4000）")
    ap.add_argument("--time-limit-ms", type=int, default=5000, help="config.yml 单点时限 ms（默认 5000）")
    ap.add_argument("--memory-limit-kb", type=int, default=524288, help="config.yml 单点内存 KB（默认 524288 = 512 MiB）")
    ap.add_argument("--score-per-case", type=int, default=100, help="config.yml 单点满分（默认 100 以保留 quitp 精度）")
    ap.add_argument("--random-seeds", action="store_true", help="使用 OS-CSPRNG 生成全新随机种子")
    ns = ap.parse_args()

    out_dir = Path(ns.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if ns.cases == len(LUOGU_DEFAULT_SCHEDULE) and not ns.random_seeds:
        sched = list(LUOGU_DEFAULT_SCHEDULE)
    else:
        fams = [2, 1, 4, 3]
        sched = []
        used: set[int] = set()
        for i in range(ns.cases):
            fam = fams[(i + (i // 4)) % 4]
            if ns.random_seeds:
                while True:
                    s = secrets.randbits(62) + 1
                    if s not in used:
                        used.add(s)
                        break
            else:
                s = 82050000 + (i + 1) * 1009
            sched.append((fam, s))

    # 1. config.yml（严格 2 空格缩进，零 Tab）
    yml_lines: list[str] = []
    for i in range(len(sched)):
        name = f"gr{i + 1:02d}"
        yml_lines.append(f"{name}.in:")
        yml_lines.append(f"  timeLimit: {ns.time_limit_ms}")
        yml_lines.append(f"  memoryLimit: {ns.memory_limit_kb}")
        yml_lines.append(f"  score: {ns.score_per_case}")
        yml_lines.append("")
    config_yml = "\n".join(yml_lines)
    assert "\t" not in config_yml

    # 2. testdata_for_luogu.zip（根目录完全平铺）
    inter_src = (LUOGU_DIR / "interactor.cpp").read_bytes()
    testdata_zip = out_dir / "testdata_for_luogu.zip"
    with zipfile.ZipFile(testdata_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("interactor.cpp", inter_src)
        zf.writestr("config.yml", config_yml.encode("utf-8"))
        for i, (fam, seed) in enumerate(sched):
            name = f"gr{i + 1:02d}"
            zf.writestr(f"{name}.in", f"{fam} {seed} {ns.max_turns} {ns.max_traps}\n".encode("ascii"))
            zf.writestr(f"{name}.out", b"0\n")

    zip_bytes = testdata_zip.stat().st_size
    zip_sha = hashlib.sha256(testdata_zip.read_bytes()).hexdigest()

    # 3. samples_attachment.zip（零剧透公开附件）
    samples_zip = out_dir / "samples_attachment.zip"
    pub_files = [
        (PROBLEM_ROOT / "agents" / "starter.cpp", "starter.cpp"),
        (PROBLEM_ROOT / "agents" / "baselines" / "blitz_150.cpp", "blitz_150.cpp"),
        (PROBLEM_ROOT / "agents" / "baselines" / "staggered_125.cpp", "staggered_125.cpp"),
        (PROBLEM_ROOT / "engine" / "judge_core.hpp", "judge_core.hpp"),
        (PROBLEM_ROOT / "engine" / "practice_interactor.cpp", "practice_interactor.cpp"),
    ]
    with zipfile.ZipFile(samples_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src, arcname in pub_files:
            raw = src.read_text(encoding="utf-8")
            _check_zero_spoilers(raw, arcname)
            zf.writestr(arcname, raw.encode("utf-8"))

    # 4. luogu_statement.md
    stmt_text = (LUOGU_DIR / "statement.md").read_text(encoding="utf-8")
    _check_zero_spoilers(stmt_text, "luogu_statement.md")
    (out_dir / "luogu_statement.md").write_text(stmt_text, encoding="utf-8", newline="\n")

    # 5. UPLOAD_README.md
    _write_upload_readme(
        out_dir / "UPLOAD_README.md",
        n_cases=len(sched),
        time_limit_ms=ns.time_limit_ms,
        memory_limit_kb=ns.memory_limit_kb,
        score_per_case=ns.score_per_case,
        max_turns=ns.max_turns,
        max_traps=ns.max_traps,
        zip_bytes=zip_bytes,
        zip_sha256=zip_sha,
    )

    print(f"Exported Luogu interactive package to: {out_dir}")
    print(f"  testdata_for_luogu.zip : {zip_bytes / 1024:.1f} KiB ({len(sched)} cases, flat root, SHA256={zip_sha[:16]})")
    print(f"  samples_attachment.zip : {samples_zip.stat().st_size / 1024:.1f} KiB ({len(pub_files)} public files, 0 spoilers)")
    print("  luogu_statement.md     : ready to paste (0 spoilers)")
    print("  UPLOAD_README.md       : upload instructions")


if __name__ == "__main__":
    main()
