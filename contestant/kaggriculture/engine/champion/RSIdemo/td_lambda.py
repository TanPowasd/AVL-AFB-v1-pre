"""Linear value approximation with accumulating TD(lambda) traces."""

from numbers import Real
import math
from .adp_pds import StateEncoder


def _features(value, encoder, action=None):
    """Distinguish encoded numeric features from nested observations."""
    if isinstance(value, dict) and all(isinstance(item, Real) for item in value.values()):
        return value
    return encoder.encode(value, action)


class TDLambda:
    def __init__(self, encoder=None, gamma=1.0, lam=0.8, learning_rate=0.01,
                 trace_cutoff=1e-7, normalized=False):
        self.encoder = encoder or StateEncoder()
        self.gamma = min(1.0, max(0.0, float(gamma)))
        self.lam = min(1.0, max(0.0, float(lam)))
        self.learning_rate = float(learning_rate)
        self.trace_cutoff = float(trace_cutoff)
        self.normalized = bool(normalized)
        self.weights = {}
        self.trace = {}

    def reset(self):
        self.trace.clear()

    def value(self, state, action=None):
        features = _features(state, self.encoder, action)
        return sum(self.weights.get(k, 0.0) * float(v) for k, v in features.items())

    def update(self, state, reward, next_state=None, done=False, action=None, next_action=None):
        features = _features(state, self.encoder, action)
        next_features = {} if done or next_state is None else _features(next_state, self.encoder, next_action)
        current = sum(self.weights.get(k, 0.0) * float(v) for k, v in features.items())
        nxt = 0.0 if done else sum(self.weights.get(k, 0.0) * float(v)
                                   for k, v in next_features.items())
        delta = float(reward) + self.gamma * nxt - current
        for key in list(self.trace):
            self.trace[key] *= self.gamma * self.lam
            if abs(self.trace[key]) < self.trace_cutoff:
                del self.trace[key]
        for key, value in features.items():
            self.trace[key] = self.trace.get(key, 0.0) + float(value)
        rate = self.learning_rate
        if self.normalized:
            rate /= max(1.0, sum(abs(v)*abs(self.trace.get(k,0.)) for k,v in features.items()))
        for key, eligibility in self.trace.items():
            self.weights[key] = self.weights.get(key, 0.0) + rate * delta * eligibility
            if not math.isfinite(self.weights[key]):
                raise FloatingPointError('TD value update diverged; refuse model')
        if done:
            self.reset()
        return {"delta": delta, "value": current, "next_value": nxt,
                "trace_size": len(self.trace)}

    def fit_episode(self, transitions):
        self.reset()
        diagnostics = []
        for transition in transitions:
            if len(transition) == 4:
                state, reward, next_state, done = transition
                action = next_action = None
            else:
                state, action, reward, next_state, done = transition
                next_action = None
            diagnostics.append(self.update(state, reward, next_state, done, action, next_action))
        return diagnostics
