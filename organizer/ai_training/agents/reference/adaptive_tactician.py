"""adaptive_tactician: Adaptive Counter-Play & R&D Tactician (private scoring_v2 opponent).

Strategic identity:
  - Opponent-aware 4-regime controller (SOLO_FRONTIER, SCALE_COUNTER, SHARE_DEFENSE,
    ENDGAME_DEFENSE): tracks opponent GPU auction wins, clearing prices, live flagship
    (pub, price, N), API share, staff count, and fundraising to adapt compute splits,
    counter-pricing, auction counter-bidding, adaptive distillation, and talent poaching.
  - Lightning zero-spike probes & complete R&D mastery: 8 sub-critical LR probes + exact
    B_crit extraction + consistent D_eff scaling-law OLS fit, paired with focused
    single-tech R&D blitz (zero-penalty curriculum ordering) and ROI-gated poaching.
  - Hybrid WSD + continuous cosine frontier training with agile checkpoint pacing:
    guaranteed-anneal WSD (+3% bonus) early generations with algo_eff_1/2, followed by
    a pre-audited continuous algo_eff_3 Mega Run (2-tick cosine tuck-and-release in
    competitive play, full WSD endgame anneal in SOLO_FRONTIER), marginal-gradient
    dynamic curriculum set_mix (zero penalty), exact contamination harvesting (<0.05
    audit cliff), and adaptive distillation when behind.
  - Zero-dilution Sunday auction-first GPU ownership engine & exact Q11 backward-DP
    counter-pricing: bids on Sunday (t%168==167) with full weekly revenue, converts
    profits into permanent G3/G4/G5 fleets (40%-95% terminal hw value + zero-rent Q11
    serving), uses capital-preserving cloud rentals only for zero-gap serving & surplus
    overflow, and solves an exact capacity-aware Q11 backward DP for 24x GM harvest.

Deterministic, single-file, Python standard library only.
"""
from __future__ import annotations

import json
import math
import sys

REAL_DOMAINS = ("web", "code", "math", "books", "science", "dialog", "multiling")
ALL_POOLS = ("web", "code", "math", "books", "science", "dialog", "multiling", "synth", "distill")
GPUS = ("G1", "G2", "G3", "G4", "G5")

PRIOR_T: dict[str, dict[str, float]] = {}
_SPECIAL_T = {
    ("math", "code"): 0.55,
    ("dialog", "multiling"): 0.45,
    ("science", "books"): 0.40,
    ("dialog", "web"): 0.30,
    ("math", "science"): 0.30,
    ("code", "web"): 0.175,
}
for _d in REAL_DOMAINS:
    PRIOR_T[_d] = {}
    for _k in ALL_POOLS:
        if _k == _d:
            PRIOR_T[_d][_k] = 1.0
        elif (_d, _k) in _SPECIAL_T:
            PRIOR_T[_d][_k] = _SPECIAL_T[(_d, _k)]
        elif _k == "synth":
            PRIOR_T[_d][_k] = 0.325
        elif _k == "distill":
            PRIOR_T[_d][_k] = 0.50
        else:
            PRIOR_T[_d][_k] = 0.07

PRIOR_W = {
    "web": 0.92,
    "code": 0.92,
    "math": 0.92,
    "dialog": 0.92,
    "multiling": 0.92,
    "science": 0.72,
    "books": 0.61,
}


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _fit_ols3(rows: list[tuple[float, float, float]]) -> tuple[float, float, float, float] | None:
    a = [[0.0] * 4 for _ in range(3)]
    for x1, x2, y in rows:
        v = (1.0, x1, x2)
        for i in range(3):
            for j in range(3):
                a[i][j] += v[i] * v[j]
            a[i][3] += v[i] * y
    for i in range(3):
        piv = max(range(i, 3), key=lambda r: abs(a[r][i]))
        a[i], a[piv] = a[piv], a[i]
        if abs(a[i][i]) < 1e-14:
            return None
        for r in range(3):
            if r != i:
                f = a[r][i] / a[i][i]
                for c in range(i, 4):
                    a[r][c] -= f * a[i][c]
    c0 = a[0][3] / a[0][0]
    c1 = a[1][3] / a[1][1]
    c2 = a[2][3] / a[2][2]
    sse = sum((y - c0 - c1 * x1 - c2 * x2) ** 2 for x1, x2, y in rows)
    return c0, c1, c2, sse


class OpponentIntel:
    """Tracks opponent fleet, auction behavior, flagship, staff, fundraising, and global shocks."""

    def __init__(self, me: str, opp: str, gpu_meta: dict):
        self.me = me
        self.opp = opp
        self.gpu_meta = gpu_meta
        self.opp_gpus_bought: dict[str, int] = {m: 0 for m in GPUS}
        self.opp_staff_n: int = 20
        self.opp_raises: int = 0
        self.opp_share: float = 0.0
        self.opp_flag_pub: float = 0.0
        self.opp_flag_price: float = 10.0
        self.opp_flag_N: float = 0.0
        self.opp_flag_id: str | None = None
        self.opp_early_seen: bool = False
        self.opp_last_won: dict[str, int] = {m: 0 for m in GPUS}
        self.my_last_won: dict[str, int] = {m: 0 for m in GPUS}
        self.clear_mult: dict[str, float] = {m: 1.35 for m in GPUS}
        self.bid_ratchet: dict[str, float] = {m: 0.0 for m in GPUS}
        self.last_bid_models: set[str] = set()
        self.regime: str = "SOLO_FRONTIER"
        self.is_active_opp: bool = False
        self.export_ctrl_q: int = -1
        self.open_source_seen: bool = False
        self.curtail_end: dict[int, int] = {}

    def opp_bought_power(self) -> float:
        return sum(self.gpu_meta[m]["power"] * cnt for m, cnt in self.opp_gpus_bought.items())

    def update_events(self, ev: list[dict]) -> None:
        for e in ev:
            ty = e.get("type")
            if ty == "raise" and e.get("lab") == self.opp:
                self.opp_raises += 1
            elif ty == "export_ctrl":
                self.export_ctrl_q = int(e.get("quarter", -1))
            elif ty == "open_source":
                self.open_source_seen = True
            elif ty == "curtail":
                self.curtail_end[int(e["region"])] = int(e["end"])

    def update_weekly(self, wk: dict, models: dict[str, dict], heat: float, t: int, ticks: int) -> None:
        self.opp_staff_n = int((wk.get("staff") or {}).get("opp", self.opp_staff_n))
        self.opp_share = float((wk.get("shares") or {}).get(self.opp, 0.0))

        self.opp_last_won = {m: 0 for m in GPUS}
        self.my_last_won = {m: 0 for m in GPUS}
        for m, ares in (wk.get("auction") or {}).items():
            winners = ares.get("winners") or {}
            opp_lots = int(winners.get(self.opp, 0))
            my_lots = int(winners.get(self.me, 0))
            self.opp_last_won[m] = opp_lots
            self.my_last_won[m] = my_lots
            if opp_lots > 0:
                self.opp_gpus_bought[m] = self.opp_gpus_bought.get(m, 0) + opp_lots * 256
                if (t - 168) // 2190 < self.gpu_meta[m]["launch_q"]:
                    self.opp_early_seen = True
            base_h = self.gpu_meta[m]["price"] * max(0.6, heat)
            if base_h > 0 and ares.get("lots", 0) > 0:
                ratio = float(ares.get("clear", base_h)) / base_h
                self.clear_mult[m] = 0.55 * self.clear_mult.get(m, 1.35) + 0.45 * ratio
            if m in self.last_bid_models and ares.get("lots", 0) > 0:
                if my_lots == 0 and opp_lots > 0:
                    self.bid_ratchet[m] = min(0.65, self.bid_ratchet.get(m, 0.0) + 0.16)
                elif my_lots == 0:
                    self.bid_ratchet[m] = min(0.45, self.bid_ratchet.get(m, 0.0) + 0.08)
                else:
                    self.bid_ratchet[m] = max(0.0, self.bid_ratchet.get(m, 0.0) - 0.04)
        self.last_bid_models = set()

        best_m, best_u = None, -1e18
        for m in models.values():
            if m["owner"] != self.opp:
                continue
            if float(m.get("pub", 0.0)) < 0.14:
                continue
            u = 6.0 * float(m["pub"]) - 1.5 * math.log(max(1.0, float(m["price"])))
            if u > best_u:
                best_m, best_u = m, u
        if best_m is not None:
            self.opp_flag_pub = float(best_m["pub"])
            self.opp_flag_price = float(best_m["price"])
            self.opp_flag_N = float(best_m.get("N") or 0.0)
            self.opp_flag_id = str(best_m["id"])
        else:
            self.opp_flag_pub = 0.0
            self.opp_flag_price = 10.0
            self.opp_flag_N = 0.0
            self.opp_flag_id = None

        self.is_active_opp = (
            self.opp_staff_n != 20
            or self.opp_raises >= 1
            or self.opp_bought_power() >= 2200.0
            or self.opp_flag_pub >= 0.34
            or (self.opp_flag_id is not None and abs(self.opp_flag_price - 10.0) > 0.25)
        )
        if t >= ticks - 3400:
            self.regime = "ENDGAME_DEFENSE"
        elif self.is_active_opp and (self.opp_share >= 0.26 or (self.opp_flag_pub >= 0.38 and self.opp_flag_price <= 13.0)):
            self.regime = "SHARE_DEFENSE"
        elif self.is_active_opp:
            self.regime = "SCALE_COUNTER"
        else:
            self.regime = "SOLO_FRONTIER"


