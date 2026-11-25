"""Recombine generated lifecycle columns; ideas from submission_cab6aa76 elite_block.

Local/annealed exploration shares the same proposal generator. Return only the
best completed feasible portfolio, including original state as incumbent.
"""
from collections import Counter
import math
import random
from . import budget
from .neighborhood import clone
from .daily import needs
from .route_validation import validate_solution


def search(model,evaluations=160,temperature=0):
    rng=random.Random(17+model.day)
    options={pos:list(pool.values()) for pos,pool in model.column_pool.items() if len(pool)>1}
    positions=sorted(options)
    if not positions:return model,Counter()
    current=model;best=model;stats=Counter()
    # Favor lower capital commitments. An expansion needing additional cash
    # belongs in macro investment search rather than this reallocation pass.
    cash_limit=model.upfront_cost()
    for step in range(evaluations):
        if budget.remaining()<.04:stats['budget_stop']+=1;break
        count=min(len(positions),rng.choices((1,2,3,5),(50,30,15,5))[0])
        chosen=rng.sample(positions,count)
        trial=clone(current);changes=0
        for pos in chosen:
            choices=[p for p in options[pos] if p.days!=trial.projects[pos].days]
            if not choices:continue
            trial.install(pos,rng.choice(choices));changes+=1
        if not changes:continue
        stats['proposed']+=1
        if trial.upfront_cost()>cash_limit+1e-6:stats['cash_rejected']+=1;continue
        try:value=trial.objective()
        except budget.BudgetExhausted:stats['budget_stop']+=1;break
        delta=value-current.value
        temp=temperature*(.005**(step/max(1,evaluations-1)))
        draw=rng.random()
        if delta>1e-8 or (temp>1e-8 and draw<math.exp(min(0,delta/temp))):
            trial.value=value;current=trial;stats['accepted']+=1
            stats['downhill']+=int(delta<0)
            if current.value>best.value+1e-8:best=current;stats['improved']+=1
    return best,stats


def certify(model,obs,planner):
    """Construct a complete same-crew route, conditional on buying its inputs.

    This certifies geometry and resource requirements. It does not assert that
    unknown simultaneous opponent trades will leave exact purchase prices.
    """
    jobs=[];resources=Counter(obs['private']['shed'])
    for pos,p in model.projects.items():
        ops=[list(op) for op in p.first(model.day)[1]]
        if ops:jobs.append(dict(pos=pos,ops=ops,mandatory=True,priority=1))
    required=Counter()
    for job in jobs:required.update(needs(job['ops']))
    for k,n in required.items():resources[k]=max(resources[k],n)
    positions=[(4,4)]
    for _ in range(planner.crew):positions.append(min(((4,4),(5,4),(4,5),(5,5)),key=positions.count))
    workers=[dict(pos=p,hour=planner.p['setup_hours'],inv={}) for p in positions]
    last_hour=22 if model.day==planner.end else 23
    cfg=dict(track_collect=True,return_to_shed=model.day==planner.end,evaluation_budget=6000)
    result=planner.bounded_routes(jobs,workers,dict(resources),last_hour=last_hour,**cfg)
    return not result['unassigned'] and validate_solution(jobs,workers,resources,result,last_hour,**cfg)
