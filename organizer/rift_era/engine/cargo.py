"""Physical cargo weights from 02; distinct from scoring shadow prices."""

from math import gcd

from .errors import InvariantViolation
from .numeric import Q


FIXED = {'vehicle': Q(5), 'ground_drone': Q(4), 'air_drone': Q(2),
         'armored_unit': Q(15), 'artillery_unit': Q(12), 'locomotive': Q(25)}


def cargo_weights(catalog):
    result = {g: Q(1) for g, row in catalog.goods.items() if not row['indivisible']}
    result.update(FIXED)
    visiting = set()

    def weight(good):
        if good in result:
            return result[good]
        if good in visiting:
            raise InvariantViolation('whole-machine cargo weight dependency cycle')
        visiting.add(good)
        recipe = next((r for _, r in sorted(catalog.recipes.items()) if good in r['outputs']), None)
        if recipe is None:
            raise InvariantViolation(f'no manufacturing recipe for cargo {good}')
        mass = sum((Q(q) * weight(g) for g, q in recipe['inputs'].items()), Q(0))
        result[good] = max(Q(1), mass / Q(recipe['outputs'][good]))
        visiting.remove(good)
        return result[good]

    for good, row in catalog.goods.items():
        if row['kind'] == 'inventory':
            weight(good)
    return result


_LOAD_CACHE: list = [None]
_WEIGHT_CACHE: list = [None]


def _integer_weights(weights):
    """Weights scaled to one common denominator, so loads sum as integers."""
    cached = _WEIGHT_CACHE[0]
    if cached is not None and cached[0] is weights and cached[1] == len(weights):
        return cached[2], cached[3]
    denominator = 1
    for w in weights.values():
        denominator = denominator * w.denominator // gcd(denominator, w.denominator)
    scaled = {good: w.numerator * (denominator // w.denominator) for good, w in weights.items()}
    _WEIGHT_CACHE[0] = (weights, len(weights), scaled, denominator)
    return scaled, denominator


def warehouse_load(inventory, weights, hubs):
    # Inventory balances change only through Inventory._apply, which bumps
    # _revision, so an unchanged (inventory, revision, weights, hubs) has the
    # same exact load. Callers receive a fresh dict they may mutate.
    revision = getattr(inventory, '_revision', None)
    hub_keys = tuple(hubs)
    cached = _LOAD_CACHE[0]
    if (revision is not None and cached is not None and cached[0] is inventory and cached[1] == revision
            and cached[2] is weights and cached[3] == hub_keys):
        return dict(cached[4])
    # Exact: sum(q_i * w_i) == Fraction(sum(q_i * w_i * D), D) for a common
    # denominator D of all weights; integer sums replace Fraction products.
    scaled, denominator = _integer_weights(weights)
    totals = dict.fromkeys(hubs, 0)
    for account, quantity in inventory.balances().items():
        if account.location in totals and account.bucket != 'outdoor' and not account.bucket.startswith('locked_outdoor_subsistence:'):
            totals[account.location] += quantity * scaled[account.asset]
    result = {hub: Q(total, denominator) for hub, total in totals.items()}
    if revision is not None:
        _LOAD_CACHE[0] = (inventory, revision, weights, hub_keys, dict(result))
    return result
