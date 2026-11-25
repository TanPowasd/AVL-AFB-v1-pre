from math import sqrt, log1p
from collections import Counter
from functools import lru_cache

CROPS = {
    'WHEAT': (10,2,4,0,4), 'CARROT': (20,2,3,0,3),
    'TOMATO': (50,8,11,1,4),'STRAWBERRY': (100,10,16,2,4),
    'MELON': (80,10,12,0,5)
}
ANIMALS = {'GOOSE':(300,'COOP',4,1,4,'EGG'), 'COW':(400,'PASTURE',8,2,6,'MILK'), 'SHEEP':(500,'PASTURE',6,3,6,'WOOL')}
SHOP = {'BAKERY':('EGG','WHEAT'), 'PIZZA_SHOP':('MILK','TOMATO','WHEAT'), 'BRUNCH_SPOT':('EGG','WHEAT','STRAWBERRY'), 'YARN_STORE':('WOOL',), 'ICE_CREAM_SHOP':('STRAWBERRY','MILK','WHEAT'), 'PET_CAFE':('CARROT',), 'SMOOTHIE_SHOP':('STRAWBERRY','MILK'), 'FARMERS_MARKET':('WHEAT','CARROT','TOMATO','STRAWBERRY')}
MP = {'WHEAT':(25,400,'r',.8,'l',.2),'CARROT':(35,450,'h',1,'r',.7), 'TOMATO':(60,200,'h',.4,'r',.6),'STRAWBERRY':(120,100,'r',.7,'n',1.6),'MELON':(250,300,'l',.2,'q',3.6),'EGG':(50,332,'h',.4,'l',.2),'MILK':(160,122,'r',.6,'n',1.6),'WOOL':(200,105,'l',.2,'q',3.2),'FERTILIZER':(100,200,'n',.4,'n',.4)}
HOME=((4,4),(5,4),(4,5),(5,5))
PASS=['PASS']

def shape(f, x, T):
    if f=='r': return sqrt(x/T)
    if f=='l': return log1p(x)/log1p(T)
    z=x/T
    if f=='h': return z+8*max(0,z-1)**2
    if f=='q': return z*z
    return z

def price(item, inv):
    b,t,f,a,g,c=MP[item]
    return max(1, round(b*(1+a*shape(f,10000-inv,t)))) if inv<10000 else max(1,round(b*(1-c*shape(g,inv-10000,t))))

def dist(a,b): return abs(a[0]-b[0])+abs(a[1]-b[1])

def path(a,b):
    dx,dy=b[0]-a[0], b[1]-a[1]
    return [['EAST' if dx>0 else 'WEST'] for _ in range(abs(dx))]+[['SOUTH' if dy>0 else 'NORTH'] for _ in range(abs(dy))]

def shed_point(a,b=None):
    if b is None: return min(HOME,key=lambda p:dist(a,p))
    return min(HOME,key=lambda p:dist(a,p)+dist(p,b))

def fib_total(n):
    a=b=1;s=0
    for _ in range(n): s+=a;a,b=b,a+b
    return s

