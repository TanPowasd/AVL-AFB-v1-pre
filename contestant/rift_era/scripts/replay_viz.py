"""Non-intrusive visual replay recorder and verifier for rift_era (afb-rift-era-replay/1).

Runs an 8-nation seated match using the exact same engine/driver/catalog pipeline
as ``dev_seats.run_seated_match`` without modifying engine RNG or simulation state,
while sampling compact visual frames for offline playback in ``viz/player.html``.
"""
from __future__ import annotations

from contextlib import ExitStack
from fractions import Fraction
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Sequence

REPLAY_FORMAT = 'afb-rift-era-replay/1'
SEAT_COUNT = 8
GRID_SIZE = 64
TERRAIN_CODES = {'ocean': 0, 'plain': 1, 'hill': 2, 'forest': 3, 'desert': 4, 'mountain': 5}
MODE_CODES = {'road': 'r', 'sea': 's', 'river': 'v', 'rail': 'l', 'air': 'a'}


def _locate_runtime() -> tuple[Path, str]:
    """Locate the rift_era package root and import prefix in kit or repo layouts."""
    here = Path(__file__).resolve()
    for parent in (here.parent, here.parents[1], here.parents[2] if len(here.parents) > 2 else here.parent):
        pkg_dir = parent / 'runtime' / 'pkg'
        if (pkg_dir / 'rift_era' / 'engine').is_dir():
            if str(pkg_dir) not in sys.path:
                sys.path.insert(0, str(pkg_dir))
            return pkg_dir / 'rift_era', 'rift_era'
        if (parent / 'engine').is_dir() and (parent / 'sdk.py').is_file():
            top = str(parent.parent)
            if top not in sys.path:
                sys.path.insert(0, top)
            return parent, parent.name
    raise ImportError(f'cannot locate rift_era engine relative to {here}')


def _import_engine(module_name: str):
    _, prefix = _locate_runtime()
    return importlib.import_module(f'{prefix}.engine.{module_name}')


def _engine_version() -> str:
    _, prefix = _locate_runtime()
    pkg = importlib.import_module(f'{prefix}.engine')
    return str(getattr(pkg, 'ENGINE_VERSION', 'unknown'))


def seat_sha256(seat: str | Path) -> str | None:
    """Compute a deterministic digest for a seat spec (zip, directory, or builtin)."""
    if seat == 'none':
        return None
    if seat == 'simple':
        pkg_root, _ = _locate_runtime()
        sdk = pkg_root / 'sdk.py'
        return hashlib.sha256(sdk.read_bytes()).hexdigest() if sdk.is_file() else None
    path = Path(seat)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        digest = hashlib.sha256()
        for file in sorted((p for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts),
                           key=lambda p: p.relative_to(path).parts):
            digest.update(file.relative_to(path).as_posix().encode('utf-8') + b'\0')
            digest.update(hashlib.sha256(file.read_bytes()).hexdigest().encode('ascii') + b'\n')
        return 'tree:' + digest.hexdigest()
    return None


def default_seat_label(seat: str | Path) -> str:
    if isinstance(seat, str):
        return seat
    p = Path(seat)
    return p.stem if p.is_file() else p.name


def _parse_fraction(value: Any) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, dict):
        if '$rational' in value:
            n, d = value['$rational']
            return Fraction(int(n), int(d))
        if 'exact' in value:
            return _parse_fraction(value['exact'])
    if isinstance(value, str) and '/' in value:
        n, d = value.split('/', 1)
        return Fraction(int(n), int(d))
    return Fraction(value)


