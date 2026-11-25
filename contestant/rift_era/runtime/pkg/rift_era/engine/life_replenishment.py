"""P2 same-Hub life replenishment after actual cargo unloading.

Original P1 reservations remain protected. Only free native cash (or actual
repatriation of free household RU) funds additions. Grain made unnecessary by
new food is returned before P7 use, preserving its original title and storage.
"""
from .errors import InvariantViolation
from .economic_ownership import purchase_quote
from .ledger import Account,CashAccount,CashTransfer,DeliveryPayment,Transfer
from .numeric import Q,ceil,floor,max_feasible,proportional
from .payments import FXRequest


OUTDOOR_LOCK='locked_outdoor_subsistence:civilian'
MAIN_LOCK='locked_subsistence:civilian'
BANDS=(('food','grain','water'),('medicine','medical_supplies','gene_therapy'),('consumer_goods',))


def convert_resident_foreign_cash(state,ctx,hub,owner,missing_native_mU,tag):
    """02 occupation_residents_v1: convert foreign-currency resident wages only on native shortfall."""
    if missing_native_mU<=0 or owner not in state.rates:return 0
    cash=state.initial.cash
    foreign_employers=[emp for emp in sorted(state.initial.known)
                       if emp!=owner and cash.balance(state.native(emp,f'household:{hub}')).native_mU>0]
    if not foreign_employers:return 0
    wallet=state.native(owner,f'household:{hub}')
    bridge_ru=CashAccount(owner,f'household_fx:{hub}','RU')
    fee_owner=state.fx.fee_rate(FXRequest('',bridge_ru,wallet,0,Q(1)))
    ru_target=ceil(missing_native_mU*state.rates[owner]*(1+fee_owner))+2
    capacities={}
    for emp in foreign_employers:
        src=state.native(emp,f'household:{hub}')
        ctrl=Q(state.fx.controls.get(src.currency,0),10000)
        if ctrl>=1:continue
        cap=floor(cash.balance(src).native_mU*(1-ctrl)*state.rates[emp])
        if cap>0:capacities[emp]=cap
    if not capacities:return 0
    shares=proportional(ru_target,capacities,key=(ctx.seed,'resident_fx_leg1',ctx.tick,hub,tag))
    leg1=[]
    for emp,lim in shares.items():
        if lim<=0:continue
        src=state.native(emp,f'household:{hub}')
        dst=CashAccount(emp,f'household_fx:{hub}','RU')
        ctrl=Q(state.fx.controls.get(src.currency,0),10000)
        fee_emp=state.fx.fee_rate(FXRequest('',src,dst,0,Q(1)))
        offered=min(cash.balance(src).native_mU,ceil(Q(lim,state.rates[emp])*(1+fee_emp)/(1-ctrl))+2)
        if offered>0:
            leg1.append(FXRequest(f'resident_fx1:{tag}:{hub}:{emp}',src,dst,offered,state.rates[emp],principal_limit_mU=lim))
    if not leg1:return 0
    state.fx.execute(ctx.seed,ctx.tick,tuple(leg1))
    transfers=[]
    for emp in foreign_employers:
        emp_ru=CashAccount(emp,f'household_fx:{hub}','RU')
        bal=cash.balance(emp_ru).native_mU
        if bal>0:transfers.append(CashTransfer(emp_ru,bridge_ru,bal))
    if transfers:cash.transfer_many(tuple(transfers))
    ru_bal=cash.balance(bridge_ru).native_mU
    if ru_bal<=0:return 0
    req=FXRequest(f'resident_fx2:{tag}:{hub}',bridge_ru,wallet,ru_bal,1/state.rates[owner],principal_limit_mU=missing_native_mU)
    fills=state.fx.execute(ctx.seed,ctx.tick,(req,))
    rem_ru=cash.balance(bridge_ru).native_mU
    if rem_ru>0:cash.transfer(bridge_ru,CashAccount(owner,f'household:{hub}','RU'),rem_ru)
    return fills.get(req.id,0)


def _additional_cost(state,hub,original,good,quantity):
    owner=state.initial.map.hubs[hub].owner
    payer=state.frame.household_locks[hub]
    def quote(q):
        exact=q*state.price(good,hub)
        return (purchase_quote(state.payments,payer,exact,'civilian:'+good)+
            state.governance.quote_tax(payer,hub,exact*state.governance.resident_tax(hub,owner),'consumption_tax:'+good))
    baseline=original.get(good,0)
    return quote(baseline+quantity)-quote(baseline)


