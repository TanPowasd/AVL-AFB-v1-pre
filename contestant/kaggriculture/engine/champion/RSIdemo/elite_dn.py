"""One-shot D14/D15 handoff probe with an isolated opening continuation.

No hidden seed, rival private observation or recorded future action is used.
The one-day rival and later market responses are explicit hypotheses, not a
claim of exact opponent prediction. Both branches use the same legal belief.
"""
import copy
from time import perf_counter
from types import SimpleNamespace
from .elite_base import gpt6_rules as rules
from .elite_base.joint_policy import _opening_code
from .elite_base.joint_v2 import PORTFOLIO_REPORT
from .elite_shadow import rival_observation,check_deadline,ShadowBudgetExhausted
from .elite_response_game import respond,scenario_payoffs,summarize


class Attr(dict):
    def __getattr__(self,key):
        try:return self[key]
        except KeyError:raise AttributeError(key) from None
    __setattr__=dict.__setitem__


def isolated_opening(history,configuration,deadline):
    check_deadline(deadline)
    namespace={'__name__':'rsi_dn_isolated_opening'}
    exec(_opening_code,namespace)
    opening=namespace['agent']
    for index,obs in enumerate(history):
        if index%12==0:check_deadline(deadline)
        opening(copy.deepcopy(obs),configuration)
    check_deadline(deadline)
    return opening,namespace


def rollout_day(obs,configuration,opening,use_macro,params,deadline):
    from .elite_agent import EnhancedPlanner
    player=int(obs['player']);rival=1-player;day=int(obs['step'])//24
    public=copy.deepcopy(obs);belief_private=rival_observation(obs,obs.get('_inventory_belief'))['private']
    states=[]
    for p in (0,1):
        private=copy.deepcopy(obs['private']) if p==player else belief_private
        current=Attr(step=int(obs['step']),day=day,hour=0,player=p,
            farms=public['farms'],market=public['market'],town=public['town'],
            private=private,remainingOverageTime=60)
        states.append(SimpleNamespace(observation=current,action={},status='ACTIVE',reward=0))
    cfg=Attr(episodeSteps=720,boardSize=10,turnsPerDay=24,shedCapacity=100,
        maxMarketOrdersPerTurn=10,farmHandCostMult=1,weedSpawnChance=.005,
        townShopUnlockInterval=3,townShopSellInterval=4,townCenterSellInterval=24,
        marketParams={},actTimeout=1)
    env=SimpleNamespace(configuration=cfg,done=False,info={'seed':191931})
    own=EnhancedPlanner(dict(backend='elite',elite_structure=True)) if use_macro else None
    other=EnhancedPlanner(dict(backend='elite'))
    for step in range(day*24,(day+1)*24):
        check_deadline(deadline)
        for p in (0,1):
            current=states[p].observation;current.step=step;current.day=day;current.hour=step%24
            detached=copy.deepcopy(dict(current))
            if p==player:
                if use_macro:
                    detached['_rsi_structure_allowance']=.15
                    action=own.act(detached,configuration)
                else:action=opening(detached,configuration)
            else:action=other.act(detached,configuration)
            states[p].action=action
        rules.interpreter(states,env)
    check_deadline(deadline)
    after=copy.deepcopy(dict(states[player].observation))
    after.update(step=(day+1)*24,day=day+1,hour=0)
    # A real D15 shop is not known at D14. Let the ordinary market ensemble
    # express that uncertainty instead of trusting the artificial rollout seed.
    after['town']=copy.deepcopy(obs['town'])
    after['_inventory_belief']=copy.deepcopy(obs.get('_inventory_belief',{}))
    return after


def choose_now(obs,configuration,history,params,deadline):
    from .elite_agent import EnhancedPlanner
    started=perf_counter();report={'completed':False,'switch':False,'day':int(obs['step'])//24}
    original_reports=PORTFOLIO_REPORT['days'];PORTFOLIO_REPORT['days']=[]
    try:
        if int(obs['step'])!=14*24:raise ValueError('Dn prototype only probes at D14 dawn')
        if len(history)!=14*24 or history[0]['step']!=0 or history[-1]['step']!=14*24-1:
            report['gate']='missing own-observation history';return False,report
        opening,_=isolated_opening(history,configuration,deadline)
        report['history_seconds']=perf_counter()-started
        after_wait=rollout_day(obs,configuration,opening,False,params,deadline)
        report['wait_day_seconds']=perf_counter()-started-report['history_seconds']
        after_now=rollout_day(obs,configuration,None,True,params,deadline)
        report['now_day_seconds']=perf_counter()-started-report['history_seconds']-report['wait_day_seconds']
        planners=[]
        for after in (after_wait,after_now):
            check_deadline(deadline)
            planner=EnhancedPlanner(dict(backend='elite',elite_structure=True))
            planner.begin_day(dict(after,_rsi_structure_allowance=.15))
            planners.append(planner)
        # Evaluate both branches under the same sampled future demand paths.
        common_demands=[d for d,_ in planners[0].economic_model.book.scenarios]
        report['tail_planning_done_at']=perf_counter()-started
        rows=[]
        for after,planner in zip((after_wait,after_now),planners):
            check_deadline(deadline);model=planner.economic_model
            model.book=copy.copy(model.book)
            model.book.scenarios=[(d,{}) for d in common_demands]
            response=respond(after,model,deadline)
            rows.append(scenario_payoffs(after,model,planner.crew,[response]))
        check_deadline(deadline)
        wait,now=map(summarize,rows)
        gain=now['mean']-wait['mean']
        take=(now['positive']>wait['positive'] and gain>500 and now['lower']>=wait['lower']-500)
        take=take or (now['positive']==wait['positive'] and gain>1000 and now['lower']>=wait['lower'])
        report.update(completed=True,switch=take,wait=wait,now=now,mean_gain=gain,
            current_cash_after_wait=after_wait['farms'][obs['player']]['money'],
            current_cash_after_now=after_now['farms'][obs['player']]['money'],
            assumptions='one-day public-belief rival macro policy; artificial weed sample; future shops unknown; isolated own opening replay; hypothetical response terminal values')
    except ShadowBudgetExhausted:report['gate']='probe deadline; preserve D15'
    finally:
        PORTFOLIO_REPORT['days']=original_reports
        report['seconds']=perf_counter()-started
    return report['switch'],report
