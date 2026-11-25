# Adapted from user-provided d15; original hashes in references/d15/manifest.json.
"""Integer market flow calculus with exact per-unit quotes and price-floor state.

Daily arrival timing is still a scenario. Conditional on that timing, there is
no midpoint-price shortcut and no fictitious inventory added by floor sales.
Finite differences propagate through the actual inventory transition, including
the loss of price impact once a floor sale stops changing inventory.
"""
from functools import lru_cache
from .forecast import MarketModel as ScenarioModel,ITEMS
from . import rules
from .budget import check

LOW=rules.MARKET_I0-5000;HIGH=rules.MARKET_I0+5000
_TABLE={}

def table(item):
    if item not in _TABLE:
        prices=[rules.market_price(item,i) for i in range(LOW,HIGH+1)]
        prefix=[0]
        for p in prices:prefix.append(prefix[-1]+p)
        # The logarithmic wheat/egg curve reaches its floor far beyond the
        # cached range. A cache boundary must never become an economic floor.
        lo=int(rules.MARKET_PARAMS[item]['I0']);span=1
        while rules.market_price(item,lo+span)>1:span*=2
        hi=lo+span
        while lo+1<hi:
            mid=(lo+hi)//2
            if rules.market_price(item,mid)==1:hi=mid
            else:lo=mid
        floor=hi
        _TABLE[item]=(prices,prefix,floor)
    return _TABLE[item]

def sell(item,inv,n):
    n=int(n)
    if n<=0:return 0,inv
    prices,prefix,floor=table(item)
    step=max(0,min(n,floor-inv))
    if inv>=LOW and inv+step<=HIGH+1:
        revenue=prefix[inv+step-LOW]-prefix[inv-LOW]+n-step
        return revenue,inv+step
    revenue=0
    for _ in range(n):
        p=rules.market_price(item,inv);revenue+=p;inv+=p>1
    return revenue,inv

def buy(item,inv,n):
    n=int(n)
    if n<=0:return 0,inv
    prices,prefix,_=table(item)
    if inv-n>=LOW and inv<=HIGH+1:return prefix[inv-LOW]-prefix[inv-n-LOW],inv-n
    cost=sum(rules.market_price(item,inv-j-1) for j in range(n))
    return cost,inv-n

def batch(item,inv,own,rival):
    """Return cash difference and ending inventory for same-item lockstep orders."""
    own=int(round(own));rival=int(round(rival));inv=int(round(inv))
    return _batch_integer(item,inv,own,rival)

