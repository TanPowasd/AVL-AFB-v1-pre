"""Physical two-depot periodic barter with escrowed first obligations.

An exchange changes custody at the declared depot; neither leg moves across the
map. Company-owned goods must be purchased before a sovereign can pledge them.
"""
from dataclasses import dataclass
from .command_schema import fields
from .errors import RuleViolation
from .ledger import Account,Transfer,CashAccount,CashTransfer,DeliveryPayment
from .protocol import PreparedCommand
from .numeric import Q


@dataclass(frozen=True)
class SwapProposal:
    id:str
    proposer:str
    counterparty:str
    command_id:str
    hub_a:str
    hub_b:str
    good_a:str
    good_b:str
    quantity_a:int
    quantity_b:int
    period_cycles:int
    duration_cycles:int
    expires_cycle:int
    cash_currency:str|None=None
    cash_amount:int=0
    cash_side:str|None=None
    # 03 cross-border power sub-item (E12 settlement by actual flow):
    # proposer sells over its power line corridor_id to the counterparty.
    power_link:str|None=None
    power_qty_mU:int=0
    price_cap_ru_mU:int=0


@dataclass
class PeriodicSwap:
    proposal:SwapProposal
    start_cycle:int
    end_cycle:int
    next_cycle:int
    status:str='ACTIVE'
    delivered_periods:int=0
    defaulted_cycle:int=-1
    blocked_by:tuple[str,...]=()
    penalized_parties:tuple[str,...]=()
    power_delivered_mU:int=0
    power_paid_ru_mU:object=Q(0)
    power_refunded_ru_mU:int=0

    def lock(self,owner,hub,good):return Account(owner,hub,'contract_swap:'+self.proposal.id,good)


