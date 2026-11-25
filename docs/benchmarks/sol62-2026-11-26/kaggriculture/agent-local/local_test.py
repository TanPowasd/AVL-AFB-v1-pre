"""Independent partial rules model for replaying one or two copies of the agent.
It is not the official engine. Illegal actions and terminal results are diagnostic.
"""
import sys,os,random,time,json
from collections import Counter
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'solution'))
from main import Agent, CROPS, ANIMALS, HOME, SHOP, price, MP
PARS={'WHEAT':(2,4,0,4), 'CARROT':(2,3,0,4), 'MELON':(10,12,0,6),'TOMATO':(8,8,1,4),'STRAWBERRY':(10,10,2,4)}
directions={'NORTH':(0,-1),'SOUTH':(0,1),'EAST':(1,0),'WEST':(-1,0)}
class Harness:
    def __init__(self,scen,mode):
        self.scen=scen;self.mode=mode
        self.swap='--swap' in sys.argv
        self.seat=1 if self.swap else 0
        self.fixed=next((a[8:] for a in sys.argv if a.startswith('--shops=')), '')
        self.b=[dict(farmer=[4,4],hands=[],money=3000,unlocked_quadrants=['NW'],hires_today=0,tiles=[[None if x<5 and y<5 else 'LOCKED' for x in range(10)] for y in range(10)]) for _ in range(2)]
        self.p=[dict(shed=Counter(),seeds=Counter(),inventories=[Counter()]) for _ in range(2)]
        self.I={x:10000 for x in MP};self.shops=[];self.agent=[Agent(),Agent()]
        self.events=Counter();self.max_step=0;self.dailies=[];self.sell=Counter()
        if mode=='v6':
            from v6_copy import Agent as OldAgent
            self.agent[1]=OldAgent()
        if self.swap:
            self.agent.reverse()
    def obs(self,i,s):
        d,h=divmod(s,24)
        return dict(step=s,day=d,hour=h,player=i,remainingOverageTime=60.,farms=self.b,private=self.p[i],market=dict(inventory=self.I,prices={c:price(c,self.I[c]) for c in MP}),town=dict(unlocked_shops=self.shops))
    def units(self,i,s,a):
        d,h=divmod(s,24)
        b,p=self.b[i],self.p[i]
        pos=[b['farmer']]+b['hands']; actions=[a['farmer']]+a['hands']
        assert len(pos)==len(actions)
        plants=Counter(op[1] for op in actions if op[0]=='PLANT')
        short_seeds={c for c,n in plants.items() if n>p['seeds'][c]}
        for j,(pt,act) in enumerate(zip(pos,actions)):
            inv=p['inventories'][j]; op=act[0]; x,y=pt;t=b['tiles'][y][x]
            if op=='PASS':continue
            if op in directions:
                dx,dy=directions[op];xx,yy=x+dx,y+dy
                assert 0<=xx<10 and 0<=yy<10
                pt[:]=[xx,yy];continue
            if i==self.seat:self.events[op]+=1
            if op=='PLANT':
                c=act[1];lo,hi,it,m=PARS[c]

                if c in short_seeds or p['seeds'][c]<1 or t is not None:
                    if i==self.seat:self.events['fail_'+op]+=1
                    continue
                p['seeds'][c]-=1
                b['tiles'][y][x]=dict(kind='PLANT',crop=c,planted_day=d,watered_today=False,consecutive_unwatered=1,yield_units=0 if it else 1,max_lifespan_step=-1 if it else (d+hi+1)*24,fertilized_until_day=-1)
            elif op=='WATER' and isinstance(t,dict) and t.get('crop'):
                lo,hi,it,m=PARS[t['crop']]
                if not it and (hi+1)//2<=d-t['planted_day']<=hi and not t['watered_today']:
                    t['yield_units']=min(m,t['yield_units']+1+int(t['fertilized_until_day']>=d))
                t['watered_today']=True
            elif op=='DIG' and isinstance(t,dict):
                b['tiles'][y][x]=None
            elif op=='FERTILIZE' and inv['FERTILIZER']>0 and isinstance(t,dict) and t.get('crop'):
                inv['FERTILIZER']-=1;t['fertilized_until_day']=d+2
            elif op=='HARVEST' and isinstance(t,dict):
                c=t.get('crop')
                if c and d-t['planted_day']>=PARS[c][0] and t['yield_units']>0:
                    inv[c]+=t['yield_units'];t['yield_units']=0
                    if not PARS[c][2]:b['tiles'][y][x]=None
                elif t.get('animal'):
                    inv[ANIMALS[t['animal']][-1]]+=t['yield_units'];t['yield_units']=0
                elif i==self.seat:self.events['fail_'+op]+=1
            elif op=='DROP' and (x,y) in HOME:
                for c,n in list(inv.items()):
                    p['shed'][c]+=min(n,100-p['shed'].total())
                inv.clear()
            elif op=='PICKUP' and (x,y) in HOME:
                c,n=act[1:];take=min(n,p['shed'][c]);p['shed'][c]-=take;inv[c]+=take
                if i==self.seat and take<n: self.events['short_'+c]+=n-take
            elif op in ('BUILD_COOP','BUILD_PASTURE') and t is None:
                b['tiles'][y][x]=dict(kind=op[6:])
            elif op=='PLACE' and isinstance(t,dict) and act[1] in ANIMALS and inv[act[1]]>0 and not t.get('animal'):
                c=act[1];inv[c]-=1;t.update(animal=c,placed_day=d,yield_units=0,consecutive_unfed=0,fed_today=False,cared_today=False,fertilizer_available=False,pending_care_bonus=0)
            elif op=='FEED' and inv['WHEAT']>0 and t.get('animal'):
                inv['WHEAT']-=1;t['fed_today']=True
            elif op=='CARE' and t.get('animal'):
                t['cared_today']=True
            elif op=='COLLECT_FERTILIZER' and t.get('fertilizer_available'):
                t['fertilizer_available']=False;inv['FERTILIZER']+=1
            elif i==self.seat:self.events['fail_'+op]+=1
    def market(self,actions):
        for k in range(10):
            slot=[a['market'][k] if k<len(a['market']) else ['PASS'] for a in actions]
            remaining=[]
            for i,cmd in enumerate(slot):
                op=cmd[0];b=self.b[i]
                if op=='HIRE':
                    a=z=1
                    for _ in range(b['hires_today']):a,z=z,a+z
                    if b['money']>=a:
                        b['money']-=a;n=Counter(map(tuple,[b['farmer']]+b['hands']));b['hands'].append(list(min(HOME,key=lambda p:n[p])))
                        b['hires_today']+=1;self.p[i]['inventories'].append(Counter())
                elif op=='BUY_LAND':
                    z=len(b['unlocked_quadrants'])
                    if z<4 and b['money']>=(1<<(z-1))*1000:
                        b['money']-=(1<<(z-1))*1000
                        b['unlocked_quadrants'].append(['NW','NE','SW','SE'][z])
                        for yy in range((z//2)*5,(z//2)*5+5):
                            for xx in range((z%2)*5,(z%2)*5+5):b['tiles'][yy][xx]=None
                remaining.append(cmd[2] if len(cmd)==3 else 0)
            while any(remaining):
                quotes=[]
                for i,cmd in enumerate(slot):
                    if remaining[i]:
                        op,c,_=cmd
                        pr=price(c,self.I[c]-(op=='BUY_PRODUCT')) if c in self.I else CROPS[c][0] if c in CROPS else ANIMALS[c][0]
                        if op=='BUY_SEED':pr=CROPS[c][0]
                    else:pr=0
                    quotes.append(pr)
                for i,cmd in enumerate(slot):
                    if not remaining[i]:continue
                    b,p=self.b[i],self.p[i];op,c,_=cmd;pr=quotes[i]
                    if op=='SELL':
                        if not p['shed'][c]:remaining[i]=0;continue
                        p['shed'][c]-=1;b['money']+=pr
                        if pr>1:self.I[c]+=1
                        if i==self.seat:self.sell[c]+=pr
                    else:
                        if pr>b['money'] or op!='BUY_SEED' and p['shed'].total()>=100:remaining[i]=0;continue
                        b['money']-=pr
                        if op=='BUY_SEED':p['seeds'][c]+=1
                        else:p['shed'][c]+=1
                        if op=='BUY_PRODUCT':self.I[c]-=1
                    remaining[i]-=1
    def tick(self,s):
        d,h=divmod(s,24)
        if h%4==0:
            for shop in self.shops:
                for c in SHOP[shop]:self.I[c]-=2 if len(SHOP[shop])==1 else 1
        if h==0:
            for c in MP:
                if c!='FERTILIZER':self.I[c]-=1
        rng=random.Random(self.scen*1000003^d)
        for i,b in enumerate(self.b):
            for y,row in enumerate(b['tiles']):
                for x,t in enumerate(row):
                    if not isinstance(t,dict):
                        if t is None and h==23 and rng.random()<.005:row[x]=dict(kind='WEED')
                        continue
                    c=t.get('crop')
                    if c:
                        lo,hi,it,m=PARS[c]; mx=t['max_lifespan_step']
                        if mx>=0 and s>=mx and (s-mx)%2==0:
                            t['yield_units']-=1
                            if t['yield_units']<=0:row[x]=dict(kind='WEED');continue
                        if h==23:
                            wet=t['watered_today'];t['watered_today']=False
                            t['consecutive_unwatered']=0 if wet else t['consecutive_unwatered']+1
                            if t['consecutive_unwatered']>=2:
                                if i==self.seat:self.events['dead_'+c]+=1
                                row[x]=dict(kind='WEED');continue
                            k=d+1-t['planted_day']-lo
                            if it and k>=0 and k%it==0 and k//it<m:
                                t['yield_units']=min(m,t['yield_units']+1+int(wet and t['fertilized_until_day']>=d))
                                if k//it+1==m:t['max_lifespan_step']=(d+2)*24
                    a=t.get('animal')
                    if a and h==23:
                        cost,st,lo,it,cap,c=ANIMALS[a]
                        t['consecutive_unfed']=0 if t['fed_today'] else t['consecutive_unfed']+1
                        if t['consecutive_unfed']>=2:row[x]=dict(kind=st);continue
                        age=d+1-t['placed_day']-lo
                        if age>=0 and age%it==0:
                            t['yield_units']=min(cap,t['yield_units']+1+(t['pending_care_bonus'] if t['fed_today'] else 0))
                            t['pending_care_bonus']=0
                        if t['cared_today'] and t['fed_today']:t['pending_care_bonus']+=1
                        t['fertilizer_available']=True;t['fed_today']=t['cared_today']=False
            if h==23:
                p=self.p[i]
                for inv in p['inventories']:
                    for c,n in inv.items():
                        amt=min(n,100-p['shed'].total()); p['shed'][c]+=amt
                        if i==self.seat:self.events['overflow']+=n-amt
                b['farmer']=[4,4];b['hands']=[];b['hires_today']=0;p['inventories']=[Counter()]
        if h==23 and (d+1)%3==0 and len(self.shops)<8:
            self.shops.append(self.fixed.split(',')[len(self.shops)%len(self.fixed.split(','))] if self.fixed else rng.choice(tuple(SHOP)))
    def run(self):
        for s in range(719):
            actions=[]
            for i in range(2):
                if i!=self.seat and self.mode=='pass':
                    actions.append({'farmer':['PASS'],'hands':[],'market':[]});continue
                t=time.monotonic()
                a=self.agent[i].act(self.obs(i,s))
                self.max_step=max(self.max_step,time.monotonic()-t)
                assert len(a['market'])<=10
                json.dumps(a,allow_nan=False)
                actions.append(a)
            for i,a in enumerate(actions):self.units(i,s,a)
            self.market(actions);self.tick(s)
            if s%24==23:
                self.dailies.append((s//24,int(self.b[0]['money']),dict(Counter(t.get('crop') or t.get('animal') for row in self.b[0]['tiles'] for t in row if isinstance(t,dict))),dict(self.p[0]['shed'])))
        print('scenario',self.scen,self.mode,'candidate seat=',self.seat,'shops=',self.shops,'money=',[b['money'] for b in self.b],'max_act_seconds=',round(self.max_step,6))
        print('events',dict(self.events),'sale_revenues',dict(self.sell))
        if '--verbose' in sys.argv:
            for row in self.dailies:print(row)
if __name__=='__main__':
    for seed in sys.argv[2:]:
        if seed.isdigit():Harness(int(seed),sys.argv[1]).run()
