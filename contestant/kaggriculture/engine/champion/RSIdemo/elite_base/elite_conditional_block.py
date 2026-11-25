# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Short block search with conditional procurement repair only."""
from .elite_conditional_market import ConditionalMarketMixin
from .elite_block import BlockMixin
from .joint_adaptive import AdaptivePlanner,InventoryBelief,_opening
from .joint_v2 import PORTFOLIO_REPORT
class ConditionalBlockPlanner(ConditionalMarketMixin,BlockMixin,AdaptivePlanner):
    pass

_players={};_beliefs={}
def agent(observation,configuration=None):
    step=int(observation['step']);p=int(observation['player'])
    if step==0:_players.pop(p,None);_beliefs[p]=InventoryBelief()
    belief=_beliefs.setdefault(p,InventoryBelief());belief.update(observation)
    obs=dict(observation);obs['_inventory_belief']=belief.snapshot()
    if step<360:action=_opening(observation,configuration)
    else:
        if p not in _players or step==360:_players[p]=ConditionalBlockPlanner()
        action=_players[p].act(obs,configuration)
        if step%24==0:PORTFOLIO_REPORT['days'][-1]['inventory_belief']=belief.snapshot()
    belief.remember(observation,action)
    return action


