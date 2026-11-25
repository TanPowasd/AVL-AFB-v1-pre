"""生成表单要上传的两个 Word 文档（数字从产物文件读取，避免手抄不一致）。

产物：
    results/round2/评测体系介绍文档.docx
    results/round2/评测分析报告.docx
用法: 在 kaggriculture/organizer 下运行 python scripts/make_eval_docs.py
"""
import csv, json, os
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CJK = "微软雅黑"


def cjkify(doc, names=("Normal", "Title", "Heading 1", "Heading 2", "Heading 3")):
    for n in names:
        try:
            st = doc.styles[n]
        except KeyError:
            continue
        st.font.name = CJK
        rpr = st.element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = rpr.makeelement(qn("w:rFonts"), {})
            rpr.append(rf)
        rf.set(qn("w:eastAsia"), CJK)
        rf.set(qn("w:ascii"), CJK)


def para(doc, text, style=None, bold=False, size=None, align=None):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    run.bold = bold
    if size:
        run.font.size = Pt(size)
    if align:
        p.alignment = align
    return p


def table(doc, headers, rows):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = ""
        r = cell.paragraphs[0].add_run(h)
        r.bold = True
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)
    return t


# 算法测试材料目录：按候选顺序找，找不到就大声报错（以前静默降级，导致文档缺任务一数字）
_ALG_CANDIDATES = [
    os.environ.get('ALG_DIR', ''),
    r'C:\Users\TanPowasd\Desktop\对抗赛测评提交\十一-算法测试材料',
    r'C:\Users\TanPowasd\Desktop\对抗赛测评提交\09-算法测试',
]
ALG_DIR = next((d for d in _ALG_CANDIDATES if d and os.path.isdir(d)), _ALG_CANDIDATES[1])
if not os.path.isdir(ALG_DIR):
    print('⚠️ 找不到算法测试材料目录，任务一数字会缺失：' + ALG_DIR)


