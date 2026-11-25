"""Public-prefix neighbor scenarios fitted from archived elite replays.

Runtime selection only sees the current public feature vector. Future shops and
trade residuals belong to frozen historical training episodes, never this game.
"""
import copy
from functools import lru_cache
import json
from pathlib import Path

from .elite_base.inventory_bank import COHORTS, ITEMS, SHOP_NAMES, features
from .elite_base.inventory_bank import select as original_select

MODEL_PATH=Path(__file__).resolve().with_name('elite_supply_model.json')


@lru_cache(maxsize=1)
def load_model():
    if not MODEL_PATH.exists():return None
    model=json.loads(MODEL_PATH.read_text(encoding='utf-8'))
    if (model.get('schema')!=1 or model.get('items')!=list(ITEMS)
            or model.get('cohorts')!=list(COHORTS) or model.get('shops')!=list(SHOP_NAMES)):
        raise ValueError('Incompatible elite replay bank feature or item schema')
    if model.get('mode')!='full':raise ValueError('Probe replay models cannot be used online')
    train=set(model['training_episode_ids']);holdout=set(model['holdout_episode_ids'])
    if train&holdout or any(row['episode'] not in train for row in model['rows']):
        raise ValueError('Elite replay bank includes a held-out or unknown episode')
    if set(model.get('fitted_episode_ids',[]))!=train:
        raise ValueError('Elite replay bank does not contain all frozen training episodes')
    expected={(episode,day) for episode in train for day in range(15,30)}
    actual=[(row['episode'],row['day']) for row in model['rows']]
    if set(actual)!=expected or len(actual)!=len(expected):
        raise ValueError('Elite replay bank needs exactly one row per training episode and day')
    return model


def nearest_rows(obs,p,rows,limit=6):
    """Select same-day rows, at most one per historical episode; no seed input."""
    if limit<=0:return []
    day=int(obs['step'])//24;x=features(obs,p)
    matches=[r for r in rows if r['day']==day]
    if any(len(row['x'])!=len(x) for row in matches):
        raise ValueError('Replay feature vector length mismatch')
    matches=sorted(matches,
                   key=lambda r:sum((a-b)**2 for a,b in zip(x,r['x'])))
    selected=[];seen=set()
    for row in matches:
        if row['episode'] in seen:continue
        seen.add(row['episode']);selected.append(copy.deepcopy(row))
        if len(selected)>=limit:break
    return selected


def select(obs,p,limit=6):
    model=load_model()
    if model is None:return original_select(obs,p,limit)
    rows=nearest_rows(obs,p,model['rows'],limit)
    return rows if rows else original_select(obs,p,limit)
