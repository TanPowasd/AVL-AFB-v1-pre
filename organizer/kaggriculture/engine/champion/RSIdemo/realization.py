"""One-day policy rollout followed by a common finite-horizon tail value.

All candidates use the same micro executor and public-cohort rival cash flow.
No hidden opponent stock/actions or future shops enter the rollout. Rival
daily flows are timing assumptions, recorded explicitly in diagnostics.
"""
from collections import Counter
import copy
from types import SimpleNamespace
from time import perf_counter

from . import budget,rules
from .daily import Planner
from .forecast import cohort_supply
from .portfolio import Portfolio

PLAN_FIELDS=('jobs','crew','want_seeds','want_animals','want_resources','buy_land','target_quads','herd_count')


class Attr(dict):
    def __getattr__(self,name):
        try:return self[name]
        except KeyError:raise AttributeError(name) from None
    __setattr__=dict.__setitem__


def snapshot(planner):
    return {key:copy.deepcopy(getattr(planner,key)) for key in PLAN_FIELDS}


def install(planner,plan):
    for key,value in plan.items():setattr(planner,key,copy.deepcopy(value))


def initial_state(obs):
    # The real private opponent observation is never available to the agent.
    # It starts with synthetic storage used solely for public-cohort arrivals.
    farms=copy.deepcopy(obs['farms']);market=copy.deepcopy(obs['market']);town=copy.deepcopy(obs['town'])
    private=copy.deepcopy(obs['private'])
    opponent=rules._new_private()
    opponent['inventories']=[{} for _ in range(1+len(farms[1-obs['player']]['hands']))]
    return [SimpleNamespace(observation=Attr(step=obs['step'],day=obs['day'],hour=obs['hour'],player=p,
                farms=farms,market=market,town=town,private=private if p==obs['player'] else opponent,
                remainingOverageTime=obs.get('remainingOverageTime',60)),action={},status='ACTIVE',reward=0)
            for p in (0,1)]


def rival_orders(states,seat,flows,day,hour):
    """Spread a public daily-flow hypothesis across the remaining clock."""
    out=[];state=states[seat];stock=state.observation.private['shed']
    for item in rules.PRODUCTS:
        amount=int(round(flows[item][day]))
        # Prefix integer allocation is deterministic and identical across all
        # candidate comparisons, including items with negative (buy) flow.
        n=abs(amount)*(hour+1)//24-abs(amount)*hour//24
        if not n:continue
        if amount>0:
            stock[item]=stock.get(item,0)+n;out.append(['SELL',item,n])
        elif item in ('WHEAT','FERTILIZER'):
            # Consumption happens off-market; avoid synthetic stock piling up
            # and preventing later purchases through an invented capacity cap.
            stock[item]=0;out.append(['BUY_PRODUCT',item,n])
    return out


