"""Economic title routing for jointly owned facilities.

Authority shares identify economic claimants but never mint inventory. This
adapter only transforms origin metadata on the one physical lot being moved.
"""
from collections import defaultdict
from dataclasses import replace
from fractions import Fraction
from .economic_ownership import EconomicOwner
from .errors import InvariantViolation
from .numeric import Q,split_payment


class FacilityOwnership:
    def __init__(self,state):
        self.s=state;self.entities={}
        # jv_cost_share_v1: unpaid partner share of operating cost, owed to
        # the operator, (entity, nation) -> mU in the operator's currency.
        self.arrears={}

    def register(self,entity,parent):self.entities[entity]=parent

    def shares(self,entity):
        from .diplomacy import Relation
        parent=self.entities.get(entity,entity);rights=self.s.authority.assets.get(parent)
        if rights is None:return {EconomicOwner(self.s.initial.assets[parent].owner,'treasury'):Q(1)}
        dip=self.s.initial.diplomacy
        weights=defaultdict(Fraction)
        for owner,shares in rights.owner_shares.items():
            # 04 F04: during war with the operator, foreign facility shares are frozen by the operator.
            recipient=(rights.operator if owner!=rights.operator and owner in dip.actors and rights.operator in dip.actors
                       and dip.relation(rights.operator,owner)==Relation.WAR else owner)
            weights[EconomicOwner(recipient,'treasury')]+=Q(shares,1000)
        return dict(weights)

    def split_output(self,entity,origins):
        shares=self.shares(entity);result=defaultdict(Fraction)
        for origin,quantity in origins.items():
            allocations={title:quantity*share for title,share in shares.items()}
            for title,amount in allocations.items():result[replace(origin,economic_owner=title)]+=amount
        total=sum(origins.values(),Q(0));out=sum(result.values(),Q(0))
        if total!=out:raise InvariantViolation('facility title split changed physical quantity')
        return dict(result)

    def cost_shares(self,entity):return self.shares(entity)
    def snapshot(self):return {'entities':self.entities,'arrears':[[e,p,v] for (e,p),v in sorted(self.arrears.items())]}
    def audit(self):
        for entity,parent in self.entities.items():
            if parent not in self.s.authority.assets:raise InvariantViolation('joint facility parent missing')
        if any(type(v) is not int or v<=0 or e not in self.entities for (e,_),v in self.arrears.items()):
            raise InvariantViolation('invalid joint venture cost arrears')

    def share_costs(self,entity,payee,amount_mU,ctx):
        """11 \"设施产出/输入费用按产权份额\": partners reimburse the operator's actually paid cost.

        ``amount_mU`` is what the operator's operating account really paid this
        tick (wages, inputs, power).  Each non-operator shareholder pays its
        split_payment share plus older arrears from its own treasury in the
        operator's currency, up to its balance; the rest stays as arrears owed
        to the operator.  No account is invented and no output is withheld.
        """
        from .ledger import CashAccount,CashTransfer
        shares=self.cost_shares(entity)
        if len(shares)<2:return ()
        total=int(amount_mU)
        weights={t.nation:share for t,share in shares.items()}
        requests=split_payment(total,weights,key=(self.s.initial.map.seed,'jv_cost_share',ctx.tick,entity)) if total else dict.fromkeys(weights,0)
        cash=self.s.initial.cash;transfers=[]
        for nation,portion in sorted(requests.items()):
            if nation==payee.owner:continue
            key=(entity,nation)
            due=portion+self.arrears.get(key,0)
            if not due:continue
            source=CashAccount(nation,'treasury',payee.currency)
            paid=min(due,max(0,cash.balance(source).native_mU))
            if paid:transfers.append(CashTransfer(source,payee,paid))
            if due-paid:self.arrears[key]=due-paid
            else:self.arrears.pop(key,None)
        cash.transfer_many(transfers)
        return tuple(transfers)

    def prepare_cost_payment(self,entity,amount,currency,kind='facility_cost'):
        """Return atomic cash transfers from each shareholder's real account.

        No fallback account is invented. Callers may commit the plan only when
        every requested shareholder amount is funded; otherwise retain unpaid
        debt externally and do not consume the facility input.
        """
        from .ledger import CashTransfer
        from .ledger import CashAccount
        shares=self.cost_shares(entity);titles={t.nation:t for t in shares}
        requests=split_payment(amount,{t.nation:q for t,q in shares.items()},key=(self.s.initial.map.seed,'facility_cost',entity,kind))
        transfers=[]
        for nation,portion in sorted(requests.items()):
            title=titles[nation]
            source=CashAccount(title.nation,title.account,currency)
            transfers.append(CashTransfer(source,CashAccount('world','facility_clearing:'+entity,currency),portion))
        return tuple(transfers)
