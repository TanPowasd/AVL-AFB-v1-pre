"""capital_arbitrageur: Capital Arbitrageur (scoring_v2 reference opponent).

Strategic identity:
  - Zero-dilution equity discipline: keeps equity = 1.00 (no fundraising dilution, except
    bankruptcy survival guard) so 100% of terminal cash, hardware book, site book, and
    24 * GM_2190 belongs to founders.
  - Scientific capital calibration: ultra-fast spike-free LR + exact B_crit + scaling-law
    (alpha, beta, A, B) probes in the opening 40 ticks, plus a zero-cost synthetic-data
    seed arbitrage to mint a deep 40,000 B zero-contamination synthetic reservoir before Gen 1.
  - High-velocity R&D sprint & full payroll liquidation: hires a lean, cost-screened team to
    unlock the full 17-tech stack (synth, algo_eff_1..3, filter:web, early_supply,
    infer_opt_1..2, cooling, curriculum, stable, and all remaining domain filters) within ~6
    months, then liquidates payroll to zero for the remaining 2.5 years.
  - Rent-vs-buy & counter-cyclic silicon arbitrage: avoids obsolete G1 capex, bridges Year 1-2
    velocity with structurally zero-illegal on-demand cloud rentals, aggressively accumulates
    high-density G3/G4/G5 silicon (especially NPC-free early_supply auctions) with clearing-ratio
    calibrated uniform-price bidding, liquidates deprecated G1/MW-constrained batches in
    favorable heat, locks 20%-discount PPAs, and snaps up high-ROI bottleneck data & distill deals.
  - Serving-coupled flagship scaling & exact zero-bloat serving: chooses capital-optimal N by
    explicitly coupling training compute, multi-year serving compute drain, and Q11 owned-fleet
    capacity; allocates exact smooth-demand serving cards (zero mid-week over-reservation, zero
    drops) and compounds remaining fleet compute into a pre-audited algo_eff_3 cosine flagship
    with exact analytical-gradient curriculum water-filling, exact B_crit/MTBF GPU & checkpoint
    scheduling, and intermediate cosine-ratchet monetization releases through Q11.
  - Competitive H2H pricing & opponent-squeezing Q11 DP harvest: explicitly accounts for
    owned vs rented marginal serving cost, competitive displacement (squeezing rival margin &
    share), exact capacity floors (zero drops), and anti-shock finite-horizon Q11 dynamic
    programming maximizing terminal valuation with owned newest-gen serving GPUs.

Deterministic, single-file, Python 3.10+ standard library only.
"""
from __future__ import annotations

import json
import math
import sys

REAL_POOLS = ("web", "code", "math", "books", "science", "dialog", "multiling")
ALL_POOLS = REAL_POOLS + ("synth", "distill")
GPUS = ("G1", "G2", "G3", "G4", "G5")

T_PRIOR = {
    d: {
        k: (
            1.0 if k == d else
            0.55 if (d, k) == ("math", "code") else
            0.45 if (d, k) == ("dialog", "multiling") else
            0.40 if (d, k) == ("science", "books") else
            0.30 if (d, k) in (("dialog", "web"), ("math", "science")) else
            0.175 if (d, k) == ("code", "web") else
            0.325 if k == "synth" else
            0.50 if k == "distill" else
            0.07
        )
        for k in ALL_POOLS
    }
    for d in REAL_POOLS
}
DOM_W = {"math": 1.0, "code": 1.0, "science": 0.72, "books": 0.58, "dialog": 1.0, "multiling": 1.0, "web": 1.0}
T_MAT = tuple(tuple(T_PRIOR[d][k] for k in ALL_POOLS) for d in REAL_POOLS)
DOM_TW_TUP = tuple(
    tuple(DOM_W[REAL_POOLS[di]] * T_PRIOR[REAL_POOLS[di]][REAL_POOLS[ki]] for di in range(7))
    for ki in range(7)
)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


class ArbitrageLedger:
    """State, cashflow, heat-cycle, rental-pool, and safety ledger."""

    def __init__(self, init: dict):
        self.seat = int(init["seat"])
        self.me = "AB"[self.seat]
        self.opp = "BA"[self.seat]
        self.ticks = int(init.get("ticks", 26280))
        self.rules = dict(init["rules"])
        self.kc = float(self.rules.get("KC", 300.0))
        self.sk = float(self.rules.get("SK", 25.0))
        self.u_pub = float(self.rules.get("U_PUB", 6.0))
        self.u_price = float(self.rules.get("U_PRICE", 1.5))
        self.u_drop = float(self.rules.get("U_DROP", 5.0))
        self.mtbf = float(self.rules.get("MTBF", 50000.0))
        self.gpu = {g["model"]: dict(g) for g in init["gpus"]}
        self.gpu_pow = {g["model"]: float(g["power"]) for g in init["gpus"]}
        self.regions = [dict(r) for r in init["regions"]]
        self.techs = dict(init["techs"])

        self.U: dict[str, float] = {d["name"]: float(d["public_b"]) for d in init["domains"]}
        self.U.setdefault("synth", 10.0)
        self.U.setdefault("distill", 0.0)
        self.q_est: dict[str, float] = {d["name"]: float(d["q_hint"]) for d in init["domains"]}
        self.q_est.setdefault("synth", 0.72)
        self.q_est.setdefault("distill", 0.95)
        self.c_est: dict[str, float] = {d["name"]: 0.065 for d in init["domains"]}
        self.c_est["synth"] = 0.0
        self.c_est["distill"] = 0.0
        self.filtered: set[str] = set()

        self.t = 0
        self.cash = float(init["start"]["cash"])
        self.cash_avail = self.cash
        self.neg_ticks = 0
        self.equity = 1.0
        self.rep = 0.0
        self.bk = False
        self.gpu_obs: dict[str, list[int]] = {}
        self.runs_obs: dict[str, list] = {}
        self.api_obs: list[float] = [0.0, 0.0, 0.0]
        self.ev: list[dict] = []
        self.res: list[dict] = []
        self.wk: dict | None = None

        self.heat = 1.0
        self.heat_hist: list[float] = []
        self.demand = float(self.rules.get("Q0", 5.0))
        self.shares: dict[str, float] = {"N1": 0.5, "N2": 0.5}
        self.my_share = 0.0
        self.opp_share = 0.0
        self.drop = 0.0
        self.models: dict[str, dict] = {m["id"]: dict(m) for m in init.get("models", [])}
        self.my_models: dict[str, dict] = {}
        self.pending_my_models: dict[str, dict] = {}
        self.staff: list[dict] = [dict(s) for s in init["start"]["staff"]]
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
        self.rent_prices: dict[str, float] = {}
        self.rent_fresh = False
        self.elec_prices: list[float] = [float(r["price"]) for r in self.regions]
        self.rent_blocked_until: dict[str, int] = {}
        self.leases: dict[int, dict] = {}
        self.min_lease_end: int = 10**9
        self.pending_rent_meta: dict[str, tuple[str, int]] = {}
        self.local_pend_mw: dict[int, float] = {}
        self.site_count = 1
        self.last_site_build_t = -10**9
        self.ppa_count: dict[int, int] = {}
        self.ppa_covered_mw: dict[int, list[tuple[int, float]]] = {}
        self.last_price_exec_day: dict[str, int] = {}
        self.anchor_u: float | None = None
        self.raised_q: set[int] = set()
        self.distill_pending = 0.0
        self.synth_pending = 0.0
        self.synth_q_weighted = self.U["synth"] * self.q_est["synth"]
        self.distill_q_weighted = 0.0
        self.distill_spent_total = 0.0
        self.last_distill_q = 0.0
        self.last_sell_t = -10**9
        self.clear_ratio: dict[str, float] = {m: 1.32 for m in GPUS}
        self.bid_boost: dict[str, float] = {m: 0.0 for m in GPUS}
        self.last_gpu_bids: dict[str, int] = {}
        self.rev_ewma = 0.0
        self.open_source_live = False
        self.export_g3_q = -1
        self.data_spent_total = 0.0
        self.last_fleet_sig: tuple = ()

        # Exact hardware batch estimate for sell_gpu book-value awareness
        self.start_g1_left = int(init["start"]["gpus"].get("G1", 512))

    def pue(self) -> float:
        return float(self.rules.get("PUE1", 1.15)) if "cooling" in self.tech else float(self.rules.get("PUE0", 1.30))

    def infer_mult(self) -> float:
        if "infer_opt_2" in self.tech:
            return 0.6
        if "infer_opt_1" in self.tech:
            return 0.8
        return 1.0

    def algo_level(self) -> int:
        return sum(1 for k in ("algo_eff_1", "algo_eff_2", "algo_eff_3") if k in self.tech)

    def algo_mult(self, lvl: int | None = None) -> float:
        lv = self.algo_level() if lvl is None else lvl
        return (1.0, 1.15, 1.30, 1.50)[min(3, max(0, lv))]

    def compute_of(self, card_map: dict[str, int]) -> float:
        return sum(self.gpu[m]["power"] * n for m, n in card_map.items() if n > 0)

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
            kw += (int(v[0]) + int(v[1]) + int(v[2])) * self.gpu[m]["kw"] * pue
        for d in self.deliv:
            kw += int(d[2]) * self.gpu[d[1]]["kw"] * pue
        return kw

    def mw_capacity(self) -> float:
        return sum(
            float(s["mw"]) + max(float(s.get("pend_mw", 0.0)), self.local_pend_mw.get(int(s["id"]), 0.0))
            for s in self.sites
        )

    def model_u(self, pub: float, price: float) -> float:
        return self.u_pub * pub - self.u_price * math.log(price if price > 1.0 else 1.0)

    def flagship(self) -> dict | None:
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

    def opp_flagship(self) -> dict | None:
        best, bu = None, -1e18
        for m in self.models.values():
            if m["owner"] != self.opp:
                continue
            if float(m.get("pub", 0.0)) < 0.145:
                continue
            u = self.model_u(float(m["pub"]), float(m["price"]))
            if u > bu:
                best, bu = m, u
        return best

    def max_own_model_N(self) -> float | None:
        ns = [float(self.models[mid]["N"]) for mid in self.my_models if mid in self.models and self.models[mid].get("N")]
        return max(ns) if ns else None

    def synth_best_N_est(self) -> float | None:
        # Engine picks highest true C among our models in self.models (including delisted).
        # Conservative upper bound: if any model has pub >= 0.10, use max N among top-pub models (safe cash bound: max_own_model_N).
        return self.max_own_model_N()

    def has_released_model(self) -> bool:
        return any(mid in self.models for mid in self.my_models)

    def best_teacher(self) -> dict | None:
        # Strictly NPC or OPEN-1 (never delisted, never raises price unpredictably, never funds rival!)
        open_m = self.models.get("OPEN-1")
        best_npc = None
        for m in self.models.values():
            if m["owner"] in ("N1", "N2"):
                if best_npc is None or float(m["pub"]) > float(best_npc["pub"]):
                    best_npc = m
        if open_m is not None:
            if best_npc is None or float(open_m["pub"]) >= float(best_npc["pub"]) - 0.12:
                return open_m
        return best_npc or open_m

    def spend_cash(self, amt: float) -> None:
        self.cash_avail = max(0.0, self.cash_avail - amt)

    def ingest(self, obs: dict) -> None:
        t = int(obs["t"])
        self.t = t
        cash = float(obs["cash"])
        self.cash = cash
        self.cash_avail = cash
        self.neg_ticks = (self.neg_ticks + 1) if cash < 0.0 else 0
        self.equity = float(obs.get("eq", self.equity))
        self.rep = float(obs.get("rep", 0.0))
        self.bk = bool(obs.get("bk", False))
        self.gpu_obs = obs.get("gpu", {})
        runs_raw = obs.get("runs")
        self.runs_obs = {r[0]: r for r in runs_raw} if runs_raw else {}
        api_raw = obs.get("api")
        if api_raw is not None:
            self.api_obs = api_raw
            self.rev_ewma = 0.992 * self.rev_ewma + 0.008 * float(api_raw[2])
        ev = obs.get("ev")
        res = obs.get("res")
        self.ev = ev if ev else []
        self.res = res if res else []
        self.distill_pending = 0.0

        if self.leases and t >= self.min_lease_end:
            for lid in [k for k, v in self.leases.items() if v["end"] <= t]:
                del self.leases[lid]
            self.min_lease_end = min((v["end"] for v in self.leases.values()), default=10**9)

        if "rent" in obs:
            self.rent_prices = dict(obs["rent"])
            self.rent_fresh = True
        else:
            self.rent_fresh = False

        if "elec" in obs:
            self.elec_prices = [float(x) for x in obs["elec"]]

        if self.my_models:
            models = self.models
            for mid, meta in self.my_models.items():
                if not meta.get("delisted") and mid in models:
                    meta["online_ticks"] += 1

        if ev:
            for e in ev:
                ty = e.get("type")
                if ty == "release":
                    m = dict(e["model"])
                    self.models[m["id"]] = m
                    if m["owner"] == self.me:
                        self.pending_my_models.pop(m["id"], None)
                        meta = self.my_models.setdefault(m["id"], {"online_ticks": 0, "delisted": False, "seed": False})
                        meta["online_ticks"] = max(1, meta["online_ticks"])
                elif ty == "open_source":
                    m = dict(e["model"])
                    self.models[m["id"]] = m
                    self.open_source_live = True
                elif ty == "export_ctrl":
                    if e.get("model") == "G3":
                        self.export_g3_q = int(e.get("quarter", -1))
                elif ty == "tech":
                    tk = e["tech"]
                    self.tech.add(tk)
                    if tk.startswith("filter:") and "q" in e:
                        d = tk[7:]
                        self.q_est[d] = float(e["q"])
                        self.c_est[d] = float(e["c"])
                        self.filtered.add(d)
                elif ty == "audit_fail":
                    mid = e.get("model")
                    if mid in self.models:
                        self.models[mid]["pub"] = float(e["pub"])
                elif ty == "preempted":
                    self.leases.pop(int(e.get("rent_id", -1)), None)

        if res:
            for r in res:
                act = r.get("act")
                if r.get("ok"):
                    if act == "release":
                        mid = r["model"]
                        self.my_models.setdefault(mid, {"online_ticks": 0, "delisted": False, "seed": False})
                        self.pending_my_models[mid] = {"live": int(r.get("live", t + 24))}
                    elif act == "rent":
                        rid = int(r["id"])
                        m = r["model"]
                        kind, ticks_len = self.pending_rent_meta.get(m, (r.get("kind", "ondemand"), 168))
                        end_t = t + ticks_len
                        self.leases[rid] = {"m": m, "n": int(r["n"]), "kind": kind, "end": end_t}
                        if end_t < self.min_lease_end:
                            self.min_lease_end = end_t
                    elif act == "distill":
                        tok = float(r.get("tokens_b", 0.0))
                        self.distill_spent_total += float(r.get("cost", 0.0))
                        mid = r.get("model", "")
                        tm = self.models.get(mid)
                        q_add = (0.5 + 0.8 * float(tm["pub"])) if tm else 0.95
                        self.last_distill_q = max(self.last_distill_q, q_add)
                        self.distill_q_weighted += tok * q_add
                        self.U["distill"] = self.U.get("distill", 0.0) + tok
                        if self.U["distill"] > 0:
                            self.q_est["distill"] = self.distill_q_weighted / self.U["distill"]
                    elif act == "synth":
                        tok = float(r.get("tokens_b", 0.0))
                        self.synth_pending += tok
                        fl = self.flagship()
                        c_est = float(fl["pub"]) if fl else 0.0
                        q_add = 0.6 + 0.6 * c_est
                        self.synth_q_weighted += tok * q_add
                        tot_s = self.U.get("synth", 0.0) + self.synth_pending
                        if tot_s > 0:
                            self.q_est["synth"] = self.synth_q_weighted / tot_s
                    elif act == "expand_site":
                        sid = int(r["site"])
                        self.local_pend_mw[sid] = self.local_pend_mw.get(sid, 0.0) + float(r["mw"])
                    elif act == "build_site":
                        self.site_count += 1
                    elif act == "sell_gpu":
                        if r.get("model") == "G1":
                            self.start_g1_left = max(0, self.start_g1_left - int(r.get("n", 0)))
                    elif act == "ppa":
                        sid = int(r["site"])
                        self.ppa_count[sid] = self.ppa_count.get(sid, 0) + 1
                    elif act == "bid_data":
                        self.data_spent_total += float(r.get("price", 0.0))
                else:
                    if act == "rent":
                        for m_k in GPUS:
                            self.rent_blocked_until[m_k] = max(self.rent_blocked_until.get(m_k, 0), t + 168)

        if "wk" in obs:
            wk = obs["wk"]
            self.wk = wk
            self.heat = float(wk.get("heat", self.heat))
            self.heat_hist.append(self.heat)
            self.demand = float(wk.get("demand", self.demand))
            self.shares = dict(wk.get("shares", self.shares))
            self.my_share = float(self.shares.get(self.me, 0.0))
            self.opp_share = float(self.shares.get(self.opp, 0.0))
            live_ids = set()
            for m in wk.get("models", []):
                self.models[m["id"]] = dict(m)
                live_ids.add(m["id"])
            for mid in list(self.models):
                if mid not in live_ids:
                    del self.models[mid]
            for m, ares in (wk.get("auction") or {}).items():
                bh = self.gpu[m]["price"] * max(0.6, self.heat)
                if bh > 0 and ares.get("lots", 0) > 0:
                    rat = float(ares.get("clear", bh)) / bh
                    self.clear_ratio[m] = 0.55 * self.clear_ratio.get(m, 1.45) + 0.45 * rat
                bid_k = self.last_gpu_bids.get(m, 0)
                if bid_k > 0:
                    won_k = int((ares.get("winners") or {}).get(self.me, 0))
                    if won_k < bid_k:
                        self.bid_boost[m] = min(0.55, self.bid_boost.get(m, 0.0) + 0.14)
                    else:
                        self.bid_boost[m] = max(0.0, self.bid_boost.get(m, 0.0) - 0.04)
            self.last_gpu_bids = {}
            my = wk["my"]
            self.staff = [{"id": int(s[0]), "ab": int(s[1]), "sal": float(s[2])} for s in my["staff"]]
            self.sites = list(my["sites"])
            self.local_pend_mw = {int(s["id"]): float(s.get("pend_mw", 0.0)) for s in self.sites}
            self.site_count = len(self.sites)
            for k, v in my["data"].items():
                self.U[k] = float(v)
            self.synth_pending = 0.0
            self.tech = set(my["tech"])
            self.drop = float(my.get("drop", 0.0))
            self.deliv = list(my.get("deliv", []))
            fl = self.flagship()
            self.anchor_u = self.model_u(float(fl["pub"]), float(fl["price"])) if fl else None
        else:
            self.wk = None


