"""S03 infiltration / tech theft, E84 model theft and S08 paralysis.

Every operation debits a real cyber suite (and, for S08, a real EW module)
from a controlled stocked Hub, records the catalogue value as the attacker's
``Loss_action`` and takes effect only on systems the victim actually operates.
Attribution is a single deterministic draw; the engine never leaks the true
outcome of a failed attempt through a different report shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .command_schema import fields
from .determinism import digest, uniform
from .errors import InvariantViolation, RuleViolation
from .ledger import Account
from .numeric import Q, ceil
from .protocol import PreparedCommand
from .provenance import Use
from .research import Project
from .scoring import action_loss

# 03 §S03: one suite per infiltration, two suites plus an EW module for S08.
S03_COST = {'cyber_suite': 1000}
S08_COST = {'cyber_suite': 2000, 'ew_module': 1000}
S03_COMPUTE_MU = 10000
S08_COMPUTE_MU = 20000
S03_COOLDOWN_CYCLES = 10
S08_COOLDOWN_CYCLES = 40
FINANCE_COOLDOWN_CYCLES = 40
S03_KINDS = ('S03_infiltrate', 'S03_steal_tech', 'S08_paralyze')
# 03 §S03 rollback targets: the engine implements the systems it actually has.
ROLLBACK_TECHS = ('C06', 'C10', 'E07', 'L07', 'L11')
SYSTEM_BY_TECH = {'E07': 'smart_grid', 'L07': 'automated_terminal'}


@dataclass
class Rollback:
    """A manual-fallback window forced on one victim system family."""
    owner: str
    tech: str
    hub: str
    until_window: int


class CyberOperations:
    """State for S03/E84/S08; one action budget entry per executed operation."""

    def __init__(self, state):
        self.s = state
        self.rollbacks: dict[tuple[str, str, str], Rollback] = {}
        self.cooldown: dict[tuple[str, str], int] = {}       # (attacker, target hub) -> cycle
        self.finance_cooldown: dict[tuple[str, str], int] = {}
        self.s08_cooldown: dict[str, int] = {}
        self.auction_freeze: dict[str, int] = {}             # victim -> cycle whose auction is skipped
        self.plan_freeze: dict[str, int] = {}                # victim -> tick until which C10 plans are frozen
        self.action_cycles: dict[int, dict[str, Q]] = {}
        self.reports = {p: [] for p in state.initial.known}
        self.last_cycle = -1

    # ---- helpers ----
    def _report(self, owner, row):
        self.reports[owner] = (self.reports[owner] + [row])[-64:]

    def begin_tick(self, ctx):
        if ctx.cycle == self.last_cycle:
            return
        self.last_cycle = ctx.cycle
        self.action_cycles = {c: v for c, v in self.action_cycles.items() if c >= ctx.cycle - 19}
        self.rollbacks = {k: r for k, r in self.rollbacks.items() if ctx.window < r.until_window}

    def rolled_back(self, owner, tech, hub=None):
        """True while the victim's named system family is forced to manual."""
        return any(r.owner == owner and r.tech == tech and (hub is None or r.hub == hub)
                   for r in self.rollbacks.values())

    def auction_suspended(self, owner, cycle):
        return self.auction_freeze.get(owner, -1) == cycle

    def plans_frozen(self, owner, tick):
        return tick < self.plan_freeze.get(owner, -1)

    def ladder(self, owner):
        """L: highest known S-tier plus the nation's effective AI cyber strength."""
        state = self.s
        known = state.research[owner].known
        tiers = [state.catalog.technologies[t]['tier'] for t in known
                 if t.startswith('S') and t in state.catalog.technologies]
        strength = Q(0)
        deployment = getattr(state, 'ai_deployment', None)
        if deployment is not None and 'AI09' in known:
            hubs = {d.hub for d in state.compute.deployments.values() if d.owner == owner}
            strength = max((deployment.previous_strength(owner, hub, 'cyber', S03_COMPUTE_MU)
                            for hub in sorted(hubs)), default=Q(0))
        return Q(max(tiers, default=0)) + strength

    def success_probability(self, attacker, target):
        defended = 'S04' in self.s.research[target].known
        value = Q(6, 10) + Q(8, 100) * (self.ladder(attacker) - self.ladder(target)) - (Q(2, 5) if defended else 0)
        return min(Q(9, 10), max(Q(5, 100), value))

    def _source(self, owner, costs, ctx, command_id):
        """One controlled Hub that actually holds the whole kit."""
        balances = self.s.initial.inventory.balances()
        hubs = sorted({a.location for a in balances
                       if a.owner == owner and a.bucket == 'available' and a.asset in costs})
        candidates = [h for h in hubs if self.s.authority.controls_hub(owner, h)
                      and all(balances.get(Account(owner, h, 'available', g), 0) >= q for g, q in costs.items())]
        if not candidates:
            raise RuleViolation('ERR_INSUFFICIENT_STOCK')
        return min(candidates, key=lambda h: digest(ctx.seed, 'cyber_source', ctx.tick, owner, command_id, h))

    # ---- commands ----
    def cyber_prepare(self, owner, order, ctx):
        fields(order, {'target_nation': str, 'target_hub': str, 'op_kind': str})
        state = self.s
        target, hub, kind = order['target_nation'], order['target_hub'], order['op_kind']
        if kind not in S03_KINDS:
            raise RuleViolation('ERR_SCHEMA', 'cyber operation not defined in 06')
        if target == owner or target not in state.initial.known:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if hub not in state.initial.map.hubs:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        paralyse = kind == 'S08_paralyze'
        technology = 'S08' if paralyse else 'S03'
        if technology not in state.research[owner].known:
            raise RuleViolation('ERR_TECH_LOCKED')
        if paralyse:
            if ctx.cycle < self.s08_cooldown.get(owner, -S08_COOLDOWN_CYCLES) + S08_COOLDOWN_CYCLES:
                raise RuleViolation('ERR_CAPACITY_EXCEEDED', 'S08 is limited to one operation per 40 cycles')
        elif ctx.cycle < self.cooldown.get((owner, hub), -S03_COOLDOWN_CYCLES) + S03_COOLDOWN_CYCLES:
            raise RuleViolation('ERR_CAPACITY_EXCEEDED', 'S03 cooldown per originator and target Hub')
        costs = S08_COST if paralyse else S03_COST
        source_hub = self._source(owner, costs, ctx, str(int(order['id'])))
        stock = state.initial.inventory.prepare_transform(
            {Account(owner, source_hub, 'available', g): q for g, q in costs.items()}, {},
            reason='cyber_operation', use=Use.ACTION_LOSS)
        command_id = str(int(order['id']))

        def commit():
            stock.commit()
            losses = self.action_cycles.setdefault(ctx.cycle, {})
            losses[owner] = losses.get(owner, Q(0)) + action_loss(costs, state.catalog.weights)
            if paralyse:
                self.s08_cooldown[owner] = ctx.cycle
                self._paralyze(owner, target, hub, ctx, command_id)
            else:
                self.cooldown[(owner, hub)] = ctx.cycle
                self._infiltrate(owner, target, hub, kind, ctx, command_id)
        return PreparedCommand(commit)

    def _attribute(self, owner, target, ctx, command_id, penalty):
        """03: 25% attribution (65% against S04); Credit is the only signal."""
        rate = Q(65, 100) if 'S04' in self.s.research[target].known else Q(1, 4)
        if penalty is None:
            return
        if Q(uniform(ctx.seed, 'cyber_attribution', ctx.tick, f'{owner}:{target}:{command_id}')).limit_denominator(10**6) < rate:
            self.s.credit[owner] = max(Q(1, 10), self.s.credit[owner] - penalty)
            self._report(target, {'cycle': ctx.cycle, 'event': 'attributed_cyber_operation', 'actor': owner})

    def _infiltrate(self, owner, target, hub, kind, ctx, command_id):
        state = self.s
        probability = self.success_probability(owner, target)
        draw = Q(uniform(ctx.seed, 'cyber_success', ctx.tick, f'{owner}:{target}:{hub}:{command_id}')).limit_denominator(10**6)
        success = draw < probability
        if success and kind == 'S03_steal_tech':
            self._steal_tech(owner, target, hub)
        elif success:
            self._rollback(owner, target, hub, ctx, probability)
        self._attribute(owner, target, ctx, command_id, Q(5, 100))
        # The originator's receipt never states the victim's true internal state.
        self._report(owner, {'command_id': command_id, 'op': kind, 'status': 'EXECUTED'})

    def _rollback(self, owner, target, hub, ctx, probability):
        """Force manual fallback for ceil(5p) windows on one real victim system."""
        state = self.s
        windows = ceil(5 * probability)
        for tech in ROLLBACK_TECHS:
            if tech not in state.research[target].known:
                continue
            kind = SYSTEM_BY_TECH.get(tech)
            if kind is not None and not state.systems.online(hub, kind, owner=target):
                continue
            key = (target, tech, hub)
            row = self.rollbacks.get(key)
            # Refresh, never stack.
            self.rollbacks[key] = Rollback(target, tech, hub, max(ctx.window + windows,
                                                                  row.until_window if row else 0))
            break
        # C33: the victim's next securities auction is suspended once.
        if {'F03', 'F06'} & state.research[target].known and \
                ctx.cycle >= self.finance_cooldown.get((owner, target), -FINANCE_COOLDOWN_CYCLES) + FINANCE_COOLDOWN_CYCLES:
            self.finance_cooldown[(owner, target)] = ctx.cycle
            self.auction_freeze[target] = ctx.cycle

    def _steal_tech(self, owner, target, hub):
        """04: a legally observed in-progress project becomes 20% cheaper once."""
        state = self.s
        victim, thief = state.research[target], state.research[owner]
        lines = state.initial.lines
        observed = sorted({tech for lid, tech in victim.focus.items()
                           if lid in lines and lines[lid].hub == hub
                           and victim.security.get(lid, 'standard') != 'classified'})
        for tech in observed:
            if tech in thief.known or not state.catalog.researchable(tech, thief.known):
                continue
            project = thief.projects.get(tech)
            if project is not None:
                if project.cost_modifiers.get('stolen'):
                    continue
                project.cost_modifiers['stolen'] = True
                project.target_mU = thief.target_cost(tech, project.cost_modifiers)
                project.theory_mU = min(project.theory_mU, project.target_mU)
            else:
                thief.projects[tech] = Project(tech, thief.target_cost(tech, {'stolen': True}),
                                               cost_modifiers={'stolen': True})
            return tech
        return None

    def _paralyze(self, owner, target, hub, ctx, command_id):
        """S08: freeze the victim's conditional plans for one window; always attributed."""
        self.plan_freeze[target] = max(self.plan_freeze.get(target, -1),
                                       ctx.tick + ctx.clock.decision_ticks)
        self.s.credit[owner] = max(Q(1, 10), self.s.credit[owner] - Q(1, 10))
        self.s.city_economy.record_coercion(owner, ctx.cycle)
        self._report(target, {'cycle': ctx.cycle, 'event': 'attributed_cyber_operation', 'actor': owner})
        self._report(owner, {'command_id': command_id, 'op': 'S08_paralyze', 'status': 'EXECUTED'})

    def steal_model_prepare(self, owner, order, ctx):
        fields(order, {'target_nation': str, 'target_hub': str})
        state = self.s
        target, hub = order['target_nation'], order['target_hub']
        if target == owner or target not in state.initial.known or hub not in state.initial.map.hubs:
            raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if 'S03' not in state.research[owner].known:
            raise RuleViolation('ERR_TECH_LOCKED')
        if ctx.cycle < self.cooldown.get((owner, hub), -S03_COOLDOWN_CYCLES) + S03_COOLDOWN_CYCLES:
            raise RuleViolation('ERR_CAPACITY_EXCEEDED', 'S03 cooldown per originator and target Hub')
        source_hub = self._source(owner, S03_COST, ctx, str(int(order['id'])))
        stock = state.initial.inventory.prepare_transform(
            {Account(owner, source_hub, 'available', g): q for g, q in S03_COST.items()}, {},
            reason='cyber_model_theft', use=Use.ACTION_LOSS)
        command_id = str(int(order['id']))

        def commit():
            stock.commit()
            losses = self.action_cycles.setdefault(ctx.cycle, {})
            losses[owner] = losses.get(owner, Q(0)) + action_loss(S03_COST, state.catalog.weights)
            self.cooldown[(owner, hub)] = ctx.cycle
            probability = self.success_probability(owner, target)
            draw = Q(uniform(ctx.seed, 'model_theft', ctx.tick, f'{owner}:{target}:{hub}:{command_id}')).limit_denominator(10**6)
            if draw < probability:
                self._copy_model(owner, target, hub, ctx)
            self._attribute(owner, target, ctx, command_id, Q(5, 100))
            self._report(owner, {'command_id': command_id, 'op': 'steal_model', 'status': 'EXECUTED'})
        return PreparedCommand(commit)

    def _copy_model(self, owner, target, hub, ctx):
        """E84: gamma = 0.70 copy of the best model hosted at that datacenter."""
        state = self.s
        ai = state.ai
        if not any(row.owner == target and row.center.hub == hub for row in state.compute.centers.values()):
            return None
        model = ai.best(target)
        if model is None or ai.model_slots_used(owner) >= 16:
            return None
        mid = 'model:' + digest('steal', owner, target, ctx.cycle).hex()[:24]
        if mid in ai.models[owner]:
            return None
        ai.models[owner][mid] = model.copy_for(mid, owner, ctx.cycle, Q(7, 10))
        return mid

    # ---- views / audit ----
    def visible(self, owner):
        return {'rollbacks': [{'technology': r.tech, 'hub_id': r.hub, 'until_window': r.until_window}
                              for r in sorted(self.rollbacks.values(), key=lambda r: (r.owner, r.tech, r.hub))
                              if r.owner == owner],
                'auction_suspended_cycle': self.auction_freeze.get(owner),
                'plan_frozen_until_tick': self.plan_freeze.get(owner),
                'reports': list(self.reports[owner])}

    def audit(self):
        for key, row in self.rollbacks.items():
            if key != (row.owner, row.tech, row.hub) or row.owner not in self.s.initial.known:
                raise InvariantViolation('invalid cyber rollback row')
        for value in self.action_cycles.values():
            if any(q < 0 for q in value.values()):
                raise InvariantViolation('negative cyber action loss')

    def snapshot(self):
        return {'rollbacks': [[*k, v.until_window] for k, v in sorted(self.rollbacks.items())],
                'cooldown': [[*k, v] for k, v in sorted(self.cooldown.items())],
                'finance_cooldown': [[*k, v] for k, v in sorted(self.finance_cooldown.items())],
                's08_cooldown': self.s08_cooldown, 'auction_freeze': self.auction_freeze,
                'plan_freeze': self.plan_freeze, 'last_cycle': self.last_cycle,
                'action_cycles': {str(c): v for c, v in sorted(self.action_cycles.items())},
                'reports': self.reports}


class CyberCommands:
    OPERATIONS = frozenset({('intelligence', 'cyber_op'), ('intelligence', 'steal_model')})

    def __init__(self, state):
        self.state = state

    def prepare(self, owner, order, ctx):
        book = self.state.cyber
        if order['op'] == 'cyber_op':
            return book.cyber_prepare(owner, order, ctx)
        return book.steal_model_prepare(owner, order, ctx)
