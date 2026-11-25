"""Insert feasible intraday deliveries into already complete worker routes.

Only add a round trip when all original work still fits. Inputs remain in the
bag through product-specific PLACE, so FEED/FERTILIZE commitments survive.
"""
from collections import Counter,deque
import copy
from .daily import dist,moves
from . import rules

PRODUCTS=tuple(k for k in rules.PRODUCTS if k not in ('WHEAT','FERTILIZER'))


def repair_queues(planner,obs,hour):
    farm=obs['farms'][obs['player']]
    if planner.day==planner.end:return
    cfg=planner.cfg;last_hour=23
    for worker,original in enumerate(planner.queues):
        if not original:continue
        queue=list(original)
        # Estimate products accumulated along this worker's original route.
        local_farm,private=copy.deepcopy((farm,obs['private']))
        best=None
        for index,op in enumerate(queue):
            if op[0]=='PICKUP':
                # Acquisition is an established route reservation; simulate
                # it without letting another worker's independent scan consume it.
                item=op[1];private['shed'][item]=max(private['shed'].get(item,0),op[2])
            rules._apply_unit_action(local_farm,private,worker,op,10,planner.day,24,cfg.get('shedCapacity',100))
            inventory=private['inventories'][worker]
            cargo={k:inventory.get(k,0) for k in PRODUCTS if inventory.get(k,0)>0}
            if not cargo:continue
            pos=tuple(([local_farm['farmer']]+local_farm['hands'])[worker])
            home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
            tail=index==len(queue)-1
            extra=dist(pos,home)*(1 if tail else 2)+len(cargo)
            if hour+len(queue)+extra>last_hour+1:continue
            # Ship the most valuable feasible parcel without removing input
            # materials. The actual callback still repairs shared capacity.
            value=sum(n*obs['market']['prices'][k] for k,n in cargo.items())
            score=value/(1+extra)
            if best is None or score>best[0]:best=(score,index,pos,home,cargo,tail)
        if best:
            _,index,pos,home,cargo,tail=best
            suffix=moves(pos,home)+[['PLACE',k,n] for k,n in cargo.items()]
            if not tail:suffix+=moves(home,pos)
            planner.queues[worker]=deque(queue[:index+1]+suffix+queue[index+1:])
            planner.metrics['day_delivery_insertions']+=1
            planner.metrics['day_delivery_planned_units']+=sum(cargo.values())
