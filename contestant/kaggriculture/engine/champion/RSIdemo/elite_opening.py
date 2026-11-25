"""Isolated, optional correction of the embedded opening's animal estimate."""
from collections import Counter
from .elite_base.joint_policy import _opening, _opening_ns


def corrected_ev(option,k,obs,st,namespace,macro_day=15,fix_floor=True,fix_horizon=True,receipt=None):
    n=namespace;spec=n['_HD2_SPEC'][option];item=spec['product'];animal_of=option
    day=int(obs['step'])//24;seat=int(obs['player']);market=obs['market']
    params={key:dict(v) for key,v in n['_R37_MARKET_PARAMS'].items()}
    for key,patch in (market.get('params') or {}).items():
        if key in params and isinstance(patch,dict):params[key].update(patch)
    existing=[{},{}];schedule=n['_hd2_schedule']
    for side,farm in enumerate(obs['farms']):
        for row in farm['tiles']:
            for tile in row:
                if isinstance(tile,dict) and tile.get('animal')==animal_of:
                    for d,u in schedule(animal_of,int(tile.get('placed_day',day)),day+1).items():
                        existing[side][d]=existing[side].get(d,0.)+u
    private=obs.get('private') or {};shed=private.get('shed') or {}
    held=sum(int(inv.get(animal_of,0)) for inv in private.get('inventories') or [])
    for _ in range(int(shed.get(animal_of,0))+held):
        for d,u in schedule(animal_of,day+1,day+1).items():existing[seat][d]=existing[seat].get(d,0.)+u
    native=n['_IMPL'].chassis.players.get(seat)
    if native:
        similar=n['_r37_similarity'](obs)>=.9
        for t in range(int(obs['step'])+1,696):
            tape=n['_IMPL'].chassis.routes[2 if t>=648 else native['route']]
            if t>=len(tape) or not isinstance(tape[t],dict):continue
            for order in tape[t].get('market',[]) or []:
                if len(order)<3 or order[0]!='BUY_ANIMAL' or order[1]!=animal_of:continue
                units=max(0,int(order[2]))
                if fix_horizon and t>=24*macro_day:
                    if receipt is not None:receipt['excluded_future_animals']+=units
                    continue
                for _ in range(units):
                    for d,u in schedule(animal_of,t//24+1,day+1).items():
                        existing[seat][d]=existing[seat].get(d,0.)+u
                        if similar:existing[1-seat][d]=existing[1-seat].get(d,0.)+u
    ours,rival=existing[seat],existing[1-seat]
    new={d:u*k for d,u in schedule(option,day+1,day+1).items()}
    shops=list((obs.get('town') or {}).get('unlocked_shops') or [])
    unlocks=max(0,8-len(shops));base=n['_hd2_daily_shop_demand'](item,shops)+1.
    extra=n['_hd2_future_demand_per_day'](item)*n['_HD2_FUTURE']
    quote=n['_r37_market_price']
    def path(with_new):
        inv=float(market['inventory'][item]);prices={};revenue=0.
        for d in range(day+1,30):
            opened=min(unlocks,max(0,d//3-day//3));inv-=base+extra*opened
            units=ours.get(d,0.)+rival.get(d,0.)
            if fix_floor:
                # Preserve fractional care expectations; split only at unit
                # boundaries so the floor can stop subsequent inventory adds.
                while units>1e-9:
                    piece=min(1.,units);units-=piece
                    if quote(item,int(round(inv)),params)>1:inv+=piece
                    elif receipt is not None:receipt['floor_blocked_units']+=piece
            else:inv+=units
            prices[d]=quote(item,int(round(inv)),params)
            if with_new:
                for _ in range(int(round(new.get(d,0.)))):
                    price=quote(item,int(round(inv)),params);revenue+=price
                    if price>1:inv+=1
        return prices,revenue
    before,_=path(False);after,revenue=path(True)
    swing=sum((after[d]-before[d])*(ours.get(d,0.)-rival.get(d,0.)) for d in before)
    return revenue+swing-spec['cost']*k,revenue


def act(obs,configuration,params,metrics):
    if not params.get('elite_opening_ev_fix'):return _opening(obs,configuration)
    original=_opening_ns['_hd2_ev']
    def wrapped(option,k,current,st):
        receipt=Counter();old=original(option,k,current,st)
        value=corrected_ev(option,k,current,st,_opening_ns,
            macro_day=int(params.get('elite_macro_day',15)),receipt=receipt)
        metrics['opening_ev_calls']+=1
        metrics['opening_ev_changed']+=abs(old[0]-value[0])>1e-7
        for key,number in receipt.items():metrics['opening_ev_'+key]+=number
        return value
    _opening_ns['_hd2_ev']=wrapped
    try:return _opening(obs,configuration)
    finally:_opening_ns['_hd2_ev']=original
