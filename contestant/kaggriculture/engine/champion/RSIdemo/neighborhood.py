"""Local, heuristic, and collective jump proposals for an existing portfolio.

Perturbations change how candidates are generated. Acceptance always uses the
same unperturbed full-portfolio market and labor objective and cash bound.
"""
from collections import Counter
import copy
import random

from .projects import CropDP, Project, animal_project
from .portfolio import shadows,sd
from . import rules,budget


def clone(model):
    trial=copy.copy(model)
    trial.projects=dict(model.projects);trial.parts=dict(model.parts)
    trial.flow={k:v[:] for k,v in model.flow.items()};trial.work=model.work[:]
    trial.labor_multiplier=model.labor_multiplier[:]
    trial.upfront=model.upfront.copy();trial.total_investment=model.total_investment.copy()
    trial.column_pool={pos:dict(options) for pos,options in model.column_pool.items()}
    return trial


def legal_project(model,pos,dp,prices,labor):
    tile=model.farm['tiles'][pos[1]][pos[0]]
    if isinstance(tile,dict) and 'animal' in tile:
        return animal_project(tile['animal'],tile['placed_day'],model.day,tile,prices,labor,[2.0]*30)
    if isinstance(tile,dict) and tile.get('kind')=='PLANT':
        return dp.existing(tile,model.day)
    project=dp.free[model.day]
    if tile not in (None,'LOCKED'):
        row=project.first(model.day)
        if row[1]:project=Project(project.value-labor[model.day],
            ((model.day,(('DIG',),)+row[1],row[2],row[3]),)+tuple(r for r in project.days if r[0]!=model.day))
    return project


def beam_pool(candidates,width=3,tolerance=.10):
    """Keep a bounded set of distinct feasible near-optimal portfolios."""
    if not candidates:return []
    ordered=sorted(candidates,key=lambda c:c.value,reverse=True)
    threshold=ordered[0].value-max(100,abs(ordered[0].value)*tolerance)
    seen=set();pool=[]
    for candidate in ordered:
        if candidate.value<threshold:continue
        signature=tuple((p,candidate.projects[p].days) for p in sorted(candidate.projects))
        if signature in seen:continue
        seen.add(signature);pool.append(candidate)
        if len(pool)>=width:break
    return pool


def perturb(model, kind='jump', attempts=3, width=6):
    """Compare coordinated alternatives; never retain an unfinished jump."""
    stats=Counter(attempts=0,accepted=0,interrupted=0)
    baseline=model
    pool=[model]
    beam_next=None
    # The search seed depends only on public state, never the hidden game seed.
    rng=random.Random(4817+model.day*97+sum(sum(n for _,n in p.first(model.day)[2])
                                        for p in model.projects.values()))
    for attempt in range(attempts):
        if budget.remaining()<.18:break
        stats['attempts']+=1
        try:
            origin=beam_next if kind=='beam' and any(c is beam_next for c in pool) else baseline
            prices=origin.book.marginal(origin.flow)
            labor=[v*m for v,m in zip(shadows(origin.priced_work()),origin.labor_multiplier)]
            if kind=='heuristic':
                # Simple opportunity heuristic: compare finite-lifetime return
                # per visit across crops, then generate a homogeneous subset.
                remaining=29-baseline.day
                options=[]
                for crop,cd in rules.CROPS.items():
                    if cd['first_yield_day']>remaining:continue
                    units=(min(cd['max_yield'],1+(remaining-cd['first_yield_day'])//max(1,cd['interval']))*1.6
                           if cd['ongoing'] else cd['max_yield'])
                    quote=sum(prices[crop][baseline.day:])/max(1,30-baseline.day)
                    value=(units*quote-cd['seed'])/(cd['first_yield_day']+2)
                    options.append((value,crop))
                if not options:break
                options.sort(reverse=True)
                allowed=[options[attempt%len(options)][1]]
            else:
                allowed=list(rules.CROPS)
                factors={k:rng.choice((.65,.85,1.15,1.4)) for k in rules.PRODUCTS}
                prices={k:[v*factors[k] for v in vs] for k,vs in prices.items()}
                labor=[v*rng.choice((.7,1.0,1.4)) for v in labor]
            dp=CropDP(prices,labor,[2.0]*30,start=origin.day,allowed=allowed)
            positions=list(origin.projects)
            if kind=='heuristic':
                positions.sort(key=lambda p:(len(origin.projects[p].first(origin.day)[1]),sd(p),p))
            else:
                anchor=rng.choice(positions)
                # Alternate spatial blocks with scattered moves, so the search
                # can change a production cohort without requiring all single
                # tile exchanges to be improvements.
                if attempt%2==0:positions.sort(key=lambda p:(abs(p[0]-anchor[0])+abs(p[1]-anchor[1]),rng.random()))
                else:rng.shuffle(positions)
            trial=clone(origin)
            changed=0
            for pos in positions[:width]:
                budget.check()
                candidate=legal_project(origin,pos,dp,prices,labor)
                if candidate.days!=trial.projects[pos].days:
                    trial.install(pos,candidate);changed+=1
            if not changed:continue
            if trial.upfront_cost()>max(trial.investment_budget,baseline.upfront_cost()):
                stats['cash_rejected']+=1;continue
            value=trial.objective()
            trial.value=value
            if kind=='beam':
                pool=beam_pool([*pool,trial],3)
                if any(c is trial for c in pool) and value<=baseline.value+1:
                    stats['suboptimal_retained']+=1
                if origin is not baseline:stats['suboptimal_expansions']+=1
                # Deliberately expand a retained alternative next. Cycling by
                # attempt number could repeatedly select only the best node
                # whenever pruning changes pool size.
                alternatives=[c for c in pool if c is not pool[0]]
                beam_next=alternatives[attempt%len(alternatives)] if alternatives else pool[0]
            if value>baseline.value+1:
                trial.value=value;baseline=trial;stats['accepted']+=1
                stats['changed_columns']+=changed
            else:stats['objective_rejected']+=1
        except budget.BudgetExhausted:
            stats['interrupted']+=1
            break
    return baseline,stats
