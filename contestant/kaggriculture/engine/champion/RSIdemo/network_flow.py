"""Cheap necessary-condition flow checks for route candidates.

The exact route solver remains authoritative.  This module only rejects a
candidate when material, worker, or time-window supply is provably
insufficient, so a false positive can still be sent to route certification.
"""

from dataclasses import dataclass
import heapq


@dataclass(frozen=True)
class TaskWindow:
    name: str
    demand: int
    slots: tuple
    material: str = ""


@dataclass(frozen=True)
class WorkerWindow:
    """Total operation capacity plus distinct available slots."""
    name: str
    capacity: int
    slots: tuple


class _Edge:
    __slots__ = ("to", "rev", "cap")
    def __init__(self, to, rev, cap):
        self.to, self.rev, self.cap = to, rev, int(cap)


class Dinic:
    def __init__(self, n):
        self.graph = [[] for _ in range(n)]

    def add_edge(self, source, target, capacity):
        forward = _Edge(target, len(self.graph[target]), capacity)
        backward = _Edge(source, len(self.graph[source]), 0)
        self.graph[source].append(forward)
        self.graph[target].append(backward)

    def max_flow(self, source, sink):
        total = 0
        while True:
            level = [-1] * len(self.graph)
            level[source] = 0
            queue = [source]
            for vertex in queue:
                for edge in self.graph[vertex]:
                    if edge.cap and level[edge.to] < 0:
                        level[edge.to] = level[vertex] + 1
                        queue.append(edge.to)
            if level[sink] < 0:
                return total
            cursor = [0] * len(self.graph)
            def send(vertex, amount):
                if vertex == sink:
                    return amount
                while cursor[vertex] < len(self.graph[vertex]):
                    edge = self.graph[vertex][cursor[vertex]]
                    if edge.cap and level[edge.to] == level[vertex] + 1:
                        pushed = send(edge.to, min(amount, edge.cap))
                        if pushed:
                            edge.cap -= pushed
                            self.graph[edge.to][edge.rev].cap += pushed
                            return pushed
                    cursor[vertex] += 1
                return 0
            while True:
                pushed = send(source, 10**18)
                if not pushed:
                    break
                total += pushed


def assignment_flow(tasks, workers, slot_capacity=1):
    """Return (flow, required) for unit task-to-worker-slot assignment."""
    tasks = tuple(tasks); workers = tuple(workers)
    if any(int(task.demand) < 0 for task in tasks) or any(int(worker.capacity) < 0 for worker in workers):
        raise ValueError("demands and capacities must be non-negative")
    if len({w.name for w in workers}) != len(workers):
        raise ValueError('worker names must be unique')
    if int(slot_capacity) < 0:
        raise ValueError('slot capacity must be non-negative')
    worker_slots = [(i, slot) for i, worker in enumerate(workers) for slot in dict.fromkeys(worker.slots)]
    # The aggregate worker node enforces capacity across all of its slots.
    source = 0
    task_base = 1
    worker_base = task_base + len(tasks)
    aggregate_base = worker_base + len(worker_slots)
    sink = aggregate_base + len(workers)
    network = Dinic(sink + 1)
    required = sum(int(task.demand) for task in tasks)
    for index, task in enumerate(tasks):
        network.add_edge(source, task_base + index, int(task.demand))
        allowed = set(task.slots)
        for worker_index, (worker_id, slot) in enumerate(worker_slots):
            if slot in allowed:
                network.add_edge(task_base + index, worker_base + worker_index, int(slot_capacity))
    for worker_index, (worker_id, slot) in enumerate(worker_slots):
        network.add_edge(worker_base + worker_index, aggregate_base + worker_id, int(slot_capacity))
    for worker_id, worker in enumerate(workers):
        network.add_edge(aggregate_base + worker_id, sink, int(worker.capacity))
    return network.max_flow(source, sink), required