def _batch_integer(item,inv,own,rival):
    if own>=0 and rival>=0:
        paired=min(own,rival)
        floor=table(item)[2]
        changing=min(paired,max(0,(floor-inv+1)//2))
        inv+=2*changing
        if own>=rival:
            value,inv=sell(item,inv,own-paired)
        else:
            value,inv=sell(item,inv,rival-paired);value=-value
        return value,inv
    if own<=0 and rival<=0:
        paired=min(-own,-rival);inv-=2*paired
        if own<rival:
            value,inv=buy(item,inv,rival-own);value=-value
        else:value,inv=buy(item,inv,own-rival)
        return value,inv
    sign=1 if own>0 else -1
    sold=own if own>0 else rival;bought=-rival if own>0 else -own
    value=0
    for _ in range(min(sold,bought)):
        ps=rules.market_price(item,inv);pb=rules.market_price(item,inv-1)
        value+=sign*(ps+pb);inv+=(ps>1)-1
    if sold>bought:
        extra,inv=sell(item,inv,sold-bought);value+=sign*extra
    elif bought>sold:
        extra,inv=buy(item,inv,bought-sold);value+=sign*extra
    return value,inv

def daily(item,inv,own,other,consumption,phase=0):
    # The three explicit arrival scenarios cover interleaved, own-earlier and
    # opponent-earlier deliveries. They are assumptions, not hidden timestamps.
    own=int(round(own));other=int(round(other))
    return _daily_integer(item,int(round(inv)),own,other,int(consumption),phase)

def _daily_integer(item,inv,own,other,consumption,phase=0):
    first=consumption//2;inv-=first
    if phase==0:value,inv=_batch_integer(item,inv,own,other)
    elif phase==1:
        a,inv=_batch_integer(item,inv,own,0);b,inv=_batch_integer(item,inv,0,other);value=a+b
    else:
        b,inv=_batch_integer(item,inv,0,other);a,inv=_batch_integer(item,inv,own,0);value=a+b
    return value,inv-(consumption-first)

class MarketModel(ScenarioModel):
    daily_value=staticmethod(_daily_integer)
    def __init__(self,obs):
        super().__init__(obs)
        self._parts={};self._marginals={}
        # Existing own private stock is observable and must be in today's book.
        self.initial={k:int(obs['private']['shed'].get(k,0)) for k in ITEMS}
        for inv in obs['private']['inventories']:
            for k,n in inv.items():
                if k in self.initial:self.initial[k]+=n
        self.hold_inputs=bool(obs.get('_rsi_hold_inputs'))

    def material_trades(self,item,flows):
        """Keep a minimal input buffer for the next planned consumption.

        The sum of all market trades equals existing stock plus net production.
        Retention is capped across the two materials to leave room for crops.
        Stock still held at the true terminal boundary is fully liquidated.
        """
        trades=[int(round(v)) for v in flows]
        stock=self.initial[item]
        if not self.hold_inputs or item not in ('WHEAT','FERTILIZER'):
            trades[self.day]+=stock
            return trades
        cap=35 if item=='WHEAT' else 15
        for day in range(self.day,30):
            stock+=trades[day]
            if stock<0:
                trades[day]=stock;stock=0
                continue
            needed=0
            for future in range(day+1,min(30,day+4)):
                needed-=int(round(flows[future]))
            keep=min(cap,max(0,needed)) if day<29 else 0
            sold=max(0,stock-keep)
            trades[day]=sold;stock-=sold
        trades[29]+=stock
        return trades

    def component(self,item,flows,want_gradient=False):
        key=item,tuple(flows[self.day:])
        if key in self._parts and (not want_gradient or key in self._marginals):
            return self._parts[key],self._marginals.get(key)
        values=[];gradients=[]
        own=self.material_trades(item,flows)
        material_paths={}
        if want_gradient and self.hold_inputs and item in ('WHEAT','FERTILIZER'):
            for day in range(self.day,30):
                plus=list(flows);minus=list(flows);plus[day]+=1;minus[day]-=1
                material_paths[day]=(self.material_trades(item,plus),self.material_trades(item,minus))
        for index,(demands,rival) in enumerate(self.scenarios):
            check()
            phase=index%3
            rival_amounts=[int(round(v)) for v in rival[item]]
            consumption=[int(demands[d][item]) for d in range(30)]
            @lru_cache(None)
            def tail(day,inv):
                if day>=30:return 0
                gain,nxt=self.daily_value(item,inv,own[day],rival_amounts[day],consumption[day],phase)
                return gain+tail(day+1,nxt)
            inv=int(self.obs['market']['inventory'][item]);values.append(tail(self.day,inv))
            g=[0.0]*30
            if want_gradient:
                for day in range(self.day,30):
                    if material_paths:
                        # Changing production changes retained stock and can
                        # move the market transaction to a later day. Differentiate
                        # through that inventory policy, not a fictitious sale now.
                        check()
                        totals=[]
                        for path in material_paths[day]:
                            cursor=int(self.obs['market']['inventory'][item]);cash=0
                            for d in range(self.day,30):
                                gain,cursor=self.daily_value(item,cursor,path[d],rival_amounts[d],consumption[d],phase)
                                cash+=gain
                            totals.append(cash)
                        g[day]=.5*(totals[0]-totals[1])
                        continue
                    value,nxt=self.daily_value(item,inv,own[day],rival_amounts[day],consumption[day],phase)
                    vp,np=self.daily_value(item,inv,own[day]+1,rival_amounts[day],consumption[day],phase)
                    vm,nm=self.daily_value(item,inv,own[day]-1,rival_amounts[day],consumption[day],phase)
                    g[day]=.5*(vp+tail(day+1,np)-vm-tail(day+1,nm))
                    inv=nxt
                gradients.append(g)
        self._parts[key]=values
        if want_gradient:self._marginals[key]=gradients
        return values,gradients

    def score(self,own_flows,risk=.25):
        scores=[0.0]*len(self.scenarios)
        for k in ITEMS:
            values,_=self.component(k,own_flows[k])
            for i,v in enumerate(values):scores[i]+=v
        scores.sort()
        return (1-risk)*sum(scores)/len(scores)+risk*scores[self.downside_index(len(scores))]

    def marginal(self,own_flows,risk=.25):
        # A discrete derivative can correctly be negative: adding output can
        # damage our larger later inventory more than it earns now.
        out={k:[0.0]*30 for k in ITEMS};components={};total=[0.0]*len(self.scenarios)
        for k in ITEMS:
            values,gradients=self.component(k,own_flows[k],True);components[k]=gradients
            for i,v in enumerate(values):total[i]+=v
        tail_index=sorted(range(len(total)),key=total.__getitem__)[self.downside_index(len(total))]
        for k in ITEMS:
            gradients=components[k]
            for d in range(self.day,30):
                v=[g[d] for g in gradients]
                out[k][d]=(1-risk)*sum(v)/len(v)+risk*v[tail_index]
        return out