class TacticalState:
    """Complete environment state, domain telemetry, run trackers, and financial bookkeeper."""

    def __init__(self, init: dict):
        self.seat = int(init["seat"])
        self.me = "AB"[self.seat]
        self.opp = "BA"[self.seat]
        self.ticks = int(init.get("ticks", 26280))
        self.rules = dict(init["rules"])
        self.gpu = {g["model"]: g for g in init["gpus"]}
        self.regions = list(init["regions"])
        self.techs = dict(init["techs"])

        self.U: dict[str, float] = {d["name"]: float(d["public_b"]) for d in init["domains"]}
        self.U.setdefault("synth", 10.0)
        self.U.setdefault("distill", 0.0)
        self.q_hat: dict[str, float] = {d["name"]: float(d["q_hint"]) for d in init["domains"]}
        self.q_hat["synth"] = 0.72
        self.q_hat["distill"] = 0.90
        self.c_hat: dict[str, float] = {d["name"]: 0.062 for d in init["domains"]}
        self.c_hat["synth"] = 0.0
        self.c_hat["distill"] = 0.0
        self.filtered: set[str] = set()
        self.domain_loss_ema: dict[str, float] = {}

        self.t = 0
        self.cash = float(init["start"]["cash"])
        self.equity = 1.0
        self.rep = 0.0
        self.bk = False
        self.gpu_obs: dict[str, list[int]] = {}
        self.runs_obs: dict[str, list] = {}
        self.ev: list[dict] = []
        self.res: list[dict] = []
        self.wk: dict | None = None
        self.offer: dict[str, dict] = {}
        self.deals: list[dict] = []
        self.fleet_changed = True

        self.heat = 1.0
        self.demand = float(self.rules.get("Q0", 5.0))
        self.shares: dict[str, float] = {"N1": 0.5, "N2": 0.5}
        self.my_share = 0.0
        self.drop = 0.0
        self.wk_dem_acc = 0.0
        self.wk_srv_acc = 0.0
        self.wk_rev_acc = 0.0
        self.last_wk_rev = 0.0
        self.models: dict[str, dict] = {m["id"]: dict(m) for m in init.get("models", [])}
        self.my_models: dict[str, dict] = {}
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
        self.pts: dict[str, float] = {}
        self.deliv: list[list] = []
        self.arrivals: list[tuple[int, str, int]] = []
        self.rent_prices: dict[str, float] = {}
        self.rent_fresh_t = -10**9
        self.elec_prices: list[float] = [r["price"] for r in self.regions]
        self.rent_blocked_until: dict[str, int] = {}
        self.leases: dict[int, dict] = {}
        self.pending_rent_meta: list[tuple[str, int, str]] = []

        self.gm_win = int(self.rules.get("GM_WINDOW", 2190))
        self.rev_ring = [0.0] * self.gm_win
        self.hw_batches: list[list] = [["G1", 512, self.gpu["G1"]["price"], 0]]
        self.local_pend_mw: dict[int, float] = {}
        self.site_count = 1
        self.last_site_build_t = -10**9
        self.ppa_until: dict[int, int] = {}
        self.anchor_u: float | None = None
        self.last_price_exec_day: dict[str, int] = {}
        self.raised_q: set[int] = set()
        self.last_opp_poach_wk = -100
        self.distill_pending = 0.0
        self.synth_pending = 0.0
        self.synth_bootstrapped = False
        self.synth_ready_t = 10**9
        self.max_teacher_c = 0.0
        self.mix_dirty = False
        self.bc0 = 360.0
        self.bc0_known = False

        self.run_ledger: dict[str, dict] = {}
        self.intel = OpponentIntel(self.me, self.opp, self.gpu)

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

    def compute_of(self, cmap: dict[str, int]) -> float:
        gpu = self.gpu
        return sum(gpu[m]["power"] * n for m, n in cmap.items() if n > 0)

    def own_usable(self) -> dict[str, int]:
        return {m: int(v[0]) for m, v in self.gpu_obs.items() if v[0] > 0}

    def rented_usable(self) -> dict[str, int]:
        return {m: int(v[3]) for m, v in self.gpu_obs.items() if v[3] > 0}

    def stable_rent_compute(self, horizon: int = 26) -> float:
        cutoff = self.t + horizon
        gpu = self.gpu
        return sum(gpu[l["m"]]["power"] * l["n"] for l in self.leases.values() if l["end"] > cutoff)

    def deliv_power_by(self, tick_limit: int) -> float:
        gpu = self.gpu
        return sum(gpu[d[1]]["power"] * int(d[2]) for d in self.deliv if int(d[0]) <= tick_limit)

    def kw_committed(self) -> float:
        pue = self.pue()
        gpu = self.gpu
        kw = sum((int(v[0]) + int(v[1]) + int(v[2])) * gpu[m]["kw"] * pue for m, v in self.gpu_obs.items())
        kw += sum(int(d[2]) * gpu[d[1]]["kw"] * pue for d in self.deliv)
        return kw

    def mw_capacity(self) -> float:
        return sum(
            float(s["mw"]) + max(float(s.get("pend_mw", 0.0)), self.local_pend_mw.get(int(s["id"]), 0.0))
            for s in self.sites
        )

    def model_u(self, pub: float, price: float) -> float:
        return 6.0 * pub - 1.5 * math.log(price if price > 1.0 else 1.0)

    def flagship(self) -> dict | None:
        best, bu = None, -1e18
        mods = self.models
        for mid, meta in self.my_models.items():
            if meta["delisted"]:
                continue
            m = mods.get(mid)
            if not m:
                continue
            pub = float(m["pub"])
            if pub < 0.145:
                continue
            u = 6.0 * pub - 1.5 * math.log(max(float(m["price"]), 1.0))
            if u > bu:
                best, bu = m, u
        return best

    def best_own_pub(self) -> float:
        mods = self.models
        best = 0.0
        for mid, meta in self.my_models.items():
            if not meta["delisted"] and mid in mods:
                p = float(mods[mid]["pub"])
                if p > best:
                    best = p
        return best

    def best_teacher(self, allow_opp: bool = False) -> dict | None:
        open_m = self.models.get("OPEN-1")
        best_npc = None
        best_opp = None
        for m in self.models.values():
            o = m["owner"]
            if o in ("N1", "N2"):
                if best_npc is None or float(m["pub"]) > float(best_npc["pub"]):
                    best_npc = m
            elif o == self.opp and float(m.get("pub", 0.0)) >= 0.15:
                if best_opp is None or float(m["pub"]) > float(best_opp["pub"]):
                    best_opp = m
        if open_m is not None and (best_npc is None or float(open_m["pub"]) >= float(best_npc["pub"]) - 0.07):
            return open_m
        if allow_opp and best_opp is not None:
            npc_p = float(best_npc["pub"]) if best_npc else 0.0
            if float(best_opp["pub"]) >= npc_p + 0.05 and float(best_opp["price"]) <= 24.0:
                return best_opp
        return best_npc

    def init_ledger(self, name: str, mix: dict[str, float]) -> None:
        s = sum(mix.values()) or 1.0
        nm = {k: v / s for k, v in mix.items() if v > 0}
        self.run_ledger[name] = {
            "D": 0.0,
            "S": {p: 0.0 for p in ALL_POOLS},
            "S_pre": {d: 0.0 for d in REAL_DOMAINS},
            "S_post": {d: 0.0 for d in REAL_DOMAINS},
            "mix": nm,
            "mix_items": tuple(nm.items()),
            "ckpts": {},
        }

    def drop_ledger(self, name: str) -> None:
        self.run_ledger.pop(name, None)

    def update_ledger_mix(self, name: str, mix: dict[str, float]) -> None:
        ld = self.run_ledger.get(name)
        if ld is not None:
            s = sum(mix.values()) or 1.0
            nm = {k: v / s for k, v in mix.items() if v > 0}
            ld["mix"] = nm
            ld["mix_items"] = tuple(nm.items())

    def est_v_pre(self) -> float:
        t = max(0, self.t)
        hw = sum(b[1] * b[2] * max(0.0, 1.0 - (t - b[3]) / 26280.0) for b in self.hw_batches)
        gm = max(0.0, sum(self.rev_ring) * 0.88)
        own_pub = self.best_own_pub()
        other_pub = max((float(m["pub"]) for m in self.models.values() if m["owner"] != self.me), default=0.0)
        v_raw = 24.0 * gm + 300.0 * max(0.0, own_pub - other_pub) + self.cash + hw
        v_true = self.cash + hw + 24.0 * gm + 300.0 * own_pub
        return max(0.0, min(v_raw, v_true))

    def ingest(self, obs: dict, main_run: str | None) -> None:
        t = obs["t"]
        self.t = t
        self.cash = obs["cash"]
        if "eq" in obs:
            self.equity = obs["eq"]
        self.rep = obs.get("rep", 0.0)
        self.bk = obs.get("bk", False)
        self.gpu_obs = obs.get("gpu", {})
        runs_list = obs.get("runs")
        self.runs_obs = {r[0]: r for r in runs_list} if runs_list else {}
        ev = obs.get("ev")
        res = obs.get("res")
        self.ev = ev if ev else ()
        self.res = res if res else ()
        self.distill_pending = 0.0
        self.fleet_changed = False

        api = obs.get("api")
        if api:
            self.wk_dem_acc += api[0]
            self.wk_srv_acc += api[1]
            self.wk_rev_acc += api[2]
            self.rev_ring[t % self.gm_win] = api[2]

        if main_run and main_run in self.run_ledger:
            r = self.runs_obs.get(main_run)
            if r is not None:
                ld = self.run_ledger[main_run]
                obs_d = r[2]
                tpt = r[4]
                ckid = r[6]
                if obs_d + 1e-4 < ld["D"]:
                    snap = ld["ckpts"].get(ckid)
                    if snap is not None:
                        ld["D"] = snap["D"]
                        ld["S"] = dict(snap["S"])
                        ld["S_pre"] = dict(snap["S_pre"])
                        ld["S_post"] = dict(snap["S_post"])
                    elif ld["D"] > 0:
                        sc = obs_d / ld["D"]
                        ld["D"] = obs_d
                        for k in ld["S"]:
                            ld["S"][k] *= sc
                        for d in REAL_DOMAINS:
                            ld["S_pre"][d] *= sc
                            ld["S_post"][d] *= sc
                elif tpt > 0:
                    dx = obs_d - ld["D"]
                    if dx <= 0:
                        dx = tpt
                    ld["D"] = obs_d
                    S = ld["S"]
                    S_pre = ld["S_pre"]
                    S_post = ld["S_post"]
                    filt = self.filtered
                    for k, f in ld["mix_items"]:
                        tok = dx * f
                        S[k] += tok
                        if k in S_post:
                            if k in filt:
                                S_post[k] += tok
                            else:
                                S_pre[k] += tok
                if ckid > 0 and ckid not in ld["ckpts"]:
                    ld["ckpts"][ckid] = {
                        "D": ld["D"],
                        "S": dict(ld["S"]),
                        "S_pre": dict(ld["S_pre"]),
                        "S_post": dict(ld["S_post"]),
                    }
                    if len(ld["ckpts"]) > 12:
                        del ld["ckpts"][min(ld["ckpts"])]

        leases = self.leases
        if leases:
            expired = [k for k, v in leases.items() if v["end"] <= t]
            if expired:
                for lid in expired:
                    del leases[lid]
                self.fleet_changed = True

        if "rent" in obs:
            self.rent_prices = dict(obs["rent"])
            self.rent_fresh_t = t
        if "elec" in obs:
            self.elec_prices = list(obs["elec"])
        if "pts" in obs:
            self.pts = {k: float(v) for k, v in obs["pts"].items()}
        if "loss" in obs and main_run and main_run in obs["loss"]:
            lvec = obs["loss"][main_run]
            if len(lvec) == 7:
                ema = self.domain_loss_ema
                for dom, lv in zip(REAL_DOMAINS, lvec):
                    prev = ema.get(dom, lv)
                    ema[dom] = 0.55 * prev + 0.45 * lv

        if ev:
            self.intel.update_events(ev)
            for e in ev:
                ty = e.get("type")
                if ty == "release":
                    m = dict(e["model"])
                    self.models[m["id"]] = m
                    if m["owner"] == self.me:
                        self.my_models.setdefault(m["id"], {"live_t": t, "delisted": False})
                        self.my_models[m["id"]]["live_t"] = t
                        self.fleet_changed = True
                elif ty == "open_source":
                    m = dict(e["model"])
                    self.models[m["id"]] = m
                    self.mix_dirty = True
                elif ty == "tech":
                    tk = e["tech"]
                    self.tech.add(tk)
                    if tk.startswith("filter:") and "q" in e:
                        d = tk[7:]
                        self.q_hat[d] = float(e["q"])
                        self.c_hat[d] = float(e["c"])
                        self.filtered.add(d)
                        self.mix_dirty = True
                    elif tk in ("curriculum", "synth"):
                        self.mix_dirty = True
                    elif tk == "cooling":
                        self.fleet_changed = True
                elif ty == "audit_fail":
                    mid = e.get("model")
                    if mid in self.models:
                        self.models[mid]["pub"] = float(e["pub"])
                elif ty == "delivered":
                    m, n = e["model"], int(e["n"])
                    self.arrivals.append((t, m, n))
                    self.hw_batches.append([m, n, self.gpu[m]["price"] * self.heat, t])
                    self.fleet_changed = True
                elif ty in ("preempted", "site_ready", "expanded", "curtail"):
                    if ty == "preempted":
                        leases.pop(int(e.get("rent_id", -1)), None)
                    self.fleet_changed = True

        if res:
            for r in res:
                act = r.get("act")
                if r.get("ok"):
                    if act == "release":
                        mid = r["model"]
                        self.my_models.setdefault(mid, {"live_t": int(r.get("live", t + 24)), "delisted": False})
                    elif act == "rent":
                        rid = int(r["id"])
                        m = r["model"]
                        tlen = 168
                        kind = r.get("kind", "ondemand")
                        for idx, (pm, pt, pk) in enumerate(self.pending_rent_meta):
                            if pm == m and pk == kind:
                                tlen = pt
                                del self.pending_rent_meta[idx]
                                break
                        leases[rid] = {"m": m, "n": int(r["n"]), "kind": kind, "end": t + tlen}
                        self.fleet_changed = True
                    elif act == "distill":
                        tok = float(r.get("tokens_b", 0.0))
                        mid = r.get("model")
                        tm = self.models.get(mid)
                        q_new = (0.5 + 0.8 * float(tm["pub"])) if tm else 0.90
                        if tm:
                            self.max_teacher_c = max(self.max_teacher_c, float(tm["pub"]))
                        u_old = self.U.get("distill", 0.0)
                        q_old = self.q_hat.get("distill", q_new)
                        self.U["distill"] = u_old + tok
                        if self.U["distill"] > 0:
                            self.q_hat["distill"] = (u_old * q_old + tok * q_new) / self.U["distill"]
                        self.mix_dirty = True
                    elif act == "synth":
                        tok = float(r.get("tokens_b", 0.0))
                        self.synth_pending += tok
                        self.synth_bootstrapped = True
                        self.synth_ready_t = int(r.get("ready", t + 24))
                    elif act == "bid_data":
                        self.mix_dirty = True
                    elif act == "poach" and r.get("from") == "opp":
                        self.last_opp_poach_wk = t // 168
                    elif act == "expand_site":
                        sid = int(r["site"])
                        self.local_pend_mw[sid] = self.local_pend_mw.get(sid, 0.0) + float(r["mw"])
                    elif act == "build_site":
                        self.site_count += 1
                else:
                    if act == "rent":
                        for mk in GPUS:
                            self.rent_blocked_until[mk] = max(self.rent_blocked_until.get(mk, 0), t + 48)
            self.pending_rent_meta = []

        if self.synth_pending > 0 and t >= self.synth_ready_t:
            u_old = self.U.get("synth", 10.0)
            added = self.synth_pending
            q_add = 0.60 if self.best_own_pub() < 0.1 else (0.60 + 0.60 * self.best_own_pub())
            self.U["synth"] = u_old + added
            self.q_hat["synth"] = (u_old * self.q_hat.get("synth", 0.72) + added * q_add) / max(1.0, self.U["synth"])
            self.synth_pending = 0.0
            self.mix_dirty = True

        if "wk" in obs:
            wk = obs["wk"]
            self.wk = wk
            self.offer = dict(wk.get("offer") or {})
            self.deals = list(wk.get("deals") or [])
            self.heat = float(wk.get("heat", self.heat))
            self.demand = float(wk.get("demand", self.demand))
            self.shares = dict(wk.get("shares", self.shares))
            self.my_share = float(self.shares.get(self.me, 0.0))
            self.last_wk_rev = self.wk_rev_acc
            self.wk_dem_acc = 0.0
            self.wk_srv_acc = 0.0
            self.wk_rev_acc = 0.0
            live_ids = set()
            for m in wk.get("models", []):
                self.models[m["id"]] = dict(m)
                live_ids.add(m["id"])
            for mid in list(self.models):
                if mid not in live_ids:
                    del self.models[mid]
            my = wk["my"]
            self.staff = [{"id": int(s[0]), "ab": int(s[1]), "sal": float(s[2])} for s in my["staff"]]
            self.sites = list(my["sites"])
            self.local_pend_mw = {int(s["id"]): float(s.get("pend_mw", 0.0)) for s in self.sites}
            self.site_count = len(self.sites)
            for k, v in my["data"].items():
                self.U[k] = float(v)
            self.tech = set(my["tech"])
            self.drop = float(my.get("drop", 0.0))
            self.deliv = list(my.get("deliv", []))
            self.intel.update_weekly(wk, self.models, self.heat, t, self.ticks)
            fl = self.flagship()
            self.anchor_u = self.model_u(float(fl["pub"]), float(fl["price"])) if fl else None
            self.fleet_changed = True
        else:
            self.wk = None


