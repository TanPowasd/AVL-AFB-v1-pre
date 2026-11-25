"""前沿实验室引擎单元测试：python test_engine.py（或 pytest test_engine.py）。"""
import math
import os
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "score"))
import engine  # noqa: E402
import world as W  # noqa: E402


def _find_bot(stem: str) -> str:
    _here = ROOT
    for _sub in ('agents/baselines', 'agents/reference', 'bots'):
        _p = os.path.join(_here, _sub, stem if stem.endswith('.py') else stem + '.py')
        if os.path.isfile(_p):
            return _p
    raise FileNotFoundError(stem)
E = engine


def game(ticks=2000, seed=3):
    return E.Game(seed, ticks)


def run_ticks(g, n, a0=None, a1=None):
    obs = None
    for i in range(n):
        obs = g.step([a0 if (a0 and i == 0) else {}, a1 if (a1 and i == 0) else {}])
    return obs


# ---------------------------------------------------------------- 世界
def test_world_ranges_and_calibration():
    for s in range(1, 13):
        w = W.generate(s)
        assert 0.28 <= w["alpha"] <= 0.40 and 0.25 <= w["beta"] <= 0.35
        assert all(0.5 <= w["q"][d] <= 1.5 for d in W.REAL)
        assert all(0.0 <= w["c"][d] <= 0.12 for d in W.REAL)
        for task in W.TASKS:
            assert abs(sum(w["M"][task].values()) - 1) < 1e-9
        cs = W.capability(w, W.ref_losses(w, 20.0, 400.0))
        cl = W.capability(w, W.ref_losses(w, 300.0, 6000.0))
        assert 0.14 <= cs <= 0.26 and 0.79 <= cl <= 0.91, (s, cs, cl)
        assert len(w["regions"]) == 5 and w["regions"][0]["cap_mw"] >= 8
        gens = w["npcs"][0]["gens"]
        assert 0.3 <= gens[0]["C"] <= 0.5 and gens[0]["tick"] == 0
        for a, b in zip(gens, gens[1:]):
            assert 6 * W.MONTH <= b["tick"] - a["tick"] <= 9 * W.MONTH
            assert 0.05 - 1e-9 <= b["C"] - a["C"] <= 0.10 + 1e-9 or b["C"] == 0.97
    assert W.generate(5) == W.generate(5)
    k = W.base_key(1, "x")
    assert W.hash_u(k, 3, 4) == W.hash_u(k, 3, 4) and 0 <= W.hash_u(k, 3, 4) < 1


def test_eff_tokens_repetition():
    assert abs(W.eff_tokens(10.0, 10.0, 1000.0) - 1000 * 4 * (1 - math.exp(-0.01 / 4))) < 1e-9
    # 重复 4 轮：有效量 = U·4·(1−e^−1) ≈ 2.53 U，再乘质量
    assert abs(W.eff_tokens(400.0, 400.0 * 1.2, 100.0) - 100 * 4 * (1 - math.exp(-1)) * 1.2) < 1e-9
    assert W.eff_tokens(0, 0, 10) == 0.0


