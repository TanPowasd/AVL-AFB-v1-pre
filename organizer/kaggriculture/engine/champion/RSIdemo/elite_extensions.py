"""Observation-reconciled continuation on the supplied elite portfolio engine."""
from collections import Counter
import copy
from time import perf_counter
from .elite_base import gpt6_rules as rules
from .elite_base.project_model import Project,animal_project
from .elite_base.portfolio import shadows
from .elite_base.elite_block import RecordingPortfolio
from .elite_base.joint_adaptive import AdaptiveBook
from .elite_base.gpt6_policy import sd,moves,dist


class DelayedSalesBook(AdaptiveBook):
    """Explicit delivery scenario for goods that cannot be bought as inputs.

    Initial warehouse stock still sells today. Only new project outputs are
    partly shifted; fertilizer and feed retain the original dated net flows.
    The final day's mandatory return certificate retains same-day delivery.
    """
    def __init__(self,obs):
        self.delivery_lag=float(obs.get('_rsi_params',{}).get('elite_sales_lag',0))
        selector=self.residual_selector
        if obs.get('_rsi_params',{}).get('elite_replay_supply'):
            from .elite_replay_bank import select
            selector=select
        shadow=obs.get('_rsi_shadow')
        if shadow is not None:
            from .elite_shadow import augment
            self.residual_selector=lambda current,p,limit=6:augment(selector(current,p,limit),shadow)
        else:self.residual_selector=selector
        super().__init__(obs)

    def component(self,item,flows,want_gradient=False):
        lag=self.delivery_lag if item not in ('WHEAT','FERTILIZER') else 0
        if not lag:return super().component(item,flows,want_gradient)
        sales=list(flows)
        for d in range(self.day,29):
            sales[d]-=lag*flows[d];sales[d+1]+=lag*flows[d]
        values,gradients=super().component(item,sales,want_gradient)
        if not want_gradient:return values,None
        mapped=[g[:] for g in gradients]
        for target,g in zip(mapped,gradients):
            for d in range(self.day,29):target[d]=(1-lag)*g[d]+lag*g[d+1]
        return values,mapped


def remember(obs, projects):
    """Predict only local physical state, then require exact next-day agreement.

    Proposed work may fail during actual routing/procurement. Such columns are
    discarded on the next observation instead of treating a proposal as fact.
    """
    farm=copy.deepcopy(obs['farms'][obs['player']]); private=rules._new_private()
    day=obs['step']//24; columns={}
    for pos,project in projects.items():
        x,y=pos
        if farm['tiles'][y][x]=='LOCKED':continue
        farm['farmer']=[x,y];private['inventories']=[{}]
        private['seeds']={k:0 for k in rules.CROPS}
        for key,n in project.first(day)[3]:
            if key.startswith('SEED_'):private['seeds'][key[5:]]=n
            elif key in rules.PRODUCTS or key in rules.ANIMALS:private['inventories'][0][key]=n
        for raw in project.first(day)[1]:
            rules._apply_unit_action(farm,private,0,list(raw),10,day,24)
        columns[pos]=Project(project.value,tuple(row for row in project.days if row[0]>day))
    rules._decay_plants(farm,day*24+23)
    rules._daily_refresh_plants(farm,day,24)
    rules._daily_refresh_animals(farm,day)
    return {'day':day+1,'columns':columns,
            'expected':{p:copy.deepcopy(farm['tiles'][p[1]][p[0]]) for p in columns}}


