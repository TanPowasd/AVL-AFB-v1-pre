"""Causal PDS after own unit actions, before simultaneous market clearing.

Values estimate remaining cash margin in 10,000 cash units. Rival actions,
market clearing and town events belong to the unknown transition.
"""
from collections import Counter
import copy
from dataclasses import dataclass
import math
from .elite_base import gpt6_rules as rules


def _number(value, default=0.0):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('non-finite value in observation')
    return value


def _action_counts(action):
    counts = Counter()
    if not isinstance(action, dict):
        return counts
    for command in [action.get('farmer', []), *action.get('hands', [])]:
        if command and isinstance(command, (list, tuple)):
            counts[str(command[0])] += 1
    return counts


def cash_margin(obs):
    p = int(obs['player'])
    return float(obs['farms'][p]['money']) - float(obs['farms'][1-p]['money'])


def local_post_decision(observation, action, configuration=None):
    """Apply the official own-unit phase on detached legal observations.

    Same-frame seed orders cannot enable planting. Overcommitted seeds cancel
    all same-crop planting commands, matching the official interpreter.
    """
    cfg = configuration or {}
    allowed = ('step', 'day', 'hour', 'player', 'farms', 'market', 'town', 'private')
    after = copy.deepcopy({k: observation[k] for k in allowed if k in observation})
    p = int(after['player']); step = int(after['step'])
    units = [action.get('farmer', ['PASS']), *action.get('hands', [])]
    planted = Counter(a[1] for a in units if isinstance(a, list) and len(a) >= 2 and a[0] == 'PLANT')
    blocked = {k for k,n in planted.items() if n > after['private']['seeds'].get(k,0)}
    for i, command in enumerate(units):
        if isinstance(command, list) and len(command) >= 2 and command[0] == 'PLANT' and command[1] in blocked:
            command = ['PASS']
        rules._apply_unit_action(after['farms'][p], after['private'], i, command,
            int(cfg.get('boardSize',10)), step//24, 24, int(cfg.get('shedCapacity',100)))
    after['_pending_market'] = copy.deepcopy(action.get('market', []))
    return after


class StateEncoder:
    """Bounded features centered on the game's market inventory baseline."""
    def __init__(self, products=()):
        self.products = tuple(products or rules.PRODUCTS)

    def encode(self, observation, action=None):
        obs = observation; step = int(obs['step']); day = step//24
        p = int(obs['player']); remaining = max(0., (719-step)/719.)
        features = {'bias':1., 'remaining':remaining, 'hour':(step%24)/23.,
                    'cash_gap':math.tanh(cash_margin(obs)/50000.)}
        for side, farm in (('own',obs['farms'][p]), ('rival',obs['farms'][1-p])):
            counts = Counter(); held = Counter(); ages = Counter(); mature = Counter()
            for row in farm['tiles']:
                for tile in row:
                    if not isinstance(tile,dict):continue
                    key = tile.get('crop',tile.get('animal'))
                    if key is None:continue
                    counts[key] += 1; held[key] += tile.get('yield_units',0)
                    spec = rules.CROPS[key] if key in rules.CROPS else rules.ANIMALS[key]
                    age = day-tile.get('planted_day',tile.get('placed_day',day))
                    ages[key] += min(1.,max(0.,age/max(1,spec['first_yield_day'])))
                    mature[key] += age >= spec['first_yield_day']
            features[f'{side}:cash'] = math.tanh(float(farm['money'])/100000.)
            features[f'{side}:workers'] = min(1.,(len(farm.get('hands',[]))+1)/16.)
            features[f'{side}:land'] = len(farm['unlocked_quadrants'])/4.
            for key in (*rules.CROPS,*rules.ANIMALS):
                features[f'{side}:count:{key}'] = counts[key]/100.
                features[f'{side}:mature:{key}'] = mature[key]/100.
                features[f'{side}:age:{key}'] = ages[key]/100.
                features[f'{side}:held:{key}'] = math.tanh(held[key]/100.)
        shops=Counter(obs['town']['unlocked_shops'])
        for shop in sorted(rules.SHOPS):features[f'shop:{shop}'] = shops[shop]/8.
        private=obs['private']; bags=Counter()
        for bag in private.get('inventories',[]):bags.update(bag)
        for item in self.products:
            params=rules.MARKET_PARAMS[item]
            features[f'inventory:{item}']=math.tanh((obs['market']['inventory'][item]-params['I0'])/params['T'])
            features[f'price:{item}']=math.tanh(obs['market']['prices'][item]/max(1.,params['base']))
            features[f'shed:{item}']=math.tanh(private['shed'].get(item,0)/100.)
            features[f'bags:{item}']=math.tanh(bags[item]/100.)
        for crop in rules.CROPS:features[f'seed:{crop}']=math.tanh(private['seeds'].get(crop,0)/25.)
        orders=obs.get('_pending_market', (action or {}).get('market',[]))
        pending=Counter()
        for order in orders:
            if len(order)>=3:pending[f'{order[0]}:{order[1]}']+=float(order[2])
            elif order:pending[str(order[0])]+=float(order[1]) if len(order)>1 and isinstance(order[1],(int,float)) else 1.
        for key,value in pending.items():features[f'pending:{key}']=math.tanh(value/25.)
        if action:
            for op,n in _action_counts(action).items():features[f'action:{op}']=min(1.,n/16.)
        # Remaining value vanishes at the true terminal boundary.
        return {k:_number(v)*remaining for k,v in features.items()}


class LinearValueFunction:
    def __init__(self, learning_rate=.02, l2=1e-5):
        self.learning_rate=float(learning_rate);self.l2=max(0.,float(l2));self.weights={}

    def predict(self, features):
        return sum(self.weights.get(k,0.)*v for k,v in features.items())

    def update(self, features, target, weight=1.):
        error=_number(target)-self.predict(features)
        rate=self.learning_rate*weight/max(1.,sum(v*v for v in features.values()))
        for k,v in features.items():
            self.weights[k]=(1-self.learning_rate*self.l2)*self.weights.get(k,0.)+rate*error*v
        return error


@dataclass(frozen=True)
class PostDecisionState:
    features: dict
    immediate_reward: float
    observation: dict


class ADPPDS:
    def __init__(self, encoder=None, value_function=None, gamma=1.):
        self.encoder=encoder or StateEncoder();self.value=value_function or LinearValueFunction()
        self.gamma=float(gamma)

    def post_decision(self, observation, action, immediate_reward=0., configuration=None):
        after=local_post_decision(observation,action,configuration)
        return PostDecisionState(self.encoder.encode(after),float(immediate_reward)+(cash_margin(after)-cash_margin(observation))/10000.,after)

    def q_value(self, observation, action, immediate_reward=0., successor=None):
        pds=self.post_decision(observation,action,immediate_reward)
        if successor is not None:raise ValueError('PDS scoring must not read a realized future state')
        return pds.immediate_reward+self.gamma*self.value.predict(pds.features)

    def fit_transition(self, observation, action, reward, next_observation=None, done=False):
        pds=self.post_decision(observation,action)
        target=float(reward)
        if not done and next_observation is not None:
            target+=self.gamma*self.value.predict(self.encoder.encode(next_observation))
        return self.value.update(pds.features,target)


def rank_actions(model, observation, actions, reward_fn=None):
    rows=[(model.q_value(observation,a,0. if reward_fn is None else reward_fn(observation,a)),i,a)
          for i,a in enumerate(actions)]
    return sorted(rows,key=lambda r:(r[0],-r[1]),reverse=True)

