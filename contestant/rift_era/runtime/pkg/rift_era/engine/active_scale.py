"""P7 technology ActiveScale meters, installed by the full-match collector.

Unbuilt infrastructure has zero scale; a technology ID or a hostile action is
never proof of use. A meter listed in PENDING_METERS would have no engine
mechanism yet: it is zero by necessity and the finalizer reports it, so a
match using it is never certified as ``full_rules_accepted``.  Since
``active_scale_entities_v1`` every 07 meter has an engine count.
"""
from collections import defaultdict

from .errors import InvariantViolation
from .ledger import Account
from .logistics import TransitMode
from .numeric import Q


SYSTEM_METERS={
    'online_smart_grid_hubs':'smart_grid', 'online_fusion_sensor_hubs':'fusion_sensor',
    'online_reservoirs':'reservoir', 'online_closed_ecology_hubs':'closed_ecology',
    'online_automated_terminals':'automated_terminal', 'online_listening_stations':'listening',
    'counterintel_covered_hubs':'counterintel', 'post_quantum_hubs':'post_quantum',
    'online_deception_defense_hubs':'deception_defense', 'resilient_hubs':'resilience',
    'online_system_defense_hubs':'system_defense', 'online_environment_stations':'environment',
    'online_water_recycling_hubs':'water_recycle', 'online_climate_models':'climate_model',
    'online_cryptanalysis_defense_hubs':'cryptanalysis_defense',
}
COMPUTE_METERS={
    'effective_research_compute':'research', 'effective_training_compute':'train',
    'effective_production_compute':'production', 'effective_finance_compute':'finance',
    'effective_cyber_compute':'cyber', 'effective_alignment_compute_div10':'align',
}


PENDING_METERS=frozenset()
# active_scale_entities_v1 (needs author confirmation): engine entities behind
# the formerly pending meters.
CLOSED_MINE_CYCLES=120        # V07: a deposit with no extraction for 120 full cycles
JOINT_FREEZE_TICKS=10         # G12: joint doctrine frozen 10 ticks after command loss
COMBINED_SLOTS=('radio','sensor')


def intercontinental(state,link):
    regions=state.initial.map.regions;hubs=state.initial.map.hubs
    a,b=regions[hubs[link.a].region].continent,regions[hubs[link.b].region].continent
    return a is not None and b is not None and a!=b