class CapitalArbitrageDesk:
    """Counter-cyclic GPU buying, early-supply capture, data deals, lean sites/PPAs, and safe cloud rent."""

    def __init__(self, st: ArbitrageLedger):
        self.st = st

    def free_cash(self) -> float:
        sal = sum(s["sal"] for s in self.st.staff)
        # Keep tight operating cushion (payroll + 20-35M safety buffer)
        base_buf = 22.0 if self.st.t < 4000 else (28.0 if self.st.t < 21000 else 35.0)
        return self.st.cash_avail - (base_buf + sal * 24.0 * 21.0)

    def weekly_capital_ops(self, act: dict, tech_done: bool, training_models: set[str], main_active: bool) -> None:
        if not self.st.wk:
            return
        # Bankruptcy survival guard ONLY (keeps equity = 1.000 in normal play)
        if self.st.cash < -5.0 and self.st.neg_ticks > 240:
            exec_q = (self.st.t + 1) // 2190
            if exec_q not in self.st.raised_q:
                act.setdefault("raise", []).append({"amount": 90.0})
                self.st.raised_q.add(exec_q)

        self._sites_and_ppas(act)
        self._liquidate_obsolete_gpus(act, training_models)
        self._countercyclic_gpu_bids(act)
        self._data_deal_arbitrage(act)
        self._lean_hiring(act, tech_done)

    def _sites_and_ppas(self, act: dict) -> None:
        t = self.st.t
        T = self.st.ticks
        # 1. Expand Site 0 incrementally to full region cap_mw (cheapest MW: $2M/MW, 8 wks!)
        if t <= T - 3500:
            s0 = self.st.sites[0]
            r0 = self.st.regions[int(s0["region"])]
            cur_mw0 = float(s0["mw"]) + max(float(s0.get("pend_mw", 0.0)), self.st.local_pend_mw.get(0, 0.0))
            need_kw = self.st.kw_committed()
            cap_kw = self.st.mw_capacity() * 1000.0
            rem_mw0 = float(r0["cap_mw"]) - cur_mw0
            if rem_mw0 > 0.5 and (need_kw > 0.60 * cap_kw or (t >= 2000 and cur_mw0 < min(float(r0["cap_mw"]), 7.0))):
                want = max(4.0, (need_kw * 1.55 - cap_kw) / 1000.0 + 3.5)
                add = min(rem_mw0 - 0.05, want)
                cost = add * float(self.st.rules.get("EXPAND_COST", 2.0))
                if add >= 0.5 and self.st.cash_avail > cost + 28.0:
                    add_r = round(add, 2)
                    if add_r <= rem_mw0 - 0.02 and add_r >= 0.5:
                        act.setdefault("expand_site", []).append({"site": 0, "mw": add_r})
                        self.st.local_pend_mw[0] = self.st.local_pend_mw.get(0, 0.0) + add_r
                        self.st.spend_cash(add_r * float(self.st.rules.get("EXPAND_COST", 2.0)))
                        return

            # 2. Build up to 5 high-MW/cost, low-electricity datacenters ONLY when truly approaching MW cap
            if self.st.site_count < 5 and t - self.st.last_site_build_t >= 4 * 168:
                need_kw = self.st.kw_committed()
                cap_kw = self.st.mw_capacity() * 1000.0
                thresh = 0.65 if self.st.site_count == 1 else 0.72
                need_more = (need_kw > thresh * cap_kw) or (self.st.site_count == 1 and t >= 6500 and cap_kw < 14000.0 and self.st.cash_avail > 125.0)
                if need_more:
                    best_r = min(
                        self.st.regions,
                        key=lambda r: (r["build_cost"] / max(5.0, r["cap_mw"])) * (0.65 + r["price"] * 520.0)
                        + r["build_weeks"] * 0.03
                        + r["reg"] * 0.15,
                    )
                    cost = float(best_r["build_cost"])
                    if self.st.cash_avail > cost + 32.0:
                        act.setdefault("build_site", []).append({"region": int(best_r["id"])})
                        self.st.last_site_build_t = t
                        self.st.spend_cash(cost)

        # 3. PPA electricity arbitrage (20% instant discount + inflation hedge on active sites)
        if 1500 <= t <= T - 8800 and self.st.elec_prices:
            pue = self.st.pue()
            for s in self.st.sites:
                sid = int(s["id"])
                if int(s["ready"]) > t or self.st.ppa_count.get(sid, 0) >= 56:
                    continue
                cards = s.get("cards") or {}
                inst_mw = sum(int(cnt) * self.st.gpu[m]["kw"] * pue / 1000.0 for m, cnt in cards.items())
                if inst_mw < 2.0:
                    continue
                # Purge expired PPAs
                active = [(end_t, mw) for end_t, mw in self.st.ppa_covered_mw.get(sid, []) if end_t > t]
                self.st.ppa_covered_mw[sid] = active
                cov_mw = sum(mw for _, mw in active)
                # Cover up to 65% of installed MW when electricity isn't at seasonal peak
                target_mw = inst_mw * 0.65
                if target_mw - cov_mw >= 1.5:
                    reg = int(s["region"])
                    base_p = float(self.st.regions[reg]["price"]) * (1.0 + 0.12 * t / 8760.0)
                    cur_p = self.st.elec_prices[reg] if reg < len(self.st.elec_prices) else base_p
                    if cur_p <= base_p * 1.03:
                        add_mw = target_mw - cov_mw
                        mwh = round(add_mw * 8760.0, 1)
                        act.setdefault("ppa", []).append({"site": sid, "mwh": mwh})
                        self.st.ppa_covered_mw.setdefault(sid, []).append((t + 8760, add_mw))

    def _liquidate_obsolete_gpus(self, act: dict, training_models: set[str]) -> None:
        # Sell ONLY deprecated t=0 G1 batch (whose terminal book decays to ~0 anyway!)
        # or MW-blocking G2 when G4/G5 dominates, during favorable heat (>= 0.92).
        t = self.st.t
        if t < 6800 or t > self.st.ticks - 3200 or t - self.st.last_sell_t < 3 * 168:
            return
        if self.st.heat < 0.92:
            return
        own = self.st.own_usable()
        newer_power = sum(self.st.gpu[m]["power"] * own.get(m, 0) for m in ("G3", "G4", "G5"))
        if newer_power < 2200.0:
            return
        sells = []
        g1_obs = self.st.gpu_obs.get("G1", [0, 0, 0, 0, 0])
        g1_avail = int(g1_obs[0]) + int(g1_obs[2])
        if self.st.start_g1_left >= 128 and g1_avail >= 128 and "G1" not in training_models:
            sell_n = min(g1_avail, self.st.start_g1_left, 256)
            if sell_n > 0:
                sells.append({"model": "G1", "n": sell_n})
        # Sell G2 ONLY if seriously MW-constrained and G4/G5 already dominates
        g45_power = sum(self.st.gpu[m]["power"] * own.get(m, 0) for m in ("G4", "G5"))
        g2_obs = self.st.gpu_obs.get("G2", [0, 0, 0, 0, 0])
        g2_avail = int(g2_obs[0]) + int(g2_obs[2])
        if (
            g45_power >= 8000.0
            and g2_avail >= 256
            and "G2" not in training_models
            and self.st.kw_committed() > 0.86 * self.st.mw_capacity() * 1000.0
        ):
            sells.append({"model": "G2", "n": min(g2_avail, 256)})
        if sells:
            act["sell_gpu"] = sells
            self.st.last_sell_t = t

    def _countercyclic_gpu_bids(self, act: dict) -> None:
        t = self.st.t
        # Hard cutoff: max auction delivery delay is 10 weeks (1680 ticks); stop at T - 1850
        # so ZERO GPUs are ever stranded in transit at game end!
        if t > self.st.ticks - 1850:
            return
        offer = self.st.wk.get("offer") or {}
        sal = sum(s["sal"] for s in self.st.staff)
        # Active hourly rent burn
        cur_rent_hr = sum(
            self.st.rent_prices.get(l["m"], self.st.gpu[l["m"]]["price"] / 4000.0)
            * (0.4 if l["kind"] == "spot" else 1.0)
            * l["n"]
            for l in self.st.leases.values()
            if l["end"] > t + 10
        )
        # Auction settles next Monday (t + 168); conservatively include 120 ticks of expected net cash inflow!
        net_hr = max(0.0, self.st.rev_ewma - sal - cur_rent_hr - 0.002)
        proj_cash = self.st.cash_avail + 120.0 * net_hr
        reserve_cash = 18.0 + sal * 24.0 * 10.0
        avail_cash = max(0.0, proj_cash - reserve_cash)
        if avail_cash < 18.0:
            return
        h = self.st.heat

        # Uniform-price auction: NPCs NEVER bid > 1.50 * base * heat, and engine caps eligible lots
        # by int(cash // (bid_price * 256))! Keep bid_price tightly calibrated above clearing ratio
        # (with adaptive bid_boost when outbid) so we maximize eligible lots per dollar!
        if h <= 1.02:
            deploy_frac = 0.98
            bid_floor = 1.48
            clr_boost = 1.14
        elif h <= 1.20:
            deploy_frac = 0.94
            bid_floor = 1.42
            clr_boost = 1.12
        elif h <= 1.35:
            deploy_frac = 0.88
            bid_floor = 1.34
            clr_boost = 1.09
        else:
            deploy_frac = 0.78
            bid_floor = 1.26
            clr_boost = 1.06

        pue = self.st.pue()
        room_kw = self.st.mw_capacity() * 1000.0 - self.st.kw_committed()
        if t < self.st.ticks - 4200 and self.st.site_count < 5:
            room_kw += 6000.0
        elif t < self.st.ticks - 3000:
            room_kw += 2000.0

        cands = []
        for m, o in offer.items():
            lots = int(o.get("lots", 0))
            if lots <= 0:
                continue
            early = bool(o.get("early", False))
            if early and "early_supply" not in self.st.tech:
                continue
            if m == "G1":
                continue
            if m == "G2" and t >= 5800:
                continue
            if m == "G3" and t >= 18500:
                continue
            meta = self.st.gpu[m]
            res = float(o["reserve"])
            clr = self.st.clear_ratio.get(m, 1.30)
            boost = self.st.bid_boost.get(m, 0.0)
            gen = GPUS.index(m)
            if early:
                # NPCs do NOT participate in early_supply! Bid strong enough to win contested early_supply
                # without inflating bid_price so high that afford_lots drops!
                mult = min(1.90, 1.48 + boost)
                price = max(res * 1.04, meta["price"] * h * mult)
            else:
                mult = max(bid_floor, clr * clr_boost) + boost
                if gen >= 3:
                    mult = max(mult, 1.52 if h <= 1.22 else 1.38)
                if gen == 4:
                    mult = max(mult, 1.56 if h <= 1.28 else 1.42)
                if m == "G3" and self.st.export_g3_q >= 0:
                    cur_q = t // 2190
                    if cur_q in (self.st.export_g3_q - 1, self.st.export_g3_q):
                        mult = max(mult, 1.58)
                mult = min(1.95, mult)
                price = max(res * 1.02, meta["price"] * h * mult)
            # If bid price > affordable for 1 lot, step price smoothly down toward 1.05x so we still bid 1 lot!
            if price * 256.0 > avail_cash and res * 256.0 * 1.04 <= avail_cash:
                max_p = (avail_cash * 0.99) / 256.0
                if max_p >= max(res * 1.02, meta["price"] * h * 1.05):
                    price = max_p
            # Prefer newest generations: higher power/$, higher power/MW, and higher terminal book retention!
            rem_frac = max(0.12, (self.st.ticks - t - 1000) / float(self.st.ticks))
            net_cost_per_pow = (price * rem_frac) / meta["power"]
            val = (1.0 / max(1e-6, net_cost_per_pow)) * ((meta["power"] / meta["kw"]) ** 0.55) * (1.70 if early else 1.0)
            if gen == 4:
                val *= 1.18
            cands.append((val, m, price, lots, meta["kw"], early))

        if not cands:
            return
        cands.sort(reverse=True)
        budget = avail_cash * deploy_frac
        bids = []
        for _, m, price, lots, kw_c, early in cands[:3]:
            lot_cost = price * 256.0
            eff_budget = avail_cash * 0.99 if (early or lot_cost > budget) else budget
            kw_lot = 256.0 * kw_c * pue
            k = min(lots, int(eff_budget // lot_cost), int(max(0.0, room_kw) // kw_lot), 64)
            if k <= 0:
                continue
            bids.append({"model": m, "lots": k, "price": round(price, 5)})
            self.st.last_gpu_bids[m] = k
            spend_est = k * lot_cost * (0.68 if early else 0.80)
            budget = max(0.0, budget - spend_est)
            avail_cash = max(0.0, avail_cash - spend_est)
            room_kw -= k * kw_lot
        if bids:
            act["bid_gpu"] = bids

    def _data_deal_arbitrage(self, act: dict) -> None:
        # Disciplined bottleneck-domain exclusive data arbitrage:
        # Strict lifetime spend cap ($42M) so data deals boost bottleneck domains (math/dialog/science/code/books)
        # without ever crowding out high-ROI G3/G4/G5 GPU capex!
        t = self.st.t
        if t < 4800 or t > self.st.ticks - 4000:
            return
        if self.st.data_spent_total >= 42.0:
            return
        if self.st.cash_avail < 82.0:
            return
        deals = self.st.wk.get("deals") or []
        bids = []
        rem_lifetime = max(0.0, 42.0 - self.st.data_spent_total)
        spend_cap = min(rem_lifetime, 16.0)
        for d in deals:
            dom = d["domain"]
            if dom not in ("math", "dialog", "science", "code", "books"):
                continue
            tok = float(d["tokens_b"])
            qh = float(d["q_hint"])
            res = float(d["reserve"])
            cur_u = max(10.0, self.st.U.get(dom, 100.0))
            rel_gain = tok / cur_u
            # Snap up true bottleneck bargains (especially math/dialog/science where public U is tiny!)
            min_gain = 0.35 if dom in ("math", "dialog", "science") else 0.50
            if rel_gain < min_gain or qh < 0.88:
                continue
            dom_w = 1.7 if dom in ("math", "dialog", "science") else 1.2
            max_pay = res * _clip(1.12 + 0.45 * (qh - 0.90) + 0.25 * min(1.5, rel_gain) * dom_w, 1.12, 1.90)
            max_pay = min(max_pay, spend_cap, self.st.cash_avail - 68.0)
            if max_pay >= res * 1.02:
                bids.append({"deal": int(d["id"]), "price": round(max_pay, 3)})
                spend_cap -= max_pay
        if bids:
            act["bid_data"] = bids

    def _lean_hiring(self, act: dict, tech_done: bool) -> None:
        if tech_done:
            if self.st.staff and "fire" not in act:
                sev = sum(s["sal"] for s in self.st.staff) * float(self.st.rules.get("FIRE_SEVERANCE", 168))
                # Always liquidate payroll once all 17 techs are done (1 wk severance << ongoing salary!)
                if self.st.cash_avail > sev - 5.0:
                    act["fire"] = [s["id"] for s in self.st.staff]
                    self.st.spend_cash(sev)
            return
        # Fast R&D sprint: hire up to 64 cost-efficient researchers early -> finish all 17 techs in ~5 months,
        # then liquidate payroll to zero for the remaining 2.5 years!
        cap = 64 if self.st.t < 5000 else 48
        cur_n = len(self.st.staff)
        if cur_n >= cap or self.st.cash_avail < 40.0:
            return
        talent = self.st.wk.get("talent") or []
        ranked = sorted(talent, key=lambda c: -((c["lo"] + 1.15) / max(1e-9, c["ask"])))
        hires = []
        max_per_wk = 10 if cur_n < 48 else 5
        for c in ranked:
            if c["lo"] < 2 and cur_n >= 34:
                continue
            if cur_n + len(hires) >= cap or len(hires) >= max_per_wk:
                break
            mult = 1.31 if c["lo"] >= 3 else (1.24 if c["lo"] == 2 else 1.14)
            hires.append({"cand": int(c["id"]), "salary": round(float(c["ask"]) * mult, 7)})
        if hires:
            act["hire"] = hires

    def ensure_cloud_rent(
        self,
        act: dict,
        serve_need: float,
        own_serve_comp: float,
        train_want_extra_comp: float,
        max_lease_end: int,
        in_q11: bool,
    ) -> None:
        """STRUCTURALLY ZERO-ILLEGAL cloud rental desk with strict cashflow-compounding discipline."""
        t = self.st.t
        T = self.st.ticks
        # STRUCTURAL GUARD 1: ONLY rent on exact daily rent-observation ticks (t % 24 == 23)
        # AND NEVER when next tick is Monday ((t + 1) % 168 == 0) when heat changes before _apply!
        if not self.st.rent_fresh or (t + 1) % 168 == 0:
            return
        if not self.st.rent_prices or self.st.cash_avail < 18.0:
            return

        # Active rented compute that won't expire within 30 ticks
        active_rent_comp = sum(
            self.st.gpu[l["m"]]["power"] * l["n"]
            for l in self.st.leases.values()
            if l["end"] > t + 30
        )

        # Serving deficit (highest priority -> zero drops, rep = 0.0!)
        serve_short = max(0.0, serve_need - own_serve_comp - active_rent_comp)

        # Training rent boost:
        #   - In Q0 (t < 1500, before G2 launches): balanced sprint (<= $0.032/tick) preserving cash for Q1 G2!
        #   - After Q0: initiate/renew training leases on Tue & Fri (t % 168 in (23, 95)) with cashflow-gated cap
        #     so 55-65% of revenue compounds into owned G3/G4/G5 silicon while training NEVER starves!
        train_short = 0.0
        hr_room = 0.0
        allow_train_day = (t < 1500) or (t % 168 in (23, 95))
        if not in_q11 and allow_train_day and train_want_extra_comp > 60.0:
            if t < 1500:
                cash_floor = 65.0
                max_hr = 0.032
            elif t < 4300:
                cash_floor = 28.0
                max_hr = max(0.036, self.st.rev_ewma * 0.48)
            else:
                cash_floor = 32.0
                max_hr = max(0.018, self.st.rev_ewma * (0.48 if self.st.cash_avail > 140.0 else 0.36))
            if self.st.cash_avail > cash_floor and max_hr > 0.005:
                cur_hr = sum(
                    self.st.rent_prices.get(l["m"], self.st.gpu[l["m"]]["price"] / 4000.0)
                    * (0.4 if l["kind"] == "spot" else 1.0)
                    * l["n"]
                    for l in self.st.leases.values()
                    if l["end"] > t + 30
                )
                hr_room = max(0.0, max_hr - cur_hr)
                if hr_room > 0.002:
                    train_short = max(0.0, train_want_extra_comp - max(0.0, active_rent_comp - max(0.0, serve_need - own_serve_comp)))

        need_comp = serve_short + train_short
        if need_comp <= 80.0:
            return

        # Pick cheapest launched tier with SAFE pool headroom (u <= 0.82 -> >= 18% free pool!)
        opts = []
        exec_q = (t + 1) // 2190
        for m, pr in self.st.rent_prices.items():
            if self.st.rent_blocked_until.get(m, 0) > t:
                continue
            meta = self.st.gpu[m]
            if exec_q < meta["launch_q"]:
                continue
            u = (pr * 8000.0 / meta["price"] - 1.0) / 2.0
            u_cap = 0.84 if serve_short > 80.0 else 0.82
            if u >= u_cap:
                continue
            pool = meta["supply_q"] * 2.0
            safe_room = int(pool * max(0.0, (u_cap + 0.04) - u))
            if safe_room < 16:
                continue
            cost_per_pow = pr / meta["power"]
            score = cost_per_pow * (1.0 + 0.35 / meta["power"])
            opts.append((score, cost_per_pow, m, safe_room, meta["power"], pr))

        if not opts:
            return
        opts.sort()

        ticks_len = min(max(24, max_lease_end - t), 168)
        if ticks_len < 24:
            ticks_len = min(max(24, T - t), 168)
        if ticks_len <= 0:
            return

        rents = []
        rem_serve = serve_short
        rem_train = train_short
        for _, _, m, safe_room, pw, pr in opts[:2]:
            if rem_serve <= 40.0 and rem_train <= 60.0:
                break
            n_srv = int(math.ceil(rem_serve / pw)) if rem_serve > 40.0 else 0
            n_trn = 0
            if rem_train > 60.0 and hr_room > pr * 12:
                n_trn = min(int(math.ceil(rem_train / pw)), int(hr_room / max(1e-9, pr)))
            n = min(safe_room, n_srv + n_trn)
            if n < 12:
                continue
            self.st.pending_rent_meta[m] = ("ondemand", ticks_len)
            rents.append({"model": m, "n": n, "kind": "ondemand", "ticks": ticks_len})
            used_srv = min(rem_serve, n * pw)
            rem_serve -= used_srv
            rem_train = max(0.0, rem_train - max(0.0, n * pw - used_srv))
            hr_room = max(0.0, hr_room - max(0, n - n_srv) * pr)
        if rents:
            act.setdefault("rent", []).extend(rents)


class CompetitiveMarginPricer:
    """Competitive H2H pricing (marginal cost + opponent margin squeezing) & Q11 finite-horizon DP."""

    def __init__(self, st: ArbitrageLedger):
        self.st = st

    def _rival_snapshot(self) -> tuple[list[float], float | None]:
        rivals: dict[str, float] = {}
        me = self.st.me
        opp = self.st.opp
        mu = self.st.model_u
        for m in self.st.models.values():
            own = m["owner"]
            if own == me:
                continue
            if own == opp and float(m.get("pub", 0.0)) < 0.145:
                continue
            u = mu(float(m["pub"]), float(m["price"]))
            if own not in rivals or u > rivals[own]:
                rivals[own] = u
        opp_u = rivals.get(opp)
        return list(rivals.values()), opp_u

    def _shares_from_snapshot(
        self, rival_us: list[float], opp_u: float | None, pub: float, price: float, drop: float = 0.0
    ) -> tuple[float, float]:
        u_me = self.st.model_u(pub, price) + self.st.rep - self.st.u_drop * drop
        mx = u_me
        for rv in rival_us:
            if rv > mx:
                mx = rv
        e_me = math.exp(u_me - mx)
        e_opp = math.exp(opp_u - mx) if opp_u is not None else 0.0
        z = e_me
        for rv in rival_us:
            z += math.exp(rv - mx)
        if z <= 0.0:
            return 0.0, 0.0
        return e_me / z, e_opp / z

    def market_shares_at(self, pub: float, price: float, drop: float = 0.0) -> tuple[float, float]:
        """Return (target_share_me, target_share_opp) under logit competition."""
        rival_us, opp_u = self._rival_snapshot()
        return self._shares_from_snapshot(rival_us, opp_u, pub, price, drop)

    def target_share(self, pub: float, price: float, drop: float = 0.0) -> float:
        return self.market_shares_at(pub, price, drop)[0]

    def marginal_costs(self, fl: dict) -> tuple[float, float]:
        """Return (mc_own, mc_rent) in $/M tokens: tiny elec on owned GPUs vs cloud rent cost."""
        n_b = float(fl.get("N") or 30.0)
        per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
        elec_r0 = self.st.elec_prices[0] if self.st.elec_prices else 0.0008
        elec_per_g1h = 0.18 * self.st.pue() / 1000.0 * elec_r0
        if self.st.rent_prices:
            cheapest_rent = min(pr / self.st.gpu[m]["power"] for m, pr in self.st.rent_prices.items())
        else:
            cheapest_rent = 0.000010
        mc_own = per * elec_per_g1h * 1000.0
        mc_rent = per * cheapest_rent * 1000.0
        return mc_own, mc_rent

    def _piecewise_margin(self, s_val: float, p: float, own_cap: float, tot_cap: float, mc_own: float, mc_rent: float) -> float:
        s_own = min(s_val, own_cap)
        s_rent = max(0.0, min(s_val, tot_cap) - own_cap)
        drop_pen = max(0.0, s_val - tot_cap) * max(p, 8.0) * 5.0
        return s_own * (p - mc_own) + s_rent * (p - mc_rent) - drop_pen

    def _opp_margin_est(self) -> tuple[float, float]:
        """Return (opp_price, opp_mc) if rival has a live API flagship, else (0.0, 0.0)."""
        ofl = self.st.opp_flagship()
        if not ofl:
            return 0.0, 0.0
        op = min(42.0, float(ofl["price"]))
        on = float(ofl.get("N") or 30.0)
        omc = min(op * 0.35, 25.0 * on * 0.6 * 0.000002 * 1000.0)
        return op, omc

    def solve_price(self, fl: dict, own_s_cap: float, tot_s_cap: float) -> float:
        t = self.st.t
        T = self.st.ticks
        pub = float(fl["pub"])
        cur_p = float(fl["price"])
        mc_own, mc_rent = self.marginal_costs(fl)
        p_floor = max(4.2, mc_own * 1.5)
        if self.st.drop > 0.01:
            return self._apply_antishock(pub, max(p_floor, min(96.0, cur_p * 1.38)))

        q11_start = T - int(self.st.rules.get("GM_WINDOW", 2190))
        opp_p, opp_mc = self._opp_margin_est()
        opp_m = max(0.0, opp_p - opp_mc)

        # Phase 1: Pre-Q11 Share-Building Runway (t in [q11_start - 1176, q11_start))
        if q11_start - 1176 <= t < q11_start:
            rival_us, opp_u = self._rival_snapshot()
            best_v, best_p = -1e18, max(p_floor, cur_p)
            lam_runway = 0.50 if opp_m > 1.5 else 0.0
            for i in range(68):
                p = p_floor * (1.055 ** i)
                if p > 62.0:
                    break
                s_me, s_opp = self._shares_from_snapshot(rival_us, opp_u, pub, p)
                gm = self._piecewise_margin(s_me, p, own_s_cap * 0.94, tot_s_cap * 0.94, mc_own, mc_rent)
                serv = min(s_me, own_s_cap * 0.94)
                val = gm + serv * 5.0 - lam_runway * s_opp * opp_m
                if val > best_v:
                    best_v, best_p = val, p
            return self._apply_antishock(pub, best_p)

        # Phase 2: Inside Q11 GM_2190 Window -> Competitive H2H Finite-Horizon DP!
        if t >= q11_start:
            w_rem = max(1, min(14, (T - t + 167) // 168))
            p_opt = self._dp_q11_competitive(
                pub, max(p_floor, cur_p), self.st.my_share, self.st.opp_share,
                mc_own, mc_rent, opp_m, own_s_cap, tot_s_cap, w_rem, p_floor,
            )
            return self._apply_antishock(pub, max(p_floor, p_opt))

        # Phase 3: Cashflow Compounding (t < q11_start - 1176)
        rival_us, opp_u = self._rival_snapshot()
        best_v, best_p = -1e18, max(p_floor, 8.0)
        for i in range(68):
            p = p_floor * (1.055 ** i)
            if p > 88.0:
                break
            s_me, _ = self._shares_from_snapshot(rival_us, opp_u, pub, p)
            val = self._piecewise_margin(s_me, p, own_s_cap * 0.95, tot_s_cap * 0.95, mc_own, mc_rent)
            if val > best_v:
                best_v, best_p = val, p
        return self._apply_antishock(pub, best_p)

    def _dp_q11_competitive(
        self,
        pub: float,
        cur_p: float,
        cur_s: float,
        cur_so: float,
        mc_own: float,
        mc_rent: float,
        opp_m: float,
        own_cap: float,
        tot_cap: float,
        w_rem: int,
        p_floor: float,
    ) -> float:
        """Fast precomputed finite-horizon Q11 DP maximizing signed (GM_me - lambda * GM_opp)."""
        prices = [round(3.5 * (1.13 ** i), 2) for i in range(28) if 3.5 * (1.13 ** i) <= 96.0]
        n_p = len(prices)
        s_grid = [i * 0.05 for i in range(21)]
        n_s = len(s_grid)

        rival_us, opp_u = self._rival_snapshot()
        t_me = [self._shares_from_snapshot(rival_us, opp_u, pub, p)[0] for p in prices]
        other_cur = max(1e-6, 1.0 - cur_s)
        opp_of_rest_cur = _clip(cur_so / other_cur, 0.0, 0.95) if opp_m > 0.5 else 0.0
        lam = 0.85 if opp_m > 0.5 else 0.0

        # Precompute (si, pj) -> immediate payoff, next share bin, and legal pj upper bound per pi!
        # Eliminates 95% of Python overhead (35x speedup)!
        pj_lo = 0
        while pj_lo < n_p and prices[pj_lo] < p_floor:
            pj_lo += 1
        if pj_lo >= n_p:
            pj_lo = n_p - 1
        pj_hi_of = []
        for pi in range(n_p):
            p_max = min(96.0, prices[pi] * 1.45) + 1e-6
            hi = pj_lo
            while hi < n_p and prices[hi] <= p_max:
                hi += 1
            pj_hi_of.append(max(pj_lo + 1, hi))

        imm_base = [[0.0] * n_p for _ in range(n_s)]
        nxt_idx = [[0] * n_p for _ in range(n_s)]
        for si in range(n_s):
            s_val = s_grid[si]
            opp_pen = lam * (1.0 - s_val) * opp_of_rest_cur * opp_m
            s_own = min(s_val, own_cap)
            s_rent = max(0.0, min(s_val, tot_cap) - own_cap)
            s_drop = max(0.0, s_val - tot_cap)
            row_imm = imm_base[si]
            row_nxt = nxt_idx[si]
            for pj in range(pj_lo, n_p):
                pv = prices[pj]
                gm = s_own * (pv - mc_own) + s_rent * (pv - mc_rent) - s_drop * max(pv, 8.0) * 5.0
                row_imm[pj] = gm - opp_pen
                sn = s_val + _clip(t_me[pj] - s_val, -0.15, 0.15)
                row_nxt[pj] = max(0, min(n_s - 1, int(round(sn * 20.0))))

        dp_next = [[0.0] * n_p for _ in range(n_s)]
        first_choice = [[pj_lo] * n_p for _ in range(n_s)]

        for step_k in range(1, w_rem + 1):
            q_wt = 1.0068 ** (w_rem - step_k)
            is_last = (step_k == w_rem)
            dp_cur = [[-1e18] * n_p for _ in range(n_s)]
            for si in range(n_s):
                row_imm = imm_base[si]
                row_nxt = nxt_idx[si]
                # Precompute value of choosing pj from state si: q_wt * imm[si][pj] + dp_next[nxt[si][pj]][pj]
                val_pj = [q_wt * row_imm[pj] + dp_next[row_nxt[pj]][pj] for pj in range(n_p)]
                # Prefix argmax over pj in [pj_lo .. hi-1] -> O(n_p) instead of O(n_p^2)!!
                best_v = -1e18
                best_j = pj_lo
                pref_v = [0.0] * (n_p + 1)
                pref_j = [pj_lo] * (n_p + 1)
                for pj in range(pj_lo, n_p):
                    vp = val_pj[pj]
                    if vp > best_v:
                        best_v = vp
                        best_j = pj
                    pref_v[pj + 1] = best_v
                    pref_j[pj + 1] = best_j
                cur_row = dp_cur[si]
                fc_row = first_choice[si]
                for pi in range(n_p):
                    hi = pj_hi_of[pi]
                    cur_row[pi] = pref_v[hi]
                    if is_last:
                        fc_row[pi] = pref_j[hi]
            dp_next = dp_cur

        si0 = max(0, min(n_s - 1, int(round(cur_s * 20.0))))
        pi0 = min(range(n_p), key=lambda k: abs(prices[k] - cur_p))
        return prices[first_choice[si0][pi0]]

    def _apply_antishock(self, pub: float, p: float) -> float:
        if self.st.anchor_u is not None:
            u_pub = float(self.st.rules.get("U_PUB", 6.0))
            u_pr = float(self.st.rules.get("U_PRICE", 1.5))
            # Strict < 1.50 ratio (1.46x margin guarantees zero mid-week price shock!)
            min_u = self.st.anchor_u - u_pr * math.log(1.46)
            ln_p_max = (u_pub * pub - min_u) / u_pr
            p_max_safe = math.exp(min(4.605, ln_p_max))
            p = min(p, p_max_safe)
        return round(_clip(p, 1.0, 96.0), 3)


class CapitalArbitrageur:
    """Top-level orchestrator for Capital Arbitrageur."""

    TECH_PLAN = (
        "synth", "algo_eff_1", "algo_eff_2", "filter:web", "early_supply",
        "algo_eff_3", "infer_opt_1", "cooling", "infer_opt_2",
        "curriculum", "stable",
        "filter:code", "filter:multiling", "filter:books",
        "filter:science", "filter:dialog", "filter:math",
    )

    T_PRIOR = T_PRIOR
    DOM_W = DOM_W
    T_MAT = T_MAT
    DOM_TW_TUP = DOM_TW_TUP

    def __init__(self, init: dict):
        self.st = ArbitrageLedger(init)
        self.desk = CapitalArbitrageDesk(self.st)
        self.pricer = CompetitiveMarginPricer(self.st)

        # Phase 0: Fast exact LR + B_crit + Scaling-Law calibration (~40 ticks total)
        self.probe_stage = 0
        self.probe_start = 0
        self.lr_probes: dict[str, dict] = {}
        self.sc_probes: dict[str, dict] = {}
        self.eta_c = 0.0019
        self.bc0 = 380.0
        self.alpha_hat = 0.34
        self.beta_hat = 0.30
        self.A_hat = 0.45
        self.B_hat = 1.15
        self.seed_model_released = False
        self.synth_bootstrapped = False

        # Training pipeline:
        #   stage 0: Gen 1 fast compact distilled WSD cash cow (t ~ 42 .. 1460)
        #   stage 1: Gen 2 uncapped synth+distill algo_eff_2 WSD bridge flagship (t ~ 1460 .. 4250)
        #   stage 2: Main continuous pre-audited algo_eff_3 cosine masterpiece (t ~ 4250 .. 26175):
        #            0.968 cosine ratchet releases at t = 8000, 12300, 16800, 20800, 23650 (+1.87% bonus
        #            on EVERY intermediate release!) + free spare-owned-GPU Q11 scaling to t = 26175 (+2.0% bonus!)
        T = self.st.ticks
        self.q11_start = max(1000, T - int(self.st.rules.get("GM_WINDOW", 2190)))
        self.main_target_finish = max(1200, T - 105)  # 26175 -> live 26199 -> online_ticks=81 >= 72!
        self.stage = 0  # 0=Gen1, 1=Gen2, 2=MainCosine, 3=Done

        self.cur_run: str | None = None
        self.cur_N = 8.0
        self.cur_start = 0
        self.cur_end = 1460 if T >= 20000 else max(120, T - 105)
        self.cur_sched = "wsd"
        self.cur_mix: dict[str, float] = {}
        self.cur_prev_D = 0.0
        self.cur_S: dict[str, float] = {p: 0.0 for p in ALL_POOLS}
        self.cur_ckpt_S: dict[int, dict[str, float]] = {0: {p: 0.0 for p in ALL_POOLS}}
        self.cur_last_ckpt = 0
        self.cur_nonzero_ckpt: set[str] = set()
        self.wsd_locked: set[str] = set()
        self.ever_annealed: set[str] = set()
        self.released_runs: set[str] = set()
        self.main_rel_times = [8000, 12300, 16800, 20800, 23650]
        self.main_rel_idx = 0
        self.last_rel_D = 0.0
        self.pending_cosine_extend: str | None = None
        self.gen1_synth_mixed = False
        self.main_prepped: tuple[str, float, dict[str, float]] | None = None
        self.main_prep_attempted = False
        self.audit_sent: set[str] = set()
        self.run_seq = 0
        self.active_serve: dict[str, int] | None = None
        self.active_serve_comp: float = 0.0
        self.serve_auto = True
        self.train_models: set[str] = set()
        self.research_cur: tuple[str, ...] = ()
        self.last_mix_t = -10**9
        self._ckpt_cache: dict[int, tuple[int, float]] = {}
        self.cur_fl_N: float = 0.0

    def eta_star(self, n: float) -> float:
        return self.eta_c * (n ** -0.3)

    # ================================================================ Exact Training Efficiency Math
    def bc_at(self, d: float) -> float:
        return self.bc0 * ((1.0 + max(0.0, d) / 50.0) ** 0.4)

    def _cycle_eff(self, ncards: int, k: int) -> float:
        if ncards <= 0:
            return 0.0
        mtbf = float(self.st.rules.get("MTBF", 50000.0))
        f = ncards / mtbf
        if f <= 1e-9:
            return (k - 0.2) / k
        p = 1.0 - f
        if p <= 0.05:
            return 0.01
        pk = p ** k
        if pk <= 1e-12:
            return 0.0
        expected_ticks = ((1.0 - pk) / pk) * (1.0 / f + (1.0 + p) / (p * p))
        if expected_ticks <= 1e-9:
            return 0.0
        return max(0.0, (k - 0.2) / expected_ticks)

    def opt_ckpt_and_eff(self, ncards: int) -> tuple[int, float]:
        if ncards <= 0:
            return 24, 0.0
        cached = self._ckpt_cache.get(ncards)
        if cached is not None:
            return cached
        mtbf = float(self.st.rules.get("MTBF", 50000.0))
        f = max(1e-7, ncards / mtbf)
        k0 = int(max(2, min(48, round(math.sqrt(0.4 / f)))))
        best_k, best_e = k0, -1.0
        for k in range(max(2, k0 - 2), min(48, k0 + 3)):
            e = self._cycle_eff(ncards, k)
            if e > best_e:
                best_k, best_e = k, e
        res = (best_k, best_e)
        if len(self._ckpt_cache) < 4096:
            self._ckpt_cache[ncards] = res
        return res

    def batch_waste(self, ncards: int, d: float) -> float:
        if ncards <= 0:
            return 0.0
        b = max(1, (ncards + 1) // 2)
        util = min(1.0, 2.0 * b / ncards)
        bc = self.bc_at(d)
        waste = (min(b, bc) + 0.5 * max(0.0, b - bc)) / b
        return util * waste

    def exact_throughput(self, cards: dict[str, int], d: float) -> float:
        n = sum(cards.values())
        if n <= 0:
            return 0.0
        raw = self.st.compute_of(cards)
        _, ce = self.opt_ckpt_and_eff(n)
        return raw * self.batch_waste(n, d) * ce

    def select_best_train_cards(self, pool: dict[str, int], d: float) -> dict[str, int]:
        """Select optimal card subset (newest tiers first, allowing partial count on marginal tier)."""
        best_map: dict[str, int] = {}
        best_tpt = 0.0
        cur_map: dict[str, int] = {}
        for m in reversed(GPUS):
            avail = pool.get(m, 0)
            if avail <= 0:
                continue
            # Test full addition + 25/50/75% partial additions (crucial when n crosses 2*bc or MTBF peak)
            trial_counts = sorted({avail, max(1, avail // 4), max(1, avail // 2), max(1, (3 * avail) // 4)})
            for cnt in trial_counts:
                trial = dict(cur_map)
                trial[m] = cnt
                tpt = self.exact_throughput(trial, d)
                if tpt > best_tpt * 1.002:
                    best_tpt = tpt
                    best_map = trial
            # Continue greedy chain with full tier if it beat current best
            full_t = dict(cur_map)
            full_t[m] = avail
            if self.exact_throughput(full_t, d) >= best_tpt * 0.99:
                cur_map = full_t
            else:
                break
        return best_map

    # ================================================================ Phase 0: Fast Scientific Probes
    def step_probes(self, act: dict) -> bool:
        if self.probe_stage >= 4:
            return True
        for e in self.st.ev:
            if e.get("type") == "done":
                rn = e["run"]
                ml = sum(e["loss"]) / len(e["loss"])
                if rn in self.lr_probes:
                    self.lr_probes[rn]["loss"] = ml
                elif rn in self.sc_probes:
                    self.sc_probes[rn]["loss"] = ml

        # Capture exact bc0 from our batch=1000 probe run's noiseless eff!
        if self.probe_stage == 1:
            r_bc = self.st.runs_obs.get("arb_lr0")
            if r_bc is not None and float(r_bc[5]) > 0.50:
                self.bc0 = _clip(2000.0 * (float(r_bc[5]) - 0.5), 185.0, 650.0)

        c0 = float(self.st.rules.get("DEFAULT_LR_C", 0.0010))

        # Stage 0: Spike-free LR + exact B_crit + zero-contamination synth seed model (2 ticks!)
        if self.probe_stage == 0:
            mx_real = {d: 1.0 / len(REAL_POOLS) for d in REAL_POOLS}
            runs = []
            # 4 probes at k=0.05, 4 probes at k=1.18 (ALL <= k*_min=1.20 -> ZERO spikes/divergence!)
            for idx, k in enumerate((0.05, 0.05, 0.05, 0.05, 1.18, 1.18, 1.18, 1.18)):
                name = f"arb_lr{idx}"
                self.lr_probes[name] = {"k": k, "loss": None}
                runs.append({
                    "name": name, "params_b": 0.05, "tokens_b": 0.5,
                    "lr": c0 * k * (0.05 ** -0.3),
                    "batch": 1000 if idx == 0 else 16,
                    "gpus": {"G1": 32},
                    "sched": "const", "ckpt_every": 2, "mix": mx_real,
                })
            # Tiny zero-contamination synthetic-data seed model (N=0.02B, mix=synth -> cont=0, C=0)
            runs.append({
                "name": "arb_seed", "params_b": 0.02, "tokens_b": 0.5,
                "lr": c0 * 1.18 * (0.02 ** -0.3), "batch": 16,
                "gpus": {"G1": 32}, "sched": "const", "ckpt_every": 2,
                "mix": {"synth": 1.0},
            })
            act.setdefault("run", []).extend(runs)
            self.probe_stage = 1
            self.probe_start = self.st.t
            return False

        if self.probe_stage == 1:
            # Release tiny synth seed model as soon as done (priced $100, C=0 -> invisible to API!)
            if not self.seed_model_released:
                r_s = self.st.runs_obs.get("arb_seed")
                if r_s and r_s[1] == "done" and r_s[6] > 0:
                    act.setdefault("release", []).append({"run": "arb_seed", "price": 100.0})
                    self.seed_model_released = True
            if all(v["loss"] is not None for v in self.lr_probes.values()) or self.st.t - self.probe_start > 15:
                lo_vals = [v["loss"] for v in self.lr_probes.values() if v["k"] < 0.5 and v["loss"] is not None]
                hi_vals = [v["loss"] for v in self.lr_probes.values() if v["k"] > 0.5 and v["loss"] is not None]
                if lo_vals and hi_vals:
                    y_lo = sum(lo_vals) / len(lo_vals)
                    y_hi = sum(hi_vals) / len(hi_vals)
                    k_lo, k_hi = 0.05, 1.18
                    num = 0.08 * (math.log(k_hi) ** 2 - math.log(k_lo) ** 2) - (y_hi - y_lo)
                    den = 0.16 * (math.log(k_hi) - math.log(k_lo))
                    k_hat = _clip(math.exp(num / den), 1.18, 2.82)
                    self.eta_c = c0 * k_hat
                # Stop any unfinished probe runs cleanly
                for name in list(self.lr_probes.keys()) + ["arb_seed"]:
                    r = self.st.runs_obs.get(name)
                    if r and r[1] not in ("done", "stopped"):
                        act.setdefault("run_ctl", []).append({"name": name, "op": "stop"})
                self.probe_stage = 2
            return False

        # Stage 2: Fast 12-point Scaling Law (alpha, beta, A, B) probe (~32 ticks total!)
        if self.probe_stage == 2:
            if self.st.ticks < 8000:
                self.probe_stage = 4
                return True
            pts = (
                (0.02, 0.2), (0.02, 2.0), (0.02, 25.0),
                (0.15, 0.2), (0.15, 2.0), (0.15, 18.0),
                (1.5, 0.2), (1.5, 2.0), (1.5, 9.0),
                (12.0, 0.2), (12.0, 1.0), (12.0, 2.5),
            )
            tot_nd = sum(n * d for n, d in pts)
            mx_real = {d: 1.0 / len(REAL_POOLS) for d in REAL_POOLS}
            runs = []
            avail_g1 = max(256, self.st.own_usable().get("G1", 512))
            for idx, (n_p, d_p) in enumerate(pts):
                name = f"arb_sc{idx}"
                cards = max(8, int(avail_g1 * (n_p * d_p) / tot_nd))
                self.sc_probes[name] = {"N": n_p, "D": d_p, "loss": None}
                runs.append({
                    "name": name, "params_b": n_p, "tokens_b": d_p,
                    "lr": self.eta_star(n_p) * 0.96, "batch": max(4, (cards + 1) // 2),
                    "gpus": {"G1": cards}, "sched": "const", "ckpt_every": 12, "mix": mx_real,
                })
            act.setdefault("run", []).extend(runs)
            self.probe_stage = 3
            self.probe_start = self.st.t
            return False

        if self.probe_stage == 3:
            done_all = all(v["loss"] is not None for v in self.sc_probes.values())
            if done_all or self.st.t - self.probe_start > 90:
                obs = [(v["N"], v["D"], v["loss"]) for v in self.sc_probes.values() if v["loss"] is not None]
                if len(obs) >= 8:
                    self._fit_scaling_law(obs)
                for name in self.sc_probes:
                    r = self.st.runs_obs.get(name)
                    if r and r[1] not in ("done", "stopped"):
                        act.setdefault("run_ctl", []).append({"name": name, "op": "stop"})
                self.probe_stage = 4
                return True
        return False

    def _d_eff_proxy(self, d: float, u_mult: float = 1.0) -> float:
        s = 0.0
        for p in REAL_POOLS:
            u = max(10.0, self.st.U.get(p, 100.0)) * u_mult
            per = d / len(REAL_POOLS)
            s += 4.0 * u * (1.0 - math.exp(-per / (4.0 * u)))
        return max(0.01, s / len(REAL_POOLS))

    def _fit_scaling_law(self, obs: list[tuple[float, float, float]]) -> None:
        best_sse = 1e18
        best_p = None
        # Use IDENTICAL _d_eff_with_pools units (has_synth=False, has_distill=False) as choose_optimal_N!
        obs_de = [(n, self._d_eff_with_pools(d, False, False), y) for n, d, y in obs]
        for ai in range(28, 41):
            al = ai / 100.0
            for bi in range(25, 36):
                be = bi / 100.0
                S = [[0.0] * 4 for _ in range(3)]
                for n, de, y in obs_de:
                    v = (1.0, n ** (-al), de ** (-be))
                    for i in range(3):
                        for j in range(3):
                            S[i][j] += v[i] * v[j]
                        S[i][3] += v[i] * y
                ok = True
                for i in range(3):
                    piv = max(range(i, 3), key=lambda r: abs(S[r][i]))
                    S[i], S[piv] = S[piv], S[i]
                    if abs(S[i][i]) < 1e-12:
                        ok = False
                        break
                    for r in range(3):
                        if r != i:
                            f = S[r][i] / S[i][i]
                            for c in range(i, 4):
                                S[r][c] -= f * S[i][c]
                if not ok:
                    continue
                e0, a_h, b_h = (S[i][3] / S[i][i] for i in range(3))
                if not (0.20 <= a_h <= 0.78 and 0.45 <= b_h <= 2.80):
                    continue
                sse = sum((y - e0 - a_h * (n ** (-al)) - b_h * (de ** (-be))) ** 2 for n, de, y in obs_de)
                if sse < best_sse:
                    best_sse = sse
                    best_p = (al, be, a_h, b_h)
        if best_p is not None:
            self.alpha_hat, self.beta_hat, self.A_hat, self.B_hat = best_p

    def _d_eff_with_pools(self, d: float, has_synth: bool, has_distill: bool) -> float:
        sf = 0.295 if has_synth else 0.0
        df = 0.096 if has_distill else 0.0
        rf = max(0.25, 1.0 - sf - df)
        # Water-filled real pool effective tokens (including estimated q and cross-domain transfer)
        q_real_avg = sum(self.st.q_est.get(p, 0.95) for p in REAL_POOLS) / len(REAL_POOLS)
        u_real_avg = sum(self.st.U.get(p, 100.0) for p in REAL_POOLS) / len(REAL_POOLS)
        s_real = rf * d / len(REAL_POOLS)
        real_eff = 4.0 * u_real_avg * (1.0 - math.exp(-s_real / max(1.0, 4.0 * u_real_avg))) * q_real_avg
        de = real_eff * 1.50
        if has_synth:
            us = max(2000.0, self.st.U.get("synth", 25000.0))
            ss = sf * d
            qs = max(0.58, self.st.q_est.get("synth", 0.62))
            de += 0.32 * qs * 4.0 * us * (1.0 - math.exp(-ss / (4.0 * us)))
        if has_distill:
            ud = max(400.0, self.st.U.get("distill", 2000.0))
            sd = df * d
            qd = max(0.85, self.st.q_est.get("distill", 1.02))
            de += 0.50 * qd * 4.0 * ud * (1.0 - math.exp(-sd / (4.0 * ud)))
        return max(0.01, de)

    def _pred_domain_loss(self, n: float, d: float, algo_m: float, has_synth: bool, has_distill: bool) -> float:
        """Predict mean 7-domain loss at (N, D) using exact waterfilled mix & probed (alpha, beta, A, B)."""
        df = 0.096 if has_distill else 0.0
        sf = 0.295 if has_synth else 0.0
        u_map = dict(self.st.U)
        q_map = dict(self.st.q_est)
        if has_synth:
            u_map["synth"] = max(25000.0, u_map.get("synth", 40000.0))
            q_map["synth"] = max(0.58, q_map.get("synth", 0.60))
        if has_distill:
            u_map["distill"] = max(1600.0, u_map.get("distill", 1800.0))
            q_map["distill"] = max(0.95, q_map.get("distill", 1.05))
        mix = self._waterfill_static(u_map, q_map, d, df, sf)
        effs = {}
        for p, f in mix.items():
            s = d * f
            u = u_map.get(p, 0.0)
            effs[p] = (4.0 * u * (1.0 - math.exp(-s / (4.0 * u))) * q_map.get(p, 0.9)) if (s > 0.0 and u > 0.0) else 0.0
        an = self.A_hat / ((algo_m * n) ** self.alpha_hat)
        tot = 0.0
        for dom in REAL_POOLS:
            row = self.T_PRIOR[dom]
            de = 0.0
            for p, v in effs.items():
                if v > 0.0:
                    de += row[p] * v
            tot += an + self.B_hat / (max(0.01, de) ** self.beta_hat)
        return tot / len(REAL_POOLS)

    def _waterfill_fast(
        self,
        u_list: list[float],
        q_list: list[float],
        s_init: list[float],
        d_rem: float,
        df: float,
        sf: float,
        steps: int,
    ) -> dict[str, float]:
        """List-indexed exact analytical-gradient water-filling (zero dict/generator overhead)."""
        rem_f = max(0.25, 1.0 - df - sf)
        sim_S = list(s_init)
        real_alloc = [0.0] * 7
        step_tok = (rem_f * d_rem) / steps
        step_s = (sf * d_rem) / steps
        step_d = (df * d_rem) / steps
        inv_4u = [0.25 / max(5.0, u_list[i]) for i in range(7)]
        u4q = [4.0 * u_list[i] * q_list[i] if u_list[i] > 0.0 else 0.0 for i in range(9)]
        inv_4u_all = [0.25 / u_list[i] if u_list[i] > 0.0 else 0.0 for i in range(9)]
        q_real = [max(0.35, q_list[i]) for i in range(7)]
        t_mat = self.T_MAT
        dom_tw = self.DOM_TW_TUP
        pw = -self.beta_hat - 1.0
        wb_cap = 0.45 * max(1.0, d_rem)
        wb_used = 0.0
        exp = math.exp
        effs = [0.0] * 9
        des_pw = [0.0] * 7

        for _ in range(steps):
            sim_S[7] += step_s
            sim_S[8] += step_d
            for pi in range(9):
                sv = sim_S[pi]
                if sv > 0.0 and u4q[pi] > 0.0:
                    effs[pi] = u4q[pi] * (1.0 - exp(-sv * inv_4u_all[pi]))
                else:
                    effs[pi] = 0.0
            e0, e1, e2, e3, e4, e5, e6, e7, e8 = effs
            for di in range(7):
                r = t_mat[di]
                de = r[0]*e0 + r[1]*e1 + r[2]*e2 + r[3]*e3 + r[4]*e4 + r[5]*e5 + r[6]*e6 + r[7]*e7 + r[8]*e8
                des_pw[di] = (de if de > 0.05 else 0.05) ** pw
            dp0, dp1, dp2, dp3, dp4, dp5, dp6 = des_pw
            best_k = 1
            best_g = -1.0
            wb_ok = (wb_used + step_tok) <= wb_cap
            for ki in range(7):
                if not wb_ok and (ki == 0 or ki == 3):
                    continue
                dE = exp(-sim_S[ki] * inv_4u[ki]) * q_real[ki]
                tw = dom_tw[ki]
                g = dE * (tw[0]*dp0 + tw[1]*dp1 + tw[2]*dp2 + tw[3]*dp3 + tw[4]*dp4 + tw[5]*dp5 + tw[6]*dp6)
                if g > best_g:
                    best_g = g
                    best_k = ki
            sim_S[best_k] += step_tok
            real_alloc[best_k] += step_tok
            if best_k == 0 or best_k == 3:
                wb_used += step_tok

        tot_r = sum(real_alloc) or 1.0
        out: dict[str, float] = {}
        for ki in range(7):
            v = rem_f * real_alloc[ki] / tot_r
            if v > 1e-4:
                out[REAL_POOLS[ki]] = v
        if df > 1e-4:
            out["distill"] = df
        if sf > 1e-4:
            out["synth"] = sf
        z = sum(out.values()) or 1.0
        return {k: round(v / z, 5) for k, v in out.items()}

    def _waterfill_static(self, u_map: dict[str, float], q_map: dict[str, float], d: float, df: float, sf: float) -> dict[str, float]:
        u_list = [u_map.get(p, 0.0) for p in ALL_POOLS]
        q_list = [q_map.get(p, 0.9) for p in ALL_POOLS]
        return self._waterfill_fast(u_list, q_list, [0.0] * 9, d, df, sf, 20)

    def choose_optimal_N(
        self,
        budget_g1h: float,
        algo_m: float,
        n_min: float,
        n_max: float,
        serve_cap_n: float,
        has_synth: bool = True,
        has_distill: bool = True,
        mid_budget_g1h: float = 0.0,
        serve_g1h_per_b: float = 0.0,
        mid_serve_g1h_per_b: float = 0.0,
    ) -> float:
        """Choose capital-optimal N coupling training compute, serving compute drain, and Q11 capacity."""
        kc = float(self.st.rules.get("KC", 300.0))
        best_n, best_score = n_min, 1e18
        n_pts = 48 if serve_g1h_per_b > 0.0 else 40
        for i in range(n_pts):
            n = n_min * ((n_max / max(n_min, 0.1)) ** (i / float(n_pts - 1)))
            c_train = max(budget_g1h * 0.20, budget_g1h - serve_g1h_per_b * n)
            d = max(5.0, c_train / (kc * n))
            if serve_g1h_per_b > 0.0:
                loss_end = self._pred_domain_loss(n, d, algo_m, has_synth, has_distill)
            else:
                de = self._d_eff_with_pools(d, has_synth, has_distill)
                loss_end = self.A_hat / ((algo_m * n) ** self.alpha_hat) + self.B_hat / (de ** self.beta_hat)
            if mid_budget_g1h > 0.0:
                c_mid = max(mid_budget_g1h * 0.25, mid_budget_g1h - mid_serve_g1h_per_b * n)
                d_mid = max(5.0, c_mid / (kc * n))
                if serve_g1h_per_b > 0.0:
                    loss_mid = self._pred_domain_loss(n, d_mid, algo_m, has_synth, has_distill)
                else:
                    de_mid = self._d_eff_with_pools(d_mid, has_synth, has_distill)
                    loss_mid = self.A_hat / ((algo_m * n) ** self.alpha_hat) + self.B_hat / (de_mid ** self.beta_hat)
                loss_part = 0.80 * loss_end + 0.20 * loss_mid
            else:
                loss_part = loss_end
            if serve_g1h_per_b > 0.0:
                # Q11 serving footprint: 300*N G1-h needed to serve 40% share on owned ~30,000 G1-h fleet
                q11_frac = (300.0 * n) / 30000.0
                serve_pen = 0.012 * (q11_frac ** 1.6)
            else:
                serve_pen = 0.014 * (((n - serve_cap_n) / serve_cap_n) ** 1.5) if n > serve_cap_n else 0.0
            sc = loss_part + serve_pen
            if sc < best_score:
                best_score = sc
                best_n = n
        return round(best_n, 3)

    # ================================================================ Data Sourcing & Dynamic Water-Fill Mix
    def maybe_bootstrap_synth(self, act: dict) -> None:
        """Capital arbitrage: mint 40,000 B zero-contamination synth tokens while best_N == 0.02B (~$1.3M!)."""
        if self.synth_bootstrapped or "synth" not in self.st.tech:
            return
        if not self.st.has_released_model():
            return
        best_n = self.st.synth_best_N_est()
        if best_n is None:
            return
        # Strict upper-bound cost check at u=1.0 (G1 rent <= 0.05/8000*3 = 1.875e-5)
        max_unit_cost = best_n * (float(self.st.rules.get("KC", 300.0)) / 3.0) * (self.st.gpu["G1"]["price"] / 8000.0 * 3.0)
        if best_n <= 0.15:
            want = 40000.0
        elif best_n <= 12.0:
            want = 3200.0
        else:
            want = 1400.0
        need = max(0.0, want - self.st.U.get("synth", 0.0) - self.st.synth_pending)
        if need < 20.0:
            self.synth_bootstrapped = True
            return
        max_spend = max(0.0, self.st.cash_avail - 28.0) * 0.35
        can_buy = min(need, max_spend / max(1e-9, max_unit_cost))
        if can_buy >= 20.0:
            tok = round(can_buy, 2)
            act.setdefault("synth", []).append({"tokens_b": tok})
            self.st.spend_cash(tok * max_unit_cost)
            self.st.synth_pending += tok
            self.synth_bootstrapped = True

    def procure_distill(self, act: dict, d_target_add: float, frac: float, max_spend_frac: float = 0.25) -> bool:
        if frac <= 0.0:
            return False
        cur_u = self.st.U.get("distill", 0.0) + self.st.distill_pending
        tm = self.st.best_teacher()
        if not tm:
            return cur_u >= 5.0
        q_tm = 0.5 + 0.8 * float(tm["pub"])
        # Target deep distill pool (~0.75-1.25 epochs) + upgrade pool quality when NPC teachers advance!
        ep_div = 0.70 if self.st.cash_avail > 110.0 else 1.35
        want_u = max(cur_u, (frac * d_target_add) / ep_div)
        if tm["owner"] == "OPEN":
            want_u = max(want_u, 8000.0)
        elif q_tm >= self.st.last_distill_q + 0.08 and self.st.cash_avail > 85.0 and self.st.distill_spent_total < 34.0:
            # High-q NPC teacher upgrade (boosts qavg["distill"] toward 1.10-1.20 across ALL 7 domains!)
            want_u = max(want_u, min(2800.0, cur_u + 650.0))
        need = want_u - cur_u
        if need <= 1.0:
            return cur_u >= 5.0
        unit_p = 0.0 if tm["owner"] == "OPEN" else float(tm["price"]) / 1000.0
        if unit_p > 0.0:
            rem_budget = max(0.0, 36.0 - self.st.distill_spent_total)
            max_spend = min(rem_budget, max(0.0, self.st.cash_avail - 32.0) * max_spend_frac)
            can_buy = min(need, max_spend / unit_p)
            if can_buy < 2.0:
                return cur_u >= 5.0
            need = can_buy
        tok = round(need, 2)
        if tok >= 1.0:
            act.setdefault("distill", []).append({"model": tm["id"], "tokens_b": tok})
            self.st.spend_cash(tok * unit_p)
            self.st.distill_pending += tok
        return (cur_u + tok) >= 5.0

    def build_waterfill_mix(
        self,
        S_cur: dict[str, float],
        d_rem: float,
        target_distill_frac: float,
        target_synth_frac: float,
    ) -> dict[str, float]:
        """Exact analytical-gradient marginal-utility water-filling mix over all 7 domains + synth + distill."""
        d_cur = sum(S_cur.values())
        d_tot = max(1.0, d_cur + max(1.0, d_rem))

        df = 0.0
        if target_distill_frac > 0.0 and (self.st.U.get("distill", 0.0) + self.st.distill_pending) >= 5.0:
            max_s_dist = target_distill_frac * d_tot
            rem_dist = max(0.0, max_s_dist - S_cur.get("distill", 0.0))
            df = _clip(rem_dist / max(1.0, d_rem), 0.0, min(0.28, target_distill_frac * 1.4))

        sf = 0.0
        if target_synth_frac > 0.0 and self.st.U.get("synth", 0.0) >= 20.0:
            max_s_synth = target_synth_frac * d_tot
            rem_synth = max(0.0, max_s_synth - S_cur.get("synth", 0.0))
            sf = _clip(rem_synth / max(1.0, d_rem), 0.0, min(0.38, target_synth_frac * 1.3))

        if df + sf > 0.55:
            sc = 0.55 / (df + sf)
            df *= sc
            sf *= sc

        u_list = [self.st.U.get(p, 0.0) for p in ALL_POOLS]
        q_list = [self.st.q_est.get(p, 0.9) for p in ALL_POOLS]
        s_init = [S_cur.get(p, 0.0) for p in ALL_POOLS]
        return self._waterfill_fast(u_list, q_list, s_init, max(1.0, d_rem), df, sf, 32)

    # ================================================================ Card & Serving Allocation
    def serve_need_compute(self, margin: float = 1.035, for_rent: bool = False) -> float:
        """Exact smooth-demand serving compute requirement (zero mid-week over-reservation!)."""
        fl = self.st.flagship()
        if not fl:
            return 0.0
        t = self.st.t
        n_b = float(fl.get("N") or 20.0)
        per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
        live_dem = float(self.st.api_obs[0])
        cur_dem = max(live_dem, self.st.demand * self.st.my_share)
        # Quarter-boundary shock guard (+15% possible ONLY when crossing multiple of 2190)
        q_cross = ((t + (30 if for_rent else 26)) // 2190) > (t // 2190)
        q_mult = 1.16 if q_cross else 1.0

        # Shares ONLY change on Monday (t % 168 == 0)!
        #   - For active card reservation: prepare for Monday share jump ONLY in final 3 hours (t % 168 >= 165).
        #   - For cloud rental: prepare ONLY on Saturday night (t % 168 == 143) AND ONLY if rent-serving is profitable!
        if for_rent:
            if t % 168 >= 140:
                _, mc_rent = self.pricer.marginal_costs(fl)
                if float(fl["price"]) > mc_rent * 1.08:
                    t_sh = self.pricer.target_share(float(fl["pub"]), float(fl["price"]))
                    s_next = max(self.st.my_share, min(self.st.my_share + 0.155, t_sh))
                    dem_est = max(cur_dem * 1.03, self.st.demand * min(1.0, s_next) * 1.06)
                else:
                    dem_est = cur_dem * 1.03
            else:
                dem_est = cur_dem * 1.03
            return dem_est * per * margin * q_mult

        if t % 168 >= 165:
            t_sh = self.pricer.target_share(float(fl["pub"]), float(fl["price"]))
            s_next = max(self.st.my_share, min(self.st.my_share + 0.155, t_sh))
            dem_est = max(cur_dem * 1.03, self.st.demand * min(1.0, s_next) * 1.07)
            return dem_est * per * max(margin, 1.04) * q_mult
        return cur_dem * per * margin * q_mult

    def allocate_cards(
        self, cur_d: float, in_q11: bool, need_train: bool = True
    ) -> tuple[dict[str, int], dict[str, int], float, float]:
        """Allocate serving cards (preferring owned non-rented newest GPUs in Q11!) and optimal training cards."""
        own = self.st.own_usable()
        rented = self.st.rented_usable()
        need_serve = self.serve_need_compute(1.04 if in_q11 else 1.035, for_rent=False)
        serve: dict[str, int] = {}
        got = 0.0
        rent_serve_comp = 0.0

        if in_q11:
            order_no_rent = [m for m in reversed(GPUS) if m in own and rented.get(m, 0) == 0]
            order_with_rent = [m for m in reversed(GPUS) if m in own and rented.get(m, 0) > 0]
            for m in order_no_rent + order_with_rent:
                if got >= need_serve:
                    break
                pw = self.st.gpu[m]["power"]
                k = min(own[m], int(math.ceil((need_serve - got) / pw)))
                if k > 0:
                    serve[m] = k
                    got += k * pw
            if got < need_serve:
                for m in reversed(GPUS):
                    if got >= need_serve or m not in rented:
                        continue
                    pw = self.st.gpu[m]["power"]
                    k = min(rented[m], int(math.ceil((need_serve - got) / pw)))
                    if k > 0:
                        serve[m] = serve.get(m, 0) + k
                        got += k * pw
                        rent_serve_comp += k * pw
        else:
            for m in GPUS:
                if got >= need_serve:
                    break
                avail_m = own.get(m, 0) + rented.get(m, 0)
                if avail_m <= 0:
                    continue
                pw = self.st.gpu[m]["power"]
                k = min(avail_m, int(math.ceil((need_serve - got) / pw)))
                if k > 0:
                    serve[m] = k
                    got += k * pw
                    if k > own.get(m, 0):
                        rent_serve_comp += (k - own.get(m, 0)) * pw

        if not need_train:
            train: dict[str, int] = {}
        else:
            rest: dict[str, int] = {}
            for m in GPUS:
                tot_m = own.get(m, 0) + rented.get(m, 0)
                rem_m = tot_m - serve.get(m, 0)
                if rem_m > 0:
                    rest[m] = rem_m
            train = self.select_best_train_cards(rest, cur_d)
            self.train_models = set(train.keys())
        rent_frac = rent_serve_comp / max(1.0, got)
        own_serve_comp = max(0.0, got - rent_serve_comp)
        return serve, train, own_serve_comp, rent_frac

    # ================================================================ Training Pipeline
    def _track_cur_run_S(self, force: bool = False) -> None:
        if not self.cur_run:
            return
        if not force and (self.st.t % 12 != 0) and not self.st.ev:
            return
        r = self.st.runs_obs.get(self.cur_run)
        if not r:
            return
        d_now = float(r[2])
        ck_id = int(r[6])
        if ck_id > 0 and d_now > 0.01:
            self.cur_nonzero_ckpt.add(self.cur_run)
        if d_now < self.cur_prev_D - 1e-4:
            snap = self.cur_ckpt_S.get(ck_id)
            if snap is not None:
                self.cur_S = dict(snap)
            elif self.cur_prev_D > 1e-6:
                sc = d_now / self.cur_prev_D
                self.cur_S = {k: v * sc for k, v in self.cur_S.items()}
        elif d_now > self.cur_prev_D + 1e-6:
            dd = d_now - self.cur_prev_D
            for p, f in self.cur_mix.items():
                self.cur_S[p] = self.cur_S.get(p, 0.0) + dd * f
        if ck_id > self.cur_last_ckpt:
            self.cur_last_ckpt = ck_id
            self.cur_ckpt_S[ck_id] = dict(self.cur_S)
            if len(self.cur_ckpt_S) > 36:
                oldest = min(k for k in self.cur_ckpt_S if k != 0)
                del self.cur_ckpt_S[oldest]
        self.cur_prev_D = d_now

        if self.cur_sched == "wsd" and d_now >= 0.845 * float(r[3]):
            self.ever_annealed.add(self.cur_run)

    def _project_more_compute(self, t0: int, t1: int, train: dict[str, int], cur_d: float, rent_boost: float = 0.0) -> float:
        dur = max(1, t1 - t0)
        base_rate = self.exact_throughput(train, cur_d + 50.0) + rent_boost * 0.88
        tot = base_rate * dur
        for d in self.st.deliv:
            arr_t = int(d[0])
            if arr_t < t1:
                tot += self.st.gpu[d[1]]["power"] * int(d[2]) * (t1 - max(t0, arr_t)) * 0.82
        return tot

    def step_pipeline(self, act: dict) -> None:
        t = self.st.t
        T = self.st.ticks
        self._track_cur_run_S()

        # Immediate next-tick restore after a cosine ratchet release (100% legal: D < 0.98 * target!)
        if self.pending_cosine_extend is not None:
            rn = self.pending_cosine_extend
            self.pending_cosine_extend = None
            rr = self.st.runs_obs.get(rn)
            if rr is not None and rr[1] not in ("done", "stopped"):
                d_r = float(rr[2])
                tgt_r = float(rr[3])
                if d_r < 0.979 * tgt_r and d_r < 499000.0:
                    act.setdefault("run_ctl", []).append({"name": rn, "op": "set_tokens", "arg": 500000.0})

        if self.stage >= 3:
            return

        if self.cur_run is None or (self.cur_run not in self.st.runs_obs and self.cur_run in self.released_runs):
            self._launch_current_stage(act)
            return

        r = self.st.runs_obs.get(self.cur_run)
        if r is None:
            return
        status = r[1]
        cur_d = float(r[2])
        cur_tgt = float(r[3])

        if status == "diverged" and int(r[6]) == 0:
            act.setdefault("run_ctl", []).append({"name": self.cur_run, "op": "stop"})
            self.cur_run = None
            return

        if status == "done":
            self._finish_stage(act, self.cur_run)
            return

        # Pre-create & pre-audit Main Cosine Run while Gen 2 is running (once algo_eff_3 is unlocked!)
        if (
            self.stage == 1
            and not self.main_prep_attempted
            and self.st.algo_level() >= 3
            and t >= self.cur_end - 620
        ):
            self.main_prep_attempted = True
            self._prepare_main_wsd(act)

        # Intermediate Cosine Ratchet Releases during Stage 2!
        # Temporarily sets target = cur_d / 0.968 -> snaps ckpt -> releases with +1.87% cosine bonus ->
        # restores target = 500000 on tick t+1 with ZERO progress lost and ZERO illegal actions!
        min_d_first = max(7.0 * self.cur_N, 320.0) if self.main_rel_idx == 0 else max(3.5 * self.cur_N, self.last_rel_D * 1.38, 15.0)
        if (
            self.stage == 2
            and status == "running"
            and self.pending_cosine_extend is None
            and self.main_rel_idx < len(self.main_rel_times)
            and t >= self.main_rel_times[self.main_rel_idx]
            and (cur_d >= min_d_first or t >= 23400)
            and cur_d < 0.90 * cur_tgt
        ):
            tpt_est = max(0.001, float(r[4]))
            if cur_d > 30.0 * tpt_est:
                ratchet_tgt = round(max(cur_d / 0.968, (cur_d + 4.0 * tpt_est) / 0.974), 4)
                if cur_d < ratchet_tgt <= 9.5e5:
                    self.main_rel_idx += 1
                    self.last_rel_D = cur_d
                    fl = self.st.flagship()
                    if fl is not None:
                        bp = float(fl["price"])
                        init_p = round(_clip(bp * (1.28 if t >= 23000 else 1.15), 4.5, 42.0), 3)
                    else:
                        init_p = 10.0
                    ctl = act.setdefault("run_ctl", [])
                    ctl.append({"name": self.cur_run, "op": "set_tokens", "arg": ratchet_tgt})
                    ctl.append({"name": self.cur_run, "op": "ckpt"})
                    act.setdefault("release", []).append({"run": self.cur_run, "price": init_p})
                    self.pending_cosine_extend = self.cur_run
                    return

        # Gen 1 mid-run synthetic dilution: as soon as our $1.3M synth reservoir arrives (~t=500),
        # switch Gen 1 mix ONCE to include synthetic tokens -> cuts cont/D < 0.045 & boosts C!
        if (
            self.stage == 0
            and status == "running"
            and not self.gen1_synth_mixed
            and self.st.U.get("synth", 0.0) >= 500.0
            and cur_d > 5.0
            and cur_d < 0.72 * cur_tgt
        ):
            self._track_cur_run_S(force=True)
            self.gen1_synth_mixed = True
            new_mix = self.build_waterfill_mix(self.cur_S, max(50.0, cur_tgt - cur_d), 0.185, 0.292)
            act.setdefault("run_ctl", []).append({"name": self.cur_run, "op": "set_mix", "arg": new_mix})
            self.cur_mix = new_mix

        # Fast low-overhead dynamic card sync:
        #   - Scheduled sync on Monday settle (t%168==0), pre-Monday bump (t%168==165), daily (t%24==12)
        #   - Fleet-change sync checked every 12 ticks (only triggers if usable compute changed >= 1.5%!)
        #   - Instant 1-tick reactive serving guard ONLY when fleet has spare unallocated cards!
        in_q11_serve = t >= self.q11_start - 10
        need_card_sync = (t % 24 == 12) or (t % 168 == 0) or (t % 168 == 165)
        if not need_card_sync and self.active_serve_comp > 0.0 and self.cur_fl_N > 0.0:
            prev_tot = self.st.last_fleet_sig[0] if self.st.last_fleet_sig else 0.0
            if self.active_serve_comp < prev_tot - 1.0:
                per_l = self.st.sk * self.cur_fl_N * self.st.infer_mult()
                if float(self.st.api_obs[0]) * per_l > self.active_serve_comp * 0.992:
                    need_card_sync = True
        if not need_card_sync and (t % 12 == 0 or self.st.ev):
            gp = self.st.gpu_pow
            tot_c = 0.0
            for m_k, v_k in self.st.gpu_obs.items():
                tot_c += (int(v_k[0]) + int(v_k[3])) * gp[m_k]
            prev_c = self.st.last_fleet_sig[0] if self.st.last_fleet_sig else -1.0
            if prev_c < 0.0 or abs(tot_c - prev_c) >= max(20.0, 0.015 * prev_c):
                need_card_sync = True
        if need_card_sync and status in ("running", "review") and self.pending_cosine_extend is None:
            gp = self.st.gpu_pow
            tot_c = 0.0
            for m_k, v_k in self.st.gpu_obs.items():
                tot_c += (int(v_k[0]) + int(v_k[3])) * gp[m_k]
            self.st.last_fleet_sig = (tot_c,)
            fl_cur = self.st.flagship()
            self.cur_fl_N = float(fl_cur.get("N") or 20.0) if fl_cur else 0.0
            serve, train, _, _ = self.allocate_cards(cur_d, in_q11_serve, need_train=True)
            ncards = sum(train.values())
            ctl = act.setdefault("run_ctl", [])
            if ncards > 0:
                ctl.append({"name": self.cur_run, "op": "set_gpus", "arg": train})
                ctl.append({"name": self.cur_run, "op": "set_batch", "arg": max(4, (ncards + 1) // 2)})
                ck, _ = self.opt_ckpt_and_eff(ncards)
                ctl.append({"name": self.cur_run, "op": "set_ckpt", "arg": ck})
            self._set_serve(act, serve)

            # Dynamic curriculum mix update & WSD target check (once daily at t % 24 == 12)
            if t % 24 == 12:
                if (
                    status == "running"
                    and self.stage == 2
                    and "curriculum" in self.st.tech
                    and t - self.last_mix_t >= 7 * 24
                    and cur_d > 20.0
                ):
                    self._track_cur_run_S(force=True)
                    self.last_mix_t = t
                    kc = float(self.st.rules.get("KC", 300.0))
                    rem_t = max(240, self.main_target_finish - t)
                    est_rate = max(1e-4, self.exact_throughput(train, cur_d) / (self.cur_N * kc))
                    d_rem_est = max(50.0, est_rate * rem_t)
                    if not in_q11_serve:
                        self.procure_distill(act, cur_d + d_rem_est, 0.096, max_spend_frac=0.18)
                    new_mix = self.build_waterfill_mix(self.cur_S, min(d_rem_est, 2500.0), 0.096, 0.295)
                    if new_mix != self.cur_mix:
                        ctl.append({"name": self.cur_run, "op": "set_mix", "arg": new_mix})
                        self.cur_mix = new_mix

                # WSD target & annealing lock management for Gen 1 & Gen 2 ONLY (Stage 0, 1)
                if self.stage in (0, 1) and status == "running" and self.cur_run not in self.wsd_locked and self.cur_run not in self.ever_annealed:
                    kc = float(self.st.rules.get("KC", 300.0))
                    rem = self.cur_end - t
                    if cur_d < 0.80 * cur_tgt and rem > 60:
                        more_g1h = self._project_more_compute(t, self.cur_end, train, cur_d)
                        own_floor = self.st.compute_of(self.st.own_usable()) * 0.65 * max(1, rem)
                        more_g1h = max(more_g1h, own_floor)
                        d_more_cons = more_g1h * 0.90 / (self.cur_N * kc)
                        rem_frac = d_more_cons / max(1.0, cur_d + d_more_cons)
                        elapsed = (t - self.cur_start) / max(1.0, self.cur_end - self.cur_start)
                        if (elapsed >= 0.76 and rem_frac <= 0.172) or elapsed >= 0.82:
                            ft = min(9.5e5, cur_d / 0.848)
                            if ft > cur_d + 0.1 and abs(ft - cur_tgt) / max(1.0, cur_tgt) > 0.01:
                                ctl.append({"name": self.cur_run, "op": "set_tokens", "arg": round(ft, 3)})
                                self.ever_annealed.add(self.cur_run)
                            self.wsd_locked.add(self.cur_run)
                        else:
                            stgt = min(9.5e5, max(cur_d / 0.72, cur_d + d_more_cons * 1.12))
                            if stgt > cur_d + 0.1 and abs(stgt - cur_tgt) / max(1.0, cur_tgt) > 0.04:
                                ctl.append({"name": self.cur_run, "op": "set_tokens", "arg": round(stgt, 3)})

        # Final Endgame Release for Main Cosine Run at t = T - 105 (t = 26175 -> live 26199 -> online_ticks = 81 >= 72!)
        if self.stage == 2 and status in ("running", "review") and t >= self.main_target_finish and self.pending_cosine_extend is None:
            if cur_d > 5.0 and cur_d < 0.98 * cur_tgt:
                final_tgt = round(min(9.9e5, max(cur_d * 1.0004, cur_d + 0.05)), 4)
                ctl = act.setdefault("run_ctl", [])
                ctl.append({"name": self.cur_run, "op": "set_tokens", "arg": final_tgt})
                ctl.append({"name": self.cur_run, "op": "ckpt"})
                fl = self.st.flagship()
                pr = round(float(fl["price"]), 3) if fl is not None else 20.0
                act.setdefault("release", []).append({"run": self.cur_run, "price": pr})
                ctl.append({"name": self.cur_run, "op": "stop"})
                self.released_runs.add(self.cur_run)
                self.stage = 3
                self.cur_run = None
                return

        # Hard deadline guard for Stage 0 / Stage 1 (and safety fallback for Stage 2)
        hard_end = (self.cur_end - 8) if self.stage < 2 else (T - 101)
        if status in ("running", "review") and t >= hard_end:
            act.setdefault("run_ctl", []).append({"name": self.cur_run, "op": "stop"})
            if int(r[6]) > 0 and self.cur_run in self.cur_nonzero_ckpt:
                self._finish_stage(act, self.cur_run)
            else:
                self.stage += 1
                self.cur_run = None

    def _launch_current_stage(self, act: dict) -> None:
        t = self.st.t
        T = self.st.ticks
        if self.stage >= 3:
            return
        serve, train, _, _ = self.allocate_cards(0.0, False)
        if sum(train.values()) <= 0:
            return
        self._set_serve(act, serve)
        ncards = sum(train.values())
        ck, _ = self.opt_ckpt_and_eff(ncards)
        kc = float(self.st.rules.get("KC", 300.0))

        # Resume pre-audited Main Cosine Run if prepared!
        if self.stage == 2 and self.main_prepped is not None:
            name, n_b, mix = self.main_prepped
            self.main_prepped = None
            ctl = act.setdefault("run_ctl", [])
            ctl.append({"name": name, "op": "set_gpus", "arg": train})
            ctl.append({"name": name, "op": "set_batch", "arg": max(4, (ncards + 1) // 2)})
            ctl.append({"name": name, "op": "set_ckpt", "arg": ck})
            ctl.append({"name": name, "op": "resume"})
            self._init_run_tracking(name, n_b, t, self.main_target_finish, "cosine", mix)
            return

        if self.stage == 0:
            # Gen 1: Fast compact distilled WSD cash cow (t ~ 42 .. 1460)
            end = min(1460, T - 105)
            budget = max(self._project_more_compute(t, end, train, 0.0), 2100.0 * max(200, end - t))
            N = self.choose_optimal_N(budget, self.st.algo_mult(), 5.0, 15.0, 15.0, has_synth=True, has_distill=True)
            D = round(max(180.0, budget / (kc * N)), 2)
            df = 0.185 if self.procure_distill(act, D, 0.185, max_spend_frac=0.15) else 0.0
            sf = 0.28 if self.st.U.get("synth", 0.0) >= 20.0 else 0.0
            mix = self.build_waterfill_mix({p: 0.0 for p in ALL_POOLS}, D, df, sf)
            self._create_run(act, N, D, train, "wsd", mix, end, False)
        elif self.stage == 1:
            # Gen 2: Wait briefly for algo_eff_2 (+30% effective N!) before launching bridge flagship
            if self.st.algo_level() < 2 and t < 1750:
                return
            end = min(4250, T - 105)
            if end - t < 400:
                self.stage = 2
                return
            budget = max(self._project_more_compute(t, end, train, 0.0), 4200.0 * max(400, end - t))
            N = self.choose_optimal_N(budget, self.st.algo_mult(), 9.0, 26.0, 26.0, has_synth=True, has_distill=True)
            D = round(max(250.0, budget / (kc * N)), 2)
            df = 0.095 if self.procure_distill(act, D, 0.095, max_spend_frac=0.18) else 0.0
            sf = 0.29 if self.st.U.get("synth", 0.0) >= 20.0 else 0.0
            mix = self.build_waterfill_mix({p: 0.0 for p in ALL_POOLS}, D, df, sf)
            self._create_run(act, N, D, train, "wsd", mix, end, False)
        elif self.stage == 2:
            if self.st.algo_level() < 3 and t < 5600:
                return
            end = self.main_target_finish
            N, mix = self._plan_main_wsd_spec(act, t, end)
            self._create_run(act, N, 500000.0, train, "cosine", mix, end, True)

    def _plan_main_wsd_spec(self, act: dict, t0: int, end: int) -> tuple[float, dict[str, float]]:
        # Exact coupled optimization: total fleet compute (~3.2e8 G1-h) MINUS serving drain (2.3e6 * N),
        # blending Year-3 peak (80%) and Year-2 cashflow velocity (20%) + Q11 serving footprint!
        frac_t = max(0.3, (end - t0) / 21900.0)
        proj_budget = 3.2e8 * frac_t
        mid_budget = 6.0e7 * frac_t
        N = self.choose_optimal_N(
            proj_budget, 1.50, 18.0, 82.0, 56.0,
            has_synth=True, has_distill=True,
            mid_budget_g1h=mid_budget,
            serve_g1h_per_b=2.3e6 * frac_t,
            mid_serve_g1h_per_b=4.0e5 * frac_t,
        )
        kc = float(self.st.rules.get("KC", 300.0))
        d_proj = max(1000.0, (proj_budget - 2.3e6 * frac_t * N) / (kc * N))
        df = 0.096 if self.procure_distill(act, min(d_proj, 4000.0), 0.096, max_spend_frac=0.18) else 0.0
        sf = 0.295 if self.st.U.get("synth", 0.0) >= 20.0 else 0.0
        mix = self.build_waterfill_mix({p: 0.0 for p in ALL_POOLS}, min(d_proj, 3000.0), df, sf)
        return N, mix

    def _prepare_main_wsd(self, act: dict) -> None:
        _, train, _, _ = self.allocate_cards(0.0, False)
        if sum(train.values()) <= 0:
            train = self.st.own_usable() or {"G1": 64}
        N, mix = self._plan_main_wsd_spec(act, self.cur_end, self.main_target_finish)
        self.run_seq += 1
        name = f"arb_m{self.run_seq}"
        ncards = max(1, sum(train.values()))
        ck, _ = self.opt_ckpt_and_eff(ncards)
        sp = {
            "name": name, "params_b": N, "tokens_b": 500000.0, "gpus": train,
            "lr": self.eta_star(N) * 0.96, "batch": max(4, (ncards + 1) // 2),
            "sched": "cosine", "mix": mix, "ckpt_every": ck, "paused": True,
        }
        act.setdefault("run", []).append(sp)
        act.setdefault("audit_request", []).append({"run": name})
        self.audit_sent.add(name)
        self.main_prepped = (name, N, mix)

    def _create_run(
        self,
        act: dict,
        N: float,
        D: float,
        train: dict[str, int],
        sched: str,
        mix: dict[str, float],
        end: int,
        request_audit: bool,
    ) -> None:
        self.run_seq += 1
        name = f"arb_s{self.run_seq}"
        ncards = max(1, sum(train.values()))
        ck, _ = self.opt_ckpt_and_eff(ncards)
        sp = {
            "name": name, "params_b": N, "tokens_b": D, "gpus": train,
            "lr": self.eta_star(N) * 0.96, "batch": max(4, (ncards + 1) // 2),
            "sched": sched, "mix": mix, "ckpt_every": ck,
        }
        act.setdefault("run", []).append(sp)
        if request_audit and name not in self.audit_sent:
            act.setdefault("audit_request", []).append({"run": name})
            self.audit_sent.add(name)
        self._init_run_tracking(name, N, self.st.t, end, sched, mix)

    def _init_run_tracking(self, name: str, N: float, t0: int, end: int, sched: str, mix: dict[str, float]) -> None:
        self.cur_run = name
        self.cur_N = N
        self.cur_start = t0
        self.cur_end = end
        self.cur_sched = sched
        self.cur_mix = dict(mix)
        self.cur_prev_D = 0.0
        self.cur_S = {p: 0.0 for p in ALL_POOLS}
        self.cur_ckpt_S = {0: {p: 0.0 for p in ALL_POOLS}}
        self.cur_last_ckpt = 0
        self.last_mix_t = t0

    def _finish_stage(self, act: dict, name: str) -> None:
        if name in self.released_runs:
            return
        self.released_runs.add(name)
        fl = self.st.flagship()
        if fl is not None:
            base_p = float(fl["price"])
            # When releasing our final WSD-annealed flagship (stage == 2), pub jumps by +0.10..+0.15,
            # so releasing at 1.45x immediately unlocks high Q11 margin with ZERO price shock!
            if self.stage == 2:
                pr = round(_clip(base_p * 1.44, 4.5, 42.0), 3)
            else:
                pr = round(_clip(base_p * 1.10, 4.5, 45.0), 3)
        else:
            pr = 7.5
        act.setdefault("release", []).append({"run": name, "price": pr})
        self.stage += 1
        self.cur_run = None

    def _set_serve(self, act: dict, serve: dict[str, int]) -> None:
        clean = {m: int(n) for m, n in serve.items() if n > 0}
        if not clean:
            if not self.serve_auto:
                act["serve"] = []
                self.active_serve = None
                self.active_serve_comp = 0.0
                self.serve_auto = True
            return
        if self.serve_auto or clean != self.active_serve:
            act["serve"] = [{"gpus": clean}]
            self.active_serve = clean
            self.active_serve_comp = self.st.compute_of(clean)
            self.serve_auto = False

    def _ops(self, act: dict) -> None:
        for e in self.st.ev:
            ty = e.get("type")
            rn = e.get("run")
            if not rn:
                continue
            if ty in ("spike", "diverged"):
                r = self.st.runs_obs.get(rn)
                if r and int(r[6]) > 0:
                    act.setdefault("run_ctl", []).append({"name": rn, "op": "rollback"})
            elif ty == "slow_node":
                act.setdefault("run_ctl", []).append({"name": rn, "op": "diagnose"})

    def _research(self, act: dict) -> None:
        if not self.st.staff:
            return
        avail = []
        for tk in self.TECH_PLAN:
            if tk in self.st.tech:
                continue
            pre = self.st.techs.get(tk, {}).get("pre")
            if pre and pre not in self.st.tech:
                continue
            avail.append(tk)
        # Focus 100% on top-priority sequential bottlenecks (algo_eff_1/2/3, synth, filter:web, cooling, early_supply)
        if avail and avail[0] in ("algo_eff_1", "algo_eff_2", "algo_eff_3", "synth", "filter:web", "cooling", "early_supply"):
            pick = (avail[0],)
        else:
            pick = tuple(avail[:2])
        if pick != self.research_cur:
            self.research_cur = pick
            if pick:
                shares = (0.7, 0.3) if len(pick) == 2 else (1.0,)
                act["research"] = [{"tech": k, "share": s} for k, s in zip(pick, shares)]

    def _pricing_and_retirement(self, act: dict) -> None:
        exec_day = (self.st.t + 1) // 24
        # Tag tiny synth seed model (N <= 0.1) so we never touch it
        for mid, meta in self.st.my_models.items():
            m = self.st.models.get(mid)
            if m and m.get("N") is not None and float(m["N"]) <= 0.15:
                meta["seed"] = True

        # Choose best API flagship considering BOTH pub and serving-compute efficiency!
        # Never retire a compact older model for a 2x+ larger model unless pub improved by >= +0.035!
        api_models = [
            (mid, self.st.models[mid])
            for mid, meta in self.st.my_models.items()
            if mid in self.st.models and not meta.get("delisted") and not meta.get("seed")
            and float(self.st.models[mid].get("pub", 0.0)) >= 0.145
        ]
        if len(api_models) > 1:
            best_mid = max(
                api_models,
                key=lambda kv: (float(kv[1]["pub"]) - 0.0008 * float(kv[1].get("N") or 20.0), kv[0]),
            )[0]
            for mid, m in api_models:
                if mid == best_mid:
                    continue
                meta = self.st.my_models[mid]
                if meta.get("online_ticks", 0) >= 735:
                    act.setdefault("delist", []).append(mid)
                    meta["delisted"] = True
                elif float(m["price"]) < 95.0 and self.st.last_price_exec_day.get(mid, -1) != exec_day:
                    act.setdefault("price", []).append({"model": mid, "value": 100.0})
                    self.st.last_price_exec_day[mid] = exec_day

        fl = self.st.flagship()
        if fl is None:
            return
        mid = fl["id"]
        if self.st.last_price_exec_day.get(mid, -1) == exec_day:
            return

        # Reprice right after Monday settlement, on drop emergency, or on first day of a newly live flagship!
        new_live = self.st.my_models.get(mid, {}).get("online_ticks", 99) <= 26
        if self.st.t % 168 == 0 or new_live or (self.st.drop > 0.02 and self.st.t % 24 == 0):
            in_q11 = self.st.t >= self.q11_start - 10
            n_b = float(fl.get("N") or 30.0)
            per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
            own_c = self.st.compute_of(self.st.own_usable())
            rent_c = self.st.compute_of(self.st.rented_usable())
            dem_den = max(1e-6, self.st.demand * 1.06 * per)
            own_s_cap = min(1.0, (own_c if in_q11 else own_c * 0.88) / dem_den)
            tot_s_cap = min(1.0, (own_c + rent_c + (8000.0 if self.st.cash_avail > 60.0 else 0.0)) / dem_den)
            p_new = self.pricer.solve_price(fl, own_s_cap, tot_s_cap)
            cur_p = float(fl["price"])
            if abs(p_new - cur_p) / max(1.0, cur_p) > 0.03:
                act.setdefault("price", []).append({"model": mid, "value": p_new})
                fl["price"] = p_new
                self.st.last_price_exec_day[mid] = exec_day

    def _training_rent_target(self) -> float:
        """Desired extra on-demand compute for training acceleration (pre-Q11 only)."""
        t = self.st.t
        if self.stage >= 3 or t >= self.q11_start - 180 or not self.cur_run:
            return 0.0
        r = self.st.runs_obs.get(self.cur_run)
        if not r or r[1] != "running":
            return 0.0
        cur_d = float(r[2])
        own_c = self.st.compute_of(self.st.own_usable())
        # Target total compute trajectory that scales with cashflow & B_crit without over-renting
        if self.stage == 0:
            # Gen 1 sprint: balanced 2,200 G1-h preserves cash for Q1 G2 auctions!
            goal = 2200.0 if self.st.cash_avail > 75.0 else 1400.0
        elif self.stage == 1:
            goal = 4800.0 if self.st.cash_avail > 35.0 else 3000.0
        else:
            # Main Cosine Run: compound compute with cashflow!
            if t < 8760:
                goal = 12000.0
            elif t < 15000:
                goal = 24000.0
            elif t < 20000:
                goal = 38000.0
            else:
                goal = 50000.0
            if self.st.cash_avail < 80.0:
                goal *= 0.65
            elif self.st.cash_avail > 200.0:
                goal *= 1.20
        # Respect B_crit ceiling: don't rent cards past 2.4 * B_crit(D)
        bc_max_cards = int(2.4 * self.bc_at(cur_d))
        cur_cards = int(r[7])
        if cur_cards >= bc_max_cards and own_c >= 1500.0:
            return 0.0
        return max(0.0, goal - own_c)

    def _sanitize_action(self, act: dict) -> dict:
        """Final structural sanitizer: finite numbers, list length limits, non-empty keys."""
        clean = {}
        for k, v in act.items():
            if isinstance(v, list):
                lim = 300 if k == "fire" else 64
                if len(v) > lim:
                    v = v[:lim]
                if not v and k != "serve":
                    continue
            clean[k] = v
        return clean

    def step(self, obs: dict) -> dict:
        self.st.ingest(obs)
        if self.st.bk:
            return {}
        act: dict = {}
        t = self.st.t
        in_q11 = t >= self.q11_start - 10

        # 1. Synthetic data bootstrap arbitrage (fires immediately once synth tech unlocks!)
        self.maybe_bootstrap_synth(act)

        # 2. Probes -> Training Pipeline
        if self.step_probes(act):
            self.step_pipeline(act)
        self._ops(act)

        # 3. Daily fresh-price cloud rental arbitrage (STRUCTURALLY ZERO ILLEGAL!)
        if self.st.rent_fresh and (t + 1) % 168 != 0 and self.probe_stage >= 4:
            need = self.serve_need_compute(1.045, for_rent=True)
            own_c = sum(int(v[0]) * self.st.gpu_pow[m] for m, v in self.st.gpu_obs.items() if v[0] > 0)
            own_serve_comp = min(need, own_c)
            train_extra = self._training_rent_target()
            # Ensure pre-Q11 training leases expire cleanly BEFORE Q11 starts!
            max_end = self.st.ticks if in_q11 else min(self.st.ticks, self.q11_start - 2)
            self.desk.ensure_cloud_rent(act, need, own_serve_comp, train_extra, max_end, in_q11)

        # 4. Daily ops: research, instant post-tech payroll liquidation, serving sync, pricing
        if t % 24 == 0:
            tech_done = all(k in self.st.tech for k in self.TECH_PLAN)
            if tech_done and self.st.staff:
                sev = sum(s["sal"] for s in self.st.staff) * float(self.st.rules.get("FIRE_SEVERANCE", 168))
                if self.st.cash_avail > sev - 5.0:
                    act["fire"] = [s["id"] for s in self.st.staff]
                    self.st.spend_cash(sev)
            else:
                self._research(act)
            if self.st.flagship() is not None and "serve" not in act:
                cur_d = float(self.st.runs_obs[self.cur_run][2]) if (self.cur_run and self.cur_run in self.st.runs_obs) else 0.0
                serve, _, _, _ = self.allocate_cards(cur_d, in_q11 or self.stage >= 3, need_train=False)
                self._set_serve(act, serve)
            self._pricing_and_retirement(act)
        elif self.stage >= 3 and self.st.flagship() is not None and (t % 168 == 165):
            serve, _, _, _ = self.allocate_cards(0.0, True, need_train=False)
            self._set_serve(act, serve)

        # 5. Weekly capital operations (sites, PPAs, GPU auctions, obsolete liquidation, data deals, hiring/firing)
        if t % 168 == 0:
            tech_done = all(k in self.st.tech for k in self.TECH_PLAN)
            self.desk.weekly_capital_ops(act, tech_done, self.train_models, self.stage == 2)

        return self._sanitize_action(act)


def make_bot(init: dict) -> CapitalArbitrageur:
    return CapitalArbitrageur(init)


if __name__ == "__main__":
    raw_init = sys.stdin.readline()
    if raw_init:
        bot = make_bot(json.loads(raw_init))
        sys.stdout.write("{}\n")
        sys.stdout.flush()
        for line in sys.stdin:
            msg = json.loads(line)
            if msg.get("type") == "end":
                sys.stdout.write("{}\n")
                sys.stdout.flush()
                break
            try:
                action = bot.step(msg)
            except Exception:
                action = {}
            if not action:
                sys.stdout.write("{}\n")
            else:
                sys.stdout.write(json.dumps(action, separators=(",", ":")) + "\n")
            sys.stdout.flush()