class ContinuedPortfolio(RecordingPortfolio):
    book_type=DelayedSalesBook
    def __init__(self,obs,positions,previous=None):
        self.rsi_params=obs.get('_rsi_params',{})
        self.rsi_deadline=obs.get('_rsi_optional_deadline',float('inf'))
        self.continuation_stats=Counter()
        self.refinement_log=[]
        self.extra_pass_count=0
        self.extra_phase_counts=Counter()
        self.animal_land_reuse=bool(self.rsi_params.get('elite_animal_reuse'))
        self.paid_stock=Counter();self.investment=Counter()
        if self.rsi_params.get('elite_paid_assets'):
            self.paid_stock.update({'SEED_'+k:n for k,n in obs['private']['seeds'].items() if n})
            for stock in [obs['private']['shed'],*obs['private']['inventories']]:
                self.paid_stock.update({a:n for a,n in stock.items() if a in rules.ANIMALS and n})
        super().__init__(obs,positions,previous)
        saved=obs.get('_rsi_continuation')
        if self.rsi_params.get('elite_continuation') and saved and saved['day']==self.day:
            for pos,project in saved['columns'].items():
                if pos not in self.projects:continue
                if self.farm['tiles'][pos[1]][pos[0]]!=saved['expected'][pos]:
                    self.continuation_stats['rejected_state']+=1;continue
                self.continuation_stats['reconciled']+=1
                if self.offer(pos,project):self.continuation_stats['accepted']+=1

    def optimize(self,budget,only_positions=None,sweeps=2):
        result=super().optimize(budget,only_positions,sweeps)
        # Optional local refinement yields between complete passes. Baseline
        # planning/certification still finishes, even when this allowance ends.
        for _ in range(int(self.rsi_params.get('elite_extra_sweeps',0))):
            if perf_counter()>=self.rsi_deadline:break
            phase=getattr(self,'refinement_phase','unspecified')
            if self.rsi_params.get('elite_stage_refinement') and (
                phase=='land_trial' or not getattr(self,'refinement_trigger',False)
                or self.extra_phase_counts[phase]>=1):break
            cap=self.rsi_params.get('elite_extra_daily_cap')
            schedule=self.rsi_params.get('elite_extra_cap_schedule')
            if schedule:
                for until,limit in schedule:
                    if self.day<=int(until):cap=int(limit);break
            if cap is not None and self.extra_pass_count>=int(cap):break
            old=self.value; accepted=self.accepted
            self.offer_minimum_gain=max(1,float(self.rsi_params.get('elite_extra_min_gain',1)))
            try:result=super().optimize(budget,only_positions,1)
            finally:self.offer_minimum_gain=1
            self.extra_pass_count+=1
            self.extra_phase_counts=self.extra_phase_counts.copy();self.extra_phase_counts[phase]+=1
            self.refinement_log=self.refinement_log+[dict(day=self.day,pass_number=self.extra_pass_count,
                phase=phase,gain=self.value-old,accepted=self.accepted-accepted,only_positions=only_positions is not None)]
            self.continuation_stats=self.continuation_stats.copy()
            self.continuation_stats['extra_passes']+=1
            if self.accepted==accepted or self.value-old<25:break
        if self.paid_stock:self.recover_paid_animals(only_positions)
        return result

    def capital_credit(self):
        return sum(min(max(0,n),self.paid_stock[k])*(rules.CROPS[k[5:]]['seed']
            if k.startswith('SEED_') else rules.ANIMALS[k]['cost'])
            for k,n in self.investment.items() if self.paid_stock[k]>0)

    def objective(self):return super().objective()+self.capital_credit()

    def recover_paid_animals(self,only_positions):
        animals=[a for a in rules.ANIMALS if self.paid_stock[a]>self.investment[a]]
        if not animals:return
        prices=self.book.marginal(self.flow)
        labor=[v*m for v,m in zip(shadows(self.priced_work()),self.labor_multiplier)]
        positions=sorted(self.projects if only_positions is None else only_positions,key=lambda p:(sd(p),p))
        positions=[p for p in positions if not isinstance(self.farm['tiles'][p[1]][p[0]],dict)
            or ('animal' not in self.farm['tiles'][p[1]][p[0]] and self.farm['tiles'][p[1]][p[0]].get('kind')!='PLANT')][:12]
        for animal in animals:
            for pos in positions:
                if self.investment[animal]>=self.paid_stock[animal]:break
                tile=self.farm['tiles'][pos[1]][pos[0]]
                project=animal_project(animal,self.day,self.day,rules._new_animal(animal,self.day),prices,labor,[2+sd(pos)/3]*30)
                first=project.first(self.day);structure=rules.ANIMALS[animal]['structure']
                build=() if isinstance(tile,dict) and tile.get('kind')==structure else ((('DIG',),) if tile not in (None,'LOCKED') else ())+(('BUILD_'+structure,),)
                candidate=Project(project.value-rules.ANIMALS[animal]['cost'],
                    ((self.day,build+(('PLACE',animal),)+first[1],first[2],((animal,1),)+first[3]),)
                    +tuple(row for row in project.days if row[0]!=self.day))
                if self.offer(pos,candidate):self.continuation_stats['paid_animal_recovered']+=1


def repair_idle(planner,obs,action):
    """Use genuinely empty worker queues; never displace scheduled service.

    Ordinary days allow a drop on hour 23. Last day ends after action hour 22,
    so opportunistic harvests must still have a complete return and sale.
    """
    day,hour=divmod(int(obs['step']),24)
    farm=copy.deepcopy(obs['farms'][obs['player']]);private=copy.deepcopy(obs['private'])
    units=copy.deepcopy([action['farmer'],*action['hands']]);changed=False
    for i,cmd in enumerate(units):
        idle=cmd==['PASS'] and (i>=len(planner.queues) or not planner.queues[i])
        pos=([farm['farmer']]+farm['hands'])[i];inv=private['inventories'][i]
        home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
        if idle and day<29 and any(inv.values()) and hour+dist(pos,home)+1<=24:
            path=moves(pos,home);cmd=path[0] if path else ['DROP']
        if idle and day==29 and 20<=hour and hour+dist(pos,home)+2<=23:
            tile=farm['tiles'][pos[1]][pos[0]]
            if isinstance(tile,dict):
                ready=tile.get('yield_units',0)>0 and ('animal' in tile or
                    (tile.get('kind')=='PLANT' and day-tile['planted_day']>=rules.CROPS[tile['crop']]['first_yield_day']))
                if ready:cmd=['HARVEST']
                elif tile.get('fertilizer_available') and 'animal' in tile:cmd=['COLLECT_FERTILIZER']
        if cmd!=units[i]:
            if cmd==['DROP']:
                room=max(0,100-sum(private['shed'].values()))
                if sum(inv.values())>room:
                    items=[k for k,n in inv.items() if n>0 and k in rules.PRODUCTS]
                    if not room or not items:cmd=['PASS']
                    else:
                        key=max(items,key=lambda k:obs['market']['prices'][k]);cmd=['PLACE',key,min(room,inv[key])]
            if cmd!=units[i]:
                changed=True;planner.metrics['idle_repair_'+cmd[0]]+=1
        units[i]=cmd
        rules._apply_unit_action(farm,private,i,cmd,10,day,24)
    if not changed:return action
    return dict(farmer=units[0],hands=units[1:],market=planner.market(obs,hour,units))