def replenish_households(state,ctx):
    frame=state.frame;inventory=state.initial.inventory;cash=state.initial.cash
    if frame.life_replenished:raise InvariantViolation('life needs replenished twice in one tick')
    plans={}
    for hub,needs in frame.household_need.items():
        owner=state.initial.map.hubs[hub].owner
        original=dict(frame.household[hub]);lock=frame.household_locks[hub]
        wanted={}
        for good in needs:
            available=sum(inventory.balance(Account(owner,hub,b,good)) for b in ('available','outdoor'))
            wanted[good]=min(max(0,needs[good]-original.get(good,0)),available)
        target_grain=floor(max(Q(0),Q(3,4)*needs['food']-original.get('food',0)-wanted['food'])*Q(27,20))
        available=sum(inventory.balance(Account(owner,hub,b,'grain')) for b in ('available','outdoor'))
        wanted['grain']=min(max(0,target_grain-original.get('grain',0)),available)
        # Store closed integer invoice differences, not a second independent
        # quote that would charge the same old fraction twice.
        costs={g:_additional_cost(state,hub,original,g,q) for g,q in wanted.items()}
        plans[hub]=(owner,original,wanted,costs)
    transfers=[];money=[]
    updates={hub:dict(row[1]) for hub,row in plans.items()}
    outdoor_updates={hub:dict(frame.household_outdoor.get(hub,{})) for hub in plans}
    spent=dict.fromkeys(plans,0)
    for priority,band in enumerate(BANDS):
        fx=[]
        for hub,(owner,original,wanted,costs) in plans.items():
            wallet=state.native(owner,'household:'+hub)
            missing=max(0,sum(costs.get(g,0) for g in band)-cash.balance(wallet).native_mU+spent[hub])
            foreign=state.operating_account('municipal:'+hub,'RU')
            if missing and cash.balance(foreign).native_mU:
                principal=ceil(missing*state.rates[owner])
                offered=min(cash.balance(foreign).native_mU,ceil(principal*(1+state.fx.fee))+1)
                fx.append(FXRequest('life_repatriation:'+str(priority)+':'+hub,foreign,wallet,offered,
                    1/state.rates[owner],principal_limit_mU=principal))
        # The same life band competes globally; lower-priority consumption
        # cannot consume another Hub's scarce native FX before its food.
        if fx:
            state.fx.execute(ctx.seed,ctx.tick,tuple(fx))
            state.corporate_finance._record_fx_costs(fx)
        for hub,(owner,original,wanted,costs) in plans.items():
            wallet=state.native(owner,'household:'+hub)
            missing=max(0,sum(costs.get(g,0) for g in band)-cash.balance(wallet).native_mU+spent[hub])
            if missing:
                convert_resident_foreign_cash(state,ctx,hub,owner,missing,f'p2:{priority}')
            request={g:costs[g] for g in band if g in wanted}
            grants=proportional(cash.balance(wallet).native_mU-spent[hub],request,
                key=(ctx.seed,'life_replenishment',ctx.tick,hub,priority))
            for good in request:
                quantity=max_feasible(wanted[good],lambda q:_additional_cost(state,hub,original,good,q)<=grants[good])
                updates[hub][good]=original.get(good,0)+quantity
                spent[hub]+=_additional_cost(state,hub,original,good,quantity)
                main=min(quantity,inventory.balance(Account(owner,hub,'available',good)))
                if main:transfers.append(Transfer(Account(owner,hub,'available',good),Account(owner,hub,MAIN_LOCK,good),main))
                other=quantity-main
                if other:
                    transfers.append(Transfer(Account(owner,hub,'outdoor',good),Account(owner,hub,OUTDOOR_LOCK,good),other))
                    outside=outdoor_updates[hub]
                    outside[good]=outside.get(good,0)+other
    for hub,(owner,_,_,_) in plans.items():
        money.append(CashTransfer(state.native(owner,'household:'+hub),frame.household_locks[hub],spent[hub]))
    DeliveryPayment.prepare(inventory,cash,transfers,money).commit()
    frame.household.update(updates);frame.household_outdoor.update(outdoor_updates)
    frame.life_replenished=True


def release_unused_grain(state,hub):
    frame=state.frame;goods=frame.household[hub]
    reserved=goods.get('grain',0)
    if not reserved:return
    need=frame.household_need[hub]['food']
    used=min(reserved,floor(max(Q(0),Q(3,4)*need-goods.get('food',0))*Q(27,20)))
    outside=frame.household_outdoor.get(hub,{}).get('grain',0)
    main=reserved-outside
    use_main=min(used,main);use_outside=used-use_main
    owner=state.initial.map.hubs[hub].owner
    state.initial.inventory.transfer_many((
        Transfer(Account(owner,hub,MAIN_LOCK,'grain'),Account(owner,hub,'available','grain'),main-use_main),
        Transfer(Account(owner,hub,OUTDOOR_LOCK,'grain'),Account(owner,hub,'outdoor','grain'),outside-use_outside)))
    goods['grain']=used
    frame.household_outdoor.setdefault(hub,{})['grain']=use_outside