def _extract_map(state) -> tuple[dict, dict[str, tuple[str, ...]], list[str]]:
    """Extract compact 512x512 world layout, terrain preview, hubs and corridors."""
    world = state.initial.map
    size = int(len(world.terrain_cells) ** 0.5)
    step = max(1, size // GRID_SIZE)
    terrain_chars = []
    for gy in range(GRID_SIZE):
        sy = min(size - 1, gy * step + step // 2)
        base = sy * size
        for gx in range(GRID_SIZE):
            sx = min(size - 1, gx * step + step // 2)
            terrain_chars.append(str(int(world.terrain_cells[base + sx])))

    regions_ordered = sorted(world.regions.values(), key=lambda r: (r.row, r.column))
    max_row = max((r.row for r in regions_ordered), default=19) + 1
    max_col = max((r.column for r in regions_ordered), default=23) + 1
    regions_compact = [
        [r.id, int(r.row), int(r.column), r.continent, TERRAIN_CODES.get(r.terrain, 1), r.sovereign]
        for r in regions_ordered
    ]

    city_states_set = set(world.city_states)
    coastal_set = set(getattr(world, 'coastal_hubs', ()) or ())
    pops = state.initial.populations
    city_pops_map = {c.hub: c.population.total_mU for c in state.city_economy.cities.values()}

    hub_ids = sorted(world.hubs)
    hub_index = {hid: idx for idx, hid in enumerate(hub_ids)}
    region_to_hubs: dict[str, list[str]] = {}
    cities = []
    for hid in hub_ids:
        hub = world.hubs[hid]
        region_to_hubs.setdefault(hub.region, []).append(hid)
        auth = state.authority.regions.get(hub.region)
        ctrl = auth.controller if auth is not None else hub.owner
        pop_mU = pops[hid].total_mU if hid in pops else city_pops_map.get(hid, 0)
        cities.append({
            'id': hid,
            'name': hid,
            'region': hub.region,
            'x': int(hub.x),
            'y': int(hub.y),
            'owner': ctrl,
            'sovereign': hub.owner,
            'is_capital': hub.core_index == 0,
            'is_core': hub.core_index is not None,
            'is_city_state': hub.owner in city_states_set,
            'is_coastal': hid in coastal_set,
            'pop': round(pop_mU / 1000.0, 1),
        })

    corridors = []
    for rid in sorted(world.routes):
        route = world.routes[rid]
        if route.a in hub_index and route.b in hub_index:
            corridors.append([hub_index[route.a], hub_index[route.b], MODE_CODES.get(route.mode, 'r')])

    map_payload = {
        'width': size,
        'height': size,
        'grid_w': GRID_SIZE,
        'grid_h': GRID_SIZE,
        'terrain': ''.join(terrain_chars),
        'rows': max_row,
        'cols': max_col,
        'regions': regions_compact,
        'cities': cities,
        'corridors': corridors,
    }
    return map_payload, {k: tuple(v) for k, v in region_to_hubs.items()}, hub_ids


class _SamplingEngine:
    """Thin read-only step observer delegating all Kernel access untouched."""

    def __init__(self, real_engine, on_after_step):
        self._engine = real_engine
        self._on_after_step = on_after_step

    def __getattr__(self, name: str):
        return getattr(self._engine, name)

    def step(self, orders=None):
        result = self._engine.step(orders)
        self._on_after_step(self._engine, orders, result)
        return result


class _FrameCollector:
    """Pure read-only sampler collecting timeline frames and strategic events."""

    def __init__(self, engine, owners: list[str], sample_every: int,
                 region_to_hubs: dict[str, tuple[str, ...]], hub_ids: list[str],
                 sessions: dict[str, Any]):
        self.engine = engine
        self.owners = list(owners)
        self.sample_every = max(1, int(sample_every))
        self.region_to_hubs = region_to_hubs
        self.hub_ids = hub_ids
        self.sessions = sessions
        self.total_cycles = max(1, engine.clock.total_ticks // engine.clock.cycle_ticks)
        self.era_cycles = max(1, self.total_cycles // 4)

        state = engine.state
        self.tech_names = {
            tid: str(row.get('name', tid))
            for tid, row in state.catalog.technologies.items()
        }
        self._cash_account_cls = _import_engine('ledger').CashAccount
        self._relation_war = _import_engine('diplomacy').Relation.WAR

        self.prev_relations: dict[tuple[str, str], str] = {
            pair: row.state.value for pair, row in state.initial.diplomacy.pairs.items()
        }
        self.prev_region_ctrl: dict[str, str | None] = {
            rid: row.controller for rid, row in state.authority.regions.items()
        }
        self.prev_known: dict[str, set[str]] = {
            p: set(state.research[p].known) for p in self.owners
        }
        self.prev_listed: set[str] = {
            cid for cid, comp in state.initial.companies.items() if comp.listed
        }
        self.prev_defaults: dict[str, int] = {
            p: int(state.sovereign_finance.default_count.get(p, 0)) for p in self.owners
        }
        self.prev_sats: set[str] = set(state.orbit.satellites.keys())
        self.seen_combat: set[tuple[int, str]] = set()

        self.last_frame_hub_owners: dict[str, str | None] = {
            hid: state.authority.regions[state.initial.map.hubs[hid].region].controller
            for hid in self.hub_ids
        }
        self.last_frame_region_owners: dict[str, str | None] = dict(self.prev_region_ctrl)
        self.last_frame_pops: dict[str, float] = self._current_hub_pops(state)

        self.pending_events: list[dict[str, Any]] = []
        self.window_orders: dict[str, int] = {p: 0 for p in self.owners}
        self.frames: list[dict[str, Any]] = []

        # Capture tick 0 initial frame before any step runs.
        self.capture_frame(0, initial=True)

    def _current_hub_pops(self, state) -> dict[str, float]:
        out: dict[str, float] = {}
        for hid, pop in state.initial.populations.items():
            val = round(pop.total_mU / 1000.0, 1)
            if val > 0:
                out[hid] = val
        for city in state.city_economy.cities.values():
            val = round(city.population.total_mU / 1000.0, 1)
            if val > 0:
                out[city.hub] = val
        return out

    def on_after_step(self, engine, orders, result) -> None:
        t = result.tick
        state = engine.state
        if orders:
            for owner, batch in orders.items():
                if owner in self.window_orders:
                    self.window_orders[owner] += len(batch)

        # 1) Diplomacy transitions
        for pair_key, row in sorted(state.initial.diplomacy.pairs.items()):
            cur = row.state.value
            old = self.prev_relations.get(pair_key, 'PEACE')
            if cur != old:
                self.prev_relations[pair_key] = cur
                a, b = pair_key
                if cur == 'WAR':
                    aggressors = sorted(row.aggressors)
                    actor = aggressors[0] if aggressors else a
                    target = b if actor == a else a
                    self.pending_events.append({
                        'tick': t, 'type': 'war', 'actor': actor, 'target': target,
                        'detail': f'{actor} 向 {target} 宣战',
                    })
                elif cur == 'TRUCE':
                    self.pending_events.append({
                        'tick': t, 'type': 'peace', 'actor': a, 'target': b,
                        'detail': f'{a} 与 {b} 达成停战',
                    })
                elif cur == 'PEACE' and old == 'WAR':
                    self.pending_events.append({
                        'tick': t, 'type': 'peace', 'actor': a, 'target': b,
                        'detail': f'{a} 与 {b} 缔结和约',
                    })
                elif cur == 'PEACE' and old == 'TRUCE':
                    self.pending_events.append({
                        'tick': t, 'type': 'diplomacy', 'actor': a, 'target': b,
                        'detail': f'{a} 与 {b} 停战到期',
                    })

        # 2) Territorial control captures
        for rid, row in sorted(state.authority.regions.items()):
            old_ctrl = self.prev_region_ctrl.get(rid)
            if row.controller != old_ctrl:
                self.prev_region_ctrl[rid] = row.controller
                hubs = self.region_to_hubs.get(rid, ())
                target_label = hubs[0] if hubs else rid
                new_ctrl = row.controller or '中立'
                prev_label = old_ctrl or '无主'
                self.pending_events.append({
                    'tick': t, 'type': 'capture', 'actor': new_ctrl, 'target': target_label,
                    'detail': f'{new_ctrl} 占领 {target_label}（原 {prev_label}）',
                })

        # 3) Research unlocks
        for owner in self.owners:
            known = state.research[owner].known
            if len(known) > len(self.prev_known[owner]):
                unlocked = sorted(known - self.prev_known[owner])
                self.prev_known[owner] = set(known)
                for tid in unlocked:
                    tname = self.tech_names.get(tid, tid)
                    self.pending_events.append({
                        'tick': t, 'type': 'tech', 'actor': owner, 'target': tid,
                        'detail': f'{owner} 解锁科技 {tid}（{tname}）',
                    })

        # 4) Active combat regions (deduplicated per cycle + region)
        if state.battles.combat_regions:
            cyc = engine.clock.cycle(t)
            for rid in sorted(state.battles.combat_regions):
                key = (cyc, rid)
                if key not in self.seen_combat:
                    self.seen_combat.add(key)
                    auth = state.authority.regions.get(rid)
                    ctrl = auth.controller if auth is not None else None
                    self.pending_events.append({
                        'tick': t, 'type': 'combat', 'actor': ctrl or '战区', 'target': rid,
                        'detail': f'{rid} 发生交战',
                    })

        # 5) Sovereign defaults
        for owner in self.owners:
            cnt = int(state.sovereign_finance.default_count.get(owner, 0))
            if cnt > self.prev_defaults[owner]:
                self.prev_defaults[owner] = cnt
                self.pending_events.append({
                    'tick': t, 'type': 'finance', 'actor': owner,
                    'detail': f'{owner} 发生主权违约（第 {cnt} 次）',
                })

        # 6) Corporate IPOs
        for cid, comp in sorted(state.initial.companies.items()):
            if comp.listed and cid not in self.prev_listed:
                self.prev_listed.add(cid)
                self.pending_events.append({
                    'tick': t, 'type': 'finance', 'actor': comp.issuer, 'target': cid,
                    'detail': f'{comp.issuer} 公司 {cid} 上市',
                })

        # 7) Orbital satellites
        if len(state.orbit.satellites) > len(self.prev_sats):
            for sid, sat in sorted(state.orbit.satellites.items()):
                if sid not in self.prev_sats:
                    self.prev_sats.add(sid)
                    self.pending_events.append({
                        'tick': t, 'type': 'milestone', 'actor': sat.owner, 'target': sid,
                        'detail': f'{sat.owner} 发射卫星 {sid}',
                    })

        current_tick = engine.tick
        if current_tick % self.sample_every == 0 or current_tick == engine.clock.total_ticks:
            self.capture_frame(current_tick, initial=False)

    def capture_frame(self, current_tick: int, *, initial: bool = False) -> None:
        state = self.engine.state
        world = state.initial.map
        cycle_done = current_tick // self.engine.clock.cycle_ticks
        epoch = 1 if current_tick == 0 else min(4, ((current_tick - 1) // self.engine.clock.cycle_ticks) // self.era_cycles + 1)

        # Pre-aggregate region & hub control counts (read-only)
        region_counts = {p: 0 for p in self.owners}
        city_counts = {p: 0 for p in self.owners}
        region_diffs: dict[str, str | None] = {}
        for rid, row in state.authority.regions.items():
            ctrl = row.controller
            if ctrl in region_counts:
                region_counts[ctrl] += 1
            if not initial and ctrl != self.last_frame_region_owners.get(rid):
                region_diffs[rid] = ctrl
                self.last_frame_region_owners[rid] = ctrl

        hub_diffs: dict[str, str | None] = {}
        for hid in self.hub_ids:
            ctrl = state.authority.regions[world.hubs[hid].region].controller
            if ctrl in city_counts:
                city_counts[ctrl] += 1
            if not initial and ctrl != self.last_frame_hub_owners.get(hid):
                hub_diffs[hid] = ctrl
                self.last_frame_hub_owners[hid] = ctrl

        # Pre-aggregate population per controlling nation
        pop_by_owner = {p: 0 for p in self.owners}
        for hid, pop in state.initial.populations.items():
            ctrl = state.authority.regions[world.hubs[hid].region].controller
            if ctrl in pop_by_owner:
                pop_by_owner[ctrl] += pop.total_mU

        cur_pops = self._current_hub_pops(state)
        pop_diffs: dict[str, float] = {}
        if not initial:
            for hid in cur_pops.keys() | self.last_frame_pops.keys():
                new_v = cur_pops.get(hid, 0.0)
                old_v = self.last_frame_pops.get(hid, 0.0)
                if abs(new_v - old_v) >= 0.1:
                    pop_diffs[hid] = new_v
            self.last_frame_pops = cur_pops

        # Pre-aggregate military formations & strength
        units_count = {p: 0 for p in self.owners}
        formations_count = {p: 0 for p in self.owners}
        mil_power = {p: 0.0 for p in self.owners}
        weights = state.catalog.weights
        for form in state.formations.rows.values():
            p = form.owner
            if p not in formations_count:
                continue
            formations_count[p] += 1
            units_count[p] += len(form.unit_ids)
            for uid in form.unit_ids:
                u = state.initial.units.get(uid)
                if u is None:
                    continue
                spec = state.catalog.units.get(u.unit_type)
                max_hp = max(1, int(spec['hp']) * 1000) if spec else 1000
                hp_frac = min(1.0, max(0.0, u.hp_mU / max_hp))
                val = sum(float(q) * float(weights[g]) for g, q in u.book_nmU.items()) / 1_000_000.0
                mil_power[p] += hp_frac * val

        # Pre-aggregate compute capacity
        cur_cycle = self.engine.clock.cycle(max(0, current_tick - 1)) if current_tick > 0 else 0
        compute_by_owner = {p: 0 for p in self.owners}
        for row in state.compute.centers.values():
            if row.owner in compute_by_owner:
                compute_by_owner[row.owner] += state.compute.available(row, cur_cycle)

        # Active wars
        wars = []
        war_targets: dict[str, list[str]] = {p: [] for p in self.owners}
        for (a, b), pair in sorted(state.initial.diplomacy.pairs.items()):
            if pair.state == self._relation_war:
                wars.append([a, b])
                if a in war_targets:
                    war_targets[a].append(b)
                if b in war_targets:
                    war_targets[b].append(a)

        np_coll = state.national_power
        nations_frame: dict[str, dict[str, Any]] = {}
        for owner in self.owners:
            rows = np_coll.rows.get(owner, ()) if np_coll is not None else ()
            last_row = rows[-1] if rows else None
            score = round(float(last_row.total), 4) if last_row is not None else 0.0
            pillars = (
                [round(float(last_row.population), 3), round(float(last_row.industry), 3),
                 round(float(last_row.control), 3), round(float(last_row.technology), 3),
                 round(float(last_row.international), 3)]
                if last_row is not None else [0.0, 0.0, 0.0, 0.0, 0.0]
            )
            gdp = round(float(last_row.va), 3) if last_row is not None else 0.0
            nfa = round(float(last_row.nfa_ru), 2) if last_row is not None else 0.0

            cash_ru = round(float(state.initial.cash.owner_book(owner)) / 1000.0, 2)
            treasury = round(state.initial.cash.balance(self._cash_account_cls(owner, 'treasury', owner)).native_mU / 1000.0, 2)

            hist = state.history.get(owner)
            cyc_metric = hist[-1] if hist else state.cycles.get(owner)
            e_gen = round((cyc_metric.generation_mU if cyc_metric else 0) / 1000.0, 2)
            e_need = round((cyc_metric.power_needed_mU if cyc_metric else 0) / 1000.0, 2)
            e_recv = round((cyc_metric.power_received_mU if cyc_metric else 0) / 1000.0, 2)
            p_ratio = round(min(1.0, e_recv / e_need), 3) if e_need > 0 else 1.0

            sess = self.sessions.get(owner)
            fault_cnt = sess.clock.timeouts if sess is not None else 0

            nations_frame[owner] = {
                'score': score,
                'pillars': pillars,
                'cash': cash_ru,
                'treasury': treasury,
                'gdp': gdp,
                'energy_gen': e_gen,
                'energy_need': e_need,
                'energy_recv': e_recv,
                'power_ratio': p_ratio,
                'compute': round(compute_by_owner[owner] / 1000.0, 2),
                'pop': round(pop_by_owner[owner] / 1000.0, 1),
                'cities': city_counts[owner],
                'regions': region_counts[owner],
                'mil_power': round(mil_power[owner], 2),
                'units': units_count[owner],
                'formations': formations_count[owner],
                'techs': len(state.research[owner].known),
                'credit': round(float(state.credit.get(owner, 0)), 3),
                'nfa': nfa,
                'orders': int(state.active_order_count(owner)),
                'window_orders': int(self.window_orders.get(owner, 0)),
                'war_targets': war_targets[owner],
                'faults': int(fault_cnt),
            }
            self.window_orders[owner] = 0

        events = self.pending_events[:200]
        self.pending_events = []

        self.frames.append({
            'tick': int(current_tick),
            'cycle': int(cycle_done),
            'epoch': int(epoch),
            'nations': nations_frame,
            'city_owners': hub_diffs,
            'region_owners': region_diffs,
            'city_pops': pop_diffs,
            'wars': wars,
            'events': events,
        })


def _backfill_faults(frames: list[dict], faults_by_owner: dict[str, list[dict]]) -> None:
    """Attach exact protocol/timeout fault counts and events to the nearest frame."""
    if not frames:
        return
    frame_ticks = [f['tick'] for f in frames]
    cum_faults: dict[str, list[int]] = {}
    for owner, flist in faults_by_owner.items():
        if not flist:
            continue
        ticks = []
        for entry in flist:
            ft = entry.get('tick')
            if ft is None:
                win = entry.get('window')
                ft = int(win) * 5 if win is not None else 0
            ft = int(ft)
            ticks.append(ft)
            # Find first frame with tick >= ft
            idx = len(frames) - 1
            for i, candidate_t in enumerate(frame_ticks):
                if candidate_t >= ft:
                    idx = i
                    break
            errs = entry.get('errors') or ()
            reason = ', '.join(str(e) for e in errs[:2]) if errs else ('custodial' if entry.get('custodial') else 'fault')
            frames[idx]['events'].append({
                'tick': ft, 'type': 'fault', 'actor': owner,
                'detail': f'{owner} 故障：{reason}',
            })
        ticks.sort()
        cum_faults[owner] = ticks

    for frame in frames:
        t = frame['tick']
        for owner, fticks in cum_faults.items():
            if owner in frame['nations']:
                frame['nations'][owner]['faults'] = sum(1 for x in fticks if x <= t)


def record_match_replay(
    *,
    seed: int,
    seats: Sequence[str | Path],
    ticks: int = 200,
    sample_every: int = 10,
    legacy_map: bool = False,
    seat_labels: Sequence[str] | None = None,
    launcher=None,
) -> dict[str, Any]:
    """Run an 8-nation match identically to ``dev_seats.run_seated_match`` and capture visual replay data."""
    if len(seats) != SEAT_COUNT:
        raise ValueError(f'expected {SEAT_COUNT} seats, got {len(seats)}')
    if ticks <= 0 or ticks % 40:
        raise ValueError('ticks must be a positive multiple of 40')
    if sample_every <= 0:
        raise ValueError('sample_every must be a positive integer')

    catalog_mod = _import_engine('catalog')
    det_mod = _import_engine('determinism')
    dev_seats_mod = _import_engine('dev_seats')
    driver_mod = _import_engine('driver')
    economy_mod = _import_engine('economy')
    init_mod = _import_engine('initialization')
    ipc_mod = _import_engine('ipc')
    match_mod = _import_engine('match')
    world_mod = _import_engine('world')

    started = time.monotonic()
    catalog = catalog_mod.Catalog()
    world = world_mod.generate_candidate(seed) if legacy_map else match_mod.accepted_world(catalog, seed)
    engine = match_mod.match_economy(
        catalog, init_mod.initialize(catalog, world), total_ticks=ticks, fingerprint_each_tick=False
    )
    owners = sorted(engine.state.initial.known)
    map_payload, region_to_hubs, hub_ids = _extract_map(engine.state)

    labels = [
        str(seat_labels[i]) if seat_labels is not None and i < len(seat_labels) else default_seat_label(seats[i])
        for i in range(SEAT_COUNT)
    ]
    seat_entries = [
        {
            'owner': owners[i],
            'seat': i,
            'label': labels[i],
            'spec': dev_seats_mod.seat_label(seats[i]),
            'sha256': seat_sha256(seats[i]),
        }
        for i in range(SEAT_COUNT)
    ]

    economy = economy_mod.Economy(engine.state)
    has_strategy = any(s != 'none' for s in seats)

    if has_strategy:
        transport = 'sandbox' if launcher is not None else 'unsandboxed_dev_pipe'
        with ExitStack() as stack:
            temp = stack.enter_context(tempfile.TemporaryDirectory(prefix='rift-viz-replay-'))
            sessions: dict[str, Any] = {}
            for owner, seat in zip(owners, seats):
                if seat == 'none':
                    continue
                root = stack.enter_context(dev_seats_mod.materialized(seat, stack))
                if launcher is not None:
                    session = ipc_mod.StrategySession.launch(launcher, root)
                else:
                    managed = dev_seats_mod.DevProcess(root)
                    try:
                        session = ipc_mod.StrategySession(managed)
                    except Exception:
                        managed.close()
                        raise
                sessions[owner] = stack.enter_context(session)

            collector = _FrameCollector(engine, owners, sample_every, region_to_hubs, hub_ids, sessions)
            proxy = _SamplingEngine(engine, collector.on_after_step)
            driver = driver_mod.MatchDriver(
                proxy,
                sessions,
                init_messages={
                    p: driver_mod.economic_development_init(economy, p, engine.clock) | {'scope': match_mod.MATCH_PROFILE}
                    for p in sessions
                },
                observe=lambda p, t: driver_mod.economic_development_messages(economy, p, t, engine.clock),
                finalize=lambda: match_mod.finalize_match(engine),
                replay=Path(temp) / 'replay.jsonl',
                official=False,
                manifest={'profile': match_mod.MATCH_PROFILE, 'rules_hash': engine.state.catalog.fingerprint, 'transport': transport},
            )
            report = driver.run()
    else:
        collector = _FrameCollector(engine, owners, sample_every, region_to_hubs, hub_ids, {})
        while engine.tick < engine.clock.total_ticks:
            res = engine.step(None)
            collector.on_after_step(engine, None, res)
        engine.state.audit()
        summary = match_mod.finalize_match(engine)
        report = summary | {
            'valid': True,
            'official': False,
            'ticks': engine.tick,
            'state_hash': det_mod.fingerprint(engine.state.snapshot()),
            'faults': {p: [] for p in owners},
            'strategy_time': {p: None for p in owners},
        }

    _backfill_faults(collector.frames, report.get('faults', {}))

    # Build final exact nations block and exact winner
    nations_meta: dict[str, dict[str, Any]] = {}
    exact_vs: list[tuple[Fraction, str]] = []
    for idx, owner in enumerate(owners):
        row = report['nations'][owner]
        v_exact = _parse_fraction(row['V'])
        phi_exact = _parse_fraction(row['phi'])
        era_means = [round(float(_parse_fraction(x)), 6) for x in row.get('era_means', ())]
        end_mean = round(float(_parse_fraction(row.get('end_mean', 0))), 6)
        exact_vs.append((v_exact, owner))
        nations_meta[owner] = {
            'seat': idx,
            'label': labels[idx],
            'V': float(v_exact),
            'V_exact': f'{v_exact.numerator}/{v_exact.denominator}',
            'phi': round(float(phi_exact), 6),
            'era_means': era_means,
            'end_mean': end_mean,
            'faults': len(report.get('faults', {}).get(owner, ())),
            'strategy_time': report.get('strategy_time', {}).get(owner),
        }

    # Winner is highest exact V (deterministic tiebreak: lowest owner id)
    best_v = max(v for v, _ in exact_vs)
    winner = min(owner for v, owner in exact_vs if v == best_v)
    winner_label = nations_meta[winner]['label']

    meta = {
        'format': REPLAY_FORMAT,
        'problem': 'rift_era',
        'seed': int(seed),
        'ticks': int(ticks),
        'cycles': int(ticks // engine.clock.cycle_ticks),
        'sample_every': int(sample_every),
        'legacy_map': bool(legacy_map),
        'map_version': str(world.generator_version),
        'engine_version': _engine_version(),
        'engine_hash': det_mod.engine_source_fingerprint(),
        'rules_version': str(catalog.version),
        'rules_hash': str(catalog.fingerprint),
        'seats': seat_entries,
        'winner': winner,
        'winner_label': winner_label,
        'nations': nations_meta,
        'state_hash': str(report['state_hash']),
        'wall_seconds': round(time.monotonic() - started, 3),
    }

    return {
        'format': REPLAY_FORMAT,
        'meta': meta,
        'map': map_payload,
        'frames': collector.frames,
    }


def save_replay(path: str | Path, data: dict[str, Any]) -> Path:
    """Write replay JSON atomically-safe with UTF-8 encoding."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    out.write_text(text + '\n', encoding='utf-8', newline='\n')
    return out


def verify_against_engine(data: dict[str, Any], ref_report: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Verify replay schema integrity and (when given) exact match against an engine report."""
    if not isinstance(data, dict):
        return False, 'replay payload is not a dict'
    if data.get('format') != REPLAY_FORMAT:
        return False, f"format mismatch: {data.get('format')!r} != {REPLAY_FORMAT!r}"
    meta = data.get('meta')
    wmap = data.get('map')
    frames = data.get('frames')
    if not isinstance(meta, dict) or not isinstance(wmap, dict) or not isinstance(frames, list) or not frames:
        return False, 'missing meta, map, or non-empty frames'
    if meta.get('format') != REPLAY_FORMAT or meta.get('problem') != 'rift_era':
        return False, 'meta format/problem mismatch'
    if wmap.get('width') != 512 or wmap.get('height') != 512:
        return False, f"unexpected map dimensions: {wmap.get('width')}x{wmap.get('height')}"
    gw, gh = int(wmap.get('grid_w', 0)), int(wmap.get('grid_h', 0))
    if len(str(wmap.get('terrain', ''))) != gw * gh or gw <= 0 or gh <= 0:
        return False, 'invalid terrain grid dimensions'
    if not isinstance(wmap.get('cities'), list) or not wmap['cities']:
        return False, 'map missing cities'

    owners = [f'n{i}' for i in range(SEAT_COUNT)]
    nations = meta.get('nations')
    if not isinstance(nations, dict) or set(nations.keys()) != set(owners):
        return False, 'meta.nations must contain n0..n7'

    prev_t = -1
    for idx, f in enumerate(frames):
        t = f.get('tick')
        if not isinstance(t, int) or t < prev_t:
            return False, f'non-monotonic tick at frame {idx}: {t} < {prev_t}'
        prev_t = t
        fn = f.get('nations')
        if not isinstance(fn, dict) or set(fn.keys()) != set(owners):
            return False, f'frame {idx} missing n0..n7 metrics'
    if frames[0]['tick'] != 0 or frames[-1]['tick'] != meta.get('ticks'):
        return False, f"frame tick span [{frames[0]['tick']}..{frames[-1]['tick']}] != [0..{meta.get('ticks')}]"

    # Verify winner matches max exact V
    parsed_vs = {p: _parse_fraction(nations[p]['V_exact']) for p in owners}
    best_v = max(parsed_vs.values())
    expected_winner = min(p for p, v in parsed_vs.items() if v == best_v)
    if meta.get('winner') != expected_winner:
        return False, f"winner mismatch: {meta.get('winner')} != {expected_winner}"

    if ref_report is not None:
        ref_hash = ref_report.get('state_hash')
        if ref_hash is not None and meta.get('state_hash') != ref_hash:
            return False, f"state_hash mismatch: {meta.get('state_hash')} != {ref_hash}"
        ref_nations = ref_report.get('nations') or {}
        for owner in owners:
            if owner not in ref_nations:
                return False, f'ref_report missing {owner}'
            row = ref_nations[owner]
            if 'V_exact' in row:
                ref_v = _parse_fraction(row['V_exact'])
            elif 'V' in row:
                ref_v = _parse_fraction(row['V'])
            else:
                return False, f'ref_report {owner} missing V'
            if parsed_vs[owner] != ref_v:
                return False, f'exact V mismatch for {owner}: {parsed_vs[owner]} != {ref_v}'

    return True, 'ok'
