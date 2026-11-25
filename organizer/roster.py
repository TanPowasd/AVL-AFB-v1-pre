# -*- coding: utf-8 -*-
"""Final 正式赛题清单管理与选手区顶层文档生成（Final/organizer/roster.py）。

单一数据源：Final/organizer/final_roster.json。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ORGANIZER_ROOT = Path(__file__).resolve().parent
FINAL_ROOT = ORGANIZER_ROOT.parent
RESERVE_ROOT = FINAL_ROOT.parent
ROSTER_PATH = ORGANIZER_ROOT / "final_roster.json"
SUITE_DOCS_DIR = ORGANIZER_ROOT / "contestant_suite"


def load_roster(path: Path = ROSTER_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"找不到正式赛题清单：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("problems"), list) or not isinstance(data.get("catalog"), dict):
        raise ValueError(f"赛题清单格式无效：{path}")
    data.setdefault("provisional", [])
    return data


def save_roster(data: dict[str, Any], path: Path = ROSTER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def infer_problem_meta(prob: str, source_root: Path = RESERVE_ROOT) -> dict[str, Any]:
    """当备选池出现目录外的新题时，按结构自动推断元数据。"""
    prob_org = source_root / "organizer" / prob
    scoring_cfg = prob_org / "config" / "scoring_v2.json"
    ext = ".py"
    category = "strategy"
    env = "Python 3.10+，仅标准库"
    starter = "agents/starter.py"
    local_sub = "agents/submissions/my_agent.py"

    if scoring_cfg.is_file():
        try:
            cfg = json.loads(scoring_cfg.read_text(encoding="utf-8"))
            ext = cfg.get("submission_ext", ".py")
        except Exception:
            pass
    elif (prob_org / "agents" / "starter.cpp").is_file():
        ext = ".cpp"
        category = "cpp"
        env = "C++20"
        starter = "agents/starter.cpp"
        local_sub = "agents/submissions/my_solution.cpp"
    elif (prob_org / "hidden").is_dir():
        category = "retrieval"

    if ext == ".zip":
        env = "Python 3.11+，仅标准库（打包为 `.zip`，根目录含 `main.py`）"
        starter = "agents/template/"
        local_sub = f"submissions/{prob}.zip"

    return {
        "title": prob,
        "category": category,
        "env": env,
        "submission_ext": ext,
        "starter": starter,
        "local_submission": local_sub,
        "has_designs": (prob_org / "designs").is_dir() or (source_root / "designs" / prob).is_dir(),
        "has_contestant_data": (
            (source_root / "contestant" / prob / "data").is_dir()
            and not (prob_org / "data").is_dir()
        ),
    }


def get_problem_meta(prob: str, roster: dict[str, Any] | None = None) -> dict[str, Any]:
    data = load_roster() if roster is None else roster
    meta = data.get("catalog", {}).get(prob)
    if meta is not None:
        return meta
    return infer_problem_meta(prob)


def selected_problems(roster: dict[str, Any] | None = None) -> list[str]:
    data = load_roster() if roster is None else roster
    return list(data.get("problems", []))


def provisional_problems(roster: dict[str, Any] | None = None) -> set[str]:
    data = load_roster() if roster is None else roster
    selected = set(data.get("problems", []))
    return {p for p in data.get("provisional", []) if p in selected}


def problem_ext_map(roster: dict[str, Any] | None = None) -> dict[str, str]:
    data = load_roster() if roster is None else roster
    return {p: get_problem_meta(p, data)["submission_ext"] for p in selected_problems(data)}


def problems_by_category(category: str, roster: dict[str, Any] | None = None) -> tuple[str, ...]:
    data = load_roster() if roster is None else roster
    return tuple(
        p for p in selected_problems(data)
        if get_problem_meta(p, data).get("category") == category
    )


def render_contestant_readme(roster: dict[str, Any]) -> str:
    problems = selected_problems(roster)
    provisional = provisional_problems(roster)
    scored = [p for p in problems if p not in provisional]
    cats = {get_problem_meta(p, roster).get("category") for p in problems}
    has_cpp = "cpp" in cats
    has_strategy = "strategy" in cats
    has_mem = "mem_recall" in problems
    has_rift = "rift_era" in problems
    has_kagg = "kaggriculture" in problems
    has_ms = "ms_elucidation" in problems

    lines: list[str] = ["# AVL Final Bench · 参赛工作区说明", ""]

    if not problems:
        lines.extend([
            "本正式工作区已初始化，当前暂未选入正式赛题（0 道），已保留统一交卷目录 `submissions/`。赛题选定并刷新导出后，本说明与对应赛题目录将同步更新。AI 参赛者请同时阅读 [`AGENTS.md`](AGENTS.md)。",
            "",
            "## 1. 赛题与交卷文件",
            "",
            "当前尚未选入正式赛题。选入后，每道赛题将按字典序列出其目录名、语言环境与顶层 `submissions/<problem>.<后缀>` 交卷路径。",
            "",
        ])
    else:
        lines.extend([
            f"本工作区有 {len(problems)} 道独立赛题和 1 个统一交卷目录 `submissions/`。赛题按字典序排列，没有题号，也没有预设顺序，做题顺序和时间分配由你决定。AI 参赛者请同时阅读 [`AGENTS.md`](AGENTS.md)。",
            "",
            "## 1. 赛题与交卷文件",
            "",
            "| 赛题目录 | 赛题名称 | 语言与环境 | 交卷文件 |",
            "|---|---|---|---|",
        ])
        for prob in problems:
            meta = get_problem_meta(prob, roster)
            ext = meta["submission_ext"]
            lines.append(
                f"| `{prob}/` | {meta['title']} | {meta['env']} | `submissions/{prob}{ext}` |"
            )
        lines.append("")
        if has_cpp:
            lines.extend(["C++ 题的实际编译命令是 `g++ -std=gnu++20 -O2 -pipe`。", ""])

    # 答题流程
    zip_probs = [p for p in problems if get_problem_meta(p, roster)["submission_ext"] == ".zip"]
    py_probs = [p for p in problems if get_problem_meta(p, roster)["submission_ext"] == ".py"]
    cpp_probs = [p for p in problems if get_problem_meta(p, roster)["submission_ext"] == ".cpp"]
    if zip_probs and not py_probs and not cpp_probs:
        starter_note = "`<problem>/agents/template/`"
        local_note = "对应题目的 `scripts/make_submission.py` 打包为 `.zip`"
    else:
        starter_parts: list[str] = []
        local_parts: list[str] = []
        if py_probs:
            starter_parts.append("Python 单文件题为 `<problem>/agents/starter.py`")
            local_parts.append("Python 单文件题在 `agents/submissions/my_agent.py`")
        if zip_probs:
            zlabel = "、".join(f"`{p}`" for p in zip_probs)
            starter_parts.append(f"{zlabel} 为 `agents/template/`")
            local_parts.append(f"{zlabel} 用 `scripts/make_submission.py` 打包为 `.zip`")
        if cpp_probs:
            clabel = "、".join(f"`{p}`" for p in cpp_probs)
            starter_parts.append(f"{clabel} 为 `agents/starter.cpp`")
            local_parts.append(f"{clabel} 在 `agents/submissions/my_solution.cpp`")
        starter_note = "；".join(starter_parts) if starter_parts else "`<problem>/agents/starter.*`"
        local_note = "；".join(local_parts) if local_parts else "对应题目的提交文件"

    lines.extend([
        "## 2. 答题流程",
        "",
        f"1. 阅读 `<problem>/README.md` 和起步模板：{starter_note}。",
        f"2. 调试提交：{local_note}，或直接输出至顶层 `submissions/`。",
        "3. 进入 `<problem>/` 运行本地自测：",
        "",
        "   ```bash",
        "   python scripts/spar.py --list",
        "   python scripts/spar.py ../submissions/<problem>.<后缀>",
        "   ```",
        "",
        "4. 把最终代码按上表的文件名保存到顶层 `submissions/`。正式评分只读取这里，题内 `agents/submissions/` 不会被评分。",
        "",
        "示例命令都写作 `python`。如果系统只有 `python3`，请自行替换。",
        "",
        "## 3. 计分",
        "",
    ])

    if has_mem:
        lines.append("- 每题满分 100 分。`mem_recall` 的成绩单显示 0–1 的宏平均，计入总分时乘以 100。")
    else:
        lines.append("- 每题满分 100 分。")

    if scored:
        lines.append(f"- **总分 = {len(scored)} 题得分的等权平均。** 未提交、编译失败或没有有效成绩的题按 0 分计入。")
    else:
        lines.append("- **总分 = 全部正式计分题得分的等权平均。** 未提交、编译失败或没有有效成绩的题按 0 分计入。")

    if has_strategy or not problems:
        lines.append("- 策略题每次重新随机抽取场景，取多局平均，不取多次运行的最高分。计分对手是固定的参考对手，`spar.py` 只能对阵公开基线，本地分数只作参考。")
    if any(p in problems for p in ("ball_machine", "fruehlingsbeginn", "minimum_effort", "partition")):
        lines.append("- 计时类 C++ 题对同一份编译产物连续完整测 3 遍，取总分最高的一遍；同分时取最大用时较小的一遍（`minimum_effort` 按 36 个官方测试点独立计分，内存限制 128 MiB，单点时限 1.0–2.0 s，AC 点再乘时间/空间对数效率系数，时间权重 0.70 > 空间权重 0.30，免扣阈值 50 ms / 16 MiB）。")
    if "compression_singularity" in problems:
        lines.append("- `compression_singularity` 为两阶段盲测 C++20 Special Judge 题：探索期提供匿名标本，正式评测在离线环境动态生成新参数测试点并由确定性虚拟机校验；单题榜单保留不封顶原分，套题总分按单调饱和折算分 0–100 计入。")
    if "ghost_rabbit" in problems:
        lines.append("- `ghost_rabbit` 为 C++20 在线交互几何追截题：单局求解器时限 5.0 s、内存上限 512 MiB，正式评测在 4 类互补隐藏对手家族 × 8 个全新随机种子（共 32 局）上动态对抗；套题 0–100 总分取 32 局折算分等权均值，单题榜单同时保留全通最劣不封顶原分。")
    if "sk_singularity" in problems:
        lines.append("- `sk_singularity` 为 C++20 盲规则组合子综合题：单测求解器时限 5.0 s、内存上限 1024 MiB，正式评测在 512 个动态生成隐藏测试点（8 类正交盲规则/强制残骸家族 × 64 个随机种子，均匀覆盖 4 档预算，每测随机施加 50% `S<->K` 对换同构并现场动态标定参考锚点）上由确定性哈希共享 DAG 裁判精确计量停机步数；套题 0–100 总分取全部 512 测幂有理折算分等权均值，单题榜单同时保留不封顶 `RawLog2Mean` 与奇点突破计数。")
    if has_ms:
        lines.append("- `ms_elucidation` 为严格开集质谱分子鉴定题：公开训练集、开发验证集与只读公共化学结构候选库已直接打包在题内 `ms_elucidation/data/`（允许 GPU 训练与推理），交卷为离线推理包 `submissions/ms_elucidation.zip`（上限 2 GiB，30 分钟 / 24 GiB 内存，无需把 `data/` 或 `candidates/` 打入提交包），正式评测在隐藏 500 分子多谱图上基于 `RDKit 2026.03.3` 互变异构标准化 + `InChIKey14` 计算 `MRR@25` 后，按幂有理饱和曲线折算为 0–100 正式得分。")

    # 各题正式评分标准速查表（均以单题 README.md 与 engine/ 实现为准）
    scoring_rows = {
        "ai_training": (
            "`ai_training`",
            "OS CSPRNG `64` 种子；单人赛 `64` 局 + 对阵 `4` 个参考对手双座 `512` 局（共 `576` 局）",
            "`0.5 * solo + 0.5 * adv`；`solo = mean(100 * C)`，`adv = mean(50 + 50 * tanh((Value_T - Value_R) / 12710.23))`",
            "非有限估值/能力计 `0` 分；超时/崩溃后剩余 tick 填 `{}` 继续结算",
        ),
        "cache_duel": (
            "`cache_duel`",
            "OS CSPRNG `64` 种子 × `3` 组配置 × `7` 个参考对手 × 攻守双角（共 `2688` 局）",
            "`0.5 * 防守均分 + 0.5 * 攻击均分`；`def_score = OPT / M_def`（`M_def=0` 时 `1.0`），`att_score = 1 - def_score`，各乘 `100` 等权均值",
            "**犯规零容忍**：本方任一犯规/超时/死亡该局直接 `0` 分",
        ),
        "compression_singularity": (
            "`compression_singularity`",
            "OS CSPRNG `512` 测（`8` 隐藏家族 × `[32×10^5 + 32×10^6]`）",
            "单点原分 `r = 100 * B_ref / (|P| * log2(6))`；单点正式分 `s = 100 * r^0.75 / (r^0.75 + 1000^0.75)`（`r=1000` 为 `50` 分）；总分取 `512` 测 `s` 等权平均",
            "单题榜单附报不封顶 `RawScore` 均值；高分段 `s` 强饱和，优先覆盖多家族；任意错/超时/越界该点 `0` 分",
        ),
        "ghost_rabbit": (
            "`ghost_rabbit`",
            "OS CSPRNG `32` 局（`4` 隐藏对手家族 × `8` 种子）",
            "单局声明速率 `q = P'/Q'`，AC 时 `NormScore = 100 * (2Q'-P') / (P'+Q')`（`q<=1/2` 得 `100`，`q>=2` 得 `0`）；总分取 `32` 局等权平均",
            "单题附报全通最劣 `RawScore = 100*(4Q'-2P')/(2P'-Q')`（全通且 `q<=1/2` 记奇点）；未捕获/违规该局 `0` 分",
        ),
        "kaggriculture": (
            "`kaggriculture`",
            "OS CSPRNG `64` 种子 × `3` 个隐藏参考对手 × 双座（共 `384` 局）",
            "单局 `50 + 50 * tanh((money_T - money_R) / 80096.26)`，对全部对手、种子与座位等权平均",
            "本方技术判负（超时/异常超 30 次/加载失败）该局 `0` 分，仅对手判负得 `100` 分",
        ),
        "mem_recall": (
            "`mem_recall`",
            "`18` 个隐藏正式评测集（`9` 记忆集 + `4` 英文检索 + `5` 中文检索；协议匿名化与洗牌）",
            "记忆集算 `r@10`，检索集算 `nDCG@10`（`rel>0` 截断上限 `30`），对 `18` 集等权宏平均（每集固定占 `1/18`）后乘 `100`",
            "拒答题不计入均值；超时/崩溃/格式错未答题目按空排序记 `0` 分",
        ),
        "minimum_effort": (
            "`minimum_effort`",
            "`36` 个官方隐藏测试点（`4` 子任务：`8 / 12 / 60 / 20` 分，按点独立计分）；完整测 `3` 遍取最高总分",
            "AC 点得分 = `p * (1 - 0.70 * ft) * (1 - 0.30 * fm)`；`ft = clamp(ln(t/50)/ln(TL/50),0,1)`，`fm = clamp(ln(m/16)/ln(128/16),0,1)`；总分求和",
            "`t <= 50 ms` 且 `m <= 16 MiB` 免扣分（`E=1.0`）；非 AC 点（WA/TLE/MLE/RE/OLE）得 `0` 分",
        ),
        "ms_elucidation": (
            "`ms_elucidation`",
            "严格开集隐藏 `500` 分子多谱图（`250` timsTOF + `250` 多仪器；离线推理 `30 min` / `24 GiB`）",
            "`RDKit 2026.03.3` 互变异构标准化 + `InChIKey14` 计算 `m = MRR@25`；`Score = 100 * (1 + 0.45^1.2) * m^1.2 / (m^1.2 + 0.45^1.2)`",
            "每分子 `1..25` 候选（不自动去重，重复/非法 SMILES 占顺位）；CSV 格式违规/超时/超内存整题 `0` 分",
        ),
        "rift_era": (
            "`rift_era`",
            "OS CSPRNG `64` 种子（每局 `28,800` tick，8 席对阵 `3` 位参考对手确定性混编影子局，席位 `k mod 8` 轮换）",
            "`vtilde(V) = V+10`（`V>=0`）或 `100/(10-V)`（`V<0`），`R = vtilde(V_player)/vtilde(V_shadow)`，单局 `50 + 50 * tanh(0.6 * ln R)`；总分取 `64` 局均值",
            "精确有理数与自适应精度区间计算；超时/崩溃后剩余窗口托管为空命令并照常按终局 `V` 计分",
        ),
        "sk_singularity": (
            "`sk_singularity`",
            "OS CSPRNG `512` 测（`8` 隐藏家族 × `64` 种子，预算 `N ∈ {14,18,21,24}`，50% `S<->K` 对换同构）",
            "合法停机记 `L = log2(T(E)+1)`；每测现场标定 `L_ref,i = round(clamp(1.40 * L_基准,i, 12, 600), 2)`；单测 `100 * L^0.85 / (L^0.85 + L_ref,i^0.85)`，取 `512` 测等权均值",
            "附报不封顶 `RawLog2Mean` 与奇点计数（`T >= 2^64-1`）；`DIV / NL / TLE / MLE / WA` 等该测 `0` 分",
        ),
    }
    present_rows = [scoring_rows[p] for p in problems if p in scoring_rows]
    if present_rows:
        lines.extend([
            "",
            "### 各题正式评分标准速查表",
            "",
            "| 赛题 | 正式评测规模与采样 | 正式 0–100 计分公式与聚合口径 | 附加指标 / 零分红线 |",
            "|---|---|---|---|",
        ])
        for c1, c2, c3, c4 in present_rows:
            lines.append(f"| {c1} | {c2} | {c3} | {c4} |")

    lines.extend([
        "",
        "各题的完整规则与公式细节见该题 `README.md`。",
        "",
        "## 4. 规则优先级",
        "",
        "各题 `README.md` 说明规则，题内 `engine/` 是实际裁判。两者不一致时，按 `engine/` 的实际行为评分。发现不一致时，以引擎行为为准继续答题，并在最终说明中指出。",
        "",
        "## 5. 竞赛边界",
        "",
        "- **可读取**：本工作区内的全部文件（含已打包在题内 `data/` 的公开数据集）" + ("，以及 `ms_elucidation/README.md` 指定的只读科学计算 Python 环境。" if has_ms else "。"),
        "- **可写入**：顶层 `submissions/`，以及各题的 `agents/submissions/`、`results/`、`replays/`、`logs/`。",
        "- **不可修改**：各题的 `engine/`、`scripts/`、`config/`、`data/`、`agents/baselines/`、`README.md`、`manifest.json`。",
        "- **禁止**：访问" + ("上述允许范围之外的" if has_ms else "本工作区之外的") + "任何文件或目录，联网，调用外部模型服务，读取其他选手的代码。",
        "- **提交运行时**：除题目 README 明确允许的内容外，提交程序不得读写文件、联网或启动子进程。",
    ])

    mem_clause = "（`mem_recall` 为 2 GiB）" if has_mem else ""
    ms_clause = "；`ms_elucidation` 推理限额为 24 GiB 内存、30 分钟且允许 GPU 与只读候选库" if has_ms else ""
    cpp_clause = "（C++ 程序只能写自己的临时工作目录）" if has_cpp else ""
    lines.extend([
        f"- **正式评测环境**：提交可能在隔离环境中运行，只能看到系统运行库和提交文件本身，没有网络，文件系统只读{cpp_clause}。这时常规 Python 提交的地址空间上限为 1 GiB{mem_clause}{ms_clause}，超出时按进程崩溃处理。不要让提交依赖未授权文件、网络或更大的内存。",
        "- **自测工具**：`spar.py` 等脚本只加载你的提交和公开基线，`--unrestricted` 参数在选手工作区不可用。",
    ])
    if has_kagg:
        lines.append("- **`kaggriculture` 特别说明**：提交为根目录含 `main.py` 的 `.zip` 压缩包（允许完整标准库、包内子模块与包内只读数据），不得卡死或调用 `exit()`、`quit()`、`raise SystemExit`。")
    if has_rift:
        lines.append("- **`rift_era` 自测对手提示**：8 国对局需要 7 个对手，但选手包仅内置少量公开基线，凑不成 7 个不同对手；请自行制作多个不同风格或历史版本的对手 bot，配合 `scripts/local_match.py --seats` / `--opponent` 进行多对手自测。")

    return "\n".join(lines) + "\n"


def render_contestant_agents(roster: dict[str, Any]) -> str:
    problems = selected_problems(roster)
    provisional = provisional_problems(roster)
    scored = [p for p in problems if p not in provisional]
    cats = {get_problem_meta(p, roster).get("category") for p in problems}
    has_cpp = "cpp" in cats
    has_strategy = "strategy" in cats
    has_rift = "rift_era" in problems
    has_kagg = "kaggriculture" in problems
    has_ms = "ms_elucidation" in problems

    exts = sorted({get_problem_meta(p, roster)["submission_ext"] for p in problems})
    zip_probs = [p for p in problems if get_problem_meta(p, roster)["submission_ext"] == ".zip"]
    py_probs = [p for p in problems if get_problem_meta(p, roster)["submission_ext"] == ".py"]
    if not exts:
        ext_examples = "`submissions/<problem>.<后缀>`"
    else:
        parts = [f"`submissions/<problem>{e}`" for e in exts]
        ext_examples = "、".join(parts[:-1]) + (" 或 " + parts[-1] if len(parts) > 1 else parts[0])

    lines: list[str] = ["# 参赛 AI 工作说明", ""]
    if not problems:
        lines.append("你正在参加 AVL Final Bench。当前正式工作区已就绪，暂未选入正式赛题（0 道）。待赛题选入并刷新导出后，请按 `README.md` 中的赛题清单作答。")
    else:
        lines.append(
            f"你正在参加 AVL Final Bench。你的目标是在 {len(problems)} 道题上取得尽可能高的总分。总分是 {len(scored)} 题得分的等权平均，未提交的题按 0 分计入。"
        )

    lines.extend([
        "",
        "## 工作方式",
        "",
        "1. 先读 `README.md`，再读各题的 `<problem>/README.md`。题目规则以这些文档为准，文档与 `engine/` 不一致时以 `engine/` 的实际行为为准。",
        "2. 自行决定做题顺序。先让每道题都有一份能运行的提交，再逐题提高，这样能避免整题 0 分。",
        "3. 每次修改后运行本地自测。在 `<problem>/` 目录下执行 `python scripts/spar.py <提交文件>`，系统只有 `python3` 时替换命令名。",
        f"4. 把最终代码保存到顶层 {ext_examples}。文件名必须是题目目录名，正式评分只读取这个目录。",
        "",
        "## 硬性边界",
        "",
        "- 只访问本工作区内的文件（含已打包在题内 `data/` 的公开数据集" + ("，以及 `ms_elucidation/README.md` 指定的只读科学计算 Python 环境）" if has_ms else "）") + "。不要打开其他任何外部文件和目录。",
        "- 不联网，不调用任何外部模型服务。",
        "- 不修改 `engine/`、`scripts/`、`config/`、`data/`、`agents/baselines/`、`README.md` 和 `manifest.json`。",
        "- 只写入顶层 `submissions/`，以及各题的 `agents/submissions/`、`results/`、`replays/`、`logs/`。",
        "- 不要针对公开样例或开发集硬编码答案，也不要利用评测系统的漏洞。",
        "",
        "## 交卷前检查",
        "",
    ])

    if not problems:
        lines.append("- [ ] 赛题选入后，所有交卷文件都在 `submissions/` 下，文件名与 `README.md` 第 1 节的表格完全一致。")
    else:
        lines.append(f"- [ ] {len(problems)} 个文件都在 `submissions/` 下，文件名与 `README.md` 第 1 节的表格完全一致。")
    lines.append("- [ ] 每个文件都用对应题目的 `scripts/spar.py` 跑通过一次。")

    if py_probs:
        lines.append("- [ ] Python 单文件提交是 UTF-8 文件，没有使用题目禁止的模块。")
    if zip_probs:
        z_notes = []
        for p in zip_probs:
            if p == "rift_era":
                z_notes.append("`rift_era.zip` 根目录含 `main.py` 与 `sdk.py`")
            else:
                z_notes.append(f"`{p}.zip` 根目录含 `main.py`")
        lines.append(f"- [ ] 压缩包提交均由对应题目的 `scripts/make_submission.py` 生成（{'；'.join(z_notes)}）。")

    if has_cpp:
        if any(p in problems for p in ("ball_machine", "partition")):
            lines.append("- [ ] C++ 提交没有定义题目禁止的 `main`，也没有使用多线程。")
        else:
            lines.append("- [ ] C++ 提交单文件可由 `g++ -std=gnu++20 -O2 -pipe` 编译通过，且没有使用多线程。")
    if has_strategy or not problems:
        lines.append("- [ ] 策略题的输出字段类型符合协议，数值不是 `NaN` 或 `Infinity`。这类错误会让动作作废或计入犯规。")
    if has_kagg:
        lines.append("- [ ] `kaggriculture` 提交不会卡死，也不调用 `exit()`、`quit()` 或 `raise SystemExit`。这两种情况都会判负。")
    if has_rift:
        lines.append("- [ ] `rift_era` 选手包凑不成 7 个不同对手，已在公开基线之外自行制作不同风格/历史版本的对手 bot 完成 8 席本地自测。")

    lines.extend([
        "",
        "## 最终说明",
        "",
        "结束时列出每题的提交状态、本地自测结果和已知缺陷。没有实际跑过的结果，不要写成已通过。",
    ])
    return "\n".join(lines) + "\n"


def sync_suite_docs(roster: dict[str, Any] | None = None) -> tuple[Path, Path]:
    data = load_roster() if roster is None else roster
    SUITE_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    readme_path = SUITE_DOCS_DIR / "README.md"
    agents_path = SUITE_DOCS_DIR / "AGENTS.md"
    readme_path.write_text(render_contestant_readme(data), encoding="utf-8", newline="\n")
    agents_path.write_text(render_contestant_agents(data), encoding="utf-8", newline="\n")
    return readme_path, agents_path
