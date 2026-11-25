"""Small response game over complete plans and public-state rival hypotheses.

Rival policies are hypothetical economic best responses, not observations of
hidden inventory or calibrated win probabilities. Each compared own plan uses
the same response columns and town scenarios, including both sides' costs.
"""
import copy
import ctypes
from time import perf_counter
from .elite_base import gpt6_rules as rules
from .elite_base.portfolio import labor_cost
from .elite_base.gpt6_policy import fibcost
from .elite_shadow import ShadowBook,ShadowPortfolio,rival_observation,check_deadline,ShadowBudgetExhausted


def bind_supply(book,flow):
    """Return an isolated book with fixed rival supply and unchanged towns."""
    result=copy.copy(book);result._parts={};result._marginals={};result._native_inputs={}
    result.scenarios=[(demands,{k:list(flow[k]) for k in flow}) for demands,_ in book.scenarios]
    result._scenario_inputs={k:[(i%3,tuple(int(round(v)) for v in rival[k]),
                               tuple(int(demands[d][k]) for d in range(30)))
                             for i,(demands,rival) in enumerate(result.scenarios)] for k in flow}
    if result._native_enabled:
        for item,scenarios in result._scenario_inputs.items():
            rivals=[v for _,rival,_ in scenarios for v in rival]
            demands=[v for _,_,demand in scenarios for v in demand]
            phases=[phase for phase,_,_ in scenarios]
            result._native_inputs[item]=((ctypes.c_int64*len(rivals))(*rivals),
                (ctypes.c_int64*len(demands))(*demands),(ctypes.c_int32*len(phases))(*phases))
    return result


class ResponseBook(ShadowBook):
    def __init__(self,obs):
        super().__init__(obs)
        fixed=obs.get('_response_supply')
        if fixed is not None:self.__dict__.update(bind_supply(self,fixed).__dict__)


class ResponsePortfolio(ShadowPortfolio):
    book_type=ResponseBook


def respond(obs,own_model,deadline):
    other=rival_observation(obs,obs.get('_inventory_belief'))
    fixed={k:v[:] for k,v in own_model.flow.items()}
    for k,n in own_model.book.initial.items():fixed[k][own_model.day]+=n
    # Stress response to a candidate production calendar. The real opponent
    # does not get our private state or an announced future action sequence.
    other.update(_shadow_deadline=deadline,_response_supply=fixed)
    farm=other['farms'][other['player']]
    positions=[(x,y) for y,row in enumerate(farm['tiles']) for x,t in enumerate(row) if t!='LOCKED']
    model=ResponsePortfolio(other,positions)
    herd=sum(isinstance(t,dict) and 'animal' in t for row in farm['tiles'] for t in row)
    budget=max(0.,farm['money']-fibcost(8)-herd*other['market']['prices']['WHEAT']-100)
    model.optimize(budget,sweeps=1);check_deadline(deadline)
    return model


def scenario_payoffs(obs,own_model,crew,responses,return_matrix=False):
    """Every row includes initial cash and investment/labor on both sides."""
    day=own_model.day;cash=obs['farms'][obs['player']]['money']-obs['farms'][1-obs['player']]['money']
    work=own_model.priced_work();work[day]=0.
    own_cost=own_model.cost-getattr(own_model,'capital_credit',lambda:0)()+labor_cost(work)+fibcost(crew)
    matrix=[]
    for response in responses:
        supply={k:v[:] for k,v in response.flow.items()}
        for k,n in response.book.initial.items():supply[k][day]+=n
        book=bind_supply(own_model.book,supply)
        rival_cost=response.cost+labor_cost(response.priced_work())
        values=[cash-own_cost+rival_cost]*len(book.scenarios)
        for item in supply:
            component,_=book.component(item,own_model.flow[item])
            values=[a+b for a,b in zip(values,component)]
        matrix.append(values)
    # Same response set for every own row. A conservative envelope is an
    # explicit stress score; scenario-positive counts are not real win rates.
    if return_matrix:
        return matrix
    return [min(column[s] for column in matrix) for s in range(len(matrix[0]))]


def summarize(values):
    ordered=sorted(values)
    return dict(positive=sum(v>0 for v in values),scenarios=len(values),
                mean=sum(values)/len(values),worst=ordered[0],lower=ordered[min(1,len(ordered)-1)])


def compare(obs,models,crews,deadline):
    started=perf_counter();report={'completed':False,'responses':0}
    try:
        responses=[]
        for model in models:
            check_deadline(deadline);responses.append(respond(obs,model,deadline));report['responses']+=1
        scores=[];response_matrices=[]
        for model,crew in zip(models,crews):
            check_deadline(deadline)
            matrix=scenario_payoffs(obs,model,crew,responses,return_matrix=True)
            response_matrices.append(matrix)
            scores.append([min(column[s] for column in matrix) for s in range(len(matrix[0]))])
        check_deadline(deadline)
        report.update(completed=True,scores=scores,summaries=[summarize(row) for row in scores],
            assumption='public rival farm, legal belief mean stocks, unknown seed/animal stocks zero; one-pass hypothetical responses; daily trade dates')
        params=obs.get('_rsi_params',{})
        if params.get('elite_cfr'):
            # Aggregate each explicit response over the lower-tail scenario
            # value. This keeps CFR a response-game ranking signal rather than
            # treating hypothetical scenario positives as win probabilities.
            def robust(values):
                values=sorted(values)
                index=max(0,min(len(values)-1,int((len(values)-1)*.2)))
                return .75*(sum(values)/len(values))+.25*values[index]
            cfr_matrix=[[robust(row) for row in response_matrices[index]]
                        for index in range(len(response_matrices))]
            from .cfr import rank_candidates
            report['cfr']=rank_candidates(cfr_matrix,['baseline','candidate'],
                [f'response_{i}' for i in range(len(responses))],
                iterations=int(params.get('elite_cfr_iterations',256)),
                risk_penalty=float(params.get('elite_cfr_risk_penalty',.25)))
            report['cfr']['enabled']=True
    except ShadowBudgetExhausted:report['gate']='response deadline; original choice retained'
    report['seconds']=perf_counter()-started
    return report


def prefer_candidate(report,minimum_mean_gain=100.):
    if not report.get('completed'):return True
    a,b=report['summaries']
    cfr=report.get('cfr')
    if cfr and cfr.get('enabled'):
        rows={row['name']:row for row in cfr['candidates']}
        candidate=rows.get('candidate');baseline=rows.get('baseline')
        # A failed CFR gate must not fall through to the old scenario-count
        # rule, which can accept a candidate rejected by the response game.
        return bool(candidate and baseline
                    and candidate['score']>baseline['score']+minimum_mean_gain
                    and candidate['worst']>=baseline['worst']-500)
    # Respect modeled scenario wins first, then require a material margin.
    return b['positive']>a['positive'] or (b['positive']==a['positive'] and b['mean']>a['mean']+minimum_mean_gain)
