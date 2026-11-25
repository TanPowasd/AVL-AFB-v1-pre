"""裂陆纪元 中等 bot（Medium，公开给选手的练习对手）。

独立实现，不导入也不复制 advanced/super 的代码。策略画像：“广度学者 + 库存上限”。
只用 Python 标准库，只读 init 目录与本国过滤观测。

1. 研究：广度优先。开局那座基础实验室永远研究当前可研究的最低阶科技（同阶按编号），
   standard 保密档；理论完成且验证品够就验证。不新建实验室。
2. 生产：粗粒度“库存上限”。每 10 个决策窗口按（户需 + 现有产线满负荷投入）估一次
   每周期需求，给每条非电站、非实验室产线设 target_stock_mU = 8 周期需求；
   本地库存高于上限的产线自动停产（BASE 持续计划），避免把投入烧成卖不掉的库存。
3. 民生补短：首都食品低于下限时在本地市场挂限价买单（与 SimpleBot 同规则）。
4. 仓容与民生扩产（rift-medium-2，修 rift-medium-1 长局崩盘）：
   - 矿井不受产线目标库存控制，煤/矿石/石料每 tick 都进首都公共仓库（开局已占约 55%）；仓库满了以后
     食品厂、轻工厂的产出也放不进去，rift-medium-1 在 tick 450–650 食品与民用品库存归零 → 饥荒、
     支持度崩、可持续性 φ 掉到 0.45。现在首都仓库占用超过 75% 就扩建仓容（一次 2 模块）。
   - 开局食品厂/轻工厂满负荷产能只比开局户需多 0%–5%，人口一涨就供不上：户需超过产能 95% 时补一座
     S1 食品厂/轻工厂（农场跟着食品厂按粮食缺口补）；槽位不够先建住宅区。一次只进行一项建设，
     新厂空产线装上对应配方。
   - 钢材/水泥/机件给一个最低库存（维护与建设用），不让“库存上限”把它们压到 0。
5. 不调税、不管电站、不碰金融/外交/军事。
"""

from fractions import Fraction

NAME = 'medium'
VERSION = 'rift-medium-3'

HOUSEHOLD = {'food': Fraction(6, 100), 'water': Fraction(4, 100), 'medicine': Fraction(1, 100),
             'consumer_goods': Fraction(3, 100)}
CAP_CYCLES = 8
REPLAN_WINDOWS = 10
FOOD_FLOOR_MU = 400000
# per-cycle full-load output of one S1 line (mU): food/consumer goods 3 per batch, grain 4 (x fertility)
LINE_OUTPUT = {'P031': 3000, 'P032': 3000, 'P028': 4000}
FLOORS = {'steel': 20000, 'cement': 24000, 'basic_parts': 10000}
S1_COST = {'steel': 6000, 'cement': 6000, 'basic_parts': 2000}
HOUSE_COST = {'steel': 5000, 'cement': 20000}
WAREHOUSE_COST = {'steel': 4000, 'cement': 6000}
NEW_LINE_RECIPE = {'食品厂': 'P031', '农场': 'P028', '轻工厂': 'P032'}


def as_fraction(value):
    if isinstance(value, dict) and '$rational' in value:
        return Fraction(*value['$rational'])
    return Fraction(value)


