# Adapted from user-provided d15/route_columns.py. Original SHA256 in references/d15/manifest.json.
# RSIdemo bounds wall time, validates global resources, and omits the unbounded DFS/repair stages.
"""Resource-aware giant-tour splitting for dense small-grid farm jobs.

Generate spatially coherent complete tours, then solve the exact partition of
each tour by dynamic programming. Compare to the previous insertion solver;
never replace a complete solution with an incomplete spatial partition.
"""
import math,time,random
from .routes import solve_routes as insertion_routes
from .route_validation import validate_solution

HOME=((4,4),(5,4),(4,5),(5,5))
def distance(a,b):return abs(a[0]-b[0])+abs(a[1]-b[1])

def hilbert(x,y,n=16):
    d=0;s=n//2
    while s:
        rx=int(bool(x&s));ry=int(bool(y&s));d+=s*s*((3*rx)^ry)
        if ry==0:
            if rx==1:x,y=s-1-x,s-1-y
            x,y=y,x
        s//=2
    return d

def solve_routes(jobs,workers,shed,last_hour=23,time_budget_ms=45,**kwargs):
    result=_solve_routes(jobs,workers,shed,last_hour,time_budget_ms,**kwargs)
    if validate_solution(jobs,workers,shed,result,last_hour,**kwargs):return result
    # Conservative fallback reconstructs the established insertion policy.
    return insertion_routes(jobs,workers,shed,last_hour,time_budget_ms,**kwargs)


