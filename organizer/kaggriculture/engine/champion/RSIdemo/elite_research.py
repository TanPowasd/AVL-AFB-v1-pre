"""Optional, observation-only research instrumentation.

ADP/PDS, TD(lambda), price signals, and flow checks are deliberately kept
outside the elite action path until a held-out ablation proves a win-rate
increase. When enabled, this class records causal features and value updates
for replay analysis; it never reads a seed, future shop, or rival private
inventory and never changes the returned action.
"""

from collections import Counter

from .adp_pds import ADPPDS, StateEncoder, cash_margin
from .price_signal import PriceSignal
from .td_lambda import TDLambda


class ResearchStack:
    def __init__(self, params=None, products=()):
        params = dict(params or {})
        encoder = StateEncoder(products=products)
        self.adp = ADPPDS(encoder, gamma=float(params.get("research_gamma", 1.)))
        self.td = TDLambda(encoder,
                           gamma=float(params.get("research_gamma", 1.)),
                           lam=float(params.get("research_lambda", .8)),
                           learning_rate=float(params.get("research_learning_rate", .01)),normalized=True)
        self.signal = PriceSignal(params.get("research_short_span", 3),
                                  params.get("research_long_span", 9),
                                  params.get("research_volatility_span", 7))
        self.products = tuple(products)
        self.history = {item: [] for item in self.products}
        self.previous = None
        self.metrics = Counter()

    def _price_history(self, observation):
        market = (observation or {}).get("market", {})
        prices = market.get("prices", {}) if isinstance(market, dict) else {}
        for item in self.products:
            if item in prices:
                self.history[item].append(float(prices[item]))

    def observe(self, observation, action=None, reward=None, done=False):
        """Record one transition and return diagnostics for logging only."""
        self._price_history(observation)
        pds=self.adp.post_decision(observation,action or {'farmer':['PASS']})
        gap=cash_margin(pds.observation)
        if self.previous is not None:
            realized=(gap-self.previous[1])/10000. if reward is None else float(reward)
            self.td.update(self.previous[0], realized, pds.features, done=done)
        features = pds.features
        q_value = self.td.value(features)
        risks = {item: self.signal.from_observation(observation,item,self.history[item]).get("risk", 0.0)
                 for item in self.products}
        self.previous = (dict(features), gap) if not done else None
        self.metrics["observations"] += 1
        self.metrics["feature_count"] = len(features)
        self.metrics["last_q_value"] = q_value
        self.metrics["mean_price_risk"] = sum(risks.values()) / len(risks) if risks else 0.0
        if done:
            self.td.reset()
        return {"q_value": q_value, "price_risk": risks,
                "feature_count": len(features), "action_changed": False}
