# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Preserve feasible route incumbents and acquire the planned daily crew.

Derived from the failed service-transfer game's concrete execution audit:
larger heuristic budgets may return worse coverage, and discretionary early
sales must not consume the slots needed to hire the committed crew.
"""
from collections import Counter
from .route_columns import solve_routes
from .joint_v2 import PORTFOLIO_REPORT, fibcost
from .joint_adaptive import AdaptivePlanner, InventoryBelief, _opening
from . import gpt6_rules as rules

def guarded_routes(jobs,workers,shed,*args,**kwargs):
    requested=kwargs.get('evaluation_budget',6000)
    first=dict(kwargs);first['evaluation_budget']=min(6000,requested)
    incumbent=solve_routes(jobs,workers,shed,*args,**first)
    if not incumbent['unassigned'] or requested<=6000:
        incumbent['stats']['coverage_guard']='complete_low_budget' if not incumbent['unassigned'] else 'low_budget'
        return incumbent
    challenger=solve_routes(jobs,workers,shed,*args,**kwargs)
    def key(result):
        missed=result['unassigned']
        lost=sum(jobs[i]['priority']+(5000 if jobs[i]['mandatory'] else 0) for i in missed)
        return bool(missed),lost,len(missed),sum(result['finish_hours'])
    best=min((incumbent,challenger),key=key)
    best['stats']['coverage_guard']='incumbent_preserved' if best is incumbent else 'challenger_improved'
    return best

class ExecutionMixin:
    def __init__(self):
        super().__init__()
        self.solve=guarded_routes

    def market(self,obs,hour,unit_actions=None):
        if hour>=self.p['setup_hours']:
            return super().market(obs,hour,unit_actions)
        private=obs['private'];farm=obs['farms'][obs['player']]
        prices=obs['market']['prices'];shed=Counter(private['shed'])
        orders=[];cash=farm['money'];buys=[]
        for item in ('WHEAT','FERTILIZER'):
            n=max(0,self.want_resources[item]-shed[item])
            if n:buys.append(('BUY_PRODUCT',item,n,prices[item]+1))
        land=None
        if self.buy_land and len(farm['unlocked_quadrants'])<self.target_quads:
            land=(1000,2000,4000)[len(farm['unlocked_quadrants'])-1]
        for animal,n in self.want_animals.items():
            missing=max(0,n-shed[animal])
            if missing:buys.append(('BUY_ANIMAL',animal,missing,rules.ANIMALS[animal]['cost']))
        for crop,n in self.want_seeds.items():
            missing=max(0,n-private['seeds'].get(crop,0))
            if missing:buys.append(('BUY_SEED',crop,missing,rules.CROPS[crop]['seed']))
        hired=len(farm['hands'])
        cost=sum(n*price for _,_,n,price in buys)+(land or 0)+fibcost(self.crew)-fibcost(hired)
        space=sum(n for op,_,n,_ in buys if op!='BUY_SEED')-max(0,100-sum(shed.values()))
        excess={k:max(0,shed[k]-self.want_resources[k]) for k in rules.PRODUCTS}
        # Sell only the lines needed to fund commitments or make warehouse room
        # before hiring. Remaining ordinary sales resume after the setup phase.
        while (cash<cost or space>0) and len(orders)<10:
            options=[k for k,n in excess.items() if n>0]
            if not options:break
            item=max(options,key=lambda k:(excess[k] if space>0 else excess[k]*prices[k],excess[k]*prices[k]))
            n=excess[item];orders.append(['SELL',item,n]);excess[item]=0
            shed[item]-=n;space-=n;cash+=n*prices[item]*.9
        while hired<self.crew and len(orders)<10:
            price=fibcost(hired+1)-fibcost(hired)
            if cash<price:break
            orders.append(['HIRE']);hired+=1;cash-=price
        for op,item,n,price in buys:
            if len(orders)>=10:break
            if land and op!='BUY_PRODUCT' and cash>=land:
                orders.append(['BUY_LAND']);cash-=land;land=None
                if len(orders)>=10:break
            n=max(0,min(n,int(cash/max(1,price))))
            if op!='BUY_SEED':n=min(n,max(0,100-sum(shed.values())))
            if n:
                orders.append([op,item,n]);cash-=n*price
                if op!='BUY_SEED':shed[item]+=n
        if land and len(orders)<10 and cash>=land:orders.append(['BUY_LAND']);cash-=land
        for item in sorted(excess,key=lambda k:-excess[k]*prices[k]):
            if excess[item] and len(orders)<10:
                orders.append(['SELL',item,excess[item]])
        return orders

    def plan_routes(self,obs,hour):
        super().plan_routes(obs,hour)
        row=PORTFOLIO_REPORT['days'][-1]
        row['execution_contract']=dict(planned_crew=self.crew,
            actual_crew=len(obs['farms'][obs['player']]['hands']),start_hour=hour,
            unassigned=[list(job['pos']) for job in self.unassigned],
            route=self.day_stats[-1])

class ExecutionPlanner(ExecutionMixin,AdaptivePlanner):
    pass

_players={};_beliefs={}
def agent(observation,configuration=None):
    step=int(observation['step']);p=int(observation['player'])
    if step==0:_players.pop(p,None);_beliefs[p]=InventoryBelief()
    belief=_beliefs.setdefault(p,InventoryBelief());belief.update(observation)
    obs=dict(observation);obs['_inventory_belief']=belief.snapshot()
    if step<360:action=_opening(observation,configuration)
    else:
        if p not in _players or step==360:_players[p]=ExecutionPlanner()
        action=_players[p].act(obs,configuration)
        if step%24==0:PORTFOLIO_REPORT['days'][-1]['inventory_belief']=belief.snapshot()
    belief.remember(observation,action)
    return action
