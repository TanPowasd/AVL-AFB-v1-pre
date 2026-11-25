"""scaling_hegemon: Compute & Frontier Scaling Hegemon (private scoring_v2 opponent).

Strategic identity:
  - Infrastructure-first silicon hegemony: just-in-time Site 0 expansion & low-cost/low-reg
    datacenter builds ahead of G3/G4/G5 waves, hedged aggressively with 0.8x PPAs, and
    dominating early-supply + newest-generation GPU auctions with zero equity dilution.
  - Disciplined cloud-compute acceleration: uses surplus operating cash (never starving
    GPU auctions!) on on-demand newest-gen cloud GPUs during high-ROI frontier training
    windows (Q0..Q10), while transitioning cleanly to 100% owned-GPU serving in Q11 for
    near-100% gross margin.
  - Pure frontier scaling (zero distillation): fast low-LR spike-free probes + wide-range
    N×D scaling grid + online loss refinement + live B_crit inference, exact Markov renewal
    card/batch/ckpt throughput maximization, convex multi-domain transfer + synthetic data
    curriculum optimization, proactive pre-audit pipelining, and a full-3%-WSD-annealed
    Hegemon Titan launched right before Q11.
  - Optimal frontier monetization: steady-state equilibrium pricing mid-game transitioning
    to exact finite-horizon Bellman DP over (share, price) for the 25x-weighted Q11 GM
    window with asymmetric Sunday-cut / Monday-raise timing, zero-shock guards, and
    auto-mode owned-GPU serving.

Deterministic, single-file, Python standard library only.
"""
from __future__ import annotations

import json
import math
import sys

REAL_DOMAINS = ("web", "code", "math", "books", "science", "dialog", "multiling")
ALL_POOLS = REAL_DOMAINS + ("synth", "distill")
GPU_ORDER = ("G1", "G2", "G3", "G4", "G5")
CONTAM_PRIOR_SAFE = 0.085  # Conservative prior for unfiltered domains to prevent audit_fail

# Expected cross-domain transfer matrix T_prior[d][k] from domain priors
_SPECIAL_T = {
    ("math", "code"): 0.55,
    ("dialog", "multiling"): 0.45,
    ("science", "books"): 0.40,
    ("dialog", "web"): 0.30,
    ("math", "science"): 0.30,
    ("code", "web"): 0.175,
}
T_PRIOR: dict[str, dict[str, float]] = {}
for _d in REAL_DOMAINS:
    _row: dict[str, float] = {}
    for _k in ALL_POOLS:
        if _k == _d:
            _row[_k] = 1.0
        elif (_d, _k) in _SPECIAL_T:
            _row[_k] = _SPECIAL_T[(_d, _k)]
        elif _k == "synth":
            _row[_k] = 0.325
        elif _k == "distill":
            _row[_k] = 0.50
        else:
            _row[_k] = 0.07
    T_PRIOR[_d] = _row

TRANSFER_LEVERAGE = {
    k: sum(T_PRIOR[d][k] for d in REAL_DOMAINS) for k in ALL_POOLS
}


def _solve_3x3(rows: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float] | None:
    """Weighted OLS y = a + b*x1 + c*x2 via partial pivoting. rows=[(x1, x2, y, w)]."""
    s00 = s01 = s02 = s03 = 0.0
    s11 = s12 = s13 = 0.0
    s22 = s23 = 0.0
    for x1, x2, y, w in rows:
        s00 += w
        wx1 = w * x1
        wx2 = w * x2
        s01 += wx1
        s02 += wx2
        s03 += w * y
        s11 += wx1 * x1
        s12 += wx1 * x2
        s13 += wx1 * y
        s22 += wx2 * x2
        s23 += wx2 * y
    mat = [
        [s00, s01, s02, s03],
        [s01, s11, s12, s13],
        [s02, s12, s22, s23],
    ]
    for col in range(3):
        piv = col
        best_abs = abs(mat[col][col])
        for r in range(col + 1, 3):
            v = abs(mat[r][col])
            if v > best_abs:
                best_abs = v
                piv = r
        if best_abs < 1e-14:
            return None
        if piv != col:
            mat[col], mat[piv] = mat[piv], mat[col]
        diag = mat[col][col]
        row_c = mat[col]
        for r in range(3):
            if r == col:
                continue
            row_r = mat[r]
            fac = row_r[col] / diag
            for c in range(col, 4):
                row_r[c] -= fac * row_c[c]
    a = mat[0][3] / mat[0][0]
    b = mat[1][3] / mat[1][1]
    c = mat[2][3] / mat[2][2]
    sse = 0.0
    for x1, x2, y, w in rows:
        diff = y - a - b * x1 - c * x2
        sse += w * diff * diff
    return a, b, c, sse


def _expected_ability(lo: int, hi: int, ask: float) -> float:
    """Exact Bayesian posterior E[ab | lo, hi, ask] from candidate salary ask range."""
    priors = (0.0, 0.30, 0.30, 0.20, 0.13, 0.07)
    num = 0.0
    den = 0.0
    for a in range(max(1, lo), min(5, hi) + 1):
        base = 0.0001 * (0.5 + 0.5 * a)
        if base * 0.9 - 1.5e-6 <= ask <= base * 1.2 + 1.5e-6:
            lik = priors[a] / max(1e-9, base * 0.3)
            num += a * lik
            den += lik
    return num / den if den > 0.0 else 0.5 * (lo + hi)


def _markov_eff(n: int, k: int, mtbf: float) -> float:
    """Exact steady-state fraction of compute delivered under per-tick failure f=n/mtbf."""
    if n <= 0 or k <= 0:
        return 0.0
    f = n / mtbf
    if f < 1e-9:
        return max(0.0, 1.0 - 0.2 / k)
    if f >= 0.99:
        return 0.0
    p_surv = (1.0 - f) ** k
    denom = (1.0 - p_surv) * (1.0 / f + 2.0)
    if denom <= 1e-12:
        return max(0.0, 1.0 - 0.2 / k)
    return max(0.0, (k - 0.2) * p_surv / denom)


_CKPT_CACHE: dict[tuple[int, float], tuple[int, float]] = {}


def _best_ckpt_and_rel(n: int, mtbf: float) -> tuple[int, float]:
    """Exact optimal ckpt_every k in [2..48] and its steady-state reliability factor."""
    if n <= 0:
        return 24, 1.0
    key = (n, mtbf)
    cached = _CKPT_CACHE.get(key)
    if cached is not None:
        return cached
    f = n / mtbf
    k0 = int(max(2, min(48, round(math.sqrt(0.4 / max(1e-7, f))))))
    lo = max(2, k0 - 3)
    hi = min(48, k0 + 4)
    best_k, best_e = k0, -1.0
    for k in range(lo, hi + 1):
        e = _markov_eff(n, k, mtbf)
        if e > best_e:
            best_e, best_k = e, k
    res = (best_k, best_e)
    _CKPT_CACHE[key] = res
    return res


def _demand_growth(t: int) -> float:
    m = t / 730.0
    if m <= 12.0:
        return 1.08 ** m
    if m <= 24.0:
        return (1.08 ** 12) * (1.05 ** (m - 12.0))
    return (1.08 ** 12) * (1.05 ** 12) * (1.03 ** (m - 24.0))


