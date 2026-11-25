"""R08 research backup hubs and C10 conditional plans (06 §research/governance).

A backup hub is only a backup while the destination Hub actually operates one
``computing_equipment`` and one ``communication_equipment``; the receipt is
re-checked every cycle, so a plundered backup stops protecting anything. A
conditional plan is evaluated once per decision window, at the window's first
P1, against the snapshot the strategy could already see.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .command_schema import fields
from .errors import InvariantViolation, RuleViolation
from .ledger import Account
from .numeric import Q
from .protocol import MAX_COMMANDS, PreparedCommand, validate_shape

BACKUP_GOODS = {'computing_equipment': 1000, 'communication_equipment': 1000}
METRICS = ('market_price', 'grid_price', 'hub_stock')
MAX_PLANS = 32
PLAN_ID_BASE = 10 ** 18
# 04 R08: a protected laboratory keeps 90% of its theory and 75% of validation.
BACKUP_THEORY = Q(9, 10)
BACKUP_VALIDATION = Q(3, 4)
# 04: without a backup, a destroyed or captured laboratory loses 70% of theory
# and all in-flight validation.
BARE_THEORY = Q(3, 10)


def is_lab(state, line):
    """A configured laboratory line (idle slots carry the '__idle__' pseudo-recipe)."""
    row = state.catalog.recipes.get(line.recipe.id)
    return row is not None and row['facility'] == '实验室'


@dataclass
class BackupHub:
    owner: str
    primary_hub: str
    backup_hub: str
    command_id: str


@dataclass
class ConditionalPlan:
    id: str
    owner: str
    command_id: str
    hub: str
    metric: str
    ref_id: str
    comparator: str
    threshold_mU: int
    action: dict
    cooldown_ticks: int
    last_fired_tick: int = -10**9
    last_fired_window: int = -1
    fired: int = 0


class Continuity:
    """R08 backups plus the C10 conditional-plan book."""

    def __init__(self, state):
        self.s = state
        self.backups: dict[tuple[str, str], BackupHub] = {}
        self.plans: dict[str, ConditionalPlan] = {}
        self.lost_labs: set[str] = set()
        self.sequence = dict.fromkeys(state.initial.known, 0)
        self.reports = {p: [] for p in state.initial.known}
        self.last_run_tick = -1

    # ---- R08 ----
    def _report(self, owner, row):
        self.reports[owner] = (self.reports[owner] + [row])[-64:]

    def backup_receipt(self, owner, hub):
        """The backup Hub must actually hold the redundant equipment now."""
        inventory = self.s.initial.inventory
        return all(inventory.balance(Account(owner, hub, 'available', g)) >= q
                   for g, q in BACKUP_GOODS.items())

    def protects(self, owner, hub):
        row = self.backups.get((owner, hub))
        return (row is not None and 'R08' in self.s.research[owner].known
                and self.backup_receipt(owner, row.backup_hub))

    def lab_lost(self, lab_id, *, captured=False):
        """04 §5: apply the real loss to a destroyed or captured laboratory."""
        state = self.s
        line = state.initial.lines.get(lab_id)
        if line is None:
            return None
        research = state.research[line.owner]
        tech = research.focus.get(lab_id)
        if tech is None:
            return None
        project = research.projects.get(tech)
        if project is None or project.verified:
            return None
        protected = self.protects(line.owner, line.hub)
        if not project.theory_complete:
            project.theory_mU = int(project.theory_mU * (BACKUP_THEORY if protected else BARE_THEORY))
        project.validation_progress_mU = int(project.validation_progress_mU * (BACKUP_VALIDATION if protected else 0))
        self._report(line.owner, {'event': 'lab_lost', 'line_id': lab_id, 'captured': bool(captured),
                                  'backup': bool(protected)})
        return protected

    def backup_prepare(self, owner, order, ctx):
        fields(order, {'primary_hub': str, 'backup_hub': str})
        state = self.s
        primary, backup = order['primary_hub'], order['backup_hub']
        if 'R08' not in state.research[owner].known:
            raise RuleViolation('ERR_TECH_LOCKED')
        if primary not in state.initial.map.hubs or backup not in state.initial.map.hubs or primary == backup:
            raise RuleViolation('ERR_SCHEMA')
        for hub in (primary, backup):
            if not state.authority.controls_hub(owner, hub):
                raise RuleViolation('ERR_NO_PERMISSION')
        if not any(line.owner == owner and line.hub == primary
                   and is_lab(state, line)
                   for line in state.initial.lines.values()):
            raise RuleViolation('ERR_SITE_INCOMPATIBLE', 'the primary Hub operates no laboratory')
        if not self.backup_receipt(owner, backup):
            raise RuleViolation('ERR_INSUFFICIENT_STOCK',
                                'the backup Hub needs computing_equipment x1 and communication_equipment x1')
        row = BackupHub(owner, primary, backup, str(int(order['id'])))
        return PreparedCommand(lambda: self.backups.__setitem__((owner, primary), row))

    # ---- C10 ----
    def plan_prepare(self, owner, order, ctx):
        fields(order, {'hub_id': str, 'metric': str, 'ref_id': str, 'comparator': str,
                       'threshold_mU': int, 'action': dict, 'cooldown_ticks': int})
        state = self.s
        hub, metric, comparator = order['hub_id'], order['metric'], order['comparator']
        if metric not in METRICS or comparator not in ('<=', '>=') or order['cooldown_ticks'] < 5 \
                or order['threshold_mU'] < 0 or not isinstance(order['action'], dict):
            raise RuleViolation('ERR_SCHEMA')
        if 'C10' not in state.research[owner].known:
            raise RuleViolation('ERR_TECH_LOCKED')
        if hub not in state.initial.map.hubs or not state.authority.controls_hub(owner, hub):
            raise RuleViolation('ERR_NO_PERMISSION')
        if not any(line.owner == owner and line.hub == hub
                   and 'ai_core' in state.line_options.get(line.id, {}).get('addons', ())
                   for line in state.initial.lines.values()):
            raise RuleViolation('ERR_SITE_INCOMPATIBLE', 'C10 requires an installed ai_core at the Hub')
        action = order['action']
        if not isinstance(action.get('kind'), str) or not isinstance(action.get('op'), str):
            raise RuleViolation('ERR_SCHEMA', 'a conditional action must be a well-formed order')
        if (action['kind'], action['op']) in (
            ('governance', 'conditional_plan'), ('cancel', 'cancel_by_id'), ('diplomacy', 'declare_war')
        ):
            raise RuleViolation('ERR_SCHEMA', 'a plan may not schedule plans, cancellations or war declarations')
        if sum(p.owner == owner for p in self.plans.values()) >= MAX_PLANS:
            raise RuleViolation('ERR_ENTITY_CAP_EXCEEDED')
        command_id = str(int(order['id']))
        pid = f'plan:{owner}:{command_id}'
        row = ConditionalPlan(pid, owner, command_id, hub, metric, order['ref_id'], comparator,
                              order['threshold_mU'], dict(action), order['cooldown_ticks'])
        return PreparedCommand(lambda: self.plans.__setitem__(pid, row), persistent=True)

    def cancel(self, owner, command_id):
        row = next((p for p in self.plans.values() if p.owner == owner and p.command_id == command_id), None)
        if row is None:
            return None
        return PreparedCommand(lambda: self.plans.pop(row.id))

    def reading(self, plan):
        """The value the strategy could already see for this plan's metric."""
        state = self.s
        if plan.metric == 'grid_price':
            price = state.grid_prices_ru.get(plan.hub)
            return None if price is None else round(1000 * price)
        if plan.metric == 'hub_stock':
            if plan.ref_id not in state.catalog.goods:
                return None
            return state.initial.inventory.balance(Account(plan.owner, plan.hub, 'available', plan.ref_id))
        venue = state.spot.venues.get(plan.ref_id)
        if venue is None:
            return None
        return state.spot.prices.get(venue.id)

    def due(self, ctx):
        """Window-first-tick P1: the orders the online, unfrozen plans emit."""
        state = self.s
        if ctx.tick % ctx.clock.decision_ticks:
            return {}
        orders = {}
        for pid in sorted(self.plans):
            plan = self.plans[pid]
            if 'C10' not in state.research[plan.owner].known:
                continue
            if state.cyber.plans_frozen(plan.owner, ctx.tick) or state.cyber.rolled_back(plan.owner, 'C10', plan.hub):
                continue
            if not state.authority.controls_hub(plan.owner, plan.hub):
                continue
            if plan.last_fired_window == ctx.window or ctx.tick - plan.last_fired_tick < plan.cooldown_ticks:
                continue
            value = self.reading(plan)
            if value is None:
                continue
            if (value <= plan.threshold_mU) if plan.comparator == '<=' else (value >= plan.threshold_mU):
                orders.setdefault(plan.owner, []).append(plan)
        return orders

    def order_for(self, plan):
        """Engine-issued ids live above 10**18 so they never meet a seat's own ids."""
        self.sequence[plan.owner] += 1
        return {**plan.action, 'id': str(PLAN_ID_BASE + self.sequence[plan.owner])}

    def executed(self, plan, ctx, order, code=None):
        plan.last_fired_window = ctx.window
        if code is None:
            plan.last_fired_tick = ctx.tick
            plan.fired += 1
        self._report(plan.owner, {'plan_id': plan.command_id, 'tick': ctx.tick, 'order_id': order['id'],
                                  'status': 'EXECUTED' if code is None else 'REJECTED',
                                  **({} if code is None else {'code': code})})

    def run(self, ctx, prepare):
        """Called inside P1 command processing, before the seats' own streams."""
        if ctx.tick == self.last_run_tick:
            return
        self.last_run_tick = ctx.tick
        state = self.s
        for owner, plans in sorted(self.due(ctx).items()):
            stream = state.command_streams[owner]
            manual_count = len(ctx.orders.get(owner, ())) if getattr(ctx, 'orders', None) is not None else 0
            used_slots = manual_count
            for plan in plans:
                order = self.order_for(plan)
                if used_slots >= MAX_COMMANDS:
                    self.executed(plan, ctx, order, 'ERR_WINDOW_ORDER_CAP')
                    continue
                try:
                    validate_shape(order)
                    replacing = order.get('replaces_id')
                    if replacing is not None and replacing not in stream.plans:
                        raise RuleViolation('ERR_UNKNOWN_TARGET_ID')
                    prepared = prepare(owner, order)
                    if prepared.persistent and replacing is None and len(stream.plans) >= 64:
                        raise RuleViolation('ERR_ENTITY_CAP_EXCEEDED')
                except RuleViolation as exc:
                    self.executed(plan, ctx, order, exc.code)
                    continue
                prepared.commit()
                if replacing is not None:
                    stream.plans.pop(replacing, None)
                if prepared.persistent:
                    stream.plans[order['id']] = dict(order)
                used_slots += 1
                self.executed(plan, ctx, order)

    def scan_labs(self, ctx):
        """P7 at cycle end: a laboratory destroyed or captured loses its project once."""
        state = self.s
        if not ctx.clock.is_cycle_end(ctx.tick):
            return
        for lid in sorted(l for l, line in state.initial.lines.items() if line.owner in state.research
                          and is_lab(state, line)):
            line = state.initial.lines[lid]
            asset = state.initial.assets.get(lid)
            shell = state.initial.assets.get(lid.rsplit('/line', 1)[0])
            captured = not state.authority.controls_hub(line.owner, line.hub)
            lost = (asset is None or asset.health_mU == 0 or captured
                    or (shell is not None and shell is not asset and shell.health_mU == 0))
            if not lost:
                self.lost_labs.discard(lid)
                continue
            if lid in self.lost_labs:
                continue
            self.lost_labs.add(lid)
            if self.lab_lost(lid, captured=captured) is not None:
                state.research[line.owner].focus.pop(lid, None)

    # ---- views / audit ----
    def visible(self, owner):
        return {'backup_hubs': [{'primary_hub': r.primary_hub, 'backup_hub': r.backup_hub,
                                 'receipt_ok': self.backup_receipt(owner, r.backup_hub)}
                                for (p, _), r in sorted(self.backups.items()) if p == owner],
                'conditional_plans': [{'id': p.command_id, 'hub_id': p.hub, 'metric': p.metric, 'ref_id': p.ref_id,
                                       'comparator': p.comparator, 'threshold_mU': p.threshold_mU,
                                       'cooldown_ticks': p.cooldown_ticks, 'fired': p.fired}
                                      for p in sorted(self.plans.values(), key=lambda r: r.id) if p.owner == owner],
                'reports': list(self.reports[owner])}

    def audit(self):
        for key, row in self.backups.items():
            if key != (row.owner, row.primary_hub) or row.primary_hub == row.backup_hub:
                raise InvariantViolation('invalid research backup hub')
        counts = {}
        for pid, plan in self.plans.items():
            if pid != plan.id or plan.cooldown_ticks < 5 or plan.metric not in METRICS:
                raise InvariantViolation('invalid conditional plan')
            counts[plan.owner] = counts.get(plan.owner, 0) + 1
        if any(n > MAX_PLANS for n in counts.values()):
            raise InvariantViolation('conditional plan cap exceeded')

    def snapshot(self):
        return {'backups': [[*k, v.backup_hub, v.command_id] for k, v in sorted(self.backups.items())],
                'plans': self.plans, 'lost_labs': self.lost_labs, 'sequence': self.sequence,
                'reports': self.reports, 'last_run_tick': self.last_run_tick}


class ContinuityCommands:
    OPERATIONS = frozenset({('research', 'set_backup_hub'), ('governance', 'conditional_plan')})

    def __init__(self, state):
        self.state = state

    def prepare(self, owner, order, ctx):
        book = self.state.continuity
        if order['op'] == 'set_backup_hub':
            return book.backup_prepare(owner, order, ctx)
        return book.plan_prepare(owner, order, ctx)
