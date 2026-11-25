# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Finite-horizon project calculus with the game's crop and animal semantics.

This is an economic column generator, not a claim of route feasibility. Every
column retains all dated work and inventory flows; the dispatcher must certify
the first day's jobs against the actual worker/resource state.
"""
from functools import lru_cache
from dataclasses import dataclass
from collections import Counter
import sys
from pathlib import Path

from .gpt6_rules import CROPS, ANIMALS

# Optional per-policy context; default preserves the original implementation.
FAST_REUSE = False


@lru_cache(maxsize=32768)
def _crop_transitions(crop,planted,day,dry,fertilized_until,held,end):
    """Price-independent physical transitions, in the original tie order."""
    cd=CROPS[crop];ongoing=cd['ongoing'];age=day-planted
    last=cd['first_yield_day']+(cd['max_yield']-1)*cd['interval'] if ongoing else cd['max_yield_day']
    transitions=[]
    for water in (0,1):
        if not water and dry>=1 and day<end:continue
        for fertilize in (0,1):
            if fertilize and (not water or fertilized_until>=day+1):continue
            if fertilize and (not ongoing and not (cd['max_yield_day']+1)//2<=age<=last):continue
            fu=day+2 if fertilize else fertilized_until
            ripe=held
            if water and not ongoing and (cd['max_yield_day']+1)//2<=age<=last:
                ripe=min(cd['max_yield'],ripe+(2 if fu>=day else 1))
            can_harvest=ripe>0 and age>=cd['first_yield_day']
            for harvest in ((0,1) if can_harvest else (0,)):
                ops=(('FERTILIZE',),)*fertilize+(('WATER',),)*water+(('HARVEST',),)*harvest
                outputs=((crop,ripe),) if harvest else ()
                inputs=(('FERTILIZER',1),) if fertilize else ()
                short=bool(harvest and not ongoing)
                if short:nxt=None
                else:
                    next_held=0 if harvest else ripe
                    if ongoing:
                        since=age+1-cd['first_yield_day']
                        if since>=0 and since%cd['interval']==0 and since//cd['interval']<cd['max_yield']:
                            next_held=min(cd['max_yield'],next_held+(2 if water and fu>=day else 1))
                    nxt=(day+1,0 if water else dry+1,fu if fu>=day+1 else -1,next_held)
                transitions.append((fertilize,harvest,ripe,short,nxt,ops,outputs,inputs))
    return tuple(transitions)


@lru_cache(maxsize=32768)
def _animal_transitions(animal,placed,end,d,hungry,bonus,held,fert,already_fed,already_cared):
    ad=ANIMALS[animal];product=ad['product'];transitions=[]
    for feed in ((0,) if already_fed else (0,1)):
        for care in ((0,) if already_cared else (0,1)):
            if care and (not (feed or already_fed) or d>=end-1):continue
            for harvest in ((0,1) if held else (0,)):
                for collect in ((0,1) if fert else (0,)):
                    count=feed+care+harvest+collect
                    effective_feed=feed or already_fed
                    starvation=0 if effective_feed else hungry+1
                    if starvation>=2:nxt=None
                    else:
                        nxt_held=0 if harvest else held;b=bonus
                        age=d+1-placed-ad['first_yield_day']
                        if age>=0 and age%ad['interval']==0:
                            nxt_held=min(ad['max_held'],nxt_held+1+(b if effective_feed else 0));b=0
                        b+=(care or already_cared) if effective_feed else 0
                        nxt=(d+1,starvation,b,nxt_held,1)
                    ops=(('FEED',),)*feed+(('CARE',),)*care+(('HARVEST',),)*harvest+(('COLLECT_FERTILIZER',),)*collect
                    inputs=(('WHEAT',1),) if feed else ()
                    outputs=(((product,held),) if harvest else ())+((('FERTILIZER',1),) if collect else ())
                    transitions.append((feed,harvest,collect,count,nxt,ops,inputs,outputs))
    return tuple(transitions)


@dataclass(frozen=True,slots=True)
class Project:
    value: float
    # Entries: day, tuple(commands), tuple(output units), tuple(input units)
    days: tuple

    def first(self, day):
        for row in self.days:
            if row[0]==day:return row
            if row[0]>day:break
        return (day, (), (), ())

    def flows(self):
        ans = {}
        for d, _, outputs, inputs in self.days:
            row = ans.setdefault(d, Counter())
            row.update(dict(outputs))
            row.subtract(dict(inputs))
        return ans


class CropDP:
    """Backward dynamic programming over full crop states and land reuse.

    Fertilizer is valued at its opportunity cost, including home production.
    Today's harvest may be followed by a new crop on the same tile. Decomposition
    uses supplied daily resource prices; those prices come from the whole farm.
    """
    def __init__(self, prices, labor, visit, end=29, allowed=None, start=0):
        self.prices = prices
        self.labor = labor
        self.visit = visit
        self.end = end
        self.allowed = tuple(allowed or CROPS)
        self.free = {end+1: Project(0.0, ())}
        self.fast_reuse = FAST_REUSE
        self._reuse_parts = {}
        self.stats = Counter()
        self._solvers = {}
        for d in range(end, start-1, -1):
            best = self.free[d+1]
            for crop in self.allowed:
                cd = CROPS[crop]
                if d + cd['first_yield_day'] > end:
                    continue
                future = self._solver(crop, d)(d+1, 0, -1, 0 if cd['ongoing'] else 1)
                cost = cd['seed'] + 2*self.labor[d] + self.visit[d]
                value=future.value-cost
                if value > best.value:
                    best = Project(value,
                        ((d, (('PLANT',crop),('WATER',)), (), (('SEED_'+crop,1),)),)+future.days)
            self.free[d] = best

    def reuse_parts(self,day):
        # free[day] is already finalized whenever a later DP state reads it.
        # Preserve the exact command/row order; cache only immutable tuples.
        if day not in self._reuse_parts:
            project=self.free[day]
            self._reuse_parts[day]=(project.first(day),tuple(r for r in project.days if r[0]!=day))
        return self._reuse_parts[day]

    def _solver(self, crop, planted):
        key = crop, planted
        if key in self._solvers:
            return self._solvers[key]
        cd = CROPS[crop]
        ongoing = cd['ongoing']
        last = cd['first_yield_day'] + (cd['max_yield']-1)*cd['interval'] if ongoing else cd['max_yield_day']
        @lru_cache(None)
        def solve(day, dry, fertilized_until, held):
            self.stats['states'] += 1
            if day > self.end:
                return Project(0.0, ())
            age = day-planted
            if age > last:
                # At normal dawn the expired crop starts hourly decay. This
                # model intentionally clears it instead of crediting late cargo.
                following = self.free[day]
                ops = (('DIG',),)
                today = self.reuse_parts(day)[0] if self.fast_reuse else following.first(day)
                combined = ops+today[1]
                tail=self.reuse_parts(day)[1] if self.fast_reuse else tuple(x for x in following.days if x[0]!=day)
                return Project(following.value-self.labor[day]-(0 if today[1] else self.visit[day]),
                    ((day,combined,today[2],today[3]),)+tail)
            best_value = -1e100
            winner = None
            # Land can be released immediately, including after the final
            # ongoing harvest. Waiting for tomorrow's decay wastes a full
            # growing day and is not an acceptable lifecycle model.
            reuse=self.free[day]
            reuse_today=self.reuse_parts(day)[0] if self.fast_reuse else reuse.first(day)
            endings=[((('DIG',),),(),0.0)]
            if ongoing and held>0 and age>=cd['first_yield_day']:
                endings.append(((('HARVEST',),('DIG',)),((crop,held),),held*self.prices[crop][day]))
            for finish_ops,finish_output,revenue in endings:
                value=reuse.value+revenue-len(finish_ops)*self.labor[day]
                if not reuse_today[1]:value-=self.visit[day]
                if value>best_value:
                    best_value=value
                    winner=('ending',finish_ops,finish_output,reuse,reuse_today)
            # Irrigation can be skipped for one night. On the final day only
            # actions that produce current cash have any value.
            crop_price=self.prices[crop][day];fert_price=self.prices['FERTILIZER'][day]
            labor=self.labor[day];visit=self.visit[day]
            for fertilize,harvest,ripe,short,nxt,ops,outputs,inputs in _crop_transitions(crop,planted,day,dry,fertilized_until,held,self.end):
                value=(ripe*crop_price if harvest else 0)-fertilize*fert_price
                value-=len(ops)*labor+(visit if ops else 0)
                if short:
                    future=self.free[day];reuse=self.reuse_parts(day)[0] if self.fast_reuse else future.first(day)
                    if reuse[1]:value+=visit
                else:future=solve(*nxt)
                value+=future.value
                if value>best_value:
                    best_value=value;winner=('action',short,ops,outputs,inputs,future)
            if winner[0]=='ending':
                _,finish_ops,finish_output,future,today=winner
                tail=self.reuse_parts(day)[1] if self.fast_reuse else tuple(r for r in future.days if r[0]!=day)
                path=((day,finish_ops+today[1],finish_output+today[2],today[3]),)+tail
            else:
                _,short,ops,outputs,inputs,future=winner
                if short:
                    reuse=self.reuse_parts(day)[0] if self.fast_reuse else future.first(day)
                    today=(day,ops+reuse[1],outputs+reuse[2],inputs+reuse[3])
                    tail=self.reuse_parts(day)[1] if self.fast_reuse else tuple(x for x in future.days if x[0]!=day)
                    path=(today,)+tail
                else:path=((day,ops,outputs,inputs),)+future.days
            return Project(best_value,path)
        self._solvers[key] = solve
        return solve

    def existing(self, tile, day):
        # Intended at day start: watered_today is false. Midday dispatcher does
        # not call the economic optimizer on partially executed states.
        return self._solver(tile['crop'],tile['planted_day'])(day,
            tile['consecutive_unwatered'],max(-1,tile.get('fertilized_until_day',-1)) if tile.get('fertilized_until_day',-1)>=day else -1,tile.get('yield_units',0))


def animal_project(animal, placed, day, tile, prices, labor, visit, end=29, reuse=None):
    """Exact finite-horizon care/feed DP, including delayed care credit.

    Harvest batching and fertilizer collection are optimized, not forced daily.
    Keeping an animal unfed is allowed only with its explicit survival state.
    """
    ad = ANIMALS[animal]
    product = ad['product']
    empty=Project(0.0,())
    death_cache={}
    def after_death(d):
        if reuse is None or d>=end:return empty
        if d in death_cache:return death_cache[d]
        free=reuse[d+1];row=free.first(d+1)
        if row[1]:
            future=Project(free.value-labor[d+1],
                ((d+1,(('DIG',),)+row[1],row[2],row[3]),)+tuple(x for x in free.days if x[0]!=d+1))
        else:
            path=[];cleared=False
            for entry in free.days:
                dd,oo,yy,ii=entry
                if oo and not cleared:
                    oo=(('DIG',),)+oo;cleared=True
                    free=Project(free.value-labor[dd],free.days)
                path.append((dd,oo,yy,ii))
            future=Project(free.value,tuple(path))
        death_cache[d]=future
        return future
    @lru_cache(None)
    def solve(d, hungry, bonus, held, fert):
        if d>end:
            return empty
        best_value=0.0;winner=None  # abandon future optional cash production
        already_fed=d==day and tile.get('fed_today',False)
        already_cared=d==day and tile.get('cared_today',False)
        product_price=prices[product][d];fert_price=prices['FERTILIZER'][d];wheat_price=prices['WHEAT'][d]
        labor_today=labor[d];visit_today=visit[d]
        for feed,harvest,collect,count,nxt,ops,inputs,outputs in _animal_transitions(animal,placed,end,d,hungry,bonus,held,fert,already_fed,already_cared):
            value=(held*product_price if harvest else 0)+(fert_price if collect else 0)-feed*wheat_price
            value-=count*labor_today+(visit_today if count else 0)
            future=after_death(d) if nxt is None else solve(*nxt)
            value+=future.value
            if value>best_value:
                best_value=value;winner=(ops,outputs,inputs,future)
        if winner is None:return empty
        ops,outputs,inputs,future=winner
        return Project(best_value,((d,ops,outputs,inputs),)+future.days)
    return solve(day,tile.get('consecutive_unfed',0),tile.get('pending_care_bonus',0),
                 tile.get('yield_units',0),int(tile.get('fertilizer_available',False)))
