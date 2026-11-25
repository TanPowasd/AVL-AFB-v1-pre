"""Generate the command-surface record from the LIVE registry (``EngineCommands.dispatch``).

Writes ``organizer/rift_era/COMMAND_SURFACE.md`` (organizer), the public
``kit/COMMANDS.md`` (copied into the contestant kit) and a timestamped
``validation/command_surface_<UTC>.json``. Field descriptions are quoted from
``organizer/rift_era/designs/06_agent_interface.md`` where a row exists; this is a
registration record, not semantic acceptance.

    python3 -m organizer.rift_era.pipeline.command_surface
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time

RIFT_ROOT = Path(__file__).resolve().parents[1]
REPO = RIFT_ROOT.parents[1]
DESIGNS = (RIFT_ROOT / 'designs') if (RIFT_ROOT / 'designs').is_dir() else (REPO / 'designs' / 'rift_era')
DOC_06 = DESIGNS / '06_agent_interface.md'
KEY = re.compile(r'^`?([a-z_]+)`?\s*/\s*`?([a-z_0-9]+)`?$')
CELL_SPLIT = re.compile(r'(?<!\\)\|')
SPECIAL = {('cancel', 'cancel_by_id'): 'EngineCommands.prepare (built-in)'}

# Authoritative live-engine overrides for rows missing or outdated in 06_agent_interface.md.
FIELD_OVERRIDES: dict[tuple[str, str], dict[str, str]] = {
    ('ai', 'deploy'): {
        'fields': '`hub_id: str`, `lines: list[str]`（必填，可为 `[]`，非空时须全在 `hub_id` 且可自动化）, '
                  '`research: int`, `production: int`, `finance: int`, `cyber: int`, `market: int`, '
                  '`align: int`, `grid: int`（各为非负算力 `mU`）, `align_engineers_mU?: int (>=0)`, `replaces_id?: str`',
        'semantics': '与本 Hub 的 `train.alloc_compute_mU` 合计 ≤ Cbar[hub_id]（超上限返回 `ERR_CAPACITY_EXCEEDED`）；'
                     '无对应里程碑的项须为 `0`（`align_engineers_mU > 0` 需 `AI03`），否则返回 `ERR_TECH_LOCKED`；'
                     '持续计划，可用 `replaces_id` 原位替换',
    },
    ('ai', 'license_accept'): {
        'fields': '`seller: str`, `license_kind: "weights"|"data"`, `max_price_ru_mU: int (>0)`',
        'semantics': '需 `AI01`；接受卖方挂牌的权重或人工精标数据授权（数据授权同来源冷却 `100` 周期，按来源国去重高水位计入）',
    },
    ('ai', 'license_offer'): {
        'fields': '`license_kind: "weights"|"data"`, `price_ru_mU: int (>=0)`, `target: str|null`, `replaces_id?: str`',
        'semantics': '需 `AI01`；持续挂牌（可用 `replaces_id` 替换）；数据授权仅限本国人工精标数据，权重授权须已有激活模型',
    },
    ('diplomacy', 'city_state_aid'): {
        'fields': '`city_state_id: str`, `good: "food"|"water"|"medicine"|"consumer_goods"`, `qty_mU: int (>0)`',
        'semantics': '将已运抵该城邦枢纽的本国国有可用民生库存无偿援助给中立城邦，居民消耗后计入亲和度与援助指标',
    },
    ('diplomacy', 'leave_joint_venture'): {
        'fields': '`venture_id: str`',
        'semantics': '主动退出本国参与的既有合资企业（移除自身活跃成员资格，不再承担后续投入与分红）',
    },
    ('diplomacy', 'military_aid'): {
        'fields': '`recipient: str`, `hub_id: str`, `good: str`（军品或 `food`/`water`/`fuel`/`battery`/`medical_supplies`）, `qty_mU: int (>0)`',
        'semantics': '向存在待履行共同防御义务（`defense_obligations` 状态 `PENDING`）的受援国在其受控枢纽移交国有可用军需物资，受援方实际消耗后折算履约额度',
    },
    ('finance', 'fx_cover'): {
        'fields': '`currency: str`, `amount_mU: int (>0)`',
        'semantics': '需 `F03`；买入并归还指定外币空头负债，按归还比例释放保证金（同窗口累计申请不得超过剩余未平空头余额）',
    },
    ('finance', 'fx_short'): {
        'fields': '`currency: str`, `amount_mU: int (>0)`',
        'semantics': '需 `F03`；从世界池借入他国货币并当期卖出换取 RU（不得做空本币，预锁 `50%` RU 保证金，总空头不超过世界池期初余额 `25%`）',
    },
    ('industry', 'install_storage'): {
        'fields': '`facility_id: str`, `storage_kind: "battery"|"pumped"`, `expected_version?: int`',
        'semantics': '在已投产 `S2..S5` 设施空闲槽位安装储能（`battery` 需 `E04`、耗 `battery×8`、2 周期；`pumped` 需 `E02+L01` 与抽水地形、耗 `steel×30+cement×60+basic_parts×6`、30 周期）',
    },
    ('industry', 'refill_storage'): {
        'fields': '`facility_id: str`, `expected_version?: int`',
        'semantics': '对健康度 `< 800 mU` 的电化学储能（`battery`）更换电芯，消耗本地 `battery×2` 并将电池健康度恢复至 `1000 mU`',
    },
    ('military', 'interdict'): {
        'fields': '`formation_id: str`, `contact_id|shipment_track_id: str`',
        'semantics': '只接受本国当前可见（`q >= 0.2`，见观测 `strikes.shipment_contacts`）且在编队有效射程跳数内的在途接触别名，运输者及货主均须 `WAR`；共享射击预算，不打中立第三方',
    },
    ('military', 'reinforce'): {
        'fields': '`formation_id: str`, `unit_index: int`, `crew_mU: int (>0)`, `expected_version?: int`',
        'semantics': '为驻停本国受控枢纽编队的指定单元征召补充兵员；人员先转入训练池，训毕且编队仍在同枢纽时入列补编',
    },
    ('trade', 'city_reexport'): {
        'fields': '`city_state_id: str`, `good: str`, `qty_mU: int (>0)`, `price_ru_mU: int`（须等于 `10 * w_cat * 1000`）',
        'semantics': '通过中立城邦转口挂单采购受控货物，报价须严格等于城邦固定公开挂牌价（`10 * w_cat * 1000` mU RU）',
    },
}


def registry() -> dict[tuple[str, str], dict]:
    from ..engine.catalog import Catalog
    from ..engine.commands import EngineCommands
    from ..engine.initialization import initialize
    from ..engine.match import match_economy
    from ..engine.world import generate_candidate
    catalog = Catalog()
    engine = match_economy(catalog, initialize(catalog, generate_candidate(1)), total_ticks=40,
                           fingerprint_each_tick=False)
    commands = EngineCommands(engine.state)
    rows = {}
    for key, handler in commands.dispatch.items():
        cls = type(handler)
        rows[tuple(key)] = {'handler': cls.__name__, 'module': cls.__module__.rsplit('.', 1)[-1]}
    for key, where in SPECIAL.items():
        rows.setdefault(key, {'handler': where, 'module': 'commands'})
    return rows


def documented_fields(path: Path = DOC_06) -> dict[tuple[str, str], dict]:
    found: dict = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in CELL_SPLIT.split(line.strip())[1:-1]]
        if len(cells) < 3:
            continue
        match = KEY.match(cells[0])
        if match and match.group(1) != 'kind' and (match.group(1), match.group(2)) not in found:
            found[(match.group(1), match.group(2))] = {'fields': cells[1], 'semantics': cells[2]}
    return found


def build() -> dict:
    from ..engine import ENGINE_VERSION
    from ..engine.catalog import Catalog
    from ..engine.determinism import engine_source_fingerprint
    live = registry()
    docs = documented_fields()
    commands = []
    for kind, op in sorted(live):
        doc = docs.get((kind, op), {})
        override = FIELD_OVERRIDES.get((kind, op))
        fields_text = override['fields'] if override else doc.get('fields')
        semantics_text = override['semantics'] if override else doc.get('semantics')
        commands.append({'kind': kind, 'op': op, **live[(kind, op)],
                         'fields_06': fields_text, 'semantics_06': semantics_text})
    catalog = Catalog()
    return {'scope': 'handler_registration_only_not_semantic_acceptance', 'official': False,
            'generated_utc': utc_stamp(),
            'engine_version': ENGINE_VERSION, 'engine_hash': engine_source_fingerprint(),
            'rules_version': catalog.version, 'rules_hash': catalog.fingerprint,
            'count': len(commands), 'by_kind': {k: sum(1 for c in commands if c['kind'] == k)
                                                for k in sorted({c['kind'] for c in commands})},
            'documented_in_06_without_handler': sorted(f'{k}/{o}' for k, o in docs if (k, o) not in live),
            'commands': commands}


def utc_stamp() -> str:
    t = time.gmtime()
    return f'{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z'


def _cell(text) -> str:
    # 06 cells already escape their own pipes; only bare ones need escaping.
    return CELL_SPLIT.sub('\\|', text or '—').replace('\n', ' ')


def markdown(data: dict, *, public: bool) -> str:
    title = '裂陆纪元命令表（选手版）' if public else '裂陆纪元命令注册面（command surface）'
    lines = [f'# {title}', '',
             f'由 `pipeline/command_surface.py` 从引擎实时注册表生成（{data["generated_utc"]}）。'
             f'引擎 `{data["engine_version"]}`，规则 `{data["rules_version"]}`；共 **{data["count"]}** 条 `kind/op`。',
             '', '字段以实时引擎校验与 `rules/06_agent_interface.md` 为准；本表已覆盖全部实时注册命令的准确参数名。', '',
             '每条命令都是 JSON 对象：`{"id": "唯一递增字符串", "kind": ..., "op": ..., 其余字段}`；'
             '每窗口最多 256 条，单行 ≤ 256 KiB。被拒绝的命令只影响它自己（返回 ERR_* 码），不扣分也不影响其他命令。', '',
             '| kind | 数量 |', '|---|---|']
    lines += [f'| `{k}` | {n} |' for k, n in data['by_kind'].items()]
    lines += ['', '| # | kind/op | 字段（06） | 语义（06） |' + ('' if public else ' 处理器 |'),
              '|---|---|---|---|' + ('' if public else '---|')]
    for i, row in enumerate(data['commands'], 1):
        line = f'| {i} | `{row["kind"]}/{row["op"]}` | {_cell(row["fields_06"])} | {_cell(row["semantics_06"])} |'
        if not public:
            line += f' `{row["module"]}.{row["handler"]}` |'
        lines.append(line)
    if not public:
        lines += ['', f'- 引擎指纹：`{data["engine_hash"]}`', f'- 规则指纹：`{data["rules_hash"]}`',
                  '- 06 文档有行但引擎未注册：' + (', '.join(f'`{x}`' for x in data['documented_in_06_without_handler'])
                                            or '无')]
    return '\n'.join(lines) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-json', action='store_true')
    args = parser.parse_args(argv)
    data = build()
    (RIFT_ROOT / 'COMMAND_SURFACE.md').write_text(markdown(data, public=False), encoding='utf-8')
    (RIFT_ROOT / 'kit' / 'COMMANDS.md').write_text(markdown(data, public=True), encoding='utf-8')
    if not args.no_json:
        stamp = data['generated_utc'].replace('-', '').replace(':', '')
        path = RIFT_ROOT / 'validation' / f'command_surface_{stamp}.json'
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print(path)
    print(json.dumps({'count': data['count'], 'by_kind': data['by_kind'],
                      'documented_without_handler': data['documented_in_06_without_handler']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