def check_feasibility(tasks, workers, materials=None, available_slots=None):
    """Check only necessary constraints and return an auditable receipt."""
    flow, required = assignment_flow(tasks, workers)
    material_receipt = {}
    materials = materials or {}
    for material, need in materials.items():
        available = int(available_slots.get(material, 0)) if available_slots else 0
        material_receipt[material] = {"required": int(need), "available": available,
                                      "ok": available >= int(need)}
    materials_ok = all(row["ok"] for row in material_receipt.values())
    return {"feasible": flow == required and materials_ok, "assigned": flow,
            "required": required, "materials": material_receipt,
            "reason": "ok" if flow == required and materials_ok else "necessary condition failed"}


def travel_lower_bound(jobs, workers):
    """Minimum spanning forest with all worker starts joined to a free root.

    Any actual set of worker routes connects every serviced job to a start.
    Contracting repeated visits leaves a spanning forest; its metric MST
    cannot exceed total route movement. This bound never charges a separate
    depot round trip for each job.
    """
    remaining=set(tuple(job['pos']) for job in jobs if job['ops'])
    starts=[tuple(w['pos']) for w in workers]
    if not remaining:return 0
    if not starts:return float('inf')
    distances={pos:min(abs(pos[0]-s[0])+abs(pos[1]-s[1]) for s in starts) for pos in remaining}
    total=0
    while remaining:
        pos=min(remaining,key=lambda p:(distances[p],p));total+=distances[pos]
        remaining.remove(pos)
        for other in remaining:
            distances[other]=min(distances[other],abs(other[0]-pos[0])+abs(other[1]-pos[1]))
    return total


def interval_assignment(windows, slot_counts):
    """Maximum unit matching for interval task windows and identical slots.

    This is the integral min-cost-free flow problem specialized to intervals:
    at each slot, assigning the earliest deadline cannot hurt a later task.
    Individual worker budgets may ONLY be aggregated when each worker budget
    equals its available slots, as in route_relaxation. General budgets still
    use assignment_flow's explicit worker capacity nodes.
    """
    jobs=sorted(windows);pending=[];index=0;assigned=0
    for hour,count in sorted(slot_counts.items()):
        while index<len(jobs) and jobs[index][0]<=hour:
            heapq.heappush(pending,jobs[index][1]);index+=1
        while pending and pending[0]<hour:heapq.heappop(pending)
        for _ in range(count):
            if not pending:break
            heapq.heappop(pending);assigned+=1
    return assigned


def route_relaxation(jobs, workers, last_hour=23):
    """Necessary slot-matching bound for the current route solver.

    Each operation gets an earliest slot from the closest starting worker
    and a latest slot from the job's deadline and remaining operations.
    First check a rooted spanning-forest lower bound on total travel. Then
    operations may split across workers and material/return costs are omitted.
    Thus an actual complete route always induces a feasible assignment. A
    positive result is never a certificate.
    """
    from time import perf_counter
    started=perf_counter()
    windows=[WorkerWindow(str(i), max(0,last_hour+1-int(w.get('hour',0))),
               tuple(range(int(w.get('hour',0)),last_hour+1))) for i,w in enumerate(workers)]
    travel=travel_lower_bound(jobs,workers);needed=sum(len(j['ops']) for j in jobs)
    capacity=sum(w.capacity for w in windows)
    if needed+travel>capacity:
        return {'feasible':False,'required':needed,'assigned':None,'travel_lower_bound':travel if workers else None,
                'capacity':capacity,'seconds':perf_counter()-started,'kind':'operation + rooted travel bound'}
    tasks=[]
    for j,job in enumerate(jobs):
        x,y=job['pos'];ops=job['ops']
        earliest=min((int(w.get('hour',0))+abs(x-w['pos'][0])+abs(y-w['pos'][1]) for w in workers),default=last_hour+1)
        last=min(last_hour,int(job.get('deadline',last_hour)))
        for offset in range(len(ops)):
            latest=last-(len(ops)-1-offset)
            tasks.append((earliest+offset,latest))
    slot_counts={hour:sum(hour in w.slots for w in windows) for hour in range(last_hour+1)}
    flow=interval_assignment(tasks,slot_counts)
    return {'feasible':flow==needed,'assigned':flow,'required':needed,
            'travel_lower_bound':travel,'capacity':capacity,
            'seconds':perf_counter()-started,'kind':'interval integral flow + rooted travel bound'}
