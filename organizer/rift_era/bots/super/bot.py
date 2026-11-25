"""裂陆纪元 超级 bot（Super，评分集对手，不公开给选手）。

只用 Python 标准库，只读 init 目录与本国过滤观测，按 06 命令表下单。

策略骨架（r9 规则适配 + 多枢纽扩张 + 国防反快攻 + 28,800 tick 长周期货币闭环）：
  民生 > 货币回流与稳定 > 投资 > 国防与多枢纽扩张 > 实验室，带滞回。
1. 民生：按户需（BASKET）+ 下游产线投入 + 实验室/验证/建设需求，估计每种物品每周期需求，
   用 set_line.target_stock_mU（BASE 持续计划）把每条产线压到“缓冲周期 × 需求”；
   库存远超消耗的物品不再生产（eta_sink 下这些产出值为 0、投入照扣，07 §1）。
   食品与民用品缓冲更厚、不会被投资挤掉。
2. 货币闭环（长局 2,880 周期防流动性枯竭）：
   - 将所有国有企业分红比例设为 100%（finance/dividend payout_mU=1000），防止采矿/制造企业
     长期截留利润形成货币黑洞导致国库枯竭；
   - 地方自治按支持度动态调节（支持度 < 850 时给 50% 留成，>= 950 时归零，避免税款沉淀在
     不可支出的 public:h0 账户）；
   - 低税率（5%）优先保障居民现金与满支持度；国库偏低时动态回调税率或利用央行温和补流；
   - 解锁 F05 后开启社保预算回流居民。
3. 电力与矿井：燃煤电站按实测出力滞回启停并错峰检修；r9 下按需暂停富余矿井，节省电力煤耗、
   保护有限矿藏并防止公共仓库堵塞。
4. 投资与多枢纽扩张：优先扩仓与补民生/建材/实验室/制药/治污产能；开局稳态后升级通往副枢纽的
   道路走廊、迁移少量人力并开发副枢纽矿藏，按需单次运回矿石并向副枢纽补给民生品。
5. 国防与反快攻：保持基础弹药产线与底线库存；监测宣战、可视敌军轨迹与领地变动，自动动员
   并调度编队驻防首都与边境/受威胁区域，威胁解除后有序复员。
"""

from fractions import Fraction

NAME = 'super'
VERSION = 'rift-super-8'

BASKET = {'food': Fraction(6, 100), 'water': Fraction(4, 100), 'medicine': Fraction(1, 100),
          'medical_supplies': Fraction(25, 10000), 'consumer_goods': Fraction(3, 100)}
LAB_RECIPES = ('P075', 'P076', 'P077', 'P079', 'P102')
# generation capacity mU per tick at 100% output and minimum output fraction (05)
PLANT_CAP = {'P070': 1000, 'P071': 1200, 'P111': 1400, 'P112': 1500, 'P113': 800, 'P114': 700,
             'P072': 900, 'P073': 2000, 'P083': 3000, 'P086': 8000, 'P074': 300}
PLANT_MIN = {'P070': Fraction(2, 5), 'P071': Fraction(1, 5), 'P111': Fraction(1, 5), 'P073': Fraction(7, 10),
             'P083': Fraction(7, 10), 'P086': Fraction(1, 2)}
ROUTE = ['R01', 'F01', 'I02', 'C01', 'L01', 'A04', 'V01', 'R02', 'V02', 'E02', 'I01', 'R04', 'A01', 'C02', 'E01', 'L02',
         'A02', 'I03', 'R03', 'F02', 'F05', 'C03', 'E03', 'E04', 'A03', 'F03', 'R05', 'C05',
         'C06', 'A06', 'E05', 'I04', 'I05', 'I06', 'C04', 'A05', 'A08', 'R06', 'R07', 'R08', 'V03', 'V05']
# buffer cycles per output good (民生物品更厚)
BUFFER = 3
LAB_CAP = 8
LIFE_BUFFER = {'food': 5, 'consumer_goods': 4, 'medicine': 4, 'water': 4, 'grain': 3, 'lab_supplies': 3}
DUTY_DECAY = Fraction(3, 4)
HOUSE = {'cement': 20000, 'steel': 5000}
S1 = {'steel': 6000, 'cement': 6000, 'basic_parts': 2000}
S2 = {'steel': 16000, 'cement': 16000, 'basic_parts': 8000}
WAREHOUSE = {'steel': 4000, 'cement': 6000}
# never build below these (consumer goods steel, house/plant maintenance every 20 cycles)
RESERVE = {'steel': 8000, 'cement': 3000, 'basic_parts': 2000}
# mills keep at least this much on hand so a planned hall never waits a whole build time for parts
STOCK_FLOOR = {'steel': 24000, 'cement': 30000, 'basic_parts': 12000}

# world pool (r9): buy 11.5 x w_cat RU, sell 8.5 x w_cat RU, 40 units per good, side and cycle
POOL_CAP = 40000
# goods whose shortage stops power or food: never sell below this many mU
POOL_SAFETY = {'coal': 60000, 'water': 60000, 'grain': 18000, 'food': 35000, 'consumer_goods': 15000,
               'medicine': 20000, 'medical_supplies': 2000, 'lab_supplies': 3000, 'ammunition': 3000}
PRIORITY_MINERALS = ('iron_ore', 'copper_ore', 'coal', 'natural_gas', 'bauxite', 'rare_ore')
# eta_sink: chi = stock / C, eta = 1 while chi <= 6; keep at most this many cycles of estimated use
POOL_HOLD = 4
RUN_HEADROOM = 20000
# value-added units (w_cat) per unit of line power and per emitted pollution unit
POWER_COST = Fraction(12, 10)
EMISSION_COST = 12
EMISSIONS = {'P070': Fraction(1, 5), 'P001': Fraction(11, 40), 'P005': Fraction(11, 50), 'P002': Fraction(33, 200),
             'P006': Fraction(3, 10), 'P007': Fraction(33, 200), 'P066': Fraction(1, 5), 'P067': Fraction(1, 5)}
# r9: domestic mills produce building materials directly; pool buys only urgent life/construction floors
POOL_BUY = ()
POOL_MIN_STOCK = 1000000
POOL_MIN_RU = 2000000
# below this RU reserve medicine is rationed to half the household need so reserves last until A04
MEDICINE_RATION_RU = 6000000
LIFE_GOODS = ('medicine', 'medical_supplies', 'food', 'consumer_goods', 'water', 'coal', 'grain', 'ammunition')
NEVER_SELL = ('electricity', 'compute', 'research_points', 'housing_capacity', 'medicine', 'ammunition')


def ratio(value):
    if isinstance(value, dict) and '$rational' in value:
        return Fraction(*value['$rational'])
    return Fraction(value)


