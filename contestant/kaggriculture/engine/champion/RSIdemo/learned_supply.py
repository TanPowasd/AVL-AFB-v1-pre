"""Nearest public-prefix opponent supply residuals fitted offline.

Future labels exist only in a frozen training file. Online selection receives
public current state, never current-game private stock/actions or hidden seed.
"""
from collections import Counter
from functools import lru_cache
import json
import math
from pathlib import Path
from . import rules

ITEMS=tuple(rules.PRODUCTS)
COHORTS=tuple(rules.CROPS)+tuple(rules.ANIMALS)
SHOPS=tuple(sorted(rules.SHOPS))


def features(obs,player):
    farm=obs['farms'][player];counts=Counter()
    for row in farm['tiles']:
        for tile in row:
            if isinstance(tile,dict):counts[tile.get('crop',tile.get('animal',''))]+=1
    shops=Counter(obs['town']['unlocked_shops'])
    return ([shops[k]*1.5 for k in SHOPS]+[counts[k]/10 for k in COHORTS]
            +[(obs['market']['inventory'][k]-10000)/rules.MARKET_PARAMS[k]['T'] for k in ITEMS]
            +[len(farm['unlocked_quadrants']),math.log1p(max(0,farm['money']))/3])


@lru_cache(maxsize=1)
def model():
    path=Path(__file__).resolve().with_name('supply_model.json')
    if not path.exists():return None
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('items')!=list(ITEMS) or data.get('schema')!=1:raise ValueError('Unrecognized supply model')
    return data


def select(obs,player,limit=6):
    data=model()
    if data is None:return []
    day=obs['step']//24;x=features(obs,player)
    rows=[r for r in data['rows'] if r['day']==day]
    # At most one neighbor per training game group prevents both seats of one
    # game path from masquerading as independent uncertainty scenarios.
    rows.sort(key=lambda r:sum((a-b)**2 for a,b in zip(x,r['x'])))
    chosen=[];groups=set()
    for row in rows:
        if row['group'] in groups:continue
        groups.add(row['group']);chosen.append(row)
        if len(chosen)>=limit:break
    return chosen
