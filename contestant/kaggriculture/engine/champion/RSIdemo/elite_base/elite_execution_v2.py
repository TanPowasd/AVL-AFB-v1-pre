# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Preserve complete dispatches and carry day certificates across job ordering.

The normal complete 20000-evaluation dispatch is deliberately unchanged. Only
an incomplete dispatch consults a cached day witness or another job ordering.
Cached routes are reconstructed against actual workers, stock and deadlines.
Financial projections include exact own price impact, not unknown rival orders.
"""
import copy
import json
import math
from collections import Counter, defaultdict, deque
from .route_columns import solve_routes
from .joint_v2 import PORTFOLIO_REPORT, fibcost
from .joint_adaptive import AdaptivePlanner, InventoryBelief, _opening
from .elite_execution import ExecutionMixin as PreviousExecutionMixin
from . import gpt6_rules as rules


def job_key(job):
    return json.dumps(job,sort_keys=True,separators=(',',':'))


def plan_key(jobs,crew,delivery):
    return crew,bool(delivery),tuple(sorted(job_key(j) for j in jobs))


def own_trade(market,item,quantity,sell):
    """Exact own-only proceeds/cost, including the floor's no-stock-increment."""
    inventory=market['inventory'][item];total=0
    for _ in range(quantity):
        price=rules.market_price(item,inventory if sell else inventory-1,market.get('params'))
        total+=price
        if sell:
            if price>1:inventory+=1
        else:inventory-=1
    return total,inventory


def remap_routes(result,source_jobs,target_jobs):
    indices=defaultdict(deque)
    for i,j in enumerate(target_jobs):indices[job_key(j)].append(i)
    mapping={}
    for i,j in enumerate(source_jobs):
        choices=indices[job_key(j)]
        if not choices:return None
        mapping[i]=choices.popleft()
    if len(mapping)!=len(target_jobs):return None
    converted=copy.deepcopy(result)
    converted['routes']=[[mapping[i] for i in r] for r in result['routes']]
    converted['unassigned']=[mapping[i] for i in result['unassigned']]
    return converted


def validate_complete(result,jobs,workers,shed,last_hour=23,**kwargs):
    """Rebuild pickups and route timings; reject a stale complete witness.

This checks the route solver's material/time contract. Crop state and seed
prerequisites remain the dispatcher's responsibility, as in the base planner.
"""
    if result['unassigned']:return None,'incomplete witness'
    if len(result['routes'])!=len(workers):return None,'worker count changed'
    flat=[j for route in result['routes'] for j in route]
    if sorted(flat)!=list(range(len(jobs))):return None,'job coverage changed'
    returning=kwargs.get('return_to_shed',False)
    selective=kwargs.get('selective_return',False)
    delivery=kwargs.get('delivery_jobs')
    pickups=[];finishes=[];reserved=Counter();pickup_positions=[];end_positions=[]
    def distance(a,b):return abs(a[0]-b[0])+abs(a[1]-b[1])
    for wi,route in enumerate(result['routes']):
        worker=workers[wi];flow=Counter();required=Counter()
        for ji in route:
            for op in jobs[ji]['ops']:
                item=None;delta=0
                if op[0]=='FEED':item,delta='WHEAT',-1
                elif op[0]=='FERTILIZE':item,delta='FERTILIZER',-1
                elif op[0]=='PLACE' and op[1] in rules.ANIMALS:item,delta=op[1],-1
                elif op[0]=='COLLECT_FERTILIZER' and kwargs.get('track_collect'):item,delta='FERTILIZER',1
                if item:
                    flow[item]+=delta;required[item]=max(required[item],-flow[item])
            flow.update(jobs[ji].get('supply',{}))
        inv=worker.get('inv',{})
        take={k:n-inv.get(k,0) for k,n in required.items() if n>inv.get(k,0)}
        reserved.update(take)
        pos=tuple(worker['pos']);home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
        clock=worker.get('hour',0)
        if take:clock+=distance(pos,home)+len(take);pos=home
        pickup_positions.append(pos)
        for ji in route:
            job=jobs[ji];clock+=distance(pos,job['pos'])+len(job['ops']);pos=tuple(job['pos'])
            if clock>min(last_hour,job.get('deadline',last_hour))+1:return None,'job deadline exceeded'
        cargo=any(inv.values()) or any((ji in delivery if delivery is not None else any(op[0]=='HARVEST' or last_hour==22 and op[0]=='COLLECT_FERTILIZER' for op in jobs[ji]['ops'])) for ji in route)
        if returning and (route or any(inv.values())) and (not selective or cargo):
            home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
            clock+=distance(pos,home)+kwargs.get('end_service',1);pos=home
        if clock>last_hour+1:return None,'route horizon exceeded'
        pickups.append(take);finishes.append(clock);end_positions.append(pos)
    if any(n>shed.get(k,0) for k,n in reserved.items()):return None,'actual stock insufficient'
    accepted=copy.deepcopy(result)
    accepted.update(pickups=pickups,finish_hours=finishes,pickup_positions=pickup_positions,end_positions=end_positions)
    accepted.setdefault('stats',{})['reserved']=dict(reserved)
    return accepted,'actual workers, stock and deadlines validated'


