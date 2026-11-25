"""Direct extension of the newly supplied elite agent, isolated from opponents.

The original opening, native kernels, economics and certified execution remain
the baseline. RSIdemo enhancements are explicit switches for ablation.
"""
from collections import Counter
import copy
from time import perf_counter
from .elite_base.elite_conditional_block import ConditionalBlockPlanner
from .elite_base.joint_adaptive import InventoryBelief,_opening
from .elite_base.joint_v2 import PORTFOLIO_REPORT,fibcost
from .elite_base.portfolio import labor_cost
from .elite_base.elite_execution_v2 import own_trade
from .elite_base import gpt6_rules as rules
from .elite_extensions import ContinuedPortfolio, remember, repair_idle
from .elite_compute_budget import ComputeBudget


class EnhancedPlanner(ConditionalBlockPlanner):
    def __init__(self,params):
        super().__init__()
        self.rsi_params=dict(params)
        if params.get('elite_future_search'):self.rsi_params['elite_continuation']=True
        self.block_temperatures=tuple(params.get('elite_temperatures',(0,)))
        self.block_evaluations=int(params.get('elite_block_evaluations',200))
        self.block_finalists=max(1,int(params.get('elite_block_finalists',1)))
        self.metrics=Counter()
        self.continuation=None
        self.future_search=None
        if any(params.get(k) for k in ('elite_continuation','elite_extra_sweeps','elite_sales_lag','elite_paid_assets','elite_animal_reuse','elite_replay_supply','elite_shadow','elite_future_search')):
            self.portfolio_type=ContinuedPortfolio

    def act(self,obs,configuration=None):
        action=super().act(obs,configuration)
        repair=self.rsi_params.get('elite_idle_repair') or (
            self.rsi_params.get('elite_terminal_repair') and int(obs['step'])//24==29)
        return repair_idle(self,obs,action) if repair else action

    def cash_available(self,obs):
        if not self.rsi_params.get('elite_exact_cash'):
            return super().cash_available(obs)
        return obs['farms'][obs['player']]['money']+sum(
            own_trade(obs['market'],key,n,True)[0]
            for key,n in obs['private']['shed'].items() if key in rules.PRODUCTS)

    def begin_day(self,obs):
        saved_proposals=None
        if self.future_search is not None:
            if self.rsi_params.get('elite_future_proposals'):saved_proposals=self.future_search.continuation()
            else:self.continuation=self.future_search.continuation()
        local=dict(obs,_rsi_params=self.rsi_params,_rsi_continuation=self.continuation)
        if saved_proposals is not None:local['_rsi_future_proposals']=saved_proposals
        shadow_stats=None
        if self.rsi_params.get('elite_shadow'):
            from .elite_shadow import scenario
            shadow_stats={}
            shadow=scenario(obs,belief=obs.get('_inventory_belief'),
                max_seconds=float(obs.get('_rsi_shadow_allowance',.75)),diagnostics=shadow_stats)
            if shadow is not None:local['_rsi_shadow']=shadow
            # Charge shadow time separately, preserving the parent's own
            # optional-search allowance instead of quietly starving its loop.
            local['_rsi_optional_deadline']=local.get('_rsi_optional_deadline',perf_counter())+shadow_stats['seconds']
        baseline=None;baseline_report=None
        if self.rsi_params.get('elite_certified_guard'):
            baseline=copy.copy(self);baseline.metrics=self.metrics.copy()
            base_params=dict(self.rsi_params,elite_extra_sweeps=0)
            baseline_obs=dict(local,_rsi_params=base_params)
            ConditionalBlockPlanner.begin_day(baseline,baseline_obs)
            baseline_report=PORTFOLIO_REPORT['days'].pop()
        super().begin_day(local)
        if baseline is not None:
            chosen,receipt=certified_choice(baseline,self,float(self.rsi_params.get('elite_certified_min_gain',100)))
            if chosen is baseline:
                self.__dict__.update(baseline.__dict__)
                PORTFOLIO_REPORT['days'][-1]=baseline_report
            PORTFOLIO_REPORT['days'][-1]['rsi_certified_guard']=receipt
            self.metrics['certified_guard_'+receipt['selected']]+=1
        model=self.economic_model
        stats=dict(getattr(model,'continuation_stats',{}))
        for key,n in stats.items():self.metrics['continuation_'+key]+=n
        PORTFOLIO_REPORT['days'][-1]['rsi_continuation']=stats
        PORTFOLIO_REPORT['days'][-1]['rsi_refinements']=getattr(model,'refinement_log',[])
        if shadow_stats is not None:PORTFOLIO_REPORT['days'][-1]['rsi_shadow']=shadow_stats
        if self.rsi_params.get('elite_continuation'):
            self.continuation=remember(obs,model.projects)
        if (self.rsi_params.get('elite_future_search') or self.rsi_params.get('elite_future_proposals')) and self.day<29:
            from .elite_future_search import FutureSearch
            self.future_search=FutureSearch(obs,model)
            PORTFOLIO_REPORT['days'][-1]['rsi_future_search']=self.future_search.metrics
        else:self.future_search=None


def certified_choice(baseline,candidate,minimum_gain):
    """Compare same-observation alternatives under one conservative labor curve."""
    models=[baseline.economic_model,candidate.economic_model]
    multipliers=[max(m.labor_multiplier[d] for m in models) for d in range(30)]
    scores=[]
    for planner,model in zip((baseline,candidate),models):
        work=[w*factor for w,factor in zip(model.work,multipliers)];work[model.day]=0
        scores.append(model.book.score(model.flow)-model.cost+getattr(model,'capital_credit',lambda:0)()
            -labor_cost(work)-fibcost(planner.crew)-planner.certification_uncovered)
    take=scores[1]>scores[0]+minimum_gain
    return (candidate if take else baseline),dict(baseline=scores[0],candidate=scores[1],
        gain=scores[1]-scores[0],minimum_gain=minimum_gain,selected='candidate' if take else 'baseline',
        basis='same public/private observation, common future labor multipliers, constructed current crew wage')


class ElitePolicy:
    def __init__(self,params=None):
        self.params=dict(params or {})
        self.planner=None;self.belief=InventoryBelief();self.last=-1;self.player=None
        self.metrics=Counter();self.day_stats=[];self.realization_records=[]
        # Evaluation adapters expect controller; this object owns its records.
        self.controller=self
        self.total_overage=0.
        self._reports=[]
        self.compute=ComputeBudget()
        self.switch_day=int(self.params.get('elite_macro_day',15))
        self.opening_history=[] if self.params.get('elite_dynamic_dn') else None
        self.dn_probed=False
        self.price_history={k:[] for k in rules.PRODUCTS}
        self.price_signal_model=None
        if self.params.get('elite_price_signal'):
            from .price_signal import PriceSignal
            self.price_signal_model=PriceSignal(
                self.params.get('elite_price_short_span',3),
                self.params.get('elite_price_long_span',9),
                self.params.get('elite_price_volatility_span',7))
        self.research = None
        if self.params.get('elite_research_diagnostics'):
            from .elite_research import ResearchStack
            self.research = ResearchStack(self.params, products=tuple(rules.PRODUCTS))

    def act(self,obs,configuration=None):
        if not self.params.get('elite_fast_dp'):return self._act(obs,configuration)
        from .elite_base import project_model
        previous=project_model.FAST_REUSE;project_model.FAST_REUSE=True
        try:return self._act(obs,configuration)
        finally:project_model.FAST_REUSE=previous

    def _act(self,obs,configuration=None):
        started=perf_counter();step=int(obs['step']);player=int(obs['player'])
        if step<=self.last or self.player!=player:
            self.planner=None;self.belief=InventoryBelief();self.metrics=Counter();self.day_stats=[]
            self.total_overage=0.
            self._reports=[]
            self.compute=ComputeBudget();self.switch_day=int(self.params.get('elite_macro_day',15))
            self.realization_records=[]
            self.opening_history=[] if self.params.get('elite_dynamic_dn') else None
            self.dn_probed=False
            self.price_history={k:[] for k in rules.PRODUCTS}
            if self.params.get('elite_price_signal'):
                from .price_signal import PriceSignal
                self.price_signal_model=PriceSignal(
                    self.params.get('elite_price_short_span',3),
                    self.params.get('elite_price_long_span',9),
                    self.params.get('elite_price_volatility_span',7))
            else:self.price_signal_model=None
            if self.params.get('elite_research_diagnostics'):
                from .elite_research import ResearchStack
                self.research = ResearchStack(self.params, products=tuple(rules.PRODUCTS))
        self.last=step;self.player=player
        if step%24==0 and self.price_signal_model is not None:
            prices=obs.get('market',{}).get('prices',{})
            inventory=obs.get('market',{}).get('inventory',{})
            for item in rules.PRODUCTS:
                if item in prices:
                    self.price_history[item].append(float(prices[item]))
        self.belief.update(obs)
        local=dict(obs,_inventory_belief=self.belief.snapshot())
        if self.params.get('elite_replay_supply'):
            local['_rsi_replay_strength']=max(0.0,min(1.0,float(
                self.params.get('elite_replay_strength',1.0))))
        if self.price_signal_model is not None:
            prices=obs.get('market',{}).get('prices',{})
            inventory=obs.get('market',{}).get('inventory',{})
            local['_rsi_price_signals']={item:self.price_signal_model.from_observation(
                obs,item,self.price_history[item]) for item in rules.PRODUCTS}
        deadlines=self.compute.deadlines(obs,started)
        if self.params.get('elite_dynamic_dn') and step==14*24 and not self.dn_probed:
            from .elite_dn import choose_now
            self.dn_probed=True
            take,receipt=choose_now(local,configuration,self.opening_history or [],self.params,
                                    self.compute.probe_deadline(obs,started))
            if take:self.switch_day=14
            self.realization_records.append(dict(kind='dynamic_dn',**receipt))
            self.metrics['dn_probes']+=1;self.metrics['dn_completed']+=receipt['completed']
            self.metrics['dn_early_switch']+=take;self.metrics['dn_seconds']+=receipt['seconds']
            self.opening_history=None
        if step<24*self.switch_day:
            if self.params.get('elite_opening_ev_fix'):
                from .elite_opening import act as opening_act
                action=opening_act(obs,configuration,self.params,self.metrics)
            else:action=_opening(obs,configuration)
        else:
            if self.planner is None:self.planner=EnhancedPlanner(self.params)
            # Reserve cumulative time for remaining original planning calls.
            # Only optional extra sweeps consult this deadline; this is not a
            # claim that the inherited baseline supports hard interruption.
            remaining_days=max(1,30-step//24)
            allowance=min(3.0,1+max(0,60-self.total_overage-5)/remaining_days)
            if self.params.get('elite_fixed_sweeps'):
                allowance=10 if self.total_overage<40 else 0
            local['_rsi_optional_deadline']=started+allowance*.65
            local['_rsi_structure_allowance']=min(.4,max(0,(50-self.total_overage)/remaining_days))
            if self.params.get('elite_compute_budget'):
                local['_rsi_optional_deadline']=deadlines['planning']
            local['_rsi_response_deadline']=deadlines['response']
            if self.params.get('elite_anytime_search'):
                local['_rsi_search_envelope']=lambda:self.compute.search_envelope(obs,started,
                    effort=float(self.params.get('elite_anytime_effort',1.)),
                    response=bool(self.params.get('elite_response_game')))
            local['_rsi_shadow_allowance']=min(float(self.params.get('elite_shadow_seconds',.75)),
                max(0,(50-self.total_overage)/remaining_days))
            action=self.planner.act(local,configuration)
            if step%24==0:
                self.metrics['macro_days']+=1
                if PORTFOLIO_REPORT['days']:
                    report=PORTFOLIO_REPORT['days'][-1]
                    self.metrics['portfolio_accepted']+=report.get('accepted',0)
                    self.metrics['block_selected']+=bool(report.get('block_search',{}).get('accepted'))
                    structural=report.get('block_search',{}).get('structure_search',{})
                    self.metrics['structure_generated']+=structural.get('generated',0)
                    self.metrics['structure_refined']+=structural.get('refined',0)
                    self.metrics['structure_selected']+=bool(report.get('block_search',{}).get('structure_selected'))
                    game=report.get('block_search',{}).get('response_game')
                    anytime=report.get('block_search',{}).get('anytime_search')
                    if anytime:
                        for key in ('seconds','evaluations','restarts','certifications','accepted'):
                            self.metrics['anytime_'+key]+=anytime[key]
                        self.metrics['anytime_selected']+=bool(anytime['selected'])
                        extra_game=anytime.get('response_game')
                        if extra_game:
                            self.metrics['anytime_game_calls']+=1
                            self.metrics['anytime_game_completed']+=bool(extra_game['completed'])
                            self.metrics['anytime_game_kept_incumbent']+=bool(extra_game['kept_incumbent'])
                            self.metrics['anytime_game_seconds']+=extra_game['seconds']
                    proposals=report.get('block_search',{}).get('future_proposals',{})
                    for key in ('matched','accepted','rejected','selected'):
                        self.metrics['future_proposal_'+key]+=proposals.get(key,0)
                    future_game=proposals.get('response_game')
                    if future_game:
                        self.metrics['future_game_calls']+=1
                        self.metrics['future_game_completed']+=bool(future_game['completed'])
                        self.metrics['future_game_kept_incumbent']+=bool(future_game['kept_incumbent'])
                        self.metrics['future_game_seconds']+=future_game['seconds']
                    if game:
                        self.metrics['game_calls']+=1;self.metrics['game_completed']+=game['completed']
                        self.metrics['game_kept_baseline']+=bool(game.get('kept_baseline'))
                        self.metrics['game_seconds']+=game['seconds']
                    self._reports.append(report)
            for key,n in self.planner.metrics.items():self.metrics[key]=n
            self.day_stats=[dict(report,execution=[r for r in self.planner.day_stats if r['day']==report['day']])
                            for report in self._reports]
        core_finished=perf_counter()
        if self.planner is not None and self.planner.future_search is not None and step%24>=2:
            future=self.planner.future_search
            before=dict(future.metrics)
            future.step(deadlines['normal'],max_proposals=int(self.params.get('elite_future_per_call',12)))
            for key,value in future.metrics.items():self.metrics['future_'+key]+=value-before.get(key,0)
        if self.opening_history is not None and step<14*24:
            self.opening_history.append(copy.deepcopy(obs))
        self.belief.remember(obs,action)
        if self.research is not None:
            research_receipt=self.research.observe(obs,action,done=(step>=719))
            self.metrics['research_observations']=self.research.metrics['observations']
            self.metrics['research_feature_count']=research_receipt['feature_count']
            self.metrics['research_price_risk']=research_receipt['price_risk']
        extra_search_seconds=0.
        if step%24==0 and self._reports:
            block=self._reports[-1].get('block_search',{})
            extra_search_seconds=sum(block.get(key,{}).get('seconds',0.)
                                     for key in ('anytime_search','future_proposals'))
        elapsed=self.compute.record(started,core_finished,optional_finished=perf_counter(),
            dawn=step%24==0,extra_search_seconds=extra_search_seconds)
        self.total_overage=self.compute.overage
        self.metrics['total_seconds']=self.compute.total
        self.metrics['optional_after_action_seconds']=self.compute.optional_seconds
        self.metrics['calls']+=1;self.metrics['max_us']=max(self.metrics['max_us'],int(elapsed*1e6))
        self.metrics['overage_us']=int(self.total_overage*1e6)
        return action
