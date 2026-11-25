# Adapted from user-provided d15; original hashes in references/d15/manifest.json.
"""Observable cohort supply, endogenous market impact, and marginal cash gap.

No episode identifier, hidden seed, rival inventory, or future revealed shop is
an input. Forecast uncertainty is explicit; exact market_price is reused.
"""
from collections import Counter
import copy
import random
from . import rules
def residual_scenarios(*args, **kwargs):
    return []  # RSIdemo uses public physical cohorts, no opponent trace bank.

ITEMS=tuple(rules.MARKET_PARAMS)

def demand(shops):
    out=Counter({k:1 for k in rules.TOWN_CENTER_PRODUCTS})
    for shop in shops:
        for k in rules.SHOPS[shop]: out[k]+=12 if len(rules.SHOPS[shop])==1 else 6
    return out

def cohort_supply(farm, day, care=1.0, expansion=0.0, net_crop_inputs=False):
    """Physical existing-cohort forecast, plus an explicitly uncertain extension.

    Held produce is collected on the current day. Crop replacement is a demand
    scenario, not falsely labeled a known future opponent action.
    """
    flows={k:[0.0]*30 for k in ITEMS}
    for row in farm['tiles']:
        for t in row:
            if not isinstance(t,dict): continue
            if 'animal' in t:
                a=rules.ANIMALS[t['animal']];p=a['product']
                held=t.get('yield_units',0);bonus=t.get('pending_care_bonus',0)
                flows[p][day]+=held
                for d in range(day,30):
                    if d<29: flows['WHEAT'][d]-=1
                    flows['FERTILIZER'][d]+=float(d>day or t.get('fertilizer_available',False))
                    age=d+1-t['placed_day']-a['first_yield_day']
                    if d<29 and age>=0 and age%a['interval']==0:
                        flows[p][d+1]+=min(a['max_held'],1+bonus);bonus=0
                    bonus+=care
            elif t.get('kind')=='PLANT':
                c=t['crop'];cd=rules.CROPS[c];pl=t['planted_day']
                if cd['ongoing']:
                    flows[c][day]+=t.get('yield_units',0)
                    fertilizer_until=t.get('fertilized_until_day',-1)
                    for j in range(cd['max_yield']):
                        d=pl+cd['first_yield_day']+j*cd['interval']
                        if day<d<30:
                            flows[c][d]+=1+care
                            # Production dawn d uses the previous day's
                            # watering/fertilizer. One dose lasts three days;
                            # never charge a dose for already-fertilized days.
                            service=d-1
                            if net_crop_inputs and service>fertilizer_until:
                                flows['FERTILIZER'][service]-=care
                                fertilizer_until=service+2
                else:
                    # Complete elite lifecycle, including the common age-3
                    # wheat / age-2 carrot / age-10 melon turnovers.
                    age={'WHEAT':3,'CARROT':2,'MELON':10}[c]
                    d=max(day,pl+age)
                    if d<30: flows[c][d]+={'WHEAT':5,'CARROT':2,'MELON':6}[c]
                    if net_crop_inputs and c=='WHEAT' and d<30:
                        fertilize=max(day,pl+2)
                        if t.get('fertilized_until_day',-1)<fertilize:
                            flows['FERTILIZER'][fertilize]-=1
                    if expansion and c in ('WHEAT','CARROT'):
                        for future in range(d+age,30,age):
                            flows[c][future]+=expansion*{'WHEAT':5,'CARROT':2}[c]
                            if net_crop_inputs and c=='WHEAT':
                                flows['FERTILIZER'][max(day,future-1)]-=expansion
    return flows

