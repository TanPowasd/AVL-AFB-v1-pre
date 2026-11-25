"""模板策略：科研不断档 + 首都粮食兜底。在此基础上改进。

观测字段说明见选手包 rules/06_agent_interface.md；命令表见 COMMANDS.md。
"""

FOOD_FLOOR_MU = 400_000      # 首都粮食低于 400 单位（1 单位 = 1000 mU）就去本地市场买
FOOD_BID_PRICE_MU = 60_000   # 限价（mU 本币/单位）


class Bot:
    def __init__(self, init):
        self.owner = init['owner']
        tech = init.get('catalog', {}).get('technology', {})
        self.nodes = {row['id']: row for row in tech.get('technologies', ())}
        self.seq = 0

    def order(self, kind, op, **fields):
        self.seq += 1
        return {'id': str(self.seq), 'kind': kind, 'op': op} | fields

    def researchable(self, tech_id, known):
        row = self.nodes[tech_id]
        need_all = set(row.get('requires_all') or ())
        need_any = set(row.get('requires_any') or ())
        return tech_id not in known and need_all <= known and (not need_any or need_any & known)

    def decide(self, obs):
        orders = []
        research = obs.get('research', {})
        known = set(research.get('known', ()))
        projects = research.get('projects', {})
        focus = research.get('focus', {})
        busy = {j['lab_id'] for j in research.get('validation', ()) if j.get('active')}
        claimed = set(focus.values())
        # 1) 每座实验室（配方 P075 的产线）：理论完成就验证，否则研究最低阶的可研究科技
        for line in obs.get('lines', ()):
            if line.get('recipe') != 'P075' or line.get('under_construction') or line['id'] in busy:
                continue
            current = focus.get(line['id'])
            project = projects.get(current, {}) if current else {}
            if current and project.get('theory_complete') and not project.get('verified'):
                orders.append(self.order('research', 'validate_project', line_id=line['id'], tech_id=current))
                continue
            if current is None or current in known or project.get('verified'):
                options = sorted((self.nodes[t]['tier'], t) for t in self.nodes
                                 if self.researchable(t, known) and t not in claimed)
                if options:
                    claimed.add(options[0][1])
                    orders.append(self.order('research', 'set_focus', line_id=line['id'],
                                              tech_id=options[0][1], sec_mode='standard'))
        # 2) 首都（Hub id 以 -h0 结尾）粮食兜底：库存行格式 [hub, 状态, 物品, 数量mU]
        inventory = obs.get('inventory', ())
        capital = next((r[0] for r in inventory if len(r) == 4 and str(r[0]).endswith('-h0')), None)
        food = sum(r[3] for r in inventory if len(r) == 4 and r[0] == capital and r[1] == 'available' and r[2] == 'food')
        if capital and food < FOOD_FLOOR_MU and obs.get('window', 0) % 2 == 0:
            orders.append(self.order('logistics', 'market_bid', region_market=capital, good='food',
                                     qty_mU=FOOD_FLOOR_MU - food, limit_price_mU=FOOD_BID_PRICE_MU, expire_cycles=1))
        return orders
