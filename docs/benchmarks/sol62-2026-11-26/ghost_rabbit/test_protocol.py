#!/usr/bin/env python3
"""Exhaustive validator of the supplied small policy against every move."""
from collections import Counter, deque
from itertools import combinations
from functools import cache
from fractions import Fraction
from math import gcd, isqrt
import subprocess
import sys

def n(p):
    a,b=p
    d=1 if (a+b)%3==1 else -1
    return {(a+d,b),(a,b+d),(a-d,b-d)}

@cache
def dist(a,b):
    queue=deque([(a,0)])
    seen={a}
    while queue:
        p,d=queue.popleft()
        if p==b: return d
        for x in n(p)-seen:
            queue.append((x,d+1))
            seen.add(x)
    raise AssertionError("unreachable")

def prime(p):
    a,b=p
    axis = a==0 or b==0 or a==b
    z=max(abs(a),abs(b)) if axis else a*a-a*b+b*b
    return z>1 and z%3==(2 if axis else 1) and all(z%i for i in range(2,isqrt(z)+1))

def legal(p):
    a,b=p
    return max(abs(a),abs(b))<=100000 and (a+b)%3!=0

def line(stream):
    s=stream.readline()
    assert s and s.endswith("\n") and len(s.encode())<=1024,repr(s)
    return list(map(int,s.split()))

def play(exe,prefix):
    F=set()
    r=(1,0)
    proc=subprocess.Popen([exe],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        P,Q=line(proc.stdout)
        assert 1<=Q<=10**6 and 1<=P<=2*Q
        g=gcd(P,Q)
        P,Q=P//g,Q//g
        tokens=0
        steps=[]
        for round in range(1,31):
            tokens=min(tokens+P,2*Q)
            k,*xy=line(proc.stdout)
            assert 0<=k<=2 and k*Q<=tokens and len(xy)==2*k+2
            points=list(zip(xy[::2],xy[1::2]))
            A,S=points[:-1],points[-1]
            assert all(legal(p) for p in points)
            assert len(set(A))==k and not (F&set(A))
            U=F|set(A)
            for p,q in combinations(U,2):
                ap=(p[0]+q[0],p[1]+q[1])
                if ap[0]%2==ap[1]%2==0 and (ap[0]//2,ap[1]//2) in U:
                    assert dist(p,q)>6, (round,p,q,U)
            tokens-=k*Q
            bo=A.index(r)+1 if r in A else 0
            F=U-{r}
            assert len(F)<=4000
            flash=prime(r)
            M=n(r)
            if flash:
                M |= set().union(*(n(x) for x in M))-{r}
            M-=F
            steps.append((r,A,S,tokens,sorted(M)))
            if not M:
                assert len(prefix)==round-1
                proc.stdin.write("1 0 0 0\n"); proc.stdin.flush()
                assert proc.wait(timeout=2)==0 and not proc.stdout.read().strip()
                return None,(P,Q),steps
            if round>len(prefix):
                return tuple(prefix+(x,) for x in sorted(M)),(P,Q),steps
            r=prefix[round-1]
            assert r in M
            proc.stdin.write(f"0 {dist(S,r)} {int(flash)} {bo}\n")
            proc.stdin.flush()
        raise AssertionError("policy didn't finish within 30 moves")
    finally:
        if proc.poll() is None:
            proc.kill();proc.wait(timeout=2)

exe=sys.argv[1]
queue=[()]
counts=Counter()
cases=0
while queue:
    prefix=queue.pop()
    branches,rate,steps=play(exe,prefix)
    cases+=1
    if branches is not None:
        queue.extend(branches)
        continue
    counts[len(steps)]+=1
    print(f"Capture in {len(steps)} rounds: {[s[0] for s in steps]}")
    for r,A,S,tokens,M in steps:
        print(f"  r={r} place={A} sonar={S} tokens_after={tokens} exits={M}")
print(f"Rate {Fraction(*rate)}; {cases} replayed prefixes; {sum(counts.values())} terminal legal histories; capture rounds {dict(sorted(counts.items()))}; clean protocol exit on all.")
