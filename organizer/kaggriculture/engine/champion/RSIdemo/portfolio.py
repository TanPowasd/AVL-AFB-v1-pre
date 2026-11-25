# Adapted from user-provided d15; original hashes in references/d15/manifest.json.
"""Whole-farm column exchange with full-portfolio acceptance.

Individual project DP proposes a move. The nonlinear shared-market objective
and dated labor curve decide whether that move is accepted. A synchronized
block of identical per-tile best responses is deliberately never committed.
"""
from collections import Counter
import math
from .projects import CropDP, Project, animal_project
from .forecast import empty_flows,add_project,cohort_supply
from .market_model import MarketModel
from . import rules
from .routes import solve_routes as route_price
from .budget import check

def sd(p):return max(0,4-p[0],p[0]-5)+max(0,4-p[1],p[1]-5)
def wage(n):
    a,b,total=1,1,0
    for _ in range(n):total,a,b=total+a,b,a+b
    return total

def resources(project,pos):
    flow=empty_flows();add_project(flow,project)
    work=[0.0]*30;cost=0.0
    for d,ops,output,inputs in project.days:
        if ops:work[d]=len(ops)+1.4+sd(pos)/8+sum(n for k,n in output)/8
        for k,n in inputs:
            if k.startswith('SEED_'):cost+=n*rules.CROPS[k[5:]]['seed']
            elif k in rules.ANIMALS:cost+=n*rules.ANIMALS[k]['cost']
    return flow,work,cost

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
        self.animal_land_reuse=bool(obs.get('_rsi_animal_reuse',self.animal_land_reuse))
        self.obs=obs;self.day=obs['step']//24;self.book=self.book_type(obs)
        self.flow=empty_flows();self.work=[0.0]*30;self.cost=0.0
        self.projects={};self.parts={};self.accepted=0;self.proposed=0
        self.column_pool={}
        self.farm=obs['farms'][obs['player']]
        self.labor_multiplier=[1.0]*30
        self.upfront=Counter()
        self.total_investment=Counter()
        self.investment_budget=float('inf')
        self.paid_stock=Counter({'SEED_'+k:n for k,n in obs['private']['seeds'].items()})
        self.paid_stock.update({a:obs['private']['shed'].get(a,0) for a in rules.ANIMALS})
        for bag in obs['private']['inventories']:
            self.paid_stock.update({a:bag.get(a,0) for a in rules.ANIMALS})
        preliminary=cohort_supply(self.farm,self.day,1,1)
        prices=self.book.marginal(preliminary)
        dp=CropDP(prices,[4.0]*30,[2.0]*30,start=self.day)
        cache={}
        for pos in positions:
            check()
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

    def objective(self):
        credit=0
        if self.obs.get('_rsi_paid_costs'):
            # Past purchases have already reduced observed cash. Only the
            # portion actually used by this portfolio offsets future cost.
            credit=sum(min(n,self.paid_stock[k])*(rules.CROPS[k[5:]]['seed'] if k.startswith('SEED_')
                       else rules.ANIMALS[k]['cost']) for k,n in self.total_investment.items() if n>0)
        return self.book.score(self.flow)-labor_cost(self.priced_work())-self.cost+credit

    def install(self,pos,p):
        if pos in self.parts:
            self.delta(self.parts[pos],-1)
            self.upfront.subtract(self.today_investment(self.projects[pos]))
            self.total_investment.subtract(self.all_investment(self.projects[pos]))
        part=resources(p,pos);self.delta(part,1)
        self.projects[pos]=p;self.parts[pos]=part
        self.upfront.update(self.today_investment(p))
        self.total_investment.update(self.all_investment(p))
        if self.obs.get('_rsi_record_columns'):
            pool=self.column_pool.setdefault(pos,{})
            pool[p.days]=p
            while len(pool)>8:pool.pop(next(iter(pool)))

    def all_investment(self,project):
        result=Counter()
        for day,ops,outputs,inputs in project.days:
            for k,n in inputs:
                if k.startswith('SEED_') or k in rules.ANIMALS:result[k]+=n
        return result

    def today_investment(self,project):
        result=Counter()
        for k,n in project.first(self.day)[3]:
            if k.startswith('SEED_') or k in rules.ANIMALS:result[k]+=n
        return result

    def upfront_cost(self,needed=None):
        required=self.upfront if needed is None else needed
        return sum(max(0,n-self.paid_stock[k])*(rules.CROPS[k[5:]]['seed'] if k.startswith('SEED_')
                   else rules.ANIMALS[k]['cost']) for k,n in required.items())

    def delta(self,part,sign):
        flows,work,cost=part;self.cost+=sign*cost
        for k in self.flow:
            for d in range(self.day,30):self.flow[k][d]+=sign*flows[k][d]
        for d in range(self.day,30):self.work[d]+=sign*work[d]

    def offer(self,pos,p):
        check()
        if self.obs.get('_rsi_record_columns'):
            pool=self.column_pool.setdefault(pos,{})
            pool[p.days]=p
            while len(pool)>8:pool.pop(next(iter(pool)))
        self.proposed+=1
        if self.projects[pos].days==p.days:return False
        upfront=self.upfront.copy()
        upfront.subtract(self.today_investment(self.projects[pos]))
        upfront.update(self.today_investment(p))
        # Replacing a proposal refunds its actual inputs. A pre-existing root
        # may exceed budget; allow exchanges that reduce, but do not increase it.
        if self.upfront_cost(upfront)>max(self.investment_budget,self.upfront_cost()):return False
        old=self.parts[pos];part=resources(p,pos)
        old_investment=self.total_investment.copy()
        self.total_investment.subtract(self.all_investment(self.projects[pos]))
        self.total_investment.update(self.all_investment(p))
        self.delta(old,-1);self.delta(part,1)
        try:
            score=self.objective()
        except BaseException:
            self.delta(part,-1);self.delta(old,1)
            self.total_investment=old_investment
            raise
        if score>self.value+1:
            self.value=score;self.projects[pos]=p;self.parts[pos]=part;self.accepted+=1
            self.upfront=upfront
            return True
        self.delta(part,-1);self.delta(old,1)
        self.total_investment=old_investment
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
            check()
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
                check()
                workers=[dict(pos=p,hour=2,inv={}) for p in positions[:hands+1]]
                r=route_price(jobs,workers,dict(stock),last_hour=22 if d==29 else 23,
                    return_to_shed=d==29,end_service=3 if d==29 else 1,track_collect=True,evaluation_budget=2500,hard_timeout_ms=30)
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
        self.investment_budget=max(0,budget)
        # Interleave locations so identical cohorts are not proposed in one
        # global jump. Every accepted column changes subsequent market prices.
        positions=sorted(self.projects if only_positions is None else only_positions,key=lambda p:(sd(p),p))
        animal_count=0;dp=None;prices=None;labor=None;refreshed_accepted=-1;animal_cache={}
        for sweep in range(sweeps):
            seq=positions if sweep==0 else positions[::-1]
            for index,pos in enumerate(seq):
                check()
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
                    headroom=self.investment_budget-self.upfront_cost()
                    if sweep==0 and sd(pos)<=4 and self.day<=18 and headroom>300:
                        for a in rules.ANIMALS:
                            cost=rules.ANIMALS[a]['cost']
                            replacing=self.today_investment(self.projects[pos])
                            refunded=self.upfront_cost()-self.upfront_cost(self.upfront-replacing)
                            if headroom+refunded<cost:continue
                            p=animal_project(a,self.day,self.day,rules._new_animal(a,self.day),prices,labor,[2+sd(pos)/3]*30,reuse=dp.free if self.animal_land_reuse else None)
                            row=p.first(self.day)
                            build=(('BUILD_'+rules.ANIMALS[a]['structure'],),('PLACE',a))
                            if t not in (None,'LOCKED'):build=(('DIG',),)+build
                            p=Project(p.value-cost,((self.day,build+row[1],row[2],((a,1),)+row[3]),)+tuple(r for r in p.days if r[0]!=self.day))
                            if self.offer(pos,p):
                                headroom=self.investment_budget-self.upfront_cost();animal_count+=1
                                # Refresh before considering another species;
                                # a replacement refunds the former animal cost
                                # in the exact objective, but cash stays cautious.
                                prices=self.book.marginal(self.flow)
                                animal_cache={}
        return self.projects