class PeriodicContracts:
    def __init__(self,state):self.s=state;self.rows={};self.pending=[];self.last_cycle=-1

    @staticmethod
    def wallet(owner,currency):return CashAccount(owner,'reserves' if currency=='RU' else 'treasury',currency)

    def cash_lock(self,p):
        owner=p.proposer if p.cash_side=='a' else p.counterparty
        return CashAccount(owner,'contract_swap_cash:'+p.id,p.cash_currency)

    def validate_power(self,p):
        """03 power sub-item on a real cross-border line; C28: both Credit >= 0.3."""
        state=self.s
        link=state.utilities.links.get(p.power_link)
        if link is None or link.mode!='power':raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if state.utilities.endpoint_nations(link)!={p.proposer,p.counterparty}:
            raise RuleViolation('ERR_NO_PERMISSION','power sub-item needs a line between the two parties')
        if min(state.credit[p.proposer],state.credit[p.counterparty])<Q(3,10):
            raise RuleViolation('ERR_NO_PERMISSION','C28: credit below 0.3 cannot sign new power contracts')
        if state.relations.will_be_at_war(p.proposer,p.counterparty):raise RuleViolation('ERR_NO_PERMISSION')

    def validate(self,p):
        state=self.s
        if p.power_link is not None:return self.validate_power(p)
        for owner,hub,good,q in ((p.proposer,p.hub_a,p.good_a,p.quantity_a),(p.counterparty,p.hub_b,p.good_b,p.quantity_b)):
            if p.cash_side==('a' if owner==p.proposer else 'b'):continue
            if hub not in state.initial.map.hubs:raise RuleViolation('ERR_NO_VISIBLE_TARGET')
            if not state.authority.controls_hub(owner,hub):raise RuleViolation('ERR_NO_PERMISSION')
            other=p.counterparty if owner==p.proposer else p.proposer
            if not state.export_controls.permits(other,owner,owner,good):raise RuleViolation('ERR_NO_PERMISSION')
            stock=state.initial.inventory.materials.composition(Account(owner,hub,'available',good))
            if any(o.economic_owner is not None for o in stock):raise RuleViolation('ERR_NO_PERMISSION','barter requires government-owned supplies')
            if any(not state.export_controls.permits(other,owner,o.nation,good) for o in stock):
                raise RuleViolation('ERR_NO_PERMISSION','actual origin embargo blocks barter delivery')

    def obligations(self,row,*,locked):
        p=row.proposal
        legs=((p.proposer,p.counterparty,p.hub_a,p.good_a,p.quantity_a),(p.counterparty,p.proposer,p.hub_b,p.good_b,p.quantity_b))
        return tuple(Transfer(row.lock(owner,hub,good) if locked else Account(owner,hub,'available',good),
            Account(other,hub,'available',good) if locked else row.lock(owner,hub,good),q,retitle=locked)
            for owner,other,hub,good,q in legs if p.cash_side!=('a' if owner==p.proposer else 'b'))

    def cash_obligation(self,p,*,locked):
        if p.cash_side is None:return ()
        owner=p.proposer if p.cash_side=='a' else p.counterparty
        other=p.counterparty if p.cash_side=='a' else p.proposer
        return (CashTransfer(self.cash_lock(p) if locked else self.wallet(owner,p.cash_currency),
            self.wallet(other,p.cash_currency) if locked else self.cash_lock(p),p.cash_amount),)

    def prepare_accept(self,p,ctx):
        self.validate(p);row=PeriodicSwap(p,ctx.cycle,ctx.cycle+p.duration_cycles,ctx.cycle)
        if p.power_link is not None:
            row.next_cycle=ctx.cycle+p.period_cycles
            def accept_power():
                self.rows[p.id]=row
                self.s.contracts.log(p,'SIGNED');del self.s.contracts.proposals[p.id]
            return PreparedCommand(accept_power)
        plan=DeliveryPayment.prepare(self.s.initial.inventory,self.s.initial.cash,self.obligations(row,locked=False),self.cash_obligation(p,locked=False))
        def accept():
            plan.commit();self.rows[p.id]=row;self.pending.append(p.id)
            self.s.contracts.log(p,'SIGNED');del self.s.contracts.proposals[p.id]
        return PreparedCommand(accept)

    def settle(self,ctx):
        # Contracts whose two actual obligations were reserved are delivered as
        # one material transaction after command streams. New receipts cannot
        # finance or supply other accepts within that command batch.
        ready=[];money=[];permitted={}
        for pid in self.pending:
            row=self.rows[pid]
            permitted[pid]=self.allowed(row)
            if permitted[pid]:
                ready.extend(self.obligations(row,locked=True));money.extend(self.cash_obligation(row.proposal,locked=True))
            else:
                ready.extend(self.refunds(row))
                money.extend(CashTransfer(t.destination,t.source,t.amount) for t in self.cash_obligation(row.proposal,locked=False))
        DeliveryPayment.prepare(self.s.initial.inventory,self.s.initial.cash,ready,money).commit()
        for pid in self.pending:
            row=self.rows[pid]
            if permitted[pid]:row.delivered_periods+=1;row.next_cycle=ctx.cycle+row.proposal.period_cycles
            else:row.status='SUSPENDED'
        self.pending.clear()

    def allowed(self,row):
        p=row.proposal
        for owner,other,hub,good in ((p.proposer,p.counterparty,p.hub_a,p.good_a),(p.counterparty,p.proposer,p.hub_b,p.good_b)):
            if p.cash_side==('a' if owner==p.proposer else 'b'):continue
            origins=self.s.initial.inventory.materials.composition(row.lock(owner,hub,good))
            if any(not self.s.export_controls.permits(other,owner,o.nation,good) for o in origins):return False
        return True

    def refunds(self,row):
        return tuple(Transfer(t.destination,t.source,t.quantity) for t in self.obligations(row,locked=False))

    def begin_tick(self,ctx):
        if ctx.tick%ctx.clock.cycle_ticks:return
        if ctx.cycle<=self.last_cycle:return
        self.last_cycle=ctx.cycle
        for row in self.rows.values():
            if row.status in ('EXPIRED','DEFAULTED','TERMINATED'):continue
            if row.proposal.power_link is not None:
                self.power_period(row,ctx);continue
            if ctx.cycle>=row.end_cycle:row.status='EXPIRED';continue
            if ctx.cycle<row.next_cycle:continue
            p=row.proposal
            try:
                self.validate(p)
                plan=DeliveryPayment.prepare(self.s.initial.inventory,self.s.initial.cash,self.obligations(row,locked=False),self.cash_obligation(p,locked=False))
            except RuleViolation:
                row.status='SUSPENDED'
                offenders=[]
                for owner,good in ((p.proposer,p.good_a),(p.counterparty,p.good_b)):
                    other=p.counterparty if owner==p.proposer else p.proposer
                    if good and not self.s.export_controls.direct_allowed(other,owner,good):offenders.append(owner)
                row.blocked_by=tuple(offenders)
                if row.defaulted_cycle!=ctx.cycle:
                    for owner in set(offenders)-set(row.penalized_parties):self.s.credit[owner]=max(Q(1,10),self.s.credit[owner]-Q(15,100))
                    row.penalized_parties=tuple(sorted(set(row.penalized_parties)|set(offenders)))
                    row.defaulted_cycle=ctx.cycle
                continue
            plan.commit();self.pending.append(p.id);row.status='ACTIVE'

    # ---- E12: cross-border power contracts settled on actual flow ----
    def record_power(self,flows):
        """P3: (seller, buyer, edges, received_mU, source_payment_ru_mU) of this tick's dispatch receipts."""
        active=[r for r in self.rows.values() if r.proposal.power_link is not None and r.status=='ACTIVE']
        if not active:return
        for seller,buyer,edges,received,paid in flows:
            if not received:continue
            for row in active:
                p=row.proposal
                if (seller,buyer)!=(p.proposer,p.counterparty) or p.power_link not in edges:continue
                take=min(received,p.power_qty_mU-row.power_delivered_mU)
                if take<=0:continue
                row.power_delivered_mU+=take
                row.power_paid_ru_mU+=Q(paid)*take/received

    def power_period(self,row,ctx,*,final=False):
        """Period end: the seller refunds what the buyer paid above price_cap on contracted flow.

        final=True settles the running partial period on early termination."""
        if not final and ctx.cycle<row.next_cycle and ctx.cycle<row.end_cycle:return
        p=row.proposal;cash=self.s.initial.cash
        allowed=Q(row.power_delivered_mU*p.price_cap_ru_mU,1000)
        excess=int(max(Q(0),row.power_paid_ru_mU-allowed))
        if excess:
            corp=CashAccount(p.proposer,'corp:energy','RU');reserves=CashAccount(p.proposer,'reserves','RU')
            target=self.wallet(p.counterparty,'RU')
            paid_corp=min(excess,max(0,cash.balance(corp).native_mU))
            rem=excess-paid_corp
            paid_res=min(rem,max(0,cash.balance(reserves).native_mU)) if rem else 0
            transfers=[]
            if paid_corp:transfers.append(CashTransfer(corp,target,paid_corp))
            if paid_res:transfers.append(CashTransfer(reserves,target,paid_res))
            if transfers:cash.transfer_many(tuple(transfers))
            paid=paid_corp+paid_res
            row.power_refunded_ru_mU+=paid
            if paid<excess and p.proposer not in row.penalized_parties:
                self.s.credit[p.proposer]=max(Q(1,10),self.s.credit[p.proposer]-Q(15,100))
                row.penalized_parties=row.penalized_parties+(p.proposer,);row.defaulted_cycle=ctx.cycle
                row.status='DEFAULTED'
        if row.status=='ACTIVE':row.delivered_periods+=1
        row.power_delivered_mU=0;row.power_paid_ru_mU=Q(0)
        row.next_cycle=ctx.cycle+p.period_cycles
        if ctx.cycle>=row.end_cycle and row.status=='ACTIVE':row.status='EXPIRED'

    def offer_power(self,owner,order,ctx):
        state=self.s;book=state.contracts;other=order['counterparty'];p=order['params']
        if (other==owner or not 10<=order['duration_cycles']<=720 or set(p)!={'corridor_id','qty_mU','price_cap_ru_mU','period_cycles'} or
                type(p['corridor_id']) is not str or any(type(p[k]) is not int for k in ('qty_mU','price_cap_ru_mU','period_cycles')) or
                p['qty_mU']<=0 or p['price_cap_ru_mU']<=0 or not 1<=p['period_cycles']<=100):raise RuleViolation('ERR_SCHEMA')
        if other not in state.initial.known:raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        if any(book.count(party)>=state.catalog.runtime['limits']['contracts_per_nation'] for party in (owner,other)):raise RuleViolation('ERR_ENTITY_CAP_EXCEEDED')
        proposal=SwapProposal(f'swap_proposal:{owner}:{int(order["id"])}',owner,other,str(int(order['id'])),'','','electricity','',
            p['qty_mU'],0,p['period_cycles'],order['duration_cycles'],ctx.cycle+order['duration_cycles'],
            power_link=p['corridor_id'],power_qty_mU=p['qty_mU'],price_cap_ru_mU=p['price_cap_ru_mU'])
        self.validate(proposal)
        return PreparedCommand(lambda:book.proposals.__setitem__(proposal.id,proposal))

    def offer(self,owner,order,ctx):
        if isinstance(order.get('params'),dict) and 'corridor_id' in order['params']:return self.offer_power(owner,order,ctx)
        state=self.s;book=state.contracts;other=order['counterparty'];p=order['params']
        common={'hub_a','hub_b','period_cycles'}
        cash_fields={'currency','amount_mU','cash_side'}
        cash=bool(cash_fields & p.keys())
        required=common|({'good_b','qty_b_mU'} if cash and p.get('cash_side')=='a' else {'good_a','qty_a_mU'} if cash else {'good_a','qty_a_mU','good_b','qty_b_mU'})|(cash_fields if cash else set())
        if (other==owner or not 10<=order['duration_cycles']<=720 or set(p)!=required or
                any(type(p[k]) is not str for k in ('hub_a','hub_b')) or
                type(p['period_cycles']) is not int or not 1<=p['period_cycles']<=100):raise RuleViolation('ERR_SCHEMA')
        if cash and (p['cash_side'] not in ('a','b') or type(p['currency']) is not str or p['currency'] not in ('RU',*state.initial.known) or
                     type(p['amount_mU']) is not int or p['amount_mU']<=0):raise RuleViolation('ERR_SCHEMA')
        if other not in state.initial.known:raise RuleViolation('ERR_NO_VISIBLE_TARGET')
        good_a=p.get('good_a','');good_b=p.get('good_b','');qty_a=p.get('qty_a_mU',0);qty_b=p.get('qty_b_mU',0)
        for side,good,q in (('a',good_a,qty_a),('b',good_b,qty_b)):
            if cash and p['cash_side']==side:continue
            if type(good) is not str or type(q) is not int or q<=0 or good not in state.catalog.goods or state.catalog.goods[good]['kind']!='inventory':raise RuleViolation('ERR_SCHEMA')
            if state.catalog.goods[good]['indivisible'] and q%1000:raise RuleViolation('ERR_SCHEMA')
        if any(book.count(party)>=state.catalog.runtime['limits']['contracts_per_nation'] for party in (owner,other)):raise RuleViolation('ERR_ENTITY_CAP_EXCEEDED')
        proposal=SwapProposal(f'swap_proposal:{owner}:{int(order["id"])}',owner,other,str(int(order['id'])),p['hub_a'],p['hub_b'],
            good_a,good_b,qty_a,qty_b,p['period_cycles'],order['duration_cycles'],ctx.cycle+order['duration_cycles'],
            p.get('currency'),p.get('amount_mU',0),p.get('cash_side'))
        self.validate(proposal)
        return PreparedCommand(lambda:book.proposals.__setitem__(proposal.id,proposal))

    def visible(self,owner):return [r for r in self.rows.values() if owner in (r.proposal.proposer,r.proposal.counterparty)]
    def snapshot(self):return {'rows':self.rows,'pending':self.pending,'last_cycle':self.last_cycle}