class CurriculumRnDEngine:
    """Focused single-tech R&D blitz, ROI-gated talent poaching, and exact marginal-gradient set_mix."""

    # Zero-penalty order: algo_eff_1 (~t=320) -> synth (~t=590) -> curriculum (~t=830) ->
    # algo_eff_2 (~t=1160) -> early_supply (~t=1430) -> cooling/infer_opt_1/2 (~t=2050) ->
    # algo_eff_3 (~t=2850) -> stable (~t=3020) -> all 7 filter:* (~t=4000), then fire all staff!
    TECH_ORDER = (
        "algo_eff_1", "synth", "curriculum", "algo_eff_2", "early_supply",
        "cooling", "infer_opt_1", "infer_opt_2", "algo_eff_3", "stable",
        "filter:web", "filter:code", "filter:multiling", "filter:books",
        "filter:science", "filter:dialog", "filter:math",
    )

    def __init__(self, st: TacticalState):
        self.st = st
        self.research_cur: str | None = None
        self.last_data_t = -10**9

    def all_done(self) -> bool:
        return all(k in self.st.tech for k in self.TECH_ORDER)

    def daily_research(self, act: dict) -> None:
        if not self.st.staff:
            return
        pick = None
        for tk in self.TECH_ORDER:
            if tk in self.st.tech:
                continue
            pre = self.st.techs.get(tk, {}).get("pre")
            if pre and pre not in self.st.tech:
                continue
            pick = tk
            break
        if pick is not None and pick != self.research_cur:
            self.research_cur = pick
            act["research"] = [{"tech": pick, "share": 1.0}]

    def weekly_talent_and_poaching(self, act: dict) -> None:
        if self.all_done():
            if self.st.staff:
                sev_mult = float(self.st.rules.get("FIRE_SEVERANCE", 168))
                sev = sum(s["sal"] for s in self.st.staff) * sev_mult
                if self.st.cash > sev + 1.5:
                    act["fire"] = [s["id"] for s in self.st.staff]
                else:
                    # Fire highest-salary staff first within cash so we stop bleeding salary immediately!
                    rem_c = max(0.0, self.st.cash - 1.0)
                    fids = []
                    for s in sorted(self.st.staff, key=lambda x: -x["sal"]):
                        c_s = s["sal"] * sev_mult
                        if rem_c >= c_s:
                            fids.append(s["id"])
                            rem_c -= c_s
                    if fids:
                        act["fire"] = fids
            return

        cur_n = len(self.st.staff)
        pts_rem = sum(
            max(0.0, float(self.st.techs[k]["cost"]) - self.st.pts.get(k, 0.0))
            for k in self.TECH_ORDER
            if k not in self.st.tech
        )
        cur_pts = max(1, sum(s["ab"] for s in self.st.staff))
        if pts_rem / cur_pts < 600.0:
            return

        cap = 60 if self.st.intel.is_active_opp else 58
        if cur_n < cap and self.st.cash > 44.0:
            talent = self.st.wk.get("talent") or []
            scored = []
            for c in talent:
                lo = int(c["lo"])
                if lo < 2:
                    continue
                hi = float(c["hi"])
                ask = float(c["ask"])
                ab_est = _clip((ask / 0.000105 - 0.5) * 2.0, float(lo), hi)
                eff = ab_est / max(1e-9, ask)
                scored.append((eff, ab_est, lo, c))
            scored.sort(key=lambda x: (-x[0], -x[1]))
            hires = []
            opp_hot = self.st.intel.opp_staff_n >= 28
            max_per_wk = 12 if self.st.t < 600 else 10
            for _, _, lo, c in scored:
                if cur_n + len(hires) >= cap or len(hires) >= max_per_wk:
                    break
                mult = (1.31 if opp_hot else 1.26) if lo >= 3 else (1.20 if opp_hot else 1.14)
                hires.append({"cand": int(c["id"]), "salary": round(float(c["ask"]) * mult, 7)})
            if hires:
                act["hire"] = hires

        wk_num = self.st.t // 168
        if cur_n >= cap or self.st.cash < 68.0 or self.st.t > 5500 or pts_rem < 90000:
            return
        poaches = []
        if (
            self.st.intel.opp_staff_n >= 24
            and wk_num >= 2
            and wk_num - self.st.last_opp_poach_wk >= 2
        ):
            n_opp = self.st.intel.opp_staff_n
            sal_offer = 0.00048
            signing = sal_offer * float(self.st.rules.get("POACH_OPP_SIGNING", 336))
            if self.st.cash > 2.0 * signing + 58.0:
                poaches.append({"target": "opp", "idx": max(0, n_opp - 1), "salary": sal_offer})
                if n_opp >= 2:
                    poaches.append({"target": "opp", "idx": n_opp - 2, "salary": sal_offer})
        if poaches:
            act["poach"] = poaches

    def sunday_data_deals(self, act: dict, cash_left: float) -> None:
        t = self.st.t
        if t < 6500 or t > self.st.ticks - 4200 or t - self.last_data_t < 168 or cash_left < 85.0:
            return
        deals = self.st.deals
        if not deals:
            return
        best = None
        for d in deals:
            dom = d["domain"]
            if dom in ("web", "multiling"):
                continue
            qh = float(d["q_hint"])
            tok = float(d["tokens_b"])
            u_cur = max(1.0, self.st.U.get(dom, 100.0))
            if dom in ("math", "dialog", "science"):
                if qh < 0.85 or tok < 0.45 * u_cur:
                    continue
            else:
                if qh < 1.15 or tok < 0.55 * u_cur:
                    continue
            res = float(d["reserve"])
            pr = round(res * min(1.75, 1.32 + 0.35 * max(0.0, qh - 0.85)), 3)
            if pr > 18.0 or cash_left < max(5.5 * pr, pr + 65.0):
                continue
            rel_gain = (tok / u_cur) * qh * PRIOR_W.get(dom, 0.8)
            if dom in self.st.domain_loss_ema and self.st.domain_loss_ema:
                avg_l = sum(self.st.domain_loss_ema.values()) / len(self.st.domain_loss_ema)
                if self.st.domain_loss_ema[dom] > avg_l:
                    rel_gain *= 1.30
            roi = rel_gain / max(1.0, pr)
            if best is None or roi > best[0]:
                best = (roi, int(d["id"]), pr)
        if best is not None:
            act["bid_data"] = [{"deal": best[1], "price": best[2]}]
            self.last_data_t = t

    def compute_optimal_mix(
        self,
        run_name: str | None,
        chunk_d: float,
        use_distill: bool,
        distill_cap: float,
        use_synth: bool,
        beta: float = 0.30,
        harvest_contam: bool = True,
    ) -> dict[str, float]:
        """Exact marginal-gradient domain mix over next chunk_d tokens with strict safety bounds."""
        ld = self.st.run_ledger.get(run_name) if run_name else None
        cur_D = ld["D"] if ld else 0.0
        cur_S = dict(ld["S"]) if ld else {p: 0.0 for p in ALL_POOLS}
        cur_cont = 0.0
        if ld and cur_D > 0:
            for d in REAL_DOMAINS:
                c_post = self.st.c_hat[d]
                c_pre = 2.0 * c_post if d in self.st.filtered else 0.072
                cur_cont += ld["S_pre"][d] * c_pre + ld["S_post"][d] * c_post

        tot_D = cur_D + max(1.0, chunk_d)
        max_synth_f = 0.0
        if use_synth and self.st.U.get("synth", 0.0) >= 50.0:
            rem_s = max(0.0, 0.296 * tot_D - cur_S.get("synth", 0.0))
            max_synth_f = _clip(rem_s / max(1.0, chunk_d), 0.0, 0.45)
        max_dist_f = 0.0
        if use_distill and self.st.U.get("distill", 0.0) + self.st.distill_pending >= 2.0:
            rem_di = max(0.0, distill_cap * tot_D - cur_S.get("distill", 0.0))
            max_dist_f = _clip(rem_di / max(1.0, chunk_d), 0.0, distill_cap)

        w_dom = dict(PRIOR_W)
        if len(self.st.domain_loss_ema) == 7:
            avg_l = sum(self.st.domain_loss_ema.values()) / 7.0
            for d in REAL_DOMAINS:
                delta = _clip(self.st.domain_loss_ema[d] - avg_l, -0.15, 0.30)
                w_dom[d] *= 1.0 + 0.85 * delta

        all_filt = len(self.st.filtered) == 7
        cont_target = 0.0475 if all_filt else 0.036

        pools = list(REAL_DOMAINS)
        if max_synth_f > 0.01:
            pools.append("synth")
        if max_dist_f > 0.005:
            pools.append("distill")

        mix = {p: 1.0 / len(pools) for p in pools}
        if "synth" in mix:
            mix["synth"] = min(max_synth_f, 0.295)
        if "distill" in mix:
            mix["distill"] = min(max_dist_f, distill_cap)

        def project(m: dict[str, float]) -> dict[str, float]:
            out = {p: max(0.001 if p in REAL_DOMAINS else 0.0, m.get(p, 0.0)) for p in pools}
            s_all = sum(out.values()) or 1.0
            for k in out:
                out[k] /= s_all
            if "synth" in out and out["synth"] > max_synth_f:
                out["synth"] = max_synth_f
            if "distill" in out and out["distill"] > max_dist_f:
                out["distill"] = max_dist_f
            if not all_filt and "synth" in out and out["synth"] < max_synth_f * 0.90:
                out["synth"] = min(max_synth_f, max(out["synth"], max_synth_f * 0.96))
            fixed = out.get("synth", 0.0) + out.get("distill", 0.0)
            rem = max(0.10, 1.0 - fixed)
            r_sum = sum(out[d] for d in REAL_DOMAINS) or 1.0
            for d in REAL_DOMAINS:
                out[d] = out[d] / r_sum * rem
            if not all_filt:
                dom_cap = rem * 0.24
                ex = 0.0
                for d in REAL_DOMAINS:
                    if d not in self.st.filtered and out[d] > dom_cap:
                        ex += out[d] - dom_cap
                        out[d] = dom_cap
                if ex > 1e-6:
                    others = [d for d in REAL_DOMAINS if out[d] < dom_cap - 1e-6]
                    if others:
                        zo = sum(out[d] for d in others) or 1.0
                        for d in others:
                            out[d] += ex * out[d] / zo
            wb = out["web"] + out["books"]
            if wb > 0.475:
                sc = 0.475 / wb
                ex = wb - 0.475
                out["web"] *= sc
                out["books"] *= sc
                others = [d for d in REAL_DOMAINS if d not in ("web", "books")]
                zo = sum(out[d] for d in others) or 1.0
                for d in others:
                    out[d] += ex * out[d] / zo
            for _ in range(8):
                next_cont = cur_cont + chunk_d * sum(
                    out[d] * (self.st.c_hat[d] if d in self.st.filtered else 0.075)
                    for d in REAL_DOMAINS
                )
                if next_cont / tot_D <= cont_target:
                    break
                worst = max(
                    REAL_DOMAINS,
                    key=lambda d: (self.st.c_hat[d] if d in self.st.filtered else 0.075) * out[d],
                )
                if out[worst] <= 0.005:
                    break
                cut = out[worst] * 0.30
                out[worst] -= cut
                lows = sorted(
                    REAL_DOMAINS,
                    key=lambda d: (self.st.c_hat[d] if d in self.st.filtered else 0.075),
                )[:3]
                zl = sum(out[d] for d in lows) or 1.0
                for d in lows:
                    out[d] += cut * out[d] / zl
            return out

        mix = project(mix)
        U = self.st.U
        q_hat = self.st.q_hat

        for it in range(16):
            E_k = {}
            dE_dk = {}
            for p in pools:
                u_k = max(1e-3, U.get(p, 1.0))
                s_k = cur_S.get(p, 0.0) + chunk_d * mix[p]
                q_k = max(0.35, q_hat.get(p, 0.8))
                ex = math.exp(-min(40.0, s_k / (4.0 * u_k)))
                E_k[p] = 4.0 * u_k * (1.0 - ex) * q_k
                dE_dk[p] = ex * q_k

            D_eff = {}
            for d in REAL_DOMAINS:
                row = PRIOR_T[d]
                de = 0.01
                for p, ev in E_k.items():
                    de += row[p] * ev
                D_eff[d] = de

            next_cont = cur_cont + chunk_d * sum(
                mix[d] * (self.st.c_hat[d] if d in self.st.filtered else 0.075)
                for d in REAL_DOMAINS
            )
            cont_room = cont_target - next_cont / tot_D
            c_bonus_w = 0.24 if (harvest_contam and all_filt and cont_room > 0.002) else 0.0

            grads = {}
            for p in pools:
                g = 0.0
                de_p = dE_dk[p]
                for d in REAL_DOMAINS:
                    g += w_dom[d] * beta * PRIOR_T[d][p] * (D_eff[d] ** -(beta + 1.0))
                g *= de_p * chunk_d
                if c_bonus_w > 0.0 and p in REAL_DOMAINS:
                    g += c_bonus_w * self.st.c_hat[p] * (chunk_d / tot_D)
                grads[p] = g

            g_scale = max(1e-9, sum(abs(v) for v in grads.values()) / len(grads))
            eta = 0.85 / (1.0 + 0.14 * it)
            nxt = {}
            for p in pools:
                nxt[p] = mix[p] * math.exp(_clip(eta * grads[p] / g_scale, -1.5, 1.5))
            mix = project(nxt)

        s = sum(mix.values()) or 1.0
        return {k: round(v / s, 5) for k, v in mix.items() if v / s > 1e-4}