class Agent:
    def __init__(self):
        self.routes=[]
        self.order=[]
        self.last_day=-1
        self.mv={}
        self.mtrack={}
        self.last_cnt=Counter()
        self.worker_target=0
        self.set_quota={}
        self.hold=Counter()

    def forecast(self,obs):
        d=obs['day']; inv=obs['market']['inventory']
        shops=obs['town']['unlocked_shops']
        dem=Counter({c:1 for c in MP if c!='FERTILIZER'})
        for shop in shops:
            if shop in SHOP:
                g=SHOP[shop]
                for c in g: dem[c]+=6 if len(g)>1 else 12
        other=obs['farms'][1-obs['player']]['tiles']
        theirs=Counter()
        for row in other:
            for v in row:
                if isinstance(v,dict):
                    c=v.get('crop') or v.get('animal')
                    if c in CROPS or c in ANIMALS: theirs[c]+=1
        self.theirs=theirs
        if d!=self.last_day:
            if self.last_day>=0:
                for c in MP:
                    likely=inv[c]-self.mtrack.get(c,inv[c])+dem[c]
                    self.mv[c]=.72*self.mv.get(c,0)+.28*max(0,likely)
            self.mtrack=dict(inv)
        self.consume=dem

    def projected(self,c,now,horizon,other=0,own=0):
        d=self.day
        horizon=max(0,min(horizon,29-d))
        cdem=self.consume[c]
        # Future unlocks add demand on average; duplicate stores are allowed.
        delta={'CARROT':2.25,'TOMATO':1.5,'WHEAT':3.75,'STRAWBERRY':3,
            'MILK':2.25,'EGG':1.5,'WOOL':1.5}.get(c,0)
        expected_unlocks=min(max(0,8-self.shopcount),horizon/3)
        demand=(cdem + .48*delta*expected_unlocks)*horizon
        if self.shopcount<8 and c in ('CARROT','TOMATO','EGG'):
            # Risk premium: polynomial scarcity responds disproportionately to an extra duplicate shop.
            demand+=.26*delta*expected_unlocks*horizon
        supp=max(0,other)+max(0,own)
        return now-demand+supp

    def price_future(self,c,h,other=0,own=0):
        return price(c,self.projected(c,self.inv[c],h,other,own))

    def make_economics(self,tiles):
        self.pcnt=Counter(); self.acnt=Counter(); self.pending={}
        d=self.day
        for y,row in enumerate(tiles):
            for x,t in enumerate(row):
                if not isinstance(t,dict): continue
                c=t.get('crop')
                if c in CROPS:
                    self.pcnt[c]+=1
                    _,lo,hi,interval,m=CROPS[c]
                    day=t['planted_day']+hi
                    self.pending.setdefault(c,[]).append((day,m))
                a=t.get('animal')
                if a in ANIMALS: self.acnt[a]+=1
        self.styles={}
        for c,(seed,lo,hi,interval,_) in CROPS.items():
            best=(-1,hi,0,0)
            for age in range(lo,hi+1):
                sell_day=d+age+1
                if sell_day>30: break
                if interval:
                    q=1+(age-lo)//interval
                else:
                    q=1+max(0,age-(hi+1)//2+1)
                if sell_day==30: endcoef=.85
                else: endcoef=1
                h=min(sell_day,29)-d
                own=sum(m for due,m in self.pending.get(c,()) if due<=d+h)
                if c=='CARROT': oth=self.theirs[c]*.64*h
                elif c=='WHEAT': oth=self.theirs[c]*.63*h
                elif c=='MELON': oth=self.theirs[c]*2.7*min(1,h/9)
                else: oth=self.theirs[c]*1.6*min(1,h/9)
                # Marginal price estimate includes overlapping growth cohorts.
                dilution={'MELON':30,'STRAWBERRY':16,'TOMATO':12,'CARROT':9,'WHEAT':6}[c]
                p=self.price_future(c,h,oth,own*.82+dilution)
                score=(p*q-seed)*endcoef/(age+.65)
                # Prefer quicker liquidity before first expansion.
                if self.money<1500 and d<9:
                    score*= 1.18 if age<=4 else .76
                if score>best[0]: best=(score,age,q,p)
            self.styles[c]=best
        self.astyles={}
        for a,(cost,_,lo,it,cap,c) in ANIMALS.items():
            active=29-d-lo
            if active<=1:
                self.astyles[a]=(-1,0,0);continue
            projected_yield=active*(1/it+.34)
            # Use an intermediate horizon, roughly the average harvest date.
            h=lo+active*.52
            p=self.price_future(c,h,self.theirs[a]*h*.43,self.acnt[a]*h*.50+12)
            fert=max(0,self.price_future('FERTILIZER',h,0,self.acnt.total()*3+80)-35)
            feed=price('WHEAT',self.inv['WHEAT']-70)*.54*(29-d)
            # Only count fertilizers that can actually be collected and sold without oversupply.
            benefit=projected_yield*p+min(8,29-d)*fert*.47-cost-feed
            self.astyles[a]=(benefit/(29-d)*.82,p,projected_yield)

    def choose_crop(self,counts,seed_left,cap):
        best=(-1,None)
        for c,(score,age,q,p) in self.styles.items():
            if self.day+age>29 or seed_left.get(c,0)<=0 or score<=0: continue
            n=counts.get(c,0)
            if n>=cap.get(c,100): continue
            if self.day<3 and c=='MELON' and n>=29: continue
            h=age+1
            step_volume=q*(1 if c!='CARROT' else 1.3)
            delta=self.price_future(c,h,0,step_volume*n+8)-self.price_future(c,h,0,8)
            val=score + delta*q/(age+.65)
            if val>best[0]: best=val,c
        return best

    def harvest(self,t):
        c=t['crop']; age=self.day-t['planted_day']; q=t['yield_units']
        seed,lo,hi,it,_=CROPS[c]
        if age<lo or q<=0: return False
        end=self.day==29
        if end: return True
        if it:
            if age>=hi or self.day==28: return True
            # Clear space before the next double-yield if manure is active.
            if t.get('fertilized_until_day',-1)>=self.day and q>=3: return True
            return q>=3 or q>=2 and self.day>=26
        if age>=hi: return True
        # Never let otherwise marketable produce disappear after the last reset.
        if self.day==28: return True
        p=max(1,self.price_future(c,1))
        p_next=max(1,self.price_future(c,2))
        if self.day<5 and c=='CARROT' and self.money<1400 and age==2: return True
        if c=='CARROT' and age==2 and self.styles[c][1]==2:
            return p*q*.92/(age+.65)>p_next*(q+1)/(age+1+.65)
        if c=='MELON' and age==11 and self.theirs[c]>=12:
            rival_ripening=sum(1 for tile in self.enemy_crop_rows
                if tile['crop']==c and self.day-tile['planted_day']>=10)
            if rival_ripening>=8 and self.inv_price[c]>=100: return True
        if self.theirs[c]>=8 and c in ('MELON','CARROT'):
            opp_due=0
            for tile in self.enemy_crop_rows:
                if tile['crop']==c and self.day+1-tile['planted_day']>=hi:
                    opp_due+=tile['yield_units']
            if opp_due>=20 and q*p > (q+1)*self.price_future(c,2,opp_due*.8)*1.03:
                return True
        return False

    def market_sales(self,obs,already_added=None):
        shed=Counter(obs['private']['shed'])
        if already_added: shed.update(already_added)
        d=self.day; h=obs['hour']
        options=[]
        nworkers=len(obs['farms'][obs['player']]['hands'])+1
        for c in MP:
            n=shed.get(c,0)
            if n<=0: continue
            keep=self.hold[c] if h else 0
            if c=='WHEAT' and d<29: keep=max(keep,min(65,int(self.acnt.total()*.60+3)) if self.acnt else 0)
            if c=='FERTILIZER' and d<28: keep=max(keep,min(14,self.acnt.total()//2))
            n=max(0,n-keep)
            if n<=0: continue
            # Monotonic scarcity markets sometimes merit a modest inventory reserve.
            # Do not risk the overnight 100-unit shared shed limit.
            if d<27 and h<20 and self.money>2800 and c in ('CARROT','TOMATO','EGG') and not already_added:
                demand=self.consume[c]; movement=self.mv.get(c,0)
                if demand>=10 and demand>movement*1.45 and sum(shed.values())<=35:
                    if obs['market']['prices'][c]<self.price_future(c,3,3*max(0,movement))*0.82:
                        continue
            p=obs['market']['prices'].get(c,0)
            severity=p-price(c,self.inv[c]+min(80,n+self.theirs[c]//2))
            options.append((n**.42*(severity+2)+.018*p,c,n))
        options.sort(reverse=True)
        return [['SELL',c,int(n)] for p,c,n in options]

    def animal_care_model(self):
        d=self.day; acnt=self.acnt; inv=self.inv
        feedcost=price('WHEAT',inv['WHEAT']-max(25,acnt.total()))+18
        income={}
        for a,(_,_,lo,interval,_,item) in ANIMALS.items():
            p=price(item,inv[item])
            income[a]=[max(1, price(item,self.projected(item,inv[item],k,(
                self.theirs[a]*.45+acnt[a]*(1/interval+.16))*k))*.33+p*.67) for k in range(31-d)]
        @lru_cache(None)
        def dp(a,t,age,miss,pending):
            if t==29: return 0,False,False
            _,_,lo,interval,maxq,_=ANIMALS[a]
            prod=age+1>=lo and (age+1-lo)%interval==0
            p=income[a][t-d]
            best=(-1e20,False,False)
            for fed in (False,True):
                if not fed and miss>=1 and t<28:continue
                live=fed or miss<1
                for care in (False,True):
                    if care and (not fed or t>=27):continue
                    reward=(min(maxq,1+pending*int(fed))*p if prod and live else 0)-feedcost*fed-14*care
                    future=dp(a,t+1,age+1,int(not fed),min(maxq-1,(0 if prod else pending)+int(care)))[0] if live else 0
                    val=reward+future*.972
                    if val>best[0]:best=(val,fed,care)
            return best
        return dp

    def build_plan(self,obs,me):
        d=self.day
        coords=[tuple(me['farmer'])]+[tuple(p) for p in me['hands']]
        route=[[] for p in coords]
        flags=[{'wheat':0,'fert':0,'animal':None,'harvest':0} for p in coords]
        jobs={}; counts=Counter(); start_seeds=Counter(obs['private']['seeds'])
        seeds=Counter(start_seeds); harvest_total=0; fert_count=0; feed_count=0
        purchases=Counter(); animal_targets=Counter()
        lim=23-self.init_turn-int(d==29)
        tiles=me['tiles']
        free=sum(t is None or isinstance(t,dict) and t.get('kind')=='WEED' for row in tiles for t in row)
        cash=self.money+sum(n*self.inv_price[c]*.75 for _,c,n in self.market_sales(obs) if c not in ('FERTILIZER','WHEAT'))
        # Leave cash for planned inputs and next day's field crew.
        budget=max(0,cash-(100 if d<10 else 260)-self.acnt.total()*7)
        cnt_fert=obs['private']['shed'].get('FERTILIZER',0)
        fert_price=price('FERTILIZER',self.inv['FERTILIZER']-50)
        feed_price=price('WHEAT',self.inv['WHEAT']-150)
        existing_feed=obs['private']['shed'].get('WHEAT',0)
        care_plan=self.animal_care_model()
        plant_ca=0
        if d<=1 and (self.money<3500) and self.styles['CARROT'][0]>0:
            plant_ca=max(0,10-self.pcnt['CARROT'])
        for y,row in enumerate(tiles):
            for x,t in enumerate(row):
                j=(x,y)
                if t=='LOCKED': continue
                if t is None or t.get('kind')=='WEED':
                    if d<=27: jobs[j]=(12,[['DIG']] if t else [],'empty',0,None)
                    continue
                if t.get('kind')=='PLANT':
                    c=t['crop']; age=d-t['planted_day']
                    _,lo,hi,it,maxq=CROPS[c]
                    willcut=self.harvest(t)
                    required=t.get('consecutive_unwatered',0)>=1
                    yield_day=not it and (hi+1)//2<=age<=hi and t['yield_units']<maxq+(c in ('MELON','CARROT'))
                    continues=it and 0<=age+1-lo<=hi-lo and (age+1-lo)%it==0
                    fertilizer_occasion=(continues and not willcut and (age==lo-1 or age==lo-1+2*it and it==2))
                    fertilize=(c in ('MELON','CARROT','STRAWBERRY','TOMATO') and
                        t.get('fertilized_until_day',-1)<d and
                        ((not it and age==(hi+1)//2 and age+1<=hi) or fertilizer_occasion) and
                        self.price_future(c,max(1,hi-age+1),self.theirs[c],self.pcnt[c]*(3 if c=='MELON' else 1))>max(100,fert_price*1.65)
                        and d<28 and (cnt_fert>0 or cash>5500))
                    water=not endday(d) and (required or yield_day or continues and (t.get('fertilized_until_day',-1)>=d or fertilize))
                    cmds=[]
                    q=t['yield_units']
                    if fertilize: cmds.append(['FERTILIZE'])
                    if water:
                        cmds.append(['WATER'])
                        if yield_day: q+=2 if fertilize or t.get('fertilized_until_day',-1)>=d else 1
                    if willcut: cmds.append(['HARVEST'])
                    q=min(q,maxq+(c in ('CARROT','MELON'))) if willcut else 0
                    if cmds:
                        earn=q*self.price_future(c,1)
                        priority=(59+min(45,earn/35) if willcut else 43+min(20,self.styles[c][0]/10) if required else 28)
                        jobs[j]=(priority,cmds,'crop',q,c)
                    continue
                a=t.get('animal')
                if a in ANIMALS:
                    cost,_,lo,it,cap,c=ANIMALS[a]
                    age=d-t['placed_day']; q=t['yield_units']
                    daily_fert=price('FERTILIZER',self.inv['FERTILIZER']+max(0,self.acnt.total()//2))
                    _,fed,care=care_plan(a,d,age,t.get('consecutive_unfed',0),t.get('pending_care_bonus',0))
                    cmds=[]
                    if fed: cmds.append(['FEED'])
                    if q and (q>=cap-1 or d>=28 or q>=2 and it>=2 or q>=2 and self.price_future(c,1)<self.inv_price[c]*.97): cmds.append(['HARVEST'])
                    if care:
                        cmds.append(['CARE'])
                    if t.get('fertilizer_available') and daily_fert>=40 and (fed or cmds or daily_fert>=70):
                        cmds.append(['COLLECT_FERTILIZER'])
                    if cmds:
                        q=(q if ['HARVEST'] in cmds else 0)+(['COLLECT_FERTILIZER'] in cmds)
                        jobs[j]=(62 if fed else 39 if ['HARVEST'] in cmds else 18,cmds,'animal',q,c)
                    continue
                if t.get('kind') in ('COOP','PASTURE') and d<28:
                    jobs[j]=(17,[],'empty',0,t.get('kind'))

        def crop_choice():
            proposed=Counter(seeds)
            for c,(seed,*_) in CROPS.items():
                if budget>=seed: proposed[c]+=1
            if counts['CARROT']<plant_ca and d+3<=29 and proposed['CARROT']>0:
                return 'CARROT'
            return self.choose_crop(counts,proposed,{})[1]

        def animal_choice(crop,typ):
            best=(0,None)
            crop_score=self.styles[crop][0] if crop else 0
            for a,(cost,st,lo,it,cap,c) in ANIMALS.items():
                if typ and st!=typ: continue
                if budget<cost+110 or self.acnt.total()+animal_targets.total()>=38: continue
                if d<=2:
                    if a=='GOOSE' and our_early(a,animal_targets,self.acnt)<(5 if d==0 else 7):
                        if budget>cost+max(180,plant_ca*20): return a
                    continue
                if cash<750: continue
                if d+lo>=27: continue
                new=animal_targets[a]
                our=self.acnt[a]+new
                horizon=lo+(29-d-lo)*.50
                if our>=13+max(0,int(self.consume[c]*.7)): continue
                flow=our*horizon*(1/it+.22)
                p=self.price_future(c,horizon,self.theirs[a]*horizon*.47,flow+12)
                orig=self.astyles[a][1]
                val=self.astyles[a][0]+(p-orig)*self.astyles[a][2]/(29-d)*.82
                if val>max(16,crop_score*1.24,best[0]): best=val,a
            return best[1]

        projected_harvest=sum(q for _,_,_,q,_ in jobs.values())
        drop_teams=min(len(flags)//2,max(0,(projected_harvest-90+14)//23)) if d>=19 else 0
        for i,f in enumerate(flags):
            f['drop']=d==29 or i<drop_teams
        # Parallel cheapest-edge construction. Each job is removed on assignment.
        while jobs:
            best=(-1,None)
            cnext=crop_choice()
            anext=animal_choice(cnext,None) if free and d<=22 else None
            for i,(a,r,f) in enumerate(zip(coords,route,flags)):
                left=lim-len(r)
                if left<1: continue
                for b,(pri,actions,k,q,c) in jobs.items():
                    if d<29 and harvest_total+q>88 and not f['drop']: continue
                    move=dist(a,b)
                    acts=actions
                    pick=None
                    if k=='empty':
                        animal=anext if c is None else animal_choice(None,c)
                        if animal and (not f['animal'] or f['animal']==animal):
                            if f.get('animals',0)>=3: animal=None
                        if animal and (not f['animal'] or f['animal']==animal):
                            _,kind,_,_,_,_=ANIMALS[animal]
                            acts=actions+([['BUILD_COOP' if kind=='COOP' else 'BUILD_PASTURE']] if c is None else [])+[['PLACE',animal,1]]
                            if f['animal'] is None: pick=(animal,0)
                            pri=19+min(16,self.astyles[animal][0]/20)
                        elif cnext:
                            # On the opening day do not tie up all the funds in slow crops.
                            if d<3 and cnext=='MELON' and budget<240 and seeds[cnext]<=0: continue
                            acts=actions+([['DIG']] if c else [])+[['PLANT',cnext],['WATER']]
                            pri=12+min(17,self.styles[cnext][0]/25)
                        else: continue
                    elif k=='crop' and ['HARVEST'] in actions and 13<=d<28 and cnext and self.styles[cnext][0]>=55 and (
                        not CROPS[c][3] or d-tiles[b[1]][b[0]]['planted_day']>=CROPS[c][2]):
                        more=([['DIG']] if CROPS[c][3] else [])+[['PLANT',cnext],['WATER']]
                        if move+len(actions)+len(more)<=left-(dist(b,shed_point(b))+1 if f['drop'] else 0):
                            acts=actions+more
                            pri+=4
                    if k=='animal' and ['FEED'] in actions:
                        if f['wheat']>=6: continue
                        if f['wheat']==0: pick=('WHEAT',6)
                    elif acts and acts[0]==['FERTILIZE']:
                        if fert_count>=45 or f['fert']>=6 or budget<fert_price+50:
                            acts=acts[1:]
                            if not acts: continue
                        elif f['fert']==0: pick=('FERTILIZER',6)
                    if pick:
                        p=shed_point(a,b)
                        move=dist(a,p)+dist(p,b)+1
                    else: p=None
                    cost=move+len(acts)
                    if cost+(dist(b,shed_point(b))+1 if f['drop'] and (q>0 or f['harvest']) else 0)>left: continue
                    if q+f['harvest']>80: continue
                    merit=pri/(max(1,cost)**.53)+.021*left
                    if merit>best[0]:
                        best=(merit,(i,b,p,pick,acts,q,k,c))
            if best[1] is None: break
            i,b,p,pick,acts,q,k,c=best[1]; f=flags[i]
            a=coords[i]
            if pick:
                route[i].extend(path(a,p))
                route[i].append(['PICKUP',pick[0],pick[1]])
                if pick[0] in ANIMALS: f['animal_marker']=route[i][-1];f['animal']=pick[0]
                a=p
            route[i].extend(path(a,b)); route[i].extend(acts); coords[i]=b
            harvest_total+=q if not f['drop'] else 0; f['harvest']+=q
            del jobs[b]
            for cmd in acts:
                op=cmd[0]
                if op=='PLANT':
                    cc=cmd[1];counts[cc]+=1
                    if seeds[cc]>0: seeds[cc]-=1
                    else: purchases[cc]+=1;budget-=CROPS[cc][0]
                elif op=='PLACE' and cmd[1] in ANIMALS:
                    aa=cmd[1];animal_targets[aa]+=1
                    f['animals']=f.get('animals',0)+1
                    budget-=ANIMALS[aa][0]
                elif op=='FEED':
                    f['wheat']+=1;feed_count+=1
                    if feed_count>existing_feed:budget-=feed_price
                elif op=='FERTILIZE':f['fert']+=1;fert_count+=1;budget-=min(0,fert_price) if cnt_fert>=fert_count else fert_price
        self.order=[]
        for c,n in purchases.items():
            if n: self.order.append(['BUY_SEED',c,n])
        for a,n in animal_targets.items():
            n=max(0,n-obs['private']['shed'].get(a,0))
            if n:self.order.append(['BUY_ANIMAL',a,n])
        self.supplies=Counter()
        for i,(a,r,f) in enumerate(zip(coords,route,flags)):
            if 'animal_marker' in f: f['animal_marker'][2]=f['animals']
            for cmd in r:
                if cmd[0]=='PICKUP' and cmd[1] in ('WHEAT','FERTILIZER'):
                    cmd[2]=f['wheat' if cmd[1]=='WHEAT' else 'fert']
                    self.supplies[cmd[1]]+=cmd[2]
            if f['drop'] and f['harvest']:
                r.extend(path(a,shed_point(a))); r.append(['DROP'])
        self.routes=route
        self.hold=Counter(self.supplies)

    def act(self,obs):
        me=obs['farms'][obs['player']]; d=obs['day']; h=obs['hour']
        self.day=d; self.money=me['money']; self.inv=obs['market']['inventory']; self.inv_price=obs['market']['prices']
        self.shopcount=len(obs['town']['unlocked_shops'])
        tiles=me['tiles']
        self.enemy_crop_rows=[t for row in obs['farms'][1-obs['player']]['tiles'] for t in row if isinstance(t,dict) and t.get('crop') in CROPS]
        if d!=self.last_day or h==0:
            self.forecast(obs)
            self.make_economics(tiles)
            self.last_day=d
        sales=self.market_sales(obs)
        acts=[PASS]*(1+len(me['hands']))
        if h==0:
            self.hold.clear()
            wealth=self.money+sum(x[2]*obs['market']['prices'][x[1]]*.77 for x in sales)
            nland=len(me['unlocked_quadrants'])
            buy=nland<4 and 0<d<23 and wealth>=[0,2400,4700,7900][nland]
            workers=({1:7,2:8,3:10,4:11}[nland+int(buy)])-1
            if wealth<650 and d<13: workers=max(3,workers-2)
            if nland<=2 and d in (10,11,12,28,29): workers+=1
            if nland>=3 and d>=24: workers+=1
            if wealth>16000 and nland>=3: workers+=1
            # On day 29 longer return routes justify a stronger but capped crew.
            if nland>=3 and d>=28:workers+=1
            target=min(13,workers)
            while target>3 and fib_total(target)>wealth*.038: target-=1
            self.worker_target=target
            nxt=['BUY_LAND'] if buy and self.money>=[0,1000,2000,4000][nland] else None
            # More than ten market orders may be needed to staff a busy day.
            front=sales[:min(3,len(sales))]
            orders=front+([nxt] if nxt else [])
            orders+= [['HIRE']] * min(target,8,10-len(orders))
            if me['hires_today']+sum(o[0]=='HIRE' for o in orders)<target: self.init_turn=2
            else: self.init_turn=1
            return {'farmer':PASS,'hands':acts[1:], 'market':(orders+sales[len(front):])[:10]}
        if h==1 and me['hires_today']<self.worker_target:
            return {'farmer':PASS,'hands':acts[1:], 'market':([['HIRE']]*(self.worker_target-me['hires_today'])+sales)[:10]}
        init=self.init_turn
        if h==init:
            self.build_plan(obs,me)
            # Reserve ingredients for routing and free other shed space first.
            inp=[]
            for c,n in self.supplies.items():
                need=n-obs['private']['shed'].get(c,0)
                if need>0:inp.append(['BUY_PRODUCT',c,need])
            orders=(self.market_sales(obs)+inp+self.order)[:10]
            self.pending_orders=(inp+self.order)[max(0,10-len(self.market_sales(obs))):]
        else:
            for i in range(min(len(acts),len(self.routes))):
                if self.routes[i]: acts[i]=self.routes[i].pop(0)
            new=Counter()
            capacity=100-sum(obs['private']['shed'].values())
            for i,a in enumerate(acts):
                if a[0]=='DROP':
                    volume=sum(obs['private']['inventories'][i].values())
                    if volume>capacity and h<22:
                        acts[i]=PASS
                        self.routes[i].insert(0,a)
                    else:
                        capacity-=volume
            for i,a in enumerate(acts):
                if a[0]=='DROP': new.update(obs['private']['inventories'][i])
                if a[0]=='PICKUP':
                    self.hold[a[1]]-=a[2]
                    new[a[1]]-=int(a[2])
            orders=(getattr(self,'pending_orders',[])+self.market_sales(obs,new))[:10]
            self.pending_orders=[]
        return {'farmer':acts[0], 'hands':acts[1:], 'market':orders}

def endday(d):
    return d>=29

def our_early(a, planned, current):
    return planned[a]+current[a]
