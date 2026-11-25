"""Budgeted daily portfolio selection and observation-checked execution.

The readable d15 lifecycle and insertion code is reused with attribution.
RSIdemo adds a bounded macro search, protected procurement, observed action
repair, capacity-safe delivery, and persistent paid-asset commitments.
"""
from collections import Counter, deque
import copy
import math
from time import perf_counter

from .daily import Planner, fibcost, needs, moves, sd, dist
from .routes import solve_routes
from . import rules, market, budget
from .portfolio import Portfolio
from .projects import Project
from .belief import InventoryBelief


class Strategy(Planner):
    def __init__(self, params=None):
        settings = dict(adapt=True, setup_hours=3, route_ms=120, mode='daily',
                        macro_day=6, macro_ms=2000, crew_dynamic=True, crew_cap=13)
        settings.update(params or {})
        super().__init__(self.bounded_routes, settings)
        self.cfg = {}
        self.metrics = Counter()
        self.last_step = -1
        self.last_player = None
        self.paid = Counter()
        self.end = 29
        self.overage_used = 0.0
        self.belief=InventoryBelief()
        self.realization_records=[]
        self.previous_portfolio=None
        self.harvest_delivery_day=False

    def bounded_routes(self, *args, **kwargs):
        if self.harvest_delivery_day:
            kwargs['return_to_shed']=True
            kwargs['last_hour']=min(21,kwargs.get('last_hour',23))
        kwargs['hard_timeout_ms'] = min(120, max(1, budget.remaining()*1000-20))
        kwargs['evaluation_budget'] = min(kwargs.get('evaluation_budget', 12000), 12000)
        if self.p.get('spatial_routes'):
            from .spatial_routes import solve_routes as spatial
            result=spatial(*args,**kwargs)
            self.metrics['route_'+result['stats'].get('method','insertion')]+=1
            return result
        return solve_routes(*args, **kwargs)

    def herd_target(self, obs, day):
        result = super().herd_target(obs, day)
        if self.p.get('capacity_opening') and day<6:
            # Reserve two nearby slots for early dairy capacity, instead of
            # planting every affordable wheat tile before it can be funded.
            result={'COW':2 if day<2 else 4,'SHEEP':2,'GOOSE':0}
        held = Counter(obs['private']['shed'])
        for inv in obs['private']['inventories']: held.update(inv)
        alive = Counter(t['animal'] for row in obs['farms'][obs['player']]['tiles']
                        for t in row if isinstance(t, dict) and 'animal' in t)
        # Buying an animal creates a commitment even if the desired mix changes.
        return {a: max(n, alive[a]+held[a]) for a, n in result.items()}

    def choose_crop(self, pos, day, counts):
        if self.p.get('capacity_opening') and day<11:
            if day<=2 and counts['MELON']<self.p['initial_melon']:
                return 'MELON'
            wheat=7 if day<5 else 5
            if counts['WHEAT']<wheat:return 'WHEAT'
            if day<5:return None
            if counts['STRAWBERRY']<self.p.get('opening_strawberry',22):return 'STRAWBERRY'
            return None
        crop = super().choose_crop(pos, day, counts)
        if crop and day+rules.CROPS[crop]['first_yield_day'] > self.end:
            return None
        return crop

    def crop_controls(self,pos,tile,day):
        if not self.p.get('fast_turnover'):return None
        crop=tile.get('crop')
        if crop not in ('WHEAT','CARROT','MELON'):return None
        # Constructive high-throughput candidates. Fertilizer is paid for and
        # transported by the ordinary route planner; no free yield is credited.
        prices=getattr(self,'current_prices',{})
        price=prices.get(crop,rules.MARKET_PARAMS[crop]['base'])
        fert=prices.get('FERTILIZER',rules.MARKET_PARAMS['FERTILIZER']['base'])
        if crop=='WHEAT' and fert>=2*price-2.5:return None
        if crop=='CARROT' and fert>=price-20/3:return None
        harvest={'WHEAT':3,'CARROT':2,'MELON':10}[crop]
        water={'WHEAT':(0,2,3),'CARROT':(0,2),'MELON':(0,2,4,6,7,8,9,10)}[crop]
        fertilizer={'WHEAT':(2,),'CARROT':(2,),'MELON':()}[crop]
        return {'end_age':harvest,'water_ages':water,'fertilize_ages':fertilizer}

    def begin_day(self, obs):
        self.current_prices=obs['market']['prices']
        day=obs['step']//24
        ripe_melons=sum(t.get('yield_units',0) for row in obs['farms'][obs['player']]['tiles'] for t in row
                       if isinstance(t,dict) and t.get('crop')=='MELON' and day-t['planted_day']>=10)
        self.harvest_delivery_day=bool(self.p.get('harvest_delivery') and ripe_melons>=12 and day<self.end)
        if self.harvest_delivery_day:self.metrics['harvest_delivery_days']+=1
        if self.p.get('terminal_cash') and obs['step']//24==self.end:
            from .terminal import begin
            begin(self,obs)
            return
        super().begin_day(obs)
        if self.p['mode'] != 'portfolio' or self.day < self.p['macro_day']:
            return
        started = perf_counter()
        fallback = {k: copy.deepcopy(getattr(self, k)) for k in (
            'jobs', 'crew', 'want_seeds', 'want_animals', 'want_resources', 'buy_land', 'target_quads', 'herd_count')}
        farm = obs['farms'][obs['player']]
        available = farm['money']-fibcost(self.crew)-self.herd_count*obs['market']['prices']['WHEAT']-100
        positions = [(x,y) for y,row in enumerate(farm['tiles']) for x,t in enumerate(row) if t != 'LOCKED']
        reserve=.95 if self.p.get('realization') else .2
        macro_deadline = min(self.return_deadline-reserve, started+self.p['macro_ms']/1000)
        budget.set_deadline(macro_deadline)
        try:
            model = Portfolio(obs, positions)
        except budget.BudgetExhausted:
            self.metrics['macro_no_complete_model'] += 1
            budget.set_deadline(self.return_deadline)
            return
        try:
            if self.p.get('warm_columns'):
                from .continuation import reuse
                model.investment_budget=max(0,available)
                stats=reuse(model,obs,self.previous_portfolio)
                for key,n in stats.items():self.metrics['continuation_'+key]+=n
            model.optimize(max(0, available), sweeps=1)
            for _ in range(self.p.get('extra_sweeps',0)):
                before=model.value;accepted=model.accepted
                model.optimize(max(0,available),sweeps=1)
                self.metrics['extra_sweeps']+=1
                if model.accepted==accepted or model.value-before<25:break
        except budget.BudgetExhausted:
            self.metrics['macro_partial_sweep'] += 1
        if self.p.get('neighborhood') in ('jump','heuristic','beam','hybrid') and budget.remaining()>.18:
            from .neighborhood import perturb
            modes=('heuristic','jump') if self.p['neighborhood']=='hybrid' else (self.p['neighborhood'],)
            if self.p['neighborhood']=='hybrid' and self.p.get('retain_suboptimal'):modes+=('beam',)
            for index,mode in enumerate(modes):
                if budget.remaining()<.18:break
                # Share the remaining budget so a heuristic proposal cannot
                # consume the entire budget before jump search gets a chance.
                phase_end=min(macro_deadline,perf_counter()+budget.remaining()/(len(modes)-index))
                budget.set_deadline(phase_end)
                attempts=self.p.get('beam_attempts',6) if mode=='beam' else self.p.get('jump_attempts',3)
                model,stats=perturb(model,mode,attempts,self.p.get('jump_width',6))
                for key,n in stats.items():
                    self.metrics['neighborhood_'+key]+=n
                    self.metrics[mode+'_'+key]+=n
                budget.set_deadline(macro_deadline)
        if self.p.get('route_feedback') and budget.remaining()>.25:
            multipliers=model.labor_multiplier[:]
            old_value=model.value
            try:
                model.reprice_critical_routes()
            except budget.BudgetExhausted:
                model.labor_multiplier=multipliers;model.value=old_value
                self.metrics['route_feedback_incomplete']+=1
            else:
                self.metrics['route_feedback_complete']+=1
                try:model.optimize(max(0,available),sweeps=1)
                except budget.BudgetExhausted:self.metrics['feedback_partial_sweep']+=1
        if self.p.get('block_pool') and budget.remaining()>.15:
            from .block_pool import search,certify
            incumbent=model
            candidate,stats=search(model,self.p.get('block_evaluations',160),self.p.get('block_temperature',0))
            if candidate is not incumbent and budget.remaining()>.08:
                if certify(incumbent,obs,self) and certify(candidate,obs,self):
                    model=candidate;stats['certified_selected']+=1
                else:stats['coverage_rejected']+=1
            for key,n in stats.items():self.metrics['block_'+key]+=n
        buy_land = False
        target_quads = len(farm['unlocked_quadrants'])
        land_cost = 0
        # Treat land as a complete alternative portfolio: pay for the land,
        # propose real projects on it, and recompute shared prices and wages.
        if self.p.get('portfolio_land', True) and target_quads < 4 and self.day <= self.end-4:
            cost = rules.LAND_PRICES[target_quads-1]
            if available >= cost+400 and budget.remaining() > .15:
                quadrant = rules.LAND_ORDER[target_quads-1]
                new_positions = [(x,y) for y in range(10) for x in range(10)
                                 if rules._quadrant_of(x,y,10) == quadrant]
                trial = copy.copy(model)
                trial.projects = dict(model.projects)
                trial.parts = dict(model.parts)
                trial.flow = {k:v[:] for k,v in model.flow.items()}
                trial.work = model.work[:]
                trial.labor_multiplier = model.labor_multiplier[:]
                trial.upfront = model.upfront.copy()
                trial.total_investment = model.total_investment.copy()
                trial.column_pool={pos:dict(options) for pos,options in model.column_pool.items()}
                for pos in new_positions: trial.install(pos,Project(0.0,()))
                try:
                    trial.optimize(available-cost, only_positions=new_positions, sweeps=1)
                except budget.BudgetExhausted:
                    self.metrics['land_partial_sweep'] += 1
                immediate = any(trial.projects[pos].first(self.day)[1] for pos in new_positions)
                # A timeout leaves only complete accepted columns in trial.
                if immediate and trial.value-model.value > cost:
                    model = trial
                    positions += new_positions
                    buy_land = True
                    target_quads += 1
                    land_cost = cost
                    self.metrics['land_portfolios_selected'] += 1
        budget.set_deadline(self.return_deadline)
        # Every accepted column is complete even if a later proposal times out.
        # Rejected/interrupted columns were rolled back transactionally.
        jobs = []
        cash = max(0, available-land_cost)
        for pos in positions:
            tile = farm['tiles'][pos[1]][pos[0]]
            project = model.projects[pos]
            row = project.first(self.day)
            ops = []
            for raw in row[1]:
                op = list(raw)
                if op[0] == 'PLANT':
                    cost = rules.CROPS[op[1]]['seed']
                    if cash < cost: break
                    cash -= cost
                if op[0] in ('BUILD_COOP', 'BUILD_PASTURE'):
                    animal = next((o[1] for o in row[1] if o[0] == 'PLACE' and o[1] in rules.ANIMALS), None)
                    if animal:
                        cost = rules.ANIMALS[animal]['cost']
                        if cash < cost+100: break
                        cash -= cost
                ops.append(op)
            if ops:
                existing = isinstance(tile, dict) and ('animal' in tile or 'crop' in tile)
                urgent = existing and (tile.get('consecutive_unwatered', 0) or tile.get('consecutive_unfed', 0))
                value = sum(n*obs['market']['prices'][k] for k,n in row[2])
                jobs.append(dict(pos=pos, ops=ops, mandatory=existing,
                                 priority=value+.1*max(0, project.value)+10000*urgent,
                                 outputs=dict(row[2])))
        # A paid animal is retained by using the certified daily fallback until
        # it is on the board; the economic columns only model deployed assets.
        if any(obs['private']['shed'].get(a, 0) for a in rules.ANIMALS):
            self.metrics['paid_asset_fallbacks'] += 1
            return
        self.jobs = jobs
        self.buy_land = buy_land
        self.target_quads = target_quads
        self.requirements()
        self.choose_crew(obs)
        if self.p.get('realization') and budget.remaining()>.25:
            from .realization import choose,snapshot,install
            macro=snapshot(self)
            budget.set_deadline(self.return_deadline-.04)
            winner,records=choose(self,obs,[('macro',macro),('daily',fallback)])
            self.realization_records.append({'day':self.day,'candidates':records,'complete':winner is not None,
                'selected':winner[0] if winner else 'macro','rival_assumption':'public cohort daily flow spread uniformly; no new shops today'})
            if winner:
                install(self,winner[1]);self.metrics['realization_'+winner[0]+'_selected']+=1
            else:self.metrics['realization_incomplete']+=1
            budget.set_deadline(self.return_deadline)
        if perf_counter() >= self.return_deadline-.02:
            for k,v in fallback.items(): setattr(self, k, v)
            self.metrics['macro_return_fallbacks'] += 1
        else:
            self.metrics['macro_days'] += 1
            self.metrics['accepted_columns'] += model.accepted
            if self.p.get('warm_columns'):
                from .continuation import remember
                self.previous_portfolio=remember(obs,model.projects)

    def requirements(self):
        self.want_seeds = Counter()
        self.want_animals = Counter()
        self.want_resources = Counter()
        for job in self.jobs:
            self.want_resources.update(needs(job['ops']))
            for op in job['ops']:
                if op[0] == 'PLANT': self.want_seeds[op[1]] += 1
                if op[0] == 'PLACE' and op[1] in rules.ANIMALS:
                    self.want_animals[op[1]] += 1

    def choose_crew(self, obs):
        # Select the cheapest constructed roster that covers existing assets;
        # give optional expansion its nominal value, not an infinite priority.
        stock = Counter(obs['private']['shed'])
        for k,n in self.want_resources.items(): stock[k] = max(stock[k], n)
        positions = [(4,4)]
        for _ in range(self.p['crew_cap']):
            positions.append(min(((4,4),(5,4),(4,5),(5,5)), key=positions.count))
        work = sum(len(j['ops'])+1 for j in self.jobs)
        lower = max(0, min(self.p['crew_cap'], math.ceil(work/(24-self.p['setup_hours']))-2))
        best = None
        for n in range(lower, self.p['crew_cap']+1):
            if best and budget.remaining() < .07: break
            workers = [dict(pos=p, hour=self.p['setup_hours'], inv={}) for p in positions[:n+1]]
            result = self.bounded_routes(self.jobs, workers, dict(stock),
                last_hour=22 if self.day == self.end else 23,
                track_collect=True, evaluation_budget=3000,
                return_to_shed=self.day == self.end)
            missed = sum((10000 if self.jobs[i]['mandatory'] else 0)+self.jobs[i]['priority']
                         for i in result['unassigned'])
            value = fibcost(n)+missed
            if best is None or value < best[0]: best = (value, n, result)
            if not result['unassigned']: break
        self.crew = best[1]

    def market(self, obs, hour, unit_actions=None):
        units = unit_actions or [['PASS'] for _ in range(1+len(obs['farms'][obs['player']]['hands']))]
        return market.orders(self, obs, hour, units)

    def plan_routes(self, obs, hour):
        if self.p.get('terminal_cash') and self.day==self.end:
            from .terminal import routes
            routes(self,obs,hour)
        else:super().plan_routes(obs, hour)
        if self.harvest_delivery_day:
            farm=obs['farms'][obs['player']]
            for i,queue in enumerate(self.queues):
                if not queue:continue
                pos=tuple(([farm['farmer']]+farm['hands'])[i])
                for op in queue:
                    if op[0] in rules.FARMER_MOVES:
                        dx,dy=rules.FARMER_MOVES[op[0]];pos=(pos[0]+dx,pos[1]+dy)
                home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
                tail=moves(pos,home)+[['DROP']]
                if hour+len(queue)+len(tail)<=22:
                    queue.extend(tail);self.metrics['required_delivery_routes']+=1
                else:self.metrics['required_delivery_failed']+=1
        elif self.p.get('day_delivery'):
            from .delivery import repair_queues
            repair_queues(self,obs,hour)
        self.metrics['unassigned_jobs'] += len(self.unassigned)
        self.metrics['unassigned_critical'] += sum(j['mandatory'] for j in self.unassigned)

    def finish_action(self, obs, action):
        units = [action['farmer'], *action['hands']]
        farm, private = copy.deepcopy((obs['farms'][obs['player']], obs['private']))
        positions = [farm['farmer'], *farm['hands']]
        for i, op in enumerate(units):
            if op[0] == 'DROP':
                inv = private['inventories'][i]
                room = max(0, self.cfg.get('shedCapacity',100)-sum(private['shed'].values()))
                if sum(inv.values()) > room:
                    goods = [k for k,n in inv.items() if n and k in rules.PRODUCTS]
                    if room and goods:
                        item = max(goods, key=lambda k: obs['market']['prices'][k])
                        op = ['PLACE', item, min(room, inv[item])]
                    else: op = ['PASS']
                    if i < len(self.queues): self.queues[i].appendleft(['DROP'])
                    self.metrics['protected_deliveries'] += 1
            # Check against sequential same-turn state, including shared
            # seed/warehouse resources and earlier workers' operations.
            snapshot = copy.deepcopy((farm, private))
            rules._apply_unit_action(farm, private, i, op, self.cfg.get('boardSize',10),
                obs['step']//self.cfg.get('turnsPerDay',24), self.cfg.get('turnsPerDay',24),
                self.cfg.get('shedCapacity',100))
            if op != ['PASS'] and snapshot == (farm,private):
                self.metrics['prevented_noops'] += 1
                if op[0] in ('FEED','PLACE','PLANT','PICKUP'):
                    self.metrics['failed_prerequisites'] += 1
                op = ['PASS']
            units[i] = op
        action.update(farmer=units[0], hands=units[1:], market=self.market(obs, obs['step']%24, units))
        if self.p.get('tactical_market'):
            from .tactical_market import optimize
            # farm/private are exactly post-worker state from the preflight.
            action['market']=optimize(self,obs,farm,private,action['market'])
        return action

    def act(self, obs, configuration=None):
        began = perf_counter()
        obs=dict(obs,_rsi_net_crop_inputs=self.p.get('net_crop_inputs',False),
                 _rsi_paid_costs=self.p.get('paid_costs',False),
                 _rsi_hold_inputs=self.p.get('hold_inputs',False),
                 _rsi_animal_reuse=self.p.get('animal_reuse',False),
                 _rsi_residual_weights=self.p.get('residual_weights',{}),
                 _rsi_record_columns=self.p.get('block_pool',False),
                 _rsi_learned_supply=self.p.get('learned_supply',False))
        if self.p.get('prior_bank'):
            self.belief.update(obs)
            obs=dict(obs,_rsi_prior_bank=True,_inventory_belief=self.belief.snapshot())
        self.cfg = dict(configuration or {})
        self.end = (self.cfg.get('episodeSteps',720)-2)//self.cfg.get('turnsPerDay',24)
        base_timeout = self.cfg.get('actTimeout', 1)
        remaining = max(0, min(60-self.overage_used, obs.get('remainingOverageTime',60)))
        day = obs['step']//self.cfg.get('turnsPerDay',24)
        allowance = min(2.0, max(0, remaining-3)/max(1, self.end-day+1))
        self.return_deadline = began+max(.01, base_timeout+allowance-.12)
        budget.set_deadline(self.return_deadline)
        try:
            action = super().act(obs, configuration)
            action = self.finish_action(obs, action)
            self.metrics['calls'] += 1
            self.metrics['max_us'] = max(self.metrics['max_us'], int((perf_counter()-began)*1e6))
            if self.p.get('prior_bank'):self.belief.remember(obs,action)
            return action
        finally:
            self.overage_used += max(0, perf_counter()-began-self.cfg.get('actTimeout',1))
            budget.set_deadline(float('inf'))