class Bot:
    def __init__(self, init):
        self.owner = init['owner']
        self.capital = f'{self.owner}-h0'
        catalog = init.get('catalog', {})
        industry = catalog.get('industry', {})
        tech = catalog.get('technology', {})
        self.recipes = {r['id']: r for r in industry.get('recipes', ())}
        self.facility_of = {rid: r['facility'] for rid, r in self.recipes.items()}
        self.nodes = {t['id']: t for t in tech.get('technologies', ())}
        self.tiers = {t['tier']: t for t in tech.get('research_tiers', ())}
        self.route = [t for t in ROUTE if t in self.nodes]
        self.next_id = 1
        self.done = set()
        self.pending = {}          # tag -> window issued (wait for receipt / completion)
        self.failed = {}           # tag -> failures
        self.plant_window = -99
        self.issued = {}           # command id -> tag
        self.duty = {}             # line id -> running share (EMA per cycle)
        self.trace = {}            # line id -> last (input credit, output accum) snapshot
        self.costs = {}            # command id -> (material cost, window) of own construction
        self.ordered = {}          # build command id -> recipe the new hall is for
        self.fresh = {}            # facility id -> recipe its first line gets
        self.pool_cycle = -1
        # world-pool price is 10 x w_cat: proportional catalog weights for value-added checks
        self.weights = {g: Fraction(p, 10000) for g, p in
                        (init.get('initial_observation') or {}).get('world_pool', {}).get('price_ru_mU', {}).items()}
        env = (catalog.get('runtime_rules') or {}).get('environment') or {}
        self.emissions = {r: ratio(q) for r, q in (env.get('emissions') or EMISSIONS).items()}
        self.pool_stock = {}
        self.ru = 0
        self.treasury = 0
        self.hh_cash = 50000000
        self._tax_bp = None
        self._welfare_set = False
        clock = init.get('clock') or {}
        self.total_cycles = (clock.get('total_ticks') or 1200) // (clock.get('cycle_ticks') or 10)
        self.map = init.get('map', {})
        self.hubs_data = self.map.get('hubs', {})
        self.routes_data = self.map.get('routes', {})
        self.terrain = self.map.get('terrain_by_region', {})
        self.secondary_hubs = sorted(
            h['id'] for h in self.hubs_data.values()
            if h.get('owner') == self.owner and h['id'] != self.capital and h['id'].startswith(f'{self.owner}-h')
        )[:2]
        self.corridors_to_subhubs = {}
        for rid, r in self.routes_data.items():
            if r.get('mode') == 'road' and self.capital in (r.get('a'), r.get('b')):
                other = r['b'] if r['a'] == self.capital else r['a']
                if other in self.secondary_hubs:
                    self.corridors_to_subhubs[other] = r
        self.mobilized = False
        self.mobilized_tick = -99
        self.threat = False

    # ------------------------------------------------------------------ util
    def cmd(self, kind, op, tag=None, **fields):
        row = {'id': str(self.next_id), 'kind': kind, 'op': op} | fields
        if tag:
            self.issued[row['id']] = tag
        self.next_id += 1
        return row

    @staticmethod
    def margin(rec, weights):
        return sum(ratio(q) * weights.get(g, 0) for g, q in rec['outputs'].items()) - \
            sum(ratio(q) * weights.get(g, 0) for g, q in rec['inputs'].items())

    def net_value(self, rid):
        rec = self.recipes[rid]
        return self.margin(rec, self.weights) - POWER_COST * ratio(rec.get('power') or 0) - \
            EMISSION_COST * self.emissions.get(rid, 0)

    def sustainable(self, obs, rec):
        """Full-run only while its inputs last: mined/life inputs stay at or above their safety
        stock, bought inputs only while RU reserves are above the floor."""
        for g in rec['inputs']:
            if g in POOL_SAFETY and self.stock(obs, self.capital, g) < POOL_SAFETY[g]:
                return False
            if g in POOL_BUY and self.ru < POOL_MIN_RU and obs.get('cycle', 0) > 0 and self.stock(obs, self.capital, g) < 10000:
                return False
        return True

    def hh_pool_ok(self):
        return self.hh_cash > 15000000

    def med_supplies_ok(self):
        return self.hh_cash > 12000000 and (self.ru >= 6000000 or self.treasury > 20000000)

    def poolable(self, rec):
        # Under r9 (POOL_BUY == ()), domestic mills produce steel/cement directly instead of buying from pool.
        return bool(POOL_BUY) and all(g in POOL_BUY for g in rec['outputs']) and self.hh_pool_ok() and (
            self.ru >= POOL_MIN_RU or obs_cycle_zero(self)) and all(
            self.pool_stock.get(g, 0) >= POOL_MIN_STOCK for g in rec['outputs'])

    def researchable(self, tid, known):
        row = self.nodes[tid]
        need_all = set(row.get('requires_all') or ())
        need_any = set(row.get('requires_any') or ())
        return tid not in known and need_all <= known and (not need_any or bool(need_any & known))

    def stock(self, obs, hub, good):
        return sum(r[3] for r in obs.get('inventory', ()) if len(r) == 4 and r[0] == hub and r[1] == 'available' and r[2] == good)

    # -------------------------------------------------------------- receipts
    def read_receipts(self, obs):
        for receipt in obs.get('command_receipts', ()):
            tag = self.issued.pop(str(receipt.get('id')), None)
            if tag is None:
                continue
            if receipt.get('status') == 'REJECTED':
                self.failed[tag] = self.failed.get(tag, 0) + 1
                self.pending.pop(tag, None)

    # ------------------------------------------------------------ duty EMA
    def update_duty(self, obs):
        for line in obs.get('lines', ()):
            rec = self.recipes.get(line.get('recipe'))
            if rec is None:
                continue
            lid = line['id']
            snap = (tuple(sorted((line.get('input_credit_nmU') or {}).items())),
                    tuple(sorted((line.get('output_accum_nmU') or {}).items())))
            moved = self.trace.get(lid) is not None and snap != self.trace[lid]
            running = line.get('power_received_mU', 0) > 0 or moved
            if line.get('paused') or line.get('under_construction'):
                running = False
            self.trace[lid] = snap
            old = self.duty.get(lid, Fraction(1))
            self.duty[lid] = old * DUTY_DECAY + (1 - DUTY_DECAY) * (1 if running else 0)

    # ---------------------------------------------------------------- demand
    def demand(self, obs, extra):
        need = {}

        def add(hub, good, q):
            need[(hub, good)] = need.get((hub, good), 0) + q

        for hub in obs.get('hubs', ()):
            pop = hub.get('population_mU', 0)
            if pop:
                for good, rate in BASKET.items():
                    add(hub['id'], good, pop * rate)
        for line in obs.get('lines', ()):
            rid = line.get('recipe')
            rec = self.recipes.get(rid)
            if rec is None or line.get('under_construction'):
                continue
            hub = line['hub_id']
            if rec['facility'] == '电站':
                continue
            if rid in LAB_RECIPES:
                for good, q in rec['inputs'].items():
                    add(hub, good, ratio(q) * 1000)
                continue
            # downstream pull weighted by how much that line actually runs
            share = max(Fraction(1, 5), self.duty.get(line['id'], Fraction(1)))
            for good, q in rec['inputs'].items():
                add(hub, good, ratio(q) * 1000 * share)
        for (hub, good), q in extra.items():
            add(hub, good, q)
        return need

    # ------------------------------------------------------------- research
    def research(self, obs, out, extra, floors):
        research = obs.get('research', {})
        known = set(research.get('known', ()))
        projects = research.get('projects', {})
        focus = research.get('focus', {})
        active = [j for j in research.get('validation', ()) if j.get('active')]
        validating = {j['tech_id'] for j in active}
        busy = {j['lab_id'] for j in active}
        labs = [l for l in obs.get('lines', ()) if l.get('recipe') in LAB_RECIPES
                and not l.get('under_construction') and not l.get('work_locked')]
        lab_ids = {l['id'] for l in labs}
        claimed = {}
        for lid, t in focus.items():
            if lid in lab_ids and lid not in busy:
                claimed[t] = claimed.get(t, 0) + 1
        # concentrate labs on critical-path tech (n^0.75 combined) so key unlocks (R01/L01/A04/V02/F05) land early
        per_tech = max(2, min(4, len(labs)))
        awaiting = sorted(t for t, p in projects.items() if p.get('theory_complete') and not p.get('verified')
                          and t not in validating and t not in known)
        # validation goods demand (pull production and reserve in world pool)
        for t in awaiting + sorted(validating):
            spec = self.tiers.get(self.nodes[t]['tier'])
            if spec:
                vg = spec['validation_good']
                va = spec['validation_amount'] * 1000
                key = (self.capital, vg)
                extra[key] = extra.get(key, 0) + va // 2
                floors[key] = max(floors.get(key, 0), va + (POOL_SAFETY['lab_supplies'] if vg == 'lab_supplies' else 0))
        free = [l for l in labs if l['id'] not in busy]
        for line in free:
            if not awaiting:
                break
            tid = awaiting[0]
            spec = self.tiers[self.nodes[tid]['tier']]
            have = self.stock(obs, line['hub_id'], spec['validation_good'])
            if have >= spec['validation_amount'] * 1000:
                out.append(self.cmd('research', 'validate_project', line_id=line['id'], tech_id=tid))
                awaiting.pop(0)
                busy.add(line['id'])
                prev = focus.get(line['id'])
                if prev in claimed:
                    claimed[prev] -= 1
        for line in free:
            lid = line['id']
            if lid in busy:
                continue
            current = focus.get(lid)
            project = projects.get(current, {}) if current else {}
            fresh = project.get('theory_mU', 0) * 10 < project.get('theory_target_mU', 1)
            off_route = current not in self.route[:max(2, len(labs) + 1)]
            if current and current not in known and not project.get('theory_complete') \
                    and not (fresh and off_route and obs.get('window', 0) < 4):
                continue
            choice = None
            for tid in self.route:
                if claimed.get(tid, 0) < per_tech and self.researchable(tid, known) and not projects.get(tid, {}).get('theory_complete'):
                    choice = tid
                    break
            if choice is None:
                options = sorted((self.nodes[t]['tier'], t) for t in self.nodes
                                 if claimed.get(t, 0) < per_tech and self.researchable(t, known)
                                 and not projects.get(t, {}).get('theory_complete'))
                choice = options[0][1] if options else None
            if choice and choice != current:
                claimed[choice] = claimed.get(choice, 0) + 1
                sec = 'classified' if (self.hh_cash < 15000000 and self.treasury > 50000000) else 'open'
                out.append(self.cmd('research', 'set_focus', line_id=lid, tech_id=choice, sec_mode=sec))
        # tier-2 validation needs engineering packs: make sure a pack line exists
        upcoming = any(self.nodes[t]['tier'] == 2 for t in set(focus.values()) | set(awaiting) if t in self.nodes)
        return known, upcoming

    # ----------------------------------------------------------------- lines
    def lines(self, obs, need, floors, out, use=None):
        known = set(obs.get('research', {}).get('known', ()))
        weights = self.weights
        for line in obs.get('lines', ()):
            rid = line.get('recipe')
            rec = self.recipes.get(rid)
            if rec is None or line.get('under_construction') or line.get('work_locked'):
                continue
            # r7+: the opening P033 line runs without A04, but re-setting it is tech-locked
            if not set(rec.get('requires_all') or ()) <= known:
                continue
            if rec['facility'] in ('电站', '实验室'):
                continue
            hub = line['hub_id']
            target = 0
            for good in rec['outputs']:
                cycles = LIFE_BUFFER.get(good, BUFFER)
                target = max(target, int(need.get((hub, good), 0) * cycles), floors.get((hub, good), 0))
                if good in STOCK_FLOOR and hub == self.capital:
                    target = max(target, STOCK_FLOOR[good])
            if use is not None and hub == self.capital and weights:
                value = self.net_value(rid)
                if value > 0 and self.sustainable(obs, rec):
                    # positive value added at eta = 1 net of power and pollution: keep running,
                    # the pool takes the surplus
                    target = max(target, max(POOL_HOLD * max(1000, int(use.get(g, 0))) for g in rec['outputs'])
                                 + RUN_HEADROOM)
                elif self.emissions.get(rid) and self.poolable(rec):
                    # polluting mill: the world pool supplies its output instead
                    target = 0
            current = (line.get('options') or {}).get('target_stock_mU')
            if current is None or abs(current - target) > max(1500, target // 4):
                out.append(self.cmd('industry', 'set_line', line_id=line['id'], recipe=rid,
                                    mode=line.get('mode') or 'general', target_stock_mU=target))

    # ----------------------------------------------------------------- power
    def power(self, obs, out):
        w = obs['window']
        cycle = obs.get('cycle', w // 2)
        plants = sorted(
            (l for l in obs.get('lines', ()) if l.get('plant') and l.get('recipe') == 'P070'
             and not l.get('under_construction') and not l.get('work_locked')),
            key=lambda l: l['id'],
        )
        if 'plant_shifts' not in self.done and plants:
            self.done.add('plant_shifts')
            shifts = (20, 12, 4, -8, -16)
            for idx, line in enumerate(plants):
                shift = shifts[idx % len(shifts)]
                if shift != 0 and line['plant'].get('maintenance_shift_cycles', 0) != shift:
                    out.append(self.cmd('logistics', 'plant_maintenance',
                                        facility_id=line['id'], shift_cycles=shift))
        if w < 4 or len(plants) < 2:
            return

        def due_soon(line, horizon=1):
            p = line['plant']
            nxt = p.get('next_maintenance_cycle')
            if nxt is None:
                return False
            due = nxt + p.get('maintenance_shift_cycles', 0)
            return 0 <= due - cycle <= horizon

        online = [l for l in plants if l['plant'].get('online')]
        offline = [
            l for l in plants
            if not l['plant'].get('online')
            and l['plant'].get('mode') == 'OFF'
            and not due_soon(l, 3)
        ]
        healthy = [
            l for l in online
            if l['plant'].get('mode') in ('ON', 'STARTING') and not due_soon(l, 1)
        ]
        running = [l for l in online if l['plant'].get('mode') == 'ON']
        gen = sum(l['plant'].get('generation_mU', 0) for l in online)
        healthy_cap = PLANT_CAP['P070'] * len(healthy)
        need_backup = (len(healthy) < 2 or gen > healthy_cap * Fraction(4, 5))
        if need_backup and offline and (w - self.plant_window >= 2 or len(healthy) < len(online)):
            out.append(self.cmd('industry', 'plant_toggle', facility_id=offline[0]['id'], online=True))
            self.plant_window = w
            return
        if w - self.plant_window < 6 or len(running) < len(online) or any(due_soon(l, 2) for l in online):
            return
        if len(online) > 2 and gen < (len(online) - 1) * PLANT_CAP['P070'] * Fraction(11, 20):
            victim = sorted(online, key=lambda l: l['id'])[-1]
            out.append(self.cmd('industry', 'plant_toggle', facility_id=victim['id'], online=False))
            self.plant_window = w

    # ------------------------------------------------------------ building
    def build(self, obs, out, known, upcoming, floors):
        """Investment: one plan item at a time, materials net of running jobs and reserves."""
        hub = self.capital
        w = obs['window']
        facilities = [f for f in obs.get('facilities', ()) if f['hub_id'] == hub]
        houses = sum(1 for f in facilities if f['facility_type'] == 'housing' and f.get('commissioned'))
        occupied = sum(1 for f in facilities if f['facility_type'] != 'housing')
        slots = 20 * (10 + houses) // 10 - occupied
        hubrow = next((h for h in obs.get('hubs', ()) if h['id'] == hub), {})
        engineers = hubrow.get('skills_mU', {}).get('engineer', 0)
        labs = [f for f in facilities if f['facility_type'] == '实验室']
        idle_lines = [l for l in obs.get('lines', ()) if l.get('recipe') is None and l['hub_id'] == hub
                      and not l.get('under_construction') and not l.get('work_locked')]
        fac_type = {f['id']: f['facility_type'] for f in facilities}
        jobs = {j['id']: j for j in obs.get('work', {}).get('jobs', ())}
        for cid, job in jobs.items():
            if cid in self.ordered:
                self.fresh[job.get('target_id')] = self.ordered.pop(cid)
        for line in idle_lines:
            fid = line['id'].rsplit('/line', 1)[0]
            kind = fac_type.get(fid)
            recipe = self.fresh.get(fid) or {'实验室': 'P075', '机械厂': 'P063', '制药厂': 'P033', '化工厂': 'P105',
                                             '材料厂': 'P062', '食品厂': 'P031', '农场': 'P028', '轻工厂': 'P032',
                                             '装备厂': 'P044'}.get(kind)
            if recipe and not set(self.recipes[recipe]['requires_all']) <= known:
                continue
            if recipe and self.failed.get(('line', line['id']), 0) < 3 and \
                    self.pending.get(('line', line['id']), -99) <= w - 6:
                self.pending[('line', line['id'])] = w
                out.append(self.cmd('industry', 'set_line', tag=('line', line['id']), line_id=line['id'],
                                    recipe=recipe, mode='general'))
        # materials still owed to running jobs this bot started
        committed = {}
        for cid in list(self.costs):
            job = jobs.get(cid)
            if job is None:
                if w - self.costs[cid][1] > 4:
                    del self.costs[cid]
                    continue
                left = Fraction(1)
            else:
                left = 1 - Fraction(job['elapsed_ticks'], max(1, job['duration_ticks']))
            for g, q in self.costs[cid][0].items():
                committed[g] = committed.get(g, 0) + q * left
        have = {g: self.stock(obs, hub, g) for g in ('steel', 'cement', 'basic_parts')}
        free = {g: have[g] - committed.get(g, 0) - RESERVE[g] for g in have}

        def floor_for(cost):
            for g in RESERVE:
                floors[(hub, g)] = max(floors.get((hub, g), 0),
                                       int(RESERVE[g] + committed.get(g, 0) + cost.get(g, 0)))

        def start(tag, cost, op, **fields):
            row = self.cmd('industry', op, tag=tag, **fields)
            self.costs[row['id']] = (dict(cost), w)
            self.pending[tag] = w
            for g, q in cost.items():
                committed[g] = committed.get(g, 0) + q
                free[g] -= q
            out.append(row)

        plan = []
        pop_now = hubrow.get('population_mU', 0)
        count = {}
        for l in obs.get('lines', ()):
            if l['hub_id'] == hub and l.get('recipe'):
                count[l['recipe']] = count.get(l['recipe'], 0) + 1
        for f in facilities:
            if not f.get('commissioned'):
                rec = self.fresh.get(f['id']) or {'食品厂': 'P031', '农场': 'P028', '轻工厂': 'P032',
                                                  '制药厂': 'P033', '装备厂': 'P044'}.get(f['facility_type'])
                if rec:
                    count[rec] = count.get(rec, 0) + 1
        for line in obs.get('lines', ()):
            if line['hub_id'] != hub or line.get('recipe') is not None:
                continue
            rec = self.fresh.get(line['id'].rsplit('/line', 1)[0])
            if rec:
                count[rec] = count.get(rec, 0) + 1
        headroom = Fraction(51, 50)
        grain_short = self.stock(obs, hub, 'grain') < 3000 * max(1, count.get('P031', 0)) // 2 and \
            self.stock(obs, hub, 'food') < pop_now * BASKET['food'] * 4 and \
            not any(f['facility_type'] == '农场' and not f.get('commissioned') for f in facilities)
        if grain_short or count.get('P031', 0) * 3000 * 10 > count.get('P028', 0) * 4000 * 9:
            plan.append(('farm', S1, {'facility_type': '农场', 'profile_id': 'S1'}, 'P028'))
        elif pop_now * BASKET['food'] * headroom > count.get('P031', 0) * 3000:
            plan.append(('food', S1, {'facility_type': '食品厂', 'profile_id': 'S1'}, 'P031'))
        if pop_now * BASKET['consumer_goods'] * headroom > count.get('P032', 0) * 3000:
            plan.append(('goods', S1, {'facility_type': '轻工厂', 'profile_id': 'S1'}, 'P032'))
        if count.get('P005', 0) < 2 and not self.poolable(self.recipes['P005']):
            plan.append(('cement', S1, {'facility_type': '材料厂', 'profile_id': 'S1'}, 'P005'))
        if count.get('P011', 0) < 2:
            plan.append(('parts', S1, {'facility_type': '机械厂', 'profile_id': 'S1'}, 'P011'))
        if len(labs) < min(LAB_CAP, engineers // 1000):
            supply_lines = count.get('P062', 0)
            if len(labs) >= 4 * supply_lines:
                plan.append(('supplies', S1, {'facility_type': '材料厂', 'profile_id': 'S1'}, 'P062'))
            else:
                plan.append(('lab', S1, {'facility_type': '实验室', 'profile_id': 'S1'}, 'P075'))
        has_academy = any(x.get('kind') == 'academy' for x in obs.get('systems', ()))
        free_public = [l for l in obs.get('lines', ()) if l['hub_id'] == hub and l.get('slot_use') == 'unassigned'
                       and fac_type.get(l['id'].rsplit('/line', 1)[0]) == 'public']
        if 'R02' in known and not has_academy and not free_public and \
                not any(f['facility_type'] == 'public' and not f.get('commissioned') for f in facilities):
            plan.append(('public', S1, {'facility_type': 'public', 'profile_id': 'S1'}, None))
        pack_lines = [l for l in obs.get('lines', ()) if l.get('recipe') == 'P063']
        if upcoming and not pack_lines and not any(f['facility_type'] == '机械厂' and not f.get('commissioned')
                                                   for f in facilities):
            plan.append(('packs', S1, {'facility_type': '机械厂', 'profile_id': 'S1'}, 'P063'))
        pharma = [f for f in facilities if f['facility_type'] == '制药厂']
        if {'A04', 'L01'} <= known and (not pharma or (all(f.get('commissioned') for f in pharma) and
                                                       pop_now * BASKET['medicine'] * headroom > count.get('P033', 0) * 2000)):
            plan.append(('pharma', S2, {'facility_type': '制药厂', 'profile_id': 'S2'}, 'P033'))
        chem2 = [f for f in facilities if f['facility_type'] == '化工厂' and f.get('profile_id') != 'S1']
        if {'V02', 'L01'} <= known and not chem2:
            plan.append(('remedy', S2, {'facility_type': '化工厂', 'profile_id': 'S2'}, 'P105'))
        ammo_stock = self.stock(obs, hub, 'ammunition')
        if count.get('P044', 0) < 1 and (ammo_stock < 3000 or self.threat) and \
                not any(f['facility_type'] == '装备厂' and not f.get('commissioned') for f in facilities):
            plan.append(('equip', S1, {'facility_type': '装备厂', 'profile_id': 'S1'}, 'P044'))
        # storage first: prevent warehouse overflow blocking production output
        load = sum(r[3] for r in obs.get('inventory', ()) if len(r) == 4 and r[0] == hub and r[1] != 'outdoor')
        room = hubrow.get('warehouse_mU', 0)
        if load * 100 > room * 70 and not any(j.get('kind') == 'warehouse_expand' for j in jobs.values()):
            modules = max(1, min(4, int(load * 100 - room * 70) // 25000000 + 1))
            cost = {g: q * modules for g, q in WAREHOUSE.items()}
            if self.pending.get('warehouse', -99) <= w - 4 and all(have[g] - committed.get(g, 0) >= q
                                                                    for g, q in cost.items()):
                start('warehouse', cost, 'warehouse_expand', hub_id=hub, modules=modules)
                floor_for({})
                return
            if load * 100 > room * 80:
                floor_for(cost)
                return
        pop, housing = hubrow.get('population_mU', 0), hubrow.get('housing_mU', 0)
        building_house = any(f['facility_type'] == 'housing' and not f.get('commissioned') for f in facilities)
        need_house = (slots <= 1 or pop > housing * 8 // 10) and not building_house
        for tag, cost, fields, recipe in plan:
            if self.failed.get(tag, 0) >= 3:
                continue
            if self.pending.get(tag, -99) > w - 6:
                floor_for(cost)
                return
            if slots <= 0:
                if building_house:
                    floor_for({})
                    return
                need_house = True
                break
            if all(free[g] >= q for g, q in cost.items()):
                start(tag, cost, 'build_facility', hub_id=hub, **fields)
                self.ordered[out[-1]['id']] = recipe
                floor_for({})
                return
            floor_for(cost)
            return
        if need_house and self.failed.get('house', 0) < 3:
            if self.pending.get('house', -99) <= w - 6 and all(free[g] >= q for g, q in HOUSE.items()):
                start('house', HOUSE, 'build_facility', hub_id=hub, facility_type='public', profile_id='P110')
                floor_for({})
            else:
                floor_for(HOUSE)
            return
        floor_for({})

    # ------------------------------------------------------------ world pool
    def national_use(self, obs, need):
        use = {}
        for (h, g), q in need.items():
            use[g] = use.get(g, 0) + q
        for line in obs.get('lines', ()):
            plant = line.get('plant')
            rec = self.recipes.get(line.get('recipe'))
            if not plant or rec is None or not plant.get('online'):
                continue
            out_q = sum(ratio(q) for q in rec['outputs'].values()) or 1
            for g, q in rec['inputs'].items():
                use[g] = use.get(g, 0) + plant.get('generation_mU', 0) * 10 * ratio(q) / out_q
        return use

    def eta_use(self, obs, extra):
        """Per-cycle national use as the engine's eta denominator sees it."""
        use = {}
        for hub in obs.get('hubs', ()):
            pop = hub.get('population_mU', 0)
            for good, rate in BASKET.items():
                use[good] = use.get(good, 0) + pop * rate
        for line in obs.get('lines', ()):
            rec = self.recipes.get(line.get('recipe'))
            if rec is None or line.get('under_construction') or line.get('plant'):
                continue
            share = self.duty.get(line['id'], Fraction(1)) * Fraction(line.get('load_mU', 1000), 1000)
            for g, q in rec['inputs'].items():
                use[g] = use.get(g, 0) + ratio(q) * 1000 * share
        for (h, g), q in extra.items():
            use[g] = use.get(g, 0) + q
        for g, q in self.national_use(obs, {}).items():
            use[g] = use.get(g, 0) + q
        return use

    def mines(self, obs, use, out):
        """r9: pause surplus mineral mines so unused ores do not burn power/coal or deplete deposits."""
        hub = self.capital
        hubrow = next((h for h in obs.get('hubs', ()) if h['id'] == hub), {})
        room = hubrow.get('warehouse_mU', 0)
        load = sum(r[3] for r in obs.get('inventory', ()) if len(r) == 4 and r[0] == hub and r[1] != 'outdoor')
        crowded = room > 0 and load * 100 > room * 75
        for mine in obs.get('mines', ()):
            if mine.get('under_construction') or mine.get('work_locked'):
                continue
            good = mine.get('good')
            if good == 'water':
                continue
            m_hub = mine.get('hub_id')
            online = mine.get('online', True)
            have = self.stock(obs, m_hub, good)
            if m_hub == hub:
                cycle_use = int(use.get(good, 0))
                high = max(120000 if good == 'coal' else 48000, (6 if not crowded else 3) * max(2000, cycle_use))
                low = max(70000 if good == 'coal' else 24000, 3 * max(2000, cycle_use))
                if online and have > high:
                    out.append(self.cmd('industry', 'plant_toggle', facility_id=mine['id'], online=False))
                elif not online and have < low and not crowded:
                    out.append(self.cmd('industry', 'plant_toggle', facility_id=mine['id'], online=True))
            else:
                if online and have > 30000:
                    out.append(self.cmd('industry', 'plant_toggle', facility_id=mine['id'], online=False))
                elif not online and have < 10000:
                    out.append(self.cmd('industry', 'plant_toggle', facility_id=mine['id'], online=True))

    def pool(self, obs, need, floors, out, extra=None):
        """r9 world-pool trade: buy at 11.5x only for life/floor shortfalls; sell genuine excess at 8.5x."""
        cycle = obs.get('cycle')
        if cycle == self.pool_cycle:
            return
        self.pool_cycle = cycle
        hub = self.capital
        physical, avail = {}, {}
        for row in obs.get('inventory', ()):
            if len(row) != 4:
                continue
            physical[row[2]] = physical.get(row[2], 0) + row[3]
            if row[0] == hub and row[1] == 'available':
                avail[row[2]] = avail.get(row[2], 0) + row[3]
        use = self.national_use(obs, need)
        sink = self.eta_use(obs, extra or {})
        ru = sum(r[2] for r in obs.get('cash_accounts', ()) if r[0] == 'reserves' and r[1] == 'RU')
        treasury = sum(r[2] for r in obs.get('cash_accounts', ()) if r[0] == 'treasury' and r[1] == self.owner)
        # Keep treasury liquid (never drain treasury below 25M for FX)
        if cycle and ru < 15000000 and treasury > 35000000:
            amt = min(15000000, treasury - 25000000)
            out.append(self.cmd('finance', 'fx', side='buy_ru', currency=self.owner, amount_mU=amt))
        self.ru = ru
        pool = obs.get('world_pool', {})
        prices = pool.get('price_ru_mU', {})
        buy_prices = pool.get('buy_price_ru_mU') or prices
        stock = pool.get('stock_mU', {})
        self.pool_stock = dict(stock)
        used = {(g, side): q for g, side, q in pool.get('used_mU', ())}
        buys = []
        floor_goods = tuple(sorted({g for (h, g) in floors if h == hub}))
        buy_list = list(dict.fromkeys(POOL_BUY + LIFE_GOODS + floor_goods))
        for g in buy_list:
            if g == 'medical_supplies' and not self.med_supplies_ok():
                continue
            want = floors.get((hub, g), 0)
            if g in LIFE_GOODS:
                want = max(want, POOL_SAFETY.get(g, 0), 3 * int(use.get(g, 0)))
            elif g in POOL_BUY and stock.get(g, 0) >= POOL_MIN_STOCK and self.hh_pool_ok():
                want = max(want, 2 * int(use.get(g, 0)))
            short = want - avail.get(g, 0)
            if short <= 0 or g not in buy_prices:
                continue
            qty = min(POOL_CAP - used.get((g, 'buy'), 0), -(-short // 1000) * 1000, stock.get(g, 0) // 2)
            if g == 'medicine' and ru < MEDICINE_RATION_RU:
                qty = min(qty, max(1000, int(use.get(g, 0)) // 2))
            qty = qty // 1000 * 1000
            cost = (qty * buy_prices[g] + 999) // 1000
            budget = ru if g in LIFE_GOODS else ru - POOL_MIN_RU
            if qty > 0 and cost <= budget:
                ru -= cost
                buys.append(g)
                out.append(self.cmd('trade', 'pool_buy', hub_id=hub, good=g, qty_mU=qty))
        for g in sorted(avail):
            if g in NEVER_SELL or g in buys or g not in prices:
                continue
            # Keep untouched non-mined opening buffers so we do not dump scarce intermediates at 8.5x.
            min_keep = 48000 if g in ('steel', 'cement') else 24000
            keep = 0 if g == 'medical_supplies' and not self.med_supplies_ok() else max(
                min_keep, POOL_HOLD * max(1000, int(sink.get(g, 0))), POOL_SAFETY.get(g, 0), floors.get((hub, g), 0))
            excess = min(avail[g], physical.get(g, 0) - keep, POOL_CAP - used.get((g, 'sell'), 0))
            if excess >= 2000:
                out.append(self.cmd('trade', 'pool_sell', hub_id=hub, good=g, qty_mU=excess // 1000 * 1000))

    def remediate(self, obs, out, known, extra):
        if 'V02' not in known:
            return
        hub = self.capital
        hubrow = next((h for h in obs.get('hubs', ()) if h['id'] == hub), {})
        extra[(hub, 'remediation_agent')] = extra.get((hub, 'remediation_agent'), 0) + 1000
        if hubrow.get('pollution_mU', 0) > 2000 and self.stock(obs, hub, 'remediation_agent') >= 1000:
            out.append(self.cmd('industry', 'remediate_pollution', region_id=hubrow['region_id'], amount_mU=1000))

    # ------------------------------------------------------------ governance
    def governance(self, obs, out, known):
        # 1. Closed-loop monetary circulation: 100% corporate dividend payout back to treasury.
        payouts = (obs.get('corporate_finance') or {}).get('payout_policies', {})
        for cid in sorted(payouts):
            if payouts[cid] < 1000 and ('div', cid) not in self.done:
                self.done.add(('div', cid))
                out.append(self.cmd('finance', 'dividend', tag=('div', cid), company=cid, payout_mU=1000))

        # 2. Adaptive fiscal policy: keep 5% tax for household health & max support,
        #    gently raise only if treasury dips low, and turn on F05 welfare when unlocked.
        want_welfare = 10 ** 9 if 'F05' in known else 0
        if self._tax_bp is None:
            want_tax = 500
        elif self.treasury < 20000000 and self.hh_cash > 15000000:
            want_tax = 1000
        elif self.treasury > 40000000 or self.hh_cash < 10000000:
            want_tax = 500
        else:
            want_tax = self._tax_bp
        if want_tax != self._tax_bp or (want_welfare > 0) != self._welfare_set:
            self._tax_bp = want_tax
            self._welfare_set = want_welfare > 0
            out.append(self.cmd('governance', 'fiscal_policy', tax_rate_bp=want_tax,
                                welfare_budget_mU=want_welfare, maint_budget_mU=10 ** 12))

        if 'F01' in known and 'ipo' not in self.done:
            self.done.add('ipo')
            for sector in ('mining', 'manufacturing'):
                out.append(self.cmd('finance', 'ipo', company=f'{self.owner}-{sector}', qty=1,
                                    min_price_mU=1000000000))

        # 3. Central bank (F02): low rate, gentle liquidity top-up only if treasury runs tight with low inflation.
        if 'F02' in known:
            macro = obs.get('macroeconomy') or {}
            inf_mU = macro.get('inflation_mU', 0)
            money_mU = macro.get('money_mU', 150000000)
            need_issue = 0
            if self.treasury < 25000000 and inf_mU < 15:
                need_issue = min(2000000, max(0, money_mU // 100))
            if 'cb' not in self.done or (need_issue > 0) != getattr(self, '_cb_issuing', False):
                self.done.add('cb')
                self._cb_issuing = need_issue > 0
                out.append(self.cmd('finance', 'central_bank', rate_bp=300, issue_mU=need_issue,
                                    intervene_ru_mU=0, peg=None, capital_control_bp=0))

        # 4. Adaptive autonomy: boost support early / under stress, zero out at max support so taxes stay in treasury.
        current = obs.get('governance', {}).get('autonomy_mU', {})
        for h in obs.get('hubs', ()):
            if h.get('population_mU', 0) <= 0:
                continue
            region = h['region_id']
            sup = h.get('support_mU', 1000)
            cur = current.get(region, 0)
            if sup < 850 and cur < 500:
                out.append(self.cmd('governance', 'autonomy', region_id=region, autonomy_mU=500))
            elif sup >= 950 and cur > 0:
                out.append(self.cmd('governance', 'autonomy', region_id=region, autonomy_mU=0))

    def academy(self, obs, out, known, extra):
        if 'R02' not in known:
            return
        hub = self.capital
        systems = [s for s in obs.get('systems', ()) if s.get('kind') == 'academy' and s.get('hub_id') == hub]
        w = obs['window']
        if not systems:
            extra[(hub, 'lab_supplies')] = extra.get((hub, 'lab_supplies'), 0) + 1000
            public = {f['id'] for f in obs.get('facilities', ()) if f['facility_type'] == 'public'}
            idle = [l for l in obs.get('lines', ()) if l['hub_id'] == hub and l.get('slot_use') == 'unassigned'
                    and not l.get('work_locked') and not l.get('under_construction')]
            free_slot = bool(idle) and all(l['id'].rsplit('/line', 1)[0] in public for l in idle)
            if free_slot and self.pending.get('academy', -99) <= w - 10 and self.failed.get('academy', 0) < 10 \
                    and self.stock(obs, hub, 'lab_supplies') >= 2000:
                out.append(self.cmd('industry', 'install_system', tag='academy', hub_id=hub, system_id='academy'))
                self.pending['academy'] = w
            return
        training = obs.get('governance', {}).get('training', ())
        if not training and self.pending.get('train', -99) <= w - 4 and self.failed.get('train', 0) < 6:
            hubrow = next((h for h in obs.get('hubs', ()) if h['id'] == hub), {})
            skilled = hubrow.get('skills_mU', {}).get('skilled_worker', 0)
            if skilled > 20000:
                out.append(self.cmd('governance', 'academy_train', tag='train', hub_id=hub,
                                    track='skilled_to_engineer', trainees_mU=5000))
                self.pending['train'] = w

    # ------------------------------------------------------------ multi-hub
    def subhub_eligible_minerals(self, subhub):
        hdata = self.hubs_data.get(subhub, {})
        reg = hdata.get('region', '')
        terr = self.terrain.get(reg, 'plain')
        if terr in ('mountain', 'hill'):
            eligible = ('iron_ore', 'copper_ore', 'rare_ore', 'stone', 'uranium_ore')
        else:
            eligible = ('bauxite', 'coal', 'natural_gas', 'crude_oil', 'quartz', 'biomass', 'water')
        preferred = [m for m in PRIORITY_MINERALS if m in eligible]
        return preferred or list(eligible)

    def expand_subhubs(self, obs, out, known, floors):
        w = obs.get('window', 0)
        if w < 40:
            return
        # Wait until capital research engine is established before diverting building materials to subhubs.
        cap_labs = sum(1 for f in obs.get('facilities', ())
                       if f['hub_id'] == self.capital and f['facility_type'] == '实验室')
        if cap_labs < 3:
            return
        jobs = {j['id']: j for j in obs.get('work', {}).get('jobs', ())}
        roads = {r['id']: r for r in obs.get('logistics', {}).get('roads', [])}
        cap_steel = self.stock(obs, self.capital, 'steel')
        cap_cem = self.stock(obs, self.capital, 'cement')
        cap_parts = self.stock(obs, self.capital, 'basic_parts')
        cap_stone = self.stock(obs, self.capital, 'stone')
        shipped_this_window = False

        # 1. Road corridor upgrades (when tech is ready and materials are ample)
        if {'L01', 'L02'} <= known:
            for sh, r in self.corridors_to_subhubs.items():
                rid = r['id']
                road_info = roads.get(rid, {})
                level = road_info.get('level', 1)
                if level == 1 and ('corridor_up', rid) not in self.done:
                    cells = r.get('cells', [])
                    if 2 <= len(cells) <= 512:
                        blocks = Fraction(len(cells) - 1, 10)
                        cost_steel = int(Fraction(3000) * blocks)
                        cost_cem = int(Fraction(6000) * blocks)
                        cost_stone = int(Fraction(4000) * blocks)
                        if cap_steel > cost_steel + 32000 and cap_cem > cost_cem + 36000 and cap_stone > cost_stone + 12000:
                            if self.pending.get(('corridor_up', rid), -99) <= w - 6:
                                out.append(self.cmd('logistics', 'build_corridor', tag=('corridor_up', rid),
                                                    origin_hub=self.capital, dest_hub=sh, mode='road', level=2,
                                                    path_cells=cells))
                                self.pending[('corridor_up', rid)] = w
                                self.done.add(('corridor_up', rid))
                                break

        # 2. Secondary hub resource development (at most 1 outbound material shipment per window)
        cap_row = next((h for h in obs.get('hubs', ()) if h['id'] == self.capital), {})
        pop_cap = cap_row.get('population_mU', 0)

        for sh in self.secondary_hubs:
            r = self.corridors_to_subhubs.get(sh)
            if not r:
                continue
            cid = r['id']
            sh_row = next((h for h in obs.get('hubs', ()) if h['id'] == sh), {})
            pop_sh = sh_row.get('population_mU', 0)
            sh_mines = [m for m in obs.get('mines', ()) if m.get('hub_id') == sh]

            # a) Relocate workers only if capital has healthy population (>150,000)
            if pop_sh < 3000 and pop_cap > 150000 and ('relocate', sh) not in self.done:
                if self.pending.get(('relocate', sh), -99) <= w - 6 and self.failed.get(('relocate', sh), 0) < 2:
                    out.append(self.cmd('governance', 'relocate_population', tag=('relocate', sh),
                                        origin_hub=self.capital, dest_hub=sh,
                                        skills_mU={'general_worker': 3000, 'skilled_worker': 0, 'engineer': 0},
                                        path=[cid], allow_unhoused=True))
                    self.pending[('relocate', sh)] = w
                    self.done.add(('relocate', sh))

            # b) Housing at subhub
            house_sh = sh_row.get('housing_mU', 0)
            have_steel = self.stock(obs, sh, 'steel')
            have_cem = self.stock(obs, sh, 'cement')
            if pop_sh > 0 and house_sh < 10000 and ('house', sh) not in self.done:
                if have_steel >= 5000 and have_cem >= 20000:
                    if self.pending.get(('house', sh), -99) <= w - 6:
                        out.append(self.cmd('industry', 'build_facility', tag=('house', sh), hub_id=sh,
                                            facility_type='public', profile_id='P110'))
                        self.pending[('house', sh)] = w
                        self.done.add(('house', sh))
                elif not shipped_this_window and self.pending.get(('ship_house', sh), -99) <= w - 6:
                    if have_steel < 5000 and cap_steel > 28000:
                        out.append(self.cmd('logistics', 'route_shipment', tag=('ship_house', sh),
                                            origin_hub=self.capital, dest_hub=sh, corridor_path=[cid],
                                            good='steel', qty_mU=min(8000, 5000 - have_steel),
                                            carrier_recipe='manual', repeat_cycles=0))
                        self.pending[('ship_house', sh)] = w
                        shipped_this_window = True
                    elif have_cem < 20000 and cap_cem > 32000:
                        out.append(self.cmd('logistics', 'route_shipment', tag=('ship_house', sh),
                                            origin_hub=self.capital, dest_hub=sh, corridor_path=[cid],
                                            good='cement', qty_mU=min(8000, 20000 - have_cem),
                                            carrier_recipe='manual', repeat_cycles=0))
                        self.pending[('ship_house', sh)] = w
                        shipped_this_window = True

            # c) Build G1 Mine at subhub
            pending_mine = any(j.get('hub_id') == sh and j.get('kind') in ('build_mine', 'build_facility')
                               for j in jobs.values())
            if not sh_mines and not pending_mine:
                candidates = self.subhub_eligible_minerals(sh)
                chosen = next((m for m in candidates if self.failed.get(('mine', sh, m), 0) < 3), None)
                if chosen:
                    have_parts = self.stock(obs, sh, 'basic_parts')
                    if have_steel >= 4000 and have_cem >= 4000 and have_parts >= 1000:
                        if self.pending.get(('mine', sh, chosen), -99) <= w - 6:
                            out.append(self.cmd('industry', 'build_facility', tag=('mine', sh, chosen),
                                                hub_id=sh, facility_type='public', profile_id='G1',
                                                good=chosen))
                            self.pending[('mine', sh, chosen)] = w
                    elif not shipped_this_window and self.pending.get(('ship_mine', sh), -99) <= w - 6:
                        if have_steel < 4000 and cap_steel > 28000:
                            out.append(self.cmd('logistics', 'route_shipment', tag=('ship_mine', sh),
                                                origin_hub=self.capital, dest_hub=sh, corridor_path=[cid],
                                                good='steel', qty_mU=4000 - have_steel,
                                                carrier_recipe='manual', repeat_cycles=0))
                            self.pending[('ship_mine', sh)] = w
                            shipped_this_window = True
                        elif have_cem < 4000 and cap_cem > 32000:
                            out.append(self.cmd('logistics', 'route_shipment', tag=('ship_mine', sh),
                                                origin_hub=self.capital, dest_hub=sh, corridor_path=[cid],
                                                good='cement', qty_mU=4000 - have_cem,
                                                carrier_recipe='manual', repeat_cycles=0))
                            self.pending[('ship_mine', sh)] = w
                            shipped_this_window = True
                        elif have_parts < 1000 and cap_parts > 10000:
                            out.append(self.cmd('logistics', 'route_shipment', tag=('ship_mine', sh),
                                                origin_hub=self.capital, dest_hub=sh, corridor_path=[cid],
                                                good='basic_parts', qty_mU=1000 - have_parts,
                                                carrier_recipe='manual', repeat_cycles=0))
                            self.pending[('ship_mine', sh)] = w
                            shipped_this_window = True

            # d) Funnel mined ores to capital (on-demand single shipments, no compounding repeat)
            for m in sh_mines:
                ore = m.get('good')
                if ore and ore != 'water':
                    ore_stock = self.stock(obs, sh, ore)
                    if ore_stock >= 2000 and self.pending.get(('funnel', sh, ore), -99) <= w - 4:
                        batch = min(4000, ore_stock // 1000 * 1000)
                        out.append(self.cmd('logistics', 'route_shipment', tag=('funnel', sh, ore),
                                            origin_hub=sh, dest_hub=self.capital, corridor_path=[cid],
                                            good=ore, qty_mU=batch, carrier_recipe='manual',
                                            repeat_cycles=0))
                        self.pending[('funnel', sh, ore)] = w

            # e) Deliver life supplies to populated subhubs
            if pop_sh > 0:
                for sup in ('food', 'water', 'medicine', 'consumer_goods'):
                    sup_stock = self.stock(obs, sh, sup)
                    cap_floor = 12000 if sup in ('medicine', 'consumer_goods') else 25000
                    if sup_stock < 2000 and self.stock(obs, self.capital, sup) > cap_floor:
                        if self.pending.get(('sup', sh, sup), -99) <= w - 6:
                            out.append(self.cmd('logistics', 'route_shipment', tag=('sup', sh, sup),
                                                origin_hub=self.capital, dest_hub=sh, corridor_path=[cid],
                                                good=sup, qty_mU=2000, carrier_recipe='manual',
                                                repeat_cycles=0))
                            self.pending[('sup', sh, sup)] = w

    # ------------------------------------------------------------ defense
    def defense(self, obs, out, known):
        w = obs.get('window', 0)
        pairs = obs.get('diplomacy', {}).get('pairs', [])
        is_war = any(p.get('state') == 'war' for p in pairs)
        tracks = obs.get('military_contacts', {}).get('tracks', [])
        threat_contacts = [t for t in tracks if t.get('currently_visible') and t.get('quality_mU', 0) > 150]
        incursions = bool(obs.get('military_control', {}).get('control_changes', []))
        self.threat = is_war or incursions or len(threat_contacts) > 0

        if self.threat and not self.mobilized:
            out.append(self.cmd('governance', 'mobilize', enabled=True))
            self.mobilized = True
            self.mobilized_tick = obs.get('tick', 0)
        elif not self.threat and self.mobilized and obs.get('tick', 0) >= self.mobilized_tick + 40:
            out.append(self.cmd('governance', 'mobilize', enabled=False))
            self.mobilized = False

        formations = obs.get('formations', [])
        hubrow = next((h for h in obs.get('hubs', ()) if h['id'] == self.capital), {})
        cap_region = hubrow.get('region_id')
        if not cap_region:
            return

        for idx, f in enumerate(formations):
            current_reg = f.get('region_id')
            current_task = f.get('task')
            fid = f.get('id')
            fver = f.get('version', 0)
            if not current_reg:
                continue
            # Garrison in place (or respond to visible threat in our theater) without issuing invalid remote targets.
            target_reg = current_reg
            if self.threat and threat_contacts and idx > 0:
                contact_reg = threat_contacts[0].get('region_id')
                if contact_reg:
                    target_reg = contact_reg

            if current_task != 'garrison' or (self.threat and current_reg != target_reg):
                tag = ('garrison', fid)
                if self.pending.get(tag, -99) <= w - 4 and self.failed.get(tag, 0) < 4:
                    out.append(self.cmd('military', 'formation_task', tag=tag,
                                        formation_id=fid, task='garrison', target_region=target_reg,
                                        posture='preserve', supply_hub=self.capital,
                                        retreat_loss_mU=500, expected_version=fver))
                    self.pending[tag] = w

    # ---------------------------------------------------------------- decide
    def decide(self, obs):
        out = []
        self.read_receipts(obs)
        self._cycle = obs.get('cycle', 0)
        self.ru = sum(r[2] for r in obs.get('cash_accounts', ()) if r[0] == 'reserves' and r[1] == 'RU')
        self.treasury = sum(r[2] for r in obs.get('cash_accounts', ()) if r[0] == 'treasury' and r[1] == self.owner)
        self.hh_cash = sum(h.get('household_cash_mU', 0) for h in obs.get('hubs', ()) if h.get('id') == self.capital)
        self.pool_stock = dict((obs.get('world_pool') or {}).get('stock_mU', {}))
        extra, floors = {}, {}
        known, upcoming = self.research(obs, out, extra, floors)
        if obs.get('window', 0) == 1 and self.med_supplies_ok() and self.ru >= 2000000 and self.stock(obs, self.capital, 'medical_supplies') < 1000:
            out.append(self.cmd('trade', 'pool_buy', hub_id=self.capital, good='medical_supplies', qty_mU=2000))
        if obs.get('window', 0) % 2 == 0:
            self.update_duty(obs)
            self.governance(obs, out, known)
            self.build(obs, out, known, upcoming, floors)
            self.academy(obs, out, known, extra)
            self.remediate(obs, out, known, extra)
            self.power(obs, out)
            need = self.demand(obs, extra)
            nat_use = self.national_use(obs, need)
            self.mines(obs, nat_use, out)
            self.lines(obs, need, floors, out, nat_use)
            self.pool(obs, need, floors, out, extra)
            self.expand_subhubs(obs, out, known, floors)
            self.defense(obs, out, known)
        return out[:200]


def obs_cycle_zero(bot):
    return getattr(bot, '_cycle', 0) == 0