class Bot:
    def __init__(self, init):
        self.owner = init['owner']
        catalog = init.get('catalog', {})
        self.recipes = {r['id']: r for r in catalog.get('industry', {}).get('recipes', ())}
        technology = catalog.get('technology', {})
        self.nodes = {r['id']: r for r in technology.get('technologies', ())}
        self.tiers = {r['tier']: r for r in technology.get('research_tiers', ())}
        self.seq = 0

    def _order(self, kind, op, **fields):
        self.seq += 1
        return {'id': str(self.seq), 'kind': kind, 'op': op} | fields

    def _open(self, tid, known):
        row = self.nodes[tid]
        need_all = set(row.get('requires_all') or ())
        need_any = set(row.get('requires_any') or ())
        return tid not in known and need_all <= known and (not need_any or bool(need_any & known))

    def _research(self, obs):
        orders = []
        research = obs.get('research', {})
        known = set(research.get('known', ()))
        projects = research.get('projects', {})
        focus = research.get('focus', {})
        jobs = [j for j in research.get('validation', ()) if j.get('active')]
        busy = {j['lab_id'] for j in jobs}
        running = {j['tech_id'] for j in jobs}
        stock = {}
        for row in obs.get('inventory', ()):
            if len(row) == 4 and row[1] == 'available':
                stock[(row[0], row[2])] = stock.get((row[0], row[2]), 0) + row[3]
        for line in obs.get('lines', ()):
            if line.get('recipe') != 'P075' or line.get('under_construction') or line['id'] in busy:
                continue
            lid = line['id']
            current = focus.get(lid)
            project = projects.get(current, {}) if current else {}
            if current and project.get('theory_complete') and not project.get('verified') and current not in running:
                tier = self.tiers.get(self.nodes.get(current, {}).get('tier'))
                if tier and stock.get((line['hub_id'], tier['validation_good']), 0) >= tier['validation_amount'] * 1000:
                    orders.append(self._order('research', 'validate_project', line_id=lid, tech_id=current))
                continue
            if current is None or current in known or project.get('verified'):
                options = sorted((self.nodes[t]['tier'], t) for t in self.nodes if self._open(t, known)
                                 and not projects.get(t, {}).get('theory_complete'))
                if options:
                    orders.append(self._order('research', 'set_focus', line_id=lid, tech_id=options[0][1],
                                              sec_mode='standard'))
        return orders

    def _caps(self, obs):
        need = {}
        for hub in obs.get('hubs', ()):
            for good, rate in HOUSEHOLD.items():
                key = (hub['id'], good)
                need[key] = need.get(key, 0) + hub.get('population_mU', 0) * rate
        for line in obs.get('lines', ()):
            recipe = self.recipes.get(line.get('recipe'))
            if recipe is None or recipe['facility'] == '电站':
                continue
            for good, q in recipe['inputs'].items():
                key = (line['hub_id'], good)
                need[key] = need.get(key, 0) + as_fraction(q) * 1000
        orders = []
        known = set(obs.get('research', {}).get('known', ()))
        for line in obs.get('lines', ()):
            recipe = self.recipes.get(line.get('recipe'))
            if recipe is None or recipe['facility'] in ('电站', '实验室') or line.get('under_construction') \
                    or line.get('work_locked'):
                continue
            # r7+: the opening P033 line runs without A04, but re-setting it is tech-locked
            if not set(recipe.get('requires_all') or ()) <= known:
                continue
            cap = int(max(need.get((line['hub_id'], g), 0) for g in recipe['outputs']) * CAP_CYCLES)
            cap = max([cap] + [FLOORS[g] for g in recipe['outputs'] if g in FLOORS])
            if (line.get('options') or {}).get('target_stock_mU') != cap:
                orders.append(self._order('industry', 'set_line', line_id=line['id'], recipe=line['recipe'],
                                          mode=line.get('mode') or 'general', target_stock_mU=cap))
        return orders

    def _food(self, obs):
        capital = f'{self.owner}-h0'
        food = sum(r[3] for r in obs.get('inventory', ()) if len(r) == 4 and r[0] == capital
                   and r[1] == 'available' and r[2] == 'food')
        if food < FOOD_FLOOR_MU and obs.get('window', 0) % 2 == 0:
            return [self._order('logistics', 'market_bid', region_market=capital, good='food',
                                qty_mU=FOOD_FLOOR_MU - food, limit_price_mU=60000, expire_cycles=1)]
        return []

    def _expand(self, obs):
        """One construction at a time: food / consumer-goods capacity ahead of the population."""
        capital = f'{self.owner}-h0'
        orders = []
        facilities = [f for f in obs.get('facilities', ()) if f['hub_id'] == capital]
        kind_of = {lid: f['facility_type'] for f in facilities for lid in f.get('line_ids', ())}
        for line in obs.get('lines', ()):
            recipe = NEW_LINE_RECIPE.get(kind_of.get(line['id']))
            if recipe and line.get('recipe') is None and not line.get('under_construction') \
                    and not line.get('work_locked'):
                orders.append(self._order('industry', 'set_line', line_id=line['id'], recipe=recipe, mode='general'))
        if obs.get('work', {}).get('jobs'):
            return orders
        hub = next((h for h in obs.get('hubs', ()) if h['id'] == capital), None)
        if hub is None:
            return orders
        count = {}
        for line in obs.get('lines', ()):
            # a fresh hall whose line still waits for its recipe already counts as capacity
            recipe = line.get('recipe') or NEW_LINE_RECIPE.get(kind_of.get(line['id']))
            if line['hub_id'] == capital and recipe in LINE_OUTPUT:
                count[recipe] = count.get(recipe, 0) + 1
        pop = hub.get('population_mU', 0)
        want = None
        if pop * HOUSEHOLD['food'] > Fraction(95, 100) * count.get('P031', 0) * LINE_OUTPUT['P031']:
            want = '食品厂'
        elif count.get('P031', 0) * 3000 > count.get('P028', 0) * LINE_OUTPUT['P028']:
            want = '农场'
        elif pop * HOUSEHOLD['consumer_goods'] > Fraction(95, 100) * count.get('P032', 0) * LINE_OUTPUT['P032']:
            want = '轻工厂'
        stock = {g: sum(r[3] for r in obs.get('inventory', ()) if len(r) == 4 and r[0] == capital
                        and r[1] == 'available' and r[2] == g) for g in FLOORS}
        load = sum(r[3] for r in obs.get('inventory', ()) if len(r) == 4 and r[0] == capital and r[1] != 'outdoor')
        if load * 4 > hub.get('warehouse_mU', 0) * 3:
            if all(stock[g] >= q * 2 for g, q in WAREHOUSE_COST.items()):
                orders.append(self._order('industry', 'warehouse_expand', hub_id=capital, modules=2))
            return orders
        if want is None:
            return orders
        houses = sum(1 for f in facilities if f['facility_type'] == 'housing' and f.get('commissioned'))
        occupied = sum(1 for f in facilities if f['facility_type'] != 'housing')
        if occupied >= 20 * (10 + houses) // 10:
            if all(stock[g] >= q for g, q in HOUSE_COST.items()):
                orders.append(self._order('industry', 'build_facility', hub_id=capital, facility_type='住宅区',
                                          profile_id='P110'))
        elif all(stock[g] >= q for g, q in S1_COST.items()):
            orders.append(self._order('industry', 'build_facility', hub_id=capital, facility_type=want,
                                      profile_id='S1'))
        return orders

    def decide(self, obs):
        orders = self._research(obs)
        if obs.get('window', 0) % REPLAN_WINDOWS == 0:
            orders += self._caps(obs)
        if obs.get('window', 0) % REPLAN_WINDOWS == 5:
            orders += self._expand(obs)
        orders += self._food(obs)
        return orders[:128]
