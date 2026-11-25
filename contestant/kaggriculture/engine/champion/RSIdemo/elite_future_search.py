"""Use spare normal callbacks to refine tomorrow's physically checked tails."""
from collections import Counter
from time import perf_counter
from .elite_base import gpt6_rules as rules
from .elite_base.project_model import CropDP,Project,animal_project
from .elite_base.portfolio import shadows
from .elite_structure import isolated_clone
from .elite_extensions import remember


class FutureSearch:
    def __init__(self,obs,model):
        self.day=model.day;self.model=isolated_clone(model)
        self.saved=remember(obs,model.projects)
        self.positions=sorted(self.saved['expected']);self.cursor=0;self.pass_number=0
        self.dp=None;self.prices=None;self.labor=None;self.refresh_at=-1
        self.metrics=Counter();self.stalled_passes=0;self.pass_accepted=0

    def step(self,deadline,max_proposals=12):
        if self.day>=28 or self.stalled_passes>=2 or not self.positions:return
        started=perf_counter();processed=0;m=self.model;tomorrow=self.day+1
        while processed<max_proposals and perf_counter()<deadline:
            if self.cursor>=len(self.positions):
                self.stalled_passes=self.stalled_passes+1 if self.pass_accepted==0 else 0
                self.pass_number+=1;self.cursor=0;self.pass_accepted=0
                self.positions.reverse();self.dp=None
                if self.stalled_passes>=2:break
            if self.dp is None or m.accepted>=self.refresh_at+4:
                self.prices=m.book.marginal(m.flow)
                self.labor=[v*f for v,f in zip(shadows(m.priced_work()),m.labor_multiplier)]
                self.dp=CropDP(self.prices,self.labor,[2.]*30,start=tomorrow)
                self.refresh_at=m.accepted
                if perf_counter()>=deadline:break
            pos=self.positions[self.cursor];self.cursor+=1;processed+=1
            tile=self.saved['expected'].get(pos)
            if tile=='LOCKED':continue
            if isinstance(tile,dict) and 'animal' in tile:
                tail=animal_project(tile['animal'],tile['placed_day'],tomorrow,tile,
                                    self.prices,self.labor,[2.]*30)
            elif isinstance(tile,dict) and tile.get('kind')=='PLANT':
                tail=self.dp.existing(tile,tomorrow)
            else:
                tail=self.dp.free[tomorrow]
                if tile is not None and tail.first(tomorrow)[1]:
                    row=tail.first(tomorrow)
                    tail=Project(tail.value,((tomorrow,(('DIG',),)+row[1],row[2],row[3]),)
                        +tuple(r for r in tail.days if r[0]!=tomorrow))
            original=m.projects[pos]
            prefix=tuple(r for r in original.days if r[0]<=self.day)
            proposal=Project(tail.value,prefix+tail.days)
            assert proposal.first(self.day)==original.first(self.day)
            self.metrics['proposed']+=1
            if m.offer(pos,proposal):
                self.metrics['accepted']+=1;self.pass_accepted+=1
                self.saved['columns'][pos]=tail
        self.metrics['calls']+=1;self.metrics['seconds']+=perf_counter()-started

    def continuation(self):return self.saved


def offer_proposals(model,obs,saved):
    """Reprice yesterday's computed columns after ordinary planning finishes.

    This differs from unconditional warm starting: the normal baseline has
    already been built and certified. Reject physical mismatches, and use the
    current day's market book for every acceptance. The returned candidate
    still needs the caller's ordinary cash and complete-route certification.
    """
    started=perf_counter();stats={'matched':0,'rejected':0,'accepted':0}
    if not saved or saved['day']!=model.day:
        return None,dict(stats,seconds=perf_counter()-started,gate='no current proposal')
    trial=isolated_clone(model)
    for pos,column in saved['columns'].items():
        if pos not in model.projects or model.farm['tiles'][pos[1]][pos[0]]!=saved['expected'][pos]:
            stats['rejected']+=1;continue
        stats['matched']+=1
        if trial.offer(pos,column):stats['accepted']+=1
    stats.update(seconds=perf_counter()-started,gain=trial.value-model.value,
                 gate='current book and route check pending')
    return (trial if stats['accepted'] else None),stats
