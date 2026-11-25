"""Legal, reversible same-frame commodity inventory cycle candidates.

Candidate orders preserve our own final physical inventory under every tested
scenario. These are game-market simulations, not real financial transactions.
The opponent order set is a public-state hypothesis and is never a private read.
"""
from collections import Counter
import copy
from types import SimpleNamespace

from . import rules,budget


class Attr(dict):
    def __getattr__(self,key):return self[key]


def predict_input_demand(obs):
    day=obs['step']//24
    needs=Counter();fert_production=0
    for row in obs['farms'][1-obs['player']]['tiles']:
        for t in row:
            if not isinstance(t,dict):continue
            if 'animal' in t:
                needs['WHEAT']+=not t.get('fed_today',False)
                fert_production+=bool(t.get('fertilizer_available'))
            elif t.get('kind')=='PLANT':
                crop=t['crop'];cd=rules.CROPS[crop];age=day-t['planted_day']
                if cd['ongoing']:
                    since=age+1-cd['first_yield_day']
                    needs['FERTILIZER']+=int(since>=0 and since%cd['interval']==0 and
                        since//cd['interval']<cd['max_yield'] and t.get('fertilized_until_day',-1)<day)
                elif crop=='WHEAT' and age==2 and t.get('fertilized_until_day',-1)<day:
                    needs['FERTILIZER']+=1
    return needs,fert_production


def simulate(obs,farm,private,ours,theirs,opponent_stock,cfg):
    fs=copy.deepcopy(obs['farms']);fs[obs['player']]=copy.deepcopy(farm)
    market=copy.deepcopy(obs['market'])
    rival=rules._new_private();rival['shed'].update(opponent_stock)
    # A hypothesis must still satisfy the same capacity and cash rules.
    rival['inventories']=[{} for _ in range(len(fs[1-obs['player']]['hands'])+1)]
    prs=[None,None];prs[obs['player']]=copy.deepcopy(private);prs[1-obs['player']]=rival
    orders=[None,None];orders[obs['player']]=ours;orders[1-obs['player']]=theirs
    states=[SimpleNamespace(observation=Attr(farms=fs,private=prs[p],market=market),action={'market':orders[p]}) for p in (0,1)]
    rules._process_market(states,SimpleNamespace(configuration=cfg))
    return fs,prs


def optimize(planner,obs,farm,private,orders):
    hour=obs['step']%24
    if hour>2 or obs['step']<6*24 or obs['step']>=27*24:return orders
    limit=planner.cfg.get('maxMarketOrdersPerTurn',10)
    if len(orders)>limit-2:return orders
    needed,fert_supply=predict_input_demand(obs)
    if max(needed.values(),default=0)<=0:return orders
    player=obs['player'];prices=obs['market']['prices'];winner=orders;best=0.
    space=max(0,planner.cfg.get('shedCapacity',100)-sum(private['shed'].values()))
    free_cash=max(0,farm['money']-1000)
    items=sorted(('WHEAT','FERTILIZER'),key=lambda k:needed[k]*prices[k],reverse=True)
    for item in items:
        if needed[item]<=0:continue
        q=min(space,50,int(free_cash/max(1,prices[item]*1.3)))
        if q<4:continue
        # Place the reversing sale after all possible opposing order slots.
        candidate=[['BUY_PRODUCT',item,q],*copy.deepcopy(orders)]
        candidate+=[[] for _ in range(max(0,limit-1-len(candidate)))]
        candidate.append(['SELL',item,q])
        scenario_orders=[]
        # Prioritise input procurement now, one slot later, or no procurement.
        for prefix in (0,1,3):
            scenario_orders.append(([[]]*prefix+[['BUY_PRODUCT',item,needed[item]]],{},1.))
        scenario_orders.append(([],{},1.))
        # Adverse farmer-supply scenario carries nonzero weight.
        amount=max(1,int(fert_supply*.35)) if item=='FERTILIZER' else max(1,needed[item]//3)
        scenario_orders.append(([['SELL',item,amount]],{item:amount},.35))
        gains=[];own_gains=[];good=True
        for theirs,stock,weight in scenario_orders:
            if budget.remaining()<.015:return winner
            base=simulate(obs,farm,private,orders,theirs,stock,planner.cfg)
            trial=simulate(obs,farm,private,candidate,theirs,stock,planner.cfg)
            if trial[1][player]!=base[1][player]:good=False;break
            old_farm={k:v for k,v in base[0][player].items() if k!='money'}
            new_farm={k:v for k,v in trial[0][player].items() if k!='money'}
            if old_farm!=new_farm:good=False;break
            a=trial[0][player]['money']-base[0][player]['money']
            b=trial[0][1-player]['money']-base[0][1-player]['money']
            gains.append((a-b)*weight);own_gains.append(a*weight)
        if not good:continue
        # Tradeoff is explicit: expected margin benefit must exceed modeled
        # downside; no forced use of a merely loss-making price perturbation.
        score=sum(gains)/sum(x[2] for x in scenario_orders)+.25*min(gains)
        if planner.p.get('tactical_robust'):
            score=min(gains)
        if score>max(5,best) and sum(own_gains)>=0:
            winner=candidate;best=score
    if winner is not orders:
        planner.metrics['tactical_market_frames']+=1
        planner.metrics['tactical_expected_margin_cents']+=int(best*100)
    return winner
