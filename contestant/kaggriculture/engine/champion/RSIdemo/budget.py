"""Cooperative, per-callback deadline shared by economic and route search."""
from time import perf_counter


class BudgetExhausted(Exception):
    pass


_deadline = float('inf')


def set_deadline(value):
    global _deadline
    _deadline = value


def check():
    if perf_counter() >= _deadline:
        raise BudgetExhausted()


def remaining():
    return max(0, _deadline - perf_counter())