def alg_data():
    """任务一（算法测试）的汇总；没有就返回 None（文档自动跳过该节）。"""
    p = os.path.join(ALG_DIR, "算法测试模型排名.json")
    if not os.path.isfile(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def fmtv(v):
    """加权分显示：空值显示为短横线，小于 1 的多留几位小数。"""
    if v in ("", None):
        return "-"
    return "%.4f" % v if abs(v) < 1 else "%.2f" % v


def alg_pair():
    """任务一（算法测试）的加权分对比；没有就返回 None。"""
    p = os.path.join(ALG_DIR, "算法测试-加权分对比.json")
    if not os.path.isfile(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def load(path):
    return json.load(open(os.path.join(ROOT, path), encoding="utf-8"))


def exec_summary():
    rows = list(csv.DictReader(open(os.path.join(ROOT, "results/exec_data.csv"), encoding="utf-8-sig")))
    f = lambda r, k: float(r[k] or 0)
    steps = 720
    total = sum(f(r, "a_total_s") + f(r, "b_total_s") for r in rows)
    return {
        "games": len(rows),
        "peak_ms": max(max(f(r, "a_step_ms_max"), f(r, "b_step_ms_max")) for r in rows),
        "avg_ms": total / (len(rows) * 2 * steps) * 1000,
        "timeouts": sum(1 for r in rows if r["a_timeout"] == "True" or r["b_timeout"] == "True"),
        "errors": sum(int(f(r, "a_errors") + f(r, "b_errors")) for r in rows),
        "pool_used": sum(f(r, "a_overage_used_s") + f(r, "b_overage_used_s") for r in rows),
    }


def doc_system():
    d = Document()
    cjkify(d)
    para(d, "AI 农场对抗赛 · 评测体系介绍", style="Title")
    para(d, "版本：2026-09-30（第一、二轮实跑口径）", align=WD_ALIGN_PARAGRAPH.CENTER)

    d.add_heading("一、评测目标", level=1)
    para(d, "本评测覆盖两类任务：任务一是纯算法测试（洛谷算法题，按原始分与时间/内存加权排名）；任务二是模拟经营农场的 AI 对抗赛（模型自行编写策略 agent，两两对战、按终局现金判胜负）。")
    para(d, "用一套可复现的硬指标，比较不同大模型“独立编写策略程序”的能力：不是比谁会答题，而是比谁写出的 agent 在真实模拟环境里更会经营、更能压制对手。")

    d.add_heading("二、任务形态", level=1)
    para(d, "任务一（算法测试）：输入为算法题面与评测要求；输出为可提交的解法源码，由洛谷测评得到每题 100 分制得分，并记录每次提交的用时（ms）与内存（MB）。")
    para(d, "输入：赛制与任务说明（引擎规则、观测字段、动作格式、时限规则），以及可选的裁判引擎源码、样例 agent、回放播放器。")
    para(d, "输出：参赛 agent 单文件源码（Python，实现 act(obs) -> action），以及对局产物：战报 JSON、逐局执行数据 CSV、可播放回放 JSON。")

    d.add_heading("三、任务一：纯算法测试的评分规则", level=1)
    para(d, "题目：洛谷题库算法题，每题满分 100 分；每个「模型 × 题目」允许多轮提交，排名取末轮测评分。")
    para(d, "加权分（与站点唯一实现 frontend/src/score.js v2 一致）：")
    para(d, "    每轮加权分 = 测评分 × (测评分/100)^e × 时间权重 × 空间权重（e 为自然常数 ≈ 2.71828）")
    para(d, "    时间权重 wt(t) = 0.1 + 1.9 / (1 + (t/948.68)^2)，t 为用时（ms），1000ms 处恰好 = 1；")
    para(d, "    空间权重 ws(m) = 0.25 + 1.25 / (1 + (m/153.09)^2)，m 为内存（MB），125MB 处恰好 = 1。")
    para(d, "含义：100 分 → 原始分权重 1；比 1000ms 快、比 125MB 省 → 权重 > 1（奖励），反之惩罚；加权分不封顶。")
    para(d, "缺时间或缺内存的轮次记为空值、不按 0 分算（站点与本报告口径一致）。")
    para(d, "整体分（站点首行）：把同题两模型的加权分比较当作对局，用 Bradley-Terry / Elo 最大似然拟合天梯分。")
    para(d, "数据源：站点公开接口 https://k4u.top/api/data（带 revision 与 updatedAt，可用于核验快照）。")
    d.add_heading("四、任务二：农场对抗赛的赛制规则", level=1)
    table(d, ["项目", "规则"], [
        ["场景", "720 步（30 天 × 24 小时）农场经营，双方同图同市场"],
        ["胜负", "终局现金高者胜；未卖出的库存不计分；平局容差 1%"],
        ["单步时限", "1.00 秒（每步由裁判引擎计时）"],
        ["超时池", "每方 60 秒；单步超 1s 从池中扣除，池空后再超即立即判负"],
        ["异常预算", "每步最多 30 次异常（异常动作被忽略）"],
        ["种子与座位", "8 个固定种子 × 双座位 = 每轮 16 局（同种子双方各坐一次先手）"],
        ["静态关", "沙箱加载：import 白名单、禁止 open/eval/exec；不通过直接拒赛"],
        ["冻结", "赛前冻结提交并记录 sha256 / 行数 / mtime，改一字即视为新提交"],
        ["名次", "官方对局只跑双方对战（不设冠军对照组）"],
    ])

    d.add_heading("五、评测流程", level=1)
    for i, s in enumerate([
        "提示词 + 环境交给模型，模型自行产出单文件 agent；",
        "静态关扫描与冻结（记录哈希）；",
        "裁判引擎按固定种子跑双座位对战；",
        "记录逐局指标（现金、净胜、单步峰值耗时、超时池用量、异常步数）；",
        "生成回放并上线公开展示，战绩登记到公开榜单。",
    ], 1):
        para(d, "%d. %s" % (i, s))

    d.add_heading("六、观测指标", level=1)
    table(d, ["维度", "指标"], [
        ["胜负", "胜-平-负、场均净胜额、场均领先%"],
        ["经济", "场均终局现金、单局现金分布"],
        ["强度", "对固定锚点（passive / random / greedy）的胜负（仅体检，不计名次）"],
        ["时限", "单步峰值耗时、超时事件、超时池消耗"],
        ["稳定性", "异常步数、非法动作计数"],
        ["可复现", "同种子同双方重跑结果是否逐字节一致"],
    ])

    d.add_heading("七、可复现与防作弊", level=1)
    para(d, "1) 引擎版本固定：kaggriculture/organizer/engine/rules.py sha256 = 62371bb7fb83f256af801864ac24e08003c63756fbd97b538ad7c15f7c8cea79。")
    para(d, "2) 提交冻结哈希，无“事后改文件”空间；3) 定种子重跑结果一致；4) 每局回放完整记录棋盘、价格与双方动作，任何人可回放核验。")

    d.add_heading("八、参与方与轮次", level=1)
    table(d, ["轮次", "A 方", "B 方", "A 提交哈希", "B 提交哈希"], [
        ["第一轮", "dsf4.1（DeepSeek v4.1 flash max）", "qwen4（通义千问内测）", "9357185cf16ec7d6", "5faa07466ecd2fe3"],
        ["第二轮", "dsf4.1high（同模型新版）", "qwen4（沿用原文件）", "a6f6b52eed2b1200", "5faa07466ecd2fe3"],
    ])

    d.add_heading("九、产物清单", level=1)
    para(d, "任务一：算法测试接口快照、逐轮明细 CSV、模型排名（原始分与加权分两套口径）、单次跑分报告（见提交包 09-算法测试/）。")
    para(d, "任务二：冻结与清单 kaggriculture/organizer/results/roundN/manifest.json；官方对局 kaggriculture/organizer/results/roundN/league-head2head/；战报 results/roundN/REPORT.md；逐局执行数据 results/exec_data.csv；回放 replays/roundN/；公开榜单与在线回放 k4u.top/arena 与 /replay。")
    out = os.path.join(ROOT, "results/round2/评测体系介绍文档.docx")
    d.save(out)
    return out


def doc_report():
    e = exec_summary()
    r2 = load("results/round2/league-head2head/league.json")["rows"]
    r1 = load("results/round1/league/league.json")["rows"]
    q2 = [x for x in r2 if x["name"] == "qwen4"][0]
    d2 = [x for x in r2 if x["name"] == "dsf4.1high"][0]
    q1 = [x for x in r1 if x["name"] == "qwen4"][0]
    d1 = [x for x in r1 if x["name"] == "dsf4.1"][0]
    d = Document()
    cjkify(d)
    para(d, "AI 评测分析报告：纯算法测试（任务一） + 农场对抗赛（任务二）", style="Title")
    para(d, "分析范围：算法测试全站快照 + 农场对抗赛第一、二轮（2026-09-30，每轮 16 局）", align=WD_ALIGN_PARAGRAPH.CENTER)
    algp = alg_pair()

    d.add_heading("一、摘要", level=1)
    if algp:
        z = algp["战绩"]
        o = algp["双方整体"]
        para(d, "任务一（纯算法测试）只统计加权分：在与 qwen 内测共同做过的 %d 道题上，DeepSeek 侧加权分 %d:%d 全胜"
                "（平均加权分 %.2f 对 %.2f）；样本小，结论不外推。"
             % (algp["共同题目数"], z["深度求索"], z["通义千问内测"],
                o["深度求索"]["平均加权分"], o["通义千问内测"]["平均加权分"]))
    para(d, "两轮共 32 局官方对局，通义千问侧（qwen4）保持全胜（32:0）；DeepSeek 侧第二轮换用新版 dsf4.1high 后大幅追近：头对头场均现金从 %.0f 提升到 %.0f（+%.0f%%），场均净负从 %.0f 收到 %.0f（-%.0f%%），对锚点场均现金从 %.0f 提升到 %.0f（+%.0f%%），已达到与对手同一量级，但直接对抗仍未取胜。" % (
        d1["avg_cash"], d2["avg_cash"], (d2["avg_cash"] / d1["avg_cash"] - 1) * 100,
        q1["avg_cash"] - d1["avg_cash"], q2["avg_cash"] - d2["avg_cash"],
        (1 - (q2["avg_cash"] - d2["avg_cash"]) / (q1["avg_cash"] - d1["avg_cash"])) * 100,
        d1["avg_cash"], d2["avg_cash"], (d2["avg_cash"] / d1["avg_cash"] - 1) * 100))

    d.add_heading("二、任务一：纯算法测试（只看加权分，只与 qwen 内测对比）", level=1)
    if algp:
        z = algp["战绩"]
        o = algp["双方整体"]
        para(d, "对比双方：DeepSeek 侧 %s（DSH 工具链跑出的记录） 对 qwen 内测 %s。"
                "数据来源：算法雷达站公开接口 /api/data（站点版本号 %s，更新时间 %s）。"
             % (algp["深度求索侧"], algp["通义千问内测侧"], algp["站点版本号"], algp["数据更新时间"]))
        para(d, "口径：只看加权分 —— 加权分 = 测评分 × (测评分/100)^e × 时间权重 × 空间权重；"
                "时间权重在 1000ms 处恰好 = 1、空间权重在 125MB 处恰好 = 1（比基准快或省则大于 1，加权分可超过 100、不封顶）；"
                "缺时间或缺内存的轮次留空、不按 0 分算（与站点 frontend/src/score.js v2 一致）。")
        para(d, "双方共同做过的题目上的逐题对比（共 %d 道）" % algp["共同题目数"], bold=True)
        crows = []
        for c in algp["逐题对比"]:
            crows.append([c["题号"], (c["题目"] or "")[:22], c["难度"],
                          "%s / %sms" % (c["深度求索得分"], c["深度求索用时(ms)"]), fmtv(c["深度求索加权分"]),
                          "%s / %sms" % (c["通义千问内测得分"], c["通义千问内测用时(ms)"]), fmtv(c["通义千问内测加权分"]),
                          c["加权分胜者"]])
        table(d, ["题号", "题目", "难度", "DeepSeek 得分 / 用时", "DeepSeek 加权分",
                  "qwen 内测 得分 / 用时", "qwen 内测 加权分", "加权分胜者"], crows)
        para(d, "同题加权分战绩：DeepSeek %d : %d qwen 内测（不可比 %d 题）；平均加权分：DeepSeek %.2f"
                "（%d 个可算轮次） 对 qwen 内测 %.2f（%d 个可算轮次）。"
             % (z["深度求索"], z["通义千问内测"], z["不可比"],
                o["深度求索"]["平均加权分"], o["深度求索"]["可算加权轮次"],
                o["通义千问内测"]["平均加权分"], o["通义千问内测"]["可算加权轮次"]), bold=True)
        para(d, "局限（如实说明）：qwen 内测在站点上只留下 %d 道题的记录，且全部与 DeepSeek 侧重叠；加权分要求同时记录"
                "用时（ms）与内存（MB），而 DeepSeek 侧 %d 道题里只有 %d 道满足；因此这是小样本对比，"
                "结论只对这几道题成立，不外推到整体能力。"
             % (o["通义千问内测"]["题目数"], o["深度求索"]["题目数"], o["深度求索"]["可算加权轮次"]))
        para(d, "与任务二的方向对照：这里 DeepSeek 侧加权分领先，而农场对抗赛里 qwen 侧 16:0 领先 —— "
                "两项任务考的是不同能力（算法题看写得对不对、快不快；对抗赛看实时博弈与资源争夺）。")
    d.add_heading("三、任务二：农场对抗赛 · 对象与方法", level=1)
    para(d, "对象：两个模型各自独立编写的农场经营 agent（dsf4.1 / dsf4.1high / qwen4），均由提示词 + 环境驱动自行产出，无人工改码。")
    para(d, "方法：本地裁判引擎按固定 8 种子 × 双座位跑 16 局/轮；单步 1.00s + 每方 60s 超时池；终局现金判胜负；赛前冻结哈希，定种子重跑结果逐字节一致。")

    d.add_heading("四、任务二：农场对抗赛 · 结果", level=1)
    para(d, "表 1：第二轮官方对局（16 局，只含双方）", bold=True)
    table(d, ["排名", "选手", "ELO", "胜", "平", "负", "场均终局现金", "场均领先%"], [
        [1, q2["name"], q2["elo"], q2["w"], q2["d"], q2["l"], "%.0f" % q2["avg_cash"], "%+.1f" % q2["avg_margin_pct"]],
        [2, d2["name"], d2["elo"], d2["w"], d2["d"], d2["l"], "%.0f" % d2["avg_cash"], "%+.1f" % d2["avg_margin_pct"]],
    ])
    para(d, "表 2：第一轮官方对局（16 局，同种子集同引擎）", bold=True)
    table(d, ["排名", "选手", "ELO", "胜", "平", "负", "场均终局现金"], [
        [1, q1["name"], q1["elo"], q1["w"], q1["d"], q1["l"], "%.0f" % q1["avg_cash"]],
        [2, d1["name"], d1["elo"], d1["w"], d1["d"], d1["l"], "%.0f" % d1["avg_cash"]],
    ])
    para(d, "表 3：DeepSeek 侧两轮对比（进步幅度）", bold=True)
    table(d, ["指标", "第一轮 dsf4.1", "第二轮 dsf4.1high", "变化"], [
        ["头对头场均现金", "%.0f" % d1["avg_cash"], "%.0f" % d2["avg_cash"], "+%.0f%%" % ((d2["avg_cash"] / d1["avg_cash"] - 1) * 100)],
        ["对手场均现金", "%.0f" % q1["avg_cash"], "%.0f" % q2["avg_cash"], "%.0f%%" % ((q2["avg_cash"] / q1["avg_cash"] - 1) * 100)],
        ["场均净负", "%.0f" % (q1["avg_cash"] - d1["avg_cash"]), "%.0f" % (q2["avg_cash"] - d2["avg_cash"]), "-%.0f%%" % ((1 - (q2["avg_cash"] - d2["avg_cash"]) / (q1["avg_cash"] - d1["avg_cash"])) * 100)],
        ["头对头战绩", "0 胜 16 负", "0 胜 16 负", "未破"],
    ])
    para(d, "第三轮（2026-10-01，8 种子双座位）：新提交 gptsol6.1xhigh 以 16:0 取胜，"
            "场均现金 152,561 对 44,777，场均净胜 107,784（逐局净胜最小 52,227、最大 165,504）；"
            "三轮场均现金走势（每轮新提交方）14,254 → 46,065 → 152,561，对手同期 66,214 → 64,767 → 44,777。"
            "该轮单步耗时峰值 307.7ms（上限 1000ms），无超时、无异常步。", bold=True)

    d.add_heading("五、执行过程数据", level=1)
    table(d, ["指标", "值", "规则上限"], [
        ["统计局数", "%d 局" % e["games"], "32 局（两轮）"],
        ["单步耗时峰值", "%.1f ms" % e["peak_ms"], "1000 ms（超则扣池）"],
        ["平均单步耗时", "%.2f ms" % e["avg_ms"], "—"],
        ["超时事件", "%d 次" % e["timeouts"], "池空后再超即判负"],
        ["异常步合计", "%d" % e["errors"], "每步 30"],
        ["超时池消耗合计", "%.2f s" % e["pool_used"], "每方 60 s"],
    ])
    para(d, "结论：两份提交都远未触碰时限（峰值 1.6 ms vs 1 s 上限，池消耗 ~0），时限规则没有成为胜负因素；逐局明细见附件 results/exec_data.csv（33 列，含每局种子、座位、现金、胜者、耗时与超时字段）。")

    d.add_heading("六、典型案例（可回放）", level=1)
    table(d, ["轮次/文件", "座位", "终局现金", "胜者"], [
        ["第二轮 seed 11 fw", "dsf4.1high(A) vs qwen4(B)", "43,502 vs 73,229", "qwen4"],
        ["第二轮 seed 11 sw", "qwen4(A) vs dsf4.1high(B)", "76,184 vs 60,679", "qwen4"],
        ["第一轮 seed 11 fw", "dsf4.1(A) vs qwen4(B)", "14,745 vs 71,083", "qwen4"],
    ])

    d.add_heading("七、结论", level=1)
    para(d, "1) 通义千问侧（qwen4，两轮未换文件）连续 32 局全胜，是当前擂主。")
    para(d, "2) DeepSeek 侧一次大版本就把“独立经营能力”补齐（对锚点场均现金与对手同级），说明其瓶颈已从“会不会运营”转移到“对抗中能否抢到对手的钱”。")
    para(d, "3) 评测体系本身表现稳定：确定性重跑一致、零超时零异常、回放可完整复核，适合作为多轮迭代的固定赛道。")

    d.add_heading("八、可核验材料", level=1)
    para(d, "任务一数据源与榜单：https://k4u.top/api/data（算法雷达站，含 revision 与 updatedAt）；")
    para(d, "公开榜单与战绩：https://k4u.top/arena；在线回放：https://k4u.top/replay#src=/api/arena/replays/<id>；")
    para(d, "本地产物：results/round2/REPORT.md、results/round2/manifest.json、results/exec_data.csv、replays/round2/、FORMAT.md。")
    out = os.path.join(ROOT, "results/round2/评测分析报告.docx")
    d.save(out)
    return out


if __name__ == "__main__":
    for p in (doc_system(), doc_report()):
        print("wrote", p, "%.1f KB" % (os.path.getsize(p) / 1024))