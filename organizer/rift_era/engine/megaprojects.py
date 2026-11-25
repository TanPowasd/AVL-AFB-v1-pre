"""L12 megaprojects: strait bridge/tunnel and artificial island (03, 04, 06).

Built through ``industry/build_facility`` with ``profile_id`` ``L12_bridge`` or
``L12_island`` and ``facility_type: public`` (06 profile domain).  Materials
and periods follow 03 L12; all materials are locked at the building site by
the ordinary construction work order.  A completed, available project is the
07 L12 ActiveScale count ``completed_megaprojects``.

Physical effects (IMPLEMENTATION_CONTRACT ``l12_megaproject_effects_v1``):

* bridge/tunnel: at order time the partner is the nearest other hub the
  builder controls within 8 grid cells (Manhattan) whose straight L path
  (x first, then y) crosses at least one ocean cell; ties by hub id.  On
  completion a ``rail`` cargo corridor ``l12:<asset>`` (800 standard loads =
  800 000 mU per cycle) and a ``power`` line ``power:l12:<asset>`` rated 800
  power/cycle are created over that path, both backed by the project asset.
* artificial island: on completion a new near-shore hub ``<site>-isl<n>`` is
  added in the site's region, on the nearest free ocean cell to the site hub,
  owned by the builder, with an empty population and the base 300-load public
  warehouse (no material book).  It has no corridor; links are built as usual.
"""
from dataclasses import dataclass

from .construction import WorkOrder
from .errors import InvariantViolation, RuleViolation
from .numeric import units

BRIDGE_CELLS = 8
BRIDGE_LOADS_mU = 800000  # 03: 800 standard loads / cycle, and the same power rating


SPECS = {
    'L12_bridge': ('bridge', {'structure_module': 40, 'steel': 80, 'cement': 120, 'metamaterial': 4}, 120),
    'L12_island': ('island', {'structure_module': 80, 'steel': 160, 'cement': 300, 'metamaterial': 8}, 240),
}


@dataclass
class Megaproject:
    id: str
    owner: str
    hub: str
    kind: str
    completed: bool = False
    partner: str | None = None
    cells: tuple[int, ...] = ()
    created: str | None = None  # corridor id (bridge) or hub id (island)


