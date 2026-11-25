# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Repeat whole-farm column exchange until improvement stalls or budget ends."""
from .portfolio import Portfolio

class ConvergedPortfolio(Portfolio):
    def optimize(self,budget,only_positions=None,sweeps=2):
        result=super().optimize(budget,only_positions,sweeps)
        # The baseline stops after a fixed pass while accepted columns may
        # have changed the prices and labor costs for all earlier positions.
        # Revisit those positions against the revised complete portfolio.
        for _ in range(2 if sweeps>=2 else 1):
            old=self.value;accepted=self.accepted
            result=super().optimize(budget,only_positions,2)
            if self.accepted==accepted or self.value-old<25:break
        return result
