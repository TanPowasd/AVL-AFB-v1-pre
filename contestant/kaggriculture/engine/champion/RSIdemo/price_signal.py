"""Causal market-price signals for risk-aware portfolio scoring."""

import math
import statistics


def _ema(values, span):
    values = [float(v) for v in values]
    if not values:
        return 0.0
    alpha = 2.0 / (max(1, int(span)) + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _safe_return(a, b):
    return 0.0 if abs(float(b)) < 1e-12 else (float(a) - float(b)) / abs(float(b))


class PriceSignal:
    def __init__(self, short_span=3, long_span=9, volatility_span=7):
        self.short_span = max(1, int(short_span))
        self.long_span = max(self.short_span, int(long_span))
        self.volatility_span = max(2, int(volatility_span))

    def calculate(self, history, current=None, inventory=None, demand=None, own_exposure=0.0,
                  inventory_baseline=10000., inventory_scale=100.):
        values = [float(v) for v in history if v is not None and math.isfinite(float(v))]
        current = float(current if current is not None else (values[-1] if values else 0.0))
        short = _ema(values[-self.short_span:], self.short_span) if values else current
        long = _ema(values[-self.long_span:], self.long_span) if values else current
        returns = [_safe_return(values[i], values[i - 1]) for i in range(1, len(values))]
        recent_returns = returns[-self.volatility_span:]
        volatility = statistics.pstdev(recent_returns) if len(recent_returns) > 1 else 0.0
        momentum = _safe_return(short, long)
        deviation = _safe_return(current, long)
        supply_shock = 0.0
        if inventory is not None and demand is not None:
            supply_shock = (float(inventory)-inventory_baseline-float(demand))/max(1.,inventory_scale)
        # Risk rises with high volatility and a large inventory exposure.  It
        # is a ranking feature, never a claim about the next shop or price.
        # Town market inventory includes a 10,000-unit reference offset. It is
        # not our warehouse exposure and must not saturate the risk measure.
        exposure = max(0.0, float(own_exposure))
        risk = max(0.0, min(1.0, 2.0 * volatility + .25 * min(1.0, exposure / 100.0)))
        return {"current": current, "ema_short": short, "ema_long": long,
                "momentum": momentum, "deviation": deviation,
                "volatility": volatility, "supply_shock": supply_shock,
                "risk": risk}

    def from_observation(self, observation, item, history=()):
        market = (observation or {}).get("market", {})
        prices = market.get("prices", {}) if isinstance(market, dict) else {}
        inventory = market.get("inventory", {}) if isinstance(market, dict) else {}
        current = prices.get(item, 0.0)
        private=observation.get('private',{})
        exposure=private.get('shed',{}).get(item,0)+sum(b.get(item,0) for b in private.get('inventories',[]))
        return self.calculate(history, current=current,
                              inventory=inventory.get(item, 0.0),
                              demand=None,own_exposure=exposure)

    @staticmethod
    def proposal_multiplier(signal, momentum_weight=0.20, risk_weight=0.10,
                            limit=0.10):
        """Bound a causal signal before it biases proposal generation.

        The exact market book still scores and certifies the proposal.  The
        bound prevents a noisy trend from moving a whole farm allocation.
        """
        signal = signal or {}
        raw = float(momentum_weight) * float(signal.get("momentum", 0.0))
        raw -= float(risk_weight) * float(signal.get("risk", 0.0))
        bound = abs(float(limit))
        return max(1.0 - bound, min(1.0 + bound, 1.0 + raw))
