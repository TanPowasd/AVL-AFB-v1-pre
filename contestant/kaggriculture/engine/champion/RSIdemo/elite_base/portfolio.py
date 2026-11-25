# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Whole-farm column exchange with full-portfolio acceptance.

Individual project DP proposes a move. The nonlinear shared-market objective
and dated labor curve decide whether that move is accepted. A synchronized
block of identical per-tile best responses is deliberately never committed.
"""
from collections import Counter
from functools import lru_cache
import math
from .project_model import CropDP, Project, animal_project
from .market_model import empty_flows,add_project,cohort_supply
from .exact_market_model import MarketModel
from . import gpt6_rules as rules
from .gpt6_math_routes import solve_routes as route_price

def sd(p):return max(0,4-p[0],p[0]-5)+max(0,4-p[1],p[1]-5)
@lru_cache(maxsize=64)
def wage(n):
    a,b,total=1,1,0
    for _ in range(n):total,a,b=total+a,b,a+b
    return total

class ResourcePart(tuple):
    """RSIdemo: preserve the original triple plus sunk-investment accounting."""
    def __new__(cls,flow,work,cost,investment):
        obj=super().__new__(cls,(flow,work,cost));obj.investment=investment;return obj
    def __getnewargs__(self):return (*self,self.investment)


def resources(project,pos):
    flow=empty_flows();add_project(flow,project)
    work=[0.0]*30;cost=0.0;investment=Counter()
    for d,ops,output,inputs in project.days:
        if ops:work[d]=len(ops)+1.4+sd(pos)/8+sum(n for k,n in output)/8
        for k,n in inputs:
            if k.startswith('SEED_'):cost+=n*rules.CROPS[k[5:]]['seed'];investment[k]+=n
            elif k in rules.ANIMALS:cost+=n*rules.ANIMALS[k]['cost'];investment[k]+=n
    return ResourcePart(flow,work,cost,investment)

def labor_cost(work):
    value=0.0
    for hours in work:
        hands=max(0,hours/21-1)
        lo=min(18,int(hands));frac=hands-lo
        value+=wage(lo)+(wage(lo+1)-wage(lo))*frac
        if hours>320:value+=(hours-320)*25
    return value

def shadows(work):
    return [max(1.0,(wage(min(17,max(1,math.ceil(h/21)-1))+1)-wage(min(17,max(1,math.ceil(h/21)-1))))/21)
            +25*(h>320) for h in work]

class Portfolio:
    animal_land_reuse=False
    book_type=MarketModel
    position_sensitive_animals=False
    def animal_column(self,pos,tile,prices,labor,visit,reuse=None):
        return animal_project(tile['animal'],tile['placed_day'],self.day,tile,prices,labor,visit,reuse=reuse)
    def __init__(self,obs,positions,previous=None):
        self.obs=obs;self.day=obs['step']//24;self.book=self.book_type(obs)
        self.flow=empty_flows();self.work=[0.0]*30;self.cost=0.0
        self.projects={};self.parts={};self.accepted=0;self.proposed=0
        self.farm=obs['farms'][obs['player']]
        self.labor_multiplier=[1.0]*30
        preliminary=cohort_supply(self.farm,self.day,1,1)
        prices=self.book.marginal(preliminary)
        dp=CropDP(prices,[4.0]*30,[2.0]*30,start=self.day)
        cache={}
        for pos in positions:
            t=self.farm['tiles'][pos[1]][pos[0]]
            if isinstance(t,dict) and 'animal' in t:
                key=(pos,tuple(sorted(t.items()))) if self.position_sensitive_animals else tuple(sorted(t.items()))
                if key not in cache:cache[key]=self.animal_column(pos,t,prices,[4.0]*30,[2.0]*30,reuse=dp.free if self.animal_land_reuse else None)
                p=cache[key]
            elif isinstance(t,dict) and t.get('kind')=='PLANT':p=dp.existing(t,self.day)
            else:p=Project(0.0,())
            self.install(pos,p)
        self.value=self.objective()

    def priced_work(self):return [w*m for w,m in zip(self.work,self.labor_multiplier)]

    def objective(self):return self.book.score(self.flow)-labor_cost(self.priced_work())-self.cost

    def install(self,pos,p):
        if pos in self.parts:self.delta(self.parts[pos],-1)
        part=resources(p,pos);self.delta(part,1)
        self.projects[pos]=p;self.parts[pos]=part

    def delta(self,part,sign):
        flows,work,cost=part;self.cost+=sign*cost
        if getattr(self,'paid_stock',None):
            # Copy-on-write also isolates shallow cloned search/land models.
            self.investment=self.investment.copy()
            for key,n in part.investment.items():self.investment[key]+=sign*n
        for k in self.flow:
            for d in range(self.day,30):self.flow[k][d]+=sign*flows[k][d]
        for d in range(self.day,30):self.work[d]+=sign*work[d]

    def offer(self,pos,p):
        self.proposed+=1
        if self.projects[pos].days==p.days:return False
        old=self.parts[pos];part=resources(p,pos)
        self.delta(old,-1);self.delta(part,1)
        score=self.objective()
        if score>self.value+getattr(self,'offer_minimum_gain',1):
            self.value=score;self.projects[pos]=p;self.parts[pos]=part;self.accepted+=1
            return True
        self.delta(part,-1);self.delta(old,1)
        return False

    def reprice_critical_routes(self):
        """Use constructive routing subproblems to price calendar bottlenecks.

        This is a resource decomposition step, not a claim that the full future
        cash/inventory path is certified. New projects must pay the route cost
        they induce instead of a fixed average cost per action.
        """
        candidates=sorted(range(self.day,30),key=lambda d:self.work[d],reverse=True)
        days=sorted({self.day,29,*candidates[:2]})
        positions=[(4,4)]
        for _ in range(15):positions.append(min(((4,4),(5,4),(4,5),(5,5)),key=positions.count))
        report=[]
        for d in days:
            jobs=[];stock=Counter();output=0
            for pos,p in self.projects.items():
                row=p.first(d);ops=[list(o) for o in row[1]]
                if not ops:continue
                jobs.append(dict(pos=pos,ops=ops,priority=1,mandatory=True))
                output+=sum(n for _,n in row[2])
                for op in ops:
                    if op[0]=='FEED':stock['WHEAT']+=1
                    elif op[0]=='FERTILIZE':stock['FERTILIZER']+=1
                    elif op[0]=='PLACE' and op[1] in rules.ANIMALS:stock[op[1]]+=1
            if not jobs:continue
            # Full return on the last day; normal days leave legal overnight
            # stock. A future surplus adds a delivery service allowance below.
            lo=max(1,min(15,math.ceil(self.work[d]/22)-2));chosen=15;unassigned=0
            for hands in range(lo,16):
                workers=[dict(pos=p,hour=2,inv={}) for p in positions[:hands+1]]
                r=route_price(jobs,workers,dict(stock),last_hour=22 if d==29 else 23,
                    return_to_shed=d==29,end_service=3 if d==29 else 1,track_collect=True,evaluation_budget=2500)
                unassigned=len(r['unassigned'])
                if not unassigned:chosen=hands;break
            effective=(chosen+1)*21
            if d!=29 and output>100:effective+=min(35,(output-100)/5)
            if unassigned:effective+=sum(len(jobs[j]['ops'])+1 for j in r['unassigned'])
            factor=max(1.0,effective/max(1,self.work[d]))
            self.labor_multiplier[d]=max(self.labor_multiplier[d],factor)
            report.append(dict(day=d,workers=chosen+1,unassigned=unassigned,multiplier=round(factor,3)))
        self.value=self.objective()
        return report

    def optimize(self,budget,only_positions=None,sweeps=2):
        # Interleave locations so identical cohorts are not proposed in one
        # global jump. Every accepted column changes subsequent market prices.
        positions=sorted(self.projects if only_positions is None else only_positions,key=lambda p:(sd(p),p))
        animal_count=0;dp=None;prices=None;labor=None;refreshed_accepted=-1;animal_cache={}
        for sweep in range(sweeps):
            seq=positions if sweep==0 else positions[::-1]
            for index,pos in enumerate(seq):
                if index%5==0 and (dp is None or self.accepted!=refreshed_accepted):
                    prices=self.book.marginal(self.flow)
                    labor=[v*m for v,m in zip(shadows(self.priced_work()),self.labor_multiplier)]
                    dp=CropDP(prices,labor,[2.0]*30,start=self.day)
                    animal_cache={}
                    refreshed_accepted=self.accepted
                t=self.farm['tiles'][pos[1]][pos[0]]
                if isinstance(t,dict) and 'animal' in t:
                    key=(pos,tuple(sorted(t.items()))) if self.position_sensitive_animals else tuple(sorted(t.items()))
                    if key not in animal_cache:
                        animal_cache[key]=self.animal_column(pos,t,prices,labor,[2.0]*30,reuse=dp.free if self.animal_land_reuse else None)
                    p=animal_cache[key]
                    self.offer(pos,p)
                elif isinstance(t,dict) and t.get('kind')=='PLANT':
                    self.offer(pos,dp.existing(t,self.day))
                else:
                    p=dp.free[self.day]
                    if t not in (None,'LOCKED'):
                        row=p.first(self.day)
                        if row[1]:p=Project(p.value,((self.day,(('DIG',),)+row[1],row[2],row[3]),)+tuple(r for r in p.days if r[0]!=self.day))
                    self.offer(pos,p)
                    if sweep==0 and sd(pos)<=4 and self.day<=18 and budget>800:
                        for a in rules.ANIMALS:
                            cost=rules.ANIMALS[a]['cost']
                            if budget<cost+300:continue
                            p=animal_project(a,self.day,self.day,rules._new_animal(a,self.day),prices,labor,[2+sd(pos)/3]*30,reuse=dp.free if self.animal_land_reuse else None)
                            row=p.first(self.day)
                            build=(('BUILD_'+rules.ANIMALS[a]['structure'],),('PLACE',a))
                            if t not in (None,'LOCKED'):build=(('DIG',),)+build
                            p=Project(p.value-cost,((self.day,build+row[1],row[2],((a,1),)+row[3]),)+tuple(r for r in p.days if r[0]!=self.day))
                            if self.offer(pos,p):
                                budget-=cost;animal_count+=1
                                # Refresh before considering another species;
                                # a replacement refunds the former animal cost
                                # in the exact objective, but cash stays cautious.
                                prices=self.book.marginal(self.flow)
                                animal_cache={}
        return self.projects