class Megaprojects:
    def __init__(self, state):
        self.s = state
        self.rows: dict[str, Megaproject] = {}

    def prepare(self, builder, owner, order, ctx):
        state = self.s
        if order['facility_type'] != 'public' or 'good' in order:
            raise RuleViolation('ERR_SCHEMA')
        kind, materials, cycles = SPECS[order['profile_id']]
        hub = order['hub_id']
        site = builder._owned_hub(owner, hub)
        builder._tech(owner, {'L12'})
        region = state.initial.map.regions[site.region]
        # Both projects are coastal: a strait crossing or an offshore island.
        if not any(state.initial.map.regions[r].terrain == 'ocean' for r in region.adjacent):
            raise RuleViolation('ERR_SITE_INCOMPATIBLE')
        partner, cells = (self.bridge_partner(owner, site) if kind == 'bridge' else (None, ()))
        if kind == 'bridge' and partner is None:
            raise RuleViolation('ERR_SITE_INCOMPATIBLE', 'no own hub across a strait within 8 cells')
        builder._slots(owner, hub)
        costs = {g: units(q) for g, q in materials.items()}
        costs, cycles = builder._materials(owner, order, costs, cycles)
        jid, aid = builder._ids(owner, order)
        asset = builder._asset(aid, owner, hub, costs)
        job = WorkOrder(jid, str(int(order['id'])), owner, hub, 'build_megaproject', aid,
                        cycles * ctx.clock.cycle_ticks, costs, builder._service_cost(owner),
                        temporary_asset=aid, payload={'profile': order['profile_id'], 'kind': kind})
        row = Megaproject(aid, owner, hub, kind, partner=partner, cells=cells)

        def register():
            state.initial.assets[aid] = asset
            self.rows[aid] = row

        return state.work.prepare_start(job, register)

    @staticmethod
    def l_path(a, b):
        from .world import SIZE
        (x0, y0), (x1, y1) = a, b
        cells = [y0 * SIZE + x0]
        x, y = x0, y0
        while x != x1:
            x += 1 if x1 > x else -1
            cells.append(y * SIZE + x)
        while y != y1:
            y += 1 if y1 > y else -1
            cells.append(y * SIZE + x)
        return tuple(cells)

    def bridge_partner(self, owner, site):
        world = self.s.initial.map
        best = None
        for hid, hub in sorted(world.hubs.items()):
            if hid == site.id or not self.s.authority.controls_hub(owner, hid):
                continue
            distance = abs(hub.x - site.x) + abs(hub.y - site.y)
            if not 0 < distance <= BRIDGE_CELLS:
                continue
            cells = self.l_path((site.x, site.y), (hub.x, hub.y))
            if not any(world.terrain_cells[c] == 0 for c in cells):
                continue
            if best is None or distance < best[0]:
                best = (distance, hid, cells)
        return (best[1], best[2]) if best else (None, ())

    def complete(self, job):
        self.s.asset_lifecycle.commission(job.target)
        row = self.rows[job.target]
        row.completed = True
        if row.kind == 'bridge':
            self._open_bridge(row)
        else:
            self._raise_island(row)

    def _open_bridge(self, row):
        from .logistics import Corridor
        from .utility_network import UtilityLink
        state, ops = self.s, self.s.cargo
        eid = f'l12:{row.id}'
        ops.corridors[eid] = Corridor(eid, row.hub, row.partner, len(row.cells) - 1, BRIDGE_LOADS_mU)
        ops.corridors[eid].mode = 'rail'
        ops.levels[eid] = 1
        ops.paths[eid] = row.cells
        ops.road_assets[eid] = row.id
        lid = f'power:l12:{row.id}'
        state.utilities.links[lid] = UtilityLink(lid, row.owner, row.hub, row.partner, 'power', 1, row.cells,
                                                 row.id, rating_mU=BRIDGE_LOADS_mU)
        row.created = eid

    def _raise_island(self, row):
        from .population import Population
        from .world import Hub, SIZE
        state, initial = self.s, self.s.initial
        world = initial.map
        site = world.hubs[row.hub]
        taken = {h.y * SIZE + h.x for h in world.hubs.values()}
        best = None
        for radius in range(1, SIZE):
            for dx in range(-radius, radius + 1):
                for dy in (radius - abs(dx), -(radius - abs(dx))):
                    x, y = site.x + dx, site.y + dy
                    if not (0 <= x < SIZE and 0 <= y < SIZE):
                        continue
                    cell = y * SIZE + x
                    if world.terrain_cells[cell] == 0 and cell not in taken and (best is None or cell < best):
                        best = cell
            if best is not None:
                break
        if best is None:
            raise InvariantViolation('no ocean cell for an artificial island')
        index = sum(r.kind == 'island' and r.hub == row.hub and r.created is not None for r in self.rows.values())
        hid = f'{row.hub}-isl{index}'
        world.hubs[hid] = Hub(hid, site.region, best % SIZE, best // SIZE, row.owner)
        initial.populations[hid] = Population(hid, {'general_worker': 0, 'skilled_worker': 0, 'engineer': 0})
        initial.warehouse_modules[hid] = {f'warehouse:{hid}': 0}
        from .assets import Asset
        initial.assets[f'warehouse:{hid}'] = Asset(f'warehouse:{hid}', row.owner, hid, {})
        initial.warehouse_mU[hid] = 300000
        initial.housing_mU[hid] = 0
        state.register_hub(hid)
        row.created = hid

    def cancelled(self, job):
        self.rows.pop(job.target, None)

    def completed(self, owner):
        assets = self.s.initial.assets
        return sum(r.completed and r.owner == owner and r.id in assets and assets[r.id].availability > 0
                   for r in self.rows.values())

    def snapshot(self):
        return {'rows': self.rows}

    def audit(self):
        for aid, row in self.rows.items():
            if row.completed and aid not in self.s.initial.assets:
                raise InvariantViolation('completed megaproject lost its asset')
            if row.kind == 'bridge' and (row.partner is None or len(row.cells) < 2):
                raise InvariantViolation('bridge without a partner path')
            if row.completed and row.created is None:
                raise InvariantViolation('completed megaproject without its physical effect')
            if row.kind == 'island' and row.created is not None and row.created not in self.s.initial.map.hubs:
                raise InvariantViolation('artificial island hub missing')
