#!/usr/bin/env python3
"""Complete finite-state search for policies that never permit a prime move."""
from collections import deque
from itertools import combinations

R=((1,0),(1,1),(0,1),(-1,0),(-1,-1),(0,-1))
S=R+tuple((a*2,b*2) for a,b in R)
D=((1,-1),(2,1),(1,2))
def bit(i):
    return 1<<i

def mask(inds):
    return sum(bit(i) for i in inds)

def forbidden(indices):
    p,m,q=[S[i] for i in indices]
    for a,b,c in [(p,m,q),(m,p,q),(p,q,m)]:
        d=c[0]-b[0],c[1]-b[1]
        if a[0]+c[0]==2*b[0] and a[1]+c[1]==2*b[1]:
            if d in D or (-d[0],-d[1]) in D:
                return True
    return False

AP=[mask(inds) for inds in combinations(range(12),3) if forbidden(inds)]
LEGAL=tuple(not any(f&a==a for a in AP) for f in range(1<<12))
PLACES=[(mask(c),k) for k in range(3) for c in combinations(range(12),k)]

def analyze(strict_refill):
    # Post-action abstract credit u/3, with strict upper bound when below=True.
    # strict_refill=True dominates every real q<4/3, possibly permitting actions
    # that would be unaffordable at every individual such q.
    start=(0,0,0,False)
    todo=deque([start])
    seen={start}
    options={}
    while todo:
        current=todo.popleft()
        r,F,u,below=current
        if u+4>6:
            u,below=6,False
        else:
            u,below=u+4,below or strict_refill
        alternatives=[]
        options[current]=alternatives
        for A,k in PLACES:
            cost=3*k
            if cost>u or (cost==u and below): continue
            if A&(F|bit(r)): continue
            f=F|A
            if not LEGAL[f] or not f&bit(6+r): continue
            children=tuple((x,f,u-cost,below) for x in ((r-1)%6,(r+1)%6)
                           if not f&bit(x))
            alternatives.append(children)
            for s in children:
                if s not in seen:
                    seen.add(s)
                    todo.append(s)
    # Least fixed point of states with an action whose all successors win.
    winners=set()
    layers=[]
    while True:
        add={s for s in seen-winners if any(all(c in winners for c in children)
                                            for children in options[s])}
        if not add:
            break
        layers.append(len(add))
        winners|=add
        if start in add:
            print(f"strict_refill={strict_refill}: {len(seen)} reachable states, "
                  f"start winning in {len(layers)} rounds; layers {layers}")
            return
    print(f"strict_refill={strict_refill}: {len(seen)} reachable states, "
          f"start not winning; fixed point has {len(winners)} winners; layers {layers}")

if __name__=="__main__":
    analyze(False)
    analyze(True)
