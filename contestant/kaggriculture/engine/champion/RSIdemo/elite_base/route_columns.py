# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Resource-aware giant-tour splitting for dense small-grid farm jobs.

Generate spatially coherent complete tours, then solve the exact partition of
each tour by dynamic programming. Compare to the previous insertion solver;
never replace a complete solution with an incomplete spatial partition.
"""
import math,time,random
from .native_routes import make_native
from .gpt6_math_routes import solve_routes as insertion_routes

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
    began=time.perf_counter();n=len(jobs);m=len(workers)
    selective=kwargs.pop('selective_return',False)
    delivery_jobs=kwargs.pop('delivery_jobs',None)
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
    native=make_native(jobs,workers,shed,last_hour,homes,returning,selective,delivery_jobs,end_service,need,net,limit)
    for tour in tours:
        if native is not None:
            partition,count=native.partition(tour,best_cost)
            evaluations+=count
            if partition is None:continue
            routes,pickups,ends,reserved=partition
        else:
            # Suffix metrics for every contiguous segment. Prefix material needs
            # are exact, including fertilizer generated earlier on the same route.
            segments=[[] for _ in range(n)]
            for i in range(n):
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
        best_cost=sum(ends)
        best=dict(routes=routes,unassigned=[],pickups=pickups,finish_hours=ends,
            pickup_positions=[depots[w] if pickups[w] else starts[w] for w in range(m)],
            end_positions=[(min(homes,key=lambda h:distance(h,pos[r[-1]])) if returning else pos[r[-1]]) if r else starts[w] for w,r in enumerate(routes)],
            return_to_shed=returning,
            stats=dict(evaluations=evaluations,assigned=n,reserved=reserved,method='spatial_partition'))
    if best['unassigned']:
        best=_repair_search(best,jobs,workers,shed,last_hour,homes,returning,selective,delivery_jobs,end_service,need,net,limit,18000)
    if best['unassigned'] and len(best['unassigned'])<=5 and m>=3:
        best=_cover_neighborhood(best,jobs,workers,shed,last_hour,homes,returning,selective,delivery_jobs,end_service,need,net,limit)
    best['stats']['elapsed_ms']=(time.perf_counter()-began)*1000
    best['stats']['spatial_evaluations']=evaluations
    return best


def _cover_neighborhood(base,jobs,workers,shed,last_hour,homes,returning,selective,delivery_jobs,end_service,needs,nets,deadlines):
    """Recombine whole resource-feasible route columns by exact cover.

    The tour pool is bounded, so failure is not proof of infeasibility. A
    successful cover is a complete concrete assignment, including all shared
    pickup quantities. Routes outside the selected neighborhood are untouched.
    """
    positions=[tuple(j['pos']) for j in jobs];missing=base['unassigned']
    ranked=sorted(range(len(workers)),key=lambda w:min((distance(positions[j],positions[k]) for j in base['routes'][w] for k in missing),default=0))
    chosen=ranked[:4]
    universe=sorted(set(missing)|{j for w in chosen for j in base['routes'][w]})
    if len(universe)>28:return base
    bit={j:1<<i for i,j in enumerate(universe)};full=(1<<len(universe))-1
    available=dict(shed)
    for w,pick in enumerate(base['pickups']):
        if w not in chosen:
            for k,q in pick.items():available[k]=available.get(k,0)-q
    types=('WHEAT','FERTILIZER','COW','SHEEP','GOOSE')
    native=make_native(jobs,workers,available,last_hour,homes,returning,selective,delivery_jobs,end_service,needs,nets,deadlines)
    def metric(w,seq):
        if native is not None:return native.evaluate(w,seq)
        inv=workers[w].get('inv',{});flow={};req={}
        for j in seq:
            for k,q in needs[j].items():req[k]=max(req.get(k,0),q-flow.get(k,0))
            for k,q in nets[j].items():flow[k]=flow.get(k,0)+q
        take={k:q-inv.get(k,0) for k,q in req.items() if q>inv.get(k,0)}
        if any(q>available.get(k,0) for k,q in take.items()):return None
        start=tuple(workers[w]['pos']);depot=min(homes,key=lambda h:distance(start,h))
        at=depot if take else start;clock=workers[w].get('hour',0)+len(take)+(distance(start,depot) if take else 0)
        cargo=any(inv.values())
        for j in seq:
            clock+=distance(at,positions[j])+len(jobs[j]['ops']);at=positions[j]
            if clock>deadlines[j]:return None
            cargo=cargo or (j in delivery_jobs if delivery_jobs is not None else any(op[0]=='HARVEST' or last_hour==22 and op[0]=='COLLECT_FERTILIZER' for op in jobs[j]['ops']))
        if returning and seq and (not selective or cargo):clock+=min(distance(at,h) for h in homes)+end_service
        if clock>last_hour+1:return None
        return clock,take
    pools=[];generated=0
    for w in chosen:
        records={};seen={};calls=0
        def walk(seq,mask):
            nonlocal calls,generated
            if calls>=7000:return
            calls+=1;result=metric(w,seq)
            if result is None:return
            finish,take=result;resource=tuple(take.get(k,0) for k in types)
            key=mask,resource
            if mask and (key not in records or finish<records[key][0]):records[key]=(finish,tuple(seq),take)
            if not seq:at=tuple(workers[w]['pos'])
            else:at=positions[seq[-1]]
            state=mask,seq[-1] if seq else -1,resource
            if seen.get(state,1e100)<=finish:return
            seen[state]=finish
            if len(seq)>=12:return
            neighbors=sorted((j for j in universe if not mask&bit[j]),key=lambda j:(distance(at,positions[j]),j not in missing,len(jobs[j]['ops'])))
            for j in neighbors[:(len(neighbors) if not seq else 5)]:walk(seq+[j],mask|bit[j])
        # Keep every established route as a legal column, even if enumeration
        # reaches its budget before reconstructing that particular sequence.
        original=base['routes'][w];result=metric(w,original)
        if result:
            finish,take=result;mask=sum(bit[j] for j in original)
            records[mask,tuple(take.get(k,0) for k in types)]=(finish,tuple(original),take)
        walk([],0);generated+=calls
        columns=[(mask,finish,seq,take) for (mask,_),(finish,seq,take) in records.items() if mask]
        by_job={j:[c for c in columns if c[0]&bit[j]] for j in universe}
        for values in by_job.values():values.sort(key=lambda c:(-c[0].bit_count(),c[1]))
        pools.append(by_job)
    difficulty=sorted(universe,key=lambda j:sum(len(pool[j]) for pool in pools))
    visits=0;failed=set()
    def cover(remaining,unused,stock):
        nonlocal visits
        visits+=1
        if not remaining:return []
        if not unused or visits>10000:return None
        key=remaining,unused,tuple(stock.get(k,0) for k in types)
        if key in failed:return None
        j=next(j for j in difficulty if remaining&bit[j]);options=[]
        for wi in range(len(chosen)):
            if not unused&(1<<wi):continue
            for column in pools[wi][j]:
                mask,finish,seq,take=column
                if mask&remaining!=mask or any(q>stock.get(k,0) for k,q in take.items()):continue
                options.append((-mask.bit_count(),finish,wi,column))
        options.sort(key=lambda x:x[:3])
        for _,_,wi,column in options:
            mask,finish,seq,take=column;after=dict(stock)
            for k,q in take.items():after[k]-=q
            rest=cover(remaining^mask,unused^(1<<wi),after)
            if rest is not None:return [(wi,column)]+rest
        failed.add(key);return None
    answer=cover(full,(1<<len(chosen))-1,available)
    if answer is None:return base
    import copy
    result=copy.deepcopy(base)
    for w in chosen:
        result['routes'][w]=[];result['pickups'][w]={};result['finish_hours'][w]=workers[w].get('hour',0)
    for wi,(_,finish,seq,take) in answer:
        w=chosen[wi];result['routes'][w]=list(seq);result['pickups'][w]=take;result['finish_hours'][w]=finish
        start=tuple(workers[w]['pos']);result['pickup_positions'][w]=min(homes,key=lambda h:distance(start,h)) if take else start
        result['end_positions'][w]=positions[seq[-1]]
    result['unassigned']=[]
    reserved={}
    for take in result['pickups']:
        for k,q in take.items():reserved[k]=reserved.get(k,0)+q
    result['stats'].update(method='resource_column_exact_cover',column_nodes=generated,cover_nodes=visits,reserved=reserved,assigned=len(jobs))
    return result

def _repair_search(base,jobs,workers,shed,last_hour,homes,returning,selective,delivery_jobs,end_service,needs,nets,deadlines,budget):
    """Large-neighborhood search, with resource-prefix feasible regret repair."""
    n=len(jobs);m=len(workers);positions=[tuple(j['pos']) for j in jobs]
    sizes=[len(j['ops']) for j in jobs];starts=[tuple(w['pos']) for w in workers]
    home=[min(homes,key=lambda h:distance(h,p)) for p in starts]
    weights=[1e5+min(10000,j.get('priority',0)) if j.get('mandatory') else 1000+min(10000,j.get('priority',0)) for j in jobs]
    calls=0
    native=make_native(jobs,workers,shed,last_hour,homes,returning,selective,delivery_jobs,end_service,needs,nets,deadlines)
    def evaluate(w,route):
        nonlocal calls
        calls+=1
        if native is not None:return native.evaluate(w,route)
        flow={};req={}
        for j in route:
            for k,q in needs[j].items():req[k]=max(req.get(k,0),q-flow.get(k,0))
            for k,q in nets[j].items():flow[k]=flow.get(k,0)+q
        inv=workers[w].get('inv',{})
        take={k:q-inv.get(k,0) for k,q in req.items() if q>inv.get(k,0)}
        if any(q>shed.get(k,0) for k,q in take.items()):return None
        p=home[w] if take else starts[w]
        t=workers[w].get('hour',0)+len(take)+(distance(starts[w],p) if take else 0)
        cargo=bool(any(inv.values()))
        for j in route:
            t+=distance(p,positions[j])+sizes[j];p=positions[j]
            if t>deadlines[j]:return None
            cargo=cargo or (j in delivery_jobs if delivery_jobs is not None else any(op[0]=='HARVEST' or (last_hour==22 and op[0]=='COLLECT_FERTILIZER') for op in jobs[j]['ops']))
        if returning and route and (not selective or cargo):t+=min(distance(p,h) for h in homes)+end_service
        if t>last_hour+1:return None
        return t,take
    def assess(routes):
        result=[evaluate(w,r) for w,r in enumerate(routes)]
        if any(r is None for r in result):return None
        reserves={}
        for _,take in result:
            for k,q in take.items():reserves[k]=reserves.get(k,0)+q
        if any(q>shed.get(k,0) for k,q in reserves.items()):return None
        score=sum(weights[j] for r in routes for j in r)-.05*sum(x[0] for x in result)
        return score,result,reserves
    routes=[r[:] for r in base['routes']];current=assess(routes)
    if current is None:return base
    best_routes=[r[:] for r in routes];best_score=current[0]
    rng=random.Random(919+31*n+m)
    for turn in range(40):
        if calls>=budget:break
        trial=[r[:] for r in routes]
        assigned=[j for r in trial for j in r]
        pivot=rng.choice(assigned) if assigned else 0
        removal=sorted(assigned,key=lambda j:distance(positions[pivot],positions[j])+.5*rng.random())[:min(12,max(4,n//7))]
        if turn%4==0:
            chosen=rng.randrange(m);removal=list(set(removal)|set(trial[chosen]))
        remove=set(removal)
        trial=[[j for j in r if j not in remove] for r in trial]
        available=set(range(n))-{j for r in trial for j in r}
        state=assess(trial)
        if state is None:continue
        _,details,reserved=state
        while available and calls<budget:
            winner=None
            for j in sorted(available):
                if native is not None:
                    selection,count=native.insertion(j,trial,[d[0] for d in details],reserved,[d[1] for d in details],regret=True)
                    calls+=count
                    if selection is None:continue
                    choice,regret=selection
                else:
                    choices=[]
                    for w,r in enumerate(trial):
                        for k in range(len(r)+1):
                            seq=r[:k]+[j]+r[k:];result=evaluate(w,seq)
                            if result is None:continue
                            if any(reserved.get(key,0)-details[w][1].get(key,0)+q>shed.get(key,0) for key,q in result[1].items()):continue
                            choices.append((result[0]-details[w][0],w,k,result))
                    if not choices:continue
                    choices.sort(key=lambda x:x[0]);choice=choices[0]
                    regret=(choices[1][0]-choice[0]) if len(choices)>1 else 30
                key=(int(jobs[j].get('mandatory',False)),regret,-choice[0],weights[j])
                if winner is None or key>winner[0]:winner=(key,j,choice)
                if calls>=budget:break
            if winner is None:break
            _,j,(_,w,k,result)=winner
            for key,q in details[w][1].items():reserved[key]-=q
            for key,q in result[1].items():reserved[key]=reserved.get(key,0)+q
            trial[w].insert(k,j);details[w]=result;available.remove(j)
        candidate=assess(trial)
        if candidate and (candidate[0]>=current[0] or rng.random()<.08):routes=trial;current=candidate
        if candidate and candidate[0]>best_score:
            best_routes=[r[:] for r in trial];best_score=candidate[0]
            if sum(map(len,best_routes))==n:break
    result=assess(best_routes)
    if result is None or best_score<=assess(base['routes'])[0]:return base
    _,details,reserved=result
    return dict(routes=best_routes,unassigned=sorted(set(range(n))-{j for r in best_routes for j in r}),
        pickups=[r[1] for r in details],finish_hours=[r[0] for r in details],
        pickup_positions=[home[w] if details[w][1] else starts[w] for w in range(m)],
        end_positions=[positions[r[-1]] if r else starts[w] for w,r in enumerate(best_routes)],
        return_to_shed=returning,stats=dict(method='lns_regret',evaluations=calls,assigned=sum(map(len,best_routes)),reserved=reserved))
