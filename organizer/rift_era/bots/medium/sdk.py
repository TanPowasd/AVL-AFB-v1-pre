"""Standalone standard-library client; copy this file into a submission zip.

No engine import, world truth or environment access is needed. Public catalog
ratios use {"$rational": [numerator, denominator]}; live command amounts are mU
integers. receive() returns an entire init/obs/end message after checked paging.
"""

import base64
from fractions import Fraction
import hashlib
import json
import sys

MAX_LINE = 262144
MAX_SNAPSHOT = 16777216


def ratio(value):
    if isinstance(value, dict) and set(value) == {'$rational'}:
        return Fraction(*value['$rational'])
    return Fraction(value)


class Client:
    def __init__(self, source=None, target=None):
        self.source = source if source is not None else sys.stdin.buffer
        self.target = target if target is not None else sys.stdout.buffer
        self.next_id = 1

    def _line(self):
        raw = self.source.readline(MAX_LINE + 2)
        if not raw:
            raise EOFError('judge closed input')
        if not raw.endswith(b'\n') or len(raw) - 1 > MAX_LINE:
            raise ValueError('invalid judge frame length')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('judge frame must be an object')
        return value

    def receive(self):
        first = self._line()
        if first.get('type') != 'snapshot_chunk':
            if first.get('type') not in ('init', 'obs', 'end'):
                raise ValueError('unknown judge message')
            return first
        total, count, sid = first.get('total_bytes'), first.get('count'), first.get('snapshot_id')
        if (type(total) is not int or not 0 <= total <= MAX_SNAPSHOT or type(count) is not int
                or not 1 <= count <= MAX_SNAPSHOT or not isinstance(sid, str) or len(sid) != 64):
            raise ValueError('invalid paged message bounds')
        chunks, size = [], 0
        for index in range(count):
            row = first if index == 0 else self._line()
            if (row.get('type'), row.get('encoding'), row.get('snapshot_id'), row.get('total_bytes'),
                    row.get('count'), row.get('index')) != (
                    'snapshot_chunk', 'base64_json_v1', sid, total, count, index):
                raise ValueError('missing, reordered or mixed snapshot page')
            chunk = base64.b64decode(row['payload'], validate=True)
            size += len(chunk)
            if size > total:
                raise ValueError('snapshot exceeds its declared length')
            chunks.append(chunk)
        raw = b''.join(chunks)
        if size != total or hashlib.sha256(raw).hexdigest() != sid:
            raise ValueError('snapshot length or digest mismatch')
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get('type') not in ('init', 'obs', 'end'):
            raise ValueError('unknown logical judge message')
        return value

    def _send(self, message):
        raw = json.dumps(message, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(raw) > MAX_LINE:
            raise ValueError('strategy message exceeds 256KiB')
        self.target.write(raw + b'\n')
        self.target.flush()

    def ready(self):
        self._send({'type': 'ready'})

    def command(self, kind, op, **parameters):
        if any(k in parameters for k in ('id', 'kind', 'op')):
            raise ValueError('reserved command field')
        result = {'id': str(self.next_id), 'kind': kind, 'op': op} | parameters
        self.next_id += 1
        return result

    def orders(self, window, commands):
        if type(window) is not int or window < 0 or not isinstance(commands, (list, tuple)) or len(commands) > 256:
            raise ValueError('invalid strategy window or command count')
        self._send({'type': 'orders', 'window': window, 'orders': list(commands)})


class SimpleBot:
    """Minimal observation-only reference policy (development, not a tier bot).

    It keeps every laboratory busy: validates a finished theory, then focuses
    the lowest-tier researchable node not yet known. When the capital's food
    stock falls below a floor it bids for food on the public market. It reads
    only the init catalog and its own filtered observation.
    """
    FOOD_FLOOR_MU = 400000

    def __init__(self, catalog):
        tech = catalog.get('technology', {})
        nodes = tech.get('technologies', ()) if hasattr(tech, 'get') else ()
        self.nodes = {}
        for row in nodes:
            if hasattr(row, 'get') and 'id' in row and 'tier' in row:
                self.nodes[row['id']] = row
        self.next_id = 1

    def _cmd(self, kind, op, **fields):
        row = {'id': str(self.next_id), 'kind': kind, 'op': op} | fields
        self.next_id += 1
        return row

    def _researchable(self, tid, known):
        row = self.nodes[tid]
        need_all = set(row.get('requires_all') or ())
        need_any = set(row.get('requires_any') or ())
        return tid not in known and need_all <= known and (not need_any or bool(need_any & known))

    def decide(self, obs):
        orders = []
        research = obs.get('research', {})
        known = set(research.get('known', ()))
        projects = research.get('projects', {})
        focus = research.get('focus', {})
        validating = {j['tech_id'] for j in research.get('validation', ()) if j.get('active')}
        busy_labs = {j['lab_id'] for j in research.get('validation', ()) if j.get('active')}
        claimed = set(focus.values())
        for line in obs.get('lines', ()):
            if line.get('recipe') != 'P075' or line.get('under_construction'):
                continue
            lid = line['id']
            current = focus.get(lid)
            project = projects.get(current, {}) if current else {}
            if current and project.get('theory_complete') and not project.get('verified') \
                    and current not in validating and lid not in busy_labs:
                orders.append(self._cmd('research', 'validate_project', line_id=lid, tech_id=current))
                continue
            if lid in busy_labs:
                continue
            if current is None or current in known or project.get('verified'):
                options = sorted((self.nodes[t]['tier'], t) for t in self.nodes
                                 if self._researchable(t, known) and (t not in claimed or t == current))
                if options:
                    choice = options[0][1]
                    claimed.add(choice)
                    orders.append(self._cmd('research', 'set_focus', line_id=lid, tech_id=choice, sec_mode='standard'))
        capital = next((row[0] for row in obs.get('inventory', ()) if len(row) == 4 and str(row[0]).endswith('-h0')), None)
        food = sum(row[3] for row in obs.get('inventory', ())
                   if len(row) == 4 and row[1] == 'available' and row[2] == 'food' and row[0] == capital)
        if capital is not None and food < self.FOOD_FLOOR_MU and obs.get('window', 0) % 2 == 0:
            orders.append(self._cmd('logistics', 'market_bid', region_market=capital, good='food',
                                    qty_mU=self.FOOD_FLOOR_MU - food, limit_price_mU=60000, expire_cycles=1))
        return orders[:64]


def run_simple_bot():
    """Entry point for a submission: python3 -c 'import sdk; sdk.run_simple_bot()'."""
    client = Client()
    init = client.receive()
    bot = SimpleBot(init.get('catalog', {}))
    client.ready()
    while True:
        message = client.receive()
        if message.get('type') == 'end':
            return
        client.orders(message['window'], bot.decide(message))
