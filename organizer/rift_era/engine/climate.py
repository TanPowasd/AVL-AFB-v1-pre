"""V08 regional climate intervention and its cross-border consent (06 §V08).

An intervention is only in effect for a cycle in which the operator actually
paid remediation_agent×2 and 10.0 of B3 electricity; an unpaid cycle simply has
no effect and is never made up later. Affecting a region the operator neither
controls nor holds a consent for is a hostile act: Credit −0.10 once, and the
cycle's inputs are the operator's Loss_action.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .command_schema import fields
from .errors import InvariantViolation, RuleViolation
from .ledger import Account
from .numeric import Q, clip
from .protocol import PreparedCommand
from .provenance import Use
from .scoring import action_loss

DURATION_CYCLES = 60
COOLDOWN_CYCLES = 120
INPUTS = {'remediation_agent': 2000}
POWER_MU_CYCLE = 10000
SHIFT = Q(15, 100)
DOWNWIND_HOPS = 3


@dataclass
class Intervention:
    id: str
    owner: str
    command_id: str
    region: str
    mode: str
    start_cycle: int
    end_cycle: int
    regions: tuple[str, ...]
    paid_cycles: int = 0
    hostile_penalized: bool = False
    effective_cycle: int = -1


@dataclass(frozen=True)
class Consent:
    grantor: str
    grantee: str
    regions: tuple[str, ...]
    start_cycle: int
    end_cycle: int


class Climate:
    """V08 state: live interventions, cooldowns and bilateral consents."""

    def __init__(self, state):
        self.s = state
        self.rows: dict[str, Intervention] = {}
        self.consents: list[Consent] = []
        self.cooldown: dict[str, int] = {}
        self.effects: dict[str, tuple[str, Q]] = {}   # region -> (mode, shift), this cycle
        self.next_effects: dict[str, tuple[str, Q]] = {}  # paid at the previous cycle end
        self.power_demands: dict[str, tuple[str, int, int]] = {}
        self.action_cycles: dict[int, dict[str, Q]] = {}
        self.reports = {p: [] for p in state.initial.known}
        self.last_cycle = -1

    # ---- helpers ----
    def _report(self, owner, row):
        self.reports[owner] = (self.reports[owner] + [row])[-64:]

    def downwind(self, region_id):
        """The target region plus three hops of map adjacency, deterministic."""
        world = self.s.initial.map
        seen, frontier = [region_id], [region_id]
        for _ in range(DOWNWIND_HOPS):
            nxt = []
            for rid in frontier:
                for other in world.regions[rid].adjacent:
                    if other not in seen:
                        seen.append(other)
                        nxt.append(other)
            frontier = nxt
        return tuple(seen)

    def consented(self, owner, region, cycle):
        """The operator controls the region, or holds a live consent for it."""
        authority = self.s.authority.regions.get(region)
        if authority is not None and authority.controller == owner:
            return True
        return any(c.grantee == owner and region in c.regions and c.start_cycle <= cycle < c.end_cycle
                   for c in self.consents)

    def active_regions(self, owner, cycle):
        """07 V08 meter: paid intervention regions whose coverage is complete."""
        total = 0
        for row in self.rows.values():
            if row.owner != owner or not row.start_cycle <= cycle < row.end_cycle \
                    or row.effective_cycle != cycle:
                continue
            if all(self.consented(owner, r, cycle) for r in row.regions):
                total += len(row.regions)
        return total

    # ---- P1: this cycle's real inputs ----
    def reserve(self, ctx):
        """At the last tick of cycle c book the B3 load that funds cycle c+1."""
        self.power_demands = {}
        if not ctx.clock.is_cycle_end(ctx.tick):
            return
        for row in sorted(self.rows.values(), key=lambda r: r.id):
            if not self.funds_next(row, ctx):
                continue
            hub = f'{row.owner}-h0'
            entity = f'climate:{row.id}'
            self.power_demands[entity] = (hub, 3, POWER_MU_CYCLE)
            self.s.frame.work_context[entity] = (row.owner, hub, 'treasury')

    def begin_tick(self, ctx):
        if ctx.cycle == self.last_cycle:
            return
        self.last_cycle = ctx.cycle
        self.action_cycles = {c: v for c, v in self.action_cycles.items() if c >= ctx.cycle - 19}
        self.consents = [c for c in self.consents if ctx.cycle < c.end_cycle]
        self.rows = {k: r for k, r in self.rows.items() if ctx.cycle < r.end_cycle}
        self.effects, self.next_effects = self.next_effects, {}

    @staticmethod
    def funds_next(row, ctx):
        return row.start_cycle <= ctx.cycle and ctx.cycle + 1 < row.end_cycle

    # ---- P4 of the cycle's last tick: pay after actual P3 power, effect next cycle ----
    def advance(self, ctx):
        state = self.s
        if not ctx.clock.is_cycle_end(ctx.tick):
            return
        self.next_effects = {}
        losses = self.action_cycles.setdefault(ctx.cycle, {})
        for row in sorted(self.rows.values(), key=lambda r: r.id):
            if not self.funds_next(row, ctx):
                continue
            hub = f'{row.owner}-h0'
            entity = f'climate:{row.id}'
            received = state.frame.power_received.get(entity, 0)
            sources = {Account(row.owner, hub, 'available', g): q for g, q in INPUTS.items()}
            if received < POWER_MU_CYCLE or any(state.initial.inventory.balance(a) < q for a, q in sources.items()):
                self._report(row.owner, {'id': row.command_id, 'cycle': ctx.cycle + 1, 'status': 'UNFUNDED'})
                continue
            hostile = any(not self.consented(row.owner, r, ctx.cycle + 1) for r in row.regions)
            # 07: only an unconsented intervention is a hostile act (Loss_action).
            state.initial.inventory.transform(sources, {}, reason='climate_intervention',
                                              use=Use.ACTION_LOSS if hostile else Use.REMEDIATION)
            if hostile:
                losses[row.owner] = losses.get(row.owner, Q(0)) + action_loss(INPUTS, state.catalog.weights)
            row.paid_cycles += 1
            row.effective_cycle = ctx.cycle + 1
            direction = 1 if row.mode == 'enhance' else -1
            for index, region in enumerate(row.regions):
                sign = direction if index == 0 else -direction
                mode, shift = self.next_effects.get(region, (row.mode, Q(0)))
                self.next_effects[region] = (mode, clip(shift + sign * SHIFT, -Q(1, 4), Q(1, 4)))
            if not row.hostile_penalized and hostile:
                row.hostile_penalized = True
                state.credit[row.owner] = max(Q(1, 10), state.credit[row.owner] - Q(1, 10))
            self._report(row.owner, {'id': row.command_id, 'cycle': ctx.cycle + 1, 'status': 'ACTIVE'})

    def factor(self, region):
        """Multiplier applied to the region's rain and wind/solar coefficients."""
        mode, shift = self.effects.get(region, (None, Q(0)))
        return 1 + shift

    # ---- commands ----
    def intervention_prepare(self, owner, order, ctx):
        fields(order, {'target_region': str, 'mode': str})
        state = self.s
        region, mode = order['target_region'], order['mode']
        if mode not in ('enhance', 'suppress'):
            raise RuleViolation('ERR_SCHEMA')
        if region not in state.initial.map.regions:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if 'V08' not in state.research[owner].known:
            raise RuleViolation('ERR_TECH_LOCKED')
        if ctx.cycle < self.cooldown.get(owner, -COOLDOWN_CYCLES) + COOLDOWN_CYCLES:
            raise RuleViolation('ERR_CAPACITY_EXCEEDED', 'V08 cooldown is 120 cycles from the previous start')
        rid = f'climate:{owner}:{int(order["id"])}'
        row = Intervention(rid, owner, str(int(order['id'])), region, mode, ctx.cycle,
                           ctx.cycle + DURATION_CYCLES, self.downwind(region))

        def commit():
            self.rows[rid] = row
            self.cooldown[owner] = ctx.cycle
        return PreparedCommand(commit)

    def consent_prepare(self, owner, order, ctx):
        fields(order, {'target_nation': str, 'region_ids': list, 'duration_cycles': int})
        state = self.s
        target, regions, duration = order['target_nation'], order['region_ids'], order['duration_cycles']
        if target == owner or target not in state.initial.known:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if not 1 <= duration <= 720 or not 1 <= len(regions) <= 64 \
                or any(type(r) is not str for r in regions) or len(set(regions)) != len(regions):
            raise RuleViolation('ERR_SCHEMA')
        for rid in regions:
            authority = state.authority.regions.get(rid)
            if authority is None:
                raise RuleViolation('ERR_NO_VISIBLE_TARGET')
            if authority.controller != owner:
                raise RuleViolation('ERR_NO_PERMISSION', 'a nation cannot consent on behalf of a third country')
        row = Consent(owner, target, tuple(sorted(regions)), ctx.cycle, ctx.cycle + duration)

        def commit():
            # A re-grant for the same pair replaces the previous one; a shorter
            # term takes effect from the next cycle, never retroactively.
            self.consents = [c for c in self.consents if (c.grantor, c.grantee) != (owner, target)]
            self.consents.append(row)
            self._report(owner, {'op': 'grant_climate_consent', 'target_nation': target,
                                 'region_ids': list(row.regions), 'end_cycle': row.end_cycle})
        return PreparedCommand(commit)

    def cancel(self, owner, command_id):
        row = next((r for r in self.rows.values() if r.owner == owner and r.command_id == command_id), None)
        if row is None:
            return None
        return PreparedCommand(lambda: self.rows.pop(row.id))

    # ---- views / audit ----
    def visible(self, owner):
        return {'interventions': [{'id': r.command_id, 'target_region': r.region, 'mode': r.mode,
                                   'end_cycle': r.end_cycle, 'paid_cycles': r.paid_cycles,
                                   'affected_regions': list(r.regions)}
                                  for r in sorted(self.rows.values(), key=lambda r: r.id) if r.owner == owner],
                'climate_consents': [{'grantor': c.grantor, 'grantee': c.grantee, 'region_ids': list(c.regions),
                                      'end_cycle': c.end_cycle} for c in self.consents
                                     if owner in (c.grantor, c.grantee)],
                'reports': list(self.reports[owner])}

    def audit(self):
        for rid, row in self.rows.items():
            if rid != row.id or row.mode not in ('enhance', 'suppress') \
                    or row.end_cycle != row.start_cycle + DURATION_CYCLES or row.paid_cycles > DURATION_CYCLES:
                raise InvariantViolation('invalid climate intervention')
        for consent in self.consents:
            if consent.end_cycle <= consent.start_cycle or consent.grantor == consent.grantee:
                raise InvariantViolation('invalid climate consent')

    def snapshot(self):
        return {'rows': self.rows, 'consents': self.consents, 'cooldown': self.cooldown,
                'effects': self.effects, 'next_effects': self.next_effects, 'power_demands': self.power_demands,
                'action_cycles': {str(c): v for c, v in sorted(self.action_cycles.items())},
                'reports': self.reports, 'last_cycle': self.last_cycle}


class ClimateCommands:
    OPERATIONS = frozenset({('governance', 'climate_intervention'), ('diplomacy', 'grant_climate_consent')})

    def __init__(self, state):
        self.state = state

    def prepare(self, owner, order, ctx):
        book = self.state.climate
        if order['op'] == 'climate_intervention':
            return book.intervention_prepare(owner, order, ctx)
        return book.consent_prepare(owner, order, ctx)
