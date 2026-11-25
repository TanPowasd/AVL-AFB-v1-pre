"""One budget for required decisions, optional work and counterfactual probes."""
from time import perf_counter


class ComputeBudget:
    def __init__(self):
        self.overage=0.;self.total=0.;self.calls=0;self.core_overages=[]
        self.optional_seconds=0.

    def remaining(self,obs):
        official=float(obs.get('remainingOverageTime',60))
        return max(0.,min(60-self.overage,official))

    def deadlines(self,obs,started):
        day,hour=divmod(int(obs['step']),24);days=max(1,30-day)
        # Budget required dawn planning before distributing discretionary time.
        recent=max(self.core_overages[-4:],default=1.0)
        reserve=min(45.,3.+days*max(.6,recent)*1.2)
        available=max(0.,self.remaining(obs)-reserve)
        extra=min(2.5,available/max(1.,days*.7)) if hour==0 else 0.
        # Leave normal callbacks a margin for returning/serializing the action.
        return {'normal':started+.80,'planning':started+1.+extra,
                'response':started+1.+extra,'extra_available':available,'reserve':reserve}

    def probe_deadline(self,obs,started):
        # Only one early handoff probe. Preserve at least 30 seconds for the
        # remaining mandatory planner calls and operation-level overshoot.
        return started+min(25.,max(0.,self.remaining(obs)-30.))

    def search_envelope(self,obs,started,now=None,effort=1.,response=False):
        """Allocate optional dawn work after the inherited plan exists.

        Current elapsed time is charged before allocation. Keep five seconds
        for operation overshoot plus a measured allowance for future required
        dawn calls. The caller also yields between objective evaluations and
        before route certificates. This is cooperative, not a hard interrupt.
        """
        now=perf_counter() if now is None else now
        day,hour=divmod(int(obs['step']),24);days=max(1,30-day)
        elapsed=max(0.,now-started);remaining=self.remaining(obs)
        required=max(.5,max(self.core_overages[-4:],default=.8),elapsed-1.)
        reserve=5.+(days-1)*required*1.2
        available=max(0.,remaining-max(0.,elapsed-1.)-reserve)
        # At most six extra seconds per dawn; only actual elapsed overage is
        # charged, so unused quota is automatically available on later days.
        optional=min(6.,available/days)*max(0.,min(1.,float(effort))) if hour==0 else 0.
        normal_slack=max(0.,.85-elapsed)
        finish=now+normal_slack+optional
        response_reserve=min(.5,(finish-now)*.25) if response else 0.
        return {'search':finish-response_reserve-.10,'finish':finish,
                'optional_seconds':optional,'remaining':remaining,'reserve':reserve,
                'response_reserve':response_reserve}

    def record(self,started,core_finished,optional_finished=None,dawn=False,extra_search_seconds=0.):
        ended=perf_counter() if optional_finished is None else optional_finished
        elapsed=max(0.,ended-started);self.overage+=max(0.,elapsed-1.)
        self.total+=elapsed;self.calls+=1
        self.optional_seconds+=max(0.,ended-core_finished)
        if dawn:self.core_overages.append(max(0.,core_finished-started-extra_search_seconds-1.))
        return elapsed
