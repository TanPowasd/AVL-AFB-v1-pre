"""Summarize runner JSON outputs into the CALIBRATION.md tables (development tool).

usage: python3 calibration_table.py [--json validation.json] run1.json run2.json ...
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ('super', 'advanced', 'medium', 'simple', 'none')
FILES = ('main.py', 'bot.py', 'sdk.py')


def tier_hashes():
    rows = {}
    for tier in ('medium', 'advanced', 'super'):
        files = {name: hashlib.sha256((HERE / tier / name).read_bytes()).hexdigest() for name in FILES}
        combined = hashlib.sha256()
        for name in FILES:
            combined.update((HERE / tier / name).read_bytes())
        version = next((line.split('=', 1)[1].strip().strip("'\"") for line in
                        (HERE / tier / 'bot.py').read_text(encoding='utf-8').splitlines()
                        if line.startswith('VERSION')), '?')
        rows[tier] = {'version': version, 'combined_sha256': combined.hexdigest(), 'files_sha256': files}
    return rows


def tier_means(data):
    by_tier = {}
    for n in data['nations'].values():
        by_tier.setdefault(n['tier'], []).append(n)
    out = {}
    for tier, rows in by_tier.items():
        k = len(rows)
        series = [sum(r['total_every_100_ticks'][i] for r in rows) / k
                  for i in range(min(len(r['total_every_100_ticks']) for r in rows))] \
            if all('total_every_100_ticks' in r for r in rows) else []
        out[tier] = {
            'seats': k,
            'V': sum(r['V'] for r in rows) / k,
            'V_min': min(r['V'] for r in rows),
            'phi': sum(r['phi'] for r in rows) / k,
            'pillars': {p: sum(r['mean_pillars'][p] for r in rows) / k
                        for p in ('population', 'industry', 'control', 'technology', 'international', 'total')},
            'last_total': sum(r['last_pillars']['total'] for r in rows) / k,
            'decision_ms_mean': (sum(r.get('decision_ms_mean', 0) for r in rows) / k) if tier != 'none' else None,
            'decision_ms_max': max((r.get('decision_ms_max', 0) for r in rows), default=0) if tier != 'none' else None,
            'total_every_100_ticks': [round(x, 3) for x in series],
        }
    return out


def ordered(means):
    tiers = [t for t in ORDER if t in means]
    v = [means[t]['V'] for t in tiers]
    strict = all(a > b for a, b in zip(v[:3], v[1:3]))  # super > advanced > medium
    return strict and means.get('medium', {}).get('V', 0) > max(means.get('simple', {}).get('V', 0),
                                                                    means.get('none', {}).get('V', 0))


def summarize(path: Path) -> str:
    data = json.loads(path.read_text(encoding='utf-8'))
    head = (f"**{path.stem}** — map `{data['map_version']}`, seed {data['seed']}, {data['ticks']} ticks, "
            f"sim {data['sim_seconds_per_tick']} s/tick, world acceptance {data['world_acceptance_seconds']} s\n\n")
    lines = ['| 席位 | 档 | V | φ | 人口 | 工业 | 控制 | 科技 | 国际 | 总分(周期均) | 末周期总分 | 决策 ms 均/最大 |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    nations = data['nations']
    for owner in sorted(nations, key=lambda p: (ORDER.index(nations[p]['tier']), p)):
        n = nations[owner]
        m = n['mean_pillars']
        timing = (f"{n['decision_ms_mean']:.2f} / {n['decision_ms_max']:.1f}" if 'decision_ms_mean' in n else '—')
        lines.append(f"| {owner} | {n['tier']} | {n['V']:.3f} | {n['phi']:.4f} | {m['population']:.2f} | "
                     f"{m['industry']:.2f} | {m['control']:.2f} | {m['technology']:.2f} | {m['international']:.2f} | "
                     f"{m['total']:.3f} | {n['last_pillars']['total']:.3f} | {timing} |")
    means = tier_means(data)
    text = ' · '.join(f"{t} {means[t]['V']:.3f}" for t in ORDER if t in means)
    series = '\n'.join(f"- {t}: {means[t]['total_every_100_ticks']}" for t in ORDER if t in means
                       and means[t]['total_every_100_ticks'])
    return (head + '\n'.join(lines) + f'\n\n档均 V：{text}（顺序 {"✓" if ordered(means) else "✗"}）\n\n'
            f'每 100 tick 的周期总分（档均）：\n{series}\n')


def validation(paths):
    runs = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        means = tier_means(data)
        runs.append({'file': Path(path).name, 'map_version': data['map_version'], 'seed': data['seed'],
                     'ticks': data['ticks'], 'sim_seconds_per_tick': data['sim_seconds_per_tick'],
                     'seats': {p: n['tier'] for p, n in sorted(data['nations'].items())},
                     'tiers': means, 'ordered': ordered(means),
                     'nations': {p: {k: n[k] for k in ('tier', 'V', 'phi', 'mean_pillars', 'last_pillars')
                                     if k in n} | {k: n[k] for k in ('decision_ms_mean', 'decision_ms_max') if k in n}
                                 for p, n in sorted(data['nations'].items())}})
    return {'official': False, 'note': 'development runner (bots/runner.py inproc), never an official score',
            'bots': tier_hashes(), 'runs': runs, 'all_ordered': all(r['ordered'] for r in runs)}


if __name__ == '__main__':
    args = sys.argv[1:]
    out = None
    if args[:1] == ['--json']:
        out, args = Path(args[1]), args[2:]
    print('哈希：', json.dumps({t: r['combined_sha256'][:16] for t, r in tier_hashes().items()}))
    for arg in args:
        print(summarize(Path(arg)))
    if out:
        out.write_text(json.dumps(validation(args), ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