class MarketModel:
    residual_selector=staticmethod(residual_scenarios)
    def __init__(self, obs):
        self.day=int(obs['step'])//24
        self.obs=obs
        self.known=list(obs['town']['unlocked_shops'])
        self.scenarios=[]
        self._score_cache={}
        # Stratified Monte Carlo is used for unrevealed shop uncertainty. The
        # artificial seed never depends on the game's secret random seed.
        for index in range(9):
            rng=random.Random(191919+self.day*97+index)
            shops=self.known[:]
            demands=[]
            for d in range(30):
                if d>=self.day:
                    while len(shops)<min(8,d//3):
                        shops.append(rng.choice(sorted(rules.SHOPS)))
                demands.append(demand(shops))
            care=(.65, .85, 1.0)[index%3]
            renewal=(.65,1.0,1.2)[index//3]
            rival=cohort_supply(obs['farms'][1-int(obs['player'])],self.day,care,renewal,
                                net_crop_inputs=obs.get('_rsi_net_crop_inputs',False))
            self.scenarios.append((demands,rival))
        # Preserve joint city/supply continuations from complete elite games.
        # Only prefix features select neighbors. Future data are the learned
        # uncertainty distribution, never current-game information.
        physical=cohort_supply(obs['farms'][1-int(obs['player'])],self.day,1,0)
        learned=[]
        selector=self.residual_selector
        if obs.get('_rsi_prior_bank'):
            from .prior_bank import select
            selector=select
        if obs.get('_rsi_learned_supply'):
            from .learned_supply import select
            selector=select
        for row in selector(obs,1-int(obs['player']),limit=6):
            known_count=len(row['future_shops'][0]);demands=[Counter() for _ in range(30)]
            flow={k:physical[k][:] for k in ITEMS}
            for offset,d in enumerate(range(self.day,30)):
                future=self.known+row['future_shops'][offset][known_count:]
                demands[d]=demand(future)
                for ki,k in enumerate(ITEMS):
                    weight=obs.get('_rsi_residual_weights',{}).get(k,1.)
                    q=physical[k][d]+weight*row['residual'][ki][offset]
                    flow[k][d]=max(-100.0 if k in ('WHEAT','FERTILIZER') else 0.0,q)
            learned.append((demands,flow))
        # d15 thins its physical grid only when six learned scenarios replace
        # the discarded paths. Without that bank, thinning would leave care
        # fixed at .65 and silently remove every high-production scenario.
        self.scenarios=(self.scenarios[::3]+learned) if learned else self.scenarios

    @staticmethod
    def downside_index(count):
        """A fixed lower quantile, independent of the scenario count."""
        return max(0, min(count-1, int((count-1)*.20)))

    def score(self, own_flows, risk=.25):
        """Evaluate a complete supply portfolio before accepting an exchange."""
        scores=[0.0]*len(self.scenarios)
        for k in ITEMS:
            key=k,tuple(own_flows[k][self.day:])
            component=self._score_cache.get(key)
            if component is None:
                component=[]
                for demands,rival in self.scenarios:
                    value=0.0
                    inv=float(self.obs['market']['inventory'][k])
                    for d in range(self.day,30):
                        ours=own_flows[k][d];theirs=rival[k][d]
                        price=rules.market_price(k,inv-.5*demands[d][k]+.5*(ours+theirs))
                        value+=(ours-theirs)*price
                        inv-=demands[d][k]
                        inv+=min(0,ours)+min(0,theirs)
                        if price>1: inv+=max(0,ours)+max(0,theirs)
                    component.append(value)
                self._score_cache[key]=component
            for i,v in enumerate(component):scores[i]+=v
        scores.sort()
        return (1-risk)*sum(scores)/len(scores)+risk*scores[self.downside_index(len(scores))]

    def marginal(self, own_flows, risk=.25):
        """Differentiate terminal cash difference through market inventory.

        The own-output column affects both participants' later execution prices.
        Daily sales timing is an approximation; action execution remains exact.
        """
        values={k:[[] for _ in range(30)] for k in ITEMS}
        inv0=self.obs['market']['inventory']
        for demands,rival in self.scenarios:
            for k in ITEMS:
                inv=float(inv0[k]);cashdiff=[];derivatives=[];quotes=[]
                for d in range(self.day,30):
                    own=own_flows[k][d];other=rival[k][d]
                    # Intra-day averaging accounts for gradual town buying.
                    mid=inv-.5*demands[d][k]+.5*(own+other)
                    p=rules.market_price(k,mid)
                    dp=(rules.market_price(k,mid+3)-rules.market_price(k,mid-3))/6
                    quotes.append(p);derivatives.append(dp);cashdiff.append(own-other)
                    # Sales at price 1 never add inventory to this game's book.
                    inv-=demands[d][k]
                    inv+=min(0,own)+min(0,other)
                    if p>1: inv+=max(0,own)+max(0,other)
                tail=0.0
                for offset in range(len(quotes)-1,-1,-1):
                    d=self.day+offset
                    impact=cashdiff[offset]*derivatives[offset]
                    raw=quotes[offset]+tail+.5*impact
                    # A local gradient is unreliable across the price floor.
                    # Keep a trust region; global iteration must recompute it.
                    value=max(.25*quotes[offset],min(2*quotes[offset],raw))
                    values[k][d].append(value)
                    tail+=impact
        out={k:[0.0]*30 for k in ITEMS}
        for k in ITEMS:
            for d in range(self.day,30):
                v=sorted(values[k][d]);mean=sum(v)/len(v)
                out[k][d]=(1-risk)*mean+risk*v[self.downside_index(len(v))]
        return out

def empty_flows(): return {k:[0.0]*30 for k in ITEMS}

def add_project(flows,project,scale=1.0,delivery_lag=0):
    for d,_,outputs,inputs in project.days:
        for k,n in outputs:
            if k in flows and d+delivery_lag<30: flows[k][d+delivery_lag]+=scale*n
        for k,n in inputs:
            if k in flows: flows[k][d]-=scale*n
