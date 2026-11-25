# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Small stdlib-only resource-aware open-route insertion solver.

solve_routes(jobs, workers, shed, last_hour=23, time_budget_ms=45)
returns routes (indices into jobs), unassigned, pickups, finish_hours, stats.
worker.hour is the next available action hour; deadlines are inclusive.
Priority is descending. Use mandatory=True for jobs that outrank all optional
jobs. Route endpoints are free because ordinary days auto-unload inventory.
The caller must execute returned pickup quantities before following the route.
"""
from time import perf_counter
from .native_routes import make_native

SHED = ((4, 4), (5, 4), (4, 5), (5, 5))
ANIMALS = frozenset(('COW', 'SHEEP', 'GOOSE'))


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def solve_routes(jobs, workers, shed, last_hour=23, time_budget_ms=45,
                 shed_positions=SHED, track_collect=False,
                 evaluation_budget=None, hard_timeout_ms=None,
                 return_to_shed=False, end_service=1, end_depot=None):
    if end_depot is not None:
        return_to_shed = bool(end_depot)
    start_clock = perf_counter()
    # With an evaluation budget, normal stopping depends only on problem data.
    # Retain a generous wall-clock circuit breaker for pathological inputs or
    # a heavily contended host; it should never fire in ordinary <=100-job runs.
    timed_limit = (time_budget_ms if evaluation_budget is None else
                   max(200, time_budget_ms * 4))
    stop_clock = start_clock + max(0.001, (hard_timeout_ms if hard_timeout_ms is not None
                                          else timed_limit) / 1000)
    n, m = len(jobs), len(workers)
    positions = [tuple(j['pos']) for j in jobs]
    lengths = [len(j.get('ops', ())) for j in jobs]
    deadlines = [min(last_hour, j.get('deadline', last_hour)) + 1 for j in jobs]
    demands = []
    # Each job records prefix needs and net flow. This preserves a supplied
    # COLLECT_FERTILIZER before later FERTILIZE when track_collect is enabled.
    nets = []
    for job in jobs:
        net, need = {}, {}
        for op in job.get('ops', ()):
            if not op:
                continue
            item, delta = None, 0
            if op[0] == 'FEED':
                item, delta = 'WHEAT', -1
            elif op[0] == 'FERTILIZE':
                item, delta = 'FERTILIZER', -1
            elif op[0] == 'PLACE' and len(op) > 1 and op[1] in ANIMALS:
                item, delta = op[1], -1
            elif op[0] == 'COLLECT_FERTILIZER' and track_collect:
                item, delta = 'FERTILIZER', 1
            if item:
                net[item] = net.get(item, 0) + delta
                need[item] = max(need.get(item, 0), -net[item])
        # Optional explicit produced resources, e.g. guaranteed wheat harvest.
        for item, qty in job.get('supply', {}).items():
            net[item] = net.get(item, 0) + qty
        demands.append(need)
        nets.append(net)
    distance = [[_dist(a, b) for b in positions] for a in positions]
    routes = [[] for _ in workers]
    pickups = [{} for _ in workers]
    reserved = {}
    ends = [w.get('hour', 0) for w in workers]
    starts = [tuple(w['pos']) for w in workers]
    inventory = [w.get('inv', {}) for w in workers]
    hours = [w.get('hour', 0) for w in workers]
    nearest_shed = [min(shed_positions, key=lambda s: _dist(s, p)) for p in starts]
    evaluations = 0
    native = make_native(jobs, workers, shed, last_hour, shed_positions, return_to_shed, False, None, end_service, demands, nets, deadlines, return_empty=True)

    def budget_available():
        return ((evaluation_budget is None or evaluations < evaluation_budget)
                and perf_counter() < stop_clock)

    def evaluate(w, route, enforce=True):
        nonlocal evaluations
        evaluations += 1
        if native is not None:
            return native.evaluate(w, route, enforce, reserved, pickups[w], travel=True)
        flow, need = {}, {}
        for j in route:
            for item, qty in demands[j].items():
                need[item] = max(need.get(item, 0), qty - flow.get(item, 0))
            for item, qty in nets[j].items():
                flow[item] = flow.get(item, 0) + qty
        take = {item: qty - inventory[w].get(item, 0) for item, qty in need.items()
                if qty > inventory[w].get(item, 0)}
        if enforce:
            for item, qty in take.items():
                if qty + reserved.get(item, 0) - pickups[w].get(item, 0) > shed.get(item, 0):
                    return None
        pos = nearest_shed[w] if take else starts[w]
        t = hours[w] + (len(take) + _dist(starts[w], pos) if take else 0)
        travel = _dist(starts[w], pos) if take else 0
        previous = None
        for j in route:
            leg = _dist(pos, positions[j]) if previous is None else distance[previous][j]
            t += leg + lengths[j]
            travel += leg
            if t > deadlines[j]:
                return None
            previous = j
        if return_to_shed and (route or any(inventory[w].values())):
            end_pos = positions[previous] if previous is not None else pos
            leg = min(_dist(end_pos, s) for s in shed_positions)
            t += leg + end_service
            travel += leg
        if t > last_hour + 1:
            return None
        return t, take, travel

    def install(w, route, result):
        for item, qty in pickups[w].items():
            reserved[item] = reserved.get(item, 0) - qty
        routes[w] = route
        ends[w], pickups[w], _ = result
        for item, qty in pickups[w].items():
            reserved[item] = reserved.get(item, 0) + qty

    def insertion(j):
        nonlocal evaluations
        if native is not None:
            answer,count=native.insertion(j,routes,ends,reserved,pickups)
            evaluations+=count
            return answer
        best = None
        for w, route in enumerate(routes):
            for k in range(len(route) + 1):
                candidate = route[:k] + [j] + route[k:]
                result = evaluate(w, candidate)
                if result is None:
                    continue
                # Incremental total work first; a mild makespan tie-break avoids
                # filling every worker to 24 before opening another route.
                score = result[0] - ends[w] + 0.035 * result[0]
                if best is None or score < best[0]:
                    best = score, w, candidate, result
        return best

    if return_to_shed:
        for w in range(m):
            initial = evaluate(w, [])
            if initial is not None:
                install(w, [], initial)

    ordered = sorted(range(n), key=lambda j: (
        -bool(jobs[j].get('mandatory', False)), -jobs[j].get('priority', 1.0),
        deadlines[j], -min((_dist(s, positions[j]) for s in starts), default=0), -lengths[j]))
    unassigned = []
    for j in ordered:
        best = insertion(j)
        if best is None:
            unassigned.append(j)
        else:
            _, w, route, result = best
            install(w, route, result)

    # Intra-route reversal reduces travel and can free the one or two turns
    # that determine whether a distant urgent tile fits. Resource prefix and
    # deadlines are recomputed; this is safe even with track_collect=True.
    improved = True
    while improved and budget_available():
        improved = False
        for w, route in enumerate(routes):
            for a in range(len(route) - 1):
                for b in range(a + 1, len(route)):
                    if not budget_available():
                        break
                    candidate = route[:a] + route[a:b + 1][::-1] + route[b + 1:]
                    result = evaluate(w, candidate)
                    if result is not None and result[0] < ends[w]:
                        install(w, candidate, result)
                        route = candidate
                        improved = True
            if not budget_available():
                break
    # Cross-worker single-job relocation. Temporarily remove its reservations
    # so another worker can acquire exactly the freed resources.
    if budget_available():
        for src in range(m):
            for j in routes[src][:]:
                if not budget_available():
                    break
                oldroute, oldend, oldpick = routes[src][:], ends[src], pickups[src].copy()
                reduced = [x for x in oldroute if x != j]
                reduced_result = evaluate(src, reduced)
                if reduced_result is None:
                    continue
                install(src, reduced, reduced_result)
                best = insertion(j)
                if best is not None and (best[3][0] - ends[best[1]]) < oldend - ends[src] - 0.001:
                    install(best[1], best[2], best[3])
                else:
                    install(src, oldroute, (oldend, oldpick, 0))
    # Retry jobs after route improvement.
    left = []
    for j in unassigned:
        if not budget_available():
            left.append(j)
            continue
        best = insertion(j)
        if best is None:
            left.append(j)
        else:
            install(best[1], best[2], best[3])
    return {'routes': routes, 'unassigned': left, 'pickups': pickups,
            'pickup_positions': [nearest_shed[w] if pickups[w] else starts[w] for w in range(m)],
            'end_positions': [min(shed_positions,
                                  key=lambda s: _dist(s, positions[routes[w][-1]] if routes[w] else starts[w]))
                              if return_to_shed else (positions[routes[w][-1]] if routes[w] else starts[w])
                              for w in range(m)],
            'return_to_shed': return_to_shed,
            'finish_hours': ends,
            'stats': {'elapsed_ms': (perf_counter() - start_clock) * 1000,
                      'evaluations': evaluations, 'assigned': n - len(left),
                      'reserved': reserved}}


if __name__ == '__main__':
    import random
    rng = random.Random(0)
    sample = [{'pos': (x, y), 'ops': [['WATER']], 'priority': 10.0, 'mandatory': True}
              for y in range(10) for x in range(10)]
    crew = [{'pos': SHED[i % 4], 'hour': 1 + (i >= 10), 'inv': {}} for i in range(14)]
    answer = solve_routes(sample, crew, {}, time_budget_ms=45)
    assert len(set(j for r in answer['routes'] for j in r)) == 100
    assert not answer['unassigned']
    assert all(h <= 24 for h in answer['finish_hours'])
    print(answer['stats'])
