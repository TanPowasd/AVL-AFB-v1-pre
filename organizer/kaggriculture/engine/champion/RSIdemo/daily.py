# Adapted from the user-provided joint-adaptive-fast-d15 package.
# Original source SHA256 and provenance: references/d15/manifest.json.
# See REFERENCE_NOTES.md for reused code and RSIdemo changes.
"""State-driven finite-horizon farm planner. No recorded action tapes.

Build complete daily tile-service jobs; buy their actual resources; route them
jointly from observed positions. The only persistent plan lasts one day.
"""
from collections import Counter, deque
import math

CROPS = {
    'WHEAT':(10,2,4,0,6), 'CARROT':(20,2,3,0,4),
    'TOMATO':(50,8,8,1,4), 'STRAWBERRY':(100,10,10,2,4),
    'MELON':(80,10,12,0,6)}
ANIMALS = {'COW':(400,8,2,6,'MILK','PASTURE'),
           'SHEEP':(500,6,3,6,'WOOL','PASTURE'),
           'GOOSE':(300,4,1,4,'EGG','COOP')}
PRODUCTS = tuple(CROPS)+('EGG','MILK','WOOL','FERTILIZER')
SHOPS = {'BAKERY':['EGG','WHEAT'],'PIZZA_SHOP':['MILK','TOMATO','WHEAT'],
         'BRUNCH_SPOT':['EGG','WHEAT','STRAWBERRY'],'YARN_STORE':['WOOL'],
         'ICE_CREAM_SHOP':['STRAWBERRY','MILK','WHEAT'],'PET_CAFE':['CARROT'],
         'SMOOTHIE_SHOP':['STRAWBERRY','MILK'],
         'FARMERS_MARKET':['WHEAT','CARROT','TOMATO','STRAWBERRY']}
DEFAULT = dict(initial_melon=12, initial_cow=2, initial_sheep=2,initial_goose=0,
               initial_straw=0,initial_tomato=0,cheap_cover=False,
               cow=8, sheep=6, goose=3, animal_ramp=1.5, animal_start=3,
               wheat=18, strawberry=40, tomato=0,
               quadrants=3, unlock_ne=6, unlock_sw=10, unlock_se=12,
               crew0=5, crew1=8, crew2=12, crew3=14,
               end_straw=17, end_melon=13, end_tomato=18,
               fert_straw=True, fert_tomato=True, fert_wheat=False,
               harvest_batch=True, route_ms=25, adapt=False,
               final_return=True, care=True, collect=True,
               secondary_melon=0, reserve_cash=0, animal_radius=4,
               idle_delivery=True, initial_buffer=140, setup_hours=3,
               crew_dynamic=False,crew_cap=14,crew_slack=0)

def sd(p):
    return max(0,4-p[0],p[0]-5)+max(0,4-p[1],p[1]-5)

def dist(a,b): return abs(a[0]-b[0])+abs(a[1]-b[1])

def moves(a,b):
    x,y = a
    tx,ty = b
    return ([['EAST' if tx>x else 'WEST']] * abs(tx-x)
            + [['SOUTH' if ty>y else 'NORTH']] * abs(ty-y))

def fibcost(n):
    a,b,total=1,1,0
    for _ in range(n): total,a,b=total+a,b,a+b
    return total

def needs(ops):
    out=Counter()
    for op in ops:
        if op[0]=='FEED': out['WHEAT']+=1
        elif op[0]=='FERTILIZE': out['FERTILIZER']+=1
        elif op[0]=='PLACE' and op[1] in ANIMALS: out[op[1]]+=1
    return out

