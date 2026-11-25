"""03 G02 field supply orders: a real shipment into a formation's own store.

03: "补给半径BASE2跳，G02装vehicle×2的野战站5跳。半径仅允许创建补给运输单，不瞬移
物资；须实际走Shipment并入Formation随队仓（每单元20载重）。"  A supply order is an
ordinary ``logistics/route_shipment`` (direct haul or intermodal relay) with the
optional ``formation_id``.  Its final unload lands in the destination hub's
``field_supply:<formation>`` bucket; P2 then moves it into the formation's
store ``Account(owner, formation, 'available', good)`` while the formation is
at that hub.  Anything that cannot join the formation (moved, disbanded, store
full) is released to the hub's ordinary ``available`` stock, never teleported.
"""
from .errors import RuleViolation
from .ledger import Account, Transfer
from .numeric import Q, floor

BASE_RADIUS = 2
FIELD_DEPOT_RADIUS = 5
FIELD_DEPOT_VEHICLES_mU = 2000   # 04 G02: vehicle x2 at the field depot
LOAD_PER_UNIT_mU = 20000         # 03: 20 standard loads per unit


def bucket(formation_id):
    return f'field_supply:{formation_id}'


def field_depot(state, owner, hub):
    """A G02 field depot: the nation knows G02 and holds vehicle x2 at the hub."""
    return ('G02' in state.research[owner].known and
            state.initial.inventory.balance(Account(owner, hub, 'available', 'vehicle')) >= FIELD_DEPOT_VEHICLES_mU)


def store_load(state, formation):
    inv = state.initial.inventory
    return sum((Q(q) * state.cargo_weights[a.asset] for a, q in inv.balances().items()
                if a.owner == formation.owner and a.location == formation.id and q), Q(0))


def capacity(formation):
    return LOAD_PER_UNIT_mU * len(formation.unit_ids)


def validate(state, owner, order, path):
    """Radius, destination and store-capacity checks for a formation supply order."""
    fid = order['formation_id']
    formation = state.formations.rows.get(fid)
    if formation is None or formation.owner != owner:
        raise RuleViolation('ERR_NO_VISIBLE_TARGET')
    if formation.hub != order['dest_hub']:
        raise RuleViolation('ERR_SITE_INCOMPATIBLE', 'supply order must end at the formation hub')
    origin = order['origin_hub']
    depot = formation.domain == 'Land' and field_depot(state, owner, origin)
    radius = FIELD_DEPOT_RADIUS if depot else BASE_RADIUS
    if len(path) > radius:
        raise RuleViolation('ERR_SITE_INCOMPATIBLE', f'supply radius is {radius} hops')
    weight = Q(order['qty_mU']) * state.cargo_weights[order['good']]
    if store_load(state, formation) + weight > capacity(formation):
        raise RuleViolation('ERR_CAPACITY_EXCEEDED', 'formation store holds 20 loads per unit')
    return formation, depot and len(path) > BASE_RADIUS


def absorb(state, ctx):
    """P2: move delivered field supply into the formation store (or release it)."""
    inv = state.initial.inventory
    rows = sorted((a, q) for a, q in inv.balances().items() if q and a.bucket.startswith('field_supply:'))
    if not rows:
        return
    transfers = []
    loads = {}
    for account, quantity in rows:
        fid = account.bucket.split(':', 1)[1]
        formation = state.formations.rows.get(fid)
        moved = 0
        if formation is not None and formation.owner == account.owner and formation.hub == account.location:
            if fid not in loads:
                loads[fid] = store_load(state, formation)
            weight = state.cargo_weights[account.asset]
            room = capacity(formation) - loads[fid]
            quantum = 1000 if state.catalog.goods[account.asset]['indivisible'] else 1
            moved = min(quantity, floor(room / weight) if weight else quantity) if room > 0 else 0
            moved -= moved % quantum
            if moved:
                transfers.append(Transfer(account, Account(account.owner, fid, 'available', account.asset), moved,
                                          destination_hub=account.location))
                loads[fid] += moved * weight
        if quantity - moved:
            transfers.append(Transfer(account, Account(account.owner, account.location, 'available', account.asset),
                                      quantity - moved))
    inv.transfer_many(tuple(transfers))
