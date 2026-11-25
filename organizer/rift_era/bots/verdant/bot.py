"""裂陆纪元 清洁科技扩张对手（Verdant — Zero-Emission Efficiency, Tech-Landing & Adaptive Tactician）。

独立实现的私有评分对手（不共享、不导入其他 bot 代码，仅用 Python 标准库）。
核心战略画像：
  极速科研 + 强制 ActiveScale 落地 + 零排放清洁能源转型 + 污染预算自适应反手交易
  （强攻 U_pop 低污染满 Qsec/Qlife + U_tech 高科技深度与全额落地 + 自适应清洁扩张）。
架构模块：
  1. ActiveScaleTechOrchestrator：开局优先冲实验室与学院（R02 技工转工程师），沿高落地价值清洁科技主轴
     （R01->R02->L01->E02->C01->I02->V01->V02->A04->E03->R04->F01->C02->F02...）冲刺，每个解锁节点均绑定
     对应的实体落地动作（P112 水电落地 L01+E02、environment 落地 V01、P105 修复剂落地 V02、P017+sensor 落地 C01、
     P008 落地 I02、P033 落地 A04、风光电站落地 E03、specialized 落地 R04）；
  2. EmissionBudgetController：窗口 2 起严格压减冗余燃煤电站至最小安全负荷，解锁 E02+L01 后将旧煤电直接
     改造升级为 S2 水电（P112，零煤零排放 +3.05 VA）并在 E03 后按地区风/光地理属性（wind_by_region / cloud_by_region）
     部署最优可再生电站；V02 后每周期持续投放修复剂将污染惩罚减半；
  3. AdaptiveCounterTradeEngine：高排放冶炼线（P001/P002/P005/P006/P007）严格受污染预算约束，污染偏高且 RU
     充裕时自动缩减本国排放线改为世界池反手采购建材/化工，同时出口零排放高增加值洁净成品（粮/食/轻工/高纯化工/修复剂）；
  4. GeographyAwareCleanExpansion：按肥力与风光资源优选洁净副枢纽布局生态农业、低污染采掘与通信骨干网；
  5. AdaptiveFiscalGovernor：按居民现金流、通胀与支持度缺口连续自适应调节税率、地方自治、国企分红与国防动员。
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction

NAME = 'verdant'
VERSION = 'rift-verdant-1'

LIFE_RATES = {
    'food': Fraction(6, 100),
    'water': Fraction(4, 100),
    'medicine': Fraction(1, 100),
    'medical_supplies': Fraction(25, 10000),
    'consumer_goods': Fraction(3, 100),
}

LAB_RECIPES = ('P075', 'P076', 'P077', 'P079', 'P102')
CLEAN_POWER_RECIPES = ('P112', 'P072', 'P113', 'P114', 'P073', 'P083', 'P086')

# Clean-tech & guaranteed ActiveScale tech spine
CLEAN_TECH_SPINE = (
    'R01', 'R02', 'L01', 'E02', 'C01', 'I02', 'V01', 'V02', 'A04', 'E03',
    'R04', 'F01', 'C02', 'F02', 'E04', 'I01', 'I03', 'R03', 'F05', 'R05',
    'A01', 'C03', 'V03', 'E01', 'L02', 'A02', 'V05', 'C05', 'C06', 'A05',
    'A06', 'I04', 'I05', 'I06', 'C04', 'R06', 'R07', 'F03',
)

# Emitting recipes gated by EmissionBudgetController
EMITTING_RECIPES = {
    'P070': Fraction(2, 10),
    'P001': Fraction(11, 40),
    'P005': Fraction(11, 50),
    'P002': Fraction(33, 200),
    'P006': Fraction(3, 10),
    'P007': Fraction(33, 200),
    'P066': Fraction(2, 10),
    'P067': Fraction(2, 10),
}

# Clean zero-emission high-VA recipes prioritized for full throughput
CLEAN_EXPORT_RECIPES = frozenset({
    'P028', 'P031', 'P032', 'P004', 'P011', 'P012', 'P062', 'P063',
    'P008', 'P033', 'P105', 'P017', 'P018', 'P022', 'P023', 'P015', 'P013',
})

QUALITY_SENSOR_RECIPES = frozenset({'P013', 'P015', 'P019', 'P020', 'P065'})

S1_COST = {'steel': 6000, 'cement': 6000, 'basic_parts': 2000}
S2_COST = {'steel': 16000, 'cement': 16000, 'basic_parts': 8000}
S2_UPGRADE_COST = {'steel': 10000, 'cement': 10000, 'basic_parts': 6000}
P112_INSTALL_COST = {'steel': 10000, 'cement': 20000, 'basic_parts': 4000}
HOUSE_COST = {'steel': 5000, 'cement': 20000}
WH_COST = {'steel': 4000, 'cement': 6000}

RESERVE = {'steel': 8000, 'cement': 4000, 'basic_parts': 2000}
BASE_FLOORS = {'steel': 22000, 'cement': 28000, 'basic_parts': 12000, 'lab_supplies': 10000}

POOL_CAP = 40000
SAFETY_FLOOR = {
    'coal': 45000, 'water': 65000, 'grain': 20000, 'food': 40000,
    'consumer_goods': 18000, 'medicine': 20000, 'medical_supplies': 2500,
    'lab_supplies': 8000, 'ammunition': 4000, 'remediation_agent': 4000,
}
URGENT_LIFE_BUY = ('medical_supplies', 'medicine', 'food', 'consumer_goods', 'water', 'coal', 'grain', 'lab_supplies')
POLLUTING_COUNTER_BUY = ('steel', 'cement', 'chemicals', 'copper')
NEVER_SELL = frozenset({
    'electricity', 'compute', 'research_points', 'housing_capacity',
    'medicine', 'medical_supplies', 'remediation_agent', 'sensor',
    'communication_equipment', 'renewable_equipment', 'lab_supplies',
    'engineering_pack', 'precision_pack', 'system_pack', 'vehicle',
})


def q_num(v) -> Fraction:
    if isinstance(v, dict) and '$rational' in v:
        return Fraction(*v['$rational'])
    return Fraction(v)


class Bot:
    """Clean-Tech Expansionist: zero-emission power, high-velocity tech landing, adaptive counter-trading."""

    def __init__(self, init: dict):
        self.owner = init['owner']
        self.capital = f'{self.owner}-h0'
        catalog = init.get('catalog', {})
        industry = catalog.get('industry', {})
        tech = catalog.get('technology', {})
        self.recipes = {r['id']: r for r in industry.get('recipes', ())}
        self.nodes = {t['id']: t for t in tech.get('technologies', ())}
        self.tiers = {t['tier']: t for t in tech.get('research_tiers', ())}
        self.spine = [t for t in CLEAN_TECH_SPINE if t in self.nodes]

        init_pool = (init.get('initial_observation') or {}).get('world_pool', {})
        self.weights = {g: Fraction(p, 10000) for g, p in init_pool.get('price_ru_mU', {}).items()}

        wmap = init.get('map', {})
        self.hubs_meta = wmap.get('hubs', {})
        self.routes_meta = wmap.get('routes', {})
        self.terrain = wmap.get('terrain_by_region', {})
        self.fertility = wmap.get('fertility_by_region', {})
        self.wind = wmap.get('wind_by_region', {})
        self.cloud = wmap.get('cloud_by_region', {})

        # Choose best clean renewable recipe for capital geography (P113 wind vs P114 solar vs P072 hybrid)
        cap_reg = self.hubs_meta.get(self.capital, {}).get('region', '')
        self.cap_can_farm = self.terrain.get(cap_reg, 'plain') in ('plain', 'hill')
        cap_wind = self.wind.get(cap_reg, 400)
        cap_sun = 1000 - self.cloud.get(cap_reg, 400)
        e_wind = 8 * cap_wind
        e_solar = 7 * cap_sun
        e_hybrid = 9 * (cap_wind + cap_sun) // 2
        if e_hybrid >= e_wind and e_hybrid >= e_solar:
            self.best_renewable = 'P072'
        elif e_wind >= e_solar:
            self.best_renewable = 'P113'
        else:
            self.best_renewable = 'P114'

        # Pick 2 best green secondary hubs by fertility + clean energy potential
        self.green_hubs, self.green_paths = self._pick_green_subhubs()

        self.seq = 1
        self.tag_map = {}
        self.cooldown = {}
        self.fails = {}
        self.done = set()
        self.ordered = {}
        self.intended = {}
        self.job_costs = {}
        self.duty = {}
        self.prev_snap = {}
        self.power_win = -99
        self.pool_cycle = -1
        self.tax_bp = None
        self.welfare_on = False
        self.cb_issuing = False
        self.mobilized = False
        self.mobilized_tick = -99
        self.threat = False
        self.counter_trade_mode = False
        self.upgraded_hydro_ids = set()

    # ------------------------------------------------------------------ geography selection
    def _pick_green_subhubs(self):
        adj = {}
        for rid, r in self.routes_meta.items():
            if r.get('mode') == 'road' and r.get('initial_operational', True):
                a, b = r.get('a'), r.get('b')
                if a and b:
                    adj.setdefault(a, []).append((b, rid))
                    adj.setdefault(b, []).append((a, rid))
        for k in adj:
            adj[k].sort()
        own = {hid for hid, h in self.hubs_meta.items() if h.get('owner') == self.owner}
        dist = {self.capital: []}
        q = deque([self.capital])
        while q:
            u = q.popleft()
            p = dist[u]
            if len(p) >= 2:
                continue
            for v, rid in adj.get(u, ()):
                if v in own and v not in dist:
                    dist[v] = p + [rid]
                    q.append(v)

        cands = [h for h in dist if h != self.capital and h.startswith(f'{self.owner}-h')]
        one_hop = [h for h in cands if len(dist[h]) == 1]
        if one_hop:
            cands = one_hop
        def score_hub(hid):
            reg = self.hubs_meta.get(hid, {}).get('region', '')
            terr = self.terrain.get(reg, 'plain')
            farm_ok = 1 if terr in ('plain', 'hill') else 0
            fert = self.fertility.get(reg, 800)
            clean_e = self.wind.get(reg, 300) + (1000 - self.cloud.get(reg, 400))
            return (-farm_ok, len(dist[hid]), -(fert + clean_e // 2), hid)

        cands.sort(key=score_hub)
        chosen = cands[:2]
        return chosen, {h: dist[h] for h in chosen}

    # ------------------------------------------------------------------ helpers
    def _cmd(self, kind: str, op: str, tag=None, **fields) -> dict:
        row = {'id': str(self.seq), 'kind': kind, 'op': op}
        row.update(fields)
        if tag is not None:
            self.tag_map[row['id']] = tag
        self.seq += 1
        return row

    def _ok(self, tag, w: int, wait: int = 6, max_fail: int = 4) -> bool:
        return self.fails.get(tag, 0) < max_fail and self.cooldown.get(tag, -999) <= w - wait

    def _read_receipts(self, obs: dict) -> None:
        for rc in obs.get('command_receipts', ()):
            tag = self.tag_map.pop(str(rc.get('id')), None)
            if tag is None:
                continue
            if rc.get('status') == 'REJECTED':
                self.fails[tag] = self.fails.get(tag, 0) + 1
                self.cooldown.pop(tag, None)

    def _read_obs_metrics(self, obs: dict):
        avail, physical, load = {}, {}, {}
        for row in obs.get('inventory', ()):
            if len(row) != 4:
                continue
            h, b, g, q = row
            physical[g] = physical.get(g, 0) + q
            if b == 'available':
                avail[(h, g)] = avail.get((h, g), 0) + q
            if b != 'outdoor':
                load[h] = load.get(h, 0) + q
        ru = treasury = 0
        for row in obs.get('cash_accounts', ()):
            if len(row) >= 3:
                if row[0] == 'reserves' and row[1] == 'RU':
                    ru += row[2]
                elif row[0] == 'treasury' and row[1] == self.owner:
                    treasury += row[2]
        cap_row = next((h for h in obs.get('hubs', ()) if h['id'] == self.capital), {})
        hh_cash = cap_row.get('household_cash_mU', 0)
        pollution = cap_row.get('pollution_mU', 0)
        return avail, physical, load, ru, treasury, hh_cash, pollution, cap_row

    def _update_duty(self, obs: dict) -> None:
        decay = Fraction(3, 4)
        for line in obs.get('lines', ()):
            if self.recipes.get(line.get('recipe')) is None:
                continue
            lid = line['id']
            snap = (
                tuple(sorted((line.get('input_credit_nmU') or {}).items())),
                tuple(sorted((line.get('output_accum_nmU') or {}).items())),
            )
            moved = lid in self.prev_snap and snap != self.prev_snap[lid]
            running = (line.get('power_received_mU', 0) > 0 or moved) and not line.get('paused') and not line.get('under_construction')
            self.prev_snap[lid] = snap
            old = self.duty.get(lid, Fraction(1))
            self.duty[lid] = old * decay + (1 - decay) * (1 if running else 0)

    def _compute_needs(self, obs: dict, extra: dict):
        need, nat, eta = {}, {}, {}

        def add(h, g, q, to_eta=True):
            need[(h, g)] = need.get((h, g), 0) + q
            nat[g] = nat.get(g, 0) + q
            if to_eta:
                eta[g] = eta.get(g, 0) + q

        for h in obs.get('hubs', ()):
            pop = h.get('population_mU', 0)
            if pop > 0:
                for g, r in LIFE_RATES.items():
                    add(h['id'], g, pop * r)
        for line in obs.get('lines', ()):
            rid = line.get('recipe')
            rec = self.recipes.get(rid)
            if rec is None or line.get('under_construction'):
                continue
            if line.get('plant'):
                if line['plant'].get('online'):
                    oq = sum(q_num(q) for q in rec['outputs'].values()) or Fraction(1)
                    gen = line['plant'].get('generation_mU', 0) * 10
                    for g, q in rec['inputs'].items():
                        v = gen * q_num(q) / oq
                        nat[g] = nat.get(g, 0) + v
                        eta[g] = eta.get(g, 0) + v
                continue
            if rid in LAB_RECIPES:
                for g, q in rec['inputs'].items():
                    add(line['hub_id'], g, q_num(q) * 1000)
                continue
            d = self.duty.get(line['id'], Fraction(1))
            lf = Fraction(line.get('load_mU', 1000), 1000)
            pull = max(Fraction(1, 5), d)
            for g, q in rec['inputs'].items():
                need[(line['hub_id'], g)] = need.get((line['hub_id'], g), 0) + q_num(q) * 1000 * pull
                nat[g] = nat.get(g, 0) + q_num(q) * 1000 * pull
                eta[g] = eta.get(g, 0) + q_num(q) * 1000 * d * lf
        for (h, g), q in extra.items():
            add(h, g, q)
        return need, nat, eta

    # ------------------------------------------------------------------ 1. ActiveScaleTechOrchestrator
    def _is_researchable(self, tid: str, known: set) -> bool:
        node = self.nodes.get(tid)
        if not node or tid in known:
            return False
        ra = set(node.get('requires_all') or ())
        rb = set(node.get('requires_any') or ())
        return ra <= known and (not rb or bool(rb & known))

    def _orchestrate_research(self, obs: dict, avail: dict, out: list, extra: dict, floors: dict):
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
        for lid, tid in focus.items():
            if lid in lab_ids and lid not in busy:
                claimed[tid] = claimed.get(tid, 0) + 1

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
                floors[(self.capital, vg)] = max(floors.get((self.capital, vg), 0), va + 4000)

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

        # High concentration on critical-path techs (up to 4 labs per tech -> super fast T1/T2 unlocks!)
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
                if claimed.get(tid, 0) < per_tech and self._is_researchable(tid, known) and not projects.get(tid, {}).get('theory_complete'):
                    pick = tid
                    break
            if pick is None:
                rest = sorted(
                    (self.nodes[t]['tier'], t) for t in self.nodes
                    if claimed.get(t, 0) < per_tech and self._is_researchable(t, known) and not projects.get(t, {}).get('theory_complete')
                )
                pick = rest[0][1] if rest else None
            if pick and pick != cur:
                claimed[pick] = claimed.get(pick, 0) + 1
                out.append(self._cmd('research', 'set_focus', line_id=lid, tech_id=pick, sec_mode='open'))

        need_t2 = any(self.nodes[t]['tier'] == 2 for t in (set(focus.values()) | set(awaiting)) if t in self.nodes)
        need_t3 = any(self.nodes[t]['tier'] == 3 for t in (set(focus.values()) | set(awaiting)) if t in self.nodes)
        return known, need_t2, need_t3

    # ------------------------------------------------------------------ 2. Clean-Tech Capital & Landing Planner
    def _plan_clean_build(self, obs: dict, avail: dict, hub_load: dict, known: set,
                          need_t2: bool, need_t3: bool, floors: dict, extra: dict, out: list) -> None:
        hub = self.capital
        w = obs['window']
        facs = [f for f in obs.get('facilities', ()) if f['hub_id'] == hub]
        fac_kind = {f['id']: f['facility_type'] for f in facs}
        fac_prof = {f['id']: f.get('profile_id', 'S1') for f in facs}
        jobs = {j['id']: j for j in obs.get('work', {}).get('jobs', ())}

        for jid, job in jobs.items():
            if jid in self.ordered:
                self.intended[job.get('target_id')] = self.ordered.pop(jid)

        fallback = {
            '实验室': 'P075', '材料厂': 'P062', '机械厂': 'P063', '化工厂': 'P008',
            '制药厂': 'P033', '电子厂': 'P012', '食品厂': 'P031', '农场': 'P028',
            '轻工厂': 'P032', '电站': 'P112', '装备厂': 'P044',
        }
        for line in obs.get('lines', ()):
            if line['hub_id'] != hub or line.get('recipe') is not None:
                continue
            if line.get('under_construction') or line.get('work_locked'):
                continue
            fid = line['id'].rsplit('/line', 1)[0]
            rid = self.intended.get(fid) or fallback.get(fac_kind.get(fid))
            if rid and rid in self.recipes and set(self.recipes[rid].get('requires_all') or ()) <= known:
                # For P112 hydro / renewable plants, ensure install parts are present before calling set_line
                inst = {g: int(q_num(q) * 1000) for g, q in (self.recipes[rid].get('installed') or {}).items()}
                if all(avail.get((hub, g), 0) >= q for g, q in inst.items()):
                    tag = ('vline', line['id'])
                    if self._ok(tag, w, wait=4, max_fail=3):
                        self.cooldown[tag] = w
                        out.append(self._cmd('industry', 'set_line', tag=tag, line_id=line['id'],
                                             recipe=rid, mode='general'))

        # Convert upgraded S2 power plant from coal P070 to clean hydro P112!
        if {'E02', 'L01'} <= known:
            hydro_cnt = sum(1 for l in obs.get('lines', ()) if l['hub_id'] == hub and l.get('recipe') == 'P112')
            if hydro_cnt < 2:
                for line in obs.get('lines', ()):
                    if line['hub_id'] != hub or line.get('recipe') != 'P070':
                        continue
                    if line.get('under_construction') or line.get('work_locked'):
                        continue
                    fid = line['id'].rsplit('/line', 1)[0]
                    if fac_prof.get(fid) == 'S2' and all(avail.get((hub, g), 0) >= q + 4000 for g, q in P112_INSTALL_COST.items()):
                        tag = ('to_hydro', line['id'])
                        if self._ok(tag, w, wait=8, max_fail=3):
                            self.cooldown[tag] = w
                            out.append(self._cmd('industry', 'set_line', tag=tag, line_id=line['id'],
                                                 recipe='P112', mode='general'))
                            return

        committed = {}
        for cid in list(self.job_costs):
            job = jobs.get(cid)
            if job is None:
                if w - self.job_costs[cid][1] > 4:
                    del self.job_costs[cid]
                    continue
                rem = Fraction(1)
            else:
                rem = 1 - Fraction(job['elapsed_ticks'], max(1, job['duration_ticks']))
            for g, q in self.job_costs[cid][0].items():
                committed[g] = committed.get(g, 0) + q * rem

        have = {g: avail.get((hub, g), 0) for g in ('steel', 'cement', 'basic_parts')}
        free = {g: have[g] - committed.get(g, 0) - RESERVE[g] for g in have}

        def floor_for(cost):
            for g in RESERVE:
                floors[(hub, g)] = max(
                    floors.get((hub, g), 0),
                    int(RESERVE[g] + committed.get(g, 0) + cost.get(g, 0)),
                )

        def start_job(tag, cost, op, recipe=None, **fields):
            cmd = self._cmd('industry', op, tag=tag, **fields)
            self.job_costs[cmd['id']] = (dict(cost), w)
            self.cooldown[tag] = w
            if recipe:
                self.ordered[cmd['id']] = recipe
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

        # Warehouse expansion strictly when > 72% full
        room = hubrow.get('warehouse_mU', 0)
        load = hub_load.get(hub, 0)
        if room > 0 and load * 100 > room * 72 and not any(j.get('kind') == 'warehouse_expand' for j in jobs.values()):
            mods = max(1, min(3, int(load * 100 - room * 70) // 25000000 + 1))
            wc = {g: q * mods for g, q in WH_COST.items()}
            if self._ok('v_wh', w, wait=5) and all(have[g] - committed.get(g, 0) >= q for g, q in wc.items()):
                start_job('v_wh', wc, 'warehouse_expand', hub_id=hub, modules=mods)
                floor_for({})
                return
            if load * 100 > room * 82:
                floor_for(wc)
                return

        # Count current + pending lines
        rcount = {}
        for l in obs.get('lines', ()):
            if l['hub_id'] == hub:
                r = l.get('recipe') or self.intended.get(l['id'].rsplit('/line', 1)[0])
                if r:
                    rcount[r] = rcount.get(r, 0) + 1
        for f in facs:
            if not f.get('commissioned'):
                r = self.intended.get(f['id']) or fallback.get(f['facility_type'])
                if r:
                    rcount[r] = rcount.get(r, 0) + 1

        # Upgrade one offline S1 coal plant -> S2 for clean P112 hydro transition once E02+L01 unlocked!
        if {'E02', 'L01'} <= known and rcount.get('P112', 0) < 2 and not any(
            j.get('kind') == 'upgrade_facility' for j in jobs.values()
        ):
            s1_coal = next((
                f for f in facs
                if f['facility_type'] == '电站' and f.get('profile_id') == 'S1' and f.get('commissioned')
                and f['id'] not in self.upgraded_hydro_ids
                and any(l['id'] in f.get('line_ids', ()) and not (l.get('plant') or {}).get('online', True)
                        for l in obs.get('lines', ()))
            ), None)
            if s1_coal and self._ok(('upg_h', s1_coal['id']), w, wait=10, max_fail=2):
                if all(free[g] >= q for g, q in S2_UPGRADE_COST.items()):
                    self.upgraded_hydro_ids.add(s1_coal['id'])
                    start_job(('upg_h', s1_coal['id']), S2_UPGRADE_COST, 'upgrade_facility',
                              facility_id=s1_coal['id'], target_profile='S2')
                    floor_for(P112_INSTALL_COST)
                    return

        # Verdant build plan: LAB RUSH FIRST -> Life -> ActiveScale clean landings!
        plan = []
        lab_cnt = sum(1 for f in facs if f['facility_type'] == '实验室')
        # Fast lab rush: build up to 4 labs BEFORE anything non-survival so R01/R02/L01/E02 land in <200 ticks!
        lab_target = min(10, max(4, engineers // 1000))
        if lab_cnt < min(4, lab_target):
            if lab_cnt >= 4 * rcount.get('P062', 0):
                plan.append(('labsup_early', S1_COST, {'facility_type': '材料厂', 'profile_id': 'S1'}, 'P062'))
            else:
                plan.append(('lab_rush', S1_COST, {'facility_type': '实验室', 'profile_id': 'S1'}, 'P075'))

        # Life headroom
        head = Fraction(52, 50)
        if self.cap_can_farm and rcount.get('P031', 0) * 3000 * 10 > rcount.get('P028', 0) * 4000 * 9:
            plan.append(('farm_bal', S1_COST, {'facility_type': '农场', 'profile_id': 'S1'}, 'P028'))
        elif pop * LIFE_RATES['food'] * head > rcount.get('P031', 0) * 3000:
            plan.append(('food_cap', S1_COST, {'facility_type': '食品厂', 'profile_id': 'S1'}, 'P031'))
        if pop * LIFE_RATES['consumer_goods'] * head > rcount.get('P032', 0) * 3000:
            plan.append(('cg_cap', S1_COST, {'facility_type': '轻工厂', 'profile_id': 'S1'}, 'P032'))

        # Scale labs to full engineer capacity (up to 10 labs!)
        if lab_cnt < lab_target:
            if lab_cnt >= 4 * rcount.get('P062', 0):
                plan.append(('labsup', S1_COST, {'facility_type': '材料厂', 'profile_id': 'S1'}, 'P062'))
            else:
                plan.append(('lab_scale', S1_COST, {'facility_type': '实验室', 'profile_id': 'S1'}, 'P075'))

        # Public slot for Academy (R02) and Environment Station (V01) and Radio Backbone (C02)
        systems_here = {s.get('kind') for s in obs.get('systems', ()) if s.get('hub_id') == hub}
        need_pub = 0
        if 'R02' in known and 'academy' not in systems_here:
            need_pub += 1
        if 'V01' in known and 'environment' not in systems_here:
            need_pub += 1
        if 'C02' in known and 'radio_backbone' not in systems_here:
            need_pub += 1
        if 'A01' in known and 'reservoir' not in systems_here:
            need_pub += 1
        free_pub = sum(
            1 for l in obs.get('lines', ())
            if l['hub_id'] == hub and l.get('slot_use') == 'unassigned'
            and fac_kind.get(l['id'].rsplit('/line', 1)[0]) == 'public'
        )
        building_pub = sum(1 for f in facs if f['facility_type'] == 'public' and not f.get('commissioned'))
        if need_pub > free_pub + building_pub:
            plan.append(('pub_sys', S1_COST, {'facility_type': 'public', 'profile_id': 'S1'}, None))

        if need_t2 and rcount.get('P063', 0) < 1:
            plan.append(('eng_pack', S1_COST, {'facility_type': '机械厂', 'profile_id': 'S1'}, 'P063'))

        # Clean ActiveScale landings!
        # C01 -> electronic_parts (P012) + sensor (P017)
        if 'C01' in known and rcount.get('P012', 0) < 1:
            plan.append(('eparts', S1_COST, {'facility_type': '电子厂', 'profile_id': 'S1'}, 'P012'))
        if 'C01' in known and rcount.get('P012', 0) >= 1 and rcount.get('P017', 0) < 1:
            plan.append(('sensor', S1_COST, {'facility_type': '电子厂', 'profile_id': 'S1'}, 'P017'))
        # V02 + L01 -> S2 remediation_agent (P105)
        if {'V02', 'L01'} <= known and rcount.get('P105', 0) < 1:
            plan.append(('remed_s2', S2_COST, {'facility_type': '化工厂', 'profile_id': 'S2'}, 'P105'))
        # I02 + L01 -> S2 pure_chemicals (P008, +9.0 VA, 0 emission!)
        if {'I02', 'L01'} <= known and rcount.get('P008', 0) < 2:
            plan.append(('pchem_s2', S2_COST, {'facility_type': '化工厂', 'profile_id': 'S2'}, 'P008'))
        # A04 + L01 -> S2 medicine (P033, +10.0 VA, 0 emission!)
        if {'A04', 'L01'} <= known and rcount.get('P033', 0) < 2:
            plan.append(('pharma_s2', S2_COST, {'facility_type': '制药厂', 'profile_id': 'S2'}, 'P033'))
        # C02 -> communication_equipment (P018)
        if 'C02' in known and rcount.get('P012', 0) >= 1 and rcount.get('P018', 0) < 1:
            plan.append(('comm_s1', S1_COST, {'facility_type': '电子厂', 'profile_id': 'S1'}, 'P018'))
        # E03 + L01 -> renewable_equipment (P022) + best geography renewable plant (P072/P113/P114)!
        if {'E03', 'L01'} <= known and rcount.get('P022', 0) < 1:
            plan.append(('ren_eq', S2_COST, {'facility_type': '机械厂', 'profile_id': 'S2'}, 'P022'))
        if {'E03', 'L01'} <= known and avail.get((hub, 'renewable_equipment'), 0) >= 2000 and rcount.get(self.best_renewable, 0) < 2:
            plan.append(('ren_pwr', S2_COST, {'facility_type': '电站', 'profile_id': 'S2'}, self.best_renewable))
        # Clean high-VA expansion: extra clean farms (P028) & light industry (P032) & glass (P004)
        if self.cap_can_farm and rcount.get('P028', 0) < 5:
            plan.append(('clean_farm', S1_COST, {'facility_type': '农场', 'profile_id': 'S1'}, 'P028'))
        if rcount.get('P032', 0) < 3:
            plan.append(('clean_cg', S1_COST, {'facility_type': '轻工厂', 'profile_id': 'S1'}, 'P032'))
        if rcount.get('P011', 0) < 2:
            plan.append(('parts_2', S1_COST, {'facility_type': '机械厂', 'profile_id': 'S1'}, 'P011'))
        if need_t3 and {'I03', 'L01'} <= known and rcount.get('P064', 0) < 1:
            plan.append(('ppack', S2_COST, {'facility_type': '机械厂', 'profile_id': 'S2'}, 'P064'))

        building_house = any(f['facility_type'] == 'housing' and not f.get('commissioned') for f in facs)
        need_house = (slots <= 1 or pop > housing * 82 // 100) and not building_house

        if not any(j.get('kind') == 'build_facility' and j.get('hub_id') == hub for j in jobs.values()):
            for tag, cost, fields, recipe in plan:
                if not self._ok(tag, w, wait=6, max_fail=3):
                    continue
                if slots <= 0:
                    need_house = True
                    break
                if all(free[g] >= q for g, q in cost.items()):
                    start_job(tag, cost, 'build_facility', recipe=recipe, hub_id=hub, **fields)
                    floor_for({})
                    return
                floor_for(cost)
                return

        if need_house and not building_house and self._ok('v_house', w, wait=6, max_fail=3):
            if all(free[g] >= q for g, q in HOUSE_COST.items()):
                start_job('v_house', HOUSE_COST, 'build_facility', hub_id=hub, facility_type='public', profile_id='P110')
                floor_for({})
                return
            floor_for(HOUSE_COST)
        else:
            floor_for({})

    # ------------------------------------------------------------------ 3. Public Systems & Academy Pipeline
    def _install_systems_and_train(self, obs: dict, avail: dict, known: set, extra: dict, out: list) -> None:
        hub = self.capital
        w = obs['window']
        systems_here = {s.get('kind') for s in obs.get('systems', ()) if s.get('hub_id') == hub}
        pub_facs = {f['id'] for f in obs.get('facilities', ()) if f['hub_id'] == hub and f['facility_type'] == 'public'}
        free_pub_slot = any(
            l['hub_id'] == hub and l.get('slot_use') == 'unassigned'
            and not l.get('work_locked') and not l.get('under_construction')
            and l['id'].rsplit('/line', 1)[0] in pub_facs
            for l in obs.get('lines', ())
        )

        # Install clean & research systems for ActiveScale landing + efficiency!
        install_specs = (
            ('R02', 'academy', (('lab_supplies', 2000),)),
            ('V01', 'environment', (('sensor', 1000),)),
            ('C02', 'radio_backbone', (('communication_equipment', 1000),)),
            ('A01', 'reservoir', (('cement', 20000), ('steel', 5000))),
        )
        if free_pub_slot:
            for req_tech, sys_id, mats in install_specs:
                if req_tech not in known or sys_id in systems_here:
                    continue
                for g, q in mats:
                    extra[(hub, g)] = extra.get((hub, g), 0) + q // 2
                if all(avail.get((hub, g), 0) >= q for g, q in mats) and self._ok(('sys', sys_id), w, wait=10, max_fail=5):
                    out.append(self._cmd('industry', 'install_system', tag=('sys', sys_id), hub_id=hub, system_id=sys_id))
                    self.cooldown[('sys', sys_id)] = w
                    break

        # Continuous Academy engineer pipeline (feeds 8-10 lab rush!)
        if 'academy' in systems_here and not obs.get('governance', {}).get('training', ()):
            cap_row = next((h for h in obs.get('hubs', ()) if h['id'] == hub), {})
            skills = cap_row.get('skills_mU', {})
            eng = skills.get('engineer', 0)
            skilled = skills.get('skilled_worker', 0)
            gen = skills.get('general_worker', 0)
            if skilled > 18000 and eng < 28000 and self._ok('v_train_e', w, wait=4, max_fail=6):
                out.append(self._cmd('governance', 'academy_train', tag='v_train_e', hub_id=hub,
                                     track='skilled_to_engineer', trainees_mU=5000))
                self.cooldown['v_train_e'] = w
            elif gen > 90000 and skilled < 24000 and self._ok('v_train_s', w, wait=4, max_fail=6):
                out.append(self._cmd('governance', 'academy_train', tag='v_train_s', hub_id=hub,
                                     track='general_to_skilled', trainees_mU=5000))
                self.cooldown['v_train_s'] = w

    # ------------------------------------------------------------------ 4. EmissionBudgetController (Power, Remediation, Lines)
    def _control_emissions_and_power(self, obs: dict, avail: dict, known: set, pollution: int,
                                     cap_row: dict, extra: dict, out: list) -> None:
        w = obs['window']
        cycle = obs.get('cycle', w // 2)
        hub = self.capital

        # Always run V02 pollution remediation when pollution > 500 mU!
        if 'V02' in known:
            extra[(hub, 'remediation_agent')] = extra.get((hub, 'remediation_agent'), 0) + 1200
            if pollution > 500 and avail.get((hub, 'remediation_agent'), 0) >= 1000:
                out.append(self._cmd('industry', 'remediate_pollution', region_id=cap_row['region_id'], amount_mU=1000))

        # Power dispatch: keep all clean plants (P112/P072/P113/P114) online ALWAYS; aggressively trim coal P070!
        all_plants = sorted(
            (l for l in obs.get('lines', ()) if l.get('plant') and l['hub_id'] == hub
             and not l.get('under_construction') and not l.get('work_locked')),
            key=lambda l: l['id'],
        )
        clean_online_gen = 0
        coal_plants = []
        for l in all_plants:
            rid = l.get('recipe')
            if rid in CLEAN_POWER_RECIPES:
                if not l['plant'].get('online'):
                    out.append(self._cmd('industry', 'plant_toggle', facility_id=l['id'], online=True))
                clean_online_gen += l['plant'].get('generation_mU', 0)
            elif rid == 'P070':
                coal_plants.append(l)

        if 'v_stagger' not in self.done and coal_plants:
            self.done.add('v_stagger')
            shifts = (20, 12, 4, -8, -16)
            for idx, l in enumerate(coal_plants):
                s = shifts[idx % len(shifts)]
                if l['plant'].get('maintenance_shift_cycles', 0) != s:
                    out.append(self._cmd('logistics', 'plant_maintenance', facility_id=l['id'], shift_cycles=s))

        if w < 2 or not coal_plants:
            return

        def due_soon(l, h):
            p = l['plant']
            nxt = p.get('next_maintenance_cycle')
            if nxt is None:
                return False
            return 0 <= (nxt + p.get('maintenance_shift_cycles', 0)) - cycle <= h

        online_coal = [l for l in coal_plants if l['plant'].get('online')]
        offline_coal = [l for l in coal_plants if not l['plant'].get('online') and l['plant'].get('mode') == 'OFF' and not due_soon(l, 3)]
        healthy_coal = [l for l in online_coal if l['plant'].get('mode') in ('ON', 'STARTING') and not due_soon(l, 1)]
        running_coal = [l for l in online_coal if l['plant'].get('mode') == 'ON']
        coal_gen = sum(l['plant'].get('generation_mU', 0) for l in online_coal)
        healthy_cap = 1000 * len(healthy_coal)

        # Minimum coal floor drops from 2 -> 1 -> 0 as clean hydro/renewable generation ramps up!
        min_coal = 0 if clean_online_gen >= 1800 else (1 if clean_online_gen >= 900 else 2)

        need_more = (len(healthy_coal) < min_coal) or (healthy_cap > 0 and coal_gen > healthy_cap * Fraction(86, 100)) or (healthy_cap == 0 and coal_gen > 0)
        if need_more and offline_coal and (w - self.power_win >= 2 or len(healthy_coal) < len(online_coal)):
            out.append(self._cmd('industry', 'plant_toggle', facility_id=offline_coal[0]['id'], online=True))
            self.power_win = w
        elif (w - self.power_win >= 2 and len(online_coal) > min_coal and len(running_coal) == len(online_coal)
              and not any(due_soon(l, 2) for l in online_coal)
              and coal_gen < max(0, (len(online_coal) - 1) * 1000 * Fraction(82, 100))):
            # Shut down surplus coal ASAP to slash emissions!
            out.append(self._cmd('industry', 'plant_toggle', facility_id=online_coal[-1]['id'], online=False))
            self.power_win = w

    def _manage_clean_lines_and_mines(self, obs: dict, avail: dict, hub_load: dict, need: dict,
                                      nat_use: dict, eta_denom: dict, floors: dict, known: set,
                                      pollution: int, ru: int, hh_cash: int, out: list) -> None:
        w = obs['window']
        hub = self.capital
        # Adaptive regime switch: when pollution is high & RU + household cash are healthy, counter-trade emitting goods!
        if pollution > 7500 and ru > 16000000 and hh_cash > 24000000:
            self.counter_trade_mode = True
        elif pollution < 3500 or ru < 10000000 or hh_cash < 16000000:
            self.counter_trade_mode = False

        spec_active = sum(1 for l in obs.get('lines', ()) if l.get('mode') == 'specialized')
        spec_budget = 5 if 'R04' in known else 0
        have_sensors = avail.get((hub, 'sensor'), 0)

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
                buf = 5 if g in ('food', 'consumer_goods', 'medicine', 'water', 'grain', 'lab_supplies') else 3
                target = max(target, int(need.get((lh, g), 0) * buf), floors.get((lh, g), 0))
                if lh == hub and g in BASE_FLOORS:
                    target = max(target, BASE_FLOORS[g])

            if lh == hub:
                if rid in EMITTING_RECIPES:
                    # Strictly throttle emitting smelters: never run above real construction/domestic need!
                    if self.counter_trade_mode and rid in ('P001', 'P005', 'P007', 'P002'):
                        target = 0
                elif rid in CLEAN_EXPORT_RECIPES:
                    # Clean zero-emission high-VA lines run full tilt and export surplus!
                    target = max(target, max(int(eta_denom.get(g, 0)) * 4 for g in rec['outputs']) + 22000)

            cur_mode = line.get('mode') or 'general'
            want_mode = cur_mode
            if (cur_mode == 'general' and rid in ('P031', 'P032', 'P008', 'P033', 'P062', 'P105')
                    and spec_active < spec_budget and avail.get((hub, 'basic_parts'), 0) >= 6000):
                tag = ('vspec', line['id'])
                if self._ok(tag, w, wait=12, max_fail=2):
                    want_mode = 'specialized'
                    spec_active += 1
                    self.cooldown[tag] = w

            cur_addons = list((line.get('options') or {}).get('addons') or ())
            want_addons = list(cur_addons)
            if ('C01' in known and rid in QUALITY_SENSOR_RECIPES and 'sensor' not in want_addons
                    and have_sensors >= 1000 and lh == hub):
                tag = ('vsens', line['id'])
                if self._ok(tag, w, wait=12, max_fail=2):
                    want_addons.append('sensor')
                    have_sensors -= 1000
                    self.cooldown[tag] = w

            cur_t = (line.get('options') or {}).get('target_stock_mU')
            changed_cfg = (want_mode != cur_mode) or (sorted(want_addons) != sorted(cur_addons))
            if changed_cfg or cur_t is None or abs(cur_t - target) > max(1500, target // 4):
                ltag = ('vspec', line['id']) if want_mode != cur_mode else (('vsens', line['id']) if sorted(want_addons) != sorted(cur_addons) else None)
                kwargs = {'line_id': line['id'], 'recipe': rid, 'mode': want_mode, 'target_stock_mU': target}
                if want_addons:
                    kwargs['addons'] = sorted(want_addons)
                out.append(self._cmd('industry', 'set_line', tag=ltag, **kwargs))

        # Tight mine control to conserve deposits, cut mine power demand, and keep warehouses crisp
        cap_row = next((h for h in obs.get('hubs', ()) if h['id'] == hub), {})
        room = cap_row.get('warehouse_mU', 0)
        crowded = room > 0 and hub_load.get(hub, 0) * 100 > room * 68
        for mine in obs.get('mines', ()):
            if mine.get('under_construction') or mine.get('work_locked'):
                continue
            g = mine.get('good')
            if g == 'water':
                continue
            mh = mine['hub_id']
            online = mine.get('online', True)
            have = avail.get((mh, g), 0)
            if mh == hub:
                use = max(2000, int(nat_use.get(g, 0)))
                hi = max(75000 if g == 'coal' else 36000, (3 if crowded else 5) * use)
                lo = max(40000 if g == 'coal' else 18000, 2 * use)
                if online and have > hi:
                    out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=False))
                elif not online and have < lo and not crowded:
                    out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=True))
            else:
                if online and have > 25000:
                    out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=False))
                elif not online and have < 10000:
                    out.append(self._cmd('industry', 'plant_toggle', facility_id=mine['id'], online=True))

    # ------------------------------------------------------------------ 5. Adaptive Counter-Trade Pool
    def _adaptive_pool(self, obs: dict, avail: dict, physical: dict, nat_use: dict, eta_denom: dict,
                       floors: dict, ru: int, treasury: int, hh_cash: int, out: list) -> None:
        cycle = obs.get('cycle', 0)
        if cycle == self.pool_cycle:
            return
        self.pool_cycle = cycle
        hub = self.capital

        if cycle > 0 and ru < 12000000 and treasury > 34000000:
            amt = min(12000000, treasury - 25000000)
            out.append(self._cmd('finance', 'fx', side='buy_ru', currency=self.owner, amount_mU=amt))

        pool = obs.get('world_pool', {})
        buy_prices = pool.get('buy_price_ru_mU') or pool.get('price_ru_mU', {})
        sell_prices = pool.get('sell_price_ru_mU') or pool.get('price_ru_mU', {})
        pstock = pool.get('stock_mU', {})
        used = {(g, s): q for g, s, q in pool.get('used_mU', ())}

        bought = set()
        buy_goods = list(URGENT_LIFE_BUY)
        if self.counter_trade_mode:
            buy_goods.extend(POLLUTING_COUNTER_BUY)
        for (h, g) in sorted(floors):
            if h == hub and g not in buy_goods:
                buy_goods.append(g)

        for g in buy_goods:
            if g == 'medical_supplies' and hh_cash < 12000000:
                continue
            want = floors.get((hub, g), 0)
            if g in URGENT_LIFE_BUY:
                want = max(want, SAFETY_FLOOR.get(g, 0), 3 * int(nat_use.get(g, 0)))
            elif g in POLLUTING_COUNTER_BUY and self.counter_trade_mode:
                want = max(want, BASE_FLOORS.get(g, 14000))
            short = want - avail.get((hub, g), 0)
            if short <= 0 or g not in buy_prices:
                continue
            qty = min(POOL_CAP - used.get((g, 'buy'), 0), ((short + 999) // 1000) * 1000, pstock.get(g, 0) // 2)
            qty = (qty // 1000) * 1000
            if qty <= 0:
                continue
            cost = (qty * buy_prices[g] + 999) // 1000
            budget = ru if g in URGENT_LIFE_BUY else max(0, ru - 8000000)
            if cost <= budget:
                ru -= cost
                bought.add(g)
                out.append(self._cmd('trade', 'pool_buy', hub_id=hub, good=g, qty_mU=qty))

        # Sell clean surplus above 3.5-cycle working buffer so eta stays 1.0 & funds clean tech imports
        for (h, g), have_q in sorted(avail.items()):
            if h != hub or g in NEVER_SELL or g in bought or g not in sell_prices:
                continue
            min_keep = 32000 if g in ('steel', 'cement') else 16000
            keep = max(
                min_keep,
                int(eta_denom.get(g, 0) * Fraction(7, 2)),
                SAFETY_FLOOR.get(g, 0),
                floors.get((hub, g), 0),
            )
            excess = min(have_q, physical.get(g, 0) - keep, POOL_CAP - used.get((g, 'sell'), 0))
            if excess >= 2000:
                out.append(self._cmd('trade', 'pool_sell', hub_id=hub, good=g, qty_mU=(excess // 1000) * 1000))

    # ------------------------------------------------------------------ 6. GeographyAwareCleanExpansion
    def _expand_green_subhubs(self, obs: dict, avail: dict, known: set, out: list) -> None:
        w = obs['window']
        if w < 30 or not self.green_hubs:
            return
        cap_labs = sum(1 for f in obs.get('facilities', ()) if f['hub_id'] == self.capital and f['facility_type'] == '实验室')
        if cap_labs < 4:
            return
        jobs = {j['id']: j for j in obs.get('work', {}).get('jobs', ())}
        hub_map = {h['id']: h for h in obs.get('hubs', ())}
        cap_pop = hub_map.get(self.capital, {}).get('population_mU', 0)
        cap_steel = avail.get((self.capital, 'steel'), 0)
        cap_cem = avail.get((self.capital, 'cement'), 0)
        cap_parts = avail.get((self.capital, 'basic_parts'), 0)
        shipped = False

        for gh in self.green_hubs:
            path = self.green_paths.get(gh)
            if not path:
                continue
            row = hub_map.get(gh, {})
            pop = row.get('population_mU', 0)
            house = row.get('housing_mU', 0)
            facs = [f for f in obs.get('facilities', ()) if f['hub_id'] == gh]
            lines = [l for l in obs.get('lines', ()) if l['hub_id'] == gh]

            for line in lines:
                if line.get('recipe') is None and not line.get('under_construction') and not line.get('work_locked'):
                    fid = line['id'].rsplit('/line', 1)[0]
                    ft = next((f['facility_type'] for f in facs if f['id'] == fid), None)
                    if ft == '农场' and self._ok(('gfline', line['id']), w, wait=4):
                        out.append(self._cmd('industry', 'set_line', tag=('gfline', line['id']),
                                             line_id=line['id'], recipe='P028', mode='general'))
                        self.cooldown[('gfline', line['id'])] = w

            if pop < 3000 and cap_pop > 160000 and ('grel', gh) not in self.done:
                if self._ok(('grel', gh), w, wait=8, max_fail=2):
                    out.append(self._cmd('governance', 'relocate_population', tag=('grel', gh),
                                         origin_hub=self.capital, dest_hub=gh,
                                         skills_mU={'general_worker': 3000, 'skilled_worker': 0, 'engineer': 0},
                                         path=list(path), allow_unhoused=True))
                    self.cooldown[('grel', gh)] = w
                    self.done.add(('grel', gh))

            gh_steel = avail.get((gh, 'steel'), 0)
            gh_cem = avail.get((gh, 'cement'), 0)
            gh_parts = avail.get((gh, 'basic_parts'), 0)
            busy = any(j.get('hub_id') == gh for j in jobs.values())

            if pop > 0 and house < 10000 and ('ghouse', gh) not in self.done and not busy:
                if gh_steel >= 5000 and gh_cem >= 20000:
                    out.append(self._cmd('industry', 'build_facility', tag=('ghouse', gh), hub_id=gh,
                                         facility_type='public', profile_id='P110'))
                    self.done.add(('ghouse', gh))
                elif not shipped and self._ok(('gsh', gh), w, wait=6):
                    if gh_steel < 5000 and cap_steel > 28000:
                        out.append(self._cmd('logistics', 'route_shipment', tag=('gsh', gh),
                                             origin_hub=self.capital, dest_hub=gh, corridor_path=list(path),
                                             good='steel', qty_mU=5000 - gh_steel, carrier_recipe='manual', repeat_cycles=0))
                        self.cooldown[('gsh', gh)] = w
                        shipped = True
                    elif gh_cem < 20000 and cap_cem > 32000:
                        out.append(self._cmd('logistics', 'route_shipment', tag=('gsh', gh),
                                             origin_hub=self.capital, dest_hub=gh, corridor_path=list(path),
                                             good='cement', qty_mU=min(8000, 20000 - gh_cem), carrier_recipe='manual', repeat_cycles=0))
                        self.cooldown[('gsh', gh)] = w
                        shipped = True

            reg = self.hubs_meta.get(gh, {}).get('region', '')
            if pop >= 2000 and self.terrain.get(reg, 'plain') in ('plain', 'hill') and not busy:
                if not any(f['facility_type'] == '农场' for f in facs):
                    if gh_steel >= 6000 and gh_cem >= 6000 and gh_parts >= 2000 and self._ok(('gfarm', gh), w, wait=8, max_fail=2):
                        out.append(self._cmd('industry', 'build_facility', tag=('gfarm', gh), hub_id=gh,
                                             facility_type='农场', profile_id='S1'))
                        self.cooldown[('gfarm', gh)] = w
                    elif not shipped and self._ok(('gsf', gh), w, wait=6):
                        for g, need_q, cap_q, cap_min in (
                            ('steel', 6000, cap_steel, 30000),
                            ('cement', 6000, cap_cem, 34000),
                            ('basic_parts', 2000, cap_parts, 14000),
                        ):
                            hv = avail.get((gh, g), 0)
                            if hv < need_q and cap_q > cap_min:
                                out.append(self._cmd('logistics', 'route_shipment', tag=('gsf', gh),
                                                     origin_hub=self.capital, dest_hub=gh, corridor_path=list(path),
                                                     good=g, qty_mU=min(6000, need_q - hv), carrier_recipe='manual', repeat_cycles=0))
                                self.cooldown[('gsf', gh)] = w
                                shipped = True
                                break

            # Funnel clean grain back to capital
            grain = avail.get((gh, 'grain'), 0)
            if grain >= 3000 and self._ok(('gback', gh), w, wait=4):
                out.append(self._cmd('logistics', 'route_shipment', tag=('gback', gh),
                                     origin_hub=gh, dest_hub=self.capital, corridor_path=list(reversed(path)),
                                     good='grain', qty_mU=min(6000, (grain // 1000) * 1000),
                                     carrier_recipe='manual', repeat_cycles=0))
                self.cooldown[('gback', gh)] = w

            if pop > 0:
                for sup in ('food', 'water', 'medicine', 'consumer_goods'):
                    if avail.get((gh, sup), 0) < 2000 and avail.get((self.capital, sup), 0) > 20000:
                        if self._ok(('gsup', gh, sup), w, wait=6):
                            out.append(self._cmd('logistics', 'route_shipment', tag=('gsup', gh, sup),
                                                 origin_hub=self.capital, dest_hub=gh, corridor_path=list(path),
                                                 good=sup, qty_mU=2000, carrier_recipe='manual', repeat_cycles=0))
                            self.cooldown[('gsup', gh, sup)] = w

    # ------------------------------------------------------------------ 7. Adaptive Fiscal & Defense
    def _govern_and_defend(self, obs: dict, known: set, treasury: int, hh_cash: int, out: list) -> None:
        w = obs['window']
        payouts = (obs.get('corporate_finance') or {}).get('payout_policies', {})
        for cid in sorted(payouts):
            if payouts[cid] < 1000 and ('div', cid) not in self.done:
                self.done.add(('div', cid))
                out.append(self._cmd('finance', 'dividend', tag=('div', cid), company=cid, payout_mU=1000))

        # Adaptive tax: 6% normal, drops to 5% if household cash < 20M, up to 10% only under treasury stress
        want_welfare = 10 ** 9 if 'F05' in known else 0
        if self.tax_bp is None:
            want_tax = 600
        elif hh_cash < 20000000:
            want_tax = 500
        elif treasury < 18000000 and hh_cash > 24000000:
            want_tax = 1000
        elif treasury > 38000000:
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
            for sec in ('mining', 'manufacturing'):
                out.append(self._cmd('finance', 'ipo', company=f'{self.owner}-{sec}', qty=1, min_price_mU=1000000000))

        if 'F02' in known:
            macro = obs.get('macroeconomy') or {}
            inf = macro.get('inflation_mU', 0)
            money = macro.get('money_mU', 150000000)
            issue = min(2000000, max(0, money // 100)) if (treasury < 22000000 and inf < 15) else 0
            if 'cb' not in self.done or (issue > 0) != self.cb_issuing:
                self.done.add('cb')
                self.cb_issuing = issue > 0
                out.append(self._cmd('finance', 'central_bank', rate_bp=300, issue_mU=issue,
                                     intervene_ru_mU=0, peg=None, capital_control_bp=0))

        # Proportional autonomy based on exact support gap
        cur_aut = obs.get('governance', {}).get('autonomy_mU', {})
        for h in obs.get('hubs', ()):
            if h.get('population_mU', 0) <= 0:
                continue
            reg = h['region_id']
            sup = h.get('support_mU', 1000)
            now = cur_aut.get(reg, 0)
            want_aut = 0 if sup >= 950 else min(500, max(100, (960 - sup) * 3))
            if abs(want_aut - now) >= 150 or (want_aut == 0 and now > 0):
                out.append(self._cmd('governance', 'autonomy', region_id=reg, autonomy_mU=want_aut))

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
                tag = ('vgar', f['id'])
                if self._ok(tag, w, wait=4, max_fail=4):
                    out.append(self._cmd('military', 'formation_task', tag=tag, formation_id=f['id'],
                                         task='garrison', target_region=treg, posture='preserve',
                                         supply_hub=self.capital, retreat_loss_mU=500,
                                         expected_version=f.get('version', 0)))
                    self.cooldown[tag] = w

    # ------------------------------------------------------------------ decide
    def decide(self, obs: dict) -> list[dict]:
        out = []
        self._read_receipts(obs)
        avail, physical, hub_load, ru, treasury, hh_cash, pollution, cap_row = self._read_obs_metrics(obs)
        extra, floors = {}, {}
        w = obs.get('window', 0)

        known, need_t2, need_t3 = self._orchestrate_research(obs, avail, out, extra, floors)

        # Window 0 FX -> Window 1 quick medical_supplies buy for full s_med = 2.0 from cycle 0!
        if w == 0 and ru < 5000000 and treasury > 25000000:
            out.append(self._cmd('finance', 'fx', side='buy_ru', currency=self.owner, amount_mU=5000000))
        if w == 1 and hh_cash > 12000000 and ru >= 1500000 and avail.get((self.capital, 'medical_supplies'), 0) < 1000:
            out.append(self._cmd('trade', 'pool_buy', hub_id=self.capital, good='medical_supplies', qty_mU=2000))

        if w % 2 == 0:
            self._update_duty(obs)
            self._govern_and_defend(obs, known, treasury, hh_cash, out)
            self._plan_clean_build(obs, avail, hub_load, known, need_t2, need_t3, floors, extra, out)
            self._install_systems_and_train(obs, avail, known, extra, out)
            self._control_emissions_and_power(obs, avail, known, pollution, cap_row, extra, out)
            need, nat_use, eta_denom = self._compute_needs(obs, extra)
            self._manage_clean_lines_and_mines(obs, avail, hub_load, need, nat_use, eta_denom, floors, known,
                                               pollution, ru, hh_cash, out)
            self._adaptive_pool(obs, avail, physical, nat_use, eta_denom, floors, ru, treasury, hh_cash, out)
            self._expand_green_subhubs(obs, avail, known, out)

        return out[:220]
