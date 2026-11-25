"""03 G07 combined-arms and G12 joint-command doctrine in P4 combat.

G07: a formation of a nation that knows G07, networked (its region is in the
nation's connected regions, as for national power), with a radio or sensor A
slot, in a region where the nation's active formations hold at least two unit
types, fires x1.2 and tracks every target with max(own q, 0.92 x the best q of
a friendly unit in the same region).  G12 adds x1.1 on top while the joint
command hub (the capital) is held; after it is lost or cut off the joint
doctrine freezes for 10 ticks.  Readiness never depends on whether it fires.
"""
from collections import defaultdict
from dataclasses import replace

from .numeric import Q

G07_FIRE = Q(6, 5)
G12_FIRE = Q(11, 10)
SHARED_Q = Q(92, 100)
FREEZE_TICKS = 10
SLOTS = ('radio', 'sensor')


def eligible(state, connected):
    """Formation id -> fire multiplier for formations meeting the G07 conditions."""
    owners = {p for p, r in state.research.items() if 'G07' in r.known}
    if not owners:
        return {}
    active = [f for f in state.formations.rows.values() if f.owner in owners and not f.refitting]
    types = defaultdict(set)
    for f in active:
        types[f.owner, f.region].update(state.initial.units[u].unit_type for u in f.unit_ids if u in state.initial.units)
    down = state.battles.joint_down
    result = {}
    for f in active:
        if f.slot_a not in SLOTS or len(types[f.owner, f.region]) < 2 or f.region not in connected(f.owner):
            continue
        multiplier = G07_FIRE
        last = down.get(f.owner)
        if 'G12' in state.research[f.owner].known and connected(f.owner) and (last is None or state.battles.doctrine_tick - last >= FREEZE_TICKS):
            multiplier *= G12_FIRE
        result[f.id] = multiplier
    return result


def update_joint_command(state, tick, connected):
    """Record the tick each G12 nation's joint command hub is lost (P4, once per tick)."""
    state.battles.doctrine_tick = tick
    for p, research in sorted(state.research.items()):
        if 'G12' in research.known and not connected(p):
            state.battles.joint_down[p] = tick


def share_tracks(state, contacts, formations):
    """G07 shared tracking: q = max(own, 0.92 x best friendly q in the region)."""
    best = defaultdict(dict)
    for f in state.formations.rows.values():
        for uid in f.unit_ids:
            for c in contacts.get(uid, ()):
                key = f.owner, f.region
                if c.target not in best[key] or c.quality > best[key][c.target].quality:
                    best[key][c.target] = c
    for fid in formations:
        f = state.formations.rows[fid]
        pool = best.get((f.owner, f.region), {})
        for uid in f.unit_ids:
            own = {c.target: c for c in contacts.get(uid, ())}
            merged = []
            for target in sorted(set(own) | set(pool)):
                shared = SHARED_Q * pool[target].quality if target in pool else Q(0)
                base = own.get(target) or pool[target]
                merged.append(replace(base, quality=max(own[target].quality if target in own else Q(0), shared)))
            contacts[uid] = tuple(merged)
    return contacts
