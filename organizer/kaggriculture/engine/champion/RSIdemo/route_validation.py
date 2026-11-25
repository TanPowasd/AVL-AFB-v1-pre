"""Independent resource-prefix and deadline checks on complete route results."""
from collections import Counter


def validate_solution(jobs,workers,shed,result,last_hour=23,**cfg):
    homes=cfg.get('shed_positions',((4,4),(5,4),(4,5),(5,5)))
    distance=lambda a,b:abs(a[0]-b[0])+abs(a[1]-b[1])
    assigned=[];reserved=Counter()
    if len(result['routes'])!=len(workers):return False
    for w,route in enumerate(result['routes']):
        assigned+=route
        inv=Counter(workers[w].get('inv',{}))
        take=result['pickups'][w];reserved.update(take)
        if any(n<0 for n in take.values()):return False
        pos=tuple(workers[w]['pos']);clock=workers[w].get('hour',0)
        if take:
            depot=result['pickup_positions'][w]
            if tuple(depot) not in homes:return False
            clock+=distance(pos,depot)+len(take);pos=depot;inv.update(take)
        for j in route:
            if not 0<=j<len(jobs):return False
            job=jobs[j];clock+=distance(pos,job['pos']);pos=job['pos']
            for op in job['ops']:
                clock+=1
                item='WHEAT' if op[0]=='FEED' else 'FERTILIZER' if op[0]=='FERTILIZE' else (
                    op[1] if op[0]=='PLACE' and op[1] in ('COW','SHEEP','GOOSE') else None)
                if item:
                    if inv[item]<1:return False
                    inv[item]-=1
                elif op[0]=='COLLECT_FERTILIZER' and cfg.get('track_collect'):
                    inv['FERTILIZER']+=1
            inv.update(job.get('supply',{}))
            if clock>min(last_hour,job.get('deadline',last_hour))+1:return False
        if (cfg.get('return_to_shed') or cfg.get('end_depot')) and (route or any(workers[w].get('inv',{}).values())):
            clock+=min(distance(pos,home) for home in homes)+cfg.get('end_service',1)
        if clock>last_hour+1:return False
    if len(set(assigned))!=len(assigned):return False
    if set(assigned)&set(result['unassigned']):return False
    if set(assigned)|set(result['unassigned'])!=set(range(len(jobs))):return False
    return all(n<=shed.get(k,0) for k,n in reserved.items())
