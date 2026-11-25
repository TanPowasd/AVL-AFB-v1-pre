"""Finite response-game CFR utilities.

The live agent never observes the opponent's private inventory.  This module
therefore solves only a small, explicit normal-form game whose columns are
publicly motivated response hypotheses.  It is useful for ranking complete
plans and for measuring exploitability; it is not a claim that the full game
has been solved to a Nash equilibrium.
"""

from dataclasses import dataclass
import math


def _probabilities(regrets):
    positive = [max(0.0, float(x)) for x in regrets]
    total = sum(positive)
    if total <= 1e-15:
        return [1.0 / len(regrets)] * len(regrets)
    return [x / total for x in positive]


def _validate_matrix(payoffs):
    rows = [list(map(float, row)) for row in payoffs]
    if not rows or not rows[0]:
        raise ValueError("payoff matrix must be non-empty")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("payoff matrix rows must have equal length")
    if any(not math.isfinite(value) for row in rows for value in row):
        raise ValueError("payoff matrix must contain finite values")
    return rows


@dataclass(frozen=True)
class CFRResult:
    """The averaged strategies and diagnostics from a zero-sum solve."""

    row_strategy: tuple
    column_strategy: tuple
    value: float
    row_exploitability: float
    column_exploitability: float
    iterations: int

    def as_dict(self):
        return {
            "row_strategy": list(self.row_strategy),
            "column_strategy": list(self.column_strategy),
            "value": self.value,
            "row_exploitability": self.row_exploitability,
            "column_exploitability": self.column_exploitability,
            "iterations": self.iterations,
        }


class CFRSolver:
    """Regret matching for a finite two-player zero-sum response game.

    Rows are our complete candidate plans and columns are hypothetical
    opponent responses.  Direct regret updates are equivalent to CFR on this
    one-information-set normal-form game and are considerably cheaper than
    enumerating action histories.
    """

    def __init__(self, payoffs, iterations=512):
        self.payoffs = _validate_matrix(payoffs)
        self.iterations = max(1, int(iterations))

    def solve(self):
        matrix = self.payoffs
        rows, cols = len(matrix), len(matrix[0])
        row_regret = [0.0] * rows
        col_regret = [0.0] * cols
        row_sum = [0.0] * rows
        col_sum = [0.0] * cols
        for _ in range(self.iterations):
            row = _probabilities(row_regret)
            col = _probabilities(col_regret)
            row_payoffs = [sum(col[j] * matrix[i][j] for j in range(cols)) for i in range(rows)]
            expected = sum(row[i] * row_payoffs[i] for i in range(rows))
            # The column player's utility is the negative of our payoff.
            col_payoffs = [-sum(row[i] * matrix[i][j] for i in range(rows)) for j in range(cols)]
            col_expected = -expected
            for i in range(rows):
                row_regret[i] += row_payoffs[i] - expected
            for j in range(cols):
                col_regret[j] += col_payoffs[j] - col_expected
            for i in range(rows):
                row_sum[i] += row[i]
            for j in range(cols):
                col_sum[j] += col[j]
        row_avg = tuple(_probabilities(row_sum))
        col_avg = tuple(_probabilities(col_sum))
        value = sum(row_avg[i] * col_avg[j] * matrix[i][j]
                    for i in range(rows) for j in range(cols))
        best_row = max(sum(col_avg[j] * matrix[i][j] for j in range(cols)) for i in range(rows))
        best_col = min(sum(row_avg[i] * matrix[i][j] for i in range(rows)) for j in range(cols))
        return CFRResult(row_avg, col_avg, value,
                         max(0.0, best_row - value),
                         max(0.0, value - best_col), self.iterations)


def rank_candidates(payoffs, candidate_names=None, response_names=None,
                    iterations=512, risk_penalty=0.25):
    """Return CFR diagnostics and conservative scores for candidate rows.

    The score is the candidate's expected payoff against the learned response
    mix minus a row-specific downside penalty. ``worst_response`` remains
    available so callers can impose a hard downside gate.
    """
    matrix = _validate_matrix(payoffs)
    result = CFRSolver(matrix, iterations).solve()
    names = list(candidate_names or [f"candidate_{i}" for i in range(len(matrix))])
    responses = list(response_names or [f"response_{j}" for j in range(len(matrix[0]))])
    if len(names) != len(matrix) or len(responses) != len(matrix[0]):
        raise ValueError("candidate_names/response_names do not match matrix")
    rows = []
    for i, name in enumerate(names):
        expected = sum(result.column_strategy[j] * matrix[i][j] for j in range(len(responses)))
        worst_index = min(range(len(responses)), key=lambda j: matrix[i][j])
        rows.append({
            "name": name,
            "expected": expected,
            "worst": min(matrix[i]),
            "worst_response": responses[worst_index],
            "score": expected - float(risk_penalty) * max(0.0, expected - min(matrix[i])),
        })
    rows.sort(key=lambda row: (row["score"], row["worst"], row["expected"]), reverse=True)
    return {"cfr": result.as_dict(), "candidates": rows,
            "responses": responses, "risk_penalty": float(risk_penalty)}


def response_matrix_from_summaries(summaries):
    """Convert response-game summary rows into a finite payoff matrix.

    This adapter deliberately uses the already-computed hypothetical scenario
    values.  It never turns the number of positive scenarios into a real win
    probability.
    """
    matrix = []
    for summary in summaries:
        if isinstance(summary, dict) and "response_values" in summary:
            matrix.append(list(summary["response_values"]))
        elif isinstance(summary, (list, tuple)):
            matrix.append(list(summary))
        else:
            raise TypeError("each summary must provide response_values")
    return _validate_matrix(matrix)
