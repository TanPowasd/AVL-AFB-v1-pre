# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Joint portfolio exchange plus procurement and delivery certificates."""
from pathlib import Path
from collections import Counter,deque
import sys,time,math
ROOT=Path(__file__).resolve().parent
from .joint_policy import JointPlanner,_opening,rules,sd,moves,needs,PRODUCTS,fibcost
from .portfolio import Portfolio,Project,labor_cost
from .route_columns import solve_routes

PORTFOLIO_REPORT={'days':[]}

class CertifiedPlanner(JointPlanner):
    feedback_rounds=0
    portfolio_type=Portfolio
    select_certified_best=False
    allow_land=True
    def __init__(self):
        super().__init__()
        self.solve=solve_routes
    def cash_available(self,obs):
        farm=obs['farms'][obs['player']];private=obs['private']
        return farm['money']+sum(n*obs['market']['prices'].get(k,0)*.90 for k,n in private['shed'].items() if k in PRODUCTS)
    def begin_day(self,obs):
        start=time.perf_counter();day=obs['step']//24;self.day=day
        self.jobs=[];self.queues=[];self.plan_ready=False
        self.want_seeds=Counter();self.want_animals=Counter();self.want_resources=Counter()
        farm=obs['farms'][obs['player']];private=obs['private']
        herd=sum(isinstance(t,dict) and 'animal' in t for row in farm['tiles'] for t in row)
        cash=self.cash_available(obs)
        budget=cash-fibcost(8)-herd*obs['market']['prices']['WHEAT']-100
        self.buy_land=False;quads=len(farm['unlocked_quadrants']);self.target_quads=quads
        positions=[(x,y) for y in range(10) for x in range(10) if farm['tiles'][y][x]!='LOCKED']
        model=self.portfolio_type(obs,positions)
        model.refinement_phase='root';model.refinement_trigger=True
        projects=model.optimize(budget)
        land_gain=None
        if self.allow_land and quads<4 and day<=23 and budget>(1000,2000,4000)[quads-1]+400:
            import copy
            price=(1000,2000,4000)[quads-1];q=rules.LAND_ORDER[quads-1]
            new_positions=[(x,y) for y in range(10) for x in range(10) if rules._quadrant_of(x,y,10)==q]
            trial=copy.copy(model)
            trial.projects=dict(model.projects);trial.parts=dict(model.parts)
            trial.flow={k:v[:] for k,v in model.flow.items()};trial.work=model.work[:]
            for pos in new_positions:trial.install(pos,Project(0.0,()))
            trial.refinement_phase='land_trial';trial.refinement_trigger=False
            trial.optimize(budget-price,only_positions=new_positions)
            land_gain=trial.value-model.value-price
            first_work=any(trial.projects[pos].first(day)[1] for pos in new_positions)
            if land_gain>0 and first_work:
                model=trial;projects=model.projects;self.buy_land=True;self.target_quads+=1
                budget-=price;positions+=new_positions
        prior_multipliers=model.labor_multiplier[:]
        route_prices=model.reprice_critical_routes()
        model.refinement_phase='reprice'
        model.refinement_trigger=any(new>old*1.05 for old,new in zip(prior_multipliers,model.labor_multiplier))
        projects=model.optimize(budget,sweeps=1)
        feedback=[];alternatives=[]
        for attempt in range(self.feedback_rounds+1):
            self.jobs=[]
            self._build_jobs(obs,model,positions,budget)
            self._requirements()
            self._certify(obs)
            estimated=labor_cost([model.priced_work()[day]])
            actual=fibcost(self.crew)
            feedback.append(dict(attempt=attempt,crew=self.crew,estimated_wage=estimated,constructed_wage=actual))
            if self.select_certified_best:
                import copy
                snapshot=copy.copy(model)
                snapshot.projects=dict(model.projects);snapshot.parts=dict(model.parts)
                snapshot.flow={k:v[:] for k,v in model.flow.items()};snapshot.work=model.work[:]
                snapshot.labor_multiplier=model.labor_multiplier[:]
                alternatives.append(dict(model=snapshot,jobs=copy.deepcopy(self.jobs),crew=self.crew,
                    delivery=self.delivery,uncovered=self.certification_uncovered,attempt=attempt))
            if attempt==self.feedback_rounds or actual<=estimated+50:break
            factor=(self.crew+1)*21/max(1,model.work[day])
            if factor<=model.labor_multiplier[day]*1.02:break
            model.labor_multiplier[day]=factor
            model.value=model.objective()
            model.refinement_phase='feedback';model.refinement_trigger=actual-estimated>100
            projects=model.optimize(budget,sweeps=1)
        if alternatives:
            common=model.labor_multiplier[:]
            for option in alternatives:
                candidate=option['model'];work=[w*m for w,m in zip(candidate.work,common)];work[day]=0
                option['score']=candidate.book.score(candidate.flow)-candidate.cost-labor_cost(work)-fibcost(option['crew'])-option['uncovered']+getattr(candidate,'capital_credit',lambda:0)()
                feedback[option['attempt']]['certified_objective']=option['score']
            best=max(alternatives,key=lambda option:option['score'])
            model=best['model'];self.jobs=best['jobs'];self.crew=best['crew'];self.delivery=best['delivery']
            self.certification_uncovered=best['uncovered']
            feedback[best['attempt']]['selected']=True
        self._requirements()
        self.economic_model=model
        PORTFOLIO_REPORT['days'].append(dict(day=day,crew=self.crew,jobs=len(self.jobs),
            delivery=self.delivery,land=self.buy_land,seeds=dict(self.want_seeds),animals=dict(self.want_animals),
            accepted=model.accepted,proposed=model.proposed,objective=model.value,seconds=time.perf_counter()-start,
            job_details=self.jobs,land_gain=land_gain,modeled_work=model.work[day],
            modeled_daily_labor=labor_cost([model.work[day]]),route_prices=route_prices,route_feedback=feedback))

    def _build_jobs(self,obs,model,positions,budget):
        day=self.day;farm=obs['farms'][obs['player']];projects=model.projects
        prices=model.book.marginal(model.flow)
        goods=Counter();planned_animals=Counter()
        # Commit maintenance before optional construction and land reuse.
        order=sorted(positions,key=lambda pos:(not isinstance(farm['tiles'][pos[1]][pos[0]],dict),sd(pos),pos))
        for pos in order:
            t=farm['tiles'][pos[1]][pos[0]];p=projects[pos];row=p.first(day)
            ops=[];existing=isinstance(t,dict) and ('animal' in t or t.get('kind')=='PLANT')
            for raw in row[1]:
                op=list(raw)
                if op[0]=='PLANT':
                    k=op[1];cost=rules.CROPS[k]['seed']
                    if budget<cost:break
                    budget-=cost
                if op[0]=='BUILD_COOP' or op[0]=='BUILD_PASTURE':
                    a=next((c[1] for c in row[1] if c[0]=='PLACE' and c[1] in rules.ANIMALS),None)
                    if a and budget<rules.ANIMALS[a]['cost']+100:break
                    if a:budget-=rules.ANIMALS[a]['cost']
                ops.append(op)
            if not ops:continue
            value=sum(n*prices.get(k,[0]*30)[day] for k,n in row[2])
            urgent=existing and (t.get('consecutive_unwatered',0)>=1 or t.get('consecutive_unfed',0)>=1)
            self.add_job(pos,ops,value+.1*max(0,p.value)+10000*urgent,existing)
            self.jobs[-1]['outputs']=dict(row[2])
            goods.update(dict(row[2]))
        self.delivery=sum(goods.values())>98 or day==29
        self.herd_count=sum(isinstance(t,dict) and 'animal' in t for row in farm['tiles'] for t in row)

    def _requirements(self):
        self.want_seeds=Counter();self.want_animals=Counter();self.want_resources=Counter()
        for job in self.jobs:
            self.want_resources.update(needs(job['ops']))
            for op in job['ops']:
                if op[0]=='PLANT':self.want_seeds[op[1]]+=1
                if op[0]=='PLACE' and op[1] in rules.ANIMALS:self.want_animals[op[1]]+=1

    def _certify(self,obs):
        resources=Counter(obs['private']['shed'])
        for k,n in self.want_resources.items():resources[k]=max(resources[k],n)
        positions=[(4,4)]
        for _ in range(15):positions.append(min(((4,4),(5,4),(4,5),(5,5)),key=positions.count))
        lower=max(1,math.ceil((sum(len(j['ops']) for j in self.jobs)+len(self.jobs))/22)-2)
        best=None
        for hands in range(max(1,min(lower,15)),16):
            workers=[dict(pos=p,hour=2,inv={}) for p in positions[:hands+1]]
            result=solve_routes(self.jobs,workers,dict(resources),last_hour=22 if self.day==29 else 23,
                track_collect=True,evaluation_budget=6000,return_to_shed=self.delivery,end_service=3 if self.delivery else 1,
                selective_return=True,delivery_jobs=self.delivery_set(self.jobs))
            lost=sum(self.jobs[j]['priority']+(5000 if self.jobs[j]['mandatory'] else 0) for j in result['unassigned'])
            score=fibcost(hands)+lost
            if best is None or score<best[0]:best=score,hands,result
            if not result['unassigned']:break
        self.crew=best[1]
        removed=set(best[2]['unassigned'])
        self.certification_uncovered=sum(self.jobs[j]['priority']+(5000 if self.jobs[j]['mandatory'] else 0) for j in removed)
        # Do not buy inputs for discarded jobs. Mandatory work is retained for
        # the actual-observation replan, which may use earlier collected inputs.
        self.jobs=[j for i,j in enumerate(self.jobs) if i not in removed or j['mandatory']]

    def plan_routes(self,obs,hour):
        old_solve=self.solve
        def delivery_solver(*args,**kwargs):
            kwargs['return_to_shed']=self.delivery
            kwargs['selective_return']=True
            kwargs['end_service']=3 if self.delivery else 1
            kwargs['delivery_jobs']=self.delivery_set(args[0])
            return old_solve(*args,**kwargs)
        self.solve=delivery_solver
        super().plan_routes(obs,hour)
        self.solve=old_solve
        if self.delivery and self.day!=29:
            farm=obs['farms'][obs['player']]
            for i,queue in enumerate(self.queues):
                pos=tuple(([farm['farmer']]+farm['hands'])[i])
                for op in queue:
                    if op[0] in rules.FARMER_MOVES:
                        dx,dy=rules.FARMER_MOVES[op[0]];pos=(pos[0]+dx,pos[1]+dy)
                home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
                suffix=moves(pos,home)+[['DROP']]
                if hour+len(queue)+len(suffix)<=24:queue.extend(suffix)

    def delivery_set(self,jobs):
        if self.day==29:
            return {i for i,j in enumerate(jobs) if j.get('outputs')}
        total=sum(sum(j.get('outputs',{}).values()) for j in jobs)
        needed=max(0,total-90)
        chosen=set();amount=0
        # Deliver enough of the harvest to leave a feasible midnight stock.
        # Selection charges distance, not just nominal sale value.
        order=sorted(range(len(jobs)),key=lambda i:-(sum(jobs[i].get('outputs',{}).values())/(1+sd(jobs[i]['pos']))))
        for i in order:
            if amount>=needed:break
            n=sum(jobs[i].get('outputs',{}).values())
            if n:chosen.add(i);amount+=n
        return chosen

    def act(self,obs,configuration=None):
        action=super().act(obs,configuration)
        # Capacity is shared by all units within a turn, before market sales.
        # Partial PLACE preserves goods; DROP irreversibly discards overflow.
        import copy
        farm=copy.deepcopy(obs['farms'][obs['player']]);private=copy.deepcopy(obs['private'])
        units=[action['farmer']]+action.get('hands',[])
        demand=Counter(c[1] for c in units if c and c[0]=='PLANT')
        blocked={k for k,n in demand.items() if n>private['seeds'].get(k,0)}
        for i,cmd in enumerate(units):
            if cmd and cmd[0]=='DROP':
                inv=private['inventories'][i]
                room=max(0,100-sum(private['shed'].values()))
                total=sum(inv.values())
                if total>room:
                    options=[k for k,n in inv.items() if n>0 and k in PRODUCTS]
                    if room and options:
                        k=max(options,key=lambda k:obs['market']['prices'].get(k,0))
                        cmd=['PLACE',k,min(room,inv[k])]
                    else:cmd=['PASS']
                    if i<len(self.queues):self.queues[i].appendleft(['DROP'])
                elif not total:cmd=['PASS']
                units[i]=cmd
            if cmd and cmd[0]=='PLANT' and cmd[1] in blocked:continue
            rules._apply_unit_action(farm,private,i,cmd,10,self.day,24)
        action['farmer']=units[0];action['hands']=units[1:]
        return action

_players={}
def agent(observation,configuration=None):
    step=int(observation['step']);p=int(observation['player'])
    if step<144:return _opening(observation,configuration)
    if p not in _players or step==144:_players[p]=CertifiedPlanner()
    return _players[p].act(observation,configuration)