class Planner:
    def __init__(self, route_solver, params=None):
        self.solve=route_solver
        self.p=dict(DEFAULT)
        if params: self.p.update(params)
        self.day=-1
        self.jobs=[]
        self.queues=[]
        self.plan_ready=False
        self.day_stats=[]
        self.want_seeds=Counter()
        self.want_animals=Counter()
        self.want_resources=Counter()

    def crop_controls(self,pos,tile,day):
        return None

    def new_crop_ops(self,pos,crop,day):
        return [['PLANT',crop],['WATER']]

    def herd_target(self, obs, day):
        p=self.p
        target={a:int(p[a.lower()]) for a in ANIMALS}
        if p['adapt']:
            shops=obs['town']['unlocked_shops']
            milk=sum(s in ('PIZZA_SHOP','ICE_CREAM_SHOP','SMOOTHIE_SHOP') for s in shops)
            wool=shops.count('YARN_STORE')
            egg=sum(s in ('BAKERY','BRUNCH_SPOT') for s in shops)
            # A prior for remaining shop draws avoids eliminating livestock
            # merely because the early town has not yet revealed its demand.
            remain=max(0, min(4,8-len(shops)))
            target['COW']=round(2+1.7*(milk+.375*remain))
            target['SHEEP']=round(1.5+2.8*(wool+.125*remain))
            target['GOOSE']=round(1.5+1.0*(egg+.25*remain))
        initial={'COW':int(p['initial_cow']),'SHEEP':int(p['initial_sheep']),'GOOSE':int(p['initial_goose'])}
        allowance=max(0,int((day-p['animal_start']+1)*p['animal_ramp']))
        result=dict(initial)
        while allowance>0:
            options=[a for a in ANIMALS if result[a]<target[a]]
            if not options: break
            a=max(options,key=lambda a:(target[a]-result[a])/(1+target[a]))
            result[a]+=1
            allowance-=1
        return result

    def choose_crop(self,pos,day,counts):
        p=self.p
        if day>26: return None
        if day<=2 and counts['MELON']<p['initial_melon']:
            return 'MELON'
        if day<=2 and counts['STRAWBERRY']<p['initial_straw']:
            return 'STRAWBERRY'
        if day<=2 and counts['TOMATO']<p['initial_tomato']:
            return 'TOMATO'
        if day<5:
            return 'WHEAT'
        if day<=25 and counts['WHEAT']<p['wheat']:
            return 'WHEAT'
        if day<=p['end_melon'] and counts['MELON']<p['secondary_melon']:
            return 'MELON'
        if day<=p['end_tomato'] and counts['TOMATO']<p['tomato']:
            return 'TOMATO'
        if day<=p['end_straw'] and counts['STRAWBERRY']<p['strawberry']:
            return 'STRAWBERRY'
        if day<=26: return 'CARROT'
        # Final planting at d27 can be cut at d29 for two units.
        return 'CARROT'

    def begin_day(self,obs):
        day=int(obs['step'])//24
        self.day=day
        self.jobs=[]
        self.queues=[]
        self.plan_ready=False
        farm=obs['farms'][obs['player']]
        private=obs['private']
        prices=obs['market']['prices']
        self.want_seeds=Counter()
        self.want_animals=Counter()
        self.want_resources=Counter()
        counts=Counter()
        animals=Counter()
        for row in farm['tiles']:
            for tile in row:
                if isinstance(tile,dict):
                    if tile.get('kind')=='PLANT': counts[tile['crop']]+=1
                    if 'animal' in tile: animals[tile['animal']]+=1
        self.herd_count=sum(animals.values())
        quads=len(farm['unlocked_quadrants'])
        self.crew=int(self.p['crew'+str(quads-1)])
        # Projected sale cash is only for choosing investments. Orders below
        # are recalculated against actual observations and per-order cash.
        budget=farm['money']+sum(n*prices.get(k,0)*.92 for k,n in private['shed'].items()
                                 if k in PRODUCTS)
        budget-=fibcost(self.crew)+self.herd_count*prices['WHEAT']+self.p['reserve_cash']
        if day==0: budget-=self.p['initial_buffer']
        due=(self.p['unlock_ne'],self.p['unlock_sw'],self.p['unlock_se'])
        land_cost=(1000,2000,4000)
        self.buy_land=False
        if quads<self.p['quadrants'] and day>=due[quads-1] and budget>land_cost[quads-1]+500:
            self.buy_land=True
            budget-=land_cost[quads-1]
            quads+=1
            self.crew=int(self.p['crew'+str(quads-1)])
        self.target_quads=quads
        valid_quads=('NW','NE','SW','SE')[:quads]
        available=[]
        for y in range(10):
            for x in range(10):
                q=('N' if y<5 else 'S')+('W' if x<5 else 'E')
                if q not in valid_quads: continue
                t=farm['tiles'][y][x]
                if t=='LOCKED': t=None
                pos=(x,y)
                if isinstance(t,dict) and 'animal' in t:
                    ops=[]
                    if not t.get('fed_today') and day<29: ops.append(['FEED'])
                    a=t['animal']; ac=ANIMALS[a]
                    age=day-t['placed_day']
                    next_care_pay=day+2
                    while next_care_pay-t['placed_day']<ac[1] or (next_care_pay-t['placed_day']-ac[1])%ac[2]:
                        next_care_pay+=1
                    if self.p['care'] and not t.get('cared_today') and next_care_pay<=29: ops.append(['CARE'])
                    next_prod=age+1>=ac[1] and (age+1-ac[1])%ac[2]==0
                    future=1+int(t.get('pending_care_bonus',0))
                    ripe=t.get('yield_units',0)
                    if ripe and (not self.p['harvest_batch'] or ripe>=ac[3]
                                 or next_prod and ripe+future>ac[3] or day==29):
                        ops.append(['HARVEST'])
                    if self.p['collect'] and t.get('fertilizer_available'): ops.append(['COLLECT_FERTILIZER'])
                    priority=(30000 if t.get('consecutive_unfed',0)>=1 else 10000)
                    if ops: self.add_job(pos,ops,priority+ripe*prices[ac[4]],True)
                    continue
                if isinstance(t,dict) and t.get('kind')=='PLANT':
                    crop=t['crop']; c=CROPS[crop]; age=day-t['planted_day']
                    control=self.crop_controls(pos,t,day)
                    ops=[]
                    ripe=t.get('yield_units',0)
                    ongoing=c[3]>0
                    spent=False
                    if ongoing:
                        final_age=control['end_age'] if control else c[1]+3*c[3]
                        next_prod=(c[1]<=age+1<=final_age and (age+1-c[1])%c[3]==0)
                        fertilize=(next_prod and t.get('fertilized_until_day',-1)<day
                                   and self.p['fert_'+('straw' if crop=='STRAWBERRY' else 'tomato')])
                        if control:
                            fertilize=age in control['fertilize_ages'] and t.get('fertilized_until_day',-1)<day+2
                        water=(t.get('consecutive_unwatered',0)>=1 or next_prod and
                               (fertilize or t.get('fertilized_until_day',-1)>=day))
                        if control: water=(age in control['water_ages'] or t.get('consecutive_unwatered',0)>=1)
                        future=2 if next_prod and (fertilize or t.get('fertilized_until_day',-1)>=day) else int(next_prod)
                        harvest=ripe and (not self.p['harvest_batch'] or ripe>=4 and water
                                          or ripe+future>4 or age>=final_age or day==29)
                        if control: harvest=ripe and (age in control['harvest_ages'] or ripe+future>4 or day==29)
                        spent=age>=final_age
                        if spent:
                            if ripe: ops.append(['HARVEST'])
                        else:
                            if fertilize: ops.append(['FERTILIZE'])
                            if water and not t.get('watered_today'): ops.append(['WATER'])
                            if harvest: ops.append(['HARVEST'])
                    else:
                        window=(c[2]+1)//2
                        fert=(crop=='WHEAT' and self.p['fert_wheat'] and window<=age<=c[2]
                              and t.get('fertilized_until_day',-1)<day and ripe<c[4])
                        if control: fert=(age in control['fertilize_ages'] and t.get('fertilized_until_day',-1)<day+2 and ripe<c[4])
                        bonus=2 if fert or t.get('fertilized_until_day',-1)>=day else 1
                        grow=window<=age<=c[2] and ripe<c[4]
                        water=(grow or t.get('consecutive_unwatered',0)>=1)
                        if control: water=(age in control['water_ages'] or t.get('consecutive_unwatered',0)>=1)
                        if fert: ops.append(['FERTILIZE'])
                        if water and not t.get('watered_today'): ops.append(['WATER'])
                        projected=ripe+(bonus if grow and not t.get('watered_today') else 0)
                        spent=(age>=c[1] and (projected>=c[4] or age>=c[2] or day==29))
                        if control: spent=(age>=control['end_age'] or day==29)
                        if spent and ripe: ops.append(['HARVEST'])
                    if spent:
                        counts[crop]-=1
                        # Fertile ongoing plants persist after their last harvest.
                        if ongoing and day<=26: ops.append(['DIG'])
                        available.append((pos,None,ops,True))
                    elif ops:
                        priority=30000 if t.get('consecutive_unwatered',0)>=1 else 9000
                        self.add_job(pos,ops,priority+ripe*prices[crop],True)
                    continue
                available.append((pos,t,[],False))
        # Reserve new livestock locations near the shed, while far corners
        # carry less frequently serviced crops. No planted tile is destroyed.
        available.sort(key=lambda z:(sd(z[0]),z[0][1],z[0][0]))
        target=self.herd_target(obs,day)
        if day>19: target=dict(animals)
        assignments={}
        for a in ('COW','SHEEP','GOOSE'):
            deficit=max(0,target.get(a,0)-animals[a])
            for _ in range(deficit):
                suitable=[i for i,(pos,t,ops,spent) in enumerate(available)
                          if i not in assignments and sd(pos)<=self.p['animal_radius']]
                if not suitable: break
                price=ANIMALS[a][0]
                have=private['shed'].get(a,0)-self.want_animals[a]
                if have<=0 and budget<price+prices['WHEAT']: break
                i=suitable[0]
                assignments[i]=a
                self.want_animals[a]+=1
                self.herd_count+=1
                if have<=0: budget-=price+prices['WHEAT']
        # Allocate initial melon to the far side of the field, so recurring
        # animals and quick crops do not force everyone across the whole map.
        crop_order=sorted(range(len(available)),key=lambda i:(-sd(available[i][0]),available[i][0]))
        for i in crop_order:
            pos,t,ops,spent=available[i]
            ops=list(ops)
            if i in assignments:
                a=assignments[i]
                structure=ANIMALS[a][5]
                if t and (not isinstance(t,dict) or t.get('kind')!=structure): ops.append(['DIG'])
                if not (isinstance(t,dict) and t.get('kind')==structure): ops.append(['BUILD_'+structure])
                ops.extend([['PLACE',a],['FEED']])
                if self.p['care'] and day<29: ops.append(['CARE'])
                self.add_job(pos,ops,15000,True)
                continue
            crop=self.choose_crop(pos,day,counts)
            price=CROPS[crop][0] if crop else 0
            seed_have=private['seeds'].get(crop,0)-self.want_seeds[crop] if crop else 0
            if crop and price>budget and seed_have<=0 and self.p['cheap_cover'] and budget>=10 and day<=25:
                crop='WHEAT'; price=10
                seed_have=private['seeds'].get(crop,0)-self.want_seeds[crop]
            # Keep mandatory harvest even when replanting is not affordable.
            if crop and (seed_have>0 or budget>=price):
                if t is not None: ops.append(['DIG'])
                ops.extend(self.new_crop_ops(pos,crop,day))
                counts[crop]+=1
                self.want_seeds[crop]+=1
                if seed_have<=0: budget-=price
            if ops:
                value=12000 if spent else 1000
                self.add_job(pos,ops,value,spent)
        for job in self.jobs: self.want_resources.update(needs(job['ops']))
        self.want_resources['WHEAT']=max(self.want_resources['WHEAT'],self.herd_count)
        if self.p['crew_dynamic']:
            self.choose_crew(obs)

    def choose_crew(self,obs):
        """Choose labor by a constructive feasible-route test, not mean load."""
        final=self.day==29
        hour=self.p['setup_hours']
        service=sum(len(j['ops']) for j in self.jobs)
        # Every distinct non-depot job requires at least one move. This is a
        # lower bound only; actual route completion below is the certificate.
        lower=max(1,math.ceil((service+max(0,len(self.jobs)-4))/(24-hour))-1)
        cap=int(self.p['crew_cap'])
        resources=Counter(obs['private']['shed'])
        for k,n in self.want_resources.items(): resources[k]=max(resources[k],n)
        positions=[(4,4)]
        for _ in range(cap):
            candidates=((4,4),(5,4),(4,5),(5,5))
            p=min(candidates,key=lambda p:positions.count(p))
            positions.append(p)
        chosen=cap
        for hands in range(min(cap,lower),cap+1):
            workers=[dict(pos=p,hour=hour,inv={}) for p in positions[:hands+1]]
            result=self.solve(self.jobs,workers,dict(resources),last_hour=22 if final else 23,
                              track_collect=True,evaluation_budget=5000,
                              return_to_shed=final,end_service=1)
            if not result['unassigned']:
                chosen=min(cap,hands+int(self.p['crew_slack']))
                break
        self.crew=chosen

    def add_job(self,pos,ops,priority,mandatory):
        self.jobs.append(dict(pos=pos,ops=ops,priority=priority,mandatory=mandatory))

    def market(self,obs,hour,unit_actions=None):
        private=obs['private']; farm=obs['farms'][obs['player']]
        prices=obs['market']['prices']; shed=Counter(private['shed'])
        # Same-turn shed deposits can be sold after the worker phase.
        if unit_actions:
            for i,op in enumerate(unit_actions):
                pos=([farm['farmer']]+farm['hands'])[i]
                if sd(pos)==0 and op:
                    inv=private['inventories'][i]
                    if op[0]=='DROP': shed.update(inv)
                    if op[0]=='PLACE' and op[1] in PRODUCTS:
                        shed[op[1]]+=min(op[2] if len(op)>2 else 1,inv.get(op[1],0))
        orders=[]; cash=farm['money']
        reserve=Counter()
        if hour<=self.p['setup_hours']:
            reserve.update(self.want_resources)
        elif self.plan_ready:
            # A route can pick up its second or third input after setup has
            # ended. Keep those committed inputs in the shed until pickup.
            for queue in self.queues:
                for op in queue:
                    if op and op[0]=='PICKUP':reserve[op[1]]+=op[2]
            for op in unit_actions or ():
                if op and op[0]=='PICKUP':reserve[op[1]]+=op[2]
        # Sales free capacity and fund the same-turn commitment. Timing is
        # ordinary immediate delivery, not a search over sell-time variants.
        for item in sorted(PRODUCTS,key=lambda k:-max(0,shed[k]-reserve[k])*prices[k]):
            n=max(0,shed[item]-reserve[item])
            if n>0 and len(orders)<10:
                orders.append(['SELL',item,n]); shed[item]-=n
                cash+=n*prices[item]*.90
        if hour>=self.p['setup_hours']:
            return orders
        def buy(op,item,n,cost):
            nonlocal cash
            n=max(0,min(int(n),int(cash/max(1,cost))))
            if n and len(orders)<10:
                orders.append([op,item,n]); cash-=cost*n
                return n
            return 0
        # Protect existing herd's feed and production-day fertilizer first.
        for item in ('WHEAT','FERTILIZER'):
            missing=self.want_resources[item]-shed[item]
            if missing>0:
                bought=buy('BUY_PRODUCT',item,missing,prices[item]+1)
                shed[item]+=bought
        if self.buy_land and len(farm['unlocked_quadrants'])<self.target_quads:
            cost=(1000,2000,4000)[len(farm['unlocked_quadrants'])-1]
            if cash>=cost and len(orders)<10:
                orders.append(['BUY_LAND']); cash-=cost
        # Initial animals seed the fertilizer cash flow. Buy only scheduled
        # locations, never a standing herd target that has no route to place it.
        for a,n in self.want_animals.items():
            missing=n-private['shed'].get(a,0)
            buy('BUY_ANIMAL',a,missing,ANIMALS[a][0])
        for crop,n in self.want_seeds.items():
            missing=n-private['seeds'].get(crop,0)
            buy('BUY_SEED',crop,missing,CROPS[crop][0])
        hired=len(farm['hands'])
        while hired<self.crew and len(orders)<10:
            cost=fibcost(hired+1)-fibcost(hired)
            if cash<cost: break
            orders.append(['HIRE']); hired+=1; cash-=cost
        return orders

    def plan_routes(self,obs,hour):
        farm=obs['farms'][obs['player']]; private=obs['private']
        positions=[farm['farmer']]+farm['hands']
        workers=[dict(pos=tuple(pos),hour=hour,inv=dict(private['inventories'][i]))
                 for i,pos in enumerate(positions)]
        # Remove jobs whose prerequisite purchase failed; preserve their safe
        # mandatory prefix (harvest etc.) and postpone new investment.
        seeds=Counter(private['seeds'])
        jobs=[]
        for job in sorted(self.jobs,key=lambda j:(not j['mandatory'],-j['priority'])):
            x,y=job['pos']
            if farm['tiles'][y][x]=='LOCKED': continue
            ops=[]
            for op in job['ops']:
                if op[0]=='PLANT':
                    if seeds[op[1]]<=0: break
                    seeds[op[1]]-=1
                ops.append(op)
            if ops: jobs.append(dict(job,ops=ops))
        final=self.day==29
        result=self.solve(jobs,workers,dict(private['shed']),
                          last_hour=22 if final else 23,time_budget_ms=self.p['route_ms'],track_collect=True,
                          evaluation_budget=20000,return_to_shed=final,end_service=1)
        # Repack peak days with shorter mandatory bundles. A missed replant
        # is recoverable tomorrow; a missed ripe harvest can expire tonight.
        for _ in range(2):
            changed=False
            for ji in result['unassigned']:
                job=jobs[ji]
                if not job['mandatory']: continue
                ops=job['ops']
                if any(op[0]=='FEED' for op in ops):
                    core=[op for op in ops if op[0] not in ('CARE','COLLECT_FERTILIZER')]
                elif any(op[0]=='HARVEST' for op in ops):
                    index=next(i for i,op in enumerate(ops) if op[0]=='HARVEST')
                    core=ops[:index+1]
                else: core=ops
                if core!=ops:
                    jobs[ji]=dict(job,ops=core); changed=True
            if not changed: break
            result=self.solve(jobs,workers,dict(private['shed']),
                              last_hour=22 if final else 23,time_budget_ms=self.p['route_ms'],
                              track_collect=True,evaluation_budget=20000,return_to_shed=final,end_service=1)
        self.queues=[deque() for _ in workers]
        for wi,route in enumerate(result['routes']):
            pos=tuple(positions[wi]); queue=self.queues[wi]
            pickups=result['pickups'][wi]
            if pickups:
                home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
                queue.extend(moves(pos,home)); pos=home
                for item,n in pickups.items():
                    if n>0: queue.append(['PICKUP',item,n])
            for ji in route:
                job=jobs[ji]
                queue.extend(moves(pos,job['pos'])); pos=job['pos']
                queue.extend(job['ops'])
            if final and self.p['final_return']:
                home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
                # The route optimizer has no end depot yet; trim complete jobs
                # below if necessary, never sell hypothetical carried goods.
                return_ops=moves(pos,home)+[['DROP']]
                if hour+len(queue)+len(return_ops)<=23:
                    queue.extend(return_ops)
            if hour+len(queue)>24:
                raise RuntimeError('route exceeds daily horizon')
        self.unassigned=[jobs[i] for i in result['unassigned']]
        self.day_stats.append(dict(day=self.day,jobs=len(jobs),assigned=sum(map(len,result['routes'])),
                                   unassigned=len(self.unassigned),workers=len(workers),
                                   planned_ops=sum(len(q) for q in self.queues),
                                   stats=result.get('stats',{})))
        self.plan_ready=True

    def act(self,obs,configuration=None):
        step=int(obs.get('step',0)); hour=step%24
        if self.day!=step//24: self.begin_day(obs)
        farm=obs['farms'][obs['player']]
        n=1+len(farm['hands'])
        if hour<self.p['setup_hours']:
            return dict(farmer=['PASS'],hands=[['PASS'] for _ in range(n-1)],market=self.market(obs,hour))
        if not self.plan_ready: self.plan_routes(obs,hour)
        actions=[]
        for i in range(n):
            queue=self.queues[i] if i<len(self.queues) else deque()
            action=queue.popleft() if queue else ['PASS']
            if action==['PASS'] and self.p['idle_delivery']:
                pos=([farm['farmer']]+farm['hands'])[i]
                inv=obs['private']['inventories'][i]
                home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
                if any(inv.values()) and hour+dist(pos,home)+1<=23:
                    path=moves(pos,home)
                    action=path[0] if path else ['DROP']
            # On the final day, reserve the exact walk-home and DROP suffix.
            # This must supersede future crop service before it is too late.
            if self.day==29 and self.p['final_return']:
                pos=([farm['farmer']]+farm['hands'])[i]
                inv=obs['private']['inventories'][i]
                home=(min(5,max(4,pos[0])),min(5,max(4,pos[1])))
                if hour+dist(pos,home)+1>=23 and any(inv.values()):
                    path=moves(pos,home)
                    action=path[0] if path else ['DROP']
                    queue.clear()
            actions.append(action)
        return dict(farmer=actions[0],hands=actions[1:],market=self.market(obs,hour,actions))
