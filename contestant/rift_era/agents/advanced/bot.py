"""裂陆纪元 高级 bot（Advanced，公开给选手的练习对手）。

独立实现，不导入也不复制 medium/super 的代码。策略画像：“固定系数计划 + 实验室突击”。
只用 Python 标准库，只读 init 目录与本国过滤观测。

1. 生产：固定系数模板。每 4 个决策窗口把“户需 + 各产线满负荷投入”线性叠加成每周期需求，
   每条产线的本地目标库存 = 5 周期需求（所有物品同一系数，不区分民生/投资）。
2. 研究：先冲固定路线 L01 → I02 → C01 → C02 → R02 → E02 → R01 → R04 → I01 → I03 → C03 → R05 → C05 → R07，
   之后按（阶, 编号）广度补齐；standard 档。实验室数量按工程师容量线性解出
   （每座基础实验室 3 名工程师），一次只建一座 S1 实验室。
3. 财政：税率一次性降到 10%（支持度），维护预算不设上限。
4. 产能与仓容（rift-advanced-2，修 rift-advanced-1 长局下滑）：每 4 个窗口检查一次，一次只排一项工程：
   首都仓库占用 > 70%（矿井不受目标库存控制，持续往公共仓库进货；仓库满了食品厂/轻工厂的产出放不进去）
   → 按缺口扩建仓容；否则户需超过产能 92% → 补 S1 食品厂 / 农场 / 轻工厂；槽位不足先建住宅区。
   钢材/水泥/机件有最低目标库存，保证建设与维护有料。（rift-advanced-1 的“钢/水泥余量全部变仓容”已删除。）
5. 不管电站、不建制药厂、不做学院培训、不碰金融/外交/军事。
"""

from fractions import Fraction

NAME = 'advanced'
VERSION = 'rift-advanced-3'

PER_CAPITA = (('food', Fraction(6, 100)), ('water', Fraction(4, 100)), ('medicine', Fraction(1, 100)),
              ('medical_supplies', Fraction(25, 10000)), ('consumer_goods', Fraction(3, 100)))
COEFFICIENT = 5
SPRINT = ('L01', 'I02', 'C01', 'C02', 'R02', 'E02', 'R01', 'R04', 'I01', 'I03', 'C03', 'R05', 'C05', 'R07')
MIN_STOCK = {'steel': 24000, 'cement': 30000, 'basic_parts': 12000}
FACTORY = {'steel': 6000, 'cement': 6000, 'basic_parts': 2000}
HOUSING = {'steel': 5000, 'cement': 20000}
MODULE = {'steel': 4000, 'cement': 6000}
# per-cycle full-load output of one S1 line and the per-capita good it serves
CAPACITY = (('食品厂', 'P031', 'food', 3000), ('轻工厂', 'P032', 'consumer_goods', 3000))
FRESH_RECIPE = {'食品厂': 'P031', '农场': 'P028', '轻工厂': 'P032', '实验室': 'P075'}
LAB_COST = {'steel': 6000, 'cement': 6000, 'basic_parts': 2000}


def rational(x):
    return Fraction(*x['$rational']) if isinstance(x, dict) and '$rational' in x else Fraction(x)


