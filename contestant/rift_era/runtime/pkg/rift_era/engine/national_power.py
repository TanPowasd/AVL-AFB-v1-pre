"""P7 national-power collection: the five r6 pillars, trajectory and V.

Organizer-owned and read-only with respect to the game: every input is an
already-settled engine quantity (actual batches, receipts, books, rights,
populations). The collector never mints cash, never moves inventory and never
reads a live FX rate to revalue an existing book (07 §2, OR-006).

Unique ownership (07 §1):
* ``Loss_destroy`` reads only ``CycleEconomy.destroyed_value``.
* ``Loss_action`` reads each registered action source's ``action_cycles``
  table exactly once per cycle; a source cannot be registered twice and no
  destroyed value is read from an action table.
* NFA reads each financial book once; margin already sits in cash books.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from fractions import Fraction

from .active_scale import ActiveScale
from .errors import InvariantViolation
from .ledger import Account
from .numeric import Q, clip
from .provenance import ELIGIBLE, erase_loops
from .scoring import (Pillars, Trajectory, control_pillar, industrial_pillar, international_pillar,
                      population_pillar, sustainability, technology_pillar)

ORANGE = frozenset({'GOV_ALERT_STRIKE', 'GOV_CRISIS_PARALYSIS'})


from .initialization import WAREHOUSE_BASE_mU

@dataclass(frozen=True)
class CycleRow:
    """One committed scoring cycle of one nation (exact rationals)."""
    cycle: int
    population: Fraction
    industry: Fraction
    control: Fraction
    technology: Fraction
    international: Fraction
    total: Fraction
    va: Fraction
    loss_destroy: Fraction
    loss_action: Fraction
    net_transit: Fraction
    nfa_ru: Fraction


def action_sources(state):
    """The single registry of Loss_action tables. Order is fixed; one owner each."""
    rows = (('battle', state.battles, 'loss_cycles'),
            ('asat', state.asat, 'action_cycles'),
            ('strikes', state.strikes, 'action_cycles'),
            ('cyber_op', state.cyber, 'action_cycles'),
            ('climate_intervention', state.climate, 'action_cycles'),
            ('intel_op', state.deception, 'action_cycles'),
            ('export_controls', state.export_controls, 'action_cycles'),
            ('corridor_policy', state.cargo, 'action_cycles'),
            ('declare_war', state.relations, 'action_cycles'))
    extra = getattr(state, 'action_registry', ())
    rows = rows + tuple(extra)
    names = [r[0] for r in rows]
    if len(set(names)) != len(names):
        raise InvariantViolation('Loss_action source registered twice')
    return rows


def _has_comm_equipment(state, owner, hub_id):
    inv = getattr(state.initial, 'inventory', None)
    if inv is not None and inv.balance(Account(owner, hub_id, 'available', 'communication_equipment')) >= 1000:
        return True
    systems = getattr(state, 'systems', None)
    if systems is not None:
        from .public_systems import SYSTEMS
        for sys in systems.instances.values():
            if (sys.owner == owner and sys.hub == hub_id and sys.online
                    and systems.availability(sys) > 0
                    and 'communication_equipment' in SYSTEMS[sys.kind].materials):
                return True
    return False


def command_hubs(state, owner):
    """Controlled own capital or backup core hubs (comm equipment + >=1 food)."""
    world = state.initial.map
    authority = state.authority.regions
    inv = getattr(state.initial, 'inventory', None)
    has_cores = any(h.owner == owner and h.core_index is not None for h in world.hubs.values())
    result = []
    for hid, hub in sorted(world.hubs.items()):
        if hub.region not in authority or authority[hub.region].controller != owner:
            continue
        if (hub.owner == owner and hub.core_index == 0) or (not has_cores and hid == f'{owner}-h0'):
            result.append(hid)
        elif hub.owner == owner and hub.core_index is not None and hub.core_index > 0:
            if inv is not None and inv.balance(Account(owner, hid, 'available', 'food')) >= 1000 and _has_comm_equipment(state, owner, hid):
                result.append(hid)
    return tuple(result)


def _supply_edge_open(state, owner, eid, edge):
    cargo = getattr(state, 'cargo', None)
    if cargo is None:
        return edge.health_mU > 0 and edge.allowed(owner)
    if cargo.owner_capacity(owner, eid) <= 0 or not edge.allowed(owner):
        return False
    if edge.mode in ('sea', 'air') and not (cargo.terminal_online(edge.a, edge.mode) and cargo.terminal_online(edge.b, edge.mode)):
        return False
    world = state.initial.map
    authority = state.authority.regions
    for hub_id in (edge.a, edge.b):
        region = world.hubs[hub_id].region
        controller = authority[region].controller if region in authority else None
        if controller == owner:
            continue
        if controller is None or state.relations.will_be_at_war(owner, controller):
            return False
        if controller in world.city_states:
            if cargo.city_transit_right(owner, eid) is None:
                return False
        elif edge.tier(owner) < 1 and cargo.transit_contract(owner, eid) is None:
            return False
    return True


def connected_regions(state, owner):
    """Controlled regions reachable from an own capital or backup command hub over authorized undamaged supply paths."""
    world = state.initial.map
    authority = state.authority.regions
    starts = command_hubs(state, owner)
    if not starts:
        return set()
    cargo = getattr(state, 'cargo', None)
    if cargo is None:
        return {world.hubs[h].region for h in starts}
    palette = tuple(sorted(world.regions)) if getattr(world, 'region_cells', ()) else ()
    by_hub = defaultdict(list)
    for eid, edge in sorted(cargo.corridors.items()):
        if _supply_edge_open(state, owner, eid, edge):
            by_hub[edge.a].append((eid, edge.b))
            by_hub[edge.b].append((eid, edge.a))
    seen_hubs = set(starts)
    queue = list(starts)
    connected = {world.hubs[h].region for h in starts if authority[world.hubs[h].region].controller == owner}
    while queue:
        hub_id = queue.pop()
        for eid, other in by_hub.get(hub_id, ()):
            other_region = world.hubs[other].region
            if authority[other_region].controller == owner:
                connected.add(other_region)
            if palette:
                for cell in cargo.paths.get(eid, ()):
                    cell_region = palette[world.region_cells[cell]]
                    if authority[cell_region].controller == owner:
                        connected.add(cell_region)
            if other not in seen_hubs:
                seen_hubs.add(other)
                queue.append(other)
    return connected


class NationalPower:
    def __init__(self, state, *, total_cycles: int = 2880):
        if total_cycles <= 0:
            raise ValueError('scoring needs at least one cycle')
        self.s = state
        self.total_cycles = total_cycles
        self.active_scale = ActiveScale(state, power=self)
        self.last_tick = -1
        self.cycle = -1
        self.combat_ticks: dict[str, int] = defaultdict(int)
        self.blockade_ticks: dict[str, Fraction] = defaultdict(lambda: Q(0))
        self.region_extract: dict[str, Fraction] = defaultdict(lambda: Q(0))
        self.line_ticks: dict[str, list] = {}
        self.choke: dict[str, Fraction] = defaultdict(lambda: Q(0))
        self.choke_history: deque = deque(maxlen=20)
        self.supply: dict[str, list] = {}
        nations = sorted(state.initial.known)
        self.rows: dict[str, list[CycleRow]] = {p: [] for p in nations}
        self.trajectories = {p: Trajectory(total_cycles) for p in nations}
        self.shortage = {p: deque(maxlen=20) for p in nations}
        self.peg_breaks = {p: [] for p in nations}
        self.loss_by_source: dict[str, dict[str, Fraction]] = {}
        self.terminal = None

    # ---- per-tick hooks (called from the assembled systems / production) ----
    def begin_tick(self, ctx):
        if ctx.cycle != self.cycle:
            self.cycle = ctx.cycle
            self.combat_ticks = defaultdict(int)
            self.blockade_ticks = defaultdict(lambda: Q(0))
            self.region_extract = defaultdict(lambda: Q(0))
            self.line_ticks = {}
            self.choke = defaultdict(lambda: Q(0))
            self.supply = {}
        self.active_scale.begin_tick(ctx)

    def extracted(self, owner, hub, good, amount_mU, site=None):
        if amount_mU and site is not None and self.active_scale.cycle >= 0:
            self.active_scale.lines[owner, site] += Q(amount_mU, 1000)
            self.active_scale.extracted(site, owner, amount_mU)
        if amount_mU:
            region = self.s.initial.map.hubs[hub].region
            self.region_extract[region] += Q(amount_mU, 1000) * self.s.catalog.weights[good]

    def production(self, line, result, power_ratio):
        self.active_scale.production(line, result)
        if result.batch_mU:
            row = self.line_ticks.setdefault(line.id, [line.owner, line.hub, 0, Q(0)])
            row[2] += 1
            row[3] += power_ratio

    def combat(self, state, ctx):
        for region in state.battles.combat_regions:
            self.combat_ticks[region] += 1
        blockade = getattr(state, 'strikes', None)
        if blockade is not None:
            for region, ratio in blockade.region_ratios().items():
                self.blockade_ticks[region] += ratio

    def settle_tick(self, state, ctx):
        """P7 after receipts and industrial activity; commits at cycle end."""
        if ctx.tick <= self.last_tick:
            raise InvariantViolation('national power collected twice in one tick')
        self.last_tick = ctx.tick
        hubs = state.initial.map.hubs
        for event in state.initial.inventory.materials.events:
            if event.use not in ELIGIBLE or event.origin.recognized or not event.origin.path:
                continue
            value = Q(event.quantity_mU, 1000) * state.catalog.weights[event.good]
            regions = set()
            for leg in erase_loops(event.origin.path):
                for hub in (leg.origin, leg.destination):
                    if hub in hubs:
                        regions.add(hubs[hub].region)
            for region in regions:
                self.choke[region] += value
        frame = state.frame
        for formation in state.formations.rows.values():
            need = frame.military_need.get(formation.id, {})
            got = frame.military.get(formation.id, {})
            ratio = min((Q(min(got.get(g, 0), n), n) for g, n in need.items() if n), default=Q(1))
            row = self.supply.setdefault(formation.id, [0, Q(0)])
            row[0] += 1
            row[1] += ratio
        self.active_scale.advance(ctx)
        if ctx.clock.is_cycle_end(ctx.tick):
            self._commit(ctx)

    # ---- pillar inputs ----
    def connected_regions(self, owner):
        """Controlled regions reachable from an own capital over authorized links."""
        return connected_regions(self.s, owner)


    def nfa_ru(self, owner, cycle):
        """07 §2 historical-RU books, RU units (books are RU mU)."""
        s = self.s
        cash = s.initial.cash
        long_book = (s.bond_market.asset_book(owner) + s.stock_market.long_book(owner, cycle)
                     + s.contracts.loans.creditor_book(owner) + s.stock_market.finance.lender_claim_book(owner))
        debt = (s.sovereign_finance.debt_book(owner) + s.payables.debt_book(owner)
                + s.contracts.loans.debt_book(owner) + s.stock_market.finance.debt_book(owner)
                + s.stock_market.finance.short_liability_book(owner, cycle)
                + s.forward_finance.debt_book(owner))
        value = (cash.owner_book(owner) - cash.initial_owner_book.get(owner, Q(0)) + long_book - debt
                 - cash.issue_book.get(owner, Q(0)) - s.stock_market.external_equity_book(owner, cycle))
        return Q(value) / 1000

    def _population(self, owner, cycle, connected):
        s = self.s
        world = s.initial.map
        total = Q(0)
        controlled_pop = 0
        unrest_pop = 0
        food_weighted = Q(0)
        for hub, population in sorted(s.initial.populations.items()):
            if not population.total_mU:
                continue
            region = world.hubs[hub].region
            authority = s.authority.regions[region]
            if authority.controller != owner:
                continue
            controlled_pop += population.total_mU
            if population.warning in ORANGE:
                unrest_pop += population.total_mU
            sat = s.last_satisfaction.get(hub, {})
            homeland = world.regions[region].sovereign == owner
            hold = 0 if homeland else max(0, cycle - (authority.occupied_since_tick or cycle * 10) // 10)
            food = sat.get('food', Q(0))
            food_weighted += min(Q(1), food) * population.total_mU
            pollution = Q(s.pollution.get(hub, 0), s.pollution.get(hub, 0) + 100000)
            total += population_pillar(population.total_mU, population.support_mU, homeland=homeland,
                hold_cycles=hold, food=food, synthetic_share=s.consumption.synthetic_share(hub, cycle),
                consumer_goods=sat.get('consumer_goods', Q(0)), medicine=sat.get('medicine', Q(0)),
                advanced_medicine=sat.get('medical_supplies', Q(0)), water=sat.get('water', Q(0)),
                electricity=sat.get('electricity', Q(0)),
                combat=min(Q(1), Q(self.combat_ticks.get(region, 0), 10)),
                blockade=min(Q(1), self.blockade_ticks.get(region, Q(0)) / 10),
                pollution=pollution,
                remediation=clip(Q(s.governance.remediation_mU.get(region, 0), 1000), Q(0), Q(1)))
        for row in s.city_economy.population_scoring_rows(owner, cycle):
            total += row['value']
            controlled_pop += row['population_mU']
        food_ratio = food_weighted / controlled_pop if controlled_pop else Q(1)
        return total, controlled_pop, unrest_pop, food_ratio

    def _powered_slots(self, owner, ticks):
        regions = defaultdict(int)
        total = 0
        for lid, (line_owner, hub, ran, power) in self.line_ticks.items():
            if line_owner != owner or lid not in self.s.initial.lines:
                continue
            if power / ran >= Q(4, 5):
                regions[self.s.initial.map.hubs[hub].region] += 1
                total += 1
        return regions, total

    def _readiness(self, owner, connected):
        s = self.s
        total = Q(0)
        for formation in s.formations.rows.values():
            if formation.owner != owner or formation.refitting:
                continue
            ticks, supplied = self.supply.get(formation.id, (0, Q(0)))
            supply = supplied / ticks if ticks else Q(1)
            if formation.region not in connected:
                supply = Q(0)
            value = Q(0)
            for uid in formation.unit_ids:
                unit = s.initial.units[uid]
                spec = s.catalog.units[unit.unit_type]
                hp = clip(Q(unit.hp_mU, max(1, spec['hp'] * 1000)), Q(0), Q(1))
                crew = clip(Q(unit.crew_mU, max(1, round(Q(spec['crew']) * 1000))), Q(0), Q(1))
                installed = sum((Q(q) * s.catalog.weights[g] for g, q in unit.book_nmU.items()), Q(0)) / 1000000
                value += hp * crew * installed * (Q(1, 2) + Q(1, 2) * unit.morale) * (1 - Q(2, 5) * unit.fatigue)
            total += supply * value
        return total

    def _edge_endpoints(self):
        """Corridor/utility edge assets and their two endpoint hubs (07 Kinfra)."""
        s = self.s
        edges = {}
        cargo = getattr(s, 'cargo', None)
        for eid, aid in (getattr(cargo, 'road_assets', None) or {}).items():
            edge = cargo.corridors.get(eid)
            if edge is not None:
                edges[aid] = (edge.a, edge.b)
        utilities = getattr(s, 'utilities', None)
        for link in (getattr(utilities, 'links', None) or {}).values():
            for aid in (link.asset_id, *link.component_assets):
                edges[aid] = (link.a, link.b)
        return edges

    def _kinfra(self, owner, connected):
        s = self.s
        hubs = s.initial.map.hubs
        total = Q(0)
        edges = self._edge_endpoints()
        occupied = None
        for aid, asset in s.initial.assets.items():
            if asset.under_construction:
                continue
            if aid.startswith('warehouse:'):
                # r7 (decision 0014, kinfra_warehouse_occupancy_v1): the
                # warehouse book is all expansion modules (the base 300 units
                # carry none); only the module capacity actually occupied by
                # stored goods counts, so empty expansion adds nothing.
                if occupied is None:
                    occupied = s.warehouse_load()
                capacity = s.initial.warehouse_mU.get(asset.hub, 0) - WAREHOUSE_BASE_mU
                if capacity <= 0:
                    continue
                used = min(Q(capacity), max(Q(0), Q(occupied.get(asset.hub, 0)) - WAREHOUSE_BASE_mU))
                if not used:
                    continue
                weight = Q(int(asset.hub in hubs and hubs[asset.hub].region in connected)) * used / capacity
            elif aid in edges:
                # 07: an edge counts half at each end, each end only when its
                # region is connected (kinfra_edge_halves_v1).
                weight = Q(sum(h in hubs and hubs[h].region in connected for h in edges[aid]), 2)
            else:
                weight = Q(int(asset.hub in hubs and hubs[asset.hub].region in connected))
            if not weight:
                continue
            facility = s.initial.facilities.get(aid)
            if (facility is not None and facility.commissioned and facility.kind != 'housing'
                    and facility.line_ids
                    and all(s.initial.lines[lid].recipe.id == '__idle__'
                            and s.slot_use.get(lid, 'unassigned') == 'unassigned'
                            for lid in facility.line_ids)):
                weight *= Q(s.catalog.tables['scoring']['pillars']['ctrl'].get('infra_unassigned_shell_factor', Q(1, 4)))
            rights = s.authority.assets.get(aid)
            share = Q(rights.owner_shares.get(owner, 0), 1000) if rights else Q(int(asset.owner == owner))
            if not share:
                continue
            alpha = max(Q(1, 5), 1 - Q(3, 10) * asset.maintenance_debt)
            book = sum((Q(q) * s.catalog.weights[g] for g, q in asset.book_nmU.items()), Q(0)) / 1000000
            total += weight * share * alpha * book
        return total

    def _control(self, owner, connected, controlled_pop):
        s = self.s
        slots_by_region, slots = self._powered_slots(owner, s.catalog)
        choke = defaultdict(lambda: Q(0))
        for row in (*self.choke_history, dict(self.choke)):
            for region, value in row.items():
                choke[region] += value
        sigma = Q(0)
        for region in sorted(connected):
            phi = min(Q(100), choke.get(region, Q(0)) / 20)
            sigma += min(Q(5), Q(5, 100) * self.region_extract.get(region, Q(0)) + Q(1, 2) * slots_by_region.get(region, 0)
                         + Q(2, 100) * phi)
        satellites = sum(sat.owner == owner for sat in s.orbit.satellites.values())
        home_pop = sum(p.total_mU for h, p in s.initial.populations.items()
                       if s.authority.regions[s.initial.map.hubs[h].region].controller == owner)
        return control_pillar(sigma, self._kinfra(owner, connected), satellites, home_pop, slots,
                              self._readiness(owner, connected)), slots

    def _loss_action(self, cycle):
        totals = defaultdict(lambda: Q(0))
        by_source = {}
        for name, obj, attr in action_sources(self.s):
            row = getattr(obj, attr).get(cycle, {})
            by_source[name] = {p: Q(v) for p, v in row.items() if v}
            for owner, value in row.items():
                totals[owner] += Q(value)
        self.loss_by_source = by_source
        return totals

    def _commit(self, ctx):
        s = self.s
        cycle = ctx.cycle
        if cycle >= self.total_cycles:
            raise InvariantViolation('scoring cycle beyond the match clock')
        losses = self._loss_action(cycle)
        self.active_scale_values = self.active_scale.values
        for owner in sorted(self.rows):
            if self.rows[owner] and self.rows[owner][-1].cycle >= cycle:
                raise InvariantViolation('scoring cycle committed twice')
            connected = self.connected_regions(owner)
            pop, controlled_pop, unrest_pop, food_ratio = self._population(owner, cycle, connected)
            metric = s.history[owner][-1] if s.history[owner] else None
            va = s.industry_activity.penetrated_va(owner)
            destroyed = metric.destroyed_value if metric is not None else Q(0)
            transit = s.consumption.receipt_mean20(owner, 'transit')
            nfa = self.nfa_ru(owner, cycle)
            industry = industrial_pillar(va, destroyed, losses.get(owner, Q(0)), transit, nfa)
            control, _ = self._control(owner, connected, controlled_pop)
            technology = technology_pillar(s.catalog, frozenset(s.research[owner].known),
                                           self.active_scale.values.get(owner, {}),
                                           self.active_scale.licensed.get(owner, {}))
            city = s.city_economy.scoring_inputs(owner)
            international = international_pillar(s.consumption.receipt_mean20(owner, 'export'), s.credit[owner],
                                                 city['city_state_alignment'], Q(city['realized_aid_mean20']))
            pillars = Pillars(pop, industry, control, technology, international)
            total = pillars.total
            self.rows[owner].append(CycleRow(cycle, pop, industry, control, technology, international, total,
                                             va, destroyed, losses.get(owner, Q(0)), transit, nfa))
            self.trajectories[owner].commit(cycle, total)
            power = Q(metric.power_received_mU, metric.power_needed_mU) if metric and metric.power_needed_mU else Q(1)
            self.shortage[owner].append(Q(1, 2) * max(Q(0), 1 - food_ratio) + Q(1, 2) * max(Q(0), 1 - min(Q(1), power)))
            if s.macro.peg_cooldown_cycle.get(owner) == cycle + 20 and cycle not in self.peg_breaks[owner]:
                self.peg_breaks[owner].append(cycle)
            self.peg_breaks[owner] = [c for c in self.peg_breaks[owner] if c > cycle - 40]
            self._unrest = getattr(self, '_unrest', {})
            self._unrest[owner] = (unrest_pop, controlled_pop)
        self.choke_history.append(dict(self.choke))

    # ---- terminal ----
    def phi(self, owner, cycle):
        s = self.s
        debts = tuple(a.maintenance_debt for a in s.initial.assets.values()
                      if a.owner == owner and not a.under_construction)
        unrest, controlled = getattr(self, '_unrest', {}).get(owner, (0, 0))
        defaults = s.sovereign_finance.default_count.get(owner, 0)
        last40 = any(c > cycle - 40 for c in s.sovereign_finance.default_cycles.get(owner, ()))
        return sustainability(maintenance_debts=debts, shortage20=tuple(self.shortage[owner]),
                              unrest_population_mU=unrest, controlled_population_mU=controlled,
                              defaults=defaults, default_last40=last40,
                              peg_break_last40=bool(self.peg_breaks[owner]))

    def finalize(self, clock):
        """Exact V per nation. No income is issued and no debt is forgiven."""
        last = self.total_cycles - 1
        result = {}
        for owner, trajectory in sorted(self.trajectories.items()):
            if trajectory.cycles != self.total_cycles:
                raise InvariantViolation('finalizer requires every scoring cycle')
            phi = self.phi(owner, last)
            result[owner] = {'V': trajectory.value(phi), 'phi': phi, 'era_means': trajectory.era_means(),
                             'end_mean': trajectory.end_mean(), 'cycles': trajectory.cycles,
                             'last': self.rows[owner][-1]}
        self.terminal = result
        return result

    def pending_components(self):
        """Rule inputs that still have no engine mechanism; zero by necessity."""
        return tuple(sorted(self.active_scale.pending_meters()))

    def audit(self):
        for owner, rows in self.rows.items():
            if len(rows) != self.trajectories[owner].cycles:
                raise InvariantViolation('scoring rows and trajectory disagree')
            if any(b.cycle <= a.cycle for a, b in zip(rows, rows[1:])):
                raise InvariantViolation('scoring cycles out of order')

    def snapshot(self):
        return {'total_cycles': self.total_cycles, 'last_tick': self.last_tick, 'cycle': self.cycle,
                'combat_ticks': dict(self.combat_ticks), 'blockade_ticks': dict(self.blockade_ticks),
                'region_extract': dict(self.region_extract), 'line_ticks': self.line_ticks,
                'choke': dict(self.choke), 'choke_history': list(self.choke_history), 'supply': self.supply,
                'rows': self.rows, 'trajectories': {p: t.snapshot() for p, t in self.trajectories.items()},
                'shortage': {p: list(v) for p, v in self.shortage.items()}, 'peg_breaks': self.peg_breaks,
                'loss_by_source': self.loss_by_source, 'unrest': getattr(self, '_unrest', {}),
                'active_scale': self.active_scale.snapshot()}