def advance(states,cfg,step):
    for p,state in enumerate(states):
        private=state.observation.private;farm=states[0].observation.farms[p]
        units=[state.action.get('farmer',['PASS']),*state.action.get('hands',[])]
        seeds=Counter(op[1] for op in units if len(op)>1 and op[0]=='PLANT')
        blocked={k for k,n in seeds.items() if n>private['seeds'].get(k,0)}
        for worker,op in enumerate(units):
            if len(op)>1 and op[0]=='PLANT' and op[1] in blocked:continue
            rules._apply_unit_action(farm,private,worker,op,cfg.boardSize,step//cfg.turnsPerDay,cfg.turnsPerDay,cfg.shedCapacity)
    env=SimpleNamespace(configuration=cfg)
    rules._process_market(states,env)
    rules._town_consume(env,states,step)
    for farm in states[0].observation.farms:rules._decay_plants(farm,step)


def dawn(states,cfg,day,rival):
    for p,state in enumerate(states):
        farm=state.observation.farms[p]
        # Unknown rival care is an explicit physical hypothesis, never read
        # from private data. Held output was assigned to synthetic deliveries.
        if p==rival:
            for row in farm['tiles']:
                for tile in row:
                    if not isinstance(tile,dict):continue
                    if tile.get('kind')=='PLANT':tile['watered_today']=True;tile['yield_units']=0
                    elif 'animal' in tile:tile.update(fed_today=True,cared_today=True,yield_units=0)
        rules._daily_refresh_plants(farm,day,cfg.turnsPerDay)
        rules._daily_refresh_animals(farm,day)
        rules._drop_inventories_to_shed(state.observation.private,cfg.shedCapacity)
        farm.update(farmer=list(rules._default_spawn(cfg.boardSize)),hands=[],hires_today=0)
        state.observation.private['inventories']=[{}]
        state.observation.update(step=(day+1)*cfg.turnsPerDay,day=day+1,hour=0)
    # Do not sample a secretly known future shop or arbitrary game seed. The
    # tail market model uses explicit future-shop scenarios from this prefix.


def evaluate_day(planner,obs,plan,flows):
    from .strategy import Strategy
    budget.check()
    cfg=Attr(dict(boardSize=10,turnsPerDay=24,episodeSteps=720,shedCapacity=100,maxMarketOrdersPerTurn=10,
                  farmHandCostMult=1,marketParams={},townShopSellInterval=4,townCenterSellInterval=24,
                  townShopUnlockInterval=3,**{k:v for k,v in planner.cfg.items() if k not in (
                    'boardSize','turnsPerDay','episodeSteps','shedCapacity','maxMarketOrdersPerTurn','farmHandCostMult',
                    'marketParams','townShopSellInterval','townCenterSellInterval','townShopUnlockInterval')}))
    cfg.update(planner.cfg)
    proxy=Strategy(dict(planner.p,realization=False))
    proxy.cfg=cfg;proxy.day=obs['step']//cfg.turnsPerDay;proxy.end=planner.end
    proxy.return_deadline=planner.return_deadline;proxy.plan_ready=False
    install(proxy,plan)
    states=initial_state(obs);player=obs['player'];rival=1-player
    start_money=[farm['money'] for farm in obs['farms']]
    for step in range(obs['step'],min((proxy.day+1)*cfg.turnsPerDay,cfg.episodeSteps-1)):
        budget.check()
        own=states[player].observation
        own.update(step=step,day=step//cfg.turnsPerDay,hour=step%cfg.turnsPerDay)
        action=Planner.act(proxy,own,cfg)
        states[player].action=proxy.finish_action(own,action)
        states[rival].action={'market':rival_orders(states,rival,flows,proxy.day,step%cfg.turnsPerDay)}
        advance(states,cfg,step)
    if proxy.day<planner.end:dawn(states,cfg,proxy.day,rival)
    final=states[player].observation
    cash=final.farms[player]['money']-final.farms[rival]['money']
    if proxy.day==planner.end:tail=0
    else:
        budget.check()
        positions=[(x,y) for y,row in enumerate(final.farms[player]['tiles']) for x,t in enumerate(row) if t!='LOCKED']
        tail=Portfolio(final,positions).value
    return {'score':cash+tail,'cash_gap':cash,'tail_estimate':tail,
            'own_cash_delta':final.farms[player]['money']-start_money[player],
            'rival_cash_delta':final.farms[rival]['money']-start_money[rival],
            'unassigned':proxy.metrics.get('unassigned_jobs',0),
            'prevented_noops':proxy.metrics.get('prevented_noops',0)}


def choose(planner,obs,plans):
    day=obs['step']//24
    if day>=planner.end:return None,[]
    flows=cohort_supply(obs['farms'][1-obs['player']],day,.85,1.)
    records=[];best=None
    for name,plan in plans:
        if budget.remaining()<.1:return None,records
        try:record=evaluate_day(planner,obs,plan,flows)
        except budget.BudgetExhausted:return None,records
        record['name']=name;records.append(record)
        if best is None or record['score']>best[0]:best=(record['score'],name,plan)
    # Require a fully completed common horizon for every candidate.
    return (best[1],best[2]) if best else None,records
