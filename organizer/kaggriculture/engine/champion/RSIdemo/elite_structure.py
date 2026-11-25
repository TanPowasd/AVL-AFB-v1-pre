"""Demand-guided whole-farm starts, refined by the original local optimizer.

Biased prices only generate proposals. The unmodified common market book,
actual procurement and full route certification decide acceptance.
"""
from collections import Counter
from time import perf_counter

from .elite_base import gpt6_rules as rules
from .elite_base.elite_block import clone_model, today_cost
from .elite_base.market_model import demand, cohort_supply
from .elite_base.portfolio import Portfolio, resources, shadows, sd
from .elite_base.project_model import CropDP, Project, animal_project


def isolated_clone(model):
    trial=clone_model(model)
    trial.labor_multiplier=model.labor_multiplier[:]
    trial.column_pool={p:dict(pool) for p,pool in model.column_pool.items()}
    for field in ('continuation_stats','extra_phase_counts','investment'):
        if hasattr(model,field):setattr(trial,field,getattr(model,field).copy())
    if hasattr(model,'refinement_log'):trial.refinement_log=list(model.refinement_log)
    return trial


def focuses(model, obs):
    """Rank observed demand relative to existing public production capacity."""
    known=demand(obs['town']['unlocked_shops']);day=model.day
    farm_supply=[cohort_supply(f,day,1,0) for f in obs['farms']]
    prices=model.book.marginal(model.flow)
    def priority(item,delay):
        days=range(day+delay,30)
        if not days:return float('-inf')
        q=sum(known[item]-sum(f[item][d] for f in farm_supply) for d in days)
        # Capacity shortfall is a proposal heuristic, never booked as revenue.
        return (max(0,q)+known[item])*max(1,sum(prices[item][d] for d in days)/len(days))/(1+delay)
    crops=[k for k,v in rules.CROPS.items() if day+v['first_yield_day']<30 and known[k]>0]
    crops=sorted(crops,key=lambda k:(-priority(k,rules.CROPS[k]['first_yield_day']),k))[:2]
    animals=[]
    if day<=18:
        animals=[k for k,v in rules.ANIMALS.items() if day+v['first_yield_day']<30 and known[v['product']]>0]
        animals=sorted(animals,key=lambda k:(-priority(rules.ANIMALS[k]['product'],rules.ANIMALS[k]['first_yield_day']),k))[:1]
    return [('crop',k) for k in crops]+[('animal',k) for k in animals]


def animal_candidate(model, pos, animal, prices, labor):
    tile=model.farm['tiles'][pos[1]][pos[0]];day=model.day
    if sd(pos)>4 or tile=='LOCKED':return None
    if isinstance(tile,dict) and ('animal' in tile or tile.get('crop') in ('STRAWBERRY','TOMATO','MELON')):
        return None
    prefix=();extra_output=()
    if isinstance(tile,dict) and tile.get('kind')=='PLANT':
        crop=tile['crop'];cd=rules.CROPS[crop]
        if tile.get('yield_units',0)>0 and day-tile['planted_day']>=cd['first_yield_day']:
            prefix=(('HARVEST',),);extra_output=((crop,tile['yield_units']),)
        else:prefix=(('DIG',),)
    elif tile is not None:prefix=(('DIG',),)
    base=animal_project(animal,day,day,rules._new_animal(animal,day),prices,labor,[2+sd(pos)/3]*30)
    row=base.first(day);spec=rules.ANIMALS[animal]
    first=(day,prefix+(('BUILD_'+spec['structure'],),('PLACE',animal))+row[1],
           extra_output+row[2],((animal,1),)+row[3])
    return Project(base.value-spec['cost'],(first,)+tuple(r for r in base.days if r[0]!=day))


def capital(model):return sum(today_cost(p,model.day) for p in model.projects.values())


def candidates(model, obs, budget, deadline):
    started=perf_counter();day=model.day;report={'generated':0,'refined':0,'branches':[]}
    prices=model.book.marginal(model.flow)
    labor=[v*m for v,m in zip(shadows(model.priced_work()),model.labor_multiplier)]
    raw=[];seen=set()
    for kind,focus in focuses(model,obs):
        if perf_counter()>=deadline:break
        item=focus if kind=='crop' else rules.ANIMALS[focus]['product']
        biased={k:v[:] for k,v in prices.items()}
        multiplier=1.35
        signals=obs.get('_rsi_price_signals',{})
        if obs.get('_rsi_params',{}).get('elite_price_signal'):
            from .price_signal import PriceSignal
            multiplier*=PriceSignal.proposal_multiplier(signals.get(item),
                momentum_weight=float(obs.get('_rsi_params',{}).get('elite_price_momentum_weight',.20)),
                risk_weight=float(obs.get('_rsi_params',{}).get('elite_price_risk_weight',.10)),
                limit=float(obs.get('_rsi_params',{}).get('elite_price_adjustment_limit',.10)))
        biased[item]=[max(1,v)*multiplier for v in biased[item]]
        dp=CropDP(biased,labor,[2.0]*30,start=day) if kind=='crop' else None
        proposals=[]
        for pos,current in sorted(model.projects.items()):
            tile=model.farm['tiles'][pos[1]][pos[0]]
            if tile=='LOCKED' or (isinstance(tile,dict) and 'animal' in tile):continue
            if kind=='animal':project=animal_candidate(model,pos,focus,biased,labor)
            elif isinstance(tile,dict) and tile.get('kind')=='PLANT':project=dp.existing(tile,day)
            else:
                project=dp.free[day]
                if tile is not None and project.first(day)[1]:
                    row=project.first(day)
                    project=Project(project.value,((day,(('DIG',),)+row[1],row[2],row[3]),)
                                    +tuple(r for r in project.days if r[0]!=day))
            if project is None or project.days==current.days:continue
            new=resources(project,pos);old=model.parts[pos]
            rank=sum(biased[k][d]*(new[0][k][d]-old[0][k][d]) for k in biased for d in range(day,30))
            rank-=new[2]-old[2]+sum(labor[d]*(new[1][d]-old[1][d]) for d in range(day,30))
            proposals.append((rank,pos,project))
        proposals.sort(key=lambda row:(-row[0],row[1]))
        for size in ((4,8) if kind=='crop' else (2,4)):
            selected=proposals[:size]
            if not selected:continue
            signature=tuple((pos,p.days) for _,pos,p in selected)
            if signature in seen:continue
            seen.add(signature);trial=isolated_clone(model)
            for _,pos,p in selected:trial.install(pos,p)
            trial.value=trial.objective()
            row=dict(kind=kind,focus=focus,changed=len(selected),raw_gain=trial.value-model.value,
                     capital=capital(trial),gate=None)
            report['branches'].append(row);report['generated']+=1
            if row['capital']>budget:row['gate']='cash';continue
            raw.append((trial.value,trial,row))
    # Keep two economically different starts instead of many near-identical
    # finalists. Even an initially inferior joint move can improve after repair.
    output=[];families=set()
    for _,trial,row in sorted(raw,key=lambda entry:-entry[0]):
        family=(row['kind'],row['focus'])
        if family in families:continue
        if len(families)>=2 or perf_counter()>=deadline:break
        families.add(family)
        Portfolio.optimize(trial,max(0,budget-capital(trial)),sweeps=1)
        report['refined']+=1;row['refined_gain']=trial.value-model.value
        if capital(trial)>budget:row['gate']='refined cash';continue
        row['gate']='route certification pending'
        output.append(trial)
    report['seconds']=perf_counter()-started
    report['returned']=len(output)
    return output,report
