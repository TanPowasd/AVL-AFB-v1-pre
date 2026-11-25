"""03 §2 surrender, prisoners of war and peace-time repatriation; 01 cession asset hand-over.

Contract ``surrender_prisoners_v1`` (IMPLEMENTATION_CONTRACT.md, needs author
confirmation).

* Surrender: a formation whose automatic retreat finds no legal supply point
  (``retreat_status == 'blocked'``) surrenders in the same P4 when an enemy it
  is at WAR with can take it: the enemy nation with the most living HP in the
  formation's region, else the region's controller if at WAR with it.
* Surviving crew become prisoners held by the captor (non-labour: in no hub
  population, no formation, no U_pop) until the pair is no longer at WAR; then
  they return to the origin nation's capital (or its first controlled populated
  hub) at the next P1.
* Every surviving unit's equipment passes to the captor as a stored unit at the
  hub where the equipment physically is, at ``min(HP, 40% max HP)``; the book
  removed by that cut, all carried supplies/modules and repair kits are
  destroyed once and charged to the loser's ``Loss_destroy``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace

from .capital_book import proportional_loss
from .diplomacy import Relation
from .errors import InvariantViolation
from .formations import StoredUnit
from .ledger import Account, Transfer
from .numeric import Q, floor, units
from .population import SKILLS
from .provenance import Use

TROPHY_HP = Q(2, 5)


@dataclass
class PrisonerGroup:
    id: str
    captor: str
    origin: str
    skills: dict
    captured_tick: int
    formation: str

    @property
    def total_mU(self):
        return sum(self.skills.values())


class Prisoners:
    def __init__(self, state):
        self.s = state
        self.groups = {}
        self.sequence = 0
        self.events = []
        self.event_tick = -1
        self.repatriated_mU = 0
        self.captured_mU = 0
        self.pending_cessions: list[tuple[str, str, tuple[str, ...]]] = []

    def _event(self, tick, row):
        if tick != self.event_tick:
            self.event_tick, self.events = tick, []
        self.events.append(row)

    # ----------------------------------------------------------- surrender
    def captor(self, formation):
        state = self.s
        dip = state.initial.diplomacy
        strength = defaultdict(int)
        for other in state.formations.rows.values():
            if other.owner != formation.owner and other.region == formation.region and dip.relation(other.owner, formation.owner) == Relation.WAR:
                strength[other.owner] += sum(state.initial.units[u].hp_mU for u in other.unit_ids)
        living = {p: hp for p, hp in strength.items() if hp > 0}
        if living:
            return min(living, key=lambda p: (-living[p], p))
        controller = state.authority.regions[formation.region].controller
        if controller is not None and controller != formation.owner and dip.relation(controller, formation.owner) == Relation.WAR:
            return controller
        return None

    def process(self, ctx, destroyed):
        """P4 after evaluate_retreat: surrender every blocked, cornered formation."""
        state = self.s
        movement = state.movement
        for fid in sorted(state.formations.rows):
            formation = state.formations.rows.get(fid)
            plan = movement.plans.get(fid)
            if formation is None or plan is None or not plan.auto_retreat or plan.retreat_status != 'blocked':
                continue
            captor = self.captor(formation)
            if captor is None:
                continue
            self.surrender(ctx, formation, captor, destroyed)

    def surrender(self, ctx, formation, captor, destroyed):
        state = self.s
        inv = state.initial.inventory
        owner = formation.owner
        weights = state.catalog.weights
        catalog_units = state.catalog.units
        loss = Q(0)
        job = next((j for j in state.refits.jobs.values() if j.formation == formation.id), None)
        if job:
            state.refits.cancel(job)
        # Carried supplies, refit modules and any other formation-held stock: destroyed once.
        held = {a: q for a, q in inv.balances().items() if a.location == formation.id and q > 0}
        if held:
            inv.transform(held, {}, reason='military_surrender_destroyed', use=Use.DESTRUCTION)
            loss += sum((Q(q) * weights[a.asset] / 1000 for a, q in held.items()), Q(0))
        skills = dict.fromkeys(SKILLS, 0)
        transfers, stored, destroy_equipment = [], {}, {}
        for uid in formation.unit_ids:
            unit = state.initial.units[uid]
            for skill, quantity in formation.crew_skills[uid].items():
                skills[skill] = skills.get(skill, 0) + quantity
            loss += state.military_repairs.destroy_credits(uid)
            spec = catalog_units[unit.unit_type]
            accounts = {g: state.formations.equipment_account(owner, uid, g) for g in spec['installed']}
            hub = None
            for account in accounts.values():
                locations = {o.location for o in inv.materials.composition(account)}
                if len(locations) == 1:
                    hub = next(iter(locations))
                break
            new_hp = min(unit.hp_mU, floor(units(spec['hp']) * TROPHY_HP))
            if hub not in state.initial.map.hubs or unit.hp_mU <= 0 or new_hp <= 0:
                destroy_equipment.update({a: inv.balance(a) for a in accounts.values()})
                loss += sum((q * weights[g] / 1000000 for g, q in unit.book_nmU.items()), Q(0))
                continue
            removed = proportional_loss(unit.book_nmU, unit.hp_mU - new_hp, unit.hp_mU)
            book = {g: q - removed.get(g, Q(0)) for g, q in unit.book_nmU.items()}
            loss += sum((q * weights[g] / 1000000 for g, q in removed.items()), Q(0))
            stored[uid] = StoredUnit(hub, replace(unit, owner=captor, crew_mU=0, hp_mU=new_hp, book_nmU=book,
                                                  book_rounding_steps=unit.book_rounding_steps + len(removed),
                                                  morale=Q(85, 100), fatigue=Q(0)))
            transfers.extend(Transfer(account, state.formations.stored_account(captor, hub, uid, g), inv.balance(account))
                             for g, account in accounts.items())
        if destroy_equipment:
            inv.transform(destroy_equipment, {}, reason='military_surrender_destroyed', use=Use.DESTRUCTION)
        if transfers:
            inv.transfer_many(transfers)
        skills = {k: v for k, v in skills.items() if v}
        if skills:
            self.sequence += 1
            gid = f'pow-{self.sequence}'
            self.groups[gid] = PrisonerGroup(gid, captor, owner, skills, ctx.tick, formation.id)
            self.captured_mU += sum(skills.values())
        for uid in formation.unit_ids:
            del state.initial.units[uid]
        state.formations.stored_units.update(stored)
        state.vision.own_retired(owner, formation.id)
        del state.formations.rows[formation.id]
        state.movement.retire(formation, wreck=False)
        destroyed[owner] += loss
        self._event(ctx.tick, {'event': 'surrender', 'formation_id': formation.id, 'nation': owner, 'captor': captor,
                               'prisoners_mU': sum(skills.values()), 'trophy_units': sorted(stored)})

    # ------------------------------------------------------- repatriation
    def home(self, nation):
        state = self.s
        capital = f'{nation}-h0'
        if capital in state.initial.populations and state.authority.controls_hub(nation, capital):
            return capital
        for hub in sorted(state.initial.populations):
            if state.authority.controls_hub(nation, hub):
                return hub
        return None

    def release(self, ctx):
        """P1 after the accepted peace batch: war over, prisoners go home."""
        dip = self.s.initial.diplomacy
        for gid in sorted(self.groups):
            group = self.groups[gid]
            if dip.relation(group.captor, group.origin) == Relation.WAR:
                continue
            hub = self.home(group.origin)
            if hub is None:
                continue
            self.s.initial.populations[hub].receive(group.skills)
            self.repatriated_mU += group.total_mU
            del self.groups[gid]
            self._event(ctx.tick, {'event': 'repatriated', 'group': gid, 'nation': group.origin, 'hub': hub,
                                   'people_mU': group.total_mU})

    def held_by(self, owner):
        return sum(g.total_mU for g in self.groups.values() if g.captor == owner)

    def visible(self, owner):
        return {'held': [{'id': g.id, 'origin': g.origin, 'people_mU': g.total_mU, 'captured_tick': g.captured_tick}
                         for g in self.groups.values() if g.captor == owner],
                'captured': [{'id': g.id, 'captor': g.captor, 'people_mU': g.total_mU, 'captured_tick': g.captured_tick}
                             for g in self.groups.values() if g.origin == owner],
                'events': [e for e in self.events if owner in (e.get('nation'), e.get('captor'))]}

    def audit(self):
        total = 0
        for gid, group in self.groups.items():
            if gid != group.id or group.captor == group.origin or not group.skills:
                raise InvariantViolation('invalid prisoner group')
            if any(type(q) is not int or q <= 0 or s not in SKILLS for s, q in group.skills.items()):
                raise InvariantViolation('invalid prisoner skills')
            total += group.total_mU
        if total + self.repatriated_mU != self.captured_mU:
            raise InvariantViolation('prisoners not conserved')

    def snapshot(self):
        return {'groups': self.groups, 'sequence': self.sequence, 'captured_mU': self.captured_mU,
                'repatriated_mU': self.repatriated_mU, 'events': self.events, 'event_tick': self.event_tick,
                'pending_cessions': list(self.pending_cessions)}

    def apply_pending_cessions(self):
        if not self.pending_cessions:
            return
        pending = list(self.pending_cessions)
        self.pending_cessions.clear()
        for giver, recipient, candidates in pending:
            apply_cession_handoff(self.s, giver, recipient, candidates)


def apply_cession_handoff(state, giver, recipient, candidates):
    """Transfer physical operational ownership of unpledged commissioned ceded assets."""
    from types import MappingProxyType
    from .economic_ownership import EconomicOwner
    from .maintenance import MaintenanceTarget
    from .production import Production

    initial = state.initial
    authority = state.authority
    work_targets = {j.target for j in state.work.jobs.values()}

    def base_ok(aid):
        asset = initial.assets.get(aid)
        rights = authority.assets.get(aid)
        return (asset is not None and rights is not None
                and asset.owner == giver and not asset.under_construction and not asset.work_locked
                and aid not in work_targets
                and rights.operator == recipient and giver not in rights.owner_shares)

    candidate_set = {aid for aid in candidates if base_ok(aid)}
    eligible = set()
    for aid in sorted(candidate_set):
        if aid in initial.facilities:
            facility = initial.facilities[aid]
            if all(base_ok(lid) for lid in facility.line_ids if lid in initial.assets):
                eligible.add(aid)
        elif aid in initial.lines:
            parent = aid.rsplit('/line', 1)[0]
            if parent not in initial.facilities or (base_ok(parent) and all(base_ok(lid) for lid in initial.facilities[parent].line_ids if lid in initial.assets)):
                eligible.add(aid)
        elif aid in state.systems.instances:
            host = state.systems.instances[aid].host
            if host not in initial.assets or initial.assets[host].owner != giver or base_ok(host):
                eligible.add(aid)
        elif aid in state.storage.stores:
            host = state.storage.stores[aid].host
            if host not in initial.assets or initial.assets[host].owner != giver or base_ok(host):
                eligible.add(aid)
        else:
            eligible.add(aid)
    if not eligible:
        return

    # Clean up giver research focus / active validation jobs on ceded labs first.
    prod = Production(initial.map.seed, initial.inventory, state.cargo_weights)
    for jid, job in tuple(state.validation_jobs.items()):
        if job.lab_id in eligible:
            prod.discard_and_switch(job.line, job.line.recipe)
            del state.validation_jobs[jid]
    for lid in sorted(eligible):
        if lid in initial.lines:
            state.continuity.lab_lost(lid, captured=True)
            state.research[giver].focus.pop(lid, None)
            state.continuity.lost_labs.discard(lid)

    def retitle_pieces(pieces, new_title):
        rebuilt = defaultdict(Q)
        for origin, qty in pieces.items():
            title = origin.economic_owner
            if title is not None and title.nation == giver:
                updated = new_title if new_title is not None else EconomicOwner(recipient, title.account)
                rebuilt[replace(origin, economic_owner=updated)] += qty
            else:
                rebuilt[origin] += qty
        return dict(rebuilt)

    draft = initial.inventory.materials.draft()
    for aid in sorted(eligible):
        asset = initial.assets[aid]
        # Temporarily evaluate recipient's asset_title by overriding owner in place after taking old accounts.
        for good, amount in asset.book_nmU.items():
            if not amount:
                continue
            old_acc = Account(giver, asset.hub, f'capital:{aid}', good)
            new_acc = Account(recipient, asset.hub, f'capital:{aid}', good)
            pieces = draft.take(old_acc, amount / 1000)
            asset.owner = recipient
            new_title = state.asset_title(aid)
            asset.owner = giver
            draft.add(new_acc, retitle_pieces(pieces, new_title), virtual=True)
        if aid in initial.lines:
            line = initial.lines[aid]
            asset.owner = recipient
            new_title = state.asset_title(aid)
            asset.owner = giver
            for good, q in line.credit_nmU.items():
                if q:
                    pieces = draft.take(Account(giver, line.hub, f'input_credit:{aid}', good), Q(q, 1000))
                    draft.add(Account(recipient, line.hub, f'input_credit:{aid}', good), retitle_pieces(pieces, new_title), virtual=True)
            for good, q in line.output_nmU.items():
                if q and good not in line.recipe.flow_goods:
                    pieces = draft.take(Account(giver, line.hub, f'work_in_progress:{aid}', good), Q(q, 1000))
                    draft.add(Account(recipient, line.hub, f'work_in_progress:{aid}', good), retitle_pieces(pieces, new_title), virtual=True)
            for (entity, good), amount in tuple(state.input_credits.items()):
                if entity == aid and amount:
                    pieces = draft.take(Account(giver, line.hub, f'generator_credit:{aid}', good), amount)
                    draft.add(Account(recipient, line.hub, f'generator_credit:{aid}', good), retitle_pieces(pieces, new_title), virtual=True)
        if aid in state.extraction_lines:
            eline = state.extraction_lines[aid]
            new_title = EconomicOwner(recipient, 'corp:mining')
            for good, q in eline.credit_nmU.items():
                if q:
                    pieces = draft.take(Account(giver, eline.hub, f'input_credit:{aid}', good), Q(q, 1000))
                    draft.add(Account(recipient, eline.hub, f'input_credit:{aid}', good), retitle_pieces(pieces, new_title), virtual=True)
            for good, q in eline.output_nmU.items():
                if q and good not in eline.recipe.flow_goods:
                    pieces = draft.take(Account(giver, eline.hub, f'work_in_progress:{aid}', good), Q(q, 1000))
                    draft.add(Account(recipient, eline.hub, f'work_in_progress:{aid}', good), retitle_pieces(pieces, new_title), virtual=True)
    initial.inventory.transform({}, {}, reason='peace_cession_handoff', material_plan=draft.finish())

    for aid in sorted(eligible):
        asset = initial.assets[aid]
        asset.owner = recipient
        asset.version += 1
        if aid in initial.facilities:
            initial.facilities[aid] = replace(initial.facilities[aid], owner=recipient)
        if aid in initial.lines:
            initial.lines[aid].owner = recipient
        if aid in initial.mines:
            initial.mines[aid] = replace(initial.mines[aid], owner=recipient)
            if aid in state.resource_sites:
                state.resource_sites[aid] = replace(state.resource_sites[aid], owner=recipient)
        if aid in state.extraction_lines:
            state.extraction_lines[aid].owner = recipient
        if aid in state.systems.instances:
            state.systems.instances[aid].owner = recipient
        if aid in state.storage.stores:
            state.storage.stores[aid].owner = recipient
        if aid in state.compute.centers:
            state.compute.centers[aid].owner = recipient
    for aid in sorted(eligible):
        if aid in state.maintenance.targets:
            target = state.maintenance.targets[aid]
            payer = state.operating_account(aid)
            suppliers = MappingProxyType({g: state.seller(recipient, g) for g in target.materials_mU})
            state.maintenance.targets[aid] = MaintenanceTarget(aid, target.materials_mU, payer, suppliers)


def settle_cession_assets(state, giver, recipient, region_ids):
    """01: state assets pass with a signed cession; third-party and pledged shares stay."""
    regions = set(region_ids)
    authority = state.authority
    moved = []
    candidates = []
    for aid in sorted(authority.assets):
        hub = authority.asset_hubs.get(aid)
        site = state.initial.map.hubs.get(hub)
        if site is None or site.region not in regions:
            continue
        rights = authority.assets[aid]
        shares = rights.free_shares(giver)
        if shares > 0:
            authority.transfer_shares(aid, giver, recipient, shares)
            moved.append((aid, shares))
        if rights.operator == giver and giver not in rights.owner_shares:
            rights.operator = recipient
            rights.version += 1
            candidates.append(aid)
    if candidates:
        if state.frame.in_progress:
            state.prisoners.pending_cessions.append((giver, recipient, tuple(candidates)))
        else:
            apply_cession_handoff(state, giver, recipient, candidates)
    return tuple(moved)