class ExecutionMixin(PreviousExecutionMixin):
    def __init__(self):
        super().__init__()
        self._certificates=[];self._coverage_records={};self._dispatch_checks=[]
        self.solve=self._execution_solve

    def _remember(self,jobs,crew,result,uncovered):
        key=plan_key(jobs,crew,self.delivery)
        # A later pack sees only retained jobs. Preserve any original penalty
        # for optional projects already discarded by _certify.
        self._coverage_records.setdefault(key,uncovered)
        if not result['unassigned']:
            self._certificates.append(dict(key=key,jobs=copy.deepcopy(jobs),result=copy.deepcopy(result)))

    def _requirements(self):
        super()._requirements()
        if hasattr(self,'crew') and hasattr(self,'delivery'):
            key=plan_key(self.jobs,self.crew,self.delivery)
            if key in self._coverage_records:self.certification_uncovered=self._coverage_records[key]

    def begin_day(self,obs):
        self._certificates=[];self._coverage_records={};self._dispatch_checks=[]
        super().begin_day(obs)
        block=getattr(self,'block_route_certificate',None)
        if block and block.get('day')==self.day and block.get('crew')==self.crew and block.get('delivery')==self.delivery and block.get('start_hour')==2:
            if plan_key(block['jobs'],self.crew,self.delivery)==plan_key(self.jobs,self.crew,self.delivery):
                self._remember(block['jobs'],self.crew,block['result'],0)
        self._requirements()
        PORTFOLIO_REPORT['days'][-1]['certificate_contract']=dict(
            uncovered=self.certification_uncovered,
            complete_witnesses=sum(c['key']==plan_key(self.jobs,self.crew,self.delivery) for c in self._certificates))

    def _certify(self,obs):
        # Same incumbent selection as CertifiedPlanner, retaining its witness.
        resources=Counter(obs['private']['shed'])
        for k,n in self.want_resources.items():resources[k]=max(resources[k],n)
        positions=[(4,4)]
        for _ in range(15):positions.append(min(((4,4),(5,4),(4,5),(5,5)),key=positions.count))
        lower=max(1,math.ceil((sum(len(j['ops']) for j in self.jobs)+len(self.jobs))/22)-2)
        best=None
        for hands in range(max(1,min(lower,15)),16):
            workers=[dict(pos=p,hour=2,inv={}) for p in positions[:hands+1]]
            result=solve_routes(self.jobs,workers,dict(resources),last_hour=22 if self.day==29 else 23,
                track_collect=True,evaluation_budget=6000,return_to_shed=self.delivery,
                end_service=3 if self.delivery else 1,selective_return=True,delivery_jobs=self.delivery_set(self.jobs))
            lost=sum(self.jobs[j]['priority']+(5000 if self.jobs[j]['mandatory'] else 0) for j in result['unassigned'])
            score=fibcost(hands)+lost
            if best is None or score<best[0]:best=score,hands,result
            if not result['unassigned']:break
        self.crew=best[1];removed=set(best[2]['unassigned'])
        self.certification_uncovered=sum(self.jobs[j]['priority']+(5000 if self.jobs[j]['mandatory'] else 0) for j in removed)
        original=copy.deepcopy(self.jobs)
        self.jobs=[j for i,j in enumerate(self.jobs) if i not in removed or j['mandatory']]
        self._coverage_records[plan_key(self.jobs,self.crew,self.delivery)]=self.certification_uncovered
        if not removed:self._remember(original,self.crew,best[2],0)

    def pack_services(self,obs,jobs,hands):
        result=super().pack_services(obs,jobs,hands)
        lost=sum(jobs[i]['priority']+(5000 if jobs[i]['mandatory'] else 0) for i in result['unassigned'])
        self._remember(jobs,hands,result,lost)
        return result

    def _execution_solve(self,jobs,workers,shed,*args,**kwargs):
        baseline=solve_routes(jobs,workers,shed,*args,**kwargs)
        if not baseline['unassigned']:
            baseline['stats']['coverage_guard']='original_complete_preserved'
            self._dispatch_checks.append(dict(source='original',accepted=True,missing=0))
            return baseline
        checks=[dict(source='original',accepted=False,missing=len(baseline['unassigned']))]
        for witness in reversed(self._certificates):
            if witness['key']!=plan_key(jobs,len(workers)-1,self.delivery):continue
            remapped=remap_routes(witness['result'],witness['jobs'],jobs)
            valid,reason=validate_complete(remapped,jobs,workers,shed,*args,**kwargs)
            checks.append(dict(source='cached_day_certificate',accepted=valid is not None,reason=reason))
            if valid is not None:
                valid['stats']['coverage_guard']='cached_certificate_revalidated';self._dispatch_checks.extend(checks)
                return valid
        candidates=[baseline]
        low=dict(kwargs);low['evaluation_budget']=min(6000,kwargs.get('evaluation_budget',6000))
        # Original job order can carry a full certificate that priority sorting lost.
        original=self.jobs
        if len(original)==len(jobs) and sorted(map(job_key,original))==sorted(map(job_key,jobs)):
            original_args=dict(low);original_args['delivery_jobs']=self.delivery_set(original)
            packed=solve_routes(original,workers,shed,*args,**original_args)
            candidates.append(remap_routes(packed,original,jobs))
        if low.get('evaluation_budget')!=kwargs.get('evaluation_budget'):
            candidates.append(solve_routes(jobs,workers,shed,*args,**low))
        for candidate in candidates[1:]:
            if candidate['unassigned']:continue
            valid,reason=validate_complete(candidate,jobs,workers,shed,*args,**kwargs)
            checks.append(dict(source='actual_input_fallback',accepted=valid is not None,reason=reason))
            if valid is not None:
                valid['stats']['coverage_guard']='actual_input_full_fallback';self._dispatch_checks.extend(checks)
                return valid
        def rank(result):
            missing=result['unassigned']
            return (sum(jobs[i]['priority']+5000*jobs[i]['mandatory'] for i in missing),len(missing),sum(result['finish_hours']))
        best=min((c for c in candidates if c['unassigned']),key=rank)
        best['stats']['coverage_guard']='no_complete_witness'
        checks.append(dict(source='weighted_incomplete',accepted=True,missing=len(best['unassigned'])))
        self._dispatch_checks.extend(checks)
        return best

    def plan_routes(self,obs,hour):
        super().plan_routes(obs,hour)
        PORTFOLIO_REPORT['days'][-1]['execution_contract']['certificate_checks']=copy.deepcopy(self._dispatch_checks)

    def cash_available(self,obs):
        return obs['farms'][obs['player']]['money']+sum(own_trade(obs['market'],k,n,True)[0]
            for k,n in obs['private']['shed'].items() if k in rules.PRODUCTS)

    def market(self,obs,hour,unit_actions=None):
        if hour>=self.p['setup_hours']:return super().market(obs,hour,unit_actions)
        private=obs['private'];farm=obs['farms'][obs['player']]
        prices=obs['market']['prices'];market=copy.deepcopy(obs['market']);shed=Counter(private['shed'])
        orders=[];cash=farm['money'];buys=[]
        for item in ('WHEAT','FERTILIZER'):
            n=max(0,self.want_resources[item]-shed[item])
            if n:buys.append(('BUY_PRODUCT',item,n,0))
        land=(1000,2000,4000)[len(farm['unlocked_quadrants'])-1] if self.buy_land and len(farm['unlocked_quadrants'])<self.target_quads else None
        for animal,n in self.want_animals.items():
            if n>shed[animal]:buys.append(('BUY_ANIMAL',animal,n-shed[animal],rules.ANIMALS[animal]['cost']))
        for crop,n in self.want_seeds.items():
            if n>private['seeds'].get(crop,0):buys.append(('BUY_SEED',crop,n-private['seeds'].get(crop,0),rules.CROPS[crop]['seed']))
        hired=len(farm['hands'])
        def commitment():
            return sum(own_trade(market,k,n,False)[0] if op=='BUY_PRODUCT' else n*p for op,k,n,p in buys)+(land or 0)+fibcost(self.crew)-fibcost(hired)
        space=sum(n for op,_,n,_ in buys if op!='BUY_SEED')-max(0,100-sum(shed.values()))
        excess={k:max(0,shed[k]-self.want_resources[k]) for k in rules.PRODUCTS}
        while (cash<commitment() or space>0) and len(orders)<10:
            options=[k for k,n in excess.items() if n>0]
            if not options:break
            item=max(options,key=lambda k:(excess[k] if space>0 else own_trade(market,k,excess[k],True)[0],own_trade(market,k,excess[k],True)[0]))
            n=excess[item];proceeds,stock=own_trade(market,item,n,True)
            orders.append(['SELL',item,n]);excess[item]=0;shed[item]-=n;space-=n;cash+=proceeds;market['inventory'][item]=stock
        while hired<self.crew and len(orders)<10:
            price=fibcost(hired+1)-fibcost(hired)
            if cash<price:break
            orders.append(['HIRE']);hired+=1;cash-=price
        for op,item,n,price in buys:
            if len(orders)>=10:break
            if land and op!='BUY_PRODUCT' and cash>=land:
                orders.append(['BUY_LAND']);cash-=land;land=None
                if len(orders)>=10:break
            if op!='BUY_SEED':n=min(n,max(0,100-sum(shed.values())))
            if op=='BUY_PRODUCT':
                bought=0;spent=0;stock=market['inventory'][item]
                for _ in range(n):
                    unit=rules.market_price(item,stock-1,market.get('params'))
                    if spent+unit>cash:break
                    spent+=unit;stock-=1;bought+=1
                n=bought;market['inventory'][item]=stock
            else:n=max(0,min(n,int(cash/max(1,price))));spent=n*price
            if n:
                orders.append([op,item,n]);cash-=spent
                if op!='BUY_SEED':shed[item]+=n
        if land and len(orders)<10 and cash>=land:orders.append(['BUY_LAND'])
        for item in sorted(excess,key=lambda k:-excess[k]*prices[k]):
            if excess[item] and len(orders)<10:orders.append(['SELL',item,excess[item]])
        return orders


ExecutionV2Mixin=ExecutionMixin


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