def _solve_routes(jobs,workers,shed,last_hour=23,time_budget_ms=45,**kwargs):
    began=time.perf_counter();n=len(jobs);m=len(workers)
    selective=kwargs.pop('selective_return',False)
    delivery_jobs=kwargs.pop('delivery_jobs',None)
    absolute_end=time.perf_counter()+max(.001,kwargs.get('hard_timeout_ms',time_budget_ms)/1000)
    kwargs=dict(kwargs,hard_timeout_ms=min(30,kwargs.get('hard_timeout_ms',time_budget_ms)))
    base=insertion_routes(jobs,workers,shed,last_hour,time_budget_ms,**kwargs)
    if n==0 or m==0:return base
    returning=kwargs.get('return_to_shed',False) or kwargs.get('end_depot',False)
    end_service=kwargs.get('end_service',1);track=kwargs.get('track_collect',False)
    homes=kwargs.get('shed_positions',HOME)
    pos=[tuple(j['pos']) for j in jobs];length=[len(j['ops']) for j in jobs]
    limit=[min(last_hour,j.get('deadline',last_hour))+1 for j in jobs]
    need=[];net=[]
    for j in jobs:
        f={};r={}
        for op in j['ops']:
            k=None;delta=0
            if op[0]=='FEED':k,delta='WHEAT',-1
            elif op[0]=='FERTILIZE':k,delta='FERTILIZER',-1
            elif op[0]=='PLACE' and op[1] in ('COW','SHEEP','GOOSE'):k,delta=op[1],-1
            elif op[0]=='COLLECT_FERTILIZER' and track:k,delta='FERTILIZER',1
            if k:f[k]=f.get(k,0)+delta;r[k]=max(r.get(k,0),-f[k])
        for k,q in j.get('supply',{}).items():f[k]=f.get(k,0)+q
        need.append(r);net.append(f)
    starts=[tuple(w['pos']) for w in workers]
    depots=[min(homes,key=lambda h:distance(h,p)) for p in starts]
    starts_h=[w.get('hour',0) for w in workers]
    inv=[w.get('inv',{}) for w in workers]
    def must_return(route,w):
        if not returning:return False
        if not selective:return True
        if any(inv[w].values()):return True
        if delivery_jobs is not None:return any(j in delivery_jobs for j in route)
        return any(op[0]=='HARVEST' or (last_hour==22 and op[0]=='COLLECT_FERTILIZER') for j in route for op in jobs[j]['ops'])
    # Open insertion can already have sufficient slack on just the selected
    # delivery routes. It need not make irrigation-only workers walk home.
    if returning and selective:
        open_args=dict(kwargs,return_to_shed=False)
        opened=insertion_routes(jobs,workers,shed,last_hour,time_budget_ms,**open_args)
        if not opened['unassigned']:
            finish=opened['finish_hours'][:];valid=True
            for w,route in enumerate(opened['routes']):
                if must_return(route,w):
                    end=pos[route[-1]] if route else starts[w]
                    finish[w]+=min(distance(end,h) for h in homes)+end_service
                    if finish[w]>last_hour+1:valid=False
            if valid and (base['unassigned'] or sum(finish)<sum(base['finish_hours'])):
                opened['finish_hours']=finish;opened['return_to_shed']=True;base=opened
    tours=[];seen=set()
    def add(seq):
        seq=tuple(seq)
        if seq not in seen:seen.add(seq);tours.append(seq)
    for swap in (False,True):
        for fx,fy in ((1,1),(1,-1),(-1,1),(-1,-1)):
            def xy(j):
                x,y=pos[j] if not swap else pos[j][::-1]
                return (x if fx>0 else 9-x,y if fy>0 else 9-y)
            add(sorted(range(n),key=lambda j:hilbert(*xy(j))))
            add(sorted(range(n),key=lambda j:(xy(j)[1],xy(j)[0] if xy(j)[1]%2 else -xy(j)[0])))
    # Current feasible clusters provide strong tour proposals for exchange.
    if base['routes']:
        flat=[j for route in base['routes'] for j in route]+base['unassigned']
        add(flat);add(flat[::-1])
    for center in ((4.5,4.5),(0,0),(9,9)):
        seq=sorted(range(n),key=lambda j:(math.atan2(pos[j][1]-center[1],pos[j][0]-center[0]),distance(pos[j],center)))
        add(seq);add(seq[::-1])
    best=base
    best_cost=sum(base['finish_hours']) if not base['unassigned'] else 1e100
    evaluations=0
    for tour in tours:
        if time.perf_counter()>=absolute_end:break
        # Suffix metrics for every contiguous segment. Prefix material needs
        # are exact, including fertilizer generated earlier on the same route.
        segments=[[] for _ in range(n)]
        for i in range(n):
            if time.perf_counter()>=absolute_end:return best
            flow={};required={};service=0;deadline=1e9;prev=None;cargo=False
            for e in range(i,n):
                j=tour[e]
                cargo=cargo or (j in delivery_jobs if delivery_jobs is not None else any(op[0]=='HARVEST' or (last_hour==22 and op[0]=='COLLECT_FERTILIZER') for op in jobs[j]['ops']))
                if prev is not None:service+=distance(pos[prev],pos[j])
                service+=length[j]
                deadline=min(deadline,limit[j]-service)
                for k,q in need[j].items():required[k]=max(required.get(k,0),q-flow.get(k,0))
                for k,q in net[j].items():flow[k]=flow.get(k,0)+q
                if service>last_hour+1-min(starts_h):break
                segments[i].append((e+1,service,deadline,dict(required),pos[j],cargo))
                prev=j
        dp=[[1e100]*(n+1) for _ in range(m+1)];parent={};dp[0][0]=0
        for w in range(m):
            if time.perf_counter()>=absolute_end:return best
            for i in range(n+1):
                old=dp[w][i]
                if old>=1e90:continue
                if old+starts_h[w]<dp[w+1][i]:
                    dp[w+1][i]=old+starts_h[w];parent[w+1,i]=(i,{},starts_h[w])
                if i==n:continue
                for e,service,deadline,required,endpos,cargo in segments[i]:
                    evaluations+=1
                    take={k:q-inv[w].get(k,0) for k,q in required.items() if q>inv[w].get(k,0)}
                    if any(q>shed.get(k,0) for k,q in take.items()):continue
                    departure=depots[w] if take else starts[w]
                    time0=starts_h[w]+len(take)+(distance(starts[w],departure) if take else 0)+distance(departure,pos[tour[i]])
                    if time0>deadline:continue
                    finish=time0+service
                    if returning and (not selective or cargo or any(inv[w].values())):finish+=min(distance(endpos,h) for h in homes)+end_service
                    if finish>last_hour+1:continue
                    cost=old+finish
                    if cost<dp[w+1][e]:
                        dp[w+1][e]=cost;parent[w+1,e]=(i,take,finish)
        if dp[m][n]>=best_cost:continue
        routes=[[] for _ in workers];pickups=[{} for _ in workers];ends=starts_h[:]
        e=n
        for w in range(m,0,-1):
            i,take,finish=parent[w,e]
            routes[w-1]=list(tour[i:e]);pickups[w-1]=take;ends[w-1]=finish;e=i
        reserved={}
        for take in pickups:
            for k,q in take.items():reserved[k]=reserved.get(k,0)+q
        if any(q>shed.get(k,0) for k,q in reserved.items()):continue
        best_cost=dp[m][n]
        best=dict(routes=routes,unassigned=[],pickups=pickups,finish_hours=ends,
            pickup_positions=[depots[w] if pickups[w] else starts[w] for w in range(m)],
            end_positions=[(min(homes,key=lambda h:distance(h,pos[r[-1]])) if returning else pos[r[-1]]) if r else starts[w] for w,r in enumerate(routes)],
            return_to_shed=returning,
            stats=dict(evaluations=evaluations,assigned=n,reserved=reserved,method='spatial_partition'))
    if not validate_solution(jobs,workers,shed,best,last_hour,**kwargs):best=base
    best['stats']['elapsed_ms']=(time.perf_counter()-began)*1000
    best['stats']['spatial_evaluations']=evaluations
    return best

