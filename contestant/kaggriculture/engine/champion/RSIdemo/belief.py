# Adapted from supplied d15. Original SHA256 in references/d15/manifest.json.
"""Public-history bounds for rival animal-product stocks across warehouse and units."""
import copy
from collections import Counter
from . import rules
TRACKED=('WOOL','MILK','EGG')

class InventoryBelief:
    def __init__(self):
        self.low=Counter();self.high=Counter();self.mean=Counter();self.previous=None;self.observations=0
    def remember(self,obs,action):
        self.previous=(copy.deepcopy(obs),copy.deepcopy(action))
    def update(self,obs):
        if not self.previous:return
        old,action=self.previous;step=old['step'];p=old['player'];q=1-p;night=(step+1)%24==0
        if obs['step']!=step+1:return
        gains_low=Counter();gains_high=Counter();gains_mean=Counter()
        rival_farm=old['farms'][q]
        possible_drop=any(4<=pos[0]<=5 and 4<=pos[1]<=5 for pos in [rival_farm['farmer']]+rival_farm['hands'])
        for y,row in enumerate(old['farms'][q]['tiles']):
            for x,t in enumerate(row):
                if not isinstance(t,dict) or 'animal' not in t:continue
                item=rules.ANIMALS[t['animal']]['product'];held=t.get('yield_units',0)
                if not held:continue
                after=obs['farms'][q]['tiles'][y][x]
                same=isinstance(after,dict) and after.get('animal')==t['animal'] and after.get('placed_day')==t['placed_day']
                if same and not night:
                    harvest=max(0,held-after.get('yield_units',0));gains_low[item]+=harvest;gains_high[item]+=harvest;gains_mean[item]+=harvest
                elif night:
                    # At midnight production and optional feeding can mask a
                    # last-hour harvest. Preserve the entire feasible range.
                    if same:
                        ad=rules.ANIMALS[t['animal']];age=(step+1)//24-t['placed_day']-ad['first_yield_day']
                        produced=age>=0 and age%ad['interval']==0
                        observed=after.get('yield_units',0)
                        if not produced:
                            harvest=max(0,held-observed);gains_low[item]+=harvest;gains_high[item]+=harvest;gains_mean[item]+=harvest
                        else:
                            max_produced=1+t.get('pending_care_bonus',0)
                            if observed<=max_produced:gains_high[item]+=held
                            if observed<min(ad['max_held'],held+1):
                                gains_low[item]+=held;gains_mean[item]+=held
                    else:gains_high[item]+=held
                else:gains_high[item]+=held
        # Predict our same-turn warehouse receipts using our own private state.
        farm=copy.deepcopy(old['farms'][p]);pr=copy.deepcopy(old['private'])
        commands=[action.get('farmer',['PASS'])]+list(action.get('hands',[]))
        plants=Counter(c[1] for c in commands if c and c[0]=='PLANT')
        blocked={k for k,n in plants.items() if n>pr['seeds'].get(k,0)}
        for i,cmd in enumerate(commands):
            if cmd and cmd[0]=='PLANT' and cmd[1] in blocked:continue
            rules._apply_unit_action(farm,pr,i,cmd,10,step//24,24)
        own_sales=Counter()
        for order in action.get('market',[])[:10]:
            if len(order)>=3 and order[0]=='SELL' and order[1] in TRACKED:
                k=order[1];n=min(max(0,int(order[2])),max(0,pr['shed'].get(k,0)))
                own_sales[k]+=n;pr['shed'][k]=pr['shed'].get(k,0)-n
        for k in TRACKED:
            self.low[k]+=gains_low[k];self.high[k]+=gains_high[k];self.mean[k]+=gains_mean[k]
            town=0
            if step%4==0:
                for name in old['town']['unlocked_shops']:
                    products=rules.SHOPS[name]
                    if k in products:town+=2 if len(products)==1 else 1
            if step%24==0 and k in rules.TOWN_CENTER_PRODUCTS:town+=1
            observed=obs['market']['inventory'][k]-old['market']['inventory'][k]+town
            # Strictly above the floor, every sold unit changes inventory.
            after_town_inventory=obs['market']['inventory'][k]+town
            exact=rules.market_price(k,after_town_inventory)>1 and old['market']['prices'][k]>1
            if exact:
                sold=max(0,observed-own_sales[k])
                self.low[k]=max(0,self.low[k]-sold);self.high[k]=max(0,self.high[k]-sold);self.mean[k]=max(0,self.mean[k]-sold)
            else:
                # Sales at the floor are observationally censored. A market
                # inventory delta must not be presented as an exact sale count.
                changed=max(0,observed-own_sales[k]);self.low[k]=0
                self.high[k]=max(0,self.high[k]-changed);self.mean[k]=max(0,self.mean[k]-changed)
            if night:
                # Unknown competing warehouse products may cause any amount
                # of overflow; retain that ambiguity in the lower bound.
                self.low[k]=0;self.high[k]=min(100,self.high[k]);self.mean[k]=min(self.high[k],self.mean[k])
            elif possible_drop:
                # Carried quantities and warehouse space are private. A unit
                # at the depot may legally discard overflow this very turn.
                self.low[k]=0
        self.observations+=1
    def snapshot(self):
        return {k:dict(low=self.low[k],mean=self.mean[k],high=self.high[k]) for k in TRACKED}
