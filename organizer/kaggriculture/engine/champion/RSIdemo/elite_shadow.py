"""A bounded hypothetical rival policy, using public state and legal beliefs.

This produces an assumed production/input calendar, not predicted exact daily
trades. It supplements empirical scenarios and never replaces their ensemble.
No other player's private inventory, future shop, seed or budget is available.
"""
import copy
import math
from time import perf_counter

from .elite_base import gpt6_rules as rules
from .elite_base.exact_market_model import MarketModel
from .elite_base.inventory_bank import ITEMS, select as original_select
from .elite_base.joint_adaptive import AdaptivePortfolio
from .elite_base.market_model import cohort_supply
from .elite_base.portfolio import Portfolio
from .elite_base.gpt6_policy import fibcost


class ShadowBudgetExhausted(Exception):
    pass


def check_deadline(deadline):
    if perf_counter()>=deadline:raise ShadowBudgetExhausted()


class ShadowBook(MarketModel):
    # Pin the original nonrecursive prior. The parent's enhanced/replay book
    # never enters this model, and a rival perspective needs no reverse belief.
    residual_selector=staticmethod(original_select)

    def __init__(self,obs):
        self.shadow_deadline=obs['_shadow_deadline']
        check_deadline(self.shadow_deadline)
        super().__init__(obs)
        check_deadline(self.shadow_deadline)

    def component(self,item,flows,want_gradient=False):
        check_deadline(self.shadow_deadline)
        return super().component(item,flows,want_gradient)


class ShadowPortfolio(AdaptivePortfolio):
    book_type=ShadowBook

    def __init__(self,obs,positions,previous=None):
        self.shadow_deadline=obs['_shadow_deadline']
        super().__init__(obs,positions,previous)

    def install(self,pos,project):
        check_deadline(self.shadow_deadline)
        return super().install(pos,project)

    def offer(self,pos,project):
        check_deadline(self.shadow_deadline)
        return super().offer(pos,project)

    def animal_column(self,*args,**kwargs):
        check_deadline(self.shadow_deadline)
        return super().animal_column(*args,**kwargs)

    def optimize(self,budget,only_positions=None,sweeps=1):
        # ConvergedPortfolio silently adds two sweeps even when sweeps=1.
        # This shadow makes exactly one finite coordinate pass and no restart.
        if sweeps!=1:raise ValueError('Shadow policy supports exactly one sweep')
        check_deadline(self.shadow_deadline)
        return Portfolio.optimize(self,budget,only_positions=only_positions,sweeps=1)


def rival_observation(obs,belief=None):
    """Build a detached rival view without reading this player's private state.

    A belief must be supplied by the caller's InventoryBelief.snapshot, computed
    from legitimate past observations/actions. All unknown stocks default zero.
    Tracked mean holdings span shed and bags, so assuming depot placement is an
    explicit simplification; the total is capped to the official shed capacity.
    """
    player=1-int(obs['player']);step=int(obs['step']);private=rules._new_private()
    means={key:0.0 for key in ITEMS}
    for key,bounds in (belief or {}).items():
        if key not in means or not isinstance(bounds,dict):continue
        value=float(bounds.get('mean',0))
        if not math.isfinite(value):raise ValueError('Non-finite rival stock belief')
        means[key]=max(0.0,value)
    scale=min(1.,100/max(1.,sum(means.values())))
    quantities={key:int(value*scale) for key,value in means.items()}
    for key,n in quantities.items():private['shed'][key]=n
    private['seeds']={key:0 for key in rules.CROPS}
    private['inventories']=[{} for _ in range(1+len(obs['farms'][player]['hands']))]
    # No remainingOverageTime: own shadow allowance is enforced locally; the
    # actual rival callback's remaining budget is hidden and never impersonated.
    return {'step':step,'day':step//24,'hour':step%24,'player':player,
            'farms':copy.deepcopy(obs['farms']),'market':copy.deepcopy(obs['market']),
            'town':copy.deepcopy(obs['town']),'private':private}


def scenario(obs,*,belief=None,max_seconds=.5,diagnostics=None):
    """Return one hypothetical residual row or None if early/over budget.

    Diagnostics receives elapsed time and discard reason even when returning
    None. Cooperative checks occur between local model operations; the caller
    must charge measured time against its own 1s + 60s official allowance.
    """
    started=perf_counter();report=diagnostics if diagnostics is not None else {}
    report.update(kind='assumed policy scenario',accepted=False,sweeps=0)
    day=int(obs['step'])//24
    if not 15<=day<30:
        report.update(gate='outside day 15..29',seconds=perf_counter()-started);return None
    if not math.isfinite(max_seconds) or max_seconds<=0:
        report.update(gate='no shadow allowance',seconds=perf_counter()-started);return None
    deadline=started+max_seconds
    try:
        rival=rival_observation(obs,belief)
        rival['_shadow_deadline']=deadline
        farm=rival['farms'][rival['player']]
        positions=[(x,y) for y,row in enumerate(farm['tiles']) for x,tile in enumerate(row) if tile!='LOCKED']
        herd=sum(isinstance(t,dict) and 'animal' in t for row in farm['tiles'] for t in row)
        estimated_stock_value=sum(n*rival['market']['prices'].get(k,0)*.9 for k,n in rival['private']['shed'].items()
                                  if k in rules.PRODUCTS)
        budget=max(0.,farm['money']+estimated_stock_value-fibcost(8)-herd*rival['market']['prices']['WHEAT']-100)
        model=ShadowPortfolio(rival,positions)
        check_deadline(deadline)
        report['sweeps']=1;model.optimize(budget,sweeps=1)
        check_deadline(deadline)
        physical=cohort_supply(farm,day,1,0)
        row={'day':day,'kind':'assumed policy scenario','actual':None,
             'future_shops':[list(rival['town']['unlocked_shops']) for _ in range(day,30)],
             'residual':[[model.flow[key][d]-physical[key][d] for d in range(day,30)] for key in ITEMS],
             'assumptions':{'stock':'legal history belief mean, depot placement, capped total 100',
                            'seeds_and_unplaced_animals':'unknown, assumed zero',
                            'shops':'current known shops only, no future unlocks',
                            'calendar':'production minus inputs, not exact execution or transaction dates',
                            'land':'only currently unlocked plots; one economic sweep; no route certificate'}}
        report.update(accepted=True,gate='one sweep completed',budget=budget,positions=len(positions),
                      accepted_columns=model.accepted,objective=model.value,seconds=perf_counter()-started)
        if report['seconds']>max_seconds:
            report.update(accepted=False,gate='shadow allowance exceeded');return None
        row['shadow_seconds']=report['seconds']
        return row
    except ShadowBudgetExhausted:
        report.update(accepted=False,gate='shadow allowance exceeded',seconds=perf_counter()-started)
        return None


def augment(base_rows,shadow_row):
    """Keep every empirical scenario; at most one added shadow gets equal weight.

    The original market's additional Monte Carlo scenarios are also preserved by
    its existing constructor. A rejected shadow leaves this prior untouched.
    """
    if shadow_row is None:return list(base_rows)
    if shadow_row.get('kind')!='assumed policy scenario':raise ValueError('Expected a shadow scenario')
    return list(base_rows)+[shadow_row]