def test_demand_growth():
    w = W.generate(2)
    for t in (0, 5000, 20000):
        q = W.demand_q(w, t)
        m = t / W.MONTH
        g = 1.08 ** min(m, 12) * 1.05 ** max(0, min(m, 24) - 12) * 1.03 ** max(0, m - 24)
        assert abs(q - W.Q0 * g * w["heat"][t // W.WEEK] * w["shock"][t // W.QUARTER]) < 1e-9


# ---------------------------------------------------------------- 训练
def test_run_throughput_and_ckpt_cost():
    g = game()
    w = g.w
    spec = {"name": "r", "params_b": 1.0, "tokens_b": 1000, "gpus": {"G1": 128}, "batch": 64, "ckpt_every": 4,
            "lr": 0.9 * w["eta_c"]}
    g.step([{"run": [spec]}, {}])
    g.step([{}, {}])
    r = g.players[0].runs["r"]
    p = g.players[0]
    # 前 2 tick：每 tick 128 张 G1，util = min(1, 2·64/128) = 1；batch 64 < B_crit → 无浪费
    if r.restart == 0 and not r.ckpts:
        per = 128 / (1.0 * E.KC)
        assert abs(r.D - 2 * per) < 1e-9, (r.D, per)
    for _ in range(2):
        g.step([{}, {}])
    assert r.ckpts and r.ckpts[0]["id"] == 1
    assert p.runs["r"].ncards == 128


def test_lr_penalty_and_loss_structure():
    g = game()
    p = g.players[0]
    for name, lr_mult in (("a", 1.0), ("b", 2.0)):
        g._new_run(p, {"name": name, "params_b": 1.0, "tokens_b": 10, "gpus": {"G1": 1},
                       "lr": lr_mult * g.eta_star(1.0)}, 0)
    ra, rb = p.runs["a"], p.runs["b"]
    for r in (ra, rb):
        r.D = 10.0
        for q, f in r.mix.items():
            r.S[q] = 10.0 * f
            r.Sq[q] = 10.0 * f * p.qavg[q]
        r.pen = 10.0 * 0.08 * math.log(r.lr / r.eta) ** 2
    la = g.loss_of(p, {"D": ra.D, "S": ra.S, "Sq": ra.Sq, "pen": ra.pen, "spike": 1.0, "deff": 1.0}, ra)
    lb = g.loss_of(p, {"D": rb.D, "S": rb.S, "Sq": rb.Sq, "pen": rb.pen, "spike": 1.0, "deff": 1.0}, rb)
    bonus = 1 - 0.02  # cosine 训满
    for x, y in zip(la, lb):
        assert abs((y - x) / bonus - 0.08 * math.log(2) ** 2) < 1e-9
    assert len(la) == 7


def test_failure_rollback_and_restart():
    g = game(ticks=4000)
    p = g.players[0]
    g.step([{"run": [{"name": "r", "params_b": 1.0, "tokens_b": 1e5, "ckpt_every": 3,
                      "lr": 0.9 * g.eta_star(1.0)}]}, {}])
    seen = None
    for _ in range(3000):
        obs = g.step([{}, {}])
        for e in obs[0].get("ev", []):
            if e["type"] == "failure":
                seen = e
        if seen:
            break
    assert seen, "512 张卡 3000 tick 内应当出过故障"
    r = p.runs["r"]
    assert r.restart == E.RESTART and abs(r.D - r.ckpts[-1]["D"]) < 1e-9
    assert sum(p.broken_n.values()) == 1
    d0 = r.D
    g.step([{}, {}])
    g.step([{}, {}])
    assert abs(r.D - d0) < 1e-9                       # 重启 2 tick 不前进


def test_spike_rollback_restores():
    g = game()
    p = g.players[0]
    first = {"run": [{"name": "hot", "params_b": 1.0, "tokens_b": 1e5, "ckpt_every": 1, "gpus": {"G1": 8},
                      "lr": 3.0 * g.eta_star(1.0)}]}
    hit = None
    for i in range(500):
        obs = g.step([first if i == 0 else {}, {}])
        for e in obs[0].get("ev", []):
            if e["type"] in ("spike", "diverged"):
                hit = e
        if hit:
            break
    assert hit
    r = p.runs["hot"]
    before = r.spike
    assert before > 1.0
    g.step([{"run_ctl": [{"name": "hot", "op": "rollback"}]}, {}])
    assert abs(r.spike - before / 1.02) < 1e-9 and r.status == "running"   # 存档在尖峰判定之前，回滚即撤销


def test_review_triggers_and_audit_request():
    g = game()
    p = g.players[0]
    g.step([{"run": [{"name": "big", "params_b": 1.0, "tokens_b": 1e6, "paused": True}]}, {}])
    g.step([{"audit_request": [{"run": "big"}]}, {}])
    r = p.runs["big"]
    L = g._review_len(p)
    assert r.review == L - 1 or r.review == L
    for _ in range(L + 2):
        g.step([{}, {}])
    assert r.approved
    # 未审批的任务累计算力越过阈值 → 自动停下审核
    g.step([{"run": [{"name": "x", "params_b": 1.0, "tokens_b": 1e6}]}, {}])
    rx = p.runs["x"]
    rx.compute = E.REG_COMPUTE - 1
    while rx.review == 0:
        g.step([{}, {}])
    assert rx.review > 0 and not rx.approved


# ---------------------------------------------------------------- 市场
def test_auction_uniform_price_and_delivery():
    g = game()
    run_ticks(g, 1)                       # t=0：周结算，放出第 0 周的供给
    o = g.offer["G1"]
    lots = o["lots"]
    hi = 3 * o["reserve"]                 # 在 200M 现金内足额覆盖 2 批报价
    g.offer["G1"] = {**o, "lots": 2, "npc": [[1, o["reserve"] * 1.3]]}
    a = g.players[0]
    cash0 = a.cash
    g.step([{"bid_gpu": [{"model": "G1", "lots": 2, "price": hi}]}, {}])
    for _ in range(E.WEEK - 1):
        g.step([{}, {}])                   # 到 t=168：结算
    assert a.deliv and a.deliv[0][2] == 2 * E.LOT
    clear = a.deliv[0][3]
    assert abs(clear - o["reserve"] * 1.3) < 1e-9          # 统一价 = 最高落选报价
    assert abs((cash0 - a.cash) - 2 * E.LOT * clear) < 1.0  # 还扣了几天工资电费
    assert lots >= 0


def test_auction_tie_split_symmetric():
    g = game()
    run_ticks(g, 1)
    o = g.offer["G1"]
    g.offer["G1"] = {**o, "lots": 3, "npc": []}
    bid = {"bid_gpu": [{"model": "G1", "lots": 3, "price": o["reserve"] * 2}]}
    g.step([bid, bid])
    for _ in range(E.WEEK - 1):
        g.step([{}, {}])
    na = sum(d[2] for d in g.players[0].deliv)
    nb = sum(d[2] for d in g.players[1].deliv)
    assert na == nb == E.LOT                 # 3 批同价平分：各 1 批，余 1 批不分（保持对称）


def test_rent_price_spot_and_sell():
    g = game()
    run_ticks(g, 1)
    u, bg, pool = g.rent_u("G1")
    assert abs(g.rent_price("G1") - E.GPU["G1"]["price"] / E.RENT_HOURS * (1 + 2 * u)) < 1e-15
    g.step([{"rent": [{"model": "G1", "n": 100, "kind": "spot", "ticks": 50}]}, {}])
    a = g.players[0]
    assert a.rentals and a.rentals[0]["n"] == 100
    u2 = g.rent_u("G1")[0]
    assert abs(u2 - (u + 100 / pool)) < 1e-9
    cash = a.cash
    g.step([{"sell_gpu": [{"model": "G1", "n": 100}]}, {}])
    expected_unit = min(0.05 * 0.65 * min(1.0, g.heat()), 0.05) * max(0.0, 1.0 - g.t / E.HW_LIFE)
    assert a.recv and abs(a.recv[0][1] - 100 * expected_unit) < 1e-9 and a.recv[0][0] == g.t + E.SELL_DELAY
    assert sum(s["cards"]["G1"] for s in a.sites) == 412
    assert cash > a.cash - 1


def test_release_live_audit_and_shares():
    g = game(ticks=4000)
    p = g.players[0]
    g.step([{"run": [{"name": "m", "params_b": 2.0, "tokens_b": 40, "mix": {"web": 1.0}}]}, {}])
    while p.runs["m"].status != "done":
        g.step([{}, {}])
    g.step([{"release": [{"run": "m", "price": 8}]}, {}])
    m = p.models[0]
    assert m["id"] == "A1" and m["live"] == g.t + E.LAUNCH
    assert abs(m["pub"] - m["C"] - g.w["c"]["web"]) < 1e-9          # 只训网页：污染加成 = c_web
    for _ in range(E.LAUNCH):
        g.step([{}, {}])
    assert "A1" in g.models
    # 份额每周最多迁移 15 个百分点
    before = dict(g.shares)
    g._shares(g.t)
    for k, v in g.shares.items():
        assert abs(v - before.get(k, 0)) <= E.MIG_MAX + 1e-9
    # 复测
    m["pub"] = m["C"] + 0.08
    g._monthly(E.MONTH)
    assert m["audited"] and abs(m["pub"] - m["C"]) < 1e-12 and p.rep <= -E.AUDIT_REP + 1e-9


def test_raise_dilution():
    g = game()
    p = g.players[0]
    g.t = E.QUARTER + 5
    v_before = g.value(0)["V"]
    g._raise(p, 100.0, g.t)
    assert abs(p.equity - v_before / (v_before + 100.0)) < 1e-12 and abs(p.cash - 300.0) < 1e-9
    # 对齐估值口径后，融资不产生免费股权估值套利
    assert g.value(0)["value"] <= v_before + 1e-6
    g._raise(p, 100.0, g.t + 1)                            # 同一季度第二次：拒绝
    assert abs(p.cash - 300.0) < 1e-9


def test_value_parts():
    g = game()
    run_ticks(g, 50)
    v = g.value(0)
    assert abs(v["V"] - (v["cash"] + v["recv"] + v["hw"] + v["site"] + E.END_GM_MULT * v["gm30"] + E.END_C_MULT * v["C"])) < 1e-6
    assert abs(v["value"] - v["V"] * v["equity"]) < 1e-9


def test_synth_distill_poach_ppa():
    g = game(ticks=3000)
    a, b = g.players
    b.tech.add("synth")
    g.models["B9"] = {"id": "B9", "owner": "B", "C": 0.5, "pub": 0.5, "price": 10.0, "live": 0, "N": 10.0}
    cash_b = b.cash
    g.step([{"distill": [{"model": "B9", "tokens_b": 5}]}, {"synth": [{"tokens_b": 2}]}])
    assert abs(a.U["distill"] - 5) < 1e-9 and abs(b.cash - cash_b) > 0
    assert abs(a.data["distill"][0][1] - (0.5 + 0.8 * 0.5)) < 1e-12
    for _ in range(E.DAY + 1):
        g.step([{}, {}])
    assert b.U["synth"] > g.w["public_b"]["synth"]
    g.step([{"ppa": [{"site": 0, "mwh": 8760}]}, {}])
    assert a.sites[0]["ppa"] and abs(a.sites[0]["ppa"][0][1] - 1.0) < 1e-9
    n0 = len(a.staff)
    while g.t % E.WEEK != E.WEEK - 1:
        g.step([{}, {}])
    cash_a_before = a.cash
    g.step([{"poach": [{"target": "opp", "idx": 0, "salary": 0.01}]}, {}])  # 合理高薪且付得起 2 周签约金
    assert len(a.staff) == n0 + 1 and len(b.staff) == 19
    assert cash_a_before - a.cash >= E.POACH_OPP_SIGNING * 0.01 - 0.5


def test_symmetry_swap_seats():
    r1 = E.play(_find_bot("merchant"), _find_bot("scaling"), 4, ticks=3000, inproc=True)
    r2 = E.play(_find_bot("scaling"), _find_bot("merchant"), 4, ticks=3000, inproc=True)
    assert r1["value"] == r2["value"][::-1] and r1["C"] == r2["C"][::-1]


def test_score_formulas():
    sys.path.insert(0, os.path.dirname(ROOT))
    import strategy_scoring as score
    scale = E.VALUE_SCALE
    assert abs(E.s_share(100, 100) - 0.5) < 1e-12
    for delta in (-2 * scale, -scale, 0, scale, 2 * scale):
        assert abs(score.margin_score(delta, 0, scale) - 100 * E.s_share(delta, 0)) < 1e-10
    assert score.margin_score(0, 0, scale) == 50
    assert score.margin_score(scale, 0, scale) > 50
    assert score.percent(0.2) == 20 and score.percent(0.8) == 80


def test_subprocess_timeout_alignment():
    """第 3 tick 睡 0.5s 的 bot（本测试把单 tick 限时设为 0.3s）：超时 tick 按空动作，迟到回复被丢弃。"""
    src = textwrap.dedent('''
        import json, sys, time
        sys.stdin.readline(); print("{}", flush=True)
        for line in sys.stdin:
            m = json.loads(line)
            if m.get("type") == "end":
                print("{}", flush=True); break
            t = m["t"]
            if t == 3:
                time.sleep(0.5)
                print(json.dumps({"hire": [{"cand": 1, "salary": 1}], "research": [{"tech": "stable", "share": 1}]}), flush=True)
            elif t == 6:
                print(json.dumps({"research": [{"tech": "cooling", "share": 1}]}), flush=True)
            else:
                print("{}", flush=True)
        ''')
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        p = os.path.join(d, "sleepy.py")
        with open(p, "w") as f:
            f.write(src)
        g = E.Game(1, 12)
        bot = E.Bot(p, g.init_msg(0))
        acts = [{}, {}]
        for t in range(12):
            obs = g.step(acts)
            obs[0]["missed"] = bot.timeouts + bot.bad
            r = bot.ask(obs[0], timeout=0.3)
            acts = [r if isinstance(r, dict) else {}, {}]
        bot.kill()
        assert 1 <= bot.timeouts <= 3
        assert g.players[0].share_r == {"cooling": 1.0}     # 迟到的 t=3 动作没生效，t=6 的生效


def test_crash_bot_is_dead():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        p = os.path.join(d, "crash.py")
        with open(p, "w") as f:
            f.write("import sys\nsys.stdin.readline()\nprint('{}', flush=True)\nsys.stdin.readline()\nraise SystemExit(1)\n")
        g = E.Game(1, 5)
        bot = E.Bot(p, g.init_msg(0))
        for _ in range(3):
            bot.ask(g.step([{}, {}])[0], timeout=1.0)
        assert bot.dead
        bot.kill()


def test_full_match_smoke_subprocess():
    r = E.play(_find_bot("blank"), _find_bot("ref"), 2, ticks=400)
    assert all(f["timeouts"] == 0 and not f["dead"] and f["bad_json"] == 0 for f in r["faults"]), r["faults"]
    r = E.play(_find_bot("scaling"), E.SOLO_NPC, 2, ticks=300, inproc_b=True)
    assert r["faults"][0]["timeouts"] == 0


def test_delist_exploit_fix():
    g = game(ticks=26280)
    p = g.players[0]
    # 模拟发布模型 A1
    m1 = {"id": "A1", "owner": "A", "C": 0.85, "pub": 0.85, "price": 10.0, "live": 0, "online_ticks": 0, "N": 10.0}
    g.models["A1"] = m1
    # 推进 1 tick 后 delist
    g.step([{}, {}])
    assert m1["online_ticks"] == 1
    g.step([{"delist": ["A1"]}, {}])
    assert m1.get("delisted") is True
    # 秒下架模型 online_ticks < 730 且已 delisted，不能计入 best_c()
    assert g.best_c(0) == 0.0

    # 模拟在终局最后阶段发布 A2（例如只在线 10 ticks < MIN_ONLINE_END=72）
    m2 = {"id": "A2", "owner": "A", "C": 0.90, "pub": 0.90, "price": 10.0, "live": 26270, "online_ticks": 10, "N": 10.0}
    g.models["A2"] = m2
    assert g.best_c(0) == 0.0

    # 模拟终局满足提前发布期的在线模型 A3（在线 >= 72 ticks 且未下架）
    m3 = {"id": "A3", "owner": "A", "C": 0.92, "pub": 0.92, "price": 10.0, "live": 26170, "online_ticks": 80, "N": 10.0}
    g.models["A3"] = m3
    assert g.best_c(0) == 0.92

    # 模拟历史正常服务满 730 ticks 后才退役下架的模型 A4（online_ticks >= 730）
    m4 = {"id": "A4", "owner": "A", "C": 0.95, "pub": 0.95, "price": 10.0, "live": 1000, "online_ticks": 800, "delisted": True, "N": 10.0}
    g.models["A4"] = m4
    assert g.best_c(0) == 0.95


def test_gross_margin_smoothing_and_multiplier():
    assert E.GM_WINDOW == E.QUARTER == 2190
    assert E.END_GM_MULT == 24.0
    g = game(ticks=3000)
    p = g.players[0]
    assert len(p.rev_ring) == E.GM_WINDOW
    assert len(p.cost_ring) == E.GM_WINDOW
    run_ticks(g, 100)
    v = g.value(0)
    assert "gm" in v and "gm30" in v
    assert abs(v["gm"] - v["gm30"]) < 1e-9


def test_distill_strong_smoke():
    bot = _find_bot("distill_strong")
    assert os.path.exists(bot)
    r = E.play(bot, _find_bot("scaling"), 3, ticks=300, inproc=True)
    assert all(f["timeouts"] == 0 and not f["dead"] and f["bad_json"] == 0 and f["illegal"] == 0 for f in r["faults"])


# ---------------------------------------------------------------- P0/P1 安全与公平性回归测试
def test_p0_1_tiny_model_price_cycle_and_raise_arbitrage_blocked():
    g = game(ticks=4000, seed=1)
    p = g.players[0]
    # 1) 极小垃圾模型 C < API_MIN_C (0.15) 即使定价 0.5 也不得进入旗舰/获取份额
    g.step([{"run": [{"name": "tiny", "params_b": 0.01, "tokens_b": 0.01}]}, {}])
    while p.runs["tiny"].status != "done":
        g.step([{}, {}])
    g.step([{"release": [{"run": "tiny", "price": 0.5}]}, {}])
    for _ in range(E.LAUNCH + E.WEEK + 2):
        g.step([{}, {}])
    assert g.models["A1"]["C"] < E.API_MIN_C
    assert g._flagship("A")[0] is None
    assert g.shares.get("A", 0.0) == 0.0
    assert p.tot["revenue"] == 0.0

    # 2) 达标模型 C >= 0.15：周一前降价 0.5、周一后暴涨 100.0 会被即时价格冲击与周内最差效用双杀
    g2 = game(ticks=2000, seed=1)
    p2 = g2.players[0]
    g2.models["A1"] = {"id": "A1", "owner": "A", "C": 0.55, "pub": 0.55, "price": 1.0,
                       "week_max_price": 1.0, "live": 0, "N": 20.0, "online_ticks": 100}
    g2.shares = {"A": 0.5, "B": 0.0, "N1": 0.25, "N2": 0.25}
    p2.share = 0.5
    g2.step([{}, {}])
    assert g2.shares["A"] >= 0.3
    # 次日猛涨至 100.0 -> 当 tick 即时份额雪崩
    for _ in range(E.DAY):
        g2.step([{}, {}])
    s_before = g2.shares["A"]
    g2.step([{"price": [{"model": "A1", "value": 100.0}]}, {}])
    assert g2.shares["A"] < s_before * 0.05, (s_before, g2.shares["A"])
    # 周日前夜回落到 0.5，但周一结算仍按 week_max_price=100.0 / wk_worst_u 计，只能停留在高价均衡（<5%），不可反弹 +15%
    while g2.t % E.WEEK != E.WEEK - 2:
        g2.step([{}, {}])
    g2.step([{"price": [{"model": "A1", "value": 0.5}]}, {}])
    g2.step([{}, {}])
    assert g2.shares["A"] < 0.05, g2.shares["A"]

    # 3) 融资投前估值 <= 真实 value()["V"]，禁止 3.33x 营收套利
    g3 = game(ticks=4000, seed=1)
    p3 = g3.players[0]
    g3.t = E.QUARTER + 10
    for i in range(E.MONTH):
        p3.tot_ring[i] = 1.0          # 假装近 30 天有 730M 营业收入（旧版 V_pre 会膨胀到 175,000M）
    for i in range(E.GM_WINDOW):
        p3.rev_ring[i] = 0.01
        p3.cost_ring[i] = 0.0
    v_before = g3.value(0)
    g3._raise(p3, 1e6, g3.t)
    v_after = g3.value(0)
    assert v_after["value"] <= v_before["value"] + 1e-6, (v_before, v_after)


def test_p0_2_auction_starvation_and_bad_bids_blocked():
    g = game(ticks=500, seed=2)
    run_ticks(g, 1)
    o = g.offer["G1"]
    g.offer["G1"] = {**o, "lots": 2, "npc": []}
    # A 只有 200M 现金却报 1e14 天价抢 64 批；B 正常报 reserve*1.5 买 2 批
    g.step([
        {"bid_gpu": [{"model": "G1", "lots": 64, "price": 1e14}]},
        {"bid_gpu": [{"model": "G1", "lots": 2, "price": o["reserve"] * 1.5}]},
    ])
    for _ in range(E.WEEK - 1):
        g.step([{}, {}])
    assert not g.players[0].deliv, "无现金支撑的 1e14 虚高报价不应中标"
    assert g.players[1].deliv and g.players[1].deliv[0][2] == 2 * E.LOT, "对手应正常拍得全部 2 批"
    assert abs(g.players[1].deliv[0][3] - o["reserve"]) < 1e-9, "虚高报价不得抬高出清价"
    # 负批数或非正报价记非法
    ill0 = g.players[0].faults["illegal"]
    g.step([{"bid_gpu": [{"model": "G1", "lots": -1, "price": 1.0}, {"model": "G1", "lots": 1, "price": 0.0}]}, {}])
    assert g.players[0].faults["illegal"] == ill0 + 2


def test_p0_3_wsd_set_tokens_exploit_blocked():
    g = game(ticks=2000, seed=1)
    p = g.players[0]
    g.step([{"run": [{"name": "w", "params_b": 2.0, "tokens_b": 500.0, "sched": "wsd",
                      "gpus": {"G1": 256}, "ckpt_every": 2}]}, {}])
    for _ in range(40):
        g.step([{}, {}])
    r = p.runs["w"]
    assert 10.0 < r.D < 200.0
    c_before = g.cap_of(p, r.ckpts[-1], r)
    # 试图把 target 骤缩到 D + 1e-4 白嫖 3% 退火加成
    g.step([{"run_ctl": [{"name": "w", "op": "set_tokens", "arg": r.D + 1e-4}]}, {}])
    assert r.anneal_locked is True
    g.step([{}, {}])
    assert r.status == "done"
    c_after = g.cap_of(p, r.ckpts[-1], r)
    assert c_after - c_before < 1e-3, f"不应通过骤缩 target 白嫖 WSD 退火加成: {c_before} -> {c_after}"
    # 对已完成任务再次 set_tokens 拉回 500 必须被拒
    ill0 = p.faults["illegal"]
    g.step([{"run_ctl": [{"name": "w", "op": "set_tokens", "arg": 500.0}]}, {}])
    assert p.faults["illegal"] == ill0 + 1 and r.status == "done"

    # 正常 WSD 跑满最后 15% 退火阶段应获得完整 3% 加成
    g2 = game(ticks=2000, seed=1)
    p2 = g2.players[0]
    g2.step([{"run": [{"name": "okw", "params_b": 1.0, "tokens_b": 20.0, "sched": "wsd",
                       "gpus": {"G1": 256}, "ckpt_every": 100}]}, {}])
    while p2.runs["okw"].status != "done":
        g2.step([{}, {}])
    rw = p2.runs["okw"]
    assert rw.anneal_tok >= 0.15 * rw.target - 1e-6
    st_no_ann = {**rw.ckpts[-1], "anneal_tok": 0.0}
    l_with = g2.loss_of(p2, rw.ckpts[-1], rw)
    l_without = g2.loss_of(p2, st_no_ann, rw)
    assert abs(l_with[0] / l_without[0] - 0.97) < 1e-6


def test_p0_4_distill_pause_evasion_blocked_and_symmetric():
    # 找到一个会触发蒸馏诉讼的周序号 wk
    g = game(ticks=4000, seed=5)
    hit_wk = None
    for wk in range(1, 50):
        if W.hash_u(g.key["sue"], wk, 0) < E.DISTILL_SUE_P:
            hit_wk = wk
            break
    assert hit_wk is not None
    # 双方均以 100% 蒸馏训练，但在周一前 1 tick 暂停试图逃避起诉
    for p in g.players:
        p.data["distill"].append([500.0, 1.0, 0.0, "distill:N1-1", None])
        p.max_teacher = 0.6
        p.refresh_data(g.w)
    spec = {"run": [{"name": "d1", "params_b": 1.0, "tokens_b": 1000.0, "mix": {"distill": 1.0}, "gpus": {"G1": 64}}]}
    g.step([spec, spec])
    target_t = hit_wk * E.WEEK
    while g.t < target_t - 2:
        g.step([{}, {}])
    # 周一前暂停
    g.step([{"run_ctl": [{"name": "d1", "op": "pause"}]}, {"run_ctl": [{"name": "d1", "op": "pause"}]}])
    obs = g.step([{}, {}])  # 到达 target_t (周一结算)
    for seat in (0, 1):
        evs = [e for e in obs[seat].get("ev", []) if e.get("type") == "lawsuit" and e.get("kind") == "distill"]
        assert evs, f"座位 {seat} 周一前暂停不得逃避蒸馏诉讼"
        assert g.players[seat].tot["fines"] > 0


def test_p0_5_poach_fire_wipe_max_staff_and_salary_guards():
    g = game(ticks=2000, seed=3)
    a, b = g.players
    # 负薪资 / 0 薪资被拒
    ill0 = a.faults["illegal"]
    g.step([{"poach": [{"target": "opp", "idx": 0, "salary": -0.01}, {"target": "N1", "idx": 0, "salary": 0.0}]}, {}])
    assert a.faults["illegal"] == ill0 + 2
    assert not a.poaches

    # 单周对手挖角最多 2 次且成功后进入 2 周冷却，并收签约金
    while g.t % E.WEEK != E.WEEK - 1:
        g.step([{}, {}])
    poach_5 = [{"target": "opp", "idx": i, "salary": 0.01} for i in range(5)]
    cash0 = a.cash
    g.step([{"poach": poach_5}, {}])
    assert len(b.staff) >= 18, f"单周挖对手上限 2 人，B 剩余不应少于 18 人，实测 {len(b.staff)}"
    assert len(a.staff) == 20 + (20 - len(b.staff))
    assert cash0 - a.cash >= (20 - len(b.staff)) * E.POACH_OPP_SIGNING * 0.01 - 0.5
    # 下一周仍在冷却期内，挖对手应失败
    while g.t % E.WEEK != E.WEEK - 1:
        g.step([{}, {}])
    b_cnt = len(b.staff)
    g.step([{"poach": [{"target": "opp", "idx": 0, "salary": 0.01}]}, {}])
    assert len(b.staff) == b_cnt

    # fire 必须支付 1 周遣散费
    s0 = a.staff[0]
    cash_pre = a.cash
    g.step([{"fire": [s0["id"]]}, {}])
    assert cash_pre - a.cash >= E.FIRE_SEVERANCE * s0["sal"] - 0.5

    # MAX_STAFF 必须限制 poach
    while len(a.staff) < E.MAX_STAFF:
        a.staff.append({"id": a.staff_id, "ab": 1, "sal": 0.0001, "since": 0})
        a.staff_id += 1
    while g.t % E.WEEK != E.WEEK - 1:
        g.step([{}, {}])
    g.step([{"poach": [{"target": "N1", "idx": 0, "salary": 0.01}]}, {}])
    assert len(a.staff) == E.MAX_STAFF


def test_p0_6_ipc_bounds_action_caps_and_fast_end():
    import time
    # 1) 列表动作上限 & PPA 数量上限
    g = game(ticks=200, seed=1)
    p = g.players[0]
    g.step([{"ppa": [{"site": 0, "mwh": 100.0}] * 100}, {}])
    assert p.faults["illegal"] >= 1
    assert len(p.sites[0]["ppa"]) <= E.MAX_PPA_PER_SITE

    # 2) 无节制刷 stdout 触发队列上限被立即终止，不 OOM
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        spam = os.path.join(d, "spam.py")
        with open(spam, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.stdin.readline()\nfor _ in range(500):\n    sys.stdout.write('{}\\n')\nsys.stdout.flush()\n")
        r = E.play(spam, _find_bot("blank"), 1, ticks=5)
        assert r["faults"][0]["dead"] and r["faults"][0]["bad_json"] >= 1

        # 3) 超大单行（>1 MiB）被截断终止
        big = os.path.join(d, "bigline.py")
        with open(big, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.stdin.readline()\nsys.stdout.write('{' + ' ' * (2 * 1024 * 1024) + '}\\n')\nsys.stdout.flush()\n")
        r2 = E.play(big, _find_bot("blank"), 1, ticks=5)
        assert r2["faults"][0]["dead"] and r2["faults"][0]["bad_json"] >= 1

        # 4) 结束消息挂死在 ~1s 内返回，不卡 122s
        hang_end = os.path.join(d, "hang_end.py")
        with open(hang_end, "w", encoding="utf-8") as fh:
            fh.write(
                "import json, sys, time\n"
                "sys.stdin.readline(); print('{}', flush=True)\n"
                "for line in sys.stdin:\n"
                "    m = json.loads(line)\n"
                "    if m.get('type') == 'end':\n"
                "        time.sleep(30); break\n"
                "    print('{}', flush=True)\n"
            )
        t0 = time.perf_counter()
        E.play(hang_end, _find_bot("blank"), 1, ticks=3)
        dt = time.perf_counter() - t0
        assert dt < 6.0, f"end 消息挂死耗时过长: {dt:.2f}s"


def test_p1_manual_ckpt_cost_bankrupt_pending_and_sell_gpu_caps():
    # P1-1: 手动 ckpt 同样扣 20% 吞吐
    g = game(ticks=200, seed=1)
    p = g.players[0]
    spec = {"name": "c", "params_b": 1.0, "tokens_b": 1000.0, "gpus": {"G1": 64}, "batch": 64,
            "ckpt_every": 100, "lr": 0.9 * g.eta_star(1.0)}
    g.step([{"run": [spec]}, {}])
    d1 = p.runs["c"].D
    g.step([{"run_ctl": [{"name": "c", "op": "ckpt"}]}, {}])
    d2 = p.runs["c"].D
    step_normal = d1
    step_ckpt = d2 - d1
    assert abs(step_ckpt - step_normal * (1.0 - E.CKPT_COST)) < 1e-9, (step_ckpt, step_normal)

    # P1-2: 同 tick 动作执行顺序按 (t + seed) % 2 交替（两方同 tick 竞租唯一余量卡时偶/奇 tick 轮流先得）
    winners_by_parity = {}
    for offset in (0, 1):
        gp = game(ticks=100, seed=1)
        gp.step([{}, {}])
        # 让 t+seed 分别为偶数与奇数，且云租池只剩刚好 1 张卡可租
        if (gp.t + 1 + gp.seed) % 2 != offset:
            gp.step([{}, {}])
        u, bg, pool = gp.rent_u("G1")
        room = int(pool * (1 - u))
        assert room > 1
        gp.players[0].rentals.append({"id": 9999, "m": "G1", "n": room - 1, "kind": "ondemand", "end": 100})
        gp.step([{"rent": [{"model": "G1", "n": 1}]}, {"rent": [{"model": "G1", "n": 1}]}])
        ok0 = any(r.get("act") == "rent" and r.get("ok") for r in gp.players[0].res)
        ok1 = any(r.get("act") == "rent" and r.get("ok") for r in gp.players[1].res)
        assert ok0 != ok1
        winners_by_parity[offset] = 0 if ok0 else 1
    assert winners_by_parity[0] == 0 and winners_by_parity[1] == 1

    # P1-3: 破产清除 pending_models，不得之后上线
    gb = game(ticks=2000, seed=1)
    pb = gb.players[0]
    gb.pending_models.append({"id": "A1", "owner": "A", "C": 0.9, "pub": 0.9, "price": 10.0,
                              "week_max_price": 10.0, "live": 50, "N": 10.0, "online_ticks": 0})
    pb.cash = -10.0
    pb.neg = E.BANKRUPT_TICKS - 1
    gb.step([{}, {}])
    assert pb.bankrupt
    assert all(m["owner"] != "A" for m in gb.pending_models)

    # P1-4: 低热度 sell_gpu 恒低于拍卖保留价，且终局折旧卡不可套利翻倍
    gs = game(ticks=E.TICKS, seed=1)
    ps = gs.players[0]
    gs.w["heat"][0] = 0.6
    gs.step([{"sell_gpu": [{"model": "G1", "n": 10}]}, {}])
    reserve_low = 0.8 * 0.6 * E.GPU["G1"]["price"]
    assert ps.recv[0][1] / 10.0 < reserve_low
    # 局末 t = TICKS - 1 卖起始老卡：应收款 <= 被扣掉的硬件残值
    gs2 = game(ticks=E.TICKS, seed=1)
    ps2 = gs2.players[0]
    gs2.t = E.TICKS - 2
    hw_before = gs2.value(0)["hw"]
    gs2.step([{"sell_gpu": [{"model": "G1", "n": 512}]}, {}])
    hw_after = gs2.value(0)["hw"]
    recv_gain = sum(r[1] for r in ps2.recv)
    assert recv_gain <= (hw_before - hw_after) + 1e-9

    # P0-7: 最晚发布观测时刻 t = 26183 恰好在线 72 tick，t = 26184 仅 71 tick
    for t_obs, expect_ok in ((26183, True), (26184, False)):
        t_live = t_obs + 1 + E.LAUNCH
        online_end = E.TICKS - t_live
        assert (online_end >= E.MIN_ONLINE_END) is expect_ok


def test_zip_dir_submission_and_bad_archive_security():
    import zipfile
    sys.path.insert(0, os.path.join(ROOT, "engine"))
    import archive

    tpl_dir = os.path.join(ROOT, "agents", "template")
    assert archive.is_submission_path(tpl_dir)
    # 1) 目录直接参赛
    r_dir = E.play(tpl_dir, E.SOLO_NPC, 1, ticks=48, inproc_b=True)
    assert all(f["timeouts"] == 0 and not f["dead"] and f["bad_json"] == 0 and f["illegal"] == 0 for f in r_dir["faults"])

    # 2) 打包为 .zip 参赛
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        zpath = os.path.join(tmp, "tpl.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for fn in ("main.py", "planner.py", "params.json"):
                zf.write(os.path.join(tpl_dir, fn), fn)
        assert archive.check_archive(zpath) == []
        r_zip = E.play(zpath, E.SOLO_NPC, 1, ticks=48, inproc_b=True)
        assert r_zip["value"] == r_dir["value"] and r_zip["faults"][0]["illegal"] == 0 and not r_zip["faults"][0]["dead"]

        # 3) 各类恶意/坏压缩包全部被拒且不崩引擎
        bad_cases = [
            ("empty.zip", []),
            ("no_main.zip", [("other.py", b"print(1)\n", 0o100644 << 16)]),
            ("traversal.zip", [("../evil.py", b"x", 0o100644 << 16), ("main.py", b"pass", 0o100644 << 16)]),
            ("backslash.zip", [("a\\b.py", b"x", 0o100644 << 16), ("main.py", b"pass", 0o100644 << 16)]),
            ("win_reserved.zip", [("CON.py", b"x", 0o100644 << 16), ("main.py", b"pass", 0o100644 << 16)]),
            ("win_illegal.zip", [("bad*name.py", b"x", 0o100644 << 16), ("main.py", b"pass", 0o100644 << 16)]),
            ("symlink.zip", [("link.py", b"/etc/passwd", 0o120777 << 16), ("main.py", b"pass", 0o100644 << 16)]),
            ("case_clash.zip", [("Foo.py", b"x", 0o100644 << 16), ("foo.py", b"y", 0o100644 << 16), ("main.py", b"pass", 0o100644 << 16)]),
            ("bomb.zip", [("main.py", b"A" * (2 * 1024 * 1024), 0o100644 << 16)]),
        ]
        for fname, entries in bad_cases:
            bp = os.path.join(tmp, fname)
            with zipfile.ZipFile(bp, "w", zipfile.ZIP_DEFLATED) as zf:
                for arcname, data, ext in entries:
                    zi = zipfile.ZipInfo("placeholder")
                    zi.filename = arcname          # 绕过 Windows ZipInfo.__init__ 的反斜杠规整，保留真实恶意头
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zi.external_attr = ext
                    zf.writestr(zi, data)
            probs = archive.check_archive(bp)
            assert probs, f"应拒绝恶意压缩包：{fname}"
            r_bad = E.play(bp, E.SOLO_NPC, 1, ticks=4, inproc_b=True)
            assert r_bad["faults"][0]["dead"] and r_bad["faults"][0]["bad_json"] >= 1, fname


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print("all ok")
