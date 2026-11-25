"""Independent small exhaustive oracle. Run from minimum_effort with solution binary as argv[1]."""
import functools
import itertools
import random
import subprocess
import sys

C=10**9
MOD=C+7
rng=random.Random(100623)
binary=sys.argv[1]

def charge(a,b):
    return 1 << (abs(a[1]-b[1])+abs(a[2]-b[2]))

def sort(board):
    return tuple(sorted(board,key=lambda row:(-row[1],row[2])))

def bests(board,n):
    r=[len(board)+1]*n
    for j,(i,*_) in enumerate(board,1):
        r[i]=min(r[i],j)
    return r

def instance(n,old,R,H,L=None,P=None):
    s=[f'{n} {len(old)} {int(L is not None)}',
       ' '.join(map(str,R)),
       *[f'{i+1} {m} {t}' for i,m,t in old]]
    if L is not None:
        s.extend([str(len(L)),*[f'{i+1} {p}' for i,p in zip(L,P)]])
    return '\n'.join(s)+'\n'

def ask(n,old,R,H,ans,L=None,P=None):
    s=instance(n,old,R,H,L,P)
    p=subprocess.run([binary],input=s,text=True,capture_output=True,check=True)
    assert int(p.stdout)==ans%MOD,(s,ans%MOD,p.stdout)

def general(n,old,R,H):
    T=C-H
    mc=max(m for _,m,_ in old)+H
    @functools.lru_cache(None)
    def dfs(board,j):
        ranks=bests(board,n)
        failing=[i for i in range(n) if ranks[i]>R[i]]
        if not failing:return 0
        if len(failing)>H-j:return None
        ans=None
        for i in failing:
            w=charge(board[ranks[i]-1],board[R[i]-1])
            for m in range(1,mc+1):
                r=dfs(sort((*board,(i,m,T+j+1))),j+1)
                if r is not None and (ans is None or w+r<ans):
                    ans=w+r
        return ans
    return dfs(sort(old),0)

def fixed(n,old,R,H,L,P):
    T=C-H
    mc=max(m for _,m,_ in old)+H
    ans=None
    for scores in itertools.product(range(1,mc+1),repeat=len(L)):
        new=[(i,m,T+j+1) for j,(i,m) in enumerate(zip(L,scores))]
        board=sort((*old,*new))
        if any(board[p-1]!=r for p,r in zip(P,new)):continue
        if any(a>b for a,b in zip(bests(board,n),R)):continue
        board=sort(old)
        w=0
        for row in new:
            i=row[0]
            a=bests(board,n)[i]
            if a<=R[i]:break
            w+=charge(board[a-1],board[R[i]-1])
            board=sort((*board,row))
        else:
            if ans is None or w<ans:ans=w
    return ans

general_done=fixed_done=0
for trial in range(1500):
    n=rng.randint(2,5)
    H=rng.randint(0,4)
    T=C-H
    M=rng.randint(n,n+3)
    ts=rng.sample(range(T-20,T),M-1)+[T]
    old=[(i if i<n else rng.randrange(n),rng.randint(1,7),ts[i]) for i in range(M)]
    R=[rng.randint(1,M) for _ in range(n)]
    ans=general(n,old,R,H)
    if ans is None:continue
    ask(n,old,R,H,ans)
    general_done+=1
    if H==0:continue
    for trial2 in range(3):
        new=[]
        board=sort(old)
        while len(new)<H:
            failing=[i for i,b in enumerate(bests(board,n)) if b>R[i]]
            if not failing:break
            i=rng.choice(failing)
            m=rng.randint(1,9)
            row=(i,m,T+len(new)+1)
            new.append(row)
            board=sort((*board,row))
        if not new or any(a>b for a,b in zip(bests(board,n),R)):continue
        L=[r[0] for r in new]
        P=[board.index(r)+1 for r in new]
        ans=fixed(n,old,R,H,L,P)
        assert ans is not None
        ask(n,old,R,H,ans,L,P)
        fixed_done+=1
print(f'All {general_done} legal general and {fixed_done} legal fixed toy cases matched the exhaustive oracle.')