class ActiveScale:
    def __init__(self,state,power=None):
        self.s=state;self.power=power;self.cycle=-1;self.last_tick=-1
        self.recipes={};self.lines={};self.systems={};self.compute={};self.formations={};self.heat=set()
        self.cables=set();self.routes=set();self.depots=set()
        # Persistent across cycles: last extraction per deposit and last tick
        # each nation's joint command hub was lost.
        self.mine_last={};self.command_down={}
        self.values={};self.licensed={}

    def restored_regions(self,cycle):
        """V07: regions holding a deposit closed >=120 cycles whose last extractor knows V07."""
        cached=getattr(self,'_restored',None)
        if cached is not None and cached[0]==cycle:return cached[1]
        state=self.s;regions=set()
        for deposit,(owner,last) in self.mine_last.items():
            if cycle-last<CLOSED_MINE_CYCLES or owner not in state.research or 'V07' not in state.research[owner].known:continue
            site=state.resource_sites.get(deposit)
            if site is not None and site.hub in state.initial.map.hubs:
                regions.add(state.initial.map.hubs[site.hub].region)
        self._restored=(cycle,frozenset(regions))
        return self._restored[1]

    def _reset(self,cycle):
        self.cycle=cycle;self.recipes=defaultdict(lambda:Q(0));self.lines=defaultdict(lambda:Q(0))
        self.systems=defaultdict(int);self.compute=defaultdict(lambda:Q(0));self.formations=defaultdict(int);self.heat=set()
        self.cables=set();self.routes=set();self.depots=set()

    def extracted(self,site,owner,amount_mU):
        """V07: remember the last cycle each deposit was actually worked, and by whom."""
        deposit=self.s.mine_deposit.get(site,site) if hasattr(self.s,'mine_deposit') else site
        if amount_mU and self.cycle>=0:self.mine_last[deposit]=(owner,self.cycle)

    def _meter_entities(self,ctx):
        state=self.s;frame=state.frame
        # E12: an intercontinental power link that actually carried flow.
        links=state.utilities.links
        for receipt in frame.power_receipts:
            for eid,q in receipt.edges:
                link=links.get(eid)
                if q>0 and link is not None and link.mode=='power' and intercontinental(state,link):
                    self.cables.add((state.utilities.operator(link),eid))
        # L11: an automatically routed (auto/intermodal) relay with loaded cargo moving.
        intermodal=getattr(state,'intermodal',None)
        if intermodal is not None:
            cargo=state.cargo
            for sid,trip in cargo.transport.shipments.items():
                if trip.mode!=TransitMode.IN_TRANSIT or trip.returning or not trip.quantity_mU:continue
                job=cargo.hauls.get(cargo.shipment_jobs.get(sid))
                plan=intermodal.plans.get(job.parent_id) if job is not None and job.parent_id else None
                if (plan is not None and plan.carrier in ('auto','intermodal') and 'L11' in state.research[plan.owner].known
                        and not state.cyber.rolled_back(plan.owner,'L11')):
                    self.routes.add((plan.owner,plan.id))
        # G02: a field depot (vehicle x2 at a hub other than the formation's home)
        # whose stock actually supplied a formation.
        inv=state.initial.inventory
        for fid,hub in sorted(frame.military_hub_draws.items()):
            formation=state.formations.rows.get(fid)
            if formation is None or hub==formation.home_hub:continue
            if inv.balance(Account(formation.owner,hub,'available','vehicle'))>=2000:
                self.depots.add((formation.owner,hub))
        # G07/G12: ready (supplied, not refitting) networked formations with a
        # radio/sensor slot, in a region holding at least two of its unit types.
        owners={p for p in state.research if state.research[p].known&{'G07','G12'}}
        if not owners or self.power is None:return
        ready=[f for f in state.formations.rows.values() if f.owner in owners and not f.refitting and
               all(frame.military.get(f.id,{}).get(g,0)>=n for g,n in frame.military_need.get(f.id,{}).items())]
        types=defaultdict(set)
        for f in ready:
            types[f.owner,f.region].update(state.initial.units[u].unit_type for u in f.unit_ids if u in state.initial.units)
        connected={}
        for p in sorted(owners):
            connected[p]=self.power.connected_regions(p)
            if not connected[p]:self.command_down[p]=ctx.tick
        for f in ready:
            if f.slot_a not in COMBINED_SLOTS or len(types[f.owner,f.region])<2 or f.region not in connected[f.owner]:continue
            self.formations[f.owner,'combined',f.id]+=1
            down=self.command_down.get(f.owner)
            if 'G12' in state.research[f.owner].known and (down is None or ctx.tick-down>=JOINT_FREEZE_TICKS):
                self.formations[f.owner,'joint',f.id]+=1

    def advance(self,ctx):
        if ctx.tick<=self.last_tick:raise InvariantViolation('technology usage metered twice')
        if ctx.cycle!=self.cycle:self._reset(ctx.cycle)
        state=self.s;frame=state.frame
        for system in state.systems.instances.values():
            if system.online and state.systems.availability(system)>0:self.systems[system.owner,system.kind,system.hub]+=1
        for (owner,hub),tasks in state.compute.effective.items():
            for task,quantity in tasks.items():
                # Cyber capacity is not a civilian active scale when consumed
                # by an actual hostile operation; defense consumers must mark
                # their own useful task through IndustryActivity.
                used=state.industry_activity.compute_use.get((owner,hub,task),Q(0))
                if task=='finance':used=min(quantity,Q(20000,ctx.clock.cycle_ticks)) if state.ai.capability(owner) else Q(0)
                self.compute[owner,task]+=min(quantity,used)
        for formation in state.formations.rows.values():
            if formation.refitting:continue
            need=frame.military_need.get(formation.id,{})
            got=frame.military.get(formation.id,{})
            supplied=all(got.get(g,0)>=n for g,n in need.items())
            if supplied and formation.slot_a=='radio':self.formations[formation.owner,'radio',formation.id]+=1
        self._meter_entities(ctx)
        self.last_tick=ctx.tick
        if ctx.clock.is_cycle_end(ctx.tick):self._finish(ctx)

    def production(self,line,result):
        if self.cycle<0:raise InvariantViolation('usage cycle has not begun')
        if result.batch_mU:
            self.recipes[line.owner,line.recipe.id]+=Q(result.batch_mU,1000)
            self.lines[line.owner,line.id]+=Q(result.batch_mU,1000)
            if line.id in self.s.heat_supported:self.heat.add(line.id)

    def begin_tick(self,ctx):
        if ctx.cycle!=self.cycle:self._reset(ctx.cycle)

    def _online(self,owner,kind,ticks):
        return sum(min(Q(1),Q(n,ticks)) for (p,k,_),n in self.systems.items() if (p,k)==(owner,kind))

    def _meter(self,owner,tech,rule,ctx):
        state=self.s;kind=rule['kind'];meter=rule['meter'];ticks=ctx.clock.cycle_ticks
        if kind=='active_recipe_batches':return sum((self.recipes.get((owner,r),Q(0)) for r in rule['ref_ids']),Q(0))
        running=[state.initial.lines[lid] for (p,lid),n in self.lines.items() if p==owner and n and lid in state.initial.lines]
        if kind=='active_mode_lines':return Q(sum(state.initial.line_policies[l.id].manufacturing_mode in rule['ref_ids'] for l in running))
        if meter in SYSTEM_METERS:return self._online(owner,SYSTEM_METERS[meter],ticks)
        if meter in COMPUTE_METERS:return self.compute.get((owner,COMPUTE_METERS[meter]),Q(0))/(10000 if tech=='AI03' else 1000)
        if meter=='effective_total_compute':return sum((q for (p,t),q in self.compute.items() if p==owner),Q(0))/1000
        if meter=='running_high_temp_lines':return Q(sum(l.id in self.heat for l in running))
        if meter=='running_precision_agri_lines':return Q(sum(state.line_options.get(l.id,{}).get('precision_agri',False) for l in running))
        if meter=='running_gene_edited_lines':return Q(sum(state.line_options.get(l.id,{}).get('gene_edited',False) for l in running))
        if meter=='running_molecular_lines':return Q(sum(l.recipe.id=='P101' and 'ai_core' in state.line_options.get(l.id,{}).get('addons',()) and
             state.initial.facilities[l.id.rsplit('/line',1)[0]].profile=='S5' for l in running))
        if meter=='listed_companies':return Q(sum(c.listed and c.issuer==owner for c in state.initial.companies.values()))
        if meter=='central_bank_active':return Q(int(owner in state.macro.active_policies))
        if meter=='social_paid_ratio':return state.governance.social_ratio(owner)
        if meter in ('active_plan_regime','active_market_regime'):return Q(int(state.governance.active(owner,'F07' if tech=='F07' else 'F08')))
        if meter=='direct_foreign_equity_assets':return Q(sum(a.owner!=owner and state.authority.assets[aid].owner_shares.get(owner,0)>=100
             for aid,a in state.initial.assets.items() if not a.under_construction))
        if meter=='settled_external_financial_trades_capped':return Q(int(any(state.stock_market.reports[owner]) or any(state.bond_market.reports[owner])))
        if meter=='external_native_settlements_capped':
            amount=sum((paid for c in state.market_events if state.spot.venues[c.venue].currency==owner
                        for b,s,paid in c.cash_flows if b!=s and owner in (b,s)),0)
            return min(Q(1),Q(amount,100000))
        if meter=='online_service_constellations':return Q(int(state.orbit.coverage(owner) is not None))
        if meter=='online_surveillance_constellations':return Q(int(state.orbit.coverage(owner)==('land_sea',Q(9,10))))
        if meter=='supplied_radio_formations':return sum((min(Q(1),Q(n,ticks)) for (p,k,_),n in self.formations.items() if p==owner and k=='radio'),Q(0))
        if meter=='ready_combined_arms_formations':return sum((min(Q(1),Q(n,ticks)) for (p,k,_),n in self.formations.items() if p==owner and k=='combined'),Q(0))
        if meter=='ready_joint_command_formations':return sum((min(Q(1),Q(n,ticks)) for (p,k,_),n in self.formations.items() if p==owner and k=='joint'),Q(0))
        if meter=='flowing_intercontinental_cables':return Q(sum(p==owner for p,_ in self.cables))
        if meter=='active_automated_routes':return Q(sum(p==owner for p,_ in self.routes))
        if meter=='supplied_field_depots':return Q(sum(p==owner for p,_ in self.depots))
        if meter=='completed_megaprojects':
            megaprojects=getattr(state,'megaprojects',None)
            return Q(megaprojects.completed(owner)) if megaprojects is not None else Q(0)
        if meter=='restored_closed_mines':
            return Q(sum(p==owner and ctx.cycle-c>=CLOSED_MINE_CYCLES for p,c in self.mine_last.values()))
        if meter=='completed_canal_tunnel_dam':return Q(sum(l.owner==owner and l.mode=='water' and state.utilities.availability(l)>0 for l in state.utilities.links.values()))
        if meter=='owned_running_lab_lines_with_node_effect':
            labs=[l for l in running if l.recipe.id in rule['ref_ids']]
            if tech=='R01':return Q(len(labs))
            if tech=='R02':return Q(sum(state.frame.worked.get(l.id,{}).get('engineer',0)>0 for l in labs))
            if tech=='R05':
                network=state.systems.communication_components(owner)
                return Q(sum(l.hub in network for l in labs))
            if tech=='R06':return Q(sum(bool(state.research[owner].licenses) for l in labs))
            if tech=='R10':return Q(sum(any(owner in r.active_members and r.proposal.mode=='research' for r in state.contracts.joint_ventures.rows.values()) for l in labs))
            if tech=='R12':return Q(len(labs)*int(state.governance.active(owner,'R12')))
            if tech=='R08':return Q(sum(state.continuity.protects(owner,l.hub) for l in labs))
        if meter=='consented_climate_regions':
            climate=getattr(state,'climate',None)
            return Q(climate.active_regions(owner,ctx.cycle)) if climate is not None else Q(0)
        if meter=='online_offshore_mines':
            return Q(sum(mine.owner==owner and state.initial.map.regions[state.initial.map.hubs[mine.hub].region].terrain=='ocean'
                         and state.initial.assets[mid].availability>0 and self.lines.get((owner,mid),Q(0))>0
                         for mid,mine in state.initial.mines.items() if mid in state.initial.assets))
        # These meters depend on entities whose actual business implementations
        # must supply receipts. They remain zero until that receipt exists.
        if meter in PENDING_METERS:return Q(0)
        raise InvariantViolation('unregistered technology usage meter: '+meter)

    def _finish(self,ctx):
        state=self.s;values={p:{} for p in state.research};licensed={p:{} for p in state.research}
        rules=state.catalog.tables['scoring']['pillars']['tech']['active_scale_rules']
        for owner in values:
            for tech in state.research[owner].known:
                values[owner][tech]=self._meter(owner,tech,rules[tech],ctx)
        for buyer,records in state.contracts.licenses.items():
            for tech,record in records.items():
                if ctx.cycle>=record['end_cycle']:continue
                owner=record['licensor'];scale=values[buyer].get(tech,Q(0))
                licensed[owner][tech]=licensed[owner].get(tech,Q(0))+scale
        self.values,self.licensed=values,licensed

    def pending_meters(self):
        rules=self.s.catalog.tables['scoring']['pillars']['tech']['active_scale_rules']
        return {rule['meter'] for rule in rules.values() if rule.get('meter') in PENDING_METERS}

    def snapshot(self):
        return {'cycle':self.cycle,'last_tick':self.last_tick,'recipes':[[*k,v] for k,v in sorted(self.recipes.items())],
                'lines':[[*k,v] for k,v in sorted(self.lines.items())], 'systems':[[*k,v] for k,v in sorted(self.systems.items())],
                'compute':[[*k,v] for k,v in sorted(self.compute.items())], 'formations':[[*k,v] for k,v in sorted(self.formations.items())],
                'heat':self.heat,'values':self.values,'licensed':self.licensed,
                'cables':sorted(self.cables),'routes':sorted(self.routes),'depots':sorted(self.depots),
                'mine_last':{d:list(v) for d,v in sorted(self.mine_last.items())},'command_down':dict(sorted(self.command_down.items()))}