class Bot:
    def __init__(self, init):
        self.nation = init['owner']
        self.home = self.nation + '-h0'
        cat = init.get('catalog', {})
        self.recipe = {r['id']: r for r in cat.get('industry', {}).get('recipes', ())}
        tech = cat.get('technology', {})
        self.tech = {r['id']: r for r in tech.get('technologies', ())}
        self.validation = {r['tier']: (r['validation_good'], r['validation_amount'] * 1000)
                           for r in tech.get('research_tiers', ())}
        self.counter = 0
        self.taxed = False
        self.last_lab = -100

    def new(self, kind, op, **fields):
        self.counter += 1
        return dict(id=str(self.counter), kind=kind, op=op, **fields)

    @staticmethod
    def stockpile(obs):
        table = {}
        for row in obs.get('inventory', ()):
            if len(row) == 4 and row[1] == 'available':
                table[row[0], row[2]] = table.get((row[0], row[2]), 0) + row[3]
        return table

    def unlocked(self, tid, known):
        row = self.tech[tid]
        a = set(row.get('requires_all') or ())
        b = set(row.get('requires_any') or ())
        return tid not in known and a <= known and (not b or bool(b & known))

    # 2. research
    def labs(self, obs, stock):
        res = obs.get('research', {})
        known = set(res.get('known', ()))
        projects = res.get('projects', {})
        focus = res.get('focus', {})
        jobs = [j for j in res.get('validation', ()) if j.get('active')]
        occupied = {j['lab_id'] for j in jobs}
        in_validation = {j['tech_id'] for j in jobs}
        taken = set(focus.values())
        result = []
        for line in obs.get('lines', ()):
            if line.get('recipe') != 'P075' or line.get('under_construction') or line.get('work_locked') \
                    or line['id'] in occupied:
                continue
            lid = line['id']
            cur = focus.get(lid)
            proj = projects.get(cur, {}) if cur else {}
            if cur and proj.get('theory_complete') and not proj.get('verified'):
                if cur not in in_validation:
                    good, amount = self.validation[self.tech[cur]['tier']]
                    if stock.get((line['hub_id'], good), 0) >= amount:
                        result.append(self.new('research', 'validate_project', line_id=lid, tech_id=cur))
                continue
            if cur and cur not in known and not proj.get('verified'):
                continue
            pick = next((t for t in SPRINT if t in self.tech and t not in taken and self.unlocked(t, known)
                         and not projects.get(t, {}).get('theory_complete')), None)
            if pick is None:
                rest = sorted((self.tech[t]['tier'], t) for t in self.tech
                              if t not in taken and self.unlocked(t, known) and not projects.get(t, {}).get('theory_complete'))
                pick = rest[0][1] if rest else None
            if pick:
                taken.add(pick)
                result.append(self.new('research', 'set_focus', line_id=lid, tech_id=pick, sec_mode='standard'))
            # idle new laboratory lines get the basic recipe
        kinds = {lid: f['facility_type'] for f in obs.get('facilities', ()) for lid in f.get('line_ids', ())}
        for line in obs.get('lines', ()):
            fresh = FRESH_RECIPE.get(kinds.get(line['id']))
            if fresh and line.get('recipe') is None and not line.get('under_construction') \
                    and not line.get('work_locked'):
                result.append(self.new('industry', 'set_line', line_id=line['id'], recipe=fresh, mode='general'))
        w = obs.get('window', 0)
        hub = next((h for h in obs.get('hubs', ()) if h['id'] == self.home), {})
        capacity = hub.get('skills_mU', {}).get('engineer', 0) // 3000
        lab_count = sum(1 for f in obs.get('facilities', ()) if f['facility_type'] == '实验室')
        if lab_count < capacity and w - self.last_lab >= 20 and \
                all(stock.get((self.home, g), 0) >= q for g, q in LAB_COST.items()):
            self.last_lab = w
            result.append(self.new('industry', 'build_facility', hub_id=self.home, facility_type='实验室',
                                   profile_id='S1'))
        return result

    # 1. production template
    def template(self, obs):
        flow = {}
        for hub in obs.get('hubs', ()):
            for good, rate in PER_CAPITA:
                flow[hub['id'], good] = flow.get((hub['id'], good), 0) + hub.get('population_mU', 0) * rate
        for line in obs.get('lines', ()):
            r = self.recipe.get(line.get('recipe'))
            if r and r['facility'] != '电站':
                for good, q in r['inputs'].items():
                    flow[line['hub_id'], good] = flow.get((line['hub_id'], good), 0) + rational(q) * 1000
        result = []
        known = set(obs.get('research', {}).get('known', ()))
        for line in obs.get('lines', ()):
            r = self.recipe.get(line.get('recipe'))
            if not r or r['facility'] in ('电站', '实验室') or line.get('under_construction') or line.get('work_locked'):
                continue
            # r7+: the opening P033 line runs without A04, but re-setting it is tech-locked
            if not set(r.get('requires_all') or ()) <= known:
                continue
            level = int(COEFFICIENT * max(flow.get((line['hub_id'], g), 0) for g in r['outputs']))
            level = max([level] + [MIN_STOCK[g] for g in r['outputs'] if g in MIN_STOCK])
            old = (line.get('options') or {}).get('target_stock_mU')
            if old is None or abs(old - level) > 2000:
                result.append(self.new('industry', 'set_line', line_id=line['id'], recipe=line['recipe'],
                                       mode=line.get('mode') or 'general', target_stock_mU=level))
        return result

    # 4. capacity and storage
    def capacity(self, obs, stock):
        if any(j for j in obs.get('work', {}).get('jobs', ())):
            return []
        hub = next((h for h in obs.get('hubs', ()) if h['id'] == self.home), None)
        if hub is None:
            return []
        have = {g: stock.get((self.home, g), 0) for g in MIN_STOCK}
        used = sum(r[3] for r in obs.get('inventory', ()) if len(r) == 4 and r[0] == self.home and r[1] != 'outdoor')
        room = hub.get('warehouse_mU', 0)
        if used * 10 > room * 7:
            modules = min(4, int((used * 10 - room * 7) // 3500000) + 1,
                          have['steel'] // MODULE['steel'], have['cement'] // MODULE['cement'])
            return [self.new('industry', 'warehouse_expand', hub_id=self.home, modules=int(modules))] if modules else []
        kinds = {lid: f['facility_type'] for f in obs.get('facilities', ()) for lid in f.get('line_ids', ())}
        lines = {}
        for line in obs.get('lines', ()):
            recipe = line.get('recipe') or FRESH_RECIPE.get(kinds.get(line['id']))
            if line['hub_id'] == self.home and recipe:
                lines[recipe] = lines.get(recipe, 0) + 1
        people = hub.get('population_mU', 0)
        target = None
        for kind, recipe, good, per_line in CAPACITY:
            if people * dict(PER_CAPITA)[good] * 100 > lines.get(recipe, 0) * per_line * 92:
                target = kind
                break
            if recipe == 'P031' and lines.get('P031', 0) * 3000 > lines.get('P028', 0) * 4000:
                target = '农场'
                break
        if target is None:
            return []
        mine = [f for f in obs.get('facilities', ()) if f['hub_id'] == self.home]
        homes = sum(1 for f in mine if f['facility_type'] == 'housing' and f.get('commissioned'))
        if sum(1 for f in mine if f['facility_type'] != 'housing') >= 20 + 2 * homes:
            if all(have[g] >= q for g, q in HOUSING.items()):
                return [self.new('industry', 'build_facility', hub_id=self.home, facility_type='住宅区',
                                 profile_id='P110')]
            return []
        if all(have[g] >= q for g, q in FACTORY.items()):
            return [self.new('industry', 'build_facility', hub_id=self.home, facility_type=target, profile_id='S1')]
        return []

    def decide(self, obs):
        w = obs.get('window', 0)
        stock = self.stockpile(obs)
        orders = self.labs(obs, stock)
        if not self.taxed:
            self.taxed = True
            orders.append(self.new('governance', 'fiscal_policy', tax_rate_bp=1000, welfare_budget_mU=0,
                                   maint_budget_mU=10 ** 12))
        if w % 4 == 0:
            orders += self.template(obs)
        if w % 4 == 2:
            orders += self.capacity(obs, stock)
        return orders[:128]
