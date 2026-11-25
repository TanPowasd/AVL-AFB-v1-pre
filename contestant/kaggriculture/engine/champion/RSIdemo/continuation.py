"""Reconcile yesterday's planned asset state before reusing future columns."""
from collections import Counter
import copy
from . import rules,budget
from .projects import Project


def remember(obs,projects):
    farm=copy.deepcopy(obs['farms'][obs['player']]);private=rules._new_private()
    day=obs['step']//24;prefix={}
    for pos,project in projects.items():
        x,y=pos
        if farm['tiles'][y][x]=='LOCKED':continue
        farm['farmer']=[x,y]
        private['inventories']=[{}]
        private['seeds']={k:0 for k in rules.CROPS}
        for k,n in project.first(day)[3]:
            if k.startswith('SEED_'):private['seeds'][k[5:]]=n
            elif k in rules.PRODUCTS or k in rules.ANIMALS:private['inventories'][0][k]=n
        for raw in project.first(day)[1]:
            rules._apply_unit_action(farm,private,0,list(raw),10,day,24)
        prefix[pos]=Project(project.value,tuple(row for row in project.days if row[0]>day))
    rules._daily_refresh_plants(farm,day,24);rules._daily_refresh_animals(farm,day)
    return {'day':day+1,'columns':prefix,'expected':{p:copy.deepcopy(farm['tiles'][p[1]][p[0]]) for p in prefix}}


def reuse(model,obs,saved):
    stats=Counter()
    if not saved or saved['day']!=obs['step']//24:return stats
    for pos,project in saved['columns'].items():
        budget.check()
        if pos not in model.projects:continue
        if model.farm['tiles'][pos[1]][pos[0]]!=saved['expected'][pos]:
            stats['rejected_state']+=1;continue
        stats['reconciled']+=1
        if model.offer(pos,project):stats['accepted']+=1
    return stats