class WorldTracker:
    """Tracks public/private state, exact data pools, PPAs, leases, auctions, models, and runs."""

    def __init__(self, init: dict):
        self.seat = int(init["seat"])
        self.me = "AB"[self.seat]
        self.opp = "BA"[self.seat]
        self.ticks = int(init.get("ticks", 26280))
        self.rules = dict(init["rules"])
        self.gpu_meta = {g["model"]: g for g in init["gpus"]}
        self.regions = list(init["regions"])
        self.techs = dict(init["techs"])

        self.U: dict[str, float] = {d["name"]: float(d["public_b"]) for d in init["domains"]}
        self.U.setdefault("synth", 10.0)
        self.U.setdefault("distill", 0.0)
        self.q_hat: dict[str, float] = {d["name"]: float(d["q_hint"]) for d in init["domains"]}
        self.q_hat.setdefault("synth", 0.75)
        self.q_hat.setdefault("distill", 0.0)
        self.c_hat: dict[str, float] = {d["name"]: CONTAM_PRIOR_SAFE for d in init["domains"]}
        self.c_hat["synth"] = 0.0
        self.c_hat["distill"] = 0.0
        self.filtered: set[str] = set()
        self.pend_synth: list[tuple[int, float, float]] = []

        self.t = 0
        self.cash = float(init["start"]["cash"])
        self.neg_cash_ticks = 0
        self.equity = 1.0
        self.rep = 0.0
        self.bk = False
        self.gpu_obs: dict[str, list[int]] = {}
        self.runs_obs: dict[str, list] = {}
        self.ev: list[dict] = []
        self.res: list[dict] = []
        self.wk: dict | None = None
        self.last_loss: dict[str, list[float]] = {}
        self.run_loss_ema: dict[str, float] = {}

        self.heat = 1.0
        self.demand = float(self.rules.get("Q0", 5.0))
        self.shock_est = 1.0
        self.shares: dict[str, float] = {"N1": 0.5, "N2": 0.5}
        self.my_share = 0.0
        self.drop = 0.0
        self.api_dem = 0.0
        self.api_srv = 0.0
        self.api_rev = 0.0
        self.rev_week_acc = 0.0
        self.last_week_rev = 0.0
        self.models: dict[str, dict] = {m["id"]: dict(m) for m in init.get("models", [])}
        self.my_models: dict[str, dict] = {}
        self.my_ever_models: dict[str, dict] = {}
        self.best_live_c_est: float = 0.0
        self.best_live_n: float = 0.0
        self.best_live_pub: float = 0.0

        self.staff: list[dict] = list(init["start"]["staff"])
        self.sites: list[dict] = [
            {
                "id": 0,
                "region": int(init["start"]["site"]["region"]),
                "mw": float(init["start"]["site"]["mw"]),
                "ready": 0,
                "pend_mw": 0.0,
                "cards": dict(init["start"]["gpus"]),
            }
        ]
        self.tech: set[str] = set()
        self.deliv: list[list] = []
        self.arrivals: list[tuple[int, str, int]] = []
        self.rent_prices: dict[str, float] = {}
        self.elec_prices: list[float] = [r["price"] for r in self.regions]
        self.open_source_live = False
        self.export_ctrl_q = -1
        self.curtailed_until: dict[int, int] = {}

        # Financial & auction tracking
        self.leases: dict[int, dict] = {}
        self.next_lease_exp: int = 10**9
        self.next_synth_arr: int = 10**9
        self.pending_rent_queue: list[tuple[str, int, str]] = []
        self.pending_gpu_bid_cost: float = 0.0
        self.clear_ratio: dict[str, float] = {m: 1.22 for m in GPU_ORDER}
        self.opp_gpu_won: dict[str, int] = {m: 0 for m in GPU_ORDER}
        self.opp_early_won: dict[str, int] = {m: 0 for m in GPU_ORDER}

        # Pricing guard tracking
        self.anchor_u: float | None = None
        self.last_price_exec_day: dict[str, int] = {}
        self.local_pend_mw: dict[int, float] = {}
        self.site_ppa_tranches: dict[int, list[tuple[int, float]]] = {}
        self.site_build_count = 1
        self.last_site_build_t = -10**9
        self.pending_builds: list[tuple[int, float]] = []

        # Run cumulative token & contamination tracking
        self.run_S: dict[str, dict[str, float]] = {}
        self.run_cont: dict[str, float] = {}
        self.run_mix: dict[str, dict[str, float]] = {}
        self.run_D_prev: dict[str, float] = {}
        self.run_ckpts: dict[str, dict[int, tuple[float, dict[str, float], float]]] = {}
        self.run_last_ckpt_id: dict[str, int] = {}
        self.run_N: dict[str, float] = {}

        self.pending_deal_meta: dict[int, dict] = {}
        self.data_spent_total: float = 0.0

    def pue(self) -> float:
        return float(self.rules.get("PUE1", 1.15)) if "cooling" in self.tech else float(self.rules.get("PUE0", 1.30))

    def infer_mult(self) -> float:
        if "infer_opt_2" in self.tech:
            return 0.6
        if "infer_opt_1" in self.tech:
            return 0.8
        return 1.0

    def algo_mult(self) -> float:
        lvl = sum(1 for k in ("algo_eff_1", "algo_eff_2", "algo_eff_3") if k in self.tech)
        return (1.0, 1.15, 1.30, 1.50)[lvl]

    def max_site_reg(self) -> float:
        if not self.sites:
            return float(self.regions[0]["reg"])
        big = max(self.sites, key=lambda s: (float(s.get("mw", 0.0)), -int(s.get("id", 0))))
        return float(self.regions[int(big["region"])]["reg"])

    def compute_of(self, card_map: dict[str, int]) -> float:
        return sum(self.gpu_meta[m]["power"] * n for m, n in card_map.items() if n > 0)

    def own_usable(self) -> dict[str, int]:
        return {m: int(v[0]) for m, v in self.gpu_obs.items() if v[0] > 0}

    def rented_usable(self) -> dict[str, int]:
        return {m: int(v[3]) for m, v in self.gpu_obs.items() if v[3] > 0}

    def total_usable(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m, v in self.gpu_obs.items():
            cnt = int(v[0]) + int(v[3])
            if cnt > 0:
                out[m] = cnt
        return out

    def kw_committed(self) -> float:
        pue = self.pue()
        kw = 0.0
        for m, v in self.gpu_obs.items():
            kw += (int(v[0]) + int(v[1]) + int(v[2])) * self.gpu_meta[m]["kw"] * pue
        for d in self.deliv:
            kw += int(d[2]) * self.gpu_meta[d[1]]["kw"] * pue
        return kw

    def mw_capacity(self) -> float:
        tot = 0.0
        for s in self.sites:
            sid = int(s["id"])
            tot += float(s["mw"]) + max(float(s.get("pend_mw", 0.0)), self.local_pend_mw.get(sid, 0.0))
        for _, mw in self.pending_builds:
            tot += mw
        return tot

    def model_u(self, pub: float, price: float) -> float:
        u_pub = float(self.rules.get("U_PUB", 6.0))
        u_pr = float(self.rules.get("U_PRICE", 1.5))
        return u_pub * pub - u_pr * math.log(max(price, 1.0))

    def active_flagship(self) -> dict | None:
        best, bu = None, -1e18
        for mid, meta in self.my_models.items():
            m = self.models.get(mid)
            if not m or meta.get("delisted"):
                continue
            if float(m.get("pub", 0.0)) < 0.145:
                continue
            u = self.model_u(float(m["pub"]), float(m["price"]))
            if u > bu:
                best, bu = m, u
        return best

    def has_any_live_model(self) -> bool:
        return len(self.my_ever_models) > 0

    def register_run(self, name: str, N: float, mix: dict[str, float]) -> None:
        self.run_S[name] = {p: 0.0 for p in ALL_POOLS}
        self.run_cont[name] = 0.0
        self.run_mix[name] = dict(mix)
        self.run_D_prev[name] = 0.0
        self.run_ckpts[name] = {}
        self.run_last_ckpt_id[name] = 0
        self.run_N[name] = N

    def update_run_mix(self, name: str, mix: dict[str, float]) -> None:
        self.run_mix[name] = dict(mix)

    def ingest(self, obs: dict) -> None:
        t = int(obs["t"])
        self.t = t
        cash = float(obs["cash"])
        self.cash = cash
        if cash < 0.0:
            self.neg_cash_ticks += 1
        else:
            self.neg_cash_ticks = 0
        self.equity = float(obs.get("eq", self.equity))
        self.rep = float(obs.get("rep", 0.0))
        self.bk = bool(obs.get("bk", False))
        self.gpu_obs = obs.get("gpu", {})
        runs_raw = obs.get("runs")
        if runs_raw:
            self.runs_obs = {r[0]: r for r in runs_raw}
        else:
            self.runs_obs = {}
        ev = obs.get("ev")
        res = obs.get("res")
        self.ev = ev if ev else []
        self.res = res if res else []

        api = obs.get("api")
        if api:
            self.api_dem = float(api[0])
            self.api_srv = float(api[1])
            rev = float(api[2])
            self.api_rev = rev
            self.rev_week_acc += rev
        else:
            self.api_dem = self.api_srv = self.api_rev = 0.0

        # Expire leases only when next expiry reached (O(1) on 99% of ticks)
        if t >= self.next_lease_exp and self.leases:
            nxt = 10**9
            for lid in [k for k, v in self.leases.items() if v["end"] <= t]:
                del self.leases[lid]
            for v in self.leases.values():
                if v["end"] < nxt:
                    nxt = v["end"]
            self.next_lease_exp = nxt

        # Expire pending synth arrivals only when due
        if t >= self.next_synth_arr and self.pend_synth:
            keep_s = []
            nxt_s = 10**9
            for arr_t, tok, q_s in self.pend_synth:
                if arr_t <= t:
                    old_u = self.U.get("synth", 0.0)
                    old_q = self.q_hat.get("synth", 0.75)
                    new_u = old_u + tok
                    if new_u > 0:
                        self.q_hat["synth"] = (old_u * old_q + tok * q_s) / new_u
                    self.U["synth"] = new_u
                else:
                    keep_s.append((arr_t, tok, q_s))
                    if arr_t < nxt_s:
                        nxt_s = arr_t
            self.pend_synth = keep_s
            self.next_synth_arr = nxt_s

        if "rent" in obs:
            self.rent_prices = dict(obs["rent"])
        if "elec" in obs:
            self.elec_prices = list(obs["elec"])
        if "loss" in obs:
            self.last_loss = dict(obs["loss"])
            for rname, lv in self.last_loss.items():
                if lv:
                    lm = sum(lv) / len(lv)
                    prev = self.run_loss_ema.get(rname)
                    self.run_loss_ema[rname] = lm if prev is None else (0.65 * prev + 0.35 * lm)

        # Process events
        if ev:
            for e in ev:
                ty = e.get("type")
                if ty == "release":
                    m = dict(e["model"])
                    self.models[m["id"]] = m
                    if m["owner"] == self.me:
                        self.my_models.setdefault(m["id"], {"delisted": False, "live_t": t})
                        self.my_models[m["id"]]["live_t"] = t
                        self.my_ever_models[m["id"]] = dict(m)
                        pub = float(m.get("pub", 0.0))
                        n_m = float(m.get("N") or 0.0)
                        if pub > self.best_live_pub:
                            self.best_live_pub = pub
                            self.best_live_c_est = max(0.0, pub - 0.025)
                            self.best_live_n = n_m
                elif ty == "open_source":
                    self.open_source_live = True
                    m = dict(e["model"])
                    self.models[m["id"]] = m
                elif ty == "export_ctrl":
                    self.export_ctrl_q = int(e.get("quarter", -1))
                elif ty == "curtail":
                    self.curtailed_until[int(e["region"])] = int(e["end"])
                elif ty == "tech":
                    tk = e["tech"]
                    self.tech.add(tk)
                    if tk.startswith("filter:") and "q" in e:
                        dom = tk[7:]
                        self.q_hat[dom] = float(e["q"])
                        self.c_hat[dom] = float(e["c"])
                        self.filtered.add(dom)
                elif ty == "audit_fail":
                    mid = e.get("model")
                    if mid in self.models:
                        self.models[mid]["pub"] = float(e["pub"])
                elif ty == "delivered":
                    self.arrivals.append((t, e["model"], int(e["n"])))
                elif ty == "preempted":
                    self.leases.pop(int(e.get("rent_id", -1)), None)
                elif ty in ("failure", "preempt"):
                    rname = e.get("run")
                    ck = int(e.get("ckpt", 0))
                    if rname in self.run_S:
                        snap = self.run_ckpts.get(rname, {}).get(ck)
                        if snap is not None:
                            self.run_D_prev[rname] = snap[0]
                            self.run_S[rname] = dict(snap[1])
                            self.run_cont[rname] = snap[2]
                        else:
                            self.run_D_prev[rname] = 0.0
                            self.run_S[rname] = {p: 0.0 for p in ALL_POOLS}
                            self.run_cont[rname] = 0.0

        # Process action receipts
        if res:
            for r in res:
                if not r.get("ok"):
                    continue
                act = r.get("act")
                if act == "release":
                    mid = r["model"]
                    self.my_models.setdefault(mid, {"delisted": False, "live_t": int(r.get("live", t + 24))})
                elif act == "rent":
                    rid = int(r["id"])
                    m = r["model"]
                    ticks_len = 168
                    for qi, (qm, qt, qk) in enumerate(self.pending_rent_queue):
                        if qm == m:
                            ticks_len = qt
                            del self.pending_rent_queue[qi]
                            break
                    end_t = t + ticks_len
                    self.leases[rid] = {"m": m, "n": int(r["n"]), "kind": r.get("kind", "ondemand"), "end": end_t}
                    if end_t < self.next_lease_exp:
                        self.next_lease_exp = end_t
                elif act == "expand_site":
                    sid = int(r["site"])
                    mw = float(r["mw"])
                    self.local_pend_mw[sid] = self.local_pend_mw.get(sid, 0.0) + mw
                elif act == "rollback":
                    rname = r.get("run")
                    ck = int(r.get("ckpt", 0))
                    if rname in self.run_S:
                        snap = self.run_ckpts.get(rname, {}).get(ck)
                        if snap is not None:
                            self.run_D_prev[rname] = snap[0]
                            self.run_S[rname] = dict(snap[1])
                            self.run_cont[rname] = snap[2]
                elif act == "bid_data":
                    did = int(r.get("deal", -1))
                    pr = float(r.get("price", 0.0))
                    self.data_spent_total += pr
                    dm = self.pending_deal_meta.pop(did, None)
                    if dm is not None:
                        dom = dm["domain"]
                        tok = dm["tokens_b"]
                        qh = dm["q_hint"]
                        old_u = self.U.get(dom, 0.0)
                        old_q = self.q_hat.get(dom, 1.0)
                        if old_u + tok > 0:
                            self.q_hat[dom] = (old_u * old_q + tok * qh) / (old_u + tok)
                        self.U[dom] = old_u + tok

        # Track run_S and checkpoints on every 6th tick or event tick (exact while mix is constant!)
        if self.runs_obs and (t % 6 == 0 or ev or res or t % 24 == 23):
            for rname, row in self.runs_obs.items():
                if rname not in self.run_S:
                    continue
                cur_d = float(row[2])
                prev_d = self.run_D_prev.get(rname, 0.0)
                dd = cur_d - prev_d
                if dd > 1e-9:
                    mx = self.run_mix.get(rname, {})
                    s_map = self.run_S[rname]
                    cont_add = 0.0
                    for p, fr in mx.items():
                        inc = dd * fr
                        s_map[p] = s_map.get(p, 0.0) + inc
                        c_p = self.c_hat.get(p, 0.0) if (p in self.filtered or p in ("synth", "distill")) else 0.06
                        cont_add += inc * c_p
                    self.run_cont[rname] = self.run_cont.get(rname, 0.0) + cont_add
                    self.run_D_prev[rname] = cur_d
                elif dd < -1e-6:
                    self.run_D_prev[rname] = cur_d
                ck_id = int(row[6])
                if ck_id > self.run_last_ckpt_id.get(rname, 0):
                    self.run_last_ckpt_id[rname] = ck_id
                    ck_map = self.run_ckpts[rname]
                    ck_map[ck_id] = (cur_d, dict(self.run_S[rname]), self.run_cont.get(rname, 0.0))
                    if len(ck_map) > 10:
                        del ck_map[min(ck_map)]

        # Weekly snapshot
        if "wk" in obs:
            wk = obs["wk"]
            self.wk = wk
            self.pending_gpu_bid_cost = 0.0  # Just settled on this Monday!
            self.last_week_rev = self.rev_week_acc
            self.rev_week_acc = 0.0
            self.heat = float(wk.get("heat", self.heat))
            self.demand = float(wk.get("demand", self.demand))
            g_dem = _demand_growth(self.t) * max(0.5, self.heat) * float(self.rules.get("Q0", 5.0))
            if g_dem > 0:
                self.shock_est = max(0.4, min(2.5, self.demand / g_dem))
            self.shares = dict(wk.get("shares", self.shares))
            self.my_share = float(self.shares.get(self.me, 0.0))
            live_ids = set()
            for m in wk.get("models", []):
                self.models[m["id"]] = dict(m)
                live_ids.add(m["id"])
            for mid in list(self.models):
                if mid not in live_ids:
                    del self.models[mid]

            for m, ares in (wk.get("auction") or {}).items():
                base_h = self.gpu_meta[m]["price"] * max(0.6, self.heat)
                if base_h > 0 and ares.get("lots", 0) > 0:
                    ratio = float(ares.get("clear", base_h)) / base_h
                    self.clear_ratio[m] = 0.55 * self.clear_ratio.get(m, 1.48) + 0.45 * ratio
                opp_lots = int((ares.get("winners") or {}).get(self.opp, 0))
                if opp_lots > 0:
                    self.opp_gpu_won[m] = self.opp_gpu_won.get(m, 0) + opp_lots * 256
                    q_prev = max(0, (self.t - 168)) // 2190
                    if q_prev == self.gpu_meta[m]["launch_q"] - 1:
                        self.opp_early_won[m] = self.opp_early_won.get(m, 0) + opp_lots

            my = wk["my"]
            self.staff = [{"id": int(s[0]), "ab": int(s[1]), "sal": float(s[2])} for s in my["staff"]]
            self.sites = list(my["sites"])
            self.pending_builds = []
            self.local_pend_mw = {int(s["id"]): float(s.get("pend_mw", 0.0)) for s in self.sites}
            self.site_build_count = len(self.sites)
            for k, v in my["data"].items():
                self.U[k] = float(v)
            self.tech = set(my["tech"])
            self.drop = float(my.get("drop", 0.0))
            self.deliv = list(my.get("deliv", []))
            for sid in list(self.site_ppa_tranches):
                self.site_ppa_tranches[sid] = [(et, mw) for et, mw in self.site_ppa_tranches[sid] if et > self.t]
            fl = self.active_flagship()
            self.anchor_u = self.model_u(float(fl["pub"]), float(fl["price"])) if fl else None
        else:
            self.wk = None


class DataMixOptimizer:
    """Convex multi-domain transfer + synthetic data curriculum mix optimizer (zero distill)."""

    def __init__(self, st: WorldTracker):
        self.st = st

    def _effective_de_vector(self, S: dict[str, float], U: dict[str, float], q: dict[str, float]) -> list[float]:
        eff = {}
        for p, s in S.items():
            u = U.get(p, 0.0)
            if s > 0.0 and u > 0.0:
                eff[p] = u * 4.0 * (1.0 - math.exp(-s / (4.0 * u))) * q.get(p, 1.0)
        out = []
        for d in REAL_DOMAINS:
            row = T_PRIOR[d]
            de = 0.0
            for p, v in eff.items():
                de += row[p] * v
            out.append(max(0.01, de))
        return out

    def optimize_mix(
        self,
        delta_D: float,
        S_now: dict[str, float] | None = None,
        cont_now: float = 0.0,
        D_now: float = 0.0,
        beta: float = 0.30,
        allow_synth: bool = True,
        max_cont_frac: float = 0.044,
        for_probe: bool = False,
        iters: int = 20,
    ) -> dict[str, float]:
        U = self.st.U
        q = self.st.q_hat
        D_tot = max(1e-3, D_now + delta_D)
        S0 = S_now or {p: 0.0 for p in ALL_POOLS}

        use_synth = allow_synth and (not for_probe) and U.get("synth", 0.0) >= 5.0
        pools = list(REAL_DOMAINS) + (["synth"] if use_synth else [])

        if use_synth:
            max_synth_tok = max(0.0, 0.296 * D_tot - S0.get("synth", 0.0))
            synth_cap = min(0.65, max_synth_tok / max(1e-6, delta_D))
            if synth_cap < 0.005:
                use_synth = False
                pools = list(REAL_DOMAINS)
        else:
            synth_cap = 0.0

        f: dict[str, float] = {}
        if use_synth:
            f["synth"] = min(synth_cap, 0.28)
            rem = 1.0 - f["synth"]
        else:
            rem = 1.0
        for p in REAL_DOMAINS:
            f[p] = rem / len(REAL_DOMAINS)

        c_est = {}
        for p in pools:
            if p == "synth":
                c_est[p] = 0.0
            elif p in self.st.filtered:
                c_est[p] = self.st.c_hat[p]
            else:
                c_est[p] = CONTAM_PRIOR_SAFE

        n_it = 12 if for_probe else iters
        for it in range(n_it):
            eff = {}
            deff_ds = {}
            for p in pools:
                u = max(1e-3, U.get(p, 0.0))
                s = S0.get(p, 0.0) + delta_D * f[p]
                ex = math.exp(-s / (4.0 * u))
                qp = max(0.35, q.get(p, 1.0))
                eff[p] = u * 4.0 * (1.0 - ex) * qp
                deff_ds[p] = ex * qp

            de_pow = []
            for d in REAL_DOMAINS:
                row = T_PRIOR[d]
                de = 0.01
                for p in pools:
                    de += row[p] * eff[p]
                de_pow.append(beta * (de ** (-beta - 1.0)))

            grad = {}
            mg = 0.0
            for p in pools:
                g = 0.0
                for idx_d, d in enumerate(REAL_DOMAINS):
                    g += de_pow[idx_d] * T_PRIOR[d][p]
                gp = g * deff_ds[p]
                grad[p] = gp
                if gp > mg:
                    mg = gp
            if mg <= 1e-18:
                break
            step = 0.48 / (1.0 + 0.12 * it)
            inv_mg = 1.0 / mg
            z = 0.0
            for p in pools:
                val = max(1e-5, f[p] * math.exp(step * (grad[p] * inv_mg - 0.5)))
                f[p] = val
                z += val
            inv_z = 1.0 / max(1e-12, z)
            for p in pools:
                f[p] *= inv_z

            if use_synth and f["synth"] > synth_cap:
                excess = f["synth"] - synth_cap
                f["synth"] = synth_cap
                zo = sum(f[p] for p in REAL_DOMAINS) or 1.0
                for p in REAL_DOMAINS:
                    f[p] += excess * f[p] / zo
            wb = f["web"] + f["books"]
            if wb > 0.475:
                sc = 0.475 / wb
                ex_wb = wb - 0.475
                f["web"] *= sc
                f["books"] *= sc
                zo = sum(f[p] for p in REAL_DOMAINS if p not in ("web", "books")) or 1.0
                for p in REAL_DOMAINS:
                    if p not in ("web", "books"):
                        f[p] += ex_wb * f[p] / zo

        if not for_probe:
            allowed_cont_rem = max(0.0, max_cont_frac * D_tot - cont_now)
            allowed_rate = allowed_cont_rem / max(1e-6, delta_D)
            for _ in range(10):
                cur_rate = sum(f[p] * c_est[p] for p in pools)
                if cur_rate <= allowed_rate:
                    break
                high_p = max(pools, key=lambda p: c_est[p] * (1.0 if f[p] > 0.02 else 0.0))
                low_pools = [p for p in pools if c_est[p] < cur_rate - 1e-4 and (p != "synth" or f[p] < synth_cap - 1e-4)]
                if not low_pools or f[high_p] <= 0.02:
                    break
                shift = min(f[high_p] * 0.35, 0.08)
                f[high_p] -= shift
                rem_shift = shift
                for lp in sorted(low_pools, key=lambda p: c_est[p]):
                    if rem_shift <= 1e-9:
                        break
                    room = 1.0
                    if lp == "synth":
                        room = max(0.0, synth_cap - f["synth"])
                    elif lp in ("web", "books"):
                        room = max(0.0, 0.475 - (f["web"] + f["books"]))
                    add = min(rem_shift, room)
                    f[lp] += add
                    rem_shift -= add
                if rem_shift > 1e-9:
                    f[high_p] += rem_shift
                    break

        s_tot = sum(f.values()) or 1.0
        return {p: round(f[p] / s_tot, 5) for p in pools if f[p] / s_tot > 1e-4}

    def effective_data_scalar(self, D: float, mix: dict[str, float], beta: float = 0.30) -> float:
        S = {p: D * fr for p, fr in mix.items()}
        des = self._effective_de_vector(S, self.st.U, self.st.q_hat)
        mean_pow = sum(de ** (-beta) for de in des) / 7.0
        return max(0.01, mean_pow ** (-1.0 / beta))


class ScalingFitter:
    """Fast spike-free LR probe + wide-range N×D scaling grid + online loss/B_crit refinement."""

    def __init__(self, st: WorldTracker, mixer: DataMixOptimizer):
        self.st = st
        self.mixer = mixer
        self.stage = 0
        self.stage_start = 0
        self.lr_probes: dict[str, dict] = {}
        self.sc_probes: dict[str, dict] = {}
        self.eta_c: float = 0.0018
        self.fit: dict[str, float] | None = None
        self.bc0_est: float = 380.0
        self.bc0_samples: int = 0
        self.obs_points: list[tuple[float, float, dict[str, float], float, float]] = []

    def eta_star(self, n: float) -> float:
        return self.eta_c * (n ** -0.3)

    def update_bc0_from_run(self, run_row: list, batch: int, slow_active: bool) -> None:
        if slow_active:
            return
        trained_d = float(run_row[2])
        eff = float(run_row[5])
        cards = int(run_row[7])
        if cards <= 0 or batch <= 0 or eff <= 0.508 or eff >= 0.996 or 2 * batch < cards:
            return
        bc = (2.0 * eff - 1.0) * batch
        denom = (1.0 + trained_d / 50.0) ** 0.4
        est = bc / max(1e-6, denom)
        if 175.0 <= est <= 665.0:
            if self.bc0_samples == 0:
                self.bc0_est = est
            else:
                alpha = 0.35 if self.bc0_samples < 5 else 0.15
                self.bc0_est = (1.0 - alpha) * self.bc0_est + alpha * est
            self.bc0_samples += 1

    def record_production_loss(self, n_eff: float, D: float, S_map: dict[str, float], loss_vec: list[float], bonus: float) -> None:
        if D < 25.0 or not loss_vec or len(loss_vec) != 7:
            return
        mult = max(0.90, 1.0 - bonus)
        l_mean = (sum(loss_vec) / 7.0) / mult
        tot_s = sum(S_map.values())
        if tot_s <= 1e-6:
            return
        mx = {p: v / tot_s for p, v in S_map.items() if v > 0}
        self.obs_points.append((n_eff, D, mx, l_mean, 0.35))
        if len(self.obs_points) > 36:
            self.obs_points = self.obs_points[:10] + self.obs_points[-26:]

    def refit_with_online_data(self) -> None:
        if len(self.obs_points) >= 6:
            self._fit_points(self.obs_points)

    def step(self, act: dict) -> bool:
        if self.stage >= 3:
            return True
        for e in self.st.ev:
            if e.get("type") == "done":
                rname = e["run"]
                l_vec = e["loss"]
                loss_mean = sum(l_vec) / len(l_vec)
                if rname in self.lr_probes:
                    self.lr_probes[rname]["loss"] = loss_mean
                elif rname in self.sc_probes:
                    self.sc_probes[rname]["loss"] = loss_mean
            elif e.get("type") in ("spike", "diverged"):
                rname = e.get("run")
                if rname in self.lr_probes:
                    self.lr_probes[rname]["spiked"] = True

        c0 = float(self.st.rules.get("DEFAULT_LR_C", 0.0010))
        if self.stage == 0:
            # 10 parallel low-LR probes (k <= 1.18 < 1.20 => ZERO spikes, ~1.5% eta_c accuracy in ~4 ticks!)
            ks = (0.06, 0.09, 0.14, 0.21, 0.32, 0.48, 0.68, 0.88, 1.04, 1.18)
            n_p, d_p = 0.12, 3.0
            mx = self.mixer.optimize_mix(d_p, for_probe=True)
            runs = []
            for idx, k in enumerate(ks):
                name = f"h_lr{idx}"
                self.lr_probes[name] = {"k": k, "loss": None, "failed": False, "spiked": False}
                runs.append({
                    "name": name, "params_b": n_p, "tokens_b": d_p,
                    "lr": c0 * k * (n_p ** -0.3), "batch": 26, "gpus": {"G1": 50},
                    "sched": "const", "ckpt_every": 24, "mix": mx,
                })
            act.setdefault("run", []).extend(runs)
            self.stage = 1
            self.stage_start = self.st.t
            return False

        if self.stage == 1:
            self._prune_stuck(act, self.lr_probes, 200)
            if all(v["loss"] is not None or v["failed"] for v in self.lr_probes.values()):
                valid = [v for v in self.lr_probes.values() if v["loss"] is not None and not v["spiked"]]
                if len(valid) >= 3:
                    xs = [math.log(v["k"]) for v in valid]
                    ys = [v["loss"] - 0.08 * (math.log(v["k"]) ** 2) for v in valid]
                    ws = [1.0 / max(1.0, v["loss"] ** 2) for v in valid]
                    sw = sum(ws)
                    mx_v = sum(w * x for w, x in zip(ws, xs)) / sw
                    my_v = sum(w * y for w, y in zip(ws, ys)) / sw
                    sxx = sum(w * (x - mx_v) ** 2 for w, x in zip(ws, xs))
                    sxy = sum(w * (x - mx_v) * (y - my_v) for w, x, y in zip(ws, xs, ys))
                    if sxx > 1e-12:
                        k_star = max(1.18, min(2.82, math.exp(-(sxy / sxx) / 0.16)))
                        self.eta_c = c0 * k_star
                if self.st.ticks < 500:
                    self.stage = 3
                    return True
                # 10-point wide-range (N, D) grid: 600x in N, 160x in D, finishes in ~45-65 ticks
                grid = (
                    (0.04, 1.0), (0.04, 18.0), (0.04, 160.0),
                    (0.25, 1.0), (0.25, 36.0),
                    (1.50, 1.0), (1.50, 24.0),
                    (6.00, 1.0), (6.00, 7.0),
                    (24.0, 1.0),
                )
                tot_nd = sum(n_ * d_ for n_, d_ in grid)
                usable_g1 = max(256, min(sum(self.st.total_usable().values()), 960))
                runs = []
                for idx, (n_, d_) in enumerate(grid):
                    name = f"h_sc{idx}"
                    cards = max(12, int(usable_g1 * 0.94 * (n_ * d_) / tot_nd))
                    mx = self.mixer.optimize_mix(d_, for_probe=True)
                    self.sc_probes[name] = {"N": n_, "D": d_, "mix": mx, "loss": None, "failed": False}
                    runs.append({
                        "name": name, "params_b": n_, "tokens_b": d_,
                        "lr": 0.965 * self.eta_star(n_), "batch": max(6, (cards + 1) // 2),
                        "gpus": {"G1": cards}, "sched": "const", "ckpt_every": 12,
                        "mix": mx,
                    })
                act.setdefault("run", []).extend(runs)
                self.stage = 2
                self.stage_start = self.st.t
            return False

        if self.stage == 2:
            self._prune_stuck(act, self.sc_probes, 600)
            if all(v["loss"] is not None or v["failed"] for v in self.sc_probes.values()):
                valid = [v for v in self.sc_probes.values() if v["loss"] is not None]
                if len(valid) >= 4:
                    self.obs_points = [
                        (v["N"] * self.st.algo_mult(), v["D"], v["mix"], v["loss"], 1.0)
                        for v in valid
                    ]
                    self._fit_points(self.obs_points)
                self.stage = 3
                return True
            return False
        return True

    def _prune_stuck(self, act: dict, probes: dict, timeout: int) -> None:
        for name, info in probes.items():
            if info["loss"] is not None or info["failed"]:
                continue
            r = self.st.runs_obs.get(name)
            if (r is not None and r[1] == "diverged" and r[6] == 0) or (self.st.t - self.stage_start > timeout):
                info["failed"] = True
                if r is not None and r[1] not in ("done", "stopped"):
                    act.setdefault("run_ctl", []).append({"name": name, "op": "stop"})

    def _fit_points(self, pts: list[tuple[float, float, dict[str, float], float, float]]) -> None:
        # 13x11 grid (step 0.01) => fast (~4ms) and accurate
        betas = [0.25 + 0.01 * j for j in range(11)]
        de_by_b: list[list[float]] = []
        for b in betas:
            de_by_b.append([self.mixer.effective_data_scalar(d, mx, b) ** (-b) for _, d, mx, _, _ in pts])

        best = None
        for i in range(13):
            a = 0.28 + 0.01 * i
            n_col = [n_eff ** (-a) for n_eff, _, _, _, _ in pts]
            for j, b in enumerate(betas):
                d_col = de_by_b[j]
                rows = [
                    (n_col[k], d_col[k], pts[k][3], pts[k][4] / max(1.0, pts[k][3] ** 2))
                    for k in range(len(pts))
                ]
                sol = _solve_3x3(rows)
                if sol is None or not (0.15 <= sol[1] <= 0.90) or not (0.45 <= sol[2] <= 2.20):
                    continue
                if not (1.10 <= sol[0] <= 2.50):
                    continue
                if best is None or sol[3] < best[0]:
                    best = (sol[3], a, b, sol[0], sol[1], sol[2])
        if best is not None:
            _, a, b, e0, A, B = best
            self.fit = {"alpha": a, "beta": b, "E": e0, "A": A, "B": B}


class ClusterAndComputePlanner:
    """Infrastructure scaling, aggressive PPAs, GPU auction hegemony, surplus cloud bursts, & hiring."""

    def __init__(self, st: WorldTracker):
        self.st = st
        self.last_data_bid_t = -10**9

    def payroll_per_tick(self) -> float:
        return sum(s["sal"] for s in self.st.staff)

    def spendable(self, extra_buf: float = 0.0) -> float:
        sal = self.payroll_per_tick()
        base_floor = 26.0 if self.st.t < 4500 else 36.0
        reserve = base_floor + sal * 24.0 * 22.0 + extra_buf
        return self.st.cash - reserve

    def weekly_actions(self, act: dict, tech_done: bool) -> None:
        if not self.st.wk:
            return
        self._emergency_raise_only(act)
        self._manage_sites(act)
        self._bid_gpus(act)
        self._manage_staff(act, tech_done)
        self._bid_data(act)

    def _emergency_raise_only(self, act: dict) -> None:
        # True Scaling Hegemon: ZERO equity dilution! Only real bankruptcy emergency (15+ days negative cash).
        if self.st.neg_cash_ticks >= 360 and self.st.t < self.st.ticks - 2500 and self.st.equity > 0.85:
            act.setdefault("raise", []).append({"amount": 40.0})

    def daily_ppa(self, act: dict) -> None:
        t = self.st.t
        if t < 480 or t > self.st.ticks - 1200:
            return
        pue = self.st.pue()
        year_ticks = float(self.st.rules.get("YEAR", 8760))
        for site in self.st.sites:
            sid = int(site["id"])
            if int(site.get("ready", 0)) > t:
                continue
            tranches = [tr for tr in self.st.site_ppa_tranches.get(sid, []) if tr[0] > t]
            self.st.site_ppa_tranches[sid] = tranches
            if len(tranches) >= 48:
                continue
            covered_mw = sum(mw for _, mw in tranches)
            cards = site.get("cards") or {}
            inst_mw = sum(n * self.st.gpu_meta[m]["kw"] * pue / 1000.0 for m, n in cards.items())
            target_cover = inst_mw * (0.50 if t >= self.st.ticks - 3000 else 0.86)
            gap_mw = target_cover - covered_mw
            if gap_mw < 0.8:
                continue
            r_id = int(site["region"])
            reg = self.st.regions[r_id]
            base_trend = reg["price"] * (1.0 + 0.12 * t / year_ticks)
            cur_elec = self.st.elec_prices[r_id] if r_id < len(self.st.elec_prices) else base_trend
            if cur_elec > base_trend * (1.03 if gap_mw > 3.0 else 0.97):
                continue
            mwh = round(gap_mw * year_ticks, 1)
            if mwh > 100.0:
                act.setdefault("ppa", []).append({"site": sid, "mwh": mwh})
                tranches.append((t + int(year_ticks), gap_mw))

    def _manage_sites(self, act: dict) -> None:
        t = self.st.t
        if t > self.st.ticks - 2600:
            return
        s0 = self.st.sites[0]
        r0 = self.st.regions[int(s0["region"])]
        cur_mw0 = float(s0["mw"]) + self.st.local_pend_mw.get(0, 0.0)
        rem_mw0 = float(r0["cap_mw"]) - cur_mw0
        need_kw = self.st.kw_committed()
        cap_kw = self.st.mw_capacity() * 1000.0

        # Just-in-time Site 0 expansion (preserves Year 0-1 cash while staying ahead of deliveries!)
        horizon_kw = need_kw * 1.45 + (3500.0 if t < 5000 else 7500.0)
        if rem_mw0 > 0.4 and cap_kw < horizon_kw:
            exp_cost = float(self.st.rules.get("EXPAND_COST", 2.0))
            want_mw = min(rem_mw0, max(3.0, (horizon_kw - cap_kw) / 1000.0 + 2.5))
            max_afford = max(0.0, (self.st.cash - 40.0) / exp_cost)
            add_mw = min(want_mw, max_afford)
            if add_mw >= 1.5:
                act.setdefault("expand_site", []).append({"site": 0, "mw": round(add_mw, 2)})
                self.st.local_pend_mw[0] = self.st.local_pend_mw.get(0, 0.0) + add_mw
                return

        # Proactively build new datacenters ahead of G3/G4/G5 waves
        if self.st.site_build_count + len(self.st.pending_builds) >= 7:
            return
        if t - self.st.last_site_build_t < 5 * 168:
            return
        if rem_mw0 > 2.5 and cap_kw > need_kw * 1.25:
            return
        if need_kw > 0.62 * cap_kw or (t >= 3500 and cap_kw - need_kw < 6000.0 and rem_mw0 < 2.0):
            used_regions = {int(s["region"]) for s in self.st.sites}

            def reg_score(reg: dict) -> float:
                dup_pen = 0.88 if reg["id"] in used_regions else 1.0
                mw_per_dollar = reg["cap_mw"] / (reg["build_cost"] + 8.0)
                return dup_pen * (mw_per_dollar ** 0.85) * (reg["cap_mw"] ** 0.25) / (
                    (0.55 + reg["price"] * 900.0) * (0.75 + 0.35 * reg["reg"]) * (1.0 + reg["build_weeks"] / 40.0)
                )

            best_reg = max(self.st.regions, key=reg_score)
            if self.spendable() > best_reg["build_cost"] + 25.0 and self.st.cash > best_reg["build_cost"] + 45.0:
                act.setdefault("build_site", []).append({"region": int(best_reg["id"])})
                self.st.last_site_build_t = t
                self.st.pending_builds.append((t + int(best_reg["build_weeks"]) * 168, float(best_reg["cap_mw"])))

    def _bid_gpus(self, act: dict) -> None:
        t = self.st.t
        # Strict delivery safety: settle at t+168, max delay 10 weeks (1680) => t <= T - 1900
        if t > self.st.ticks - 1900:
            self.st.pending_gpu_bid_cost = 0.0
            return
        offer = self.st.wk.get("offer") or {}
        # Project cash available at NEXT Monday settlement (t + 168): include conservative 65% of weekly net rev!
        sal_wk = self.payroll_per_tick() * 168.0
        rent_wk = 0.0
        for l in self.st.leases.values():
            rem_l = min(168, max(0, l["end"] - t))
            rent_wk += self.st.rent_prices.get(l["m"], 0.0) * l["n"] * rem_l
        net_wk_inflow = max(0.0, self.st.last_week_rev * 0.72 - sal_wk - rent_wk)
        cash_avail = self.st.cash + net_wk_inflow - (18.0 if t < 4500 else 22.0)
        if cash_avail <= 12.0:
            self.st.pending_gpu_bid_cost = 0.0
            return
        pue = self.st.pue()
        room_kw = self.st.mw_capacity() * 1000.0 - self.st.kw_committed()
        forward_kw = max(0.0, room_kw + (8000.0 if t < self.st.ticks - 5000 else 2500.0))

        # Warchest guard right before early_supply G4 (Q5: t=10950) & early_supply G5 (Q9: t=19710)
        saving_for_early_g4 = ("early_supply" in self.st.tech) and (10100 <= t < 13140)
        saving_for_early_g5 = ("early_supply" in self.st.tech) and (18900 <= t < 21900)

        cands = []
        for m, o in offer.items():
            lots = int(o.get("lots", 0))
            if lots <= 0:
                continue
            early = bool(o.get("early", False))
            if early and "early_supply" not in self.st.tech:
                continue
            meta = self.st.gpu_meta[m]
            gen = GPU_ORDER.index(m)
            if m == "G1":
                continue
            if m == "G2" and t >= 6400:
                continue
            # Stop buying G3 once early G4 warchest starts (unless flush with cash!)
            if m == "G3" and (t >= 13200 or (saving_for_early_g4 and cash_avail < 130.0)):
                continue
            # Preserve warchest for uncontested early G5 in Q9 (only buy G4 in Q9 if cash > 230M!)
            if m == "G4" and saving_for_early_g5 and cash_avail < 230.0:
                continue
            reserve = float(o["reserve"])
            base_h = meta["price"] * self.st.heat
            clr = self.st.clear_ratio.get(m, 1.20)
            if early:
                # Zero NPCs in early supply! Sweep cheaply at ~1.05*reserve if uncontested, defend cleanly if contested
                if self.st.opp_early_won.get(m, 0) == 0:
                    bid_p = max(reserve * 1.03, base_h * 0.90)
                else:
                    bid_p = max(reserve * 1.03, base_h * max(1.25, min(1.65, clr * 1.15)))
            else:
                if m == "G2":
                    base_mult = 1.30
                elif m == "G3":
                    base_mult = 1.46
                    if self.st.export_ctrl_q >= 0 and (t // 2190) in (self.st.export_ctrl_q - 1, self.st.export_ctrl_q):
                        base_mult = 1.60
                elif m == "G4":
                    base_mult = 1.50
                else:  # G5
                    base_mult = 1.56
                mult = max(base_mult, min(1.82, clr * 1.16))
                bid_p = max(reserve * 1.03, base_h * mult)

            max_1lot_p = cash_avail / 256.0
            if bid_p > max_1lot_p:
                if max_1lot_p >= reserve * 1.01:
                    bid_p = max_1lot_p
                else:
                    continue
            score = (meta["power"] / bid_p) * (1.0 + 0.48 * gen) * (1.55 if early else 1.0)
            cands.append((score, m, bid_p, reserve, lots, meta["kw"], early))

        if not cands:
            self.st.pending_gpu_bid_cost = 0.0
            return
        cands.sort(reverse=True)
        budget = cash_avail * (0.94 if t >= 6570 else 0.85)
        bids = []
        committed_cost = 0.0
        for _, m, bid_p, reserve, lots, kw_card, early in cands[:2]:
            kw_lot = 256.0 * kw_card * pue
            if kw_lot <= 0:
                continue
            k_power = int(forward_kw // kw_lot)
            if k_power <= 0:
                continue
            eff_p = bid_p
            if eff_p * 256.0 > budget:
                if budget / 256.0 >= reserve * 1.01:
                    eff_p = budget / 256.0
                else:
                    continue
            lot_cost = eff_p * 256.0
            k_budget = int(budget // lot_cost)
            lot_cap = 3 if (m == "G2" and not early and t < 4400) else 64
            k = min(lots, k_budget, k_power, lot_cap)
            if k <= 0:
                continue
            bids.append({"model": m, "lots": k, "price": round(eff_p, 5)})
            committed_cost += k * lot_cost
            budget -= k * lot_cost
            forward_kw -= k * kw_lot
        self.st.pending_gpu_bid_cost = committed_cost
        if bids:
            act["bid_gpu"] = bids

    def _manage_staff(self, act: dict, tech_done: bool) -> None:
        if tech_done:
            # Fire immediately once core tech roadmap finishes (1-week severance pays back in 7 days!)
            if self.st.staff and self.st.cash > -10.0:
                act["fire"] = [s["id"] for s in self.st.staff]
            return
        cur_pts = sum(s["ab"] for s in self.st.staff)
        if cur_pts >= 275 or len(self.st.staff) >= 82 or self.st.cash < 42.0:
            return
        talent = self.st.wk.get("talent") or []
        scored = []
        for c in talent:
            e_ab = _expected_ability(int(c["lo"]), int(c["hi"]), float(c["ask"]))
            if e_ab < 2.6:
                continue
            scored.append((e_ab / max(1e-9, float(c["ask"])), e_ab, c))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        hires = []
        max_add = min(10, 82 - len(self.st.staff))
        for _, e_ab, c in scored:
            if len(hires) >= max_add:
                break
            mult = 1.31 if e_ab >= 4.15 else (1.16 if e_ab >= 3.4 else 1.06)
            hires.append({"cand": int(c["id"]), "salary": round(float(c["ask"]) * mult, 7)})
        if hires:
            act["hire"] = hires

    def _bid_data(self, act: dict) -> None:
        t = self.st.t
        if t < 2400 or t > 20500 or t - self.last_data_bid_t < 168:
            return
        if self.st.data_spent_total >= 42.0:
            return
        sp = self.spendable(extra_buf=self.st.pending_gpu_bid_cost + 35.0)
        if sp < 55.0:
            return
        deals = self.st.wk.get("deals") or []
        best = None
        for d in deals:
            dom = d["domain"]
            if dom not in ("math", "science", "dialog", "books", "code"):
                continue
            q_hint = float(d["q_hint"])
            tok = float(d["tokens_b"])
            if q_hint < 1.12:
                continue
            cur_u = max(1.0, self.st.U.get(dom, 100.0))
            rel_gain = tok / cur_u
            if rel_gain < 0.55:
                continue
            res = float(d["reserve"])
            mult = min(1.90, 1.45 + 0.35 * max(0.0, q_hint - 1.0))
            pr = round(res * mult, 3)
            if pr > 18.0 or sp < pr + 45.0:
                continue
            val = rel_gain * q_hint * TRANSFER_LEVERAGE.get(dom, 1.3) / max(1.0, pr)
            if best is None or val > best[0]:
                best = (val, int(d["id"]), pr, d)
        if best is not None:
            _, did, pr, d = best
            act["bid_data"] = [{"deal": did, "price": pr}]
            self.st.pending_deal_meta[did] = dict(d)
            self.last_data_bid_t = t

    def maybe_rent_training_compute(
        self,
        act: dict,
        train_end: int,
        cur_d: float,
        bc0_est: float,
        own_train_cards: dict[str, int],
        rented_cards: dict[str, int],
        serve_models_used: set[str],
        allow_late_rent: bool = False,
    ) -> None:
        """Disciplined surplus-cash cloud rental: accelerates training WITHOUT ever starving GPU auctions!"""
        t = self.st.t
        if t % 24 != 23 or not self.st.rent_prices:
            return
        q11_start = self.st.ticks - int(self.st.rules.get("GM_WINDOW", 2190))
        hard_stop = (train_end - 36) if allow_late_rent else min(train_end, q11_start - 48)
        rem = hard_stop - (t + 1)
        if rem < 24:
            return

        # STRICT AUCTION PROTECTION: owned GPU auctions ALWAYS come first (true Scaling Hegemon!)
        free_cash = self.st.cash - self.st.pending_gpu_bid_cost

        # Preserve warchest during early_supply G4 (Q5) and early_supply G5 (Q9) windows!
        if 10200 <= t < 13140 and free_cash < 115.0:
            return
        if 19000 <= t < 21900 and free_cash < 200.0:
            return

        if t < 1460:
            cash_floor = 55.0
            max_weekly_rent_spend = 16.0
        elif t < 6570:
            cash_floor = 70.0
            max_weekly_rent_spend = min(28.0, max(12.0, self.st.last_week_rev * 0.25))
        elif t < 12800:
            cash_floor = 85.0
            max_weekly_rent_spend = min(46.0, max(18.0, self.st.last_week_rev * 0.30))
        else:
            cash_floor = 100.0
            surplus_bonus = max(0.0, (free_cash - 260.0) * 0.60)
            rev_cap = max(32.0, self.st.last_week_rev * (0.44 if t >= 20500 else 0.32)) + surplus_bonus
            max_weekly_rent_spend = min(260.0 if free_cash > 300.0 else (90.0 if t >= 21500 else 65.0), rev_cap)

        if free_cash < cash_floor + 8.0:
            return

        cur_burn_per_tick = 0.0
        for l in self.st.leases.values():
            pr = self.st.rent_prices.get(l["m"], 0.0)
            cur_burn_per_tick += pr * l["n"] * (0.4 if l["kind"] == "spot" else 1.0)
        if cur_burn_per_tick * 168.0 >= max_weekly_rent_spend * 0.88:
            return

        exec_q = (t + 1) // int(self.st.rules.get("QUARTER", 2190))
        mtbf = float(self.st.rules.get("MTBF", 50000.0))
        bc = bc0_est * ((1.0 + max(0.0, cur_d) / 50.0) ** 0.4)

        cur_cards_map = dict(own_train_cards)
        for m, c in rented_cards.items():
            cur_cards_map[m] = cur_cards_map.get(m, 0) + c

        def net_eff_compute(cmap: dict[str, int]) -> float:
            n_tot = sum(cmap.values())
            if n_tot <= 0:
                return 0.0
            raw = self.st.compute_of(cmap)
            b = (n_tot + 1) // 2
            waste = (min(b, bc) + 0.5 * max(0.0, b - bc)) / b
            _, rel = _best_ckpt_and_rel(n_tot, mtbf)
            return raw * waste * rel

        base_eff = net_eff_compute(cur_cards_map)

        best_choice = None
        for m in reversed(GPU_ORDER):
            if m not in self.st.rent_prices:
                continue
            meta = self.st.gpu_meta[m]
            if exec_q < meta["launch_q"]:
                continue
            if m in serve_models_used and t > 18000:
                continue
            pr = float(self.st.rent_prices[m])
            u = (pr * 8000.0 / meta["price"] - 1.0) / 2.0
            if u >= 0.86:
                continue
            pool = meta["supply_q"] * 2.0
            safe_room = int(pool * (0.88 - u))
            if safe_room < 64:
                continue
            rem_weekly_budget = max(0.0, max_weekly_rent_spend - cur_burn_per_tick * 168.0)
            lease_ticks = min(168, max(24, rem))
            cash_room_n = int(max(0.0, (free_cash - cash_floor) * 0.60) / max(1e-9, pr * lease_ticks))
            wk_room_n = int(rem_weekly_budget / max(1e-9, pr * 168.0))
            max_add = min(safe_room, cash_room_n, wk_room_n, 1536)
            if max_add < 64:
                continue
            trial_n = 0
            best_gain_per_m = 0.0
            # Physical net_eff/cost_per_tick ranges: G1 ~42k..65k, G2 ~52k..75k, G3 ~62k..95k, G4 ~75k..115k, G5 ~95k..140k
            if free_cash > 300.0 and t >= 12800:
                min_gpm = 28000.0
            else:
                min_gpm = 26000.0 if t < 1500 else (36000.0 if t < 6570 else (48000.0 if t < 12800 else 54000.0))
            for cand_n in (64, 128, 256, 384, 512, 768, 1024, 1536):
                add_n = min(cand_n, max_add)
                if add_n < 64:
                    continue
                tmap = dict(cur_cards_map)
                tmap[m] = tmap.get(m, 0) + add_n
                gain = net_eff_compute(tmap) - base_eff
                cost_per_tick = add_n * pr
                if gain <= 0 or cost_per_tick <= 0:
                    continue
                g_per_m = gain / cost_per_tick
                if g_per_m >= min_gpm:
                    trial_n = add_n
                    best_gain_per_m = g_per_m
                if add_n == max_add:
                    break
            if trial_n >= 64:
                score = best_gain_per_m * (1.0 + 0.14 * GPU_ORDER.index(m))
                if best_choice is None or score > best_choice[0]:
                    best_choice = (score, m, trial_n, lease_ticks)

        if best_choice is not None:
            _, m, n_add, lease_ticks = best_choice
            act.setdefault("rent", []).append({"model": m, "n": n_add, "kind": "ondemand", "ticks": lease_ticks})
            self.st.pending_rent_queue.append((m, lease_ticks, "ondemand"))


class BellmanFrontierPricer:
    """Steady-state equilibrium pricer mid-game + exact O(W*S*P) Bellman DP for 25x-weighted Q11."""

    # 29-point exact 1.482^(1/3)-aligned log price grid (2.49..99.5): 3 steps == EXACT 1.482x shock limit!
    P_GRID = tuple(round(99.5 / (1.482 ** ((28 - i) / 3.0)), 4) for i in range(29))
    P_MAX_NEXT = tuple(min(28, i + 3) for i in range(29))
    S_GRID = tuple(i / 16.0 for i in range(17))

    def __init__(self, st: WorldTracker):
        self.st = st

    def _other_market_parts(self) -> tuple[float, float, float, float, float]:
        """Return (max_other_u, sum_exp_shifted, opp_pub, opp_p, non_opp_exp_at_mx)."""
        other_best: dict[str, tuple[float, float, float]] = {}
        for m in self.st.models.values():
            owner = m["owner"]
            if owner == self.st.me:
                continue
            pub_m = float(m["pub"])
            pr_m = float(m["price"])
            u = self.st.model_u(pub_m, pr_m)
            if owner not in other_best or u > other_best[owner][0]:
                other_best[owner] = (u, pub_m, pr_m)
        if not other_best:
            return -1e9, 0.0, 0.0, 0.0, 0.0
        mx = max(v[0] for v in other_best.values())
        s_exp = 0.0
        non_opp_exp = 0.0
        for owner, v in other_best.items():
            ev = math.exp(v[0] - mx)
            s_exp += ev
            if owner != self.st.opp:
                non_opp_exp += ev
        opp_info = other_best.get(self.st.opp)
        if opp_info is not None and opp_info[1] >= 0.45 and opp_info[2] >= 3.0 and s_exp > 0.0:
            opp_pub = opp_info[1]
            opp_p = opp_info[2]
        else:
            opp_pub = 0.0
            opp_p = 0.0
            non_opp_exp = s_exp
        return mx, s_exp, opp_pub, opp_p, non_opp_exp

    def _other_exp_sum(self) -> tuple[float, float, float, float]:
        """Return (max_other_u, sum_exp_shifted, opp_share_of_others, opp_price)."""
        mx, s_exp, opp_pub, opp_p, non_opp_exp = self._other_market_parts()
        if opp_pub > 0.0 and s_exp > 0.0:
            opp_frac = max(0.0, (s_exp - non_opp_exp) / s_exp)
        else:
            opp_frac = 0.0
        return mx, s_exp, opp_frac, opp_p

    def est_share(self, pub: float, price: float, drop: float = 0.0, other_cache: tuple[float, float, float, float] | None = None) -> float:
        mx_o, s_o, _, _ = other_cache if other_cache is not None else self._other_exp_sum()
        u_me = self.st.model_u(pub, price) + self.st.rep - float(self.st.rules.get("U_DROP", 5.0)) * drop
        if s_o <= 0.0:
            return 1.0
        if u_me >= mx_o:
            return 1.0 / (1.0 + s_o * math.exp(mx_o - u_me))
        e_me = math.exp(u_me - mx_o)
        return e_me / (e_me + s_o)

    def marginal_cost_per_m(self, n_b: float, serve_has_rent: bool) -> float:
        per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
        pue = self.st.pue()
        e0 = self.st.elec_prices[0] if self.st.elec_prices else float(self.st.regions[0]["price"])
        mc = per * 0.35 * pue * e0 * 0.7
        if serve_has_rent and self.st.rent_prices:
            rent_unit = min(pr / self.st.gpu_meta[m]["power"] for m, pr in self.st.rent_prices.items())
            mc += 0.4 * per * rent_unit * 1000.0
        return max(0.20, mc)

    def _safe_price_cap(self, pub: float, cur_p: float, allow_jump: bool = False) -> float:
        p_cap = 99.5
        if self.st.anchor_u is not None and not allow_jump:
            u_pub = float(self.st.rules.get("U_PUB", 6.0))
            u_pr = float(self.st.rules.get("U_PRICE", 1.5))
            min_u = self.st.anchor_u - u_pr * math.log(1.485)
            ln_p_max = (u_pub * pub - min_u) / u_pr
            p_cap = min(p_cap, math.exp(min(4.605, ln_p_max)))
        elif not allow_jump:
            p_cap = min(p_cap, cur_p * 1.485)
        return max(1.0, min(99.5, p_cap))

    def steady_state_price(self, pub: float, n_b: float, serve_compute: float, serve_has_rent: bool, cur_p: float, allow_jump: bool = False) -> float:
        """Pure cashflow-maximizing steady-state equilibrium pricer for Year 1-2 capital accumulation."""
        per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
        q_dem = max(0.2, self.st.demand * 1.12)
        s_cap = min(1.0, (serve_compute / max(1e-6, per)) / q_dem)
        mc = self.marginal_cost_per_m(n_b, serve_has_rent)
        p_floor = max(4.5, mc * 2.0)
        p_cap = self._safe_price_cap(pub, cur_p, allow_jump=allow_jump)
        if p_cap <= p_floor:
            return round(p_cap, 3)

        other_cache = self._other_exp_sum()
        best_v, best_p = -1e18, min(p_cap, max(p_floor, 10.0))
        ratio = (p_cap / p_floor) ** (1.0 / 54.0)
        p = p_floor
        for _ in range(55):
            s = self.est_share(pub, p, 0.0, other_cache)
            served = min(s, s_cap)
            drop_pen = max(0.0, s - s_cap) * p * 6.0
            v = served * max(0.0, p - mc) - drop_pen
            if v > best_v:
                best_v, best_p = v, p
            p *= ratio
        return round(max(p_floor, min(p_cap, best_p)), 3)

    def solve_optimal_price(
        self,
        fl: dict,
        serve_compute: float,
        serve_has_rent: bool = False,
        for_next_week: bool = False,
        allow_jump: bool = False,
    ) -> float:
        pub = float(fl["pub"])
        cur_p = float(fl["price"])
        n_b = float(fl.get("N") or 20.0)

        if self.st.drop > 0.004 and not for_next_week:
            p_cap = self._safe_price_cap(pub, cur_p, allow_jump=allow_jump)
            return round(min(p_cap, max(4.0, cur_p * 1.38)), 3)

        t = self.st.t
        T = self.st.ticks
        gm_win = int(self.st.rules.get("GM_WINDOW", 2190))
        q11_start = T - gm_win

        if t < q11_start - 1150:
            return self.steady_state_price(pub, n_b, serve_compute, serve_has_rent, cur_p, allow_jump=(allow_jump or for_next_week))

        # Pre-Q11 & Q11 window: exact 2D Bellman DP maximizing V_me - alpha_adv * V_opp!
        per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
        q_dem_now = max(0.2, self.st.demand * 1.08)
        s_cap = min(1.0, (serve_compute / max(1e-6, per)) / q_dem_now)
        mc = self.marginal_cost_per_m(n_b, serve_has_rent)
        end_gm_mult = float(self.st.rules.get("END_GM_MULT", 24.0))

        cur_wk = (t + (2 if for_next_week else 0)) // 168
        last_wk = (T - 1) // 168
        wks = list(range(cur_wk, last_wk + 1))
        if not wks:
            return round(min(99.5, max(2.5, cur_p)), 3)

        wk_weights = []
        wk_scaps = []
        g_now = max(1e-6, _demand_growth(t))
        for wk in wks:
            w_s = wk * 168
            w_e = min(T, (wk + 1) * 168)
            tot_t = max(0, w_e - w_s)
            q11_t = max(0, w_e - max(w_s, q11_start))
            pre_t = max(0, tot_t - q11_t)
            g_rel = _demand_growth(w_s + tot_t // 2) / g_now
            wk_weights.append(g_rel * (pre_t * 1.0 + q11_t * (1.0 + end_gm_mult)) / 168.0)
            # Account for owned GPUs already in transit arriving before this week!
            add_comp = 0.0
            for d in self.st.deliv:
                if int(d[0]) <= w_s:
                    add_comp += self.st.gpu_meta[d[1]]["power"] * int(d[2])
            sc_w = min(1.0, ((serve_compute + add_comp) / max(1e-6, per)) / max(0.2, q_dem_now * g_rel))
            wk_scaps.append(sc_w)

        P = self.P_GRID
        S = self.S_GRID
        nP = len(P)
        nS = len(S)
        s_scale = float(nS - 1)
        mx_o, s_o, opp_pub, opp_p, non_opp_exp = self._other_market_parts()
        other_cache = (mx_o, s_o, max(0.0, (s_o - non_opp_exp) / s_o) if (opp_pub > 0.0 and s_o > 0.0) else 0.0, opp_p)
        has_rival = opp_pub >= 0.45 and opp_p >= 3.0

        # Stackelberg competitive response: an adaptive frontier rival partially matches price cuts below
        # its current price, avoiding self-defeating Bertrand wars while exact 1.00 weight on (V_me - V_opp)
        # maximizes adversarial lead! Vanishes cleanly to pure monopoly DP in Solo (has_rival=False).
        s_tgts = []
        opp_flow_by_p = []
        for pn in P:
            if has_rival:
                opp_p_eff = max(2.5, opp_p * ((pn / opp_p) ** 0.35)) if pn < opp_p else opp_p
                u_opp_eff = self.st.model_u(opp_pub, opp_p_eff)
                e_opp = math.exp(u_opp_eff - mx_o)
                s_o_eff = non_opp_exp + e_opp
                u_me = self.st.model_u(pub, pn) + self.st.rep
                if s_o_eff <= 0.0:
                    st_p = 1.0
                elif u_me >= mx_o:
                    st_p = 1.0 / (1.0 + s_o_eff * math.exp(mx_o - u_me))
                else:
                    e_me = math.exp(u_me - mx_o)
                    st_p = e_me / (e_me + s_o_eff)
                s_tgts.append(st_p)
                opp_frac_eff = e_opp / s_o_eff if s_o_eff > 0.0 else 0.0
                opp_flow_by_p.append(1.00 * opp_frac_eff * max(0.0, opp_p_eff - 1.0))
            else:
                s_tgts.append(self.est_share(pub, pn, 0.0, other_cache))
                opp_flow_by_p.append(0.0)

        max_next = self.P_MAX_NEXT

        V_next = [[0.0] * nP for _ in range(nS)]
        for step_idx in range(len(wks) - 1, 0, -1):
            wt = wk_weights[step_idx]
            sc_w = wk_scaps[step_idx]
            V_cur = [[0.0] * nP for _ in range(nS)]
            for si in range(nS):
                s_val = S[si]
                served_s = s_val if s_val < sc_w else sc_w
                over_s = (s_val - sc_w) if s_val > sc_w else 0.0
                one_minus_s = 1.0 - s_val
                pref = [0.0] * nP
                run_max = -1e18
                for pj in range(nP):
                    pn = P[pj]
                    margin = pn - mc
                    if margin < 0.0:
                        margin = 0.0
                    rew = wt * (served_s * margin - one_minus_s * opp_flow_by_p[pj] - over_s * pn * 6.0)
                    ds = s_tgts[pj] - s_val
                    if ds > 0.15:
                        ds = 0.15
                    elif ds < -0.15:
                        ds = -0.15
                    s_nxt = s_val + ds
                    if s_nxt < 0.0:
                        s_nxt = 0.0
                    elif s_nxt > 1.0:
                        s_nxt = 1.0
                    pos = s_nxt * s_scale
                    i0 = int(pos)
                    if i0 >= nS - 1:
                        cont = V_next[nS - 1][pj]
                    else:
                        frac = pos - i0
                        cont = V_next[i0][pj] * (1.0 - frac) + V_next[i0 + 1][pj] * frac
                    av = rew + cont
                    if av > run_max:
                        run_max = av
                    pref[pj] = run_max
                row_cur = V_cur[si]
                for pi in range(nP):
                    row_cur[pi] = pref[max_next[pi]]
            V_next = V_cur

        wt0 = wk_weights[0]
        sc0 = wk_scaps[0]
        if for_next_week:
            st_cur = self.est_share(pub, cur_p, self.st.drop, other_cache)
            s0 = max(0.0, min(1.0, self.st.my_share + max(-0.15, min(0.15, st_cur - self.st.my_share))))
            p_cap = 99.5
        else:
            s0 = self.st.my_share
            p_cap = self._safe_price_cap(pub, cur_p, allow_jump=allow_jump)

        served0 = min(s0, sc0)
        over0 = max(0.0, s0 - sc0)
        one_minus_s0 = 1.0 - s0
        cand_ps = [p for p in P if p <= p_cap]
        if p_cap not in cand_ps:
            cand_ps.append(p_cap)
        if 2.0 <= cur_p <= p_cap:
            cand_ps.append(cur_p)

        best_val = -1e18
        best_p = min(p_cap, max(2.5, cur_p))
        p_min = P[0]
        inv_log_span = (nP - 1) / math.log(P[-1] / p_min)
        for pn in cand_ps:
            if has_rival:
                opp_p_eff = max(2.5, opp_p * ((pn / opp_p) ** 0.35)) if pn < opp_p else opp_p
                u_opp_eff = self.st.model_u(opp_pub, opp_p_eff)
                e_opp = math.exp(u_opp_eff - mx_o)
                s_o_eff = non_opp_exp + e_opp
                u_me = self.st.model_u(pub, pn) + self.st.rep
                if s_o_eff <= 0.0:
                    st = 1.0
                elif u_me >= mx_o:
                    st = 1.0 / (1.0 + s_o_eff * math.exp(mx_o - u_me))
                else:
                    e_me = math.exp(u_me - mx_o)
                    st = e_me / (e_me + s_o_eff)
                opp_frac_eff = e_opp / s_o_eff if s_o_eff > 0.0 else 0.0
                opp_loss0 = one_minus_s0 * 1.00 * opp_frac_eff * max(0.0, opp_p_eff - 1.0)
            else:
                st = self.est_share(pub, pn, 0.0, other_cache)
                opp_loss0 = 0.0
            rew = wt0 * (served0 * max(0.0, pn - mc) - opp_loss0 - over0 * pn * 6.0)
            s_nxt = max(0.0, min(1.0, s0 + max(-0.15, min(0.15, st - s0))))
            pos_s = s_nxt * s_scale
            i0 = int(pos_s)
            lp = math.log(max(p_min, min(P[-1], pn)) / p_min) * inv_log_span
            pj = max(0, min(nP - 1, int(round(lp))))
            if i0 >= nS - 1:
                cont = V_next[nS - 1][pj]
            else:
                frac = pos_s - i0
                cont = V_next[i0][pj] * (1.0 - frac) + V_next[i0 + 1][pj] * frac
            tot = rew + cont
            if tot > best_val:
                best_val = tot
                best_p = pn

        return round(max(2.0, min(p_cap, best_p)), 3)


class ScalingHegemon:
    """Top-level orchestrator for the Compute & Frontier Scaling Hegemon."""

    # Filter top domains FIRST so Gen 1 gets zero audit_fail; early_supply promoted before Q2 (t=4380)
    TECH_ROADMAP = (
        "filter:web", "filter:books", "filter:code", "algo_eff_1",
        "cooling", "infer_opt_1", "filter:multiling", "algo_eff_2",
        "synth", "curriculum", "early_supply", "algo_eff_3",
        "infer_opt_2", "filter:math", "filter:science", "filter:dialog",
    )

    def __init__(self, init: dict):
        self.st = WorldTracker(init)
        self.mixer = DataMixOptimizer(self.st)
        self.fitter = ScalingFitter(self.st, self.mixer)
        self.infra = ClusterAndComputePlanner(self.st)
        self.pricer = BellmanFrontierPricer(self.st)

        T = self.st.ticks
        if T >= 20000:
            self.gens = [
                {"end": 1460, "sched": "wsd", "milestones": []},
                {"end": 4750, "sched": "wsd", "milestones": []},
                {"end": 9600, "sched": "wsd", "milestones": []},
                {"end": 15800, "sched": "wsd", "milestones": []},
                {"end": T - 2880, "sched": "wsd", "milestones": []},
            ]
        else:
            self.gens = [{"end": max(200, T - 105), "sched": "wsd", "milestones": []}]

        self.gen_idx = 0
        self.main_run: str | None = None
        self.main_N: float = 10.0
        self.main_sched: str = "wsd"
        self.main_start: int = 0
        self.main_end: int = self.gens[0]["end"]
        self.main_batch: int = 256
        self.main_slow: bool = False
        self.main_tpt_ema: float = 0.0
        self.main_milestones: list[int] = list(self.gens[0]["milestones"])
        self.milestones_done: set[int] = set()
        self.wsd_locked: set[str] = set()
        self.released_runs: set[str] = set()
        self.cosine_resume_pending: tuple[str, float] | None = None

        self.next_run: tuple[str, float, str] | None = None
        self.prepped_gens: set[int] = set()
        self.refit_done_gens: set[int] = set()
        self.audit_sent: set[str] = set()
        self.run_seq = 0
        self.research_cur: tuple[str, ...] = ()
        self.last_mix_update_t: int = -10**9
        self.last_drop_trim_t: int = -10**9
        self.synth_waves_done: set[int] = set()
        self.best_released_loss: float = 99.0

    # ---- Scaling-law optimal (N, D) selection ----
    def choose_ND(self, budget_g1h: float, gen_idx: int) -> tuple[float, float]:
        kc = float(self.st.rules.get("KC", 300.0))
        if not self.fitter.fit:
            n = math.sqrt(max(1.0, budget_g1h) / kc / 20.0)
            return max(1.0, n), max(10.0, budget_g1h / kc / max(1.0, n))
        f = self.fitter.fit
        algo = self.st.algo_mult()
        beta = f["beta"]

        own_now = self.st.compute_of(self.st.own_usable())
        deliv_comp = sum(self.st.gpu_meta[d[1]]["power"] * int(d[2]) for d in self.st.deliv)
        fleet_est = own_now + deliv_comp

        if gen_idx == 0:
            # Gen 1: lean N so 512..1500 G1 serves 40% share with ZERO drops!
            n_cap = 11.0
        elif gen_idx == 1:
            # Gen 2: matched to Year-1 owned+delivered+cloud fleet
            n_cap = max(14.0, min(22.0, (fleet_est + 2000.0) / 170.0))
        elif gen_idx == 2:
            # Gen 3: matched to Year-2 G3 fleet
            n_cap = max(18.0, min(34.0, (fleet_est + 3000.0) / 260.0))
        elif gen_idx < len(self.gens) - 1:
            # Gen 4: Year-2.5 G4 flagship (lean enough to serve cheaply while Gen 5 Titan trains!)
            n_cap = max(26.0, min(46.0, (fleet_est + 6000.0) / 340.0))
        else:
            q11_dem = 5.0 * _demand_growth(25200) * 1.18 * self.st.shock_est
            est_q11_own = max(38000.0, fleet_est * 2.1)
            # Fit 62% of Q11 demand comfortably in 88% of owned fleet!
            n_serve_cap = (0.88 * est_q11_own) / max(1.0, 0.62 * q11_dem * 15.0)
            opp_pub_max = max((float(m["pub"]) for m in self.st.models.values() if m["owner"] == self.st.opp), default=0.0)
            upper_cap = 108.0 if opp_pub_max < 0.55 else 92.0
            n_cap = max(48.0, min(upper_cap, n_serve_cap))

        best = None
        n0 = math.sqrt(max(1.0, budget_g1h) / kc / 22.0)
        for i in range(-28, 36):
            n = n0 * math.exp(i * 0.06)
            if n < 0.8 or n > n_cap:
                continue
            d = budget_g1h / kc / n
            if d < 5.0:
                continue
            mx = self.mixer.optimize_mix(d, beta=beta, allow_synth=(gen_idx >= 1), iters=10)
            de_s = self.mixer.effective_data_scalar(d, mx, beta=beta)
            loss = f["E"] + f["A"] * ((n * algo) ** -f["alpha"]) + f["B"] * (max(de_s, 0.05) ** -beta)
            if best is None or loss < best[0]:
                best = (loss, n, d)
        if best is None:
            n_fb = max(1.0, min(n_cap, n0))
            return n_fb, max(10.0, budget_g1h / kc / n_fb)
        return best[1], best[2]

    # ---- Exact Card Allocation ----
    def serve_buffer_compute(self) -> float:
        fl = self.st.active_flagship()
        if not fl:
            return 0.0
        n_b = float(fl.get("N") or 20.0)
        per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
        s_est = max(self.st.my_share, self.pricer.est_share(float(fl["pub"]), float(fl["price"]))) + 0.04
        return max(self.st.api_dem, self.st.demand * min(1.0, s_est)) * 1.25 * per

    def allocate_training_cards(self, cur_d: float) -> tuple[dict[str, int], int, int, set[str]]:
        own = self.st.own_usable()
        rented = self.st.rented_usable()
        # ZERO-DROP RULE: Serving ALWAYS gets 100% priority over training (protects rep == 0.00!)
        need_serve = self.serve_buffer_compute()

        serve_reserved: dict[str, int] = {}
        got = 0.0
        for m in GPU_ORDER:
            if got >= need_serve or m not in own:
                continue
            pw = self.st.gpu_meta[m]["power"]
            k = min(own[m], int(math.ceil((need_serve - got) / pw)))
            if k > 0:
                serve_reserved[m] = k
                got += k * pw
        if got < need_serve:
            for m in GPU_ORDER:
                if got >= need_serve or m not in rented:
                    continue
                pw = self.st.gpu_meta[m]["power"]
                k = min(rented[m], int(math.ceil((need_serve - got) / pw)))
                if k > 0:
                    serve_reserved[m] = serve_reserved.get(m, 0) + k
                    got += k * pw

        serve_models_used = {m for m, k in serve_reserved.items() if k > 0}
        avail: dict[str, int] = {}
        for m in GPU_ORDER:
            tot_m = own.get(m, 0) + rented.get(m, 0) - serve_reserved.get(m, 0)
            if tot_m > 0:
                avail[m] = tot_m

        if not avail:
            return {}, 4, 24, serve_models_used

        mtbf = float(self.st.rules.get("MTBF", 50000.0))
        bc = self.fitter.bc0_est * ((1.0 + max(0.0, cur_d) / 50.0) ** 0.4)
        total_avail_n = sum(avail.values())

        cand_counts: set[int] = {total_avail_n}
        cum = 0
        for m in reversed(GPU_ORDER):
            c = avail.get(m, 0)
            if c > 0:
                cum += c
                cand_counts.add(cum)
        step = max(32, total_avail_n // 24)
        for cnt in range(step, total_avail_n, step):
            cand_counts.add(cnt)
        for mult in (0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.5):
            cn = int(round(2.0 * bc * mult))
            if 16 <= cn <= total_avail_n:
                cand_counts.add(cn)

        best_score = -1.0
        best_map: dict[str, int] = {}
        best_k = 24
        best_n = 1
        for target_n in sorted(cand_counts):
            cmap: dict[str, int] = {}
            rem = target_n
            raw = 0.0
            for m in reversed(GPU_ORDER):
                c = avail.get(m, 0)
                if c <= 0 or rem <= 0:
                    continue
                take = min(c, rem)
                cmap[m] = take
                raw += take * self.st.gpu_meta[m]["power"]
                rem -= take
            n_used = target_n - rem
            if n_used <= 0:
                continue
            b = (n_used + 1) // 2
            waste = (min(b, bc) + 0.5 * max(0.0, b - bc)) / b
            k_opt, rel = _best_ckpt_and_rel(n_used, mtbf)
            eff_comp = raw * waste * rel
            if eff_comp > best_score:
                best_score = eff_comp
                best_map = cmap
                best_k = k_opt
                best_n = n_used

        return best_map, max(4, (best_n + 1) // 2), best_k, serve_models_used

    def project_compute(self, t0: int, t1: int, train_now: dict[str, int], cur_d: float = 100.0) -> float:
        dur = max(1, t1 - t0)
        mtbf = float(self.st.rules.get("MTBF", 50000.0))
        nc = max(1, sum(train_now.values()))
        bc = self.fitter.bc0_est * ((1.0 + max(50.0, cur_d) / 50.0) ** 0.4)
        b = (nc + 1) // 2
        waste = (min(b, bc) + 0.5 * max(0.0, b - bc)) / b
        _, rel = _best_ckpt_and_rel(nc, mtbf)
        eff_now = max(0.25, waste * rel)

        base = self.st.compute_of(train_now) * eff_now * dur
        for d in self.st.deliv:
            arr_t = int(d[0])
            if arr_t < t1:
                base += self.st.gpu_meta[d[1]]["power"] * int(d[2]) * 0.72 * (t1 - max(t0, arr_t))
        if dur > 2000:
            recent = [a for a in self.st.arrivals if a[0] > self.st.t - 8 * 168]
            rate = sum(self.st.gpu_meta[a[1]]["power"] * a[2] for a in recent) / (8.0 * 168.0)
            base += 0.70 * rate * 0.72 * (dur ** 2) * 0.5
            if t0 >= 14500:
                base *= 1.18
        return max(1e4, base)

    def make_run_spec(self, name: str, N: float, D: float, sched: str, train: dict[str, int], batch: int, ckpt_every: int, paused: bool = False) -> dict:
        beta = self.fitter.fit["beta"] if self.fitter.fit else 0.30
        mx = self.mixer.optimize_mix(D, beta=beta, allow_synth=(self.gen_idx >= 1))
        sp = {
            "name": name,
            "params_b": round(max(0.5, N), 3),
            "tokens_b": round(max(1.0, D), 2),
            "gpus": train,
            "lr": self.fitter.eta_star(N) * 0.965,
            "batch": max(4, batch),
            "sched": sched,
            "mix": mx,
            "ckpt_every": ckpt_every,
        }
        if paused:
            sp["paused"] = True
        return sp

    # ---- Synthetic Data Pool Bootstrap (Zero Distillation!) ----
    def _maybe_generate_synth(self, act: dict) -> None:
        if "synth" not in self.st.tech or not self.st.has_any_live_model():
            return
        if not self.st.rent_prices or "G1" not in self.st.rent_prices:
            return
        t = self.st.t
        if t > 18500:
            return
        g1_rent = float(self.st.rent_prices["G1"])
        best_n = max(1.0, self.st.best_live_n)
        best_c = self.st.best_live_c_est
        unit_cost = best_n * 100.0 * g1_rent
        if unit_cost <= 0:
            return
        cur_u = self.st.U.get("synth", 0.0) + sum(x[1] for x in self.st.pend_synth)
        free_cash = self.st.cash - self.st.pending_gpu_bid_cost

        def queue_synth(tok_b: float) -> None:
            arr_t = t + 24
            act.setdefault("synth", []).append({"tokens_b": tok_b})
            self.st.pend_synth.append((arr_t, tok_b, 0.6 + 0.6 * best_c))
            if arr_t < self.st.next_synth_arr:
                self.st.next_synth_arr = arr_t

        # Wave 1: after Gen 1 live -> quick 500B cheap synth
        if 1 not in self.synth_waves_done and best_c >= 0.25 and cur_u < 450.0:
            tok = 500.0
            est_cost = tok * unit_cost
            if free_cash > est_cost * 1.4 + 30.0 and est_cost <= 20.0:
                queue_synth(tok)
                self.synth_waves_done.add(1)
                return

        # Wave 2: after Gen 2 live -> add 1300B high-quality synth
        if 2 not in self.synth_waves_done and best_c >= 0.48 and cur_u < 1650.0:
            tok = 1300.0
            est_cost = tok * unit_cost
            if free_cash > est_cost * 1.4 + 40.0 and est_cost <= 55.0:
                queue_synth(tok)
                self.synth_waves_done.add(2)
                return

        # Wave 3: after Gen 3 live -> top up to ~3,800B at q ~ 1.05+!
        if 3 not in self.synth_waves_done and best_c >= 0.72 and cur_u < 3500.0:
            tok = round(max(1000.0, 3800.0 - cur_u), 1)
            est_cost = tok * unit_cost
            if free_cash > est_cost * 1.4 + 50.0 and est_cost <= 95.0:
                queue_synth(tok)
                self.synth_waves_done.add(3)
                return

        # Wave 4: after Gen 4 live (before Gen 5 Titan!) -> top up to ~4,800B at q ~ 1.11+!
        if 4 not in self.synth_waves_done and best_c >= 0.83 and cur_u < 4500.0:
            tok = round(max(800.0, 4800.0 - cur_u), 1)
            est_cost = tok * unit_cost
            if free_cash > est_cost * 1.4 + 65.0 and est_cost <= 45.0:
                queue_synth(tok)
                self.synth_waves_done.add(4)

    # ---- Training Lifecycle ----
    def step_training(self, act: dict) -> None:
        t = self.st.t

        if self.cosine_resume_pending is not None:
            rname, new_tgt = self.cosine_resume_pending
            self.cosine_resume_pending = None
            r = self.st.runs_obs.get(rname)
            if r is not None and r[1] == "paused":
                cur_d = float(r[2])
                safe_v = max(cur_d + 1.0, min(9e5, new_tgt))
                ctl = act.setdefault("run_ctl", [])
                ctl.append({"name": rname, "op": "set_tokens", "arg": round(safe_v, 3)})
                ctl.append({"name": rname, "op": "resume"})
            return

        if self.main_run is None or (self.main_run not in self.st.runs_obs and self.main_run in self.released_runs):
            self._start_next_gen(act)
            return

        r = self.st.runs_obs.get(self.main_run)
        if r is None:
            return
        status = r[1]
        cur_d = float(r[2])
        cur_tgt = float(r[3])

        if status == "running":
            if t % 6 == 0:
                self.fitter.update_bc0_from_run(r, self.main_batch, self.main_slow)
            tpt_now = float(r[4])
            if tpt_now > 0.0:
                self.main_tpt_ema = tpt_now if self.main_tpt_ema <= 0.0 else (0.96 * self.main_tpt_ema + 0.04 * tpt_now)
        if status == "diverged" and r[6] == 0:
            act.setdefault("run_ctl", []).append({"name": self.main_run, "op": "stop"})
            self.main_run = None
            return
        if status == "done":
            self._release_run(act, self.main_run, advance_gen=True)
            return

        # Record daily production losses (Gen 1 & Gen 2 only)
        if t % 24 == 23 and self.gen_idx <= 2 and self.main_run in self.st.last_loss:
            l_vec = self.st.last_loss[self.main_run]
            prog = cur_d / max(1e-6, cur_tgt)
            if self.main_sched == "cosine":
                bon = 0.02 * min(1.0, prog) ** 2
            elif self.main_sched == "wsd":
                bon = 0.0 if prog < 0.85 else 0.03 * min(1.0, (prog - 0.85) / 0.15)
            else:
                bon = 0.0
            self.fitter.record_production_loss(
                self.main_N * self.st.algo_mult(), cur_d, self.st.run_S.get(self.main_run, {}), l_vec, bon
            )

        # Milestone checkpoint releases
        if status == "running" and cur_d > 20.0:
            for idx, mt in enumerate(self.main_milestones):
                if t >= mt and idx not in self.milestones_done and self.main_end - t > 600:
                    if self.main_sched == "cosine":
                        if cur_d < 0.96 * cur_tgt:
                            self.milestones_done.add(idx)
                            t_snap = round((cur_d + 0.003) / 0.976, 4)
                            ctl = act.setdefault("run_ctl", [])
                            ctl.append({"name": self.main_run, "op": "pause"})
                            ctl.append({"name": self.main_run, "op": "ckpt"})
                            ctl.append({"name": self.main_run, "op": "set_tokens", "arg": t_snap})
                            pr = self._initial_release_price(expected_pub_jump=0.06)
                            act.setdefault("release", []).append({"run": self.main_run, "price": pr})
                            self.cosine_resume_pending = (self.main_run, max(cur_tgt, cur_d * 2.5))
                            return
                    else:
                        # For WSD (Gen 4 Titan): only mid-release if 3-day EMA loss strictly beats current flagship!
                        ema_l = self.st.run_loss_ema.get(self.main_run)
                        if r[6] > 0 and ema_l is not None and ema_l < self.best_released_loss - 0.028 and cur_d > 0.40 * cur_tgt:
                            self.milestones_done.add(idx)
                            pr = self._initial_release_price(expected_pub_jump=0.03)
                            act.setdefault("release", []).append({"run": self.main_run, "price": pr})
                            self.best_released_loss = min(self.best_released_loss, ema_l)

        # Proactive pre-audit for next generation (Wait for algo_eff_3 before creating Gen 3+!)
        if (
            self.next_run is None
            and self.gen_idx + 1 < len(self.gens)
            and self.gen_idx not in self.prepped_gens
            and t >= self.main_end - 800
        ):
            lead_audit = int(float(self.st.rules.get("REVIEW", 336)) * self.st.max_site_reg()) + 96
            if t >= self.main_end - lead_audit and not (self.gen_idx + 1 >= 2 and "algo_eff_3" not in self.st.tech):
                self.prepped_gens.add(self.gen_idx)
                self._prepare_next_gen(act)

        # Daily operational retuning + cloud training rental check
        if t % 24 == 23 and status in ("running", "review"):
            train, new_b, new_k, serve_models = self.allocate_training_cards(cur_d)
            ctl = act.setdefault("run_ctl", [])
            ctl.append({"name": self.main_run, "op": "set_gpus", "arg": train})
            if sum(train.values()) > 0:
                self.main_batch = new_b
                ctl.append({"name": self.main_run, "op": "set_batch", "arg": new_b})
                ctl.append({"name": self.main_run, "op": "set_ckpt", "arg": new_k})

            if status == "running":
                own_only_train = {m: min(c, self.st.own_usable().get(m, 0)) for m, c in train.items()}
                self.infra.maybe_rent_training_compute(
                    act, self.main_end, cur_d, self.fitter.bc0_est,
                    own_only_train, self.st.rented_usable(), serve_models,
                )

            if (
                status == "running"
                and "curriculum" in self.st.tech
                and t - self.last_mix_update_t >= 720
                and cur_tgt - cur_d > 50.0
            ):
                beta = self.fitter.fit["beta"] if self.fitter.fit else 0.30
                new_mix = self.mixer.optimize_mix(
                    max(50.0, cur_tgt - cur_d),
                    S_now=self.st.run_S.get(self.main_run),
                    cont_now=self.st.run_cont.get(self.main_run, 0.0),
                    D_now=cur_d,
                    beta=beta,
                    allow_synth=True,
                )
                ctl.append({"name": self.main_run, "op": "set_mix", "arg": new_mix})
                self.st.update_run_mix(self.main_run, new_mix)
                self.last_mix_update_t = t

            rem = self.main_end - t
            if status == "running" and rem > 60:
                kc = float(self.st.rules.get("KC", 300.0))
                mtbf = float(self.st.rules.get("MTBF", 50000.0))
                nc = max(1, sum(train.values()))
                bc = self.fitter.bc0_est * ((1.0 + max(10.0, cur_d) / 50.0) ** 0.4)
                waste = (min(new_b, bc) + 0.5 * max(0.0, new_b - bc)) / new_b
                _, rel = _best_ckpt_and_rel(nc, mtbf)
                tpt_model = self.st.compute_of(train) * waste * rel / (self.main_N * kc)
                # Conservative speed estimate for WSD lock (immune to 1-day serve/curtailment blips!)
                if self.main_tpt_ema > 0.0:
                    tpt_eff = max(tpt_model, self.main_tpt_ema * 0.72)
                    net_tpt_cons = min(self.main_tpt_ema * 0.90, tpt_eff * 0.95)
                    net_tpt_mid = 0.55 * self.main_tpt_ema * 0.93 + 0.45 * tpt_eff
                else:
                    net_tpt_cons = tpt_model * 0.90
                    net_tpt_mid = tpt_model
                eff_rem = max(24, rem - (96 if self.gen_idx == len(self.gens) - 1 else 48))
                est_more_cons = net_tpt_cons * eff_rem
                more_deliv = 0.0
                for d in self.st.deliv:
                    arr_t = int(d[0])
                    if arr_t < self.main_end - 96:
                        more_deliv += self.st.gpu_meta[d[1]]["power"] * int(d[2]) * 0.65 * (self.main_end - 96 - max(t, arr_t)) / (self.main_N * kc)
                proj_total = cur_d + net_tpt_mid * eff_rem + more_deliv
                elapsed_frac = (t - self.main_start) / max(1.0, self.main_end - self.main_start)

                if self.main_sched == "wsd":
                    if self.main_run not in self.wsd_locked and cur_d < 0.79 * cur_tgt:
                        anneal_needed = cur_d * (0.155 / 0.845)
                        # NEVER lock WSD early (protects against Day-1 serve blips!)
                        # Once elapsed_frac >= 0.72, lock cleanly so full 3.0% annealing finishes right before main_end!
                        if elapsed_frac >= 0.72 and cur_d > 15.0 and (est_more_cons <= anneal_needed * 1.12 or rem <= 320):
                            final_tgt = cur_d / 0.845
                            if final_tgt > cur_d + 0.2 and abs(final_tgt - cur_tgt) / max(1.0, cur_tgt) > 0.008:
                                ctl.append({"name": self.main_run, "op": "set_tokens", "arg": round(min(9e5, final_tgt), 3)})
                            self.wsd_locked.add(self.main_run)
                        else:
                            safe_tgt = max(cur_d / 0.72, proj_total * 1.06)
                            if safe_tgt > cur_d + 0.2 and abs(safe_tgt - cur_tgt) / max(1.0, cur_tgt) > 0.03:
                                ctl.append({"name": self.main_run, "op": "set_tokens", "arg": round(min(9e5, safe_tgt), 3)})
                elif self.main_sched == "cosine":
                    if cur_d < 0.96 * cur_tgt:
                        target_new = max(cur_d / 0.95, proj_total * (0.985 if elapsed_frac >= 0.80 else 1.03))
                        if target_new > cur_d + 0.2 and abs(target_new - cur_tgt) / max(1.0, cur_tgt) > 0.025:
                            ctl.append({"name": self.main_run, "op": "set_tokens", "arg": round(min(9e5, target_new), 3)})

        if status in ("running", "review") and t >= self.main_end - 4:
            act.setdefault("run_ctl", []).append({"name": self.main_run, "op": "stop"})
            if r[6] > 0:
                self._release_run(act, self.main_run, advance_gen=True)
            else:
                self.gen_idx += 1
                self.main_run = None

    def _start_next_gen(self, act: dict) -> None:
        if self.gen_idx >= len(self.gens):
            return
        gcfg = self.gens[self.gen_idx]
        end = gcfg["end"]
        sched = gcfg["sched"]
        if end - self.st.t < 100:
            self.gen_idx += 1
            return

        train, batch, ckpt_k, _ = self.allocate_training_cards(50.0)
        if sum(train.values()) <= 0:
            return

        if self.gen_idx >= 2 and self.gen_idx not in self.refit_done_gens:
            self.refit_done_gens.add(self.gen_idx)
            self.fitter.refit_with_online_data()

        if self.next_run is not None:
            name, n_b, p_sched = self.next_run
            self.next_run = None
            ctl = act.setdefault("run_ctl", [])
            ctl.append({"name": name, "op": "set_gpus", "arg": train})
            ctl.append({"name": name, "op": "set_batch", "arg": batch})
            ctl.append({"name": name, "op": "set_ckpt", "arg": ckpt_k})
            if "curriculum" in self.st.tech:
                beta = self.fitter.fit["beta"] if self.fitter.fit else 0.30
                bud = self.project_compute(self.st.t, end, train)
                d_est = max(50.0, bud / (300.0 * n_b))
                mx = self.mixer.optimize_mix(d_est, beta=beta, allow_synth=True)
                ctl.append({"name": name, "op": "set_mix", "arg": mx})
                self.st.update_run_mix(name, mx)
            ctl.append({"name": name, "op": "resume"})
            self.main_run = name
            self.main_N = n_b
            self.main_sched = p_sched
            self.main_batch = batch
            self.main_start = self.st.t
            self.main_end = end
            self.main_slow = False
            self.main_tpt_ema = 0.0
            self.main_milestones = list(gcfg["milestones"])
            self.milestones_done = set()
            self.last_mix_update_t = self.st.t
            return

        budget = self.project_compute(self.st.t, end, train)
        N, D = self.choose_ND(budget, self.gen_idx)
        self.run_seq += 1
        name = f"heg_g{self.run_seq}"
        sp = self.make_run_spec(name, N, D, sched, train, batch, ckpt_k, paused=False)
        self.st.register_run(name, sp["params_b"], sp["mix"])
        act.setdefault("run", []).append(sp)
        if budget >= float(self.st.rules.get("REG_COMPUTE", 20e6)) * 0.75 and name not in self.audit_sent:
            act.setdefault("audit_request", []).append({"run": name})
            self.audit_sent.add(name)
        self.main_run = name
        self.main_N = sp["params_b"]
        self.main_sched = sched
        self.main_batch = sp["batch"]
        self.main_start = self.st.t
        self.main_end = end
        self.main_slow = False
        self.main_tpt_ema = 0.0
        self.main_milestones = list(gcfg["milestones"])
        self.milestones_done = set()
        self.last_mix_update_t = self.st.t

    def _prepare_next_gen(self, act: dict) -> None:
        nxt_idx = self.gen_idx + 1
        if nxt_idx >= len(self.gens):
            return
        if nxt_idx not in self.refit_done_gens:
            self.refit_done_gens.add(nxt_idx)
            self.fitter.refit_with_online_data()
        gcfg = self.gens[nxt_idx]
        t0 = self.main_end
        end = gcfg["end"]
        sched = gcfg["sched"]
        train, batch, ckpt_k, _ = self.allocate_training_cards(100.0)
        if sum(train.values()) <= 0:
            return
        budget = self.project_compute(t0, end, train)
        if budget < float(self.st.rules.get("REG_COMPUTE", 20e6)) * 0.65:
            return
        N, D = self.choose_ND(budget, nxt_idx)
        self.run_seq += 1
        name = f"heg_g{self.run_seq}"
        sp = self.make_run_spec(name, N, D, sched, train, batch, ckpt_k, paused=True)
        self.st.register_run(name, sp["params_b"], sp["mix"])
        act.setdefault("run", []).append(sp)
        act.setdefault("audit_request", []).append({"run": name})
        self.audit_sent.add(name)
        self.next_run = (name, sp["params_b"], sched)

    def _initial_release_price(self, expected_pub_jump: float = 0.05) -> float:
        fl = self.st.active_flagship()
        own_comp = self.st.compute_of(self.st.own_usable())
        rent_comp = self.st.compute_of(self.st.rented_usable())
        # Sustainable serving capacity (don't count temporary training rental bursts as permanent serve cap!)
        sust_comp = own_comp if self.st.t >= self.st.ticks - 2600 else max(own_comp * 0.85, own_comp + 0.30 * rent_comp)
        if fl is not None:
            new_pub = min(0.98, float(fl["pub"]) + expected_pub_jump)
            p_opt = self.pricer.steady_state_price(new_pub, self.main_N, sust_comp, False, float(fl["price"]), allow_jump=True)
            p_safe = float(fl["price"]) * 1.48 * math.exp(4.0 * max(0.0, expected_pub_jump * 0.5))
            return round(max(4.5, min(48.0, p_opt, p_safe)), 3)
        # First commercial release: compute steady-state price for our lean Gen-1 fleet
        est_pub = 0.42
        p_opt = self.pricer.steady_state_price(est_pub, self.main_N, max(512.0, sust_comp), False, 8.0, allow_jump=True)
        return round(max(5.0, min(14.0, p_opt)), 3)

    def _release_run(self, act: dict, name: str, advance_gen: bool) -> None:
        if name in self.released_runs:
            return
        self.released_runs.add(name)
        for e in self.st.ev:
            if e.get("type") == "done" and e.get("run") == name and "loss" in e:
                lm = sum(e["loss"]) / len(e["loss"])
                self.best_released_loss = min(self.best_released_loss, lm)
        jump = 0.08 if self.gen_idx == len(self.gens) - 1 else 0.06
        act.setdefault("release", []).append({"run": name, "price": self._initial_release_price(jump)})
        if advance_gen:
            self.gen_idx += 1
            self.main_run = None

    def _handle_ops(self, act: dict) -> None:
        for e in self.st.ev:
            ty = e.get("type")
            rname = e.get("run")
            if not rname:
                continue
            if ty in ("spike", "diverged"):
                r = self.st.runs_obs.get(rname)
                if r and r[6] > 0:
                    act.setdefault("run_ctl", []).append({"name": rname, "op": "rollback"})
            elif ty == "slow_node":
                act.setdefault("run_ctl", []).append({"name": rname, "op": "diagnose"})
                if rname == self.main_run:
                    self.main_slow = False

    def _update_research(self, act: dict) -> None:
        if not self.st.staff:
            return
        avail = []
        for tk in self.TECH_ROADMAP:
            if tk in self.st.tech:
                continue
            pre = self.st.techs.get(tk, {}).get("pre")
            if pre and pre not in self.st.tech:
                continue
            avail.append(tk)
        if not avail:
            return
        if "early_supply" in avail and self.st.t >= 2900:
            avail.remove("early_supply")
            avail.insert(0, "early_supply")
        pick = (avail[0],)
        if pick != self.research_cur:
            self.research_cur = pick
            act["research"] = [{"tech": pick[0], "share": 1.0}]

    def _manage_pricing_and_models(self, act: dict) -> None:
        t = self.st.t
        exec_day = (t + 1) // 24

        live_mine = [
            (mid, self.st.models[mid])
            for mid, meta in self.st.my_models.items()
            if mid in self.st.models and not meta.get("delisted")
        ]
        if len(live_mine) > 1:
            best_mid = max(live_mine, key=lambda kv: (float(kv[1]["pub"]), kv[0]))[0]
            for mid, m in live_mine:
                if mid == best_mid:
                    continue
                meta = self.st.my_models[mid]
                if t - int(meta.get("live_t", t)) >= 740:
                    act.setdefault("delist", []).append(mid)
                    meta["delisted"] = True
                elif float(m["price"]) < 98.0 and self.st.last_price_exec_day.get(mid, -1) != exec_day:
                    act.setdefault("price", []).append({"model": mid, "value": 100.0})
                    self.st.last_price_exec_day[mid] = exec_day

        fl = self.st.active_flagship()
        if fl is None:
            return
        mid = fl["id"]
        if self.st.last_price_exec_day.get(mid, -1) == exec_day:
            return

        wk_tick = t % 168
        if wk_tick != 0 and wk_tick != 166:
            return

        own_comp = self.st.compute_of(self.st.own_usable())
        rent_comp = self.st.compute_of(self.st.rented_usable())
        training_active = self.main_run is not None and self.main_run in self.st.runs_obs and self.st.runs_obs[self.main_run][1] in ("running", "paused")
        # Use sustainable owned compute (+ modest share of rented during training) so expiring training leases never trigger drops!
        serve_comp = own_comp if not training_active else max(own_comp * 0.85, own_comp + 0.30 * rent_comp)
        serve_has_rent = (not training_active) and rent_comp > 0 and own_comp < self.serve_buffer_compute()

        cur_p = float(fl["price"])
        if wk_tick == 0:
            p_opt = self.pricer.solve_optimal_price(fl, serve_comp, serve_has_rent=serve_has_rent, for_next_week=False)
            if p_opt > cur_p * 1.015:
                act.setdefault("price", []).append({"model": mid, "value": p_opt})
                fl["price"] = p_opt
                self.st.last_price_exec_day[mid] = exec_day
        elif wk_tick == 166:
            p_opt = self.pricer.solve_optimal_price(fl, serve_comp, serve_has_rent=serve_has_rent, for_next_week=True)
            if p_opt < cur_p * 0.985:
                act.setdefault("price", []).append({"model": mid, "value": p_opt})
                fl["price"] = p_opt
                self.st.last_price_exec_day[mid] = exec_day

    def _emergency_serve_rent(self, act: dict) -> None:
        """Proactively rent ondemand GPUs whenever serving demand approaches total fleet capacity (ZERO drops!)."""
        t = self.st.t
        if t % 24 != 23 or not self.st.rent_prices:
            return
        fl = self.st.active_flagship()
        if not fl or self.st.cash < 35.0 or float(fl["price"]) < 3.5:
            return
        need = self.serve_buffer_compute()
        have = self.st.compute_of(self.st.total_usable())
        short = need - have * 0.92
        curtailed_now = any(end_t > t for end_t in self.st.curtailed_until.values())
        if short <= 120.0 and not (curtailed_now and short > 0.0) and self.st.drop <= 0.004:
            return
        short = max(short, need - have + 150.0)
        if short <= 50.0:
            return
        exec_q = (t + 1) // int(self.st.rules.get("QUARTER", 2190))
        best = None
        for m, pr in self.st.rent_prices.items():
            meta = self.st.gpu_meta[m]
            if exec_q < meta["launch_q"]:
                continue
            u = (pr * 8000.0 / meta["price"] - 1.0) / 2.0
            if u >= 0.88:
                continue
            room = int(meta["supply_q"] * 2.0 * (0.90 - u))
            if room < 32:
                continue
            cpp = pr / meta["power"]
            if best is None or cpp < best[0]:
                best = (cpp, m, room, meta["power"])
        if best is None:
            return
        _, m, room, pw = best
        n = min(room, int(math.ceil(short / pw)))
        rem_ticks = max(24, min(168, self.st.ticks - t))
        if n > 0:
            act.setdefault("rent", []).append({"model": m, "n": n, "kind": "ondemand", "ticks": rem_ticks})
            self.st.pending_rent_queue.append((m, rem_ticks, "ondemand"))

    def step(self, obs: dict) -> dict:
        self.st.ingest(obs)
        if self.st.bk:
            return {}
        act: dict = {}
        t = self.st.t

        if t == 0:
            self._update_research(act)

        if self.fitter.step(act):
            self.step_training(act)
            # Instant zero-drop guard (throttled to <= once per 12 ticks): immediately free training GPUs!
            if (
                t % 24 != 23
                and t - self.last_drop_trim_t >= 12
                and self.st.api_dem > 1e-4
                and self.st.api_srv < self.st.api_dem * 0.995
                and self.main_run is not None
                and self.main_run in self.st.runs_obs
                and self.st.runs_obs[self.main_run][1] == "running"
            ):
                self.last_drop_trim_t = t
                cur_d = float(self.st.runs_obs[self.main_run][2])
                train, new_b, new_k, _ = self.allocate_training_cards(cur_d)
                ctl = act.setdefault("run_ctl", [])
                ctl.append({"name": self.main_run, "op": "set_gpus", "arg": train})
                if sum(train.values()) > 0:
                    self.main_batch = new_b
                    ctl.append({"name": self.main_run, "op": "set_batch", "arg": new_b})
                    ctl.append({"name": self.main_run, "op": "set_ckpt", "arg": new_k})
        elif t == 23 and self.st.ticks >= 500 and "G1" in self.st.rent_prices:
            # Kick off Q0 Gen-1 cloud boost right as probes run so Gen-1 finishes fast with strong C!
            meta = self.st.gpu_meta["G1"]
            pr = float(self.st.rent_prices["G1"])
            u = (pr * 8000.0 / meta["price"] - 1.0) / 2.0
            if u < 0.85 and self.st.cash > 120.0:
                room = int(meta["supply_q"] * 2.0 * (0.87 - u))
                n_add = min(768, room)
                if n_add >= 64:
                    act.setdefault("rent", []).append({"model": "G1", "n": n_add, "kind": "ondemand", "ticks": 168})
                    self.st.pending_rent_queue.append(("G1", 168, "ondemand"))
        self._handle_ops(act)

        if t % 168 == 0:
            tech_done = all(k in self.st.tech for k in self.TECH_ROADMAP)
            self.infra.weekly_actions(act, tech_done)
            self._manage_pricing_and_models(act)
        elif t % 168 == 166:
            self._manage_pricing_and_models(act)

        if t % 24 == 23:
            self._update_research(act)
            self.infra.daily_ppa(act)
            self._maybe_generate_synth(act)
            self._emergency_serve_rent(act)
        elif t % 24 == 0:
            # Only model cleanup (delist/price-100 old models) on non-Monday days
            if t % 168 != 0:
                self._manage_pricing_and_models(act)
        return act


def make_bot(init: dict) -> ScalingHegemon:
    return ScalingHegemon(init)


if __name__ == "__main__":
    _loads = json.loads
    _dumps = json.dumps
    _write = sys.stdout.write
    _flush = sys.stdout.flush
    _EMPTY = "{}\n"
    raw_init = sys.stdin.readline()
    if raw_init:
        bot = make_bot(_loads(raw_init))
        _write(_EMPTY)
        _flush()
        _step = bot.step
        for line in sys.stdin:
            msg = _loads(line)
            if msg.get("type") == "end":
                _write(_EMPTY)
                _flush()
                break
            try:
                action = _step(msg)
            except Exception:
                action = None
            if action:
                _write(_dumps(action, separators=(",", ":")) + "\n")
            else:
                _write(_EMPTY)
            _flush()
