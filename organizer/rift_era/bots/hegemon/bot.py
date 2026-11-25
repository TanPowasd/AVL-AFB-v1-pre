"""裂陆纪元 工业霸权对手（Hegemon — Vertically-Integrated Heavy Production & Multi-Hub Dominance）。

独立实现的私有评分对手（不共享、不导入其他 bot 代码，仅用 Python 标准库）。
核心战略画像：
  垂直整合重工业产能 + 多枢纽工业化 + 实体基建资产主导
  （强攻 U_ind 垂直链条实体 VA + U_ctrl 多区 sigma_r / Kinfra / 战备 + U_pop 工业就业民生）。
架构模块：
  1. VerticalBOMBalance：按“上游冶炼严格匹配下游制造 + 施工消耗”闭环控库存，确保钢/水泥/机件/铜/化工
     在不靠世界池倾销的前提下全程保持 chi <= 6（eta = 1.0），把物理产能转化为真实设施、住宅、仓容与走廊 Kinfra；
  2. HeavyCapitalPlanner：激进扩张首都重工垂直链（钢/水泥/机件/电子/轻工/农食/弹药/特种化冶），前置住宅扩槽、
     主动大批量扩仓（高占用率赚取全额仓库 Kinfra）、R04 专用产线（specialized mode）改造与扩电保供电率；
  3. RegionalIndustrializationEngine：先运料建房建矿再迁工（零无房/零断供），将 2–3 座副枢纽建设成真实
     卫星工业城（矿场 G1->G2 + 本地农场/产线 + L2 道路走廊），多区叠加 sigma_r 与 Kinfra；
  4. PowerAndMineGrid：高安全裕度电力调度与错峰检修，确保重工业产线永不欠电丢失 powered_slots；
  5. SelectivePoolSafeguard：实体自给优先，仅购民生刚需（医用耗材/应急药），仅对高增加值下游成品防 eta 溢出泄洪；
  6. IndustrialTechProgram：工业/基建/专用产线优先路线（I01->L01->I02->R01->C01->R04->A04->E01->L02->I03...）；
  7. ReadinessAndFiscalGovernor：支持度达标后转 8% 稳健基建税率、100% 国企分红、高弹药战备与战时防御动员。
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction

NAME = 'hegemon'
VERSION = 'rift-hegemon-2'

HOUSEHOLD_RATES = {
    'food': Fraction(6, 100),
    'water': Fraction(4, 100),
    'medicine': Fraction(1, 100),
    'medical_supplies': Fraction(25, 10000),
    'consumer_goods': Fraction(3, 100),
}

LAB_RECIPES = ('P075', 'P076', 'P077', 'P079', 'P102')
POWER_CAP_MU = {
    'P070': 1000, 'P071': 1200, 'P111': 1400, 'P112': 1500,
    'P113': 800, 'P114': 700, 'P072': 900, 'P073': 2000,
}

TECH_SPINE = (
    'I01', 'L01', 'I02', 'R01', 'C01', 'R04', 'A04', 'E01', 'L02', 'R02',
    'I03', 'E02', 'F01', 'C02', 'F02', 'I04', 'C03', 'V01', 'V02', 'F05',
    'A01', 'A02', 'A03', 'L03', 'E03', 'E04', 'R03', 'R05', 'I05', 'I06',
    'C05', 'C06', 'G01', 'G02', 'G03', 'L08', 'V05',
)

# High-VA downstream manufactured goods (consumed domestically + overflow-vented so upstream smelters stay eta=1!)
DOWNSTREAM_RUN_RECIPES = frozenset({
    'P028', 'P031', 'P032', 'P011', 'P012', 'P004', 'P062', 'P008', 'P003', 'P009', 'P013', 'P033',
})

# Upstream heavy materials kept strictly eta-safe (consumed by downstream + heavy construction)
UPSTREAM_SMELT_RECIPES = frozenset({'P001', 'P002', 'P005', 'P006', 'P007', 'P044'})

SPECIALIZE_RECIPES = frozenset({'P001', 'P005', 'P011', 'P031', 'P032', 'P012', 'P008'})

COST_S1 = {'steel': 6000, 'cement': 6000, 'basic_parts': 2000}
COST_S2 = {'steel': 16000, 'cement': 16000, 'basic_parts': 8000}
COST_HOUSING = {'steel': 5000, 'cement': 20000}
COST_WAREHOUSE = {'steel': 4000, 'cement': 6000}

BUILD_RESERVE = {'steel': 8000, 'cement': 5000, 'basic_parts': 2500}
CAPITAL_STOCK_FLOORS = {
    'steel': 28000, 'cement': 34000, 'basic_parts': 14000,
    'ammunition': 8000,
}

PHYSICAL_SAFETY = {
    'coal': 70000, 'water': 70000, 'grain': 24000, 'food': 44000,
    'consumer_goods': 20000, 'medicine': 22000, 'medical_supplies': 2500,
    'lab_supplies': 4000, 'ammunition': 8000,
}

POOL_CAP = 40000
NO_VENT_GOODS = frozenset({
    'electricity', 'compute', 'research_points', 'housing_capacity',
    'medicine', 'medical_supplies', 'engineering_pack', 'precision_pack', 'system_pack', 'vehicle',
})
# Keep thick physical reserves on core building materials (only vent genuine excess above 5.5x use + high reserve)
HEAVY_RESERVE_GOODS = {
    'steel': 48000, 'cement': 56000, 'basic_parts': 22000,
    'iron_ore': 60000, 'stone': 50000, 'coal': 90000,
}
URGENT_BUY_GOODS = ('medical_supplies', 'medicine', 'food', 'water', 'consumer_goods', 'coal', 'grain')
MINERAL_PRIORITY = ('iron_ore', 'coal', 'copper_ore', 'stone', 'bauxite', 'natural_gas')


def q_val(value) -> Fraction:
    if isinstance(value, dict) and '$rational' in value:
        return Fraction(*value['$rational'])
    return Fraction(value)


class Bot:
    """Industrial Hegemon: vertically-integrated heavy production, multi-hub factories & high Kinfra."""

    def __init__(self, init: dict):
        self.owner = init['owner']
        self.capital = f'{self.owner}-h0'
        catalog = init.get('catalog', {})
        industry = catalog.get('industry', {})
        tech = catalog.get('technology', {})
        self.recipes = {r['id']: r for r in industry.get('recipes', ())}
        self.nodes = {t['id']: t for t in tech.get('technologies', ())}
        self.tiers = {t['tier']: t for t in tech.get('research_tiers', ())}
        self.spine = [t for t in TECH_SPINE if t in self.nodes]

        self.weights = {
            g: Fraction(p, 10000)
            for g, p in (init.get('initial_observation') or {}).get('world_pool', {}).get('price_ru_mU', {}).items()
        }
        world_map = init.get('map', {})
        self.hubs_meta = world_map.get('hubs', {})
        self.routes_meta = world_map.get('routes', {})
        self.terrain_by_region = world_map.get('terrain_by_region', {})
        cap_reg = self.hubs_meta.get(self.capital, {}).get('region', '')
        self.cap_can_farm = self.terrain_by_region.get(cap_reg, 'plain') in ('plain', 'hill')

        self.satellite_hubs, self.paths_to_hub = self._discover_industrial_satellites()
        self.direct_roads = self._index_direct_roads()

        self.seq = 1
        self.issued_tags = {}
        self.cooldown = {}
        self.failures = {}
        self.flags = set()
        self.build_intent = {}
        self.facility_recipe = {}
        self.active_build_costs = {}
        self.line_util = {}
        self.line_prev = {}
        self.last_power_win = -99
        self.last_pool_cycle = -1
        self.cur_tax_bp = None
        self.cur_welfare = False
        self.cur_cb_issue = False
        self.mobilized = False
        self.mobilized_tick = -99
        self.under_threat = False

    def _discover_industrial_satellites(self):
        adj = {}
        for rid, r in self.routes_meta.items():
            if r.get('mode') == 'road' and r.get('initial_operational', True):
                a, b = r.get('a'), r.get('b')
                if a and b:
                    adj.setdefault(a, []).append((b, rid))
                    adj.setdefault(b, []).append((a, rid))
        for k in adj:
            adj[k].sort()

        own_hubs = {hid for hid, h in self.hubs_meta.items() if h.get('owner') == self.owner}
        visited = {self.capital: []}
        q = deque([self.capital])
        while q:
            u = q.popleft()
            p = visited[u]
            if len(p) >= 2:
                continue
            for v, rid in adj.get(u, ()):
                if v in own_hubs and v not in visited:
                    visited[v] = p + [rid]
                    q.append(v)

        candidates = [h for h in visited if h != self.capital and h.startswith(f'{self.owner}-h')]
        one_hop = [h for h in candidates if len(visited[h]) == 1]
        if one_hop:
            candidates = one_hop

        def rank_hub(hid):
            hops = len(visited[hid])
            reg = self.hubs_meta.get(hid, {}).get('region', '')
            terr = self.terrain_by_region.get(reg, 'plain')
            terr_rank = 0 if terr in ('plain', 'hill') else 1
            return (hops, terr_rank, hid)

        candidates.sort(key=rank_hub)
        chosen = candidates[:3]
        return chosen, {h: visited[h] for h in chosen}

    def _index_direct_roads(self):
        direct = {}
        for rid, r in self.routes_meta.items():
            if r.get('mode') == 'road' and self.capital in (r.get('a'), r.get('b')):
                other = r['b'] if r['a'] == self.capital else r['a']
                if other in self.satellite_hubs:
                    direct[other] = r
        return direct

    def _cmd(self, kind: str, op: str, tag=None, **fields) -> dict:
        row = {'id': str(self.seq), 'kind': kind, 'op': op}
        row.update(fields)
        if tag is not None:
            self.issued_tags[row['id']] = tag
        self.seq += 1
        return row

    def _can_retry(self, tag, window: int, wait: int = 6, max_fail: int = 3) -> bool:
        return self.failures.get(tag, 0) < max_fail and self.cooldown.get(tag, -999) <= window - wait

    def _process_receipts(self, obs: dict) -> None:
        for rc in obs.get('command_receipts', ()):
            tag = self.issued_tags.pop(str(rc.get('id')), None)
            if tag is None:
                continue
            if rc.get('status') == 'REJECTED':
                self.failures[tag] = self.failures.get(tag, 0) + 1
                self.cooldown.pop(tag, None)

    def _inv_index(self, obs: dict):
        avail, physical, hub_load = {}, {}, {}
        for row in obs.get('inventory', ()):
            if len(row) != 4:
                continue
            h, bucket, g, qty = row
            physical[g] = physical.get(g, 0) + qty
            if bucket == 'available':
                avail[(h, g)] = avail.get((h, g), 0) + qty
            if bucket != 'outdoor':
                hub_load[h] = hub_load.get(h, 0) + qty
        return avail, physical, hub_load

    def _cash_summary(self, obs: dict):
        ru = treasury = 0
        for row in obs.get('cash_accounts', ()):
            if len(row) >= 3:
                if row[0] == 'reserves' and row[1] == 'RU':
                    ru += row[2]
                elif row[0] == 'treasury' and row[1] == self.owner:
                    treasury += row[2]
        cap_row = next((h for h in obs.get('hubs', ()) if h['id'] == self.capital), {})
        hh_cash = cap_row.get('household_cash_mU', 0)
        return ru, treasury, hh_cash, cap_row

    # ------------------------------------------------------------------ 1. VerticalBOMBalance
    def _update_utilization(self, obs: dict) -> None:
        decay = Fraction(3, 4)
        for line in obs.get('lines', ()):
            if self.recipes.get(line.get('recipe')) is None:
                continue
            lid = line['id']
            snap = (
                tuple(sorted((line.get('input_credit_nmU') or {}).items())),
                tuple(sorted((line.get('output_accum_nmU') or {}).items())),
            )
            moved = lid in self.line_prev and snap != self.line_prev[lid]
            active = (line.get('power_received_mU', 0) > 0 or moved) and not line.get('paused') and not line.get('under_construction')
            self.line_prev[lid] = snap
            prev = self.line_util.get(lid, Fraction(1))
            self.line_util[lid] = prev * decay + (1 - decay) * (1 if active else 0)

    def _compute_material_balance(self, obs: dict, extra_need: dict):
        hub_need, nat_use, eta_denom = {}, {}, {}

        def add_all(hub, good, qty, to_eta=True):
            hub_need[(hub, good)] = hub_need.get((hub, good), 0) + qty
            nat_use[good] = nat_use.get(good, 0) + qty
            if to_eta:
                eta_denom[good] = eta_denom.get(good, 0) + qty

        for h in obs.get('hubs', ()):
            pop = h.get('population_mU', 0)
            if pop > 0:
                hid = h['id']
                for g, rate in HOUSEHOLD_RATES.items():
                    add_all(hid, g, pop * rate)

        for line in obs.get('lines', ()):
            rid = line.get('recipe')
            rec = self.recipes.get(rid)
            if rec is None or line.get('under_construction'):
                continue
            hid = line['hub_id']
            plant = line.get('plant')
            if plant:
                if plant.get('online'):
                    out_total = sum(q_val(q) for q in rec['outputs'].values()) or Fraction(1)
                    gen = plant.get('generation_mU', 0) * 10
                    for g, q in rec['inputs'].items():
                        val = gen * q_val(q) / out_total
                        nat_use[g] = nat_use.get(g, 0) + val
                        eta_denom[g] = eta_denom.get(g, 0) + val
                continue
            if rid in LAB_RECIPES:
                for g, q in rec['inputs'].items():
                    add_all(hid, g, q_val(q) * 1000)
                continue
            util = self.line_util.get(line['id'], Fraction(1))
            load_f = Fraction(line.get('load_mU', 1000), 1000)
            pull_share = max(Fraction(1, 4), util)
            eta_share = util * load_f
            for g, q in rec['inputs'].items():
                hub_need[(hid, g)] = hub_need.get((hid, g), 0) + q_val(q) * 1000 * pull_share
                nat_use[g] = nat_use.get(g, 0) + q_val(q) * 1000 * pull_share
                eta_denom[g] = eta_denom.get(g, 0) + q_val(q) * 1000 * eta_share

        for (hid, g), qty in extra_need.items():
            add_all(hid, g, qty)

        return hub_need, nat_use, eta_denom

    # ------------------------------------------------------------------ 2. IndustrialTechProgram
    def _tech_open(self, tid: str, known: set) -> bool:
        node = self.nodes.get(tid)
        if not node or tid in known:
            return False
        req_all = set(node.get('requires_all') or ())
        req_any = set(node.get('requires_any') or ())
        return req_all <= known and (not req_any or bool(req_any & known))

    def _run_research(self, obs: dict, avail: dict, out: list, extra_need: dict, floors: dict):
        res = obs.get('research', {})
        known = set(res.get('known', ()))
        projects = res.get('projects', {})
        focus = res.get('focus', {})
        active_val = [v for v in res.get('validation', ()) if v.get('active')]
        validating = {v['tech_id'] for v in active_val}
        busy_labs = {v['lab_id'] for v in active_val}

        labs = [
            l for l in obs.get('lines', ())
            if l.get('recipe') in LAB_RECIPES and not l.get('under_construction') and not l.get('work_locked')
        ]
        lab_ids = {l['id'] for l in labs}
        assigned = {}
        for lid, tid in focus.items():
            if lid in lab_ids and lid not in busy_labs:
                assigned[tid] = assigned.get(tid, 0) + 1

        ready_to_val = sorted(
            t for t, p in projects.items()
            if p.get('theory_complete') and not p.get('verified') and t not in validating and t not in known
        )
        for tid in ready_to_val + sorted(validating):
            spec = self.tiers.get(self.nodes[tid]['tier'])
            if spec:
                vg = spec['validation_good']
                va = spec['validation_amount'] * 1000
                extra_need[(self.capital, vg)] = extra_need.get((self.capital, vg), 0) + va // 2
                floors[(self.capital, vg)] = max(floors.get((self.capital, vg), 0), va + 4000)

        free_labs = [l for l in labs if l['id'] not in busy_labs]
        for line in free_labs:
            if not ready_to_val:
                break
            tid = ready_to_val[0]
            spec = self.tiers[self.nodes[tid]['tier']]
            if avail.get((line['hub_id'], spec['validation_good']), 0) >= spec['validation_amount'] * 1000:
                out.append(self._cmd('research', 'validate_project', line_id=line['id'], tech_id=tid))
                ready_to_val.pop(0)
                busy_labs.add(line['id'])
                prev = focus.get(line['id'])
                if prev in assigned:
                    assigned[prev] -= 1

        cap_per_tech = max(2, min(3, len(labs)))
        for line in free_labs:
            lid = line['id']
            if lid in busy_labs:
                continue
            cur = focus.get(lid)
            proj = projects.get(cur, {}) if cur else {}
            if cur and cur not in known and not proj.get('theory_complete'):
                continue
            pick = None
            for tid in self.spine:
                if assigned.get(tid, 0) < cap_per_tech and self._tech_open(tid, known) and not projects.get(tid, {}).get('theory_complete'):
                    pick = tid
                    break
            if pick is None:
                cands = sorted(
                    (self.nodes[t]['tier'], t) for t in self.nodes
                    if assigned.get(t, 0) < cap_per_tech and self._tech_open(t, known) and not projects.get(t, {}).get('theory_complete')
                )
                pick = cands[0][1] if cands else None
            if pick and pick != cur:
                assigned[pick] = assigned.get(pick, 0) + 1
                out.append(self._cmd('research', 'set_focus', line_id=lid, tech_id=pick, sec_mode='open'))

        need_t2_packs = any(
            self.nodes[t]['tier'] == 2 for t in (set(focus.values()) | set(ready_to_val)) if t in self.nodes
        )
        return known, need_t2_packs

    # ------------------------------------------------------------------ 3. HeavyCapitalPlanner
    def _recipe_va(self, rid: str) -> Fraction:
        rec = self.recipes.get(rid)
        if not rec:
            return Fraction(0)
        return (
            sum(q_val(q) * self.weights.get(g, Fraction(0)) for g, q in rec['outputs'].items())
            - sum(q_val(q) * self.weights.get(g, Fraction(0)) for g, q in rec['inputs'].items())
        )

    def _plan_capital_buildout(self, obs: dict, avail: dict, hub_load: dict, known: set,
                               need_t2_packs: bool, floors: dict, out: list) -> None:
        hub = self.capital
        w = obs['window']
        facs = [f for f in obs.get('facilities', ()) if f['hub_id'] == hub]
        fac_kind = {f['id']: f['facility_type'] for f in facs}
        jobs = {j['id']: j for j in obs.get('work', {}).get('jobs', ())}

        for jid, job in jobs.items():
            if jid in self.build_intent:
                self.facility_recipe[job.get('target_id')] = self.build_intent.pop(jid)

        fallback_recipe = {
            '冶金厂': 'P001', '材料厂': 'P005', '机械厂': 'P011', '电子厂': 'P012',
            '食品厂': 'P031', '农场': 'P028', '轻工厂': 'P032', '制药厂': 'P033',
            '化工厂': 'P008', '装备厂': 'P044', '实验室': 'P075', '电站': 'P070',
        }
        for line in obs.get('lines', ()):
            if line['hub_id'] != hub or line.get('recipe') is not None:
                continue
            if line.get('under_construction') or line.get('work_locked'):
                continue
            fid = line['id'].rsplit('/line', 1)[0]
            rec_id = self.facility_recipe.get(fid) or fallback_recipe.get(fac_kind.get(fid))
            if not rec_id or rec_id not in self.recipes:
                continue
            if not set(self.recipes[rec_id].get('requires_all') or ()) <= known:
                continue
            tag = ('equip_line', line['id'])
            if self._can_retry(tag, w, wait=4, max_fail=3):
                self.cooldown[tag] = w
                out.append(self._cmd('industry', 'set_line', tag=tag, line_id=line['id'],
                                     recipe=rec_id, mode='general'))

        committed = {}
        for cid in list(self.active_build_costs):
            job = jobs.get(cid)
            if job is None:
                if w - self.active_build_costs[cid][1] > 4:
                    del self.active_build_costs[cid]
                    continue
                rem = Fraction(1)
            else:
                rem = 1 - Fraction(job['elapsed_ticks'], max(1, job['duration_ticks']))
            for g, q in self.active_build_costs[cid][0].items():
                committed[g] = committed.get(g, 0) + q * rem

        have = {g: avail.get((hub, g), 0) for g in ('steel', 'cement', 'basic_parts')}
        free = {g: have[g] - committed.get(g, 0) - BUILD_RESERVE[g] for g in have}

        def reserve_floor(cost: dict):
            for g in BUILD_RESERVE:
                floors[(hub, g)] = max(
                    floors.get((hub, g), 0),
                    int(BUILD_RESERVE[g] + committed.get(g, 0) + cost.get(g, 0)),
                )

        def issue_build(tag, cost, op, recipe=None, **fields):
            cmd = self._cmd('industry', op, tag=tag, **fields)
            self.active_build_costs[cmd['id']] = (dict(cost), w)
            self.cooldown[tag] = w
            if recipe:
                self.build_intent[cmd['id']] = recipe
            for g, q in cost.items():
                committed[g] = committed.get(g, 0) + q
                free[g] -= q
            out.append(cmd)

        hubrow = next((h for h in obs.get('hubs', ()) if h['id'] == hub), {})
        pop = hubrow.get('population_mU', 0)
        housing = hubrow.get('housing_mU', 0)
        engineers = hubrow.get('skills_mU', {}).get('engineer', 0)
        houses = sum(1 for f in facs if f['facility_type'] == 'housing' and f.get('commissioned'))
        occupied_slots = sum(1 for f in facs if f['facility_type'] != 'housing')
        free_slots = 20 * (10 + houses) // 10 - occupied_slots
        building_house = any(f['facility_type'] == 'housing' and not f.get('commissioned') for f in facs)

        # Proactive warehouse expansion (heavy industrial throughput keeps expanded modules occupied -> high Kinfra!)
        room = hubrow.get('warehouse_mU', 0)
        load = hub_load.get(hub, 0)
        if room > 0 and load * 100 > room * 64 and not any(j.get('kind') == 'warehouse_expand' and j.get('hub_id') == hub for j in jobs.values()):
            modules = max(2, min(4, int(load * 100 - room * 60) // 20000000 + 2))
            w_cost = {g: q * modules for g, q in COST_WAREHOUSE.items()}
            if self._can_retry('wh_cap', w, wait=4) and all(have[g] - committed.get(g, 0) >= q for g, q in w_cost.items()):
                issue_build('wh_cap', w_cost, 'warehouse_expand', hub_id=hub, modules=modules)
                reserve_floor({})
                return
            if load * 100 > room * 78:
                reserve_floor(w_cost)
                return

        rcount = {}
        for l in obs.get('lines', ()):
            if l['hub_id'] == hub:
                r = l.get('recipe') or self.facility_recipe.get(l['id'].rsplit('/line', 1)[0])
                if r:
                    rcount[r] = rcount.get(r, 0) + 1
        for f in facs:
            if not f.get('commissioned'):
                r = self.facility_recipe.get(f['id']) or fallback_recipe.get(f['facility_type'])
                if r:
                    rcount[r] = rcount.get(r, 0) + 1

        # Vertically-integrated build queue:
        # Build downstream VA sinks (farms, light goods, electronics, parts) + upstream materials so every ton of steel/cement is consumed productively!
        queue = []
        head = Fraction(54, 50)
        if self.cap_can_farm and rcount.get('P031', 0) * 3000 * 10 > rcount.get('P028', 0) * 4000 * 9:
            queue.append(('farm_bal', COST_S1, {'facility_type': '农场', 'profile_id': 'S1'}, 'P028'))
        elif pop * HOUSEHOLD_RATES['food'] * head > rcount.get('P031', 0) * 3000:
            queue.append(('food_cap', COST_S1, {'facility_type': '食品厂', 'profile_id': 'S1'}, 'P031'))
        if pop * HOUSEHOLD_RATES['consumer_goods'] * head > rcount.get('P032', 0) * 3000:
            queue.append(('cg_cap', COST_S1, {'facility_type': '轻工厂', 'profile_id': 'S1'}, 'P032'))

        # High-VA downstream industrial powerhouse (farms + light goods + parts + electronics + cement)
        if self.cap_can_farm and rcount.get('P028', 0) < 5:
            queue.append(('farm_hi', COST_S1, {'facility_type': '农场', 'profile_id': 'S1'}, 'P028'))
        if rcount.get('P032', 0) < 4:
            queue.append(('cg_hi', COST_S1, {'facility_type': '轻工厂', 'profile_id': 'S1'}, 'P032'))
        if rcount.get('P011', 0) < 2:
            queue.append(('parts_2', COST_S1, {'facility_type': '机械厂', 'profile_id': 'S1'}, 'P011'))
        if rcount.get('P005', 0) < 2:
            queue.append(('cem_2', COST_S1, {'facility_type': '材料厂', 'profile_id': 'S1'}, 'P005'))
        if rcount.get('P012', 0) < 2:
            queue.append(('elec_hi', COST_S1, {'facility_type': '电子厂', 'profile_id': 'S1'}, 'P012'))

        # Solid 5-lab industrial tech engine
        lab_cnt = sum(1 for f in facs if f['facility_type'] == '实验室')
        if lab_cnt < min(5, max(2, engineers // 1600)):
            if lab_cnt >= 4 * rcount.get('P062', 0):
                queue.append(('labsup', COST_S1, {'facility_type': '材料厂', 'profile_id': 'S1'}, 'P062'))
            else:
                queue.append(('lab_add', COST_S1, {'facility_type': '实验室', 'profile_id': 'S1'}, 'P075'))

        if rcount.get('P031', 0) < 6:
            queue.append(('food_hi', COST_S1, {'facility_type': '食品厂', 'profile_id': 'S1'}, 'P031'))
        if rcount.get('P001', 0) < 3:
            queue.append(('steel_3', COST_S1, {'facility_type': '冶金厂', 'profile_id': 'S1'}, 'P001'))
        if rcount.get('P044', 0) < 1:
            queue.append(('ammo_1', COST_S1, {'facility_type': '装备厂', 'profile_id': 'S1'}, 'P044'))
        if rcount.get('P011', 0) < 3 and w > 30:
            queue.append(('parts_3', COST_S1, {'facility_type': '机械厂', 'profile_id': 'S1'}, 'P011'))
        if rcount.get('P005', 0) < 3 and w > 35:
            queue.append(('cem_3', COST_S1, {'facility_type': '材料厂', 'profile_id': 'S1'}, 'P005'))

        if need_t2_packs and rcount.get('P063', 0) < 1:
            queue.append(('eng_pack', COST_S1, {'facility_type': '机械厂', 'profile_id': 'S1'}, 'P063'))

        if {'L01', 'I02'} <= known and rcount.get('P008', 0) < 2:
            queue.append(('pure_chem', COST_S2, {'facility_type': '化工厂', 'profile_id': 'S2'}, 'P008'))
        if {'L01', 'A04'} <= known and rcount.get('P033', 0) < 2:
            queue.append(('pharma_s2', COST_S2, {'facility_type': '制药厂', 'profile_id': 'S2'}, 'P033'))
        if {'L01', 'I01'} <= known and rcount.get('P003', 0) < 1 and avail.get((hub, 'bauxite'), 0) >= 8000:
            queue.append(('alum_s2', COST_S2, {'facility_type': '冶金厂', 'profile_id': 'S2'}, 'P003'))
        if {'L01', 'V02'} <= known and rcount.get('P105', 0) < 1:
            queue.append(('remed_s2', COST_S2, {'facility_type': '化工厂', 'profile_id': 'S2'}, 'P105'))

        has_acad = any(s.get('kind') == 'academy' and s.get('hub_id') == hub for s in obs.get('systems', ()))
        free_pub = any(
            l['hub_id'] == hub and l.get('slot_use') == 'unassigned'
            and fac_kind.get(l['id'].rsplit('/line', 1)[0]) == 'public'
            for l in obs.get('lines', ())
        )
        if 'R02' in known and not has_acad and not free_pub and not any(
            f['facility_type'] == 'public' and not f.get('commissioned') for f in facs
        ):
            queue.append(('pub_acad', COST_S1, {'facility_type': 'public', 'profile_id': 'S1'}, None))

        online_plants = [l for l in obs.get('lines', ()) if l['hub_id'] == hub and l.get('plant') and l['plant'].get('online')]
        all_plants = [l for l in obs.get('lines', ()) if l['hub_id'] == hub and l.get('plant')]
        gen_now = sum(l['plant'].get('generation_mU', 0) for l in online_plants)
        total_cap = sum(POWER_CAP_MU.get(l.get('recipe'), 1000) for l in all_plants)
        if total_cap > 0 and gen_now > total_cap * Fraction(78, 100) and len(all_plants) < 8:
            queue.insert(0, ('pwr_exp', COST_S1, {'facility_type': '电站', 'profile_id': 'S1'}, 'P070'))

        need_house = (free_slots <= 2 or pop > housing * 82 // 100) and not building_house
        if need_house and free_slots <= 0:
            if self._can_retry('house_cap', w, wait=6) and all(free[g] >= q for g, q in COST_HOUSING.items()):
                issue_build('house_cap', COST_HOUSING, 'build_facility', hub_id=hub,
                            facility_type='public', profile_id='P110')
                reserve_floor({})
                return
            reserve_floor(COST_HOUSING)
            return

        active_cap_jobs = sum(1 for j in jobs.values() if j.get('hub_id') == hub and j.get('kind') == 'build_facility')
        if active_cap_jobs < 2:
            for tag, cost, fields, recipe in queue:
                if not self._can_retry(tag, w, wait=6, max_fail=3):
                    continue
                if free_slots <= 0:
                    need_house = True
                    break
                if all(free[g] >= q for g, q in cost.items()):
                    issue_build(tag, cost, 'build_facility', recipe=recipe, hub_id=hub, **fields)
                    reserve_floor({})
                    return
                reserve_floor(cost)
                break

        if need_house and self._can_retry('house_cap', w, wait=6, max_fail=3):
            if all(free[g] >= q for g, q in COST_HOUSING.items()):
                issue_build('house_cap', COST_HOUSING, 'build_facility', hub_id=hub,
                            facility_type='public', profile_id='P110')
                reserve_floor({})
                return
            reserve_floor(COST_HOUSING)
        else:
            reserve_floor({})

    # ------------------------------------------------------------------ 4. Line scheduling (strict eta=1!)
    def _manage_lines(self, obs: dict, hub_need: dict, floors: dict, eta_denom: dict,
                      known: set, avail: dict, out: list) -> None:
        w = obs['window']
        spec_active = sum(1 for l in obs.get('lines', ()) if l.get('mode') == 'specialized')
        spec_budget = 6 if 'R04' in known else 0

        for line in obs.get('lines', ()):
            rid = line.get('recipe')
            rec = self.recipes.get(rid)
            if rec is None or line.get('under_construction') or line.get('work_locked'):
                continue
            if not set(rec.get('requires_all') or ()) <= known:
                continue
            if rec['facility'] in ('电站', '实验室'):
                continue
            hub = line['hub_id']
            target = 0
            for g in rec['outputs']:
                buf = 5 if g in ('food', 'consumer_goods', 'medicine', 'water', 'grain') else 4
                target = max(target, int(hub_need.get((hub, g), 0) * buf), floors.get((hub, g), 0))
                if hub == self.capital and g in CAPITAL_STOCK_FLOORS:
                    target = max(target, CAPITAL_STOCK_FLOORS[g])

            if hub == self.capital:
                if rid in DOWNSTREAM_RUN_RECIPES and self._recipe_va(rid) > 0:
                    # Downstream manufactured goods run at full eta-safe throughput (excess vented to pool)
                    eta_run = max(int(eta_denom.get(g, 0)) * 4 for g in rec['outputs']) + 22000
                    target = max(target, eta_run)
                elif rid in UPSTREAM_SMELT_RECIPES:
                    # Upstream heavy smelters: cap strictly at eta-safe level so chi NEVER exceeds 6!
                    eta_cap = max(max(int(eta_denom.get(g, 0)) * 5, CAPITAL_STOCK_FLOORS.get(g, 16000)) for g in rec['outputs'])
                    target = min(max(target, max(floors.get((hub, g), 0) for g in rec['outputs'])), eta_cap)

            cur_mode = line.get('mode') or 'general'
            want_mode = cur_mode
            if (cur_mode == 'general' and rid in SPECIALIZE_RECIPES and spec_active < spec_budget
                    and hub == self.capital and avail.get((hub, 'basic_parts'), 0) >= 8000):
                tag = ('spec', line['id'])
                if self._can_retry(tag, w, wait=12, max_fail=2):
                    want_mode = 'specialized'
                    spec_active += 1
                    self.cooldown[tag] = w

            cur_target = (line.get('options') or {}).get('target_stock_mU')
            if want_mode != cur_mode or cur_target is None or abs(cur_target - target) > max(1500, target // 4):
                out.append(self._cmd(
                    'industry', 'set_line',
                    tag=('spec', line['id']) if want_mode != cur_mode else None,
                    line_id=line['id'], recipe=rid, mode=want_mode, target_stock_mU=target,
                ))

    # ------------------------------------------------------------------ 5. Power & Mines
    def _manage_power_and_mines(self, obs: dict, avail: dict, hub_load: dict, nat_use: dict, out: list) -> None:
        w = obs['window']
        cycle = obs.get('cycle', w // 2)
        plants = sorted(
            (l for l in obs.get('lines', ()) if l.get('plant') and l['hub_id'] == self.capital
             and not l.get('under_construction') and not l.get('work_locked')),
            key=lambda l: l['id'],
        )
        coal_plants = [l for l in plants if l.get('recipe') == 'P070']
        if 'maint_stagger' not in self.flags and coal_plants:
            self.flags.add('maint_stagger')
            shifts = (18, 10, 2, -6, -14, 14, -10)
            for idx, l in enumerate(coal_plants):
                s = shifts[idx % len(shifts)]
                if l['plant'].get('maintenance_shift_cycles', 0) != s:
                    out.append(self._cmd('logistics', 'plant_maintenance', facility_id=l['id'], shift_cycles=s))

        for l in plants:
            if l.get('recipe') in ('P112', 'P072', 'P113', 'P114') and not l['plant'].get('online'):
                out.append(self._cmd('industry', 'plant_toggle', facility_id=l['id'], online=True))

        if w >= 4 and len(coal_plants) >= 2:
            def due_in(l, horizon):
                p = l['plant']
                nxt = p.get('next_maintenance_cycle')
                if nxt is None:
                    return False
                due = nxt + p.get('maintenance_shift_cycles', 0)
                return 0 <= due - cycle <= horizon

            online = [l for l in coal_plants if l['plant'].get('online')]
            offline = [l for l in coal_plants if not l['plant'].get('online') and l['plant'].get('mode') == 'OFF' and not due_in(l, 3)]
            healthy = [l for l in online if l['plant'].get('mode') in ('ON', 'STARTING') and not due_in(l, 2)]
            running = [l for l in online if l['plant'].get('mode') == 'ON']
            gen = sum(l['plant'].get('generation_mU', 0) for l in online)
            healthy_cap = 1000 * len(healthy)

            # High headroom: keep >= 3 plants or load < 72% cap so heavy factories NEVER brown out
            if (len(healthy) < 3 or gen > healthy_cap * Fraction(72, 100)) and offline and w - self.last_power_win >= 2:
                out.append(self._cmd('industry', 'plant_toggle', facility_id=offline[0]['id'], online=True))
                self.last_power_win = w
            elif (w - self.last_power_win >= 6 and len(online) > 3 and len(running) == len(online)
                  and not any(due_in(l, 2) for l in online)
                  and gen < (len(online) - 1) * 1000 * Fraction(62, 100)):
                out.append(self._cmd('industry', 'plant_toggle', facility_id=online[-1]['id'], online=False))
                self.last_power_win = w

        cap_row = next((h for h in obs.get('hubs', ()) if h['id'] == self.capital), {})
        cap_room = cap_row.get('warehouse_mU', 0)
        cap_crowded = cap_room > 0 and hub_load.get(self.capital, 0) * 100 > cap_room * 78
        for mine in obs.get('mines', ()):
            if mine.get('under_construction') or mine.get('work_locked'):
                continue
            good = mine.get('good')
            if good == 'water':
                continue
            mh = mine.get('hub_id')
            online = mine.get('online', True)
            have = avail.get((mh, good), 0)
            if mh == self.capital:
                use = max(3000, int(nat_use.get(good, 0)))
                hi = max(140000 if good == 'coal' else 72000, (7 if not cap_crowded else 4) * use)
                lo = max(80000 if good == 'coal' else 36000, 3 * use)
                if online and have > hi:
                    out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=False))
                elif not online and have < lo and not cap_crowded:
                    out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=True))
            else:
                if online and have > 40000:
                    out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=False))
                elif not online and have < 16000:
                    out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=True))

    # ------------------------------------------------------------------ 6. RegionalIndustrializationEngine
    def _eligible_minerals(self, hub_id: str) -> list[str]:
        reg = self.hubs_meta.get(hub_id, {}).get('region', '')
        terr = self.terrain_by_region.get(reg, 'plain')
        if terr in ('mountain', 'hill'):
            base = ('iron_ore', 'copper_ore', 'stone', 'rare_ore')
        else:
            base = ('coal', 'bauxite', 'natural_gas', 'quartz')
        return [m for m in MINERAL_PRIORITY if m in base]

    def _industrialize_satellites(self, obs: dict, avail: dict, known: set, out: list) -> None:
        w = obs['window']
        if w < 24:
            return
        jobs = {j['id']: j for j in obs.get('work', {}).get('jobs', ())}
        roads = {r['id']: r for r in obs.get('logistics', {}).get('roads', ())}
        hub_map = {h['id']: h for h in obs.get('hubs', ())}
        cap_pop = hub_map.get(self.capital, {}).get('population_mU', 0)
        cap_steel = avail.get((self.capital, 'steel'), 0)
        cap_cem = avail.get((self.capital, 'cement'), 0)
        cap_parts = avail.get((self.capital, 'basic_parts'), 0)
        cap_stone = avail.get((self.capital, 'stone'), 0)

        if {'L01', 'L02'} <= known:
            for sh, r in self.direct_roads.items():
                rid = r['id']
                if roads.get(rid, {}).get('level', 1) == 1 and ('corr', rid) not in self.flags:
                    cells = r.get('cells', ())
                    if 2 <= len(cells) <= 512:
                        blocks = Fraction(len(cells) - 1, 10)
                        cs = int(Fraction(3000) * blocks)
                        cc = int(Fraction(6000) * blocks)
                        ct = int(Fraction(4000) * blocks)
                        if cap_steel > cs + 32000 and cap_cem > cc + 36000 and cap_stone > ct + 12000:
                            if self._can_retry(('corr', rid), w, wait=8):
                                out.append(self._cmd('logistics', 'build_corridor', tag=('corr', rid),
                                                     origin_hub=self.capital, dest_hub=sh, mode='road',
                                                     level=2, path_cells=list(cells)))
                                self.cooldown[('corr', rid)] = w
                                self.flags.add(('corr', rid))
                                break

        ship_budget = 2
        for sh in self.satellite_hubs[:2]:
            path = self.paths_to_hub.get(sh)
            if not path:
                continue
            sh_row = hub_map.get(sh, {})
            sh_pop = sh_row.get('population_mU', 0)
            sh_house = sh_row.get('housing_mU', 0)
            sh_mines = [m for m in obs.get('mines', ()) if m.get('hub_id') == sh]
            sh_facs = [f for f in obs.get('facilities', ()) if f['hub_id'] == sh]
            sh_lines = [l for l in obs.get('lines', ()) if l['hub_id'] == sh]
            sh_steel = avail.get((sh, 'steel'), 0)
            sh_cem = avail.get((sh, 'cement'), 0)
            sh_parts = avail.get((sh, 'basic_parts'), 0)
            sh_busy = any(j.get('hub_id') == sh for j in jobs.values())

            for line in sh_lines:
                if line.get('recipe') is None and not line.get('under_construction') and not line.get('work_locked'):
                    fid = line['id'].rsplit('/line', 1)[0]
                    ft = next((f['facility_type'] for f in sh_facs if f['id'] == fid), None)
                    rid = self.facility_recipe.get(fid) or {'农场': 'P028', '材料厂': 'P005', '食品厂': 'P031'}.get(ft)
                    if rid and set(self.recipes[rid].get('requires_all') or ()) <= known:
                        tag = ('sline', line['id'])
                        if self._can_retry(tag, w, wait=4):
                            out.append(self._cmd('industry', 'set_line', tag=tag, line_id=line['id'], recipe=rid, mode='general'))
                            self.cooldown[tag] = w

            # Step 1: Build housing & pre-stage life goods BEFORE relocating workers (zero homelessness / zero phi loss!)
            if sh_house < 10000 and ('shouse', sh) not in self.flags and not sh_busy:
                if sh_steel >= 5000 and sh_cem >= 20000:
                    if self._can_retry(('shouse', sh), w, wait=8):
                        out.append(self._cmd('industry', 'build_facility', tag=('shouse', sh), hub_id=sh,
                                             facility_type='public', profile_id='P110'))
                        self.cooldown[('shouse', sh)] = w
                        self.flags.add(('shouse', sh))
                elif ship_budget > 0 and self._can_retry(('ship_sh', sh), w, wait=4):
                    if sh_steel < 5000 and cap_steel > 28000:
                        out.append(self._cmd('logistics', 'route_shipment', tag=('ship_sh', sh),
                                             origin_hub=self.capital, dest_hub=sh, corridor_path=list(path),
                                             good='steel', qty_mU=min(8000, 5000 - sh_steel),
                                             carrier_recipe='manual', repeat_cycles=0))
                        self.cooldown[('ship_sh', sh)] = w
                        ship_budget -= 1
                    elif sh_cem < 20000 and cap_cem > 32000:
                        out.append(self._cmd('logistics', 'route_shipment', tag=('ship_sh', sh),
                                             origin_hub=self.capital, dest_hub=sh, corridor_path=list(path),
                                             good='cement', qty_mU=min(8000, 20000 - sh_cem),
                                             carrier_recipe='manual', repeat_cycles=0))
                        self.cooldown[('ship_sh', sh)] = w
                        ship_budget -= 1

            # Pre-stage food/water/medicine/cg once housing exists (or while occupied)
            if sh_house >= 10000 or sh_pop > 0:
                for sup in ('food', 'water', 'medicine', 'consumer_goods'):
                    if avail.get((sh, sup), 0) < 2500 and avail.get((self.capital, sup), 0) > 20000:
                        if self._can_retry(('ssup', sh, sup), w, wait=6):
                            out.append(self._cmd('logistics', 'route_shipment', tag=('ssup', sh, sup),
                                                 origin_hub=self.capital, dest_hub=sh, corridor_path=list(path),
                                                 good=sup, qty_mU=3000, carrier_recipe='manual', repeat_cycles=0))
                            self.cooldown[('ssup', sh, sup)] = w

            # Step 2: Relocate workers ONLY after housing & initial life goods are staged!
            staged_life = all(avail.get((sh, g), 0) >= 1500 for g in ('food', 'water', 'medicine', 'consumer_goods'))
            if sh_house >= 10000 and staged_life and sh_pop < 4000 and cap_pop > 165000 and ('reloc', sh) not in self.flags:
                if self._can_retry(('reloc', sh), w, wait=8, max_fail=2):
                    out.append(self._cmd('governance', 'relocate_population', tag=('reloc', sh),
                                         origin_hub=self.capital, dest_hub=sh,
                                         skills_mU={'general_worker': 4000, 'skilled_worker': 0, 'engineer': 0},
                                         path=list(path), allow_unhoused=False))
                    self.cooldown[('reloc', sh)] = w
                    self.flags.add(('reloc', sh))

            # Step 3: Build a local Farm (P028) at plain/hill satellite (guaranteed compatible, boosts sigma_r + Kinfra!)
            reg = self.hubs_meta.get(sh, {}).get('region', '')
            terr = self.terrain_by_region.get(reg, 'plain')
            sh_farms = [f for f in sh_facs if f['facility_type'] == '农场']
            if sh_pop >= 2000 and terr in ('plain', 'hill') and len(sh_farms) < 2 and not sh_busy:
                if sh_steel >= 6000 and sh_cem >= 6000 and sh_parts >= 2000:
                    tag = ('sfarm', sh, len(sh_farms))
                    if self._can_retry(tag, w, wait=8, max_fail=1):
                        cmd = self._cmd('industry', 'build_facility', tag=tag, hub_id=sh,
                                        facility_type='农场', profile_id='S1')
                        self.build_intent[cmd['id']] = 'P028'
                        self.cooldown[tag] = w
                        out.append(cmd)
                elif ship_budget > 0 and self._can_retry(('ship_sf', sh), w, wait=6):
                    for g, need_q, cap_q, cap_min in (
                        ('steel', 6000, cap_steel, 30000),
                        ('cement', 6000, cap_cem, 34000),
                        ('basic_parts', 2000, cap_parts, 14000),
                    ):
                        have_g = avail.get((sh, g), 0)
                        if have_g < need_q and cap_q > cap_min:
                            out.append(self._cmd('logistics', 'route_shipment', tag=('ship_sf', sh),
                                                 origin_hub=self.capital, dest_hub=sh, corridor_path=list(path),
                                                 good=g, qty_mU=min(6000, need_q - have_g),
                                                 carrier_recipe='manual', repeat_cycles=0))
                            self.cooldown[('ship_sf', sh)] = w
                            ship_budget -= 1
                            break

            # Step 4: Build 1 G1 mine (at most 1 probe per mineral so rejects stay tiny!)
            if sh_pop >= 2000 and not sh_mines and not sh_busy:
                cands = self._eligible_minerals(sh)
                chosen = next((m for m in cands if self.failures.get(('smine', sh, m), 0) < 1), None)
                if chosen and sh_steel >= 4000 and sh_cem >= 4000 and sh_parts >= 1000:
                    tag = ('smine', sh, chosen)
                    if self._can_retry(tag, w, wait=6, max_fail=1):
                        out.append(self._cmd('industry', 'build_facility', tag=tag, hub_id=sh,
                                             facility_type='public', profile_id='G1', good=chosen))
                        self.cooldown[tag] = w

            # Step 5: Upgrade G1 mine -> G2 after L01+I01
            if {'L01', 'I01'} <= known and sh_mines and not sh_busy:
                g1 = next((m for m in sh_mines if m.get('level') == 'G1' and not m.get('work_locked')), None)
                if g1 and sh_steel >= 8000 and sh_cem >= 8000 and sh_parts >= 3000:
                    tag = ('upg_g2', g1['id'])
                    if self._can_retry(tag, w, wait=12, max_fail=1):
                        out.append(self._cmd('industry', 'upgrade_facility', tag=tag,
                                             facility_id=g1['id'], target_profile='G2'))
                        self.cooldown[tag] = w

            # Funnel satellite grain & ores back to capital
            rev_path = list(reversed(path))
            for g in [m.get('good') for m in sh_mines if m.get('good') and m.get('good') != 'water'] + ['grain']:
                st = avail.get((sh, g), 0)
                if st >= 3000 and self._can_retry(('back', sh, g), w, wait=4):
                    out.append(self._cmd('logistics', 'route_shipment', tag=('back', sh, g),
                                         origin_hub=sh, dest_hub=self.capital, corridor_path=rev_path,
                                         good=g, qty_mU=min(6000, (st // 1000) * 1000),
                                         carrier_recipe='manual', repeat_cycles=0))
                    self.cooldown[('back', sh, g)] = w

    # ------------------------------------------------------------------ 7. SelectivePoolSafeguard
    def _manage_pool(self, obs: dict, avail: dict, physical: dict, nat_use: dict,
                     eta_denom: dict, floors: dict, ru: int, treasury: int, hh_cash: int, out: list) -> None:
        cycle = obs.get('cycle', 0)
        if cycle == self.last_pool_cycle:
            return
        self.last_pool_cycle = cycle
        hub = self.capital

        if cycle > 0 and ru < 8000000 and treasury > 32000000:
            amt = min(8000000, treasury - 25000000)
            out.append(self._cmd('finance', 'fx', side='buy_ru', currency=self.owner, amount_mU=amt))

        pool = obs.get('world_pool', {})
        buy_prices = pool.get('buy_price_ru_mU') or pool.get('price_ru_mU', {})
        sell_prices = pool.get('sell_price_ru_mU') or pool.get('price_ru_mU', {})
        pool_stock = pool.get('stock_mU', {})
        used = {(g, s): q for g, s, q in pool.get('used_mU', ())}

        bought = set()
        for g in URGENT_BUY_GOODS:
            if g == 'medical_supplies' and hh_cash < 12000000:
                continue
            want = max(floors.get((hub, g), 0), 3 * int(nat_use.get(g, 0)))
            if g == 'medical_supplies':
                want = max(want, 2500)
            elif g == 'medicine':
                want = max(want, 18000)
            elif g == 'food':
                want = max(want, 36000)
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

        # Selective overflow venting: preserve large domestic reserves on heavy materials (steel/cement/parts/ores),
        # while venting downstream finished goods above 4.2x use so eta NEVER drops below 1.0!
        for (h, g), have_q in sorted(avail.items()):
            if h != hub or g in NO_VENT_GOODS or g in bought or g not in sell_prices:
                continue
            base_keep = HEAVY_RESERVE_GOODS.get(g, 20000)
            mult = Fraction(11, 2) if g in HEAVY_RESERVE_GOODS else Fraction(21, 5)
            keep = max(
                base_keep,
                int(eta_denom.get(g, 0) * mult),
                PHYSICAL_SAFETY.get(g, 0),
                floors.get((hub, g), 0),
            )
            excess = min(have_q, physical.get(g, 0) - keep, POOL_CAP - used.get((g, 'sell'), 0))
            if excess >= 2000:
                out.append(self._cmd('trade', 'pool_sell', hub_id=hub, good=g, qty_mU=(excess // 1000) * 1000))

    # ------------------------------------------------------------------ 8. Governance, Academy & Defense
    def _govern_and_defend(self, obs: dict, avail: dict, known: set, treasury: int, hh_cash: int,
                           cap_row: dict, extra_need: dict, out: list) -> None:
        w = obs['window']
        payouts = (obs.get('corporate_finance') or {}).get('payout_policies', {})
        for cid in sorted(payouts):
            if payouts[cid] < 1000 and ('div', cid) not in self.flags:
                self.flags.add(('div', cid))
                out.append(self._cmd('finance', 'dividend', tag=('div', cid), company=cid, payout_mU=1000))

        # Start at 5% until support hits 960+, then 8% steady infrastructure tax to fund heavy capex!
        want_welfare = 10 ** 9 if 'F05' in known else 0
        cap_sup = cap_row.get('support_mU', 1000)
        if cap_sup < 950 or hh_cash < 18000000:
            want_tax = 500
        elif treasury < 24000000 and hh_cash > 24000000:
            want_tax = 1000
        else:
            want_tax = 800

        if want_tax != self.cur_tax_bp or (want_welfare > 0) != self.cur_welfare:
            self.cur_tax_bp = want_tax
            self.cur_welfare = want_welfare > 0
            out.append(self._cmd('governance', 'fiscal_policy', tax_rate_bp=want_tax,
                                 welfare_budget_mU=want_welfare, maint_budget_mU=10 ** 12))

        if 'F01' in known and 'ipo' not in self.flags:
            self.flags.add('ipo')
            for sector in ('mining', 'manufacturing'):
                out.append(self._cmd('finance', 'ipo', company=f'{self.owner}-{sector}', qty=1, min_price_mU=1000000000))

        if 'F02' in known:
            macro = obs.get('macroeconomy') or {}
            inf = macro.get('inflation_mU', 0)
            money = macro.get('money_mU', 150000000)
            issue = min(2000000, max(0, money // 100)) if (treasury < 24000000 and inf < 15) else 0
            if 'cb_init' not in self.flags or (issue > 0) != self.cur_cb_issue:
                self.flags.add('cb_init')
                self.cur_cb_issue = issue > 0
                out.append(self._cmd('finance', 'central_bank', rate_bp=350, issue_mU=issue,
                                     intervene_ru_mU=0, peg=None, capital_control_bp=0))

        cur_aut = obs.get('governance', {}).get('autonomy_mU', {})
        for h in obs.get('hubs', ()):
            if h.get('population_mU', 0) <= 0:
                continue
            reg = h['region_id']
            sup = h.get('support_mU', 1000)
            now = cur_aut.get(reg, 0)
            if sup < 870 and now < 500:
                out.append(self._cmd('governance', 'autonomy', region_id=reg, autonomy_mU=500))
            elif sup >= 960 and now > 0:
                out.append(self._cmd('governance', 'autonomy', region_id=reg, autonomy_mU=0))

        if 'R02' in known:
            has_acad = any(s.get('kind') == 'academy' and s.get('hub_id') == self.capital for s in obs.get('systems', ()))
            if not has_acad:
                extra_need[(self.capital, 'lab_supplies')] = extra_need.get((self.capital, 'lab_supplies'), 0) + 1000
                pub_facs = {f['id'] for f in obs.get('facilities', ()) if f['hub_id'] == self.capital and f['facility_type'] == 'public'}
                idle_pub = any(
                    l['hub_id'] == self.capital and l.get('slot_use') == 'unassigned'
                    and not l.get('work_locked') and not l.get('under_construction')
                    and l['id'].rsplit('/line', 1)[0] in pub_facs
                    for l in obs.get('lines', ())
                )
                if idle_pub and avail.get((self.capital, 'lab_supplies'), 0) >= 2000 and self._can_retry('acad', w, wait=10, max_fail=6):
                    out.append(self._cmd('industry', 'install_system', tag='acad', hub_id=self.capital, system_id='academy'))
                    self.cooldown['acad'] = w
            elif not obs.get('governance', {}).get('training', ()) and self._can_retry('train', w, wait=4, max_fail=6):
                skills = cap_row.get('skills_mU', {})
                if skills.get('skilled_worker', 0) > 22000:
                    out.append(self._cmd('governance', 'academy_train', tag='train', hub_id=self.capital,
                                         track='skilled_to_engineer', trainees_mU=4000))
                    self.cooldown['train'] = w

        if 'V02' in known:
            extra_need[(self.capital, 'remediation_agent')] = extra_need.get((self.capital, 'remediation_agent'), 0) + 1000
            if cap_row.get('pollution_mU', 0) > 3000 and avail.get((self.capital, 'remediation_agent'), 0) >= 1000:
                out.append(self._cmd('industry', 'remediate_pollution', region_id=cap_row['region_id'], amount_mU=1000))

        pairs = obs.get('diplomacy', {}).get('pairs', ())
        at_war = any(p.get('state') == 'war' for p in pairs)
        tracks = [t for t in obs.get('military_contacts', {}).get('tracks', ())
                  if t.get('currently_visible') and t.get('quality_mU', 0) > 150]
        incursion = bool(obs.get('military_control', {}).get('control_changes', ()))
        self.under_threat = at_war or incursion or bool(tracks)

        if self.under_threat and not self.mobilized:
            out.append(self._cmd('governance', 'mobilize', enabled=True))
            self.mobilized = True
            self.mobilized_tick = obs.get('tick', 0)
        elif not self.under_threat and self.mobilized and obs.get('tick', 0) >= self.mobilized_tick + 40:
            out.append(self._cmd('governance', 'mobilize', enabled=False))
            self.mobilized = False

        for idx, f in enumerate(obs.get('formations', ())):
            cur_reg = f.get('region_id')
            if not cur_reg:
                continue
            target_reg = cur_reg
            if self.under_threat and tracks and idx > 0 and tracks[0].get('region_id'):
                target_reg = tracks[0]['region_id']
            if f.get('task') != 'garrison' or (self.under_threat and cur_reg != target_reg):
                tag = ('gar', f['id'])
                if self._can_retry(tag, w, wait=4, max_fail=4):
                    out.append(self._cmd('military', 'formation_task', tag=tag, formation_id=f['id'],
                                         task='garrison', target_region=target_reg, posture='preserve',
                                         supply_hub=self.capital, retreat_loss_mU=500,
                                         expected_version=f.get('version', 0)))
                    self.cooldown[tag] = w

    # ------------------------------------------------------------------ decide
    def decide(self, obs: dict) -> list[dict]:
        out = []
        self._process_receipts(obs)
        avail, physical, hub_load = self._inv_index(obs)
        ru, treasury, hh_cash, cap_row = self._cash_summary(obs)
        extra_need, floors = {}, {}
        w = obs.get('window', 0)

        known, need_t2_packs = self._run_research(obs, avail, out, extra_need, floors)

        # Window 0: buy small initial RU so window 1 immediately secures medical_supplies (s_med = 2.0 from cycle 0!)
        if w == 0 and ru < 4000000 and treasury > 25000000:
            out.append(self._cmd('finance', 'fx', side='buy_ru', currency=self.owner, amount_mU=5000000))
        if w == 1 and hh_cash > 12000000 and ru >= 1500000 and avail.get((self.capital, 'medical_supplies'), 0) < 1000:
            out.append(self._cmd('trade', 'pool_buy', hub_id=self.capital, good='medical_supplies', qty_mU=2000))

        if w % 2 == 0:
            self._update_utilization(obs)
            self._govern_and_defend(obs, avail, known, treasury, hh_cash, cap_row, extra_need, out)
            self._plan_capital_buildout(obs, avail, hub_load, known, need_t2_packs, floors, out)
            hub_need, nat_use, eta_denom = self._compute_material_balance(obs, extra_need)
            self._manage_power_and_mines(obs, avail, hub_load, nat_use, out)
            self._manage_lines(obs, hub_need, floors, eta_denom, known, avail, out)
            self._manage_pool(obs, avail, physical, nat_use, eta_denom, floors, ru, treasury, hh_cash, out)
            self._industrialize_satellites(obs, avail, known, out)

        return out[:220]
