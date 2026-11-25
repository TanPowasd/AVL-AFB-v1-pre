# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Converged whole-farm optimization with public-history inventory uncertainty."""
from .joint_v2 import CertifiedPlanner,_opening,PORTFOLIO_REPORT
from .converged_portfolio import ConvergedPortfolio
from .exact_market_model import MarketModel
from .inventory_belief import InventoryBelief
from .inventory_bank import select
class AdaptiveBook(MarketModel):
    residual_selector=staticmethod(select)
class AdaptivePortfolio(ConvergedPortfolio):
    book_type=AdaptiveBook
class AdaptivePlanner(CertifiedPlanner):
    feedback_rounds=2
    select_certified_best=True
    portfolio_type=AdaptivePortfolio
_players={};_beliefs={}
def agent(observation,configuration=None):
    step=int(observation['step']);p=int(observation['player'])
    if step==0:_players.pop(p,None);_beliefs[p]=InventoryBelief()
    belief=_beliefs.setdefault(p,InventoryBelief());belief.update(observation)
    obs=dict(observation);obs['_inventory_belief']=belief.snapshot()
    if step<360:action=_opening(observation,configuration)
    else:
        if p not in _players or step==360:_players[p]=AdaptivePlanner()
        action=_players[p].act(obs,configuration)
        if step%24==0:PORTFOLIO_REPORT['days'][-1]['inventory_belief']=belief.snapshot()
    belief.remember(observation,action)
    return action
