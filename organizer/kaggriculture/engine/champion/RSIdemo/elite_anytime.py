"""Spend spare dawn time on certified multi-start joint proposals.

Keep the incumbent from the inherited search, including its exact route
certificate. Sample only complete project columns from the already built
economic DP; every improvement must pass cash, operation and route checks.
No new future-state prediction or unconditional continuation is introduced.
"""
import copy
from time import perf_counter
from .elite_base.elite_block import search,economic_without_today_wage
from .elite_base.gpt6_policy import fibcost


def improve(planner,obs,model,options,incumbent,best_gain,budget,baseline):
    started=perf_counter();params=obs['_rsi_params']
    allocate=obs.get('_rsi_search_envelope')
    report={'restarts':0,'evaluations':0,'certifications':0,'accepted':0,'branches':[],
            'initial_gain':best_gain,'seconds':0.}
    if allocate is None:
        report['gate']='no budget allocator';return incumbent,best_gain,report
    envelope=allocate();deadline=envelope['search']
    obs['_rsi_response_deadline']=max(obs.get('_rsi_response_deadline',0),envelope['finish'])
    report['allocation']={k:v for k,v in envelope.items() if k not in ('search','finish')}
    seen=set();no_gain=0
    max_restarts=int(params.get('elite_anytime_restarts',32))
    for iteration in range(max_restarts):
        if perf_counter()+.15>=deadline:break
        start_model=incumbent[0] if incumbent is not None and iteration%2==0 else model
        # The zeroth column must be the actual current plan for the search
        # delta accounting. Include inherited structural starts as well.
        rebased={pos:[p]+[q for q in options[pos] if q.days!=p.days]
                 for pos,p in start_model.projects.items()}
        temperature=(0,150,500,50)[iteration%4]
        additional=[]
        candidate,receipt=search(start_model,rebased,
            evaluations=int(params.get('elite_anytime_batch',800)),temperature=temperature,
            seed=7919+iteration*101,finalists=3,candidates_out=additional,
            deadline=deadline-.12)
        report['restarts']+=1;report['evaluations']+=receipt['evaluations']
        report['branches'].append({k:receipt[k] for k in ('gain','evaluations','temperature','search_seed','seconds')})
        changed=False
        for proposal in sorted([candidate,*additional],key=economic_without_today_wage,reverse=True):
            if perf_counter()+.10>=deadline:break
            signature=tuple((pos,p.days) for pos,p in proposal.projects.items())
            if signature in seen:continue
            seen.add(signature)
            trial=copy.copy(planner);trial.jobs=[]
            trial._build_jobs(obs,proposal,list(proposal.projects),budget);trial._requirements()
            if not planner.jobs_match(proposal,trial.jobs):continue
            current_crew=incumbent[2] if incumbent is not None else planner.crew
            for crew in dict.fromkeys((current_crew,max(1,current_crew-1),min(15,current_crew+1))):
                if perf_counter()+.08>=deadline:break
                gain=economic_without_today_wage(proposal)-fibcost(crew)-baseline
                if gain<=best_gain:continue
                certificate=trial.pack_block(obs,trial.jobs,crew,trial.delivery)
                report['certifications']+=1
                if not certificate['unassigned']:
                    incumbent=(proposal,trial,crew,certificate);best_gain=gain
                    changed=True;report['accepted']+=1
        no_gain=0 if changed else no_gain+1
        if no_gain>=8:break  # Stop when the finite column pool is exhausted.
    report.update(final_gain=best_gain,seconds=perf_counter()-started,
                  gate='certified improvement' if report['accepted'] else 'incumbent retained')
    return incumbent,best_gain,report
