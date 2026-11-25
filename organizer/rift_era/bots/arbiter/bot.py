"""裂陆纪元 世界池套利与城邦商贸对手（Arbiter — Trade Spread, City-State Commerce & Liquidity Compounder）。

独立实现的私有评分对手（不共享、不导入其他 bot 代码，仅用 Python 标准库）。
核心战略画像：
  轻资产高周转 + 金融套利与城邦商贸网络（强攻 U_ind NFA_RU/NetTransit + U_intl 出口/城邦结盟/援助 + 高流动性民生）。
架构模块：
  1. SpreadMarginCalculator：实时读取 r9 世界池买卖点差（11.5x / 8.5x）与城邦 10.0x 现货报价，
     按每周期净 RU 现金收益率对产线排序，集中扩建高毛利零排放现金牛产线（农/食/轻工/弹药/高纯化工/制药）；
  2. CityStateCommerceBroker：开局 BFS 寻找经由本国道路直达的最近中立城邦，使用初始物流车队（P036）与陆运
     向城邦运送民生商品并在城邦现货市场按 10.0x 挂卖（比世界池 8.5x 高 +17.6%，同步刷出口分、NetTransit 与
     城邦亲和度），亲和度达标（>=0.35）即运料签署 resource_jv / port_lease 协议锁定持续国际支柱分；
  3. LeanWarehouseAndJITController：充分利用 r9 制造优先入仓（A2）规则，保持精简仓容，在仓容 > 62% 或原矿超
     3 周期时果断暂停矿井，采用 2.2 周期精益库存周转将一切非必需盈余高效变现为 RU 储备（NFA_RU 复利滚雪球）；
  4. FinanceAndLiquidityEngine：冲刺金融科技链（F01->I02->R01->C01->R04->F02->C02->F03->A04->L01->F05...），
     双向 FX 流动管理、5% 超低税率、早开 F05 社保与 F01/F02/F03 金融制度落地；
  5. LeanPowerAndDefense：精简电力调度与防御托管安全网。
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction

NAME = 'arbiter'
VERSION = 'rift-arbiter-1'

BASKET = {
    'food': Fraction(6, 100),
    'water': Fraction(4, 100),
    'medicine': Fraction(1, 100),
    'medical_supplies': Fraction(25, 10000),
    'consumer_goods': Fraction(3, 100),
}

LAB_RECIPES = ('P075', 'P076', 'P077', 'P079', 'P102')

# Finance, commerce & high-margin export tech spine
FINANCE_TECH_SPINE = (
    'F01', 'I02', 'R01', 'C01', 'R04', 'F02', 'C02', 'F03', 'A04', 'L01',
    'F05', 'R02', 'E02', 'I01', 'L02', 'F04', 'C03', 'V01', 'V02', 'I03',
    'E03', 'E04', 'R03', 'R05', 'A02', 'A03', 'F06', 'C05', 'C06', 'L04',
)

# High RU-cash-yield recipes under r9 8.5x sell / 10x city spot
CASH_COW_RECIPES = frozenset({
    'P028', 'P031', 'P032', 'P044', 'P002', 'P005', 'P008', 'P033', 'P003', 'P012', 'P105',
})

EMISSION_PENALTY_RU = {
    'P070': 25, 'P001': 35, 'P005': 28, 'P002': 20, 'P006': 40, 'P007': 22, 'P066': 25, 'P067': 25,
}

S1_COST = {'steel': 6000, 'cement': 6000, 'basic_parts': 2000}
S2_COST = {'steel': 16000, 'cement': 16000, 'basic_parts': 8000}
HOUSE_COST = {'steel': 5000, 'cement': 20000}
WH_COST = {'steel': 4000, 'cement': 6000}

LEAN_RESERVE = {'steel': 6000, 'cement': 4000, 'basic_parts': 2000}
LEAN_FLOORS = {'steel': 18000, 'cement': 22000, 'basic_parts': 10000}

POOL_CAP = 40000
LIFE_SAFETY = {
    'coal': 50000, 'water': 50000, 'grain': 16000, 'food': 34000,
    'consumer_goods': 16000, 'medicine': 18000, 'medical_supplies': 2500,
    'lab_supplies': 3000, 'ammunition': 4000, 'fuel': 8000,
}
POOL_BUY_PRIORITY = ('medical_supplies', 'medicine', 'food', 'consumer_goods', 'water', 'coal', 'grain', 'fuel')
NEVER_LIQUIDATE = frozenset({
    'electricity', 'compute', 'research_points', 'housing_capacity',
    'medicine', 'medical_supplies', 'vehicle', 'engineering_pack', 'precision_pack', 'system_pack',
})

# City-state commerce goods (sold at 10x w_cat RU vs world-pool 8.5x!)
CITY_TRADE_GOODS = ('food', 'consumer_goods', 'medicine')
JV_MATERIALS = (('steel', 1000), ('cement', 1000), ('basic_parts', 1000))


def to_q(value) -> Fraction:
    if isinstance(value, dict) and '$rational' in value:
        return Fraction(*value['$rational'])
    return Fraction(value)


class Bot:
    """World-Pool Arbitrageur & City-State Commerce Broker."""

    def __init__(self, init: dict):
        self.owner = init['owner']
        self.capital = f'{self.owner}-h0'
        catalog = init.get('catalog', {})
        industry = catalog.get('industry', {})
        tech = catalog.get('technology', {})
        self.recipes = {r['id']: r for r in industry.get('recipes', ())}
        self.nodes = {t['id']: t for t in tech.get('technologies', ())}
        self.tiers = {t['tier']: t for t in tech.get('research_tiers', ())}
        self.spine = [t for t in FINANCE_TECH_SPINE if t in self.nodes]

        init_pool = (init.get('initial_observation') or {}).get('world_pool', {})
        self.w_cat = {g: Fraction(p, 10000) for g, p in init_pool.get('price_ru_mU', {}).items()}
        self.world_map = init.get('map', {})
        self.hubs_meta = self.world_map.get('hubs', {})
        self.routes_meta = self.world_map.get('routes', {})
        self.terrain_by_region = self.world_map.get('terrain_by_region', {})
        cap_reg = self.hubs_meta.get(self.capital, {}).get('region', '')
        self.cap_can_farm = self.terrain_by_region.get(cap_reg, 'plain') in ('plain', 'hill')
        self.coastal_hubs = set(self.world_map.get('coastal_hubs') or ())

        # BFS to find nearest neutral city-states reachable exclusively via own hubs -> city road
        self.city_routes = self._find_reachable_city_states()

        self.seq = 1
        self.tag_of = {}
        self.last_try = {}
        self.fail_cnt = {}
        self.done = set()
        self.ordered_recipe = {}
        self.fresh_recipe = {}
        self.build_costs = {}
        self.util_ema = {}
        self.last_trace = {}
        self.plant_win = -99
        self.pool_cycle = -1
        self.tax_bp = None
        self.welfare_on = False
        self.cb_issuing = False
        self.mobilized = False
        self.mobilized_tick = -99
        self.threat = False
        self.jv_submitted = set()
        self.truck_fails = 0
        self.cap_truck_deployed = False
        self.depot_truck_sent = set()
        self.depot_armed = set()

    # ------------------------------------------------------------------ map BFS to neutral city-states
    def _find_reachable_city_states(self) -> list[dict]:
        adj = {}
        for rid, r in self.routes_meta.items():
            if r.get('mode') == 'road' and r.get('initial_operational', True):
                a, b = r.get('a'), r.get('b')
                if a and b:
                    adj.setdefault(a, []).append((b, rid, r.get('length', 10)))
                    adj.setdefault(b, []).append((a, rid, r.get('length', 10)))
        for k in adj:
            adj[k].sort()

        own_hubs = {hid for hid, h in self.hubs_meta.items() if h.get('owner') == self.owner}
        # Only traverse through OWN hubs, stopping as soon as we enter a city-* hub (toll-free city_commerce!)
        dist = {self.capital: (0, 0, [])}
        q = deque([self.capital])
        found = []
        while q:
            u = q.popleft()
            hops, cells, path = dist[u]
            if hops >= 4:
                continue
            for v, rid, length in adj.get(u, ()):
                if v in dist:
                    continue
                v_meta = self.hubs_meta.get(v, {})
                v_owner = v_meta.get('owner') or ''
                new_path = path + [rid]
                if v_owner.startswith('city-'):
                    dist[v] = (hops + 1, cells + length, new_path)
                    found.append({
                        'city_id': v_owner,
                        'hub_id': v,
                        'hops': hops + 1,
                        'cells': cells + length,
                        'path': new_path,
                        'mid_hub': u if hops == 1 else None,
                        'coastal': v in self.coastal_hubs,
                    })
                elif v in own_hubs:
                    dist[v] = (hops + 1, cells + length, new_path)
                    q.append(v)
        found.sort(key=lambda x: (x['hops'], x['cells'], x['city_id']))
        return found[:4]

    # ------------------------------------------------------------------ utilities
    def _cmd(self, kind: str, op: str, tag=None, **fields) -> dict:
        row = {'id': str(self.seq), 'kind': kind, 'op': op}
        row.update(fields)
        if tag is not None:
            self.tag_of[row['id']] = tag
        self.seq += 1
        return row

    def _ready(self, tag, w: int, wait: int = 6, max_fail: int = 4) -> bool:
        return self.fail_cnt.get(tag, 0) < max_fail and self.last_try.get(tag, -999) <= w - wait

    def _read_receipts(self, obs: dict) -> None:
        for rc in obs.get('command_receipts', ()):
            tag = self.tag_of.pop(str(rc.get('id')), None)
            if tag is None:
                continue
            if rc.get('status') == 'REJECTED':
                self.fail_cnt[tag] = self.fail_cnt.get(tag, 0) + 1
                self.last_try.pop(tag, None)
                if isinstance(tag, tuple) and tag[0] == 'truck_city':
                    self.truck_fails += 1

    def _scan_state(self, obs: dict):
        avail = {}
        physical = {}
        hub_load = {}
        for row in obs.get('inventory', ()):
            if len(row) != 4:
                continue
            h, b, g, q = row
            physical[g] = physical.get(g, 0) + q
            if b == 'available':
                avail[(h, g)] = avail.get((h, g), 0) + q
            if b != 'outdoor':
                hub_load[h] = hub_load.get(h, 0) + q
        ru = 0
        treasury = 0
        for row in obs.get('cash_accounts', ()):
            if len(row) >= 3:
                if row[0] == 'reserves' and row[1] == 'RU':
                    ru += row[2]
                elif row[0] == 'treasury' and row[1] == self.owner:
                    treasury += row[2]
        hh_cash = sum(h.get('household_cash_mU', 0) for h in obs.get('hubs', ()) if h.get('id') == self.capital)
        return avail, physical, hub_load, ru, treasury, hh_cash

    # ------------------------------------------------------------------ 1. SpreadMarginCalculator
    def _recipe_cash_yield_ru(self, rid: str, sell_prices: dict) -> Fraction:
        """Net RU cash yield per batch when selling outputs at world-pool 8.5x minus input opportunity cost."""
        rec = self.recipes.get(rid)
        if not rec:
            return Fraction(-999)
        out_ru = sum(
            to_q(q) * Fraction(sell_prices.get(g, int(self.w_cat.get(g, 0) * 8500)), 1000)
            for g, q in rec['outputs'].items()
        )
        # Domestic raw inputs cost ~7.5x w_cat opportunity cost; power costs ~6 RU/unit; emissions penalized
        in_ru = sum(
            to_q(q) * self.w_cat.get(g, Fraction(0)) * Fraction(8, 1)
            for g, q in rec['inputs'].items()
        )
        pwr_cost = to_q(rec.get('power') or 0) * Fraction(6, 1)
        em_pen = Fraction(EMISSION_PENALTY_RU.get(rid, 0))
        return out_ru - in_ru - pwr_cost - em_pen

    def _update_util(self, obs: dict) -> None:
        decay = Fraction(3, 4)
        for line in obs.get('lines', ()):
            if self.recipes.get(line.get('recipe')) is None:
                continue
            lid = line['id']
            snap = (
                tuple(sorted((line.get('input_credit_nmU') or {}).items())),
                tuple(sorted((line.get('output_accum_nmU') or {}).items())),
            )
            moved = lid in self.last_trace and snap != self.last_trace[lid]
            running = (line.get('power_received_mU', 0) > 0 or moved) and not line.get('paused') and not line.get('under_construction')
            self.last_trace[lid] = snap
            old = self.util_ema.get(lid, Fraction(1))
            self.util_ema[lid] = old * decay + (1 - decay) * (1 if running else 0)

    def _demand_and_eta(self, obs: dict, extra: dict):
        need = {}
        nat = {}
        eta = {}

        def add(h, g, q, to_eta=True):
            need[(h, g)] = need.get((h, g), 0) + q
            nat[g] = nat.get(g, 0) + q
            if to_eta:
                eta[g] = eta.get(g, 0) + q

        for h in obs.get('hubs', ()):
            pop = h.get('population_mU', 0)
            if pop > 0:
                for g, rate in BASKET.items():
                    add(h['id'], g, pop * rate)
        for line in obs.get('lines', ()):
            rid = line.get('recipe')
            rec = self.recipes.get(rid)
            if rec is None or line.get('under_construction'):
                continue
            if line.get('plant'):
                if line['plant'].get('online'):
                    oq = sum(to_q(q) for q in rec['outputs'].values()) or Fraction(1)
                    gen = line['plant'].get('generation_mU', 0) * 10
                    for g, q in rec['inputs'].items():
                        v = gen * to_q(q) / oq
                        nat[g] = nat.get(g, 0) + v
                        eta[g] = eta.get(g, 0) + v
                continue
            if rid in LAB_RECIPES:
                for g, q in rec['inputs'].items():
                    add(line['hub_id'], g, to_q(q) * 1000)
                continue
            u = self.util_ema.get(line['id'], Fraction(1))
            lf = Fraction(line.get('load_mU', 1000), 1000)
            pull = max(Fraction(1, 5), u)
            for g, q in rec['inputs'].items():
                need[(line['hub_id'], g)] = need.get((line['hub_id'], g), 0) + to_q(q) * 1000 * pull
                nat[g] = nat.get(g, 0) + to_q(q) * 1000 * pull
                eta[g] = eta.get(g, 0) + to_q(q) * 1000 * u * lf
        for (h, g), q in extra.items():
            add(h, g, q)
        return need, nat, eta

    # ------------------------------------------------------------------ 2. Research (Finance & Commerce)
    def _open_tech(self, tid: str, known: set) -> bool:
        node = self.nodes.get(tid)
        if not node or tid in known:
            return False
        req_a = set(node.get('requires_all') or ())
        req_b = set(node.get('requires_any') or ())
        return req_a <= known and (not req_b or bool(req_b & known))

    def _research(self, obs: dict, avail: dict, out: list, extra: dict, floors: dict):
        res = obs.get('research', {})
        known = set(res.get('known', ()))
        projects = res.get('projects', {})
        focus = res.get('focus', {})
        active_val = [v for v in res.get('validation', ()) if v.get('active')]
        val_techs = {v['tech_id'] for v in active_val}
        busy = {v['lab_id'] for v in active_val}

        labs = [
            l for l in obs.get('lines', ())
            if l.get('recipe') in LAB_RECIPES and not l.get('under_construction') and not l.get('work_locked')
        ]
        lab_ids = {l['id'] for l in labs}
        claimed = {}
        for lid, t in focus.items():
            if lid in lab_ids and lid not in busy:
                claimed[t] = claimed.get(t, 0) + 1

        awaiting = sorted(
            t for t, p in projects.items()
            if p.get('theory_complete') and not p.get('verified') and t not in val_techs and t not in known
        )
        for t in awaiting + sorted(val_techs):
            spec = self.tiers.get(self.nodes[t]['tier'])
            if spec:
                vg = spec['validation_good']
                va = spec['validation_amount'] * 1000
                extra[(self.capital, vg)] = extra.get((self.capital, vg), 0) + va // 2
                floors[(self.capital, vg)] = max(floors.get((self.capital, vg), 0), va + 3000)

        free = [l for l in labs if l['id'] not in busy]
        for line in free:
            if not awaiting:
                break
            tid = awaiting[0]
            spec = self.tiers[self.nodes[tid]['tier']]
            if avail.get((line['hub_id'], spec['validation_good']), 0) >= spec['validation_amount'] * 1000:
                out.append(self._cmd('research', 'validate_project', line_id=line['id'], tech_id=tid))
                awaiting.pop(0)
                busy.add(line['id'])
                prev = focus.get(line['id'])
                if prev in claimed:
                    claimed[prev] -= 1

        per_tech = max(2, min(4, len(labs)))
        for line in free:
            lid = line['id']
            if lid in busy:
                continue
            cur = focus.get(lid)
            proj = projects.get(cur, {}) if cur else {}
            if cur and cur not in known and not proj.get('theory_complete'):
                continue
            pick = None
            for tid in self.spine:
                if claimed.get(tid, 0) < per_tech and self._open_tech(tid, known) and not projects.get(tid, {}).get('theory_complete'):
                    pick = tid
                    break
            if pick is None:
                opts = sorted(
                    (self.nodes[t]['tier'], t) for t in self.nodes
                    if claimed.get(t, 0) < per_tech and self._open_tech(t, known) and not projects.get(t, {}).get('theory_complete')
                )
                pick = opts[0][1] if opts else None
            if pick and pick != cur:
                claimed[pick] = claimed.get(pick, 0) + 1
                out.append(self._cmd('research', 'set_focus', line_id=lid, tech_id=pick, sec_mode='open'))

        need_t2 = any(self.nodes[t]['tier'] == 2 for t in (set(focus.values()) | set(awaiting)) if t in self.nodes)
        return known, need_t2

    # ------------------------------------------------------------------ 3. Lean Cash-Cow Capital Planner
    def _build_lean(self, obs: dict, avail: dict, hub_load: dict, known: set, need_t2: bool,
                    floors: dict, out: list) -> None:
        hub = self.capital
        w = obs['window']
        facs = [f for f in obs.get('facilities', ()) if f['hub_id'] == hub]
        fac_kind = {f['id']: f['facility_type'] for f in facs}
        jobs = {j['id']: j for j in obs.get('work', {}).get('jobs', ())}

        for jid, job in jobs.items():
            if jid in self.ordered_recipe:
                self.fresh_recipe[job.get('target_id')] = self.ordered_recipe.pop(jid)

        default_rec = {
            '实验室': 'P075', '农场': 'P028', '食品厂': 'P031', '轻工厂': 'P032',
            '装备厂': 'P044', '化工厂': 'P008', '制药厂': 'P033', '材料厂': 'P062',
            '机械厂': 'P063',
        }
        for line in obs.get('lines', ()):
            if line['hub_id'] != hub or line.get('recipe') is not None:
                continue
            if line.get('under_construction') or line.get('work_locked'):
                continue
            fid = line['id'].rsplit('/line', 1)[0]
            rid = self.fresh_recipe.get(fid) or default_rec.get(fac_kind.get(fid))
            if rid and rid in self.recipes and set(self.recipes[rid].get('requires_all') or ()) <= known:
                tag = ('setl', line['id'])
                if self._ready(tag, w, wait=4, max_fail=3):
                    self.last_try[tag] = w
                    out.append(self._cmd('industry', 'set_line', tag=tag, line_id=line['id'], recipe=rid, mode='general'))

        committed = {}
        for cid in list(self.build_costs):
            job = jobs.get(cid)
            if job is None:
                if w - self.build_costs[cid][1] > 4:
                    del self.build_costs[cid]
                    continue
                rem = Fraction(1)
            else:
                rem = 1 - Fraction(job['elapsed_ticks'], max(1, job['duration_ticks']))
            for g, q in self.build_costs[cid][0].items():
                committed[g] = committed.get(g, 0) + q * rem

        have = {g: avail.get((hub, g), 0) for g in ('steel', 'cement', 'basic_parts')}
        free = {g: have[g] - committed.get(g, 0) - LEAN_RESERVE[g] for g in have}

        def set_floor(cost):
            for g in LEAN_RESERVE:
                floors[(hub, g)] = max(
                    floors.get((hub, g), 0),
                    int(LEAN_RESERVE[g] + committed.get(g, 0) + cost.get(g, 0)),
                )

        def launch(tag, cost, op, recipe=None, **fields):
            cmd = self._cmd('industry', op, tag=tag, **fields)
            self.build_costs[cmd['id']] = (dict(cost), w)
            self.last_try[tag] = w
            if recipe:
                self.ordered_recipe[cmd['id']] = recipe
            for g, q in cost.items():
                committed[g] = committed.get(g, 0) + q
                free[g] -= q
            out.append(cmd)

        hubrow = next((h for h in obs.get('hubs', ()) if h['id'] == hub), {})
        pop = hubrow.get('population_mU', 0)
        housing = hubrow.get('housing_mU', 0)
        engineers = hubrow.get('skills_mU', {}).get('engineer', 0)
        houses = sum(1 for f in facs if f['facility_type'] == 'housing' and f.get('commissioned'))
        occupied = sum(1 for f in facs if f['facility_type'] != 'housing')
        slots = 20 * (10 + houses) // 10 - occupied

        # Lean warehouse policy: only expand 1-2 modules when > 76% full (zero wasted capital on idle storage!)
        room = hubrow.get('warehouse_mU', 0)
        load = hub_load.get(hub, 0)
        if room > 0 and load * 100 > room * 76 and not any(j.get('kind') == 'warehouse_expand' for j in jobs.values()):
            mods = 1 if load * 100 < room * 86 else 2
            wc = {g: q * mods for g, q in WH_COST.items()}
            if self._ready('wh_lean', w, wait=6) and all(have[g] - committed.get(g, 0) >= q for g, q in wc.items()):
                launch('wh_lean', wc, 'warehouse_expand', hub_id=hub, modules=mods)
                set_floor({})
                return
            if load * 100 > room * 84:
                set_floor(wc)
                return

        rcount = {}
        for l in obs.get('lines', ()):
            if l['hub_id'] == hub:
                r = l.get('recipe') or self.fresh_recipe.get(l['id'].rsplit('/line', 1)[0])
                if r:
                    rcount[r] = rcount.get(r, 0) + 1
        for f in facs:
            if not f.get('commissioned'):
                r = self.fresh_recipe.get(f['id']) or default_rec.get(f['facility_type'])
                if r:
                    rcount[r] = rcount.get(r, 0) + 1

        # Build lean cash-cow export & finance portfolio (NO heavy extra smelting or bloated footprint)
        plan = []
        head = Fraction(52, 50)
        if pop * BASKET['food'] * head > rcount.get('P031', 0) * 3000:
            plan.append(('food', S1_COST, {'facility_type': '食品厂', 'profile_id': 'S1'}, 'P031'))
        if self.cap_can_farm and rcount.get('P031', 0) * 3000 * 10 > rcount.get('P028', 0) * 4000 * 9:
            plan.append(('farm_bal', S1_COST, {'facility_type': '农场', 'profile_id': 'S1'}, 'P028'))
        if pop * BASKET['consumer_goods'] * head > rcount.get('P032', 0) * 3000:
            plan.append(('cg', S1_COST, {'facility_type': '轻工厂', 'profile_id': 'S1'}, 'P032'))

        # High-margin export cash-cows: zero-emission grain (P028), light consumer goods (P032), food (P031), ammo (P044)
        if self.cap_can_farm and rcount.get('P028', 0) < 5:
            plan.append(('farm_cash', S1_COST, {'facility_type': '农场', 'profile_id': 'S1'}, 'P028'))
        if rcount.get('P032', 0) < (5 if not self.cap_can_farm else 4):
            plan.append(('cg_cash', S1_COST, {'facility_type': '轻工厂', 'profile_id': 'S1'}, 'P032'))

        # Compact 5-lab finance research engine
        lab_cnt = sum(1 for f in facs if f['facility_type'] == '实验室')
        if lab_cnt < min(5, max(2, engineers // 1800)):
            if lab_cnt >= 4 * rcount.get('P062', 0):
                plan.append(('labsup', S1_COST, {'facility_type': '材料厂', 'profile_id': 'S1'}, 'P062'))
            else:
                plan.append(('lab', S1_COST, {'facility_type': '实验室', 'profile_id': 'S1'}, 'P075'))

        if rcount.get('P031', 0) < 5:
            plan.append(('food_exp', S1_COST, {'facility_type': '食品厂', 'profile_id': 'S1'}, 'P031'))
        if rcount.get('P044', 0) < 1:
            plan.append(('ammo_cash', S1_COST, {'facility_type': '装备厂', 'profile_id': 'S1'}, 'P044'))
        if need_t2 and rcount.get('P063', 0) < 1:
            plan.append(('pack', S1_COST, {'facility_type': '机械厂', 'profile_id': 'S1'}, 'P063'))

        if {'I02', 'L01'} <= known and rcount.get('P008', 0) < 2:
            plan.append(('pchem', S2_COST, {'facility_type': '化工厂', 'profile_id': 'S2'}, 'P008'))
        if {'A04', 'L01'} <= known and rcount.get('P033', 0) < 2:
            plan.append(('pharma', S2_COST, {'facility_type': '制药厂', 'profile_id': 'S2'}, 'P033'))
        if {'V02', 'L01'} <= known and rcount.get('P105', 0) < 1:
            plan.append(('remed', S2_COST, {'facility_type': '化工厂', 'profile_id': 'S2'}, 'P105'))

        building_house = any(f['facility_type'] == 'housing' and not f.get('commissioned') for f in facs)
        need_house = (slots <= 1 or pop > housing * 85 // 100) and not building_house

        if not any(j.get('kind') == 'build_facility' and j.get('hub_id') == hub for j in jobs.values()):
            for tag, cost, fields, recipe in plan:
                if not self._ready(tag, w, wait=6, max_fail=3):
                    continue
                if slots <= 0:
                    need_house = True
                    break
                if all(free[g] >= q for g, q in cost.items()):
                    launch(tag, cost, 'build_facility', recipe=recipe, hub_id=hub, **fields)
                    set_floor({})
                    return
                set_floor(cost)
                return

        if need_house and not building_house and self._ready('house', w, wait=6, max_fail=3):
            if all(free[g] >= q for g, q in HOUSE_COST.items()):
                launch('house', HOUSE_COST, 'build_facility', hub_id=hub, facility_type='public', profile_id='P110')
                set_floor({})
                return
            set_floor(HOUSE_COST)
        else:
            set_floor({})

    # ------------------------------------------------------------------ 4. CityStateCommerceBroker
    def _broker_city_states(self, obs: dict, avail: dict, ru: int, out: list) -> None:
        """Arbitrage 10.0x city-state spot markets vs 8.5x world pool, earn U_intl exports & treaties!"""
        w = obs['window']
        if not self.city_routes or w < 4:
            return
        cities_obs = {c['id']: c for c in (obs.get('city_states') or {}).get('cities', ())}
        prices = (obs.get('markets') or {}).get('prices', {})
        existing_asks = set()
        for o in (obs.get('markets') or {}).get('orders', ()):
            if isinstance(o, dict):
                side = o.get('side')
                hub_or_venue = o.get('hub_id') or o.get('venue', '')
                good_o = o.get('good')
            else:
                side = getattr(o, 'side', None)
                hub_or_venue = getattr(o, 'venue', '')
                good_o = None
            if side == 'sell':
                if good_o:
                    existing_asks.add((hub_or_venue, good_o))
                elif isinstance(hub_or_venue, str) and hub_or_venue.startswith('spot:'):
                    parts = hub_or_venue.split(':')
                    if len(parts) == 3:
                        existing_asks.add((parts[1], parts[2]))
                        existing_asks.add((hub_or_venue, parts[2]))

        cap_fuel = avail.get((self.capital, 'fuel'), 0)
        cap_vehicles = avail.get((self.capital, 'vehicle'), 0)
        shipments_sent = 0

        # Track whether any border relay hub has received its stationed truck
        for route in self.city_routes[:3]:
            mid = route.get('mid_hub')
            if mid and avail.get((mid, 'vehicle'), 0) >= 1000:
                self.depot_armed.add(mid)

        for route in self.city_routes[:3]:
            cid = route['city_id']
            chub = route['hub_id']
            path = route['path']
            hops = route['hops']
            mid = route.get('mid_hub')
            if hops > 2:
                continue
            cinfo = cities_obs.get(cid)
            if not cinfo:
                continue
            aff = cinfo.get('affinity_mU', {})
            j_score = aff.get('j', 0)
            partner = cinfo.get('partner')
            pending_prop = cinfo.get('proposal_pending', False)

            # Step A: Sell any arrived trade goods on the city-state's 10x RU spot market!
            for g in CITY_TRADE_GOODS:
                at_city = avail.get((chub, g), 0)
                if at_city >= 1000:
                    venue = f'spot:{chub}:{g}'
                    vprice = prices.get(venue) or int(self.w_cat.get(g, Fraction(4)) * 10000)
                    ask_tag = ('cask', chub, g)
                    if (chub, g) not in existing_asks and (venue, g) not in existing_asks and self._ready(ask_tag, w, wait=4, max_fail=8):
                        qty = (at_city // 1000) * 1000
                        out.append(self._cmd(
                            'logistics', 'market_ask', tag=ask_tag,
                            hub_id=chub, good=g, qty_mU=qty,
                            limit_price_mU=int(vprice), expire_cycles=6,
                        ))
                        avail[(chub, g)] = max(0, avail.get((chub, g), 0) - qty)
                        existing_asks.add((chub, g))
                        self.last_try[ask_tag] = w

            # Step 0 (for 2-hop city-states): Arm border relay hub `mid` with 1 spare truck + fuel!
            route_ready = (hops == 1)
            if hops == 2 and mid:
                leg0 = [path[0]]
                mid_armed = mid in self.depot_armed
                mid_fuel = avail.get((mid, 'fuel'), 0)
                if not mid_armed:
                    if (mid not in self.depot_truck_sent and len(self.depot_truck_sent) < 1
                            and cap_vehicles >= 2000 and cap_fuel >= 4000 and shipments_sent < 2
                            and self._ready(('arm_veh', mid), w, wait=8, max_fail=2)):
                        out.append(self._cmd(
                            'logistics', 'route_shipment', tag=('arm_veh', mid),
                            origin_hub=self.capital, dest_hub=mid, corridor_path=leg0,
                            good='vehicle', qty_mU=1000, carrier_recipe='P036', repeat_cycles=0,
                        ))
                        self.depot_truck_sent.add(mid)
                        self.cap_truck_deployed = True
                        cap_vehicles -= 1000
                        cap_fuel -= 1500
                        avail[(self.capital, 'vehicle')] = max(0, avail.get((self.capital, 'vehicle'), 0) - 1000)
                        avail[(self.capital, 'fuel')] = max(0, avail.get((self.capital, 'fuel'), 0) - 1500)
                        self.last_try[('arm_veh', mid)] = w
                        shipments_sent += 1
                else:
                    if mid_fuel < 3000 and cap_fuel >= 7000 and shipments_sent < 2 and self._ready(('arm_fuel', mid), w, wait=6, max_fail=4):
                        out.append(self._cmd(
                            'logistics', 'route_shipment', tag=('arm_fuel', mid),
                            origin_hub=self.capital, dest_hub=mid, corridor_path=leg0,
                            good='fuel', qty_mU=4000, carrier_recipe='P036', repeat_cycles=0,
                        ))
                        self.cap_truck_deployed = True
                        cap_fuel -= 5500
                        avail[(self.capital, 'fuel')] = max(0, avail.get((self.capital, 'fuel'), 0) - 5500)
                        self.last_try[('arm_fuel', mid)] = w
                        shipments_sent += 1
                    if mid_fuel >= 1500:
                        route_ready = True

            # Step B: If affinity >= 350 and no partner yet, stage JV materials & sign resource_jv / port_lease!
            if j_score >= 350 and partner != self.owner and not pending_prop and ru >= 600000:
                if route['coastal'] and j_score >= 500:
                    ttag = ('treaty', cid, 'port_lease')
                    if self._ready(ttag, w, wait=10, max_fail=3):
                        out.append(self._cmd(
                            'diplomacy', 'city_state_treaty', tag=ttag,
                            city_state_id=cid, treaty_kind='port_lease', aid_ru_mU=0,
                        ))
                        self.last_try[ttag] = w
                else:
                    have_all_jv = all(avail.get((chub, g), 0) >= q for g, q in JV_MATERIALS)
                    if have_all_jv:
                        ttag = ('treaty', cid, 'resource_jv')
                        if self._ready(ttag, w, wait=10, max_fail=3):
                            out.append(self._cmd(
                                'diplomacy', 'city_state_treaty', tag=ttag,
                                city_state_id=cid, treaty_kind='resource_jv', aid_ru_mU=0,
                            ))
                            self.last_try[ttag] = w
                    elif route_ready and shipments_sent < 2:
                        can_truck = (cap_vehicles >= 1000 or self.cap_truck_deployed) and cap_fuel >= 3500 and self.truck_fails < 4
                        jv_carrier = 'P036' if (hops == 2 or can_truck) else 'manual'
                        if jv_carrier != 'P036' or can_truck:
                            for g, need_q in JV_MATERIALS:
                                short = need_q - avail.get((chub, g), 0)
                                if short > 0 and avail.get((self.capital, g), 0) > LEAN_FLOORS.get(g, 8000) + short:
                                    stag = ('jvship', cid, g)
                                    if self._ready(stag, w, wait=6, max_fail=3):
                                        out.append(self._cmd(
                                            'logistics', 'route_shipment', tag=stag,
                                            origin_hub=self.capital, dest_hub=chub, corridor_path=list(path),
                                            good=g, qty_mU=short, carrier_recipe=jv_carrier, repeat_cycles=0,
                                        ))
                                        avail[(self.capital, g)] = max(0, avail.get((self.capital, g), 0) - short)
                                        if jv_carrier == 'P036':
                                            self.cap_truck_deployed = True
                                            cap_fuel -= 1500
                                            avail[(self.capital, 'fuel')] = max(0, avail.get((self.capital, 'fuel'), 0) - 1500)
                                        self.last_try[stag] = w
                                        shipments_sent += 1
                                        break

            # Step C: Dispatch regular commercial convoys (food & consumer_goods) to city-state!
            if not route_ready or shipments_sent >= 2:
                continue
            cpop = cinfo.get('population_mU', 15000)
            for g, rate, cap_min in (
                ('consumer_goods', Fraction(3, 100), 22000),
                ('food', Fraction(6, 100), 48000),
            ):
                if shipments_sent >= 2:
                    break
                at_city = avail.get((chub, g), 0)
                cap_avail = avail.get((self.capital, g), 0)
                target_city = max(3000, int(cpop * rate * 5))
                if at_city >= target_city or cap_avail < cap_min:
                    continue
                use_truck = (cap_vehicles >= 1000 or self.cap_truck_deployed) and cap_fuel >= 4000 and self.truck_fails < 4
                if hops == 2 and not use_truck:
                    continue
                carrier = 'P036' if use_truck else 'manual'
                batch = min(8000 if use_truck else 3000, ((cap_avail - cap_min) // 1000) * 1000, target_city - at_city)
                batch = (batch // 1000) * 1000
                if batch < 1000:
                    continue
                stag = ('truck_city' if use_truck else 'man_city', cid, g)
                if self._ready(stag, w, wait=8 if use_truck else 6, max_fail=6):
                    out.append(self._cmd(
                        'logistics', 'route_shipment', tag=stag,
                        origin_hub=self.capital, dest_hub=chub, corridor_path=list(path),
                        good=g, qty_mU=batch, carrier_recipe=carrier, repeat_cycles=0,
                    ))
                    avail[(self.capital, g)] = max(0, avail.get((self.capital, g), 0) - batch)
                    if use_truck:
                        self.cap_truck_deployed = True
                        cap_fuel -= 1500
                        avail[(self.capital, 'fuel')] = max(0, avail.get((self.capital, 'fuel'), 0) - 1500)
                    self.last_try[stag] = w
                    shipments_sent += 1

    # ------------------------------------------------------------------ 5. JIT Lines, Mines, Power & World Pool
    def _manage_lines_and_mines(self, obs: dict, avail: dict, hub_load: dict, need: dict,
                                nat_use: dict, eta_denom: dict, floors: dict, known: set,
                                sell_prices: dict, out: list) -> None:
        w = obs['window']
        hub = self.capital
        spec_active = sum(1 for l in obs.get('lines', ()) if l.get('mode') == 'specialized')
        spec_cap = 4 if 'R04' in known else 0

        for line in obs.get('lines', ()):
            rid = line.get('recipe')
            rec = self.recipes.get(rid)
            if rec is None or line.get('under_construction') or line.get('work_locked'):
                continue
            if not set(rec.get('requires_all') or ()) <= known:
                continue
            if rec['facility'] in ('电站', '实验室'):
                continue
            lh = line['hub_id']
            target = 0
            for g in rec['outputs']:
                buf = 4 if g in ('food', 'consumer_goods', 'medicine', 'water', 'grain') else 3
                target = max(target, int(need.get((lh, g), 0) * buf), floors.get((lh, g), 0))
                if lh == hub and g in LEAN_FLOORS:
                    target = max(target, LEAN_FLOORS[g])
            # Cash-cow recipes with positive RU cash yield run at full JIT export throughput!
            if lh == hub and (rid in CASH_COW_RECIPES or self._recipe_cash_yield_ru(rid, sell_prices) > 10):
                # Keep stock safely high enough to feed continuous world-pool & city-state sales without stalling
                target = max(target, max(int(eta_denom.get(g, 0)) * 3 for g in rec['outputs']) + 28000)

            cur_mode = line.get('mode') or 'general'
            want_mode = cur_mode
            if (cur_mode == 'general' and rid in ('P032', 'P031', 'P044', 'P008') and spec_active < spec_cap
                    and avail.get((hub, 'basic_parts'), 0) >= 6000):
                tag = ('aspec', line['id'])
                if self._ready(tag, w, wait=12, max_fail=2):
                    want_mode = 'specialized'
                    spec_active += 1
                    self.last_try[tag] = w

            cur_t = (line.get('options') or {}).get('target_stock_mU')
            if want_mode != cur_mode or cur_t is None or abs(cur_t - target) > max(1500, target // 4):
                out.append(self._cmd(
                    'industry', 'set_line',
                    tag=('aspec', line['id']) if want_mode != cur_mode else None,
                    line_id=line['id'], recipe=rid, mode=want_mode, target_stock_mU=target,
                ))

        # Aggressive r9 A2 mine throttling: protect warehouse space strictly for high-margin exports!
        cap_row = next((h for h in obs.get('hubs', ()) if h['id'] == hub), {})
        room = cap_row.get('warehouse_mU', 0)
        crowded = room > 0 and hub_load.get(hub, 0) * 100 > room * 62
        for mine in obs.get('mines', ()):
            if mine.get('under_construction') or mine.get('work_locked'):
                continue
            g = mine.get('good')
            if g == 'water':
                continue
            online = mine.get('online', True)
            have = avail.get((mine['hub_id'], g), 0)
            use = max(2000, int(nat_use.get(g, 0)))
            hi = max(80000 if g == 'coal' else 32000, (3 if crowded else 5) * use)
            lo = max(48000 if g == 'coal' else 16000, 2 * use)
            if online and have > hi:
                out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=False))
            elif not online and have < lo and not crowded:
                out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=True))

    def _manage_world_pool_and_fx(self, obs: dict, avail: dict, physical: dict, nat_use: dict,
                                  eta_denom: dict, floors: dict, ru: int, treasury: int,
                                  hh_cash: int, out: list) -> None:
        cycle = obs.get('cycle', 0)
        if cycle == self.pool_cycle:
            return
        self.pool_cycle = cycle
        hub = self.capital

        # Active two-way FX liquidity balancing:
        # Maintain strong RU reserve when treasury is flush; repatriate RU to treasury if domestic cash gets tight!
        if cycle > 0:
            if ru < 14000000 and treasury > 36000000:
                amt = min(14000000, treasury - 26000000)
                out.append(self._cmd('finance', 'fx', side='buy_ru', currency=self.owner, amount_mU=amt))
            elif treasury < 16000000 and ru > 45000000:
                amt = min(12000000, ru - 30000000)
                out.append(self._cmd('finance', 'fx', side='sell_ru', currency=self.owner, amount_mU=amt))

        pool = obs.get('world_pool', {})
        buy_prices = pool.get('buy_price_ru_mU') or pool.get('price_ru_mU', {})
        sell_prices = pool.get('sell_price_ru_mU') or pool.get('price_ru_mU', {})
        pool_stock = pool.get('stock_mU', {})
        used = {(g, s): q for g, s, q in pool.get('used_mU', ())}

        bought = set()
        floor_goods = tuple(sorted({g for (h, g) in floors if h == hub}))
        for g in list(dict.fromkeys(POOL_BUY_PRIORITY + floor_goods)):
            if g == 'medical_supplies' and hh_cash < 12000000:
                continue
            want = floors.get((hub, g), 0)
            if g in LIFE_SAFETY:
                want = max(want, LIFE_SAFETY[g], 3 * int(nat_use.get(g, 0)))
            short = want - avail.get((hub, g), 0)
            if short <= 0 or g not in buy_prices:
                continue
            qty = min(POOL_CAP - used.get((g, 'buy'), 0), ((short + 999) // 1000) * 1000, pool_stock.get(g, 0) // 2)
            qty = (qty // 1000) * 1000
            if qty <= 0:
                continue
            cost = (qty * buy_prices[g] + 999) // 1000
            if cost <= ru:
                ru -= cost
                bought.add(g)
                out.append(self._cmd('trade', 'pool_buy', hub_id=hub, good=g, qty_mU=qty))

        # Aggressive JIT world-pool liquidation: keep tight 2.5-cycle working stock, convert all surplus to RU!
        # Prioritize highest-RU-value goods first so our pool_sell commands maximize NFA_RU compounding.
        candidates = []
        for (h, g), have_q in avail.items():
            if h != hub or g in NEVER_LIQUIDATE or g in bought or g not in sell_prices:
                continue
            min_buf = 24000 if g in ('steel', 'cement') else 12000 if g == 'basic_parts' else 10000
            # Reserve extra buffer on city-state export goods if city-states are reachable
            city_res = 12000 if (g in ('food', 'consumer_goods') and self.city_routes) else 0
            keep = max(
                min_buf + city_res,
                int(eta_denom.get(g, 0) * Fraction(5, 2)),
                LIFE_SAFETY.get(g, 0),
                floors.get((hub, g), 0),
            )
            excess = min(have_q, physical.get(g, 0) - keep, POOL_CAP - used.get((g, 'sell'), 0))
            if excess >= 1000:
                qty = (excess // 1000) * 1000
                candidates.append((qty * sell_prices[g], g, qty))

        candidates.sort(key=lambda x: (-x[0], x[1]))
        for _, g, qty in candidates:
            out.append(self._cmd('trade', 'pool_sell', hub_id=hub, good=g, qty_mU=qty))

    # ------------------------------------------------------------------ 6. Power, Governance & Defense
    def _power(self, obs: dict, out: list) -> None:
        w = obs['window']
        cycle = obs.get('cycle', w // 2)
        plants = sorted(
            (l for l in obs.get('lines', ()) if l.get('plant') and l.get('recipe') == 'P070'
             and l['hub_id'] == self.capital and not l.get('under_construction') and not l.get('work_locked')),
            key=lambda l: l['id'],
        )
        if 'p_stagger' not in self.done and plants:
            self.done.add('p_stagger')
            shifts = (16, 8, 0, -8, -16)
            for i, l in enumerate(plants):
                s = shifts[i % len(shifts)]
                if s != 0 and l['plant'].get('maintenance_shift_cycles', 0) != s:
                    out.append(self._cmd('logistics', 'plant_maintenance', facility_id=l['id'], shift_cycles=s))
        if w < 4 or len(plants) < 2:
            return

        def due(l, h):
            p = l['plant']
            nxt = p.get('next_maintenance_cycle')
            if nxt is None:
                return False
            return 0 <= (nxt + p.get('maintenance_shift_cycles', 0)) - cycle <= h

        online = [l for l in plants if l['plant'].get('online')]
        offline = [l for l in plants if not l['plant'].get('online') and l['plant'].get('mode') == 'OFF' and not due(l, 3)]
        healthy = [l for l in online if l['plant'].get('mode') in ('ON', 'STARTING') and not due(l, 1)]
        running = [l for l in online if l['plant'].get('mode') == 'ON']
        gen = sum(l['plant'].get('generation_mU', 0) for l in online)
        if (len(healthy) < 2 or gen > 1000 * len(healthy) * Fraction(4, 5)) and offline and (w - self.plant_win >= 2 or len(healthy) < len(online)):
            out.append(self._cmd('industry', 'plant_toggle', facility_id=offline[0]['id'], online=True))
            self.plant_win = w
        elif (w - self.plant_win >= 6 and len(online) > 2 and len(running) == len(online)
              and not any(due(l, 2) for l in online) and gen < (len(online) - 1) * 1000 * Fraction(68, 100)):
            out.append(self._cmd('industry', 'plant_toggle', facility_id=online[-1]['id'], online=False))
            self.plant_win = w

    def _govern_and_finance(self, obs: dict, avail: dict, known: set, ru: int, treasury: int,
                            hh_cash: int, extra: dict, out: list) -> None:
        w = obs['window']
        payouts = (obs.get('corporate_finance') or {}).get('payout_policies', {})
        for cid in sorted(payouts):
            if payouts[cid] < 1000 and ('div', cid) not in self.done:
                self.done.add(('div', cid))
                out.append(self._cmd('finance', 'dividend', tag=('div', cid), company=cid, payout_mU=1000))

        # Ultra-low 5% tax policy for maximum household consumption & support
        want_welfare = 10 ** 9 if 'F05' in known else 0
        if self.tax_bp is None:
            want_tax = 500
        elif treasury < 14000000 and hh_cash > 18000000:
            want_tax = 900
        elif treasury > 30000000 or hh_cash < 12000000:
            want_tax = 500
        else:
            want_tax = self.tax_bp
        if want_tax != self.tax_bp or (want_welfare > 0) != self.welfare_on:
            self.tax_bp = want_tax
            self.welfare_on = want_welfare > 0
            out.append(self._cmd('governance', 'fiscal_policy', tax_rate_bp=want_tax,
                                 welfare_budget_mU=want_welfare, maint_budget_mU=10 ** 12))

        if 'F01' in known and 'ipo' not in self.done:
            self.done.add('ipo')
            for sec in ('mining', 'manufacturing', 'energy'):
                out.append(self._cmd('finance', 'ipo', company=f'{self.owner}-{sec}', qty=1, min_price_mU=1000000000))

        if 'F02' in known:
            macro = obs.get('macroeconomy') or {}
            inf = macro.get('inflation_mU', 0)
            money = macro.get('money_mU', 150000000)
            issue = min(2000000, max(0, money // 100)) if (treasury < 22000000 and inf < 15) else 0
            if 'cb' not in self.done or (issue > 0) != self.cb_issuing:
                self.done.add('cb')
                self.cb_issuing = issue > 0
                out.append(self._cmd('finance', 'central_bank', rate_bp=250, issue_mU=issue,
                                     intervene_ru_mU=0, peg=None, capital_control_bp=0))

        # F03 active scale: opportunistically buy 1 share of an affordable listed foreign stock if F03 known & cash rich
        if 'F03' in known and treasury > 40000000 and self._ready('f03_trade', w, wait=20, max_fail=3):
            stocks = (obs.get('stock_market') or {}).get('companies', {})
            if isinstance(stocks, dict):
                for cname, cdata in sorted(stocks.items()):
                    if not cname.startswith(f'{self.owner}-') and cdata.get('listed') and cdata.get('price_mU', 0) > 0:
                        price = int(cdata['price_mU'])
                        if price <= 2000000:
                            out.append(self._cmd('finance', 'stock_order', tag='f03_trade', company=cname,
                                                 side='buy', qty=1, limit_price_mU=price, margin=False, expire_cycles=4))
                            self.last_try['f03_trade'] = w
                            break

        cur_aut = obs.get('governance', {}).get('autonomy_mU', {})
        for h in obs.get('hubs', ()):
            if h.get('population_mU', 0) <= 0:
                continue
            reg = h['region_id']
            sup = h.get('support_mU', 1000)
            cur = cur_aut.get(reg, 0)
            if sup < 850 and cur < 500:
                out.append(self._cmd('governance', 'autonomy', region_id=reg, autonomy_mU=500))
            elif sup >= 950 and cur > 0:
                out.append(self._cmd('governance', 'autonomy', region_id=reg, autonomy_mU=0))

        if 'V02' in known:
            cap_row = next((h for h in obs.get('hubs', ()) if h['id'] == self.capital), {})
            extra[(self.capital, 'remediation_agent')] = extra.get((self.capital, 'remediation_agent'), 0) + 1000
            if cap_row.get('pollution_mU', 0) > 2000 and avail.get((self.capital, 'remediation_agent'), 0) >= 1000:
                out.append(self._cmd('industry', 'remediate_pollution', region_id=cap_row['region_id'], amount_mU=1000))

        # Defense
        pairs = obs.get('diplomacy', {}).get('pairs', ())
        at_war = any(p.get('state') == 'war' for p in pairs)
        tracks = [t for t in obs.get('military_contacts', {}).get('tracks', ())
                  if t.get('currently_visible') and t.get('quality_mU', 0) > 150]
        self.threat = at_war or bool(obs.get('military_control', {}).get('control_changes', ())) or bool(tracks)
        if self.threat and not self.mobilized:
            out.append(self._cmd('governance', 'mobilize', enabled=True))
            self.mobilized = True
            self.mobilized_tick = obs.get('tick', 0)
        elif not self.threat and self.mobilized and obs.get('tick', 0) >= self.mobilized_tick + 40:
            out.append(self._cmd('governance', 'mobilize', enabled=False))
            self.mobilized = False

        for idx, f in enumerate(obs.get('formations', ())):
            reg = f.get('region_id')
            if not reg:
                continue
            treg = tracks[0]['region_id'] if (self.threat and tracks and idx > 0 and tracks[0].get('region_id')) else reg
            if f.get('task') != 'garrison' or (self.threat and reg != treg):
                tag = ('gar', f['id'])
                if self._ready(tag, w, wait=4, max_fail=4):
                    out.append(self._cmd('military', 'formation_task', tag=tag, formation_id=f['id'],
                                         task='garrison', target_region=treg, posture='preserve',
                                         supply_hub=self.capital, retreat_loss_mU=500,
                                         expected_version=f.get('version', 0)))
                    self.last_try[tag] = w

    # ------------------------------------------------------------------ decide
    def decide(self, obs: dict) -> list[dict]:
        out = []
        self._read_receipts(obs)
        avail, physical, hub_load, ru, treasury, hh_cash = self._scan_state(obs)
        extra, floors = {}, {}
        w = obs.get('window', 0)

        known, need_t2 = self._research(obs, avail, out, extra, floors)

        # Window 0 FX -> Window 1 instant medical_supplies so s_med = 2.0 right from cycle 0!
        if w == 0 and ru < 6000000 and treasury > 25000000:
            out.append(self._cmd('finance', 'fx', side='buy_ru', currency=self.owner, amount_mU=6000000))
        if w == 1 and hh_cash > 12000000 and ru >= 1500000 and avail.get((self.capital, 'medical_supplies'), 0) < 1000:
            out.append(self._cmd('trade', 'pool_buy', hub_id=self.capital, good='medical_supplies', qty_mU=2000))

        if w % 2 == 0:
            self._update_util(obs)
            self._govern_and_finance(obs, avail, known, ru, treasury, hh_cash, extra, out)
            self._build_lean(obs, avail, hub_load, known, need_t2, floors, out)
            self._power(obs, out)
            need, nat_use, eta_denom = self._demand_and_eta(obs, extra)
            sell_prices = (obs.get('world_pool') or {}).get('sell_price_ru_mU') or (obs.get('world_pool') or {}).get('price_ru_mU', {})
            self._manage_lines_and_mines(obs, avail, hub_load, need, nat_use, eta_denom, floors, known, sell_prices, out)
            # Broker city-state exports (10x spot + U_intl + NetTransit) BEFORE liquidating remaining surplus to 8.5x pool!
            self._broker_city_states(obs, avail, ru, out)
            self._manage_world_pool_and_fx(obs, avail, physical, nat_use, eta_denom, floors, ru, treasury, hh_cash, out)

        return out[:220]
