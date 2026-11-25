# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Preserve original setup sales unless their own-only continuation misses inputs.

The lookahead uses the native unit-by-unit market transition and known town
consumption, with an explicitly empty opponent order list and no rival private
state. It is a conditional forecast, not a guarantee against rival trades.
Planning, cash_available and the route solver remain the AdaptivePlanner ones.
"""
import copy
from types import SimpleNamespace
from .elite_execution_v2 import ExecutionV2Mixin
from .joint_adaptive import AdaptivePlanner, InventoryBelief, _opening
from .joint_v2 import PORTFOLIO_REPORT
from . import gpt6_rules as rules


class _Observation(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def advance_own_market(observation, orders, unit_actions=None):
    """One native market window; only own private state is constructed.

Farm workers PASS during normal setup. Explicit current unit actions, when
provided, run before trading just as in the native engine. No unknown future
shop is sampled or read: no shop unlock occurs between setup hours 0 and 2.
"""
    obs = copy.deepcopy(observation)
    player = int(obs['player'])
    farm = obs['farms'][player]
    private = obs['private']
    step = int(obs['step'])
    if unit_actions:
        for actor, command in enumerate(unit_actions):
            if actor <= len(farm['hands']):
                rules._apply_unit_action(farm, private, actor, command,
                                         len(farm['tiles']), step // 24, 24)
    states = []
    for index in range(len(obs['farms'])):
        # This empty state is not a guess at the rival's actual inventory;
        # with no opponent orders it is never used for a rival transaction.
        side_private = private if index == player else dict(shed={}, seeds={}, inventories=[])
        states.append(SimpleNamespace(
            observation=_Observation(farms=obs['farms'], market=obs['market'],
                                     town=obs['town'], private=side_private),
            action=dict(market=orders if index == player else [])))
    env = SimpleNamespace(configuration=dict(boardSize=len(farm['tiles']),
                                              maxMarketOrdersPerTurn=10,
                                              shedCapacity=100))
    rules._process_market(states, env)
    rules._town_consume(env, states, step)
    obs.update(step=step + 1, day=(step + 1) // 24, hour=(step + 1) % 24)
    return obs


class _CrewFirstAdapter(ExecutionV2Mixin, AdaptivePlanner):
    """Bind the existing market method to a compatible MRO, without init."""
    pass


class ConditionalMarketMixin:
    def setup_missing(self, obs):
        farm = obs['farms'][obs['player']]
        private = obs['private']
        missing = {}
        if len(farm['hands']) < self.crew:
            missing['hands'] = self.crew - len(farm['hands'])
        for label, requirements, inventory in (
            ('resource', self.want_resources, private['shed']),
            ('animal', self.want_animals, private['shed']),
            ('seed', self.want_seeds, private['seeds']),
        ):
            for item, quantity in requirements.items():
                deficit = quantity - inventory.get(item, 0)
                if deficit > 0:
                    missing[label + ':' + item] = deficit
        if self.buy_land and len(farm['unlocked_quadrants']) < self.target_quads:
            missing['land'] = self.target_quads - len(farm['unlocked_quadrants'])
        return missing

    def market(self, obs, hour, unit_actions=None):
        # Deliberately bypass any other market mixin: preservation refers to
        # the verified original AdaptivePlanner ordering, including late sales.
        original = AdaptivePlanner.market(self, obs, hour, unit_actions)
        if hour >= self.p['setup_hours']:
            return original
        forecast = advance_own_market(obs, original, unit_actions)
        for future_hour in range(hour + 1, self.p['setup_hours']):
            future_orders = AdaptivePlanner.market(self, forecast, future_hour)
            forecast = advance_own_market(forecast, future_orders)
        missing = self.setup_missing(forecast)
        if missing:
            # Calling ExecutionV2Mixin.market with an unrelated self would
            # leave its zero-argument super invalid. Bind it to a real subtype
            # and a shallow read-only view of the original planner's fields.
            adapter = object.__new__(_CrewFirstAdapter)
            adapter.__dict__ = dict(self.__dict__)
            chosen = adapter.market(obs, hour, unit_actions)
        else:
            chosen = original
        if PORTFOLIO_REPORT['days'] and PORTFOLIO_REPORT['days'][-1].get('day') == self.day:
            PORTFOLIO_REPORT['days'][-1].setdefault('conditional_market', []).append(
                dict(hour=hour, repaired=bool(missing), predicted_missing=missing,
                     assumption='native own-only orders plus known town consumption'))
        return chosen


class ConditionalMarketPlanner(ConditionalMarketMixin, AdaptivePlanner):
    pass


_players = {}
_beliefs = {}


def agent(observation, configuration=None):
    step = int(observation['step'])
    player = int(observation['player'])
    if step == 0:
        _players.pop(player, None)
        _beliefs[player] = InventoryBelief()
    belief = _beliefs.setdefault(player, InventoryBelief())
    belief.update(observation)
    obs = dict(observation)
    obs['_inventory_belief'] = belief.snapshot()
    if step < 360:
        action = _opening(observation, configuration)
    else:
        if player not in _players or step == 360:
            _players[player] = ConditionalMarketPlanner()
        action = _players[player].act(obs, configuration)
        if step % 24 == 0:
            PORTFOLIO_REPORT['days'][-1]['inventory_belief'] = belief.snapshot()
    belief.remember(observation, action)
    return action
