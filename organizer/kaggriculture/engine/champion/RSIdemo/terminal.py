"""Cash-only terminal-day work: executable harvest, delivery, and liquidation.

No feed, survival, or crop renewal is credited on the last day. Compare real
routes with alternative worker counts; leave warehouse capacity for staggered
deposits and reserve a second deposit turn for partial unload repair.
"""
from collections import Counter, deque
import math
from time import perf_counter
from . import rules, budget
from .daily import fibcost, sd, moves


def jobs_from_observation(obs, end_day):
    jobs=[]
    farm=obs['farms'][obs['player']]
    prices=obs['market']['prices']
    for y,row in enumerate(farm['tiles']):
        for x,tile in enumerate(row):
            if not isinstance(tile,dict):continue
            ops=[];output=Counter()
            held=tile.get('yield_units',0)
            if 'animal' in tile:
                if held:
                    ops.append(['HARVEST']);output[rules.ANIMALS[tile['animal']]['product']]+=held
                if tile.get('fertilizer_available'):
                    ops.append(['COLLECT_FERTILIZER']);output['FERTILIZER']+=1
            elif tile.get('kind')=='PLANT':
                crop=tile['crop'];cd=rules.CROPS[crop];age=end_day-tile['planted_day']
                if held and age>=cd['first_yield_day']:
                    if (not cd['ongoing'] and not tile['watered_today'] and held<cd['max_yield']
                            and (cd['max_yield_day']+1)//2<=age<=cd['max_yield_day']):
                        ops.append(['WATER'])
                        held=min(cd['max_yield'],held+(2 if tile.get('fertilized_until_day',-1)>=end_day else 1))
                    ops.append(['HARVEST']);output[crop]+=held
            if ops:
                gross=sum(n*prices[k] for k,n in output.items())
                jobs.append(dict(pos=(x,y),ops=ops,outputs=dict(output),priority=gross,mandatory=False))
    return jobs


def begin(planner,obs):
    planner.day=planner.end;planner.plan_ready=False;planner.queues=[]
    planner.buy_land=False;planner.target_quads=len(obs['farms'][obs['player']]['unlocked_quadrants'])
    planner.want_seeds=Counter();planner.want_animals=Counter();planner.want_resources=Counter()
    planner.jobs=jobs_from_observation(obs,planner.end)
    positions=[(4,4)]
    max_hands=planner.p.get('terminal_crew_cap',17)
    for _ in range(max_hands):positions.append(min(((4,4),(5,4),(4,5),(5,5)),key=positions.count))
    work=sum(len(j['ops'])+1 for j in planner.jobs)
    lower=max(0,min(max_hands,math.ceil(work/19)-2))
    best=None
    for hands in range(lower,max_hands+1):
        if best and budget.remaining()<.08:break
        # Current shed can pay wages after its first sale on the next callback.
        cash=obs['farms'][obs['player']]['money']+sum(n*obs['market']['prices'].get(k,0)*.75
             for k,n in obs['private']['shed'].items())
        if fibcost(hands)>cash:continue
        workers=[dict(pos=p,hour=planner.p['setup_hours'],inv={}) for p in positions[:hands+1]]
        result=planner.bounded_routes(planner.jobs,workers,{},last_hour=21,
                                     return_to_shed=True,end_service=1,evaluation_budget=6000)
        missed=sum(planner.jobs[i]['priority'] for i in result['unassigned'])
        score=fibcost(hands)+missed
        if best is None or score<best[0]:best=(score,hands)
        if not result['unassigned']:break
    planner.crew=best[1] if best else 0
    planner.metrics['terminal_cash_plans']+=1


def routes(planner,obs,hour):
    farm=obs['farms'][obs['player']];private=obs['private']
    positions=[farm['farmer'],*farm['hands']]
    planner.jobs=jobs_from_observation(obs,planner.end)
    workers=[dict(pos=tuple(p),hour=hour,inv=private['inventories'][i]) for i,p in enumerate(positions)]
    result=planner.bounded_routes(planner.jobs,workers,{},last_hour=21,return_to_shed=True,
                                 end_service=1,evaluation_budget=20000)
    planner.queues=[deque() for _ in workers]
    for i,route in enumerate(result['routes']):
        pos=tuple(positions[i]);queue=planner.queues[i]
        for index in route:
            job=planner.jobs[index]
            queue.extend(moves(pos,job['pos']));pos=job['pos'];queue.extend(job['ops'])
        if route or any(private['inventories'][i].values()):
            home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
            queue.extend(moves(pos,home));queue.append(['DROP'])
    planner.unassigned=[planner.jobs[i] for i in result['unassigned']]
    planner.plan_ready=True
    planner.day_stats.append(dict(day=planner.end,jobs=len(planner.jobs),workers=len(workers),
        unassigned=len(planner.unassigned),assigned=sum(map(len,result['routes'])),
        planned_ops=sum(map(len,planner.queues)),stats=result['stats'],mode='terminal_cash'))
