"""Verify a fixed-shape answer and prove optimality of the supplied score witness."""
from pathlib import Path
import sys
import networkx as nx

U = 10**9
MOD = U + 7

def verify(path, witness):
    vals=iter(map(int,path.read_bytes().split())); n,M,c=next(vals),next(vals),next(vals)
    R=[0]+[next(vals) for _ in range(n)]; assert c==1
    old=[tuple(next(vals) for _ in range(3)) for _ in range(M)]
    old.sort(key=lambda a:(-a[1],a[2]))
    L=next(vals); moves=[(next(vals),next(vals)) for _ in range(L)]
    T=max(h[2] for h in old);assert T+L<=U
    x=[0]+list(map(int,witness.read_bytes().split()));assert len(x)==L+1
    rows=[None]*(M+L+1);N=M+L
    for j,(a,p) in enumerate(moves,1):
        assert rows[p] is None;rows[p]=(a,x[j],T+j,j)
    ri=0;best=[N+1]*(n+1);f=[0]*(N+1);prev=None
    for k in range(1,N+1):
        if rows[k] is None:
            a,m,t=old[ri];ri+=1;rows[k]=(a,m,t,0);best[a]=min(best[a],k);f[k]=1
        a,m,t,j=rows[k];key=(-m,t);assert prev is None or prev<key;prev=key
    for k in range(1,N+1):
        nex=k+(k&-k)
        if nex<=N:f[nex]+=f[k]
    fees=[]; order=sorted(range(1,L+1),key=lambda j:moves[j-1][1])
    bounds=[];lo=[0]*(L+1);hi=[0]*(L+1)
    for a,j in enumerate(order):
        q=moves[j-1][1]-1-a
        lo[j]=old[q][1]+1 if q<M else 1;hi[j]=old[q-1][1] if q else U
        bounds.extend(((0,j,hi[j]),(j,0,-lo[j])))
        if a:
            k=order[a-1];bounds.append((k,j,-int(k>j)))
    for j,(a,p) in enumerate(moves,1):
        q=R[a];ix=0;step=1<<(N.bit_length()-1)
        while step:
            t=ix+step
            if t<=N and f[t]<q:q-=f[t];ix=t
            step//=2
        gate=ix+1;assert best[a]>gate
        _,ma,ta,u=rows[best[a]];_,mb,tb,v=rows[gate]
        e=mb-ma+abs(tb-ta);assert e>=1
        fees.append((u,v,e-x[v]+x[u]));best[a]=min(best[a],p)
        t=p
        while t<=N:f[t]+=1;t+=t&-t
    assert all(best[i]<=R[i] for i in range(1,n+1))
    get=lambda y:sum(pow(2,d+y[v]-y[u],MOD) for u,v,d in fees)%MOD
    answer=get(x);assert answer==int(path.with_suffix('.out').read_text())
    for k in range(L-2,-1,-1):
        i,j=order[k:k+2];lo[i]=max(lo[i],lo[j]+(i>j))
    for i,j in zip(order,order[1:]):hi[j]=min(hi[j],hi[i]-(i>j))
    assert all(a<=v<=b for a,v,b in zip(lo,x,hi))
    # Contract fixed coordinates with the translation anchor.
    atom=lambda j: j if lo[j]!=hi[j] else 0
    terms=[(atom(u),atom(v),d+x[v]-x[u]) for u,v,d in fees if atom(u)!=atom(v)]
    exp=sorted({w for u,v,e in terms for w in (e,e-1)})
    bits={}; last=0;gap=(2*len(terms)+1).bit_length()
    for i,e in enumerate(exp):
        if i:last+=min(gap,e-exp[i-1])
        bits[e]=last
    G=nx.DiGraph();src=L+1;sink=L+2;G.add_nodes_from([src,sink])
    def add(u,v,cap):
        if u==v or not cap:return
        if G.has_edge(u,v):G[u][v]['capacity']+=cap
        else:G.add_edge(u,v,capacity=cap)
    fields=[0]*(L+1);total=0
    for u,v,e in terms:
        a=1<<bits[e];b=1<<bits[e-1]
        fields[v]+=a;fields[u]-=a;add(u,v,a-b);total+=a-b
    zero=0
    for j,d in enumerate(fields):
        if d>0:add(j,sink,d)
        if d<0:add(src,j,-d);zero-=d
        total+=abs(d)
    for u,v,c in bounds:
        if x[v]-x[u]==c:add(atom(v),atom(u),total+1)
    cut=nx.maximum_flow_value(G,src,sink,flow_func=nx.algorithms.flow.preflow_push)
    assert cut==zero,'improving unit move exists'
    return L,sum(u!=0 and v!=0 for u,v,_ in fees),get(lo),get(hi),answer
if __name__=='__main__':
    print(*verify(Path(sys.argv[1]),Path(sys.argv[2])))
