"""10 C18/C19: AI09 cyber deployment strengthens intelligence fusion and drones.

Both read the previous cycle's effective cyber deployment (03: "AI情报C18/无人
C19读上一周期有效部署，不读同拍未来供电").  The deployment strength is
``AIDeployment.previous_strength(owner, capital, 'cyber', 10_000)``, i.e. the
capability-weighted ``Cap * min(1, a_cyber/10)`` of the cyber compute in the
capital's communication component (a_cyber in compute units, 10 000 mU = 10).
"""
from .numeric import Q

DRONES = frozenset({'ground_drone', 'air_drone', 'swarm'})
C08_NARROW = Q(45, 100)  # 04 C08: count-estimate interval narrowed by 55 %


def cyber_strength(state, owner):
    """Cap * s_c from the previous cycle, 0 without AI09 or while suspended."""
    if 'AI09' not in state.research[owner].known:
        return Q(0)
    deployment = state.ai_deployment
    if state.ai.suspended_until[owner] > deployment.cycle:
        return Q(0)
    return deployment.previous_strength(owner, f'{owner}-h0', 'cyber', 10000)


def drone_multiplier(state, owner):
    """C19: x(1 + min(.15, .004 * Cap * min(1, a_cyber/10)))."""
    strength = cyber_strength(state, owner)
    return 1 + min(Q(15, 100), Q(4, 1000) * strength) if strength else Q(1)


def drone_fire(state, connected):
    """Unit id -> C19 fire multiplier for drone units of in-network formations."""
    result = {}
    cache = {}
    for formation in state.formations.rows.values():
        owner = formation.owner
        if owner not in state.research or formation.refitting:
            continue
        if owner not in cache:
            cache[owner] = drone_multiplier(state, owner)
        multiplier = cache[owner]
        if multiplier == 1 or formation.region not in connected(owner):
            continue
        for uid in formation.unit_ids:
            unit = state.initial.units.get(uid)
            if unit is not None and unit.unit_type in DRONES:
                result[uid] = multiplier
    return result


def fusion_factor(state, owner):
    """Width factor of foreign count estimates: C08 x.45, then C18 x(1-min(.2,.005*Cap*s_c))."""
    if 'C08' not in state.research[owner].known:
        return Q(1)
    systems = state.systems
    if not any(s.owner == owner and s.kind == 'fusion_sensor' and s.online and systems.availability(s) > 0
               for s in systems.instances.values()):
        return Q(1)
    return C08_NARROW * (1 - min(Q(1, 5), Q(5, 1000) * cyber_strength(state, owner)))
