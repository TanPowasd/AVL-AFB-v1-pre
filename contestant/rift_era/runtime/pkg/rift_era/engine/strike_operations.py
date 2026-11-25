"""N10 hypersonic strikes, blockade/interdiction and their G11 interception.

Every shot debits real ammunition from the launching Hub or formation, records
one Loss_action event per tick per executing entity (07) and damages only an
actual, visible, WAR-legal target. Nothing here rewards a kill: destroyed value
belongs to the victim's book.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .combat import blockade_fraction
from .command_schema import fields
from .determinism import digest, uniform
from .diplomacy import Relation
from .errors import InvariantViolation, RuleViolation
from .ledger import Account, Transfer
from .numeric import Q, ceil, floor
from .protocol import PreparedCommand
from .provenance import Use
from .scoring import action_loss

MISSILE = {'hypersonic_missile': 1000}
NONBASIC_DAMAGE_MU = 250
INTERCEPT_POWER_MU = 5000


@dataclass
class HypersonicShot:
    id: str
    command_id: str
    owner: str
    origin_hub: str
    target: str
    target_alias: str
    target_owner: str
    quantity: int
    launch_tick: int
    arrival_tick: int
    launched: bool = False

    def lock(self, good):
        return Account(self.owner, self.origin_hub, 'locked_strike:' + self.id, good)


@dataclass
class Blockade:
    id: str
    command_id: str
    owner: str
    formation: str
    edge: str
    targets: tuple[str, ...]
    started_tick: int


@dataclass
class Interdiction:
    id: str
    owner: str
    formation: str
    shipment: str
    alias: str
    tick: int


class StrikeOperations:
    """03 §7 blockade/interdiction and N10 strikes; one action budget per tick."""

    def __init__(self, state):
        self.s = state
        self.shots: dict[str, HypersonicShot] = {}
        self.blockades: dict[str, Blockade] = {}
        self.interdictions: list[Interdiction] = []
        self.action_cycles: dict[int, dict[str, Q]] = {}
        self.committed: set[str] = set()
        self.intercepts: dict[str, int] = {}
        self.power_demands: dict[str, tuple[str, int, int]] = {}
        self.recent = {p: [] for p in state.initial.known}
        self.contact_seq = {p: 0 for p in state.initial.known}
        self.track_aliases = {p: {} for p in state.initial.known}
        self.alias_tracks = {p: {} for p in state.initial.known}
        self.visible_shipments = {p: [] for p in state.initial.known}
        self.last_tick = -1
        self.blocked_edges: set[str] = set()

    # ---- helpers ----
    def _report(self, owner, row):
        self.recent[owner] = (self.recent[owner] + [row])[-64:]

    def formation(self, owner, fid):
        row = self.s.formations.rows.get(fid)
        if row is None or row.owner != owner:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if row.refitting:
            raise RuleViolation('ERR_SITE_INCOMPATIBLE')
        return row

    def reserve(self, ctx):
        """P1: G11 interception power is a separate, explicitly booked load."""
        self.power_demands = {}
        state = self.s
        for formation in state.formations.rows.values():
            if formation.refitting or formation.hub is None:
                continue
            batteries = sum(state.initial.units[u].unit_type == 'energy_weapon' for u in formation.unit_ids)
            if batteries and any(shot.launched and shot.arrival_tick == ctx.tick and
                                 self._target_owner(shot) == formation.owner for shot in self.shots.values()):
                entity = f'intercept:{formation.id}'
                self.power_demands[entity] = (formation.hub, 0, batteries * INTERCEPT_POWER_MU)
                state.frame.work_context[entity] = (formation.owner, formation.hub, 'treasury')

    def _target_owner(self, shot):
        return shot.target_owner

    # ---- commands ----
    def strike_prepare(self, owner, order, ctx):
        fields(order, {'origin_hub': str, 'target_id': str, 'missile_qty': int})
        state = self.s
        if 'N10' not in state.research[owner].known:
            raise RuleViolation('ERR_TECH_LOCKED')
        quantity = order['missile_qty']
        if type(quantity) is not int or not 1 <= quantity <= 20:
            raise RuleViolation('ERR_SCHEMA')
        hub = order['origin_hub']
        if not state.authority.controls_hub(owner, hub):
            raise RuleViolation('ERR_NO_PERMISSION')
        target, target_owner = self.resolve_target(owner, order['target_id'], hub)
        state.initial.diplomacy.validate_kinetic(owner, target_owner)
        sid = 'strike:' + digest(owner, str(int(order['id']))).hex()[:24]
        shot = HypersonicShot(sid, str(int(order['id'])), owner, hub, target, order['target_id'],
                              target_owner, quantity, ctx.tick, ctx.tick + 1)
        stock = state.initial.inventory.prepare_transfers(
            Transfer(Account(owner, hub, 'available', good), shot.lock(good), amount * quantity)
            for good, amount in MISSILE.items())

        def commit():
            stock.commit()
            self.shots[sid] = shot
        return PreparedCommand(commit)

    def resolve_target(self, owner, target_id, origin_hub):
        """A strike target must be an actually visible foreign facility/line."""
        state = self.s
        asset = state.initial.assets.get(target_id)
        if asset is None or asset.owner == owner:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        region = state.initial.map.hubs[asset.hub].region
        origin_region = state.initial.map.hubs[origin_hub].region
        if state.sensors.distances(origin_region).get(region, 99) > 8:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET', 'target beyond the 8-hop hypersonic range')
        hub = state.initial.map.hubs[asset.hub]
        from .military_sensors import Signal
        from .world import SIZE
        quality, _ = state.sensors.quality(owner, Signal(target_id, asset.owner, 'Land', region, hub.y * SIZE + hub.x, 1000))
        if quality < Q(1, 2):
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        controller = state.authority.regions[region].controller
        return target_id, controller if controller is not None else asset.owner

    def blockade_prepare(self, owner, order, ctx):
        fields(order, {'formation_id': str, 'edge_id': str, 'target_nations': list})
        state = self.s
        formation = self.formation(owner, order['formation_id'])
        edge = state.cargo.corridors.get(order['edge_id'])
        targets = order['target_nations']
        if edge is None:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if not 1 <= len(targets) <= 8 or len(set(targets)) != len(targets) \
                or any(type(t) is not str for t in targets):
            raise RuleViolation('ERR_SCHEMA')
        for target in targets:
            if target not in state.initial.known or target == owner:
                raise RuleViolation('ERR_NO_VISIBLE_TARGET')
            state.initial.diplomacy.validate_kinetic(owner, target)
        if formation.domain == 'Air':
            raise RuleViolation('ERR_SITE_INCOMPATIBLE', 'air formations escort or interdict, they do not blockade')
        endpoints = (edge.a, edge.b)
        if formation.domain == 'Land':
            if formation.hub not in endpoints:
                raise RuleViolation('ERR_SITE_INCOMPATIBLE')
        else:
            regions = {state.initial.map.hubs[h].region for h in endpoints}
            if formation.region not in regions and not any(
                    formation.region in state.initial.map.regions[r].adjacent for r in regions):
                raise RuleViolation('ERR_SITE_INCOMPATIBLE')
        existing = next((b for b in self.blockades.values() if b.formation == formation.id), None)
        bid = 'blockade:' + digest(owner, formation.id).hex()[:24]
        row = Blockade(bid, str(int(order['id'])), owner, formation.id, edge.id, tuple(sorted(targets)), ctx.tick)

        def commit():
            if existing is not None:
                state.command_streams[owner].plans.pop(existing.command_id, None)
                del self.blockades[existing.id]
            self.blockades[bid] = row
        return PreparedCommand(commit, persistent=True)

    def _shipment_locus(self, shipment):
        state = self.s
        corridors = state.cargo.corridors
        hubs = state.initial.map.hubs
        if shipment.mode == 'COMPLETED':
            return None
        if 0 <= shipment.index < len(shipment.path):
            eid = shipment.path[shipment.index]
            edge = corridors.get(eid)
            if edge is None:
                return None
            return eid, (hubs[edge.a], hubs[edge.b])
        if not shipment.path:
            return None
        eid = shipment.path[0 if shipment.reversed else -1]
        hub = hubs[shipment.unload_hub]
        return eid, (hub, hub)

    def _shipment_quality(self, observer, shipment, locus, frame=None):
        from .military_sensors import Signal
        from .world import SIZE
        state = self.s
        eid, endpoints = locus
        vehicle = state.cargo.vehicles.get(shipment.vehicle_id)
        if vehicle is None:
            return Q(0)
        edge = state.cargo.corridors.get(eid)
        domain = ('Air' if vehicle.good in ('transport_aircraft', 'air_drone')
                  else 'Sea-Surface' if vehicle.good == 'freighter' and edge is not None and edge.mode == 'sea'
                  else 'Land')
        seen = {}
        for hub in endpoints:
            seen[hub.id] = hub
        best = Q(0)
        for hub in seen.values():
            q, _ = state.sensors.quality(
                observer,
                Signal(shipment.id, shipment.owner, domain, hub.region, hub.y * SIZE + hub.x, max(1, shipment.quantity_mU)),
                frame=frame,
            )
            if q > best:
                best = q
        return best

    def _formation_range(self, formation):
        state = self.s
        return max((state.catalog.units[state.initial.units[u].unit_type]['range_hops']
                    for u in formation.unit_ids if u in state.initial.units), default=0)

    def publish(self, ctx=None):
        del ctx
        state = self.s
        frame = state.sensors.frame()
        active = {sid: s for sid, s in state.cargo.transport.shipments.items() if s.mode != 'COMPLETED'}
        ordered = sorted(active.values(), key=lambda s: (s.departure_tick, s.id))
        for observer in sorted(state.initial.known):
            aliases = self.track_aliases[observer]
            reverse = self.alias_tracks[observer]
            for sid in tuple(aliases):
                if sid not in active:
                    alias = aliases.pop(sid)
                    reverse.pop(alias, None)
            rows = []
            for shipment in ordered:
                if shipment.owner == observer:
                    continue
                locus = self._shipment_locus(shipment)
                if locus is None:
                    continue
                q = self._shipment_quality(observer, shipment, locus, frame=frame)
                if q < Q(1, 5):
                    continue
                alias = aliases.get(shipment.id)
                if alias is None:
                    self.contact_seq[observer] += 1
                    alias = f'ship-contact-{self.contact_seq[observer]}'
                    aliases[shipment.id] = alias
                    reverse[alias] = shipment.id
                eid, _ = locus
                vehicle = state.cargo.vehicles[shipment.vehicle_id]
                rows.append({
                    'contact_id': alias,
                    'edge_id': eid,
                    'carrier_kind': vehicle.good,
                    'owner': shipment.owner,
                    'quality_mU': round(1000 * q),
                })
            rows.sort(key=lambda r: r['contact_id'])
            self.visible_shipments[observer] = rows

    def interdict_prepare(self, owner, order, ctx):
        fields(order, {'formation_id': str}, {'contact_id': str, 'shipment_track_id': str})
        cid = order.get('contact_id')
        tid = order.get('shipment_track_id')
        if not cid and not tid or (cid is not None and tid is not None and cid != tid):
            raise RuleViolation('ERR_SCHEMA')
        ref = cid if cid is not None else tid
        state = self.s
        formation = self.formation(owner, order['formation_id'])
        track = self.alias_tracks[owner].get(ref)
        shipment = state.cargo.transport.shipments.get(track) if track is not None else None
        locus = self._shipment_locus(shipment) if shipment is not None else None
        if shipment is None or locus is None or shipment.owner == owner:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if self._shipment_quality(owner, shipment, locus) < Q(1, 5):
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        _, endpoints = locus
        regions = {h.region for h in endpoints}
        dists = state.sensors.distances(formation.region)
        if min(dists.get(r, 99) for r in regions) > self._formation_range(formation):
            raise RuleViolation('ERR_SITE_INCOMPATIBLE', 'shipment outside formation interdiction range')
        carrier = state.cargo.vehicles[shipment.vehicle_id].owner
        if state.initial.diplomacy.relation(owner, shipment.owner) != Relation.WAR \
                or state.initial.diplomacy.relation(owner, carrier) != Relation.WAR:
            raise RuleViolation('ERR_NOT_AT_WAR', 'neutral carrier or neutral cargo is protected')
        row = Interdiction('interdict:' + digest(owner, str(int(order['id']))).hex()[:24], owner, formation.id,
                           track, ref, ctx.tick)
        return PreparedCommand(lambda: self.interdictions.append(row))

    # ---- P4 execution ----
    def firepower(self, formation, kind):
        state = self.s
        total = 0
        for uid in formation.unit_ids:
            unit = state.initial.units[uid]
            spec = state.catalog.units[unit.unit_type]
            total += spec['firepower'].get(kind, 0) * unit.hp_mU // max(1, spec['hp'] * 1000)
        return total

    def advance(self, ctx):
        if ctx.tick <= self.last_tick:
            raise InvariantViolation('strike phase repeated or backdated')
        self.last_tick = ctx.tick
        state = self.s
        losses = self.action_cycles.setdefault(ctx.cycle, {})
        self.committed = set()
        self._launch(ctx, losses)
        self._arrivals(ctx)
        self._blockades(ctx, losses)
        self._interdictions(ctx, losses)
        self.action_cycles = {c: v for c, v in self.action_cycles.items() if c >= ctx.cycle - 19}

    def _launch(self, ctx, losses):
        state = self.s
        for sid in sorted(self.shots):
            shot = self.shots[sid]
            if shot.launched or shot.launch_tick > ctx.tick:
                continue
            state.initial.inventory.transform({shot.lock(g): q * shot.quantity for g, q in MISSILE.items()}, {},
                                              reason='hypersonic_launch', use=Use.ACTION_LOSS)
            losses[shot.owner] = losses.get(shot.owner, Q(0)) + action_loss(
                {g: q * shot.quantity for g, q in MISSILE.items()}, state.catalog.weights)
            shot.launched = True
            self._report(shot.owner, {'command_id': shot.command_id, 'status': 'LAUNCHED',
                                      'target_id': shot.target_alias, 'arrival_tick': shot.arrival_tick})

    def _arrivals(self, ctx):
        state = self.s
        self.intercepts = {}
        for sid in sorted(self.shots):
            shot = self.shots[sid]
            if not shot.launched or shot.arrival_tick > ctx.tick:
                continue
            surviving = shot.quantity
            for formation in sorted(state.formations.rows.values(), key=lambda f: f.id):
                if formation.owner != shot.target_owner or formation.refitting or not surviving:
                    continue
                batteries = sum(state.initial.units[u].unit_type == 'energy_weapon' for u in formation.unit_ids)
                entity = f'intercept:{formation.id}'
                demand = self.power_demands.get(entity, (None, 0, 0))[2]
                if not batteries or not demand:
                    continue
                received = min(demand, state.frame.power_received.get(entity, 0))
                ratio = Q(received, demand)
                for index in range(min(batteries, surviving)):
                    if uniform(ctx.seed, 'g11_intercept', ctx.tick, f'{sid}:{formation.id}:{index}') < .6 * float(ratio):
                        surviving -= 1
                        self.intercepts[formation.id] = self.intercepts.get(formation.id, 0) + 1
            damaged = []
            asset = state.initial.assets.get(shot.target)
            if surviving and asset is not None and not asset.under_construction:
                remaining = surviving
                lines = [shot.target] + sorted(lid for lid, line in state.initial.lines.items()
                                               if lid.rsplit('/line', 1)[0] == shot.target)
                for aid in lines:
                    if not remaining or aid not in state.initial.assets:
                        continue
                    row = state.initial.assets[aid]
                    if not row.health_mU:
                        continue
                    facility = state.initial.facilities.get(aid.rsplit('/line', 1)[0])
                    basic = facility is not None and facility.profile in ('S1', 'S2', 'S3')
                    amount = row.health_mU if basic and aid in state.initial.lines else NONBASIC_DAMAGE_MU
                    # The AssetLoss event alone carries the victim's Loss_destroy.
                    state.asset_lifecycle.damage(aid, min(row.health_mU, amount), event_id=f'hypersonic:{sid}:{aid}')
                    damaged.append(aid)
                    remaining -= 1
            self._report(shot.owner, {'command_id': shot.command_id, 'status': 'ARRIVED',
                                      'target_id': shot.target_alias})
            del self.shots[sid]

    def _supplies(self, formation):
        """Per-tick combat consumables the whole formation needs (military.json)."""
        state = self.s
        need = defaultdict(int)
        for uid in formation.unit_ids:
            spec = state.catalog.units[state.initial.units[uid].unit_type]
            for good, amount in spec['combat_tick'].items():
                if amount:
                    need[good] += ceil(Q(amount) * 1000)
        return dict(need)

    def _draw_supplies(self, formation, need, reason):
        """Draw combat consumables from onboard stock first, then an online base Hub only."""
        if not need:
            return True
        from .nonland_navigation import base_online
        state = self.s
        inv = state.initial.inventory
        has_base = base_online(state, formation.owner, formation.hub, formation.domain)
        draws = {}
        for g, q in need.items():
            onboard = Account(formation.owner, formation.id, 'available', g)
            take_onboard = min(q, inv.balance(onboard))
            rem = q - take_onboard
            if take_onboard:
                draws[onboard] = take_onboard
            if rem:
                if not has_base:
                    return False
                hub_acc = Account(formation.owner, formation.hub, 'available', g)
                if inv.balance(hub_acc) < rem:
                    return False
                draws[hub_acc] = rem
        if draws:
            inv.transform(draws, {}, reason=reason, use=Use.ACTION_LOSS)
        return True

    def _blockades(self, ctx, losses):
        state = self.s
        attack = defaultdict(int)
        touched = set(self.blocked_edges)
        for bid in sorted(self.blockades):
            row = self.blockades[bid]
            formation = state.formations.rows.get(row.formation)
            edge = state.cargo.corridors.get(row.edge)
            if formation is None or edge is None or formation.owner != row.owner:
                if edge is not None:
                    touched.add(edge.id)
                state.command_streams[row.owner].plans.pop(row.command_id, None)
                del self.blockades[bid]
                continue
            touched.add(edge.id)
            live = [t for t in row.targets if state.initial.diplomacy.relation(row.owner, t) == Relation.WAR]
            if not live or formation.refitting or formation.hub is None and formation.domain == 'Land':
                continue
            need = self._supplies(formation)
            if not self._draw_supplies(formation, need, 'blockade_operations'):
                continue  # not actually supplied this tick: no effect, no action event
            losses[row.owner] = losses.get(row.owner, Q(0)) + action_loss(need, state.catalog.weights)
            self.committed.add(formation.id)
            power = self.firepower(formation, 'sea' if edge.mode in ('sea', 'river') else 'ground')
            for target in live:
                attack[edge.id, target] += power
        for eid in sorted(touched | {e for e, _ in attack}):
            edge = state.cargo.corridors[eid]
            regions = {state.initial.map.hubs[h].region for h in (edge.a, edge.b)}
            kind = 'sea' if edge.mode in ('sea', 'river') else 'ground'
            for target in sorted(set(edge.blocked) | {t for e, t in attack if e == eid}):
                a = attack.get((eid, target), 0)
                if not a:
                    edge.blocked.pop(target, None)
                    continue
                defense = sum(self.firepower(f, kind) for f in state.formations.rows.values()
                              if f.owner == target and f.task == 'escort' and not f.refitting and f.region in regions)
                edge.blocked[target] = blockade_fraction(Q(a), Q(defense))
        self.blocked_edges = {eid for eid, edge in state.cargo.corridors.items() if edge.blocked}

    def _interdictions(self, ctx, losses):
        state = self.s
        frame = state.sensors.frame() if self.interdictions else None
        for row in sorted(self.interdictions, key=lambda r: r.id):
            formation = state.formations.rows.get(row.formation)
            shipment = state.cargo.transport.shipments.get(row.shipment)
            locus = self._shipment_locus(shipment) if shipment is not None else None
            if formation is None or shipment is None or locus is None or formation.id in self.committed:
                continue
            if self._shipment_quality(row.owner, shipment, locus, frame=frame) < Q(1, 5):
                continue
            _, endpoints = locus
            regions = {h.region for h in endpoints}
            dists = state.sensors.distances(formation.region)
            if min(dists.get(r, 99) for r in regions) > self._formation_range(formation):
                continue
            carrier = state.cargo.vehicles[shipment.vehicle_id]
            if state.initial.diplomacy.relation(row.owner, shipment.owner) != Relation.WAR \
                    or state.initial.diplomacy.relation(row.owner, carrier.owner) != Relation.WAR:
                continue
            channel = ('sea' if carrier.good == 'freighter'
                       else 'aa' if carrier.good in ('transport_aircraft', 'air_drone')
                       else 'ground')
            attack = self.firepower(formation, channel)
            escort = sum(self.firepower(f, channel)
                         for f in state.formations.rows.values()
                         if f.owner == shipment.owner and f.task == 'escort' and not f.refitting
                         and f.region in regions)
            share = min(Q(1, 4), Q(attack, 100 + escort)) if attack else Q(0)
            need = self._supplies(formation)
            if not self._draw_supplies(formation, need, 'interdiction'):
                continue
            losses[row.owner] = losses.get(row.owner, Q(0)) + action_loss(need, state.catalog.weights)
            self.committed.add(formation.id)
            if not share:
                continue
            quantum = shipment.cargo_quantum_mU
            lost = floor(shipment.quantity_mU * share)
            lost -= lost % quantum
            if lost:
                state.cargo.transport.inventory.transform(
                    {Account(shipment.owner, shipment.id, 'in_transit', shipment.good): lost}, {},
                    reason='interdiction_cargo_loss', use=Use.DESTRUCTION)
                shipment.quantity_mU -= lost
            carrier.health_mU = max(0, carrier.health_mU - ceil(1000 * share))
            self._report(row.owner, {'op': 'interdict', 'contact_id': row.alias, 'damage_share_mU': ceil(share * 1000)})
        self.interdictions.clear()

    def region_ratios(self):
        """Blockade[r] per 03 §7.6: rated-capacity weighted entry blocking on targeted enemy regions only."""
        state = self.s
        totals = defaultdict(lambda: [0, 0])
        for eid, edge in state.cargo.corridors.items():
            for hub in (edge.a, edge.b):
                region = state.initial.map.hubs[hub].region
                controller = state.authority.regions[region].controller
                row = totals[region]
                row[0] += edge.capacity_mU_cycle
                ratio = edge.blocked.get(controller)
                if ratio:
                    row[1] += ceil(edge.capacity_mU_cycle * ratio)
        result = {}
        for region, (capacity, blocked) in totals.items():
            if capacity and blocked:
                result[region] = min(Q(1), Q(blocked, capacity))
        return result

    def cancel(self, owner, command_id):
        row = next((b for b in self.blockades.values() if b.owner == owner and b.command_id == command_id), None)
        if row is None:
            return None

        def commit():
            del self.blockades[row.id]
            # The effect lapses next tick; P4 recomputes the edge without it.
        return PreparedCommand(commit)

    def visible(self, owner):
        return {'blockades': [{'id': b.id, 'formation_id': b.formation, 'edge_id': b.edge,
                               'target_nations': list(b.targets)} for b in self.blockades.values() if b.owner == owner],
                'shipment_contacts': list(self.visible_shipments[owner]),
                'recent': list(self.recent[owner])}

    def audit(self):
        for shot in self.shots.values():
            if shot.quantity <= 0 or shot.arrival_tick <= shot.launch_tick:
                raise InvariantViolation('invalid hypersonic shot')
        for row in self.blockades.values():
            if row.formation in self.s.formations.rows and self.s.formations.rows[row.formation].owner != row.owner:
                raise InvariantViolation('blockade formation changed owner')
        for owner in self.s.initial.known:
            forward = self.track_aliases[owner]
            reverse = self.alias_tracks[owner]
            if len(forward) != len(reverse) or any(reverse.get(a) != s for s, a in forward.items()):
                raise InvariantViolation('inconsistent strike shipment contact aliases')

    def snapshot(self):
        return {'shots': self.shots, 'blockades': self.blockades, 'interdictions': self.interdictions,
                'action_cycles': {str(c): v for c, v in sorted(self.action_cycles.items())},
                'intercepts': self.intercepts, 'blocked_edges': self.blocked_edges, 'power_demands': self.power_demands,
                'contact_seq': dict(self.contact_seq),
                'track_aliases': {p: sorted(v.items()) for p, v in sorted(self.track_aliases.items())},
                'visible_shipments': self.visible_shipments,
                'recent': self.recent, 'last_tick': self.last_tick}


class StrikeCommands:
    OPERATIONS = frozenset({('military', 'hypersonic_strike'), ('military', 'blockade'), ('military', 'interdict')})

    def __init__(self, state):
        self.state = state

    def prepare(self, owner, order, ctx):
        book = self.state.strikes
        op = order['op']
        if op == 'hypersonic_strike':
            return book.strike_prepare(owner, order, ctx)
        if op == 'blockade':
            return book.blockade_prepare(owner, order, ctx)
        return book.interdict_prepare(owner, order, ctx)