class AdaptiveCounterPricer:
    """Steady-state equilibrium pricer (mid-game) + Capacity-aware Exact Backward-DP (endgame)."""

    def __init__(self, st: TacticalState):
        self.st = st
        self.p_grid = [round(1.8 * (1.135 ** i), 3) for i in range(32)]
        if self.p_grid[-1] < 100.0:
            self.p_grid.append(100.0)
        self.s_grid = [i / 25.0 for i in range(26)]
        self.max_next_j = []
        nP = len(self.p_grid)
        for i in range(nP):
            lim = self.p_grid[i] * 1.485
            mj = i
            while mj + 1 < nP and self.p_grid[mj + 1] <= lim:
                mj += 1
            self.max_next_j.append(mj)

    def competitor_utils(self) -> list[float]:
        rivals: dict[str, float] = {}
        me = self.st.me
        for m in self.st.models.values():
            o = m["owner"]
            if o == me:
                continue
            u = 6.0 * float(m["pub"]) - 1.5 * math.log(max(float(m["price"]), 1.0))
            if o not in rivals or u > rivals[o]:
                rivals[o] = u
        return list(rivals.values())

    def est_share(
        self,
        pub: float,
        price: float,
        drop: float = 0.0,
        rep: float | None = None,
        rival_us: list[float] | None = None,
    ) -> float:
        r_us = self.competitor_utils() if rival_us is None else rival_us
        rep_v = self.st.rep if rep is None else rep
        u_me = 6.0 * pub - 1.5 * math.log(price if price > 1.0 else 1.0) + rep_v - 5.0 * drop
        if not r_us:
            return 1.0
        mx = max(u_me, max(r_us))
        e_me = math.exp(u_me - mx)
        z = e_me + sum(math.exp(u - mx) for u in r_us)
        return e_me / z if z > 0 else 0.0

    def choose_price(
        self,
        fl: dict,
        s_cap_pre: float,
        own_c: float,
        est_drop: float,
    ) -> float:
        pub = float(fl["pub"])
        cur_p = float(fl["price"])
        n_b = float(fl.get("N") or 30.0)
        per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()

        e0 = self.st.elec_prices[0] if self.st.elec_prices else 0.0008
        mc_own = max(0.12, per * 0.22 * self.st.pue() * e0)
        rent_unit = min(
            (pr / self.st.gpu[m]["power"] for m, pr in self.st.rent_prices.items()),
            default=0.000018,
        )
        mc_pre = max(0.30, 0.25 * per * rent_unit * 1000.0)

        t = self.st.t
        T = self.st.ticks
        q11_start = T - int(self.st.rules.get("GM_WINDOW", 2190))
        in_q11_now = t >= q11_start - 168
        mc_now = mc_own if in_q11_now else mc_pre
        p_floor = max(2.2, mc_now * 1.25)

        if est_drop > 0.025:
            return self._guard_up(pub, max(p_floor, min(98.0, cur_p * 1.38)))

        rival_us = self.competitor_utils()

        # ---- Mid-game: steady-state equilibrium optimum (margin-protecting + GPU-preserving!) ----
        if t < q11_start - 1800:
            regime = self.st.intel.regime
            # Protect cash margins & training GPUs when rented; defend share harder once owned fleet scales
            if regime == "SHARE_DEFENSE" and own_c >= 6000.0:
                share_exp = 0.97
            elif self.st.my_share < 0.20:
                share_exp = 0.95
            else:
                share_exp = 0.91
            mc_mid = mc_pre if own_c < 12000.0 else (0.35 * mc_own + 0.65 * mc_pre)
            best_v, best_p = -1e18, max(p_floor, 7.5)
            p = p_floor
            for _ in range(72):
                if p > 90.0:
                    break
                s_eq = self.est_share(pub, p, 0.0, self.st.rep, rival_us)
                serv = s_eq if s_eq < s_cap_pre else s_cap_pre
                over = s_eq - s_cap_pre if s_eq > s_cap_pre else 0.0
                val = (serv ** share_exp) * (p - mc_mid) - over * p * 3.5
                if val > best_v:
                    best_v, best_p = val, p
                p *= 1.055
            s_eq_best = self.est_share(pub, best_p, 0.0, self.st.rep, rival_us)
            if self.st.my_share < min(s_cap_pre * 0.85, s_eq_best - 0.07):
                best_p = max(p_floor, best_p * 0.86)
            return round(_clip(best_p, p_floor, 95.0), 3)

        # ---- Endgame: EXACT capacity-aware Backward DP through T (separates pre-Q11 vs owned Q11!) ----
        w0_start = t if t % 168 == 0 else ((t // 168) + 1) * 168
        weeks = []
        ws = w0_start
        while ws < T:
            we = min(T, ws + 168)
            ov_q11 = max(0, we - max(ws, q11_start))
            ov_pre = (we - ws) - ov_q11
            wt = (ov_pre * 1.0 + ov_q11 * 25.0) / 168.0
            q_rel = 1.03 ** ((ws - w0_start) / 730.0)
            if ov_q11 > 0:
                comp_wk = (own_c + self.st.deliv_power_by(ws + 84)) * 0.94
                sc_wk = min(1.0, comp_wk / max(1e-6, self.st.demand * q_rel * 1.04 * per))
                mc_wk = mc_own
            else:
                sc_wk = s_cap_pre
                mc_wk = mc_pre
            weeks.append((wt * q_rel, sc_wk, mc_wk))
            ws = we

        if not weeks:
            return round(_clip(cur_p, p_floor, 100.0), 3)

        p_grid = self.p_grid
        nP = len(p_grid)
        s_grid = self.s_grid
        nS = len(s_grid)
        max_next_j = self.max_next_j
        t_shares = [self.est_share(pub, p, 0.0, self.st.rep, rival_us) for p in p_grid]

        V_w1 = [[0.0] * nP for _ in range(nS)]
        for wk_idx in range(len(weeks) - 1, 0, -1):
            wt, sc_wk, mc_wk = weeks[wk_idx]
            V_cur = [[0.0] * nP for _ in range(nS)]
            for si, s in enumerate(s_grid):
                serv = s if s < sc_wk else sc_wk
                over = s - sc_wk if s > sc_wk else 0.0
                best = -1e18
                pref = [0.0] * nP
                for pj, p in enumerate(p_grid):
                    if p < p_floor:
                        val = -1e18
                    else:
                        imm = wt * (serv * (p - mc_wk) - over * p * 3.8)
                        s_nx = s + _clip(t_shares[pj] - s, -0.15, 0.15)
                        idx_f = _clip(s_nx * 25.0, 0.0, 25.0)
                        i0 = int(idx_f)
                        if i0 >= 25:
                            fut = V_w1[25][pj]
                        else:
                            fr = idx_f - i0
                            fut = V_w1[i0][pj] * (1.0 - fr) + V_w1[i0 + 1][pj] * fr
                        val = imm + fut
                    if val > best:
                        best = val
                    pref[pj] = best
                row = V_cur[si]
                for pi in range(nP):
                    row[pi] = pref[max_next_j[pi]]
            V_w1 = V_cur

        s_now = self.st.my_share
        if t % 168 == 167:
            t_cur = self.est_share(pub, cur_p, est_drop, self.st.rep, rival_us)
            s_now = _clip(s_now + _clip(t_cur - s_now, -0.15, 0.15), 0.0, 1.0)

        p_up_lim = min(100.0, cur_p * 1.485)
        if self.st.anchor_u is not None and t % 168 == 0:
            min_u = self.st.anchor_u - 1.5 * math.log(1.485)
            p_up_lim = min(p_up_lim, math.exp(min(4.605, (6.0 * pub - min_u) / 1.5)))

        wt0, sc0, mc0 = weeks[0]
        serv0 = min(s_now, sc0)
        over0 = max(0.0, s_now - sc0)
        best_v, best_p = -1e18, _clip(cur_p, p_floor, p_up_lim)
        cands = [p for p in p_grid if p_floor <= p <= p_up_lim]
        cands.append(max(p_floor, p_up_lim))
        if p_floor <= cur_p <= p_up_lim:
            cands.append(cur_p)

        for p in cands:
            imm = wt0 * (serv0 * max(0.0, p - mc0) - over0 * p * 3.8)
            t_sh = self.est_share(pub, p, 0.0, self.st.rep, rival_us)
            s_nx = _clip(s_now + _clip(t_sh - s_now, -0.15, 0.15), 0.0, 1.0)
            pj = 0
            while pj + 1 < nP and p_grid[pj + 1] <= p:
                pj += 1
            idx_f = _clip(s_nx * 25.0, 0.0, 25.0)
            i0 = int(idx_f)
            if i0 >= 25:
                fut = V_w1[25][pj]
            else:
                fr = idx_f - i0
                fut = V_w1[i0][pj] * (1.0 - fr) + V_w1[i0 + 1][pj] * fr
            val = imm + fut
            if val > best_v:
                best_v, best_p = val, p

        return round(_clip(best_p, p_floor, 100.0), 3)

    def _guard_up(self, pub: float, p: float) -> float:
        if self.st.anchor_u is not None:
            min_u = self.st.anchor_u - 1.5 * math.log(1.485)
            p_max = math.exp(min(4.605, (6.0 * pub - min_u) / 1.5))
            p = min(p, p_max)
        return round(_clip(p, 1.0, 100.0), 3)


class AdaptiveTactician:
    """Top-level orchestrator for the Adaptive Counter-Play & R&D Tactician."""

    def __init__(self, init: dict):
        self.st = TacticalState(init)
        self.rnd = CurriculumRnDEngine(self.st)
        self.pricer = AdaptiveCounterPricer(self.st)

        self.p_stage = 0
        self.p_start = 0
        self.lr_p: dict[str, dict] = {}
        self.sc_p: dict[str, dict] = {}
        self.bc_probe_name = "tac_lr7"
        self.seed_released = False
        self.eta_c = 0.0018
        self.fit: dict[str, float] | None = None

        self.phase = 0  # 0=probes, 1=gen1(wsd), 2=gen2(wsd), 3=mega(cosine/wsd)
        self.main_run: str | None = None
        self.main_N = 14.0
        self.main_start = 0
        self.main_end = 1480
        self.wsd_locked: set[str] = set()
        self.gen1_synth_mixed = False
        self.mega_run: str | None = None
        self.mega_N = 50.0
        self.mega_sched = "cosine"
        self.mega_ready = False
        self.tuck_pending: str | None = None
        self.tuck_final_done = False

        T = self.st.ticks
        if T >= 20000:
            self.mega_milestones = [8100, 12400, 16800, 20600, 23280]
            self.final_release_t = T - 110  # t=26170 -> live=26194 -> 86 ticks online >= 72
        else:
            self.mega_milestones = []
            self.final_release_t = max(200, T - 110)
        self.milestone_idx = 0
        self.last_release_t = -10**9
        self.last_release_D = 0.0

        self.released_runs: set[str] = set()
        self.audit_sent: set[str] = set()
        self.run_seq = 0
        self.active_serve: dict[str, int] | None = None
        self.serve_auto = True
        self.last_train_ncards = -1
        self.last_train_gpus: dict[str, int] = {}
        self.last_split_t = -10**9
        self.last_mix_update_t = -10**9
        self._ckpt_cache: dict[int, tuple[int, float]] = {}

    def eta_star(self, n: float) -> float:
        return self.eta_c * (n ** -0.3)

    # ---- Exact checkpoint & throughput physics (cached) ----
    def ckpt_opt(self, ncards: int) -> tuple[int, float]:
        if ncards <= 0:
            return 24, 0.0
        key = ncards if ncards < 128 else (ncards // 4) * 4
        hit = self._ckpt_cache.get(key)
        if hit is not None:
            return hit
        f = ncards / float(self.st.rules.get("MTBF", 50000.0))
        if f <= 1e-9:
            res = (48, 1.0 - 0.2 / 48.0)
        else:
            p = 1.0 - f
            best_k, best_eta = 2, 0.0
            pk = p
            inv_f2 = 1.0 / f + 2.0
            for k in range(1, 52):
                if k > 1:
                    pk *= p
                denom = (1.0 - pk) * inv_f2
                eta = (pk * (k - 0.2)) / denom if denom > 1e-12 else 0.0
                if eta > best_eta:
                    best_eta = eta
                    best_k = k
            res = (best_k, max(0.05, best_eta))
        self._ckpt_cache[key] = res
        return res

    def waste_of(self, ncards: int, D: float) -> float:
        if ncards <= 0:
            return 0.0
        b = max(1, (ncards + 1) // 2)
        bc = self.st.bc0 * ((1.0 + max(0.0, D) / 50.0) ** 0.4)
        return (min(b, bc) + 0.5 * max(0.0, b - bc)) / b

    def eff_cluster_power(self, gmap: dict[str, int], D: float) -> float:
        ncards = sum(gmap.values())
        if ncards <= 0:
            return 0.0
        raw = self.st.compute_of(gmap)
        _, ck_eta = self.ckpt_opt(ncards)
        return raw * self.waste_of(ncards, D) * ck_eta

    def predict_deff_per_domain(self, D: float, mix: dict[str, float], u_syn_override: float | None = None) -> list[float]:
        U = self.st.U
        q_hat = self.st.q_hat
        z = sum(mix.values()) or 1.0
        E_k = {}
        for p, f in mix.items():
            if f <= 0:
                continue
            s_k = D * (f / z)
            u_k = u_syn_override if (p == "synth" and u_syn_override is not None) else U.get(p, 1.0)
            u_k = max(1e-3, u_k)
            q_k = max(0.35, q_hat.get(p, 0.9))
            E_k[p] = 4.0 * u_k * (1.0 - math.exp(-min(40.0, s_k / (4.0 * u_k)))) * q_k
        out = []
        for d in REAL_DOMAINS:
            row = PRIOR_T[d]
            de = 0.01
            for p, ev in E_k.items():
                de += row[p] * ev
            out.append(de)
        return out

    # ---- Stage 0-2: 8 zero-spike LR probes + B_crit + consistent D_eff OLS fit ----
    def step_probes(self, act: dict) -> bool:
        if self.p_stage >= 3:
            return True

        if not self.st.bc0_known and self.bc_probe_name in self.st.runs_obs:
            r = self.st.runs_obs[self.bc_probe_name]
            eff = float(r[5])
            if 0.45 < eff < 0.995:
                d_prev = max(0.0, float(r[2]) - float(r[4]))
                bc = 1600.0 * eff - 800.0
                bc0 = bc / ((1.0 + d_prev / 50.0) ** 0.4)
                if 170.0 <= bc0 <= 670.0:
                    self.st.bc0 = bc0
                    self.st.bc0_known = True

        for e in self.st.ev:
            if e.get("type") == "done":
                rn = e["run"]
                if rn == "tac_seed" and not self.seed_released:
                    act.setdefault("release", []).append({"run": "tac_seed", "price": 100.0})
                    self.seed_released = True
                    continue
                lm = sum(e["loss"]) / len(e["loss"])
                if rn in self.lr_p:
                    self.lr_p[rn]["loss"] = lm
                elif rn in self.sc_p:
                    self.sc_p[rn]["loss"] = lm

        c0 = float(self.st.rules.get("DEFAULT_LR_C", 0.0010))
        if self.p_stage == 0:
            runs = [{
                "name": "tac_seed", "params_b": 0.01, "tokens_b": 0.01,
                "lr": c0 * 1.5 * (0.01 ** -0.3), "batch": 1, "gpus": {"G1": 2},
                "sched": "const", "ckpt_every": 1,
            }]
            ks = (0.03, 0.05, 0.09, 0.16, 0.28, 0.50, 0.82, 1.16)
            mx = {d: 1.0 / 7.0 for d in REAL_DOMAINS}
            for i, k in enumerate(ks):
                name = f"tac_lr{i}"
                self.lr_p[name] = {"k": k, "loss": None, "failed": False}
                runs.append({
                    "name": name, "params_b": 0.32, "tokens_b": 5.5,
                    "lr": c0 * k * (0.32 ** -0.3),
                    "batch": 800 if name == self.bc_probe_name else 32,
                    "gpus": {"G1": 63},
                    "sched": "const", "ckpt_every": 24, "mix": mx,
                })
            act.setdefault("run", []).extend(runs)
            self.p_stage = 1
            self.p_start = self.st.t
            return False

        if self.p_stage == 1:
            self._prune(act, self.lr_p, 90)
            if all(v["loss"] is not None or v["failed"] for v in self.lr_p.values()):
                valid = [v for v in self.lr_p.values() if v["loss"] is not None]
                if len(valid) >= 4:
                    xs = [math.log(v["k"]) for v in valid]
                    ys = [v["loss"] - 0.08 * (math.log(v["k"]) ** 2) for v in valid]
                    mx_v = sum(xs) / len(xs)
                    my_v = sum(ys) / len(ys)
                    sxx = sum((x - mx_v) ** 2 for x in xs)
                    sxy = sum((x - mx_v) * (y - my_v) for x, y in zip(xs, ys))
                    if sxx > 1e-12:
                        k_star = _clip(math.exp(-(sxy / sxx) / 0.16), 1.18, 2.82)
                        self.eta_c = c0 * k_star
                grid = (
                    (0.08, 3.0), (0.08, 45.0),
                    (0.40, 6.0), (0.40, 80.0),
                    (1.80, 10.0), (1.80, 90.0),
                    (5.50, 10.0), (5.50, 32.0),
                )
                tot_nd = sum(n_ * d_ for n_, d_ in grid)
                raw_cards = [max(14, int(484 * n_ * d_ / tot_nd)) for n_, d_ in grid]
                while sum(raw_cards) > 504:
                    mi = max(range(len(raw_cards)), key=lambda idx: raw_cards[idx])
                    raw_cards[mi] -= 1
                mx = {d: 1.0 / 7.0 for d in REAL_DOMAINS}
                runs = []
                for i, (n_, d_) in enumerate(grid):
                    name = f"tac_sc{i}"
                    cards = raw_cards[i]
                    self.sc_p[name] = {"N": n_, "D": d_, "loss": None, "failed": False}
                    runs.append({
                        "name": name, "params_b": n_, "tokens_b": d_,
                        "lr": 0.955 * self.eta_star(n_),
                        "batch": max(8, (cards + 1) // 2),
                        "gpus": {"G1": cards}, "sched": "const",
                        "ckpt_every": self.ckpt_opt(cards)[0], "mix": mx,
                    })
                act.setdefault("run", []).extend(runs)
                self.p_stage = 2
                self.p_start = self.st.t
            return False

        if self.p_stage == 2:
            self._prune(act, self.sc_p, 480)
            if all(v["loss"] is not None or v["failed"] for v in self.sc_p.values()):
                valid = [v for v in self.sc_p.values() if v["loss"] is not None]
                if len(valid) >= 4:
                    pmix = {d: 1.0 / 7.0 for d in REAL_DOMAINS}
                    deffs = [self.predict_deff_per_domain(v["D"], pmix) for v in valid]
                    best = None
                    for i in range(25):
                        a = 0.28 + 0.005 * i
                        for j in range(21):
                            b = 0.25 + 0.005 * j
                            rows = [
                                (
                                    v["N"] ** -a,
                                    sum(de ** -b for de in deffs[idx]) / 7.0,
                                    v["loss"],
                                )
                                for idx, v in enumerate(valid)
                            ]
                            sol = _fit_ols3(rows)
                            if sol is None or not (0.15 <= sol[1] <= 0.90) or not (0.55 <= sol[2] <= 2.15):
                                continue
                            if best is None or sol[3] < best[0]:
                                best = (sol[3], a, b, sol[0], sol[1], sol[2])
                    if best:
                        _, a, b, e0, A, B = best
                        self.fit = {"alpha": a, "beta": b, "E": e0, "A": A, "B": B}
                self.p_stage = 3
                self.phase = 1
                return True
        return False

    def _prune(self, act: dict, probes: dict, lim: int) -> None:
        for name, v in probes.items():
            if v["loss"] is not None or v["failed"]:
                continue
            r = self.st.runs_obs.get(name)
            if (r and r[1] == "diverged" and r[6] == 0) or (self.st.t - self.p_start > lim):
                v["failed"] = True
                if r and r[1] not in ("done", "stopped"):
                    act.setdefault("run_ctl", []).append({"name": name, "op": "stop"})

    # ---- Bootstrap ultra-cheap 50T synth pool & adaptive distill top-up ----
    def maybe_bootstrap_synth(self, act: dict) -> None:
        if self.st.synth_bootstrapped or "synth" not in self.st.tech:
            return
        own_live = [m for m in self.st.models.values() if m["owner"] == self.st.me]
        if not own_live:
            return
        best = max(own_live, key=lambda m: float(m.get("pub", 0.0)))
        n_best = float(best.get("N") or 10.0)
        g1_r = self.st.rent_prices.get("G1", 0.05 / 8000.0 * 2.2)
        if n_best <= 0.1:
            tok = 50000.0
            cost = tok * n_best * 100.0 * g1_r
            if self.st.cash > cost * 1.4 + 25.0:
                act.setdefault("synth", []).append({"tokens_b": tok})
                self.st.synth_pending += tok
                self.st.synth_bootstrapped = True
                self.st.synth_ready_t = self.st.t + 25
        else:
            tok = min(4000.0, max(0.0, (self.st.cash - 50.0) * 0.18) / max(1e-9, n_best * 100.0 * g1_r * 1.3))
            if tok >= 300.0:
                act.setdefault("synth", []).append({"tokens_b": round(tok, 1)})
                self.st.synth_pending += tok
                self.st.synth_bootstrapped = True
                self.st.synth_ready_t = self.st.t + 25

    def ensure_distill_pool(self, act: dict, target_tok: float, allow_opp: bool = False) -> bool:
        cur_u = self.st.U.get("distill", 0.0) + self.st.distill_pending
        tm = self.st.best_teacher(allow_opp=allow_opp)
        if not tm:
            return cur_u >= 5.0
        if tm["owner"] == "OPEN":
            if cur_u < 15000.0:
                act.setdefault("distill", []).append({"model": tm["id"], "tokens_b": 25000.0})
                self.st.distill_pending += 25000.0
            return True
        want = max(cur_u, target_tok)
        need = want - cur_u
        if need <= 15.0:
            return cur_u >= 5.0
        unit_p = float(tm["price"]) / 1000.0
        max_spend = max(0.0, self.st.cash - 36.0) * 0.10
        max_spend = min(max_spend, 18.0 if self.st.t > 8000 else 5.0)
        can = min(need, max_spend / max(1e-9, unit_p * 1.05))
        if can >= 15.0:
            act.setdefault("distill", []).append({"model": tm["id"], "tokens_b": round(can, 1)})
            self.st.distill_pending += can
            return True
        return cur_u >= 5.0

    # ---- Dimensionally consistent scaling-law N selection ----
    def choose_N(
        self,
        budget_g1h: float,
        algo_mult: float,
        n_lo: float,
        n_hi: float,
        use_big_synth: bool,
        serve_pen: float = 0.0,
    ) -> float:
        kc = float(self.st.rules.get("KC", 300.0))
        if not self.fit:
            n0 = math.sqrt(max(1.0, budget_g1h) / kc / 25.0)
            return round(_clip(n0, n_lo, n_hi), 3)
        f = self.fit
        alpha, beta, A, B = f["alpha"], f["beta"], f["A"], f["B"]
        if use_big_synth:
            mix = {d: 0.61 / 7.0 for d in REAL_DOMAINS}
            mix["synth"] = 0.295
            mix["distill"] = 0.095
            u_syn = 50000.0
        else:
            mix = {d: 0.905 / 7.0 for d in REAL_DOMAINS}
            mix["distill"] = 0.095
            u_syn = None
        best = None
        for i in range(61):
            n = n_lo * ((n_hi / n_lo) ** (i / 60.0))
            d = budget_g1h / (kc * n)
            if d < 5.0:
                continue
            deffs = self.predict_deff_per_domain(d, mix, u_syn_override=u_syn)
            d_term = sum(de ** -beta for de in deffs) / 7.0
            loss = A * ((n * algo_mult) ** -alpha) + B * d_term
            obj = loss + serve_pen * (n / 100.0)
            if best is None or obj < best[0]:
                best = (obj, n)
        return round(best[1], 3) if best else round((n_lo + n_hi) * 0.5, 3)

    # ---- Card & serving allocation (strict Q11 isolation + NEVER under-serve!) ----
    def serve_need(self, safety: float = 1.08) -> float:
        fl = self.st.flagship()
        if not fl:
            return 0.0
        n_b = float(fl.get("N") or 30.0)
        per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
        t_sh = self.pricer.est_share(float(fl["pub"]), float(fl["price"]))
        s_next = _clip(self.st.my_share + _clip(t_sh - self.st.my_share, -0.15, 0.15), 0.0, 1.0)
        s_est = max(self.st.my_share, s_next)
        return self.st.demand * max(0.01, s_est) * per * safety

    def split_cards(self, cur_D: float) -> tuple[dict[str, int], dict[str, int]]:
        own = self.st.own_usable()
        rented = self.st.rented_usable()
        t = self.st.t
        q11_start = self.st.ticks - int(self.st.rules.get("GM_WINDOW", 2190))
        in_q11 = t >= q11_start - 24

        fl = self.st.flagship()
        if not fl:
            pool = {m: own.get(m, 0) + rented.get(m, 0) for m in GPUS if own.get(m, 0) + rented.get(m, 0) > 0}
            return {}, self._best_train_subset(pool, cur_D)

        need = self.serve_need(safety=1.06 if in_q11 else 1.14)
        serve: dict[str, int] = {}
        got = 0.0

        if in_q11:
            zero_rent_own = [m for m in reversed(GPUS) if own.get(m, 0) > 0 and rented.get(m, 0) == 0]
            fallback_own = [m for m in reversed(GPUS) if own.get(m, 0) > 0 and rented.get(m, 0) > 0]
            for m in zero_rent_own + fallback_own:
                if got >= need:
                    break
                pw = self.st.gpu[m]["power"]
                k = min(own[m], int(math.ceil((need - got) / pw)))
                if k > 0:
                    serve[m] = k
                    got += k * pw
            if got < need:
                for m in reversed(GPUS):
                    if got >= need or rented.get(m, 0) <= 0:
                        continue
                    pw = self.st.gpu[m]["power"]
                    k = min(rented[m], int(math.ceil((need - got) / pw)))
                    if k > 0:
                        serve[m] = serve.get(m, 0) + k
                        got += k * pw
            avail_train = {}
            for m in GPUS:
                rem = own.get(m, 0) + rented.get(m, 0) - serve.get(m, 0)
                if rem > 0:
                    avail_train[m] = rem
            return serve, self._best_train_subset(avail_train, cur_D)

        tot_avail = {m: own.get(m, 0) + rented.get(m, 0) for m in GPUS}
        for m in GPUS:
            if got >= need or tot_avail[m] <= 0:
                continue
            pw = self.st.gpu[m]["power"]
            k = min(tot_avail[m], int(math.ceil((need - got) / pw)))
            if k > 0:
                serve[m] = k
                got += k * pw
        avail_train = {m: tot_avail[m] - serve.get(m, 0) for m in GPUS if tot_avail[m] - serve.get(m, 0) > 0}
        return serve, self._best_train_subset(avail_train, cur_D)

    def _best_train_subset(self, avail: dict[str, int], D: float) -> dict[str, int]:
        best_map: dict[str, int] = {}
        best_score = 0.0
        cur: dict[str, int] = {}
        for m in reversed(GPUS):
            cnt = avail.get(m, 0)
            if cnt <= 0:
                continue
            trial_full = dict(cur)
            trial_full[m] = cnt
            sc_full = self.eff_cluster_power(trial_full, D)
            if sc_full > best_score * 1.003:
                cur = trial_full
                best_map = dict(cur)
                best_score = sc_full
            else:
                improved = False
                for frac in (0.15, 0.35, 0.65):
                    k = max(1, int(cnt * frac))
                    tr = dict(cur)
                    tr[m] = k
                    sc = self.eff_cluster_power(tr, D)
                    if sc > best_score * 1.003:
                        best_map = tr
                        best_score = sc
                        improved = True
                if improved:
                    cur = dict(best_map)
        return best_map

    # ---- Training lifecycle: Gen 1 (WSD+algo1) -> Gen 2 (WSD+algo2) -> Continuous Mega Run (algo3) ----
    def step_training(self, act: dict) -> None:
        t = self.st.t
        if not self.st.synth_bootstrapped:
            self.maybe_bootstrap_synth(act)

        if self.tuck_pending is not None:
            rn = self.tuck_pending
            r = self.st.runs_obs.get(rn)
            if r is not None and r[1] == "paused":
                cur_d = float(r[2])
                cur_tgt = float(r[3])
                ctl = act.setdefault("run_ctl", [])
                if cur_d < 0.979 * cur_tgt:
                    ctl.append({"name": rn, "op": "set_tokens", "arg": 500000.0})
                ctl.append({"name": rn, "op": "resume"})
                self.tuck_pending = None
                return
            elif r is None or r[1] in ("done", "stopped"):
                self.tuck_pending = None

        if (
            self.mega_run is None
            and self.phase == 2
            and "algo_eff_3" in self.st.tech
            and ("stable" in self.st.tech or t >= 3400)
            and t < self.st.ticks - 3000
        ):
            self._create_mega_run(act)

        if self.mega_run is not None and not self.mega_ready:
            mr = self.st.runs_obs.get(self.mega_run)
            if mr is not None and mr[1] == "paused":
                self.mega_ready = True

        if self.main_run is None:
            if self.phase == 1:
                if "algo_eff_1" in self.st.tech or t >= 450:
                    self._start_early_gen(act, gen_idx=1)
            elif self.phase == 2:
                if self.mega_ready and self.mega_run is not None:
                    self._activate_mega(act)
                elif self.mega_run is None and t < 2600:
                    self._start_early_gen(act, gen_idx=2)
            return

        r = self.st.runs_obs.get(self.main_run)
        if r is None:
            return
        status = r[1]

        # Fast-path early exit on quiet ticks (90%+ of ticks!)
        if (
            status == "running"
            and not self.st.fleet_changed
            and t - self.last_split_t < 24
            and t % 24 != 12
            and (
                (self.phase == 3 and t < self.final_release_t and (
                    self.milestone_idx >= len(self.mega_milestones) or t < self.mega_milestones[self.milestone_idx]
                ))
                or (self.phase in (1, 2) and t < self.main_end + 100 and (self.phase == 2 or self.gen1_synth_mixed or "curriculum" not in self.st.tech))
            )
        ):
            return

        cur_d = float(r[2])
        cur_tgt = float(r[3])

        if status == "diverged" and int(r[6]) == 0:
            act.setdefault("run_ctl", []).append({"name": self.main_run, "op": "stop"})
            self.st.drop_ledger(self.main_run)
            self.main_run = None
            return

        if status == "done":
            if self.main_run != self.mega_run:
                self._release_model(act, self.main_run, self.main_N, is_final=False)
                self.st.drop_ledger(self.main_run)
                self.main_run = None
                if self.phase == 1:
                    self.phase = 2
                elif self.mega_ready and self.mega_run is not None:
                    self._activate_mega(act)
            elif self.mega_sched == "wsd" and not self.tuck_final_done:
                self.tuck_final_done = True
                self._release_model(act, self.mega_run, self.mega_N, is_final=True)
            return

        if self.st.fleet_changed or t - self.last_split_t >= 24:
            serve, train = self.split_cards(cur_d)
            self.last_split_t = t
            ncards = sum(train.values())
            if status in ("running", "review") and ncards > 0:
                ctl = act.setdefault("run_ctl", [])
                if train != self.last_train_gpus:
                    ctl.append({"name": self.main_run, "op": "set_gpus", "arg": train})
                    self.last_train_gpus = dict(train)
                if ncards != self.last_train_ncards:
                    ctl.append({"name": self.main_run, "op": "set_batch", "arg": max(4, (ncards + 1) // 2)})
                    ctl.append({"name": self.main_run, "op": "set_ckpt", "arg": self.ckpt_opt(ncards)[0]})
                    self.last_train_ncards = ncards
            self._set_serve(act, serve)
        else:
            train = self.last_train_gpus

        # ---- Phase 1 (Gen 1 WSD) & Phase 2 (Gen 2 WSD): GUARANTEED 100% WSD ANNEALING + ZERO MIX PENALTY ----
        if self.phase in (1, 2):
            if (
                self.phase == 1
                and status == "running"
                and not self.gen1_synth_mixed
                and "curriculum" in self.st.tech
                and self.st.U.get("synth", 0.0) >= 500.0
            ):
                self.gen1_synth_mixed = True
                self._refresh_run_mix(act, self.main_run, max(120.0, cur_tgt - cur_d), is_mega=False)

            if t % 24 == 12 and status == "running":
                if self.phase == 2 and not self.mega_ready and t > self.main_end - 450:
                    self.main_end = max(self.main_end, t + 500)
                rem = max(24, self.main_end - t)
                eff_c = self.eff_cluster_power(train, cur_d)
                kc = float(self.st.rules.get("KC", 300.0))
                more_safe = (eff_c * 0.88) * rem / (self.main_N * kc)
                proj_safe = cur_d + more_safe

                if cur_d < 0.848 * cur_tgt:
                    # NEVER lock WSD annealing early due to a temporary compute dip!
                    lock_win = (rem <= 320) if self.phase == 1 else (rem <= 680 and (self.mega_ready or t >= 4150))
                    if lock_win and proj_safe > 1.0 and (cur_d / proj_safe >= 0.820 or rem <= (180 if self.phase == 1 else 360)):
                        ft = round(max(cur_d + 2.0, cur_d / 0.850), 3)
                        if abs(ft - cur_tgt) / max(1.0, cur_tgt) > 0.01:
                            act.setdefault("run_ctl", []).append({
                                "name": self.main_run, "op": "set_tokens", "arg": min(9e5, ft)
                            })
                        self.wsd_locked.add(self.main_run)
                    else:
                        stgt = round(max(cur_d / 0.65, proj_safe * 1.15), 3)
                        if stgt > cur_d + 1.0 and abs(stgt - cur_tgt) / max(1.0, cur_tgt) > 0.04:
                            act.setdefault("run_ctl", []).append({
                                "name": self.main_run, "op": "set_tokens", "arg": min(9e5, stgt)
                            })
                if "curriculum" in self.st.tech and (self.st.mix_dirty or t - self.last_mix_update_t >= 168):
                    self._refresh_run_mix(act, self.main_run, max(80.0, cur_tgt - cur_d), is_mega=False)

            hard_cap = self.main_end + (420 if cur_d >= 0.84 * cur_tgt else 160)
            if self.phase == 1 and t >= hard_cap and (self.st.synth_bootstrapped or t >= 1750):
                self._finalize_and_release_early_gen(act, self.main_run, r)
                self.st.drop_ledger(self.main_run)
                self.phase = 2
                self.main_run = None
            elif self.phase == 2 and self.mega_ready and t >= hard_cap:
                self._finalize_and_release_early_gen(act, self.main_run, r)
                self.st.drop_ledger(self.main_run)
                self._activate_mega(act)
            return

        # ---- Phase 3: Continuous Mega Run (cosine in competitive, WSD in SOLO_FRONTIER!) ----
        if self.phase == 3 and status == "running":
            if t % 24 == 12 and (self.st.mix_dirty or t - self.last_mix_update_t >= 168):
                eff_c = self.eff_cluster_power(train, cur_d)
                kc = float(self.st.rules.get("KC", 300.0))
                chunk_d = max(180.0, eff_c * 1000.0 / (self.mega_N * kc))
                self.ensure_distill_pool(act, min(15000.0, 0.11 * (cur_d + chunk_d)), allow_opp=False)
                self._refresh_run_mix(act, self.mega_run, chunk_d, is_mega=True)

            if self.mega_sched == "wsd":
                if t % 24 == 12 and cur_d < 0.848 * cur_tgt and t >= 20500:
                    rem = max(24, self.final_release_t - 40 - t)
                    eff_c = self.eff_cluster_power(train, cur_d)
                    kc = float(self.st.rules.get("KC", 300.0))
                    more_safe = (eff_c * 0.90) * rem / (self.mega_N * kc)
                    proj_safe = cur_d + more_safe
                    if proj_safe > 1.0 and (cur_d / proj_safe >= 0.838 or t >= 23300):
                        ft = round(max(cur_d + 10.0, cur_d / 0.850), 3)
                        act.setdefault("run_ctl", []).append({
                            "name": self.mega_run, "op": "set_tokens", "arg": min(9e5, ft)
                        })
                        self.wsd_locked.add(self.mega_run)
                if not self.tuck_final_done and t >= self.final_release_t and cur_d >= 10.0:
                    self.tuck_final_done = True
                    act.setdefault("run_ctl", []).append({"name": self.mega_run, "op": "ckpt"})
                    act.setdefault("run_ctl", []).append({"name": self.mega_run, "op": "stop"})
                    self._release_model(act, self.mega_run, self.mega_N, is_final=True)
                    return
                if (
                    self.milestone_idx < len(self.mega_milestones)
                    and t >= self.mega_milestones[self.milestone_idx]
                    and t - self.last_release_t >= 1200
                    and (self.last_release_D > 0 or cur_d >= 420.0)
                ):
                    while self.milestone_idx < len(self.mega_milestones) and t >= self.mega_milestones[self.milestone_idx]:
                        self.milestone_idx += 1
                    act.setdefault("run_ctl", []).append({"name": self.mega_run, "op": "ckpt"})
                    self._release_model(act, self.mega_run, self.mega_N, is_final=False)
                    self.last_release_D = cur_d
                return

            # Competitive Cosine Mega Run: 2-tick tuck-and-release for +1.91% bonus on EVERY release!
            if not self.tuck_final_done and t >= self.final_release_t and cur_d >= 10.0:
                self.tuck_final_done = True
                tgt_fin = round(cur_d + 0.05, 3)
                ctl = act.setdefault("run_ctl", [])
                if cur_d < 0.979 * cur_tgt or tgt_fin <= cur_tgt * 1.04:
                    ctl.append({"name": self.mega_run, "op": "set_tokens", "arg": tgt_fin})
                ctl.append({"name": self.mega_run, "op": "ckpt"})
                ctl.append({"name": self.mega_run, "op": "stop"})
                self._release_model(act, self.mega_run, self.mega_N, is_final=True)
                return

            should_tuck = False
            if self.milestone_idx < len(self.mega_milestones) and t >= self.mega_milestones[self.milestone_idx]:
                min_d_first = max(250.0, (22000.0 if self.st.best_own_pub() >= 0.56 else 14000.0) / max(10.0, self.mega_N))
                if t - self.last_release_t >= 1200 and (self.last_release_D > 0 or cur_d >= min_d_first):
                    should_tuck = True
                    while self.milestone_idx < len(self.mega_milestones) and t >= self.mega_milestones[self.milestone_idx]:
                        self.milestone_idx += 1
                elif self.last_release_D == 0 and cur_d < min_d_first and t >= self.mega_milestones[self.milestone_idx] + 600:
                    self.milestone_idx += 1
            elif (
                t < self.final_release_t - 700
                and t - self.last_release_t >= 2000
                and cur_d >= max(22000.0 / max(10.0, self.mega_N), self.last_release_D * 1.50)
                and self.st.intel.opp_flag_pub > self.st.best_own_pub() + 0.032
            ):
                should_tuck = True

            if should_tuck and cur_d >= 50.0 and cur_d < 0.97 * cur_tgt:
                self._execute_cosine_tuck(act, self.mega_run, cur_d)

    def _start_early_gen(self, act: dict, gen_idx: int) -> None:
        serve, train = self.split_cards(0.0)
        ncards = sum(train.values())
        if ncards <= 0:
            return
        self._set_serve(act, serve)
        t = self.st.t
        end = 1480 if gen_idx == 1 else max(t + 2900, 4480)
        dur = max(300, end - t)
        cur_eff = self.eff_cluster_power(train, 30.0)
        proj_eff = max(cur_eff, 1800.0 if gen_idx == 1 else 5200.0)
        budget = proj_eff * dur * 0.95
        # Calibrated unclamped scaling-law optimum: matches each world's (alpha, beta, A, B)!
        n_lo, n_hi = (3.5, 18.0) if gen_idx == 1 else (6.5, 36.0)
        N = self.choose_N(
            budget, self.st.algo_mult(), n_lo, n_hi,
            use_big_synth=(gen_idx == 2 or self.st.U.get("synth", 0.0) >= 500.0),
            serve_pen=0.0 if gen_idx == 1 else 0.015,
        )
        kc = float(self.st.rules.get("KC", 300.0))
        D = round(max(120.0, budget / (kc * N) * 1.25), 2)

        tm = self.st.best_teacher(allow_opp=(gen_idx == 2))
        can_boost = tm is not None and (
            (gen_idx == 1 and float(tm["pub"]) >= 0.44)
            or (gen_idx == 2 and float(tm["pub"]) >= 0.72)
        )
        dist_cap = 0.185 if can_boost else 0.094
        ud = self.ensure_distill_pool(act, dist_cap * D * 1.15, allow_opp=(gen_idx == 2))
        us = self.st.U.get("synth", 0.0) >= 200.0

        self.run_seq += 1
        name = f"tac_g{self.run_seq}"
        beta = self.fit["beta"] if self.fit else 0.30
        mix = self.rnd.compute_optimal_mix(None, D, ud, dist_cap, us, beta=beta, harvest_contam=(gen_idx == 2))
        lr_mult = 0.97 if "stable" in self.st.tech else 0.96
        sp = {
            "name": name, "params_b": N, "tokens_b": D, "gpus": train,
            "lr": self.eta_star(N) * lr_mult,
            "batch": max(4, (ncards + 1) // 2),
            "sched": "wsd", "mix": mix,
            "ckpt_every": self.ckpt_opt(ncards)[0],
        }
        act.setdefault("run", []).append(sp)
        self.st.init_ledger(name, mix)
        self.main_run = name
        self.main_N = N
        self.main_start = t
        self.main_end = end
        self.last_train_ncards = ncards
        self.last_train_gpus = dict(train)
        self.last_mix_update_t = t

    def _create_mega_run(self, act: dict) -> None:
        is_solo = not self.st.intel.is_active_opp
        # Calibrated serving-aware scaling-law optimum: adapts cleanly across all (alpha, beta, A, B)!
        est_budget = 3.6e8 if is_solo else 1.8e8
        n_lo = 10.0 if not is_solo else 14.0
        n_hi = 86.0 if is_solo else 68.0
        N = self.choose_N(
            est_budget, 1.50, n_lo, n_hi,
            use_big_synth=True,
            serve_pen=0.014 if is_solo else 0.048,
        )
        self.mega_sched = "wsd" if is_solo else "cosine"
        self.run_seq += 1
        name = f"tac_mega{self.run_seq}"
        beta = self.fit["beta"] if self.fit else 0.30
        ud = self.ensure_distill_pool(act, 300.0, allow_opp=False)
        us = self.st.U.get("synth", 0.0) >= 200.0
        mix = self.rnd.compute_optimal_mix(None, 800.0, ud, 0.095, us, beta=beta, harvest_contam=True)
        lr_mult = 0.975 if "stable" in self.st.tech else 0.96
        sp = {
            "name": name, "params_b": N, "tokens_b": 500000.0,
            "gpus": {"G1": 1}, "lr": self.eta_star(N) * lr_mult,
            "batch": 128, "sched": self.mega_sched, "mix": mix,
            "ckpt_every": 12, "paused": True,
        }
        act.setdefault("run", []).append(sp)
        act.setdefault("audit_request", []).append({"run": name})
        self.audit_sent.add(name)
        self.st.init_ledger(name, mix)
        self.mega_run = name
        self.mega_N = N

    def _activate_mega(self, act: dict) -> None:
        serve, train = self.split_cards(0.0)
        ncards = max(1, sum(train.values()))
        self._set_serve(act, serve)
        ctl = act.setdefault("run_ctl", [])
        if train:
            ctl.append({"name": self.mega_run, "op": "set_gpus", "arg": train})
        ctl.append({"name": self.mega_run, "op": "set_batch", "arg": max(4, (ncards + 1) // 2)})
        ctl.append({"name": self.mega_run, "op": "set_ckpt", "arg": self.ckpt_opt(ncards)[0]})
        ctl.append({"name": self.mega_run, "op": "resume"})
        self.main_run = self.mega_run
        self.main_N = self.mega_N
        self.main_start = self.st.t
        self.phase = 3
        self.last_train_ncards = ncards
        self.last_train_gpus = dict(train)
        self.last_release_D = 0.0
        self.st.mix_dirty = True

    def _refresh_run_mix(self, act: dict, name: str, chunk_d: float, is_mega: bool) -> None:
        beta = self.fit["beta"] if self.fit else 0.30
        dist_cap = 0.095 if is_mega else 0.094
        ud = self.st.U.get("distill", 0.0) + self.st.distill_pending >= 2.0
        us = self.st.U.get("synth", 0.0) >= 200.0
        mix = self.rnd.compute_optimal_mix(name, chunk_d, ud, dist_cap, us, beta=beta, harvest_contam=True)
        act.setdefault("run_ctl", []).append({"name": name, "op": "set_mix", "arg": mix})
        self.st.update_ledger_mix(name, mix)
        self.st.mix_dirty = False
        self.last_mix_update_t = self.st.t

    def _finalize_and_release_early_gen(self, act: dict, name: str, r: list) -> None:
        cur_d = float(r[2])
        ctl = act.setdefault("run_ctl", [])
        if cur_d >= 5.0 and r[1] == "running":
            ctl.append({"name": name, "op": "ckpt"})
        ctl.append({"name": name, "op": "stop"})
        if cur_d >= 5.0:
            self._release_model(act, name, self.main_N, is_final=False)

    def _execute_cosine_tuck(self, act: dict, name: str, cur_d: float) -> None:
        v = round(max(cur_d + 0.5, cur_d / 0.976), 3)
        ctl = act.setdefault("run_ctl", [])
        ctl.append({"name": name, "op": "set_tokens", "arg": v})
        ctl.append({"name": name, "op": "ckpt"})
        ctl.append({"name": name, "op": "pause"})
        self._release_model(act, name, self.mega_N, is_final=False)
        self.tuck_pending = name
        self.last_release_t = self.st.t
        self.last_release_D = cur_d

    def _release_model(self, act: dict, name: str, n_b: float, is_final: bool) -> None:
        fl = self.st.flagship()
        if fl is not None:
            pr = round(_clip(float(fl["price"]), 3.5, 99.9), 3)
        else:
            pr = 6.2
        act.setdefault("release", []).append({"run": name, "price": pr})
        self.released_runs.add(name)
        self.last_release_t = self.st.t

    # ---- Monday operations: proactive sites, talent/poaching, obsolete sale ----
    def monday_ops(self, act: dict) -> None:
        if not self.st.wk:
            return
        self._emergency_raise_only(act)
        self._manage_sites(act)
        self.rnd.weekly_talent_and_poaching(act)
        self._maybe_sell_obsolete(act)

    def _emergency_raise_only(self, act: dict) -> None:
        # Strict bankruptcy-only guard with guaranteed positive valuation (zero illegal raises!)
        exec_q = (self.st.t + 1) // 2190
        if self.st.cash >= 4.0 or self.st.t < 400 or exec_q in self.st.raised_q:
            return
        t = max(0, self.st.t)
        hw = sum(b[1] * b[2] * max(0.0, 1.0 - (t - b[3]) / 26280.0) for b in self.st.hw_batches)
        if self.st.cash + hw < 25.0:
            return
        v_pre = self.st.est_v_pre()
        if v_pre >= 60.0:
            amt = min(50.0, 0.06 * v_pre)
            if amt >= 15.0:
                act.setdefault("raise", []).append({"amount": round(amt, 2)})
                self.st.raised_q.add(exec_q)

    def _manage_sites(self, act: dict) -> None:
        t = self.st.t
        if t > self.st.ticks - 2800:
            return
        kw_com = self.st.kw_committed()
        mw_cap = self.st.mw_capacity()

        s0 = self.st.sites[0]
        r0 = self.st.regions[int(s0["region"])]
        cur_mw0 = float(s0["mw"]) + self.st.local_pend_mw.get(0, 0.0)
        rem0 = float(r0["cap_mw"]) - cur_mw0
        if rem0 > 0.25 and (len(self.st.deliv) > 0 or kw_com > 0.45 * cur_mw0 * 1000.0 or (t >= 4200 and self.st.cash > 75.0)):
            if self.st.cash > 44.0:
                add = min(rem0, 10.0, max(0.0, (self.st.cash - 36.0) / 2.0))
                if add >= 1.5 or add >= rem0 - 0.05:
                    act.setdefault("expand_site", []).append({"site": 0, "mw": round(add, 2)})
                    self.st.local_pend_mw[0] = self.st.local_pend_mw.get(0, 0.0) + add
                    return

        if self.st.site_count >= 7 or t - self.st.last_site_build_t < 4 * 168:
            return
        if kw_com > 0.48 * mw_cap * 1000.0 or (t >= 10000 and mw_cap < 18.0 and self.st.cash > 140.0):
            used = {int(s["region"]) for s in self.st.sites}
            reg_w = 0.50 if self.mega_run is None else 0.0
            ranked = sorted(
                self.st.regions,
                key=lambda r: -(r["cap_mw"] ** 0.90) / ((r["build_cost"] + 10.0) * (1.0 + reg_w * r["reg"]) * (0.45 + r["price"] * 750.0)),
            )
            best = next((r for r in ranked[:3] if r["id"] not in used), ranked[0])
            if self.st.cash > best["build_cost"] + 34.0:
                act.setdefault("build_site", []).append({"region": int(best["id"])})
                self.st.last_site_build_t = t

    # ---- SUNDAY (t%168==167) Auction-First GPU & Data Bidding (0 ticks before clearing!) ----
    def sunday_auctions(self, act: dict) -> None:
        t = self.st.t
        sal_buf = sum(s["sal"] for s in self.st.staff) * 168.0 * 1.15
        rent_buf = sum(
            self.st.rent_prices.get(l["m"], 0.0) * l["n"] * (0.4 if l["kind"] == "spot" else 1.0) * min(72, max(0, l["end"] - t))
            for l in self.st.leases.values()
        )
        # Keep lean cash floor since auctions are uniform-price (only pay clear <= pr) and revenue flows hourly
        cash_keep = 16.0 + sal_buf + rent_buf * 0.45
        budget = max(0.0, self.st.cash - cash_keep)
        committed = self._sunday_bid_gpus(act, budget)
        self.rnd.sunday_data_deals(act, max(0.0, budget - committed))

    def _sunday_bid_gpus(self, act: dict, budget: float) -> float:
        t = self.st.t
        if t > self.st.ticks - 1850 or budget < 18.0 or not self.st.offer:
            return 0.0
        offer = self.st.offer
        pue = self.st.pue()
        own_c = self.st.compute_of(self.st.own_usable())
        is_opp = self.st.intel.is_active_opp
        room_kw = self.st.mw_capacity() * 1000.0 - self.st.kw_committed()
        if t < self.st.ticks - 3600 and self.st.site_count < 7:
            room_kw += 18000.0

        cands = []
        for m, o in offer.items():
            lots = int(o.get("lots", 0))
            if lots <= 0 or m == "G1":
                continue
            early = bool(o.get("early", False))
            if early and "early_supply" not in self.st.tech:
                continue
            if m == "G2" and (t >= 4380 or own_c >= 2000.0 or budget < 46.0):
                continue
            if m == "G3" and (t >= 22800 or (t >= 18500 and own_c >= 14000.0)):
                continue

            meta = self.st.gpu[m]
            res = float(o["reserve"])
            base_h = meta["price"] * self.st.heat
            gen = GPUS.index(m)
            ratchet = self.st.intel.bid_ratchet.get(m, 0.0)
            clr = self.st.intel.clear_mult.get(m, 1.35)

            if not is_opp:
                # SOLO_FRONTIER: early clears at reserve (0.80); regular NPCs cap at 1.50!
                bm = 1.05 if early else 1.52
                pr = max(res * 1.015, base_h * bm)
            elif early:
                if self.st.intel.opp_early_seen or self.st.intel.opp_last_won.get(m, 0) > 0 or t >= 10000:
                    cap_m = 2.40 if gen >= 3 else 1.95
                    floor_m = 1.64 if gen >= 3 else 1.48
                    bm = min(cap_m, max(floor_m, clr * 1.15) + ratchet)
                else:
                    bm = min(1.45, 1.12 + ratchet)
                pr = max(res * 1.015, base_h * bm)
            else:
                ec_boost = (
                    0.22
                    if (m == "G3" and self.st.intel.export_ctrl_q >= 0
                        and (t // 2190) in (self.st.intel.export_ctrl_q - 1, self.st.intel.export_ctrl_q))
                    else 0.0
                )
                if gen == 4:  # G5: 85%-92% returns as hw + massive 24x Q11 GM!
                    bm = min(2.60, max(1.78, clr * 1.16) + ratchet)
                elif gen == 3:  # G4: main snowball engine!
                    bm = min(2.40, max(1.68, clr * 1.15) + ratchet)
                elif gen == 2:  # G3
                    bm = min(2.05, max(1.56, clr * 1.12) + ratchet + ec_boost)
                else:  # G2
                    bm = min(1.42, max(1.20, clr * 1.04))
                pr = max(res * 1.015, base_h * bm)

            # Strongly prefer newer generations (G5 > G4 > G3 > G2) and early supply
            prio = (meta["power"] / max(1e-6, meta["price"])) * (1.0 + 0.75 * gen) * (1.45 if early else 1.0)
            cands.append((prio, m, pr, res, base_h, lots, meta["kw"], early, clr))

        if not cands:
            return 0.0
        cands.sort(reverse=True)

        bids = []
        committed = 0.0
        for _, m, pr, res, base_h, lots, kw_c, early, clr in cands:
            if budget < res * 1.015 * 256.0:
                continue
            kw_l = 256.0 * kw_c * pue
            max_kw_k = int(max(0.0, room_kw) // kw_l)
            if max_kw_k <= 0:
                continue
            lc = pr * 256.0
            k = min(lots, int(budget // lc), max_kw_k, 64)
            if k <= 0:
                # NEVER sit out when we can afford at least reserve for 1 lot!
                k = 1
                pr = min(pr, (budget - 0.5) / 256.0)
                if pr < res * 1.005:
                    continue
                lc = pr * 256.0
            elif k < min(lots, max_kw_k):
                # If stepping up to k+1 lots still clears comfortably above market, take more lots!
                k_try = min(lots, max_kw_k, k + 1)
                pr_try = (budget - 0.5) / (k_try * 256.0)
                min_ok = res * 1.02 if (early and not is_opp) else (
                    base_h * 1.51 if not is_opp else max(res * 1.02, base_h * max(1.25, clr * 1.04))
                )
                if pr_try >= min_ok:
                    k = k_try
                    pr = min(pr, pr_try)
                    lc = pr * 256.0

            bids.append({"model": m, "lots": k, "price": round(pr, 5)})
            self.st.intel.last_bid_models.add(m)
            # Reserve expected clearing cost (conservatively 0.85 * bid for multi-model budget cascade)
            est_pay = k * max(res, min(pr, base_h * max(1.05 if early else 1.35, clr * 1.02))) * 256.0
            budget = max(0.0, budget - max(est_pay, lc * k * 0.88))
            committed += est_pay
            room_kw -= k * kw_l
        if bids:
            act["bid_gpu"] = bids
        return committed

    def _maybe_sell_obsolete(self, act: dict) -> None:
        inv_high = sum(int(self.st.gpu_obs.get(m, [0, 0, 0, 0, 0])[2]) for m in ("G3", "G4", "G5"))
        if inv_high <= 0:
            return
        g1 = self.st.gpu_obs.get("G1")
        if g1 and g1[0] > 32:
            sell_n = min(g1[0] - 4, 256)
            if sell_n > 0:
                act.setdefault("sell_gpu", []).append({"model": "G1", "n": sell_n})

    # ---- Capital-Preserving Zero-Gap Cloud Rental Engine (Auction-First!) ----
    def manage_rentals(self, act: dict) -> None:
        t = self.st.t
        if self.st.rent_fresh_t != t or t % 168 == 167 or not self.st.rent_prices:
            return
        if self.phase == 0 or t >= self.st.ticks - 180:
            return

        q11_start = self.st.ticks - int(self.st.rules.get("GM_WINDOW", 2190))
        in_q11 = t >= q11_start - 48
        own = self.st.own_usable()
        own_c = self.st.compute_of(own)
        stable_rent_c = self.st.stable_rent_compute(horizon=26)
        s_need = self.serve_need(safety=1.14)

        if in_q11:
            serve_models = set(self.active_serve.keys()) if (not self.serve_auto and self.active_serve) else set()
            # Emergency Q11 fallback: ONLY if owned fleet is severely below serving demand AND price >> rent cost!
            fl = self.st.flagship()
            if fl and own_c + stable_rent_c < s_need * 0.78 and self.st.cash > 35.0:
                n_b = float(fl.get("N") or 40.0)
                per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
                rent_unit = min((pr / self.st.gpu[m]["power"] for m, pr in self.st.rent_prices.items()), default=1e-4)
                rent_mc = per * rent_unit * 1000.0
                if float(fl["price"]) > rent_mc * 2.1:
                    tlen = min(168, max(24, self.st.ticks - t))
                    self._rent_best_available(act, (s_need - own_c - stable_rent_c) * 1.10, (self.st.cash - 22.0) * 0.45, tlen)
                    return
            to_end = [
                lid for lid, l in self.st.leases.items()
                if l["m"] in serve_models and l["end"] > t + 2
            ]
            if to_end:
                act.setdefault("end_rent", []).extend(to_end)
            # Disjoint high-gen training rent in Q11 (0x Q11 serve_cost!) for peak final C
            if t < self.final_release_t - 48 and self.st.cash > 160.0:
                is_solo = not self.st.intel.is_active_opp
                if is_solo or self.st.cash > 280.0:
                    for dm in ("G5", "G4"):
                        if dm not in serve_models and dm in self.st.rent_prices:
                            self._try_rent_model(
                                act, dm,
                                target_add_power=32000.0 if is_solo else 16000.0,
                                max_spend=(self.st.cash - (100.0 if is_solo else 210.0)) * (0.48 if is_solo else 0.28),
                                tlen=min(168, max(24, self.final_release_t - t)),
                            )
                            break
            return

        # Priority 1 (EVERY day): ZERO-GAP serving compute guarantee -> drop == 0.000, rep == 0.000!
        serve_short = max(0.0, s_need - (own_c + stable_rent_c))
        if serve_short > 40.0 and self.st.cash > 3.0:
            tlen = min(72 if self.st.cash < 24.0 else 168, max(24, q11_start - 24 - t))
            if tlen >= 24:
                self._rent_best_available(act, serve_short * 1.22, max(0.5, (self.st.cash - 1.5) * 0.65), tlen)
            return

        # Priority 2: Training rentals
        wk_tick = t % 168
        in_anneal = (self.phase in (1, 2) and self.main_run in self.wsd_locked)
        cur_train_c = max(0.0, own_c + stable_rent_c - s_need)
        cur_wk_rent = sum(
            self.st.rent_prices.get(l["m"], 0.0) * l["n"] * (0.4 if l["kind"] == "spot" else 1.0) * 168.0
            for l in self.st.leases.values()
            if l["end"] > t + 26
        )

        if t < 4380:
            # Q0-Q1 bootstrap (before G3 auctions open): build strong Gen 1 & Gen 2 without draining cash!
            cash_floor = 32.0 if in_anneal else 40.0
            want_train_c = 3200.0 if t < 2190 else 5800.0
            max_wk_rent = 26.0 if t < 2190 else 36.0
            if self.st.cash <= cash_floor or cur_train_c >= want_train_c or cur_wk_rent >= max_wk_rent * 0.90:
                return
            tlen = min(168, q11_start - 36 - t)
            spend_cap = min((max_wk_rent - cur_wk_rent) * (tlen / 168.0), (self.st.cash - cash_floor) * 0.55)
            if spend_cap >= 1.2:
                self._rent_best_available(act, want_train_c - cur_train_c, spend_cap, tlen)
            return

        # Q2-Q10 Auction Era: ONLY rent for training ONCE per week (Monday, right AFTER Sunday auction clears!)
        if not in_anneal and wk_tick != 23:
            return

        rev_wk = max(self.st.last_wk_rev, 16.0)
        if t < 10950:
            target_gpu = "G3"
            min_train_c = 3800.0
        elif t < 19710:
            target_gpu = "G4" if own_c >= 5000.0 else "G3"
            min_train_c = 6200.0
        else:
            target_gpu = "G5" if own_c >= 12000.0 else "G4"
            min_train_c = 8500.0
        next_lot_c = self.st.gpu[target_gpu]["price"] * self.st.heat * 1.50 * 256.0

        tlen = min(168, q11_start - 36 - t)
        if tlen < 24:
            return

        # Case A: True excess cash (we already hold >1.45-1.85 frontier lots after Sunday auction!) -> deploy overflow into training!
        is_solo = not self.st.intel.is_active_opp
        ov_mult = 1.45 if is_solo else 1.80
        if self.st.cash > next_lot_c * ov_mult + 65.0:
            want_c = max(min_train_c, own_c * (0.65 if is_solo else 0.45) + (24000.0 if is_solo else 16000.0))
            if cur_train_c < want_c:
                spend_cap = min(
                    130.0 if is_solo else 95.0,
                    max(0.0, (self.st.cash - next_lot_c * (ov_mult - 0.25) - 48.0) * (0.55 if is_solo else 0.45)),
                ) * (tlen / 168.0)
                if spend_cap >= 2.0:
                    self._rent_best_available(act, want_c - cur_train_c, spend_cap, tlen)
            return

        # Case B: Building cash toward next GPU lot -> ONLY keep modest baseline training velocity (<= 22% of revenue)!
        if cur_train_c < min_train_c and self.st.cash > 44.0:
            max_wk_rent = min(20.0, rev_wk * 0.24)
            if cur_wk_rent < max_wk_rent * 0.85:
                spend_cap = min(
                    (max_wk_rent - cur_wk_rent) * (tlen / 168.0),
                    (self.st.cash - 38.0) * 0.28,
                )
                if spend_cap >= 1.2:
                    self._rent_best_available(act, min_train_c - cur_train_c, spend_cap, tlen)

    def _try_rent_model(self, act: dict, m: str, target_add_power: float, max_spend: float, tlen: int) -> bool:
        t = self.st.t
        if m not in self.st.rent_prices or self.st.rent_blocked_until.get(m, 0) > t:
            return False
        meta = self.st.gpu[m]
        if (t + 1) // 2190 < meta["launch_q"]:
            return False
        pr = self.st.rent_prices[m]
        u = (pr * 8000.0 / meta["price"] - 1.0) / 2.0
        if u >= 0.82:
            return False
        pool = meta["supply_q"] * 2.0
        safe_room = int(pool * (0.86 - u))
        if safe_room < 16:
            return False
        pw = meta["power"]
        n_need = int(math.ceil(target_add_power / pw))
        n_afford = int(max_spend // max(1e-9, pr * tlen))
        n = min(safe_room, n_need, n_afford)
        if n < 16:
            return False
        self.st.pending_rent_meta.append((m, tlen, "ondemand"))
        act.setdefault("rent", []).append({"model": m, "n": n, "kind": "ondemand", "ticks": tlen})
        return True

    def _rent_best_available(self, act: dict, target_add_power: float, max_spend: float, tlen: int) -> None:
        t = self.st.t
        best = None
        for m, pr in self.st.rent_prices.items():
            if self.st.rent_blocked_until.get(m, 0) > t:
                continue
            meta = self.st.gpu[m]
            if (t + 1) // 2190 < meta["launch_q"]:
                continue
            u = (pr * 8000.0 / meta["price"] - 1.0) / 2.0
            if u >= 0.82:
                continue
            pool = meta["supply_q"] * 2.0
            safe_room = int(pool * (0.86 - u))
            if safe_room < 16:
                continue
            gen = GPUS.index(m)
            eff_cpp = (pr / meta["power"]) * (1.0 - 0.05 * gen)
            if best is None or eff_cpp < best[0]:
                best = (eff_cpp, m)
        if best is not None:
            self._try_rent_model(act, best[1], target_add_power, max_spend, tlen)

    # ---- Daily PPA, operational recovery & exact Sunday-down / Monday-up pricing ----
    def _daily_ppa(self, act: dict) -> None:
        t = self.st.t
        if t < 1600 or t > self.st.ticks - 4800:
            return
        pue = self.st.pue()
        yr = float(self.st.rules.get("YEAR", 8760))
        for site in self.st.sites:
            sid = int(site["id"])
            if int(site.get("ready", 0)) > t or self.st.ppa_until.get(sid, -1) > t:
                continue
            rid = int(site["region"])
            trend = self.st.regions[rid]["price"] * (1.0 + 0.12 * t / yr)
            cur = self.st.elec_prices[rid] if rid < len(self.st.elec_prices) else trend
            if cur > trend * 0.96:
                continue
            mw_used = sum(n * self.st.gpu[m]["kw"] * pue / 1000.0 for m, n in (site.get("cards") or {}).items())
            if mw_used >= 1.8:
                act.setdefault("ppa", []).append({"site": sid, "mwh": round(mw_used * 0.78 * yr, 1)})
                self.st.ppa_until[sid] = t + int(yr)

    def _set_serve(self, act: dict, serve: dict[str, int]) -> None:
        clean = {m: int(n) for m, n in serve.items() if n > 0}
        if not clean:
            if not self.serve_auto:
                act["serve"] = []
                self.active_serve = None
                self.serve_auto = True
            return
        if self.serve_auto or clean != self.active_serve:
            act["serve"] = [{"gpus": clean}]
            self.active_serve = clean
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

    def _manage_models_and_price(self, act: dict) -> None:
        t = self.st.t
        exec_day = (t + 1) // 24

        api_mods = [
            (mid, self.st.models[mid])
            for mid, meta in self.st.my_models.items()
            if mid in self.st.models and not meta["delisted"] and float(self.st.models[mid].get("pub", 0.0)) >= 0.145
        ]
        if len(api_mods) > 1:
            best_mid = max(api_mods, key=lambda kv: (float(kv[1]["pub"]), kv[0]))[0]
            for mid, m in api_mods:
                if mid == best_mid:
                    continue
                meta = self.st.my_models[mid]
                if t - meta["live_t"] >= 735:
                    act.setdefault("delist", []).append(mid)
                    meta["delisted"] = True
                elif float(m["price"]) < 98.0 and self.st.last_price_exec_day.get(mid, -1) != exec_day:
                    act.setdefault("price", []).append({"model": mid, "value": 100.0})
                    m["price"] = 100.0
                    self.st.last_price_exec_day[mid] = exec_day

        if t % 168 not in (0, 167):
            return

        fl = self.st.flagship()
        if fl is None:
            return
        mid = fl["id"]
        if self.st.last_price_exec_day.get(mid, -1) == exec_day:
            return

        n_b = float(fl.get("N") or 30.0)
        per = float(self.st.rules.get("SK", 25.0)) * n_b * self.st.infer_mult()
        own_c = self.st.compute_of(self.st.own_usable())
        rent_c = self.st.compute_of(self.st.rented_usable())
        extra_rentable = 10000.0 if self.st.cash > 24.0 else 0.0
        cap_comp_pre = (own_c + rent_c) * 0.90 + extra_rentable
        s_cap_pre = min(1.0, cap_comp_pre / max(1e-6, self.st.demand * 1.06 * per))
        est_drop = (
            1.0 - self.st.wk_srv_acc / self.st.wk_dem_acc
            if self.st.wk_dem_acc > 1e-6
            else self.st.drop
        )
        p_new = self.pricer.choose_price(fl, s_cap_pre, own_c, est_drop)
        cur_p = float(fl["price"])

        if t % 168 == 167:
            if p_new < cur_p * 0.97:
                act.setdefault("price", []).append({"model": mid, "value": p_new})
                fl["price"] = p_new
                self.st.last_price_exec_day[mid] = exec_day
        else:
            if p_new > cur_p * 1.025:
                p_safe = self.pricer._guard_up(float(fl["pub"]), p_new)
                if p_safe > cur_p * 1.015:
                    act.setdefault("price", []).append({"model": mid, "value": p_safe})
                    fl["price"] = p_safe
                    self.st.last_price_exec_day[mid] = exec_day

    def step(self, obs: dict) -> dict:
        self.st.ingest(obs, self.main_run)
        if self.st.bk:
            return {}
        act: dict = {}
        t = self.st.t

        if self.p_stage >= 3:
            self.step_training(act)
        elif self.step_probes(act):
            self.step_training(act)
        if self.st.ev:
            self._ops(act)

        wk_t = t % 168
        if wk_t == 0:
            self.monday_ops(act)
        elif wk_t == 167:
            self.sunday_auctions(act)
        if t % 24 == 23:
            self.manage_rentals(act)
        if t % 24 == 0:
            self.rnd.daily_research(act)
            self._daily_ppa(act)
            if self.main_run is None or self.main_run not in self.st.runs_obs or self.st.runs_obs[self.main_run][1] in ("done", "stopped"):
                if not self.serve_auto:
                    act["serve"] = []
                    self.active_serve = None
                    self.serve_auto = True
        if t % 24 == 0 or wk_t == 167:
            self._manage_models_and_price(act)
        return act


def make_bot(init: dict) -> AdaptiveTactician:
    return AdaptiveTactician(init)


if __name__ == "__main__":
    raw_init = sys.stdin.readline()
    if raw_init:
        bot = make_bot(json.loads(raw_init))
        step = bot.step
        write = sys.stdout.write
        flush = sys.stdout.flush
        loads = json.loads
        dumps = json.dumps
        write("{}\n")
        flush()
        for line in sys.stdin:
            if not line.strip():
                continue
            msg = loads(line)
            if msg.get("type") == "end":
                write("{}\n")
                flush()
                break
            act = step(msg)
            write(dumps(act, separators=(",", ":")) + "\n" if act else "{}\n")
            flush()
