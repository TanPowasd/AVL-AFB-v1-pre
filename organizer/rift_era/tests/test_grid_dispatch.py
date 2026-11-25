"""grid_dispatch_v1: the fast HV-grid dispatcher equals the 1 mU oracle exactly.

dispatch() with edges runs dispatch_grid(); dispatch_reference() is the
specified 1 mU sequence. Every field but the `iterations` work counter must
match: receipts (incl. per-edge flows and tolls), received, spent,
source_remaining, edge_remaining.
"""
import random
import time
import unittest

from organizer.rift_era.engine.numeric import Q
from organizer.rift_era.engine.power import Load, PowerEdge, Supply, dispatch, dispatch_grid, dispatch_reference


def outcome(result):
    return result.receipts, result.received, result.spent, result.source_remaining, result.edge_remaining


def random_case(r):
    hubs = [str(i) for i in range(r.randrange(2, 6))]
    supplies = tuple(Supply(f's{i}', r.choice(hubs), 'A', r.randrange(40), Q(r.randrange(5), r.choice((1, 2, 3))))
                     for i in range(r.randrange(1, 6)))
    loads = []
    for i in range(r.randrange(1, 7)):
        budget, capped = Q(r.randrange(80)), r.random() < 0.3
        loads.append(Load(f'l{i}', r.choice(hubs), 'B', r.randrange(1, 40), r.randrange(6),
                          Q(r.randrange(1, 9), r.choice((1, 2))), budget,
                          user_budget_ru_mU=budget * r.randrange(0, 5) / 4 if capped else None,
                          user_cap_ru=Q(r.randrange(4)) if capped else None))
    edges = []
    for i in range(r.randrange(1, 6)):
        a, b = r.sample(hubs, 2)
        edges.append(PowerEdge(f'e{i}', a, b, r.randrange(0, 30), Q(r.choice((1000, 961, 900, 750, 500)), 1000),
                               r.choice('XY'), Q(r.randrange(3), r.choice((10, 100)))))
    return supplies, tuple(loads), tuple(edges)


class GridDispatchParityTests(unittest.TestCase):
    def test_random_small_grids_match_oracle(self):
        r = random.Random(20261106)
        for case in range(400):
            supplies, loads, edges = random_case(r)
            fast, slow = dispatch(case, 3, supplies, loads, edges), dispatch_reference(case, 3, supplies, loads, edges)
            self.assertEqual(outcome(fast), outcome(slow), case)

    def scenario(self):
        """Game-shaped grid: six hubs on a lossy chain, three with no local plant,
        many small loads across ranks, half on user-capped city funding."""
        r = random.Random(7)
        hubs = [f'h{i}' for i in range(6)]
        supplies = tuple(Supply(f'plant{i}', hubs[i % 6], 'A', r.randrange(200, 900), Q(r.randrange(2, 9), 2))
                         for i in range(9) if i % 6 not in (1, 3, 4))
        loads = []
        for i in range(40):
            budget, capped = Q(r.randrange(200, 3000)), i % 2 == 0
            loads.append(Load(f'load{i}', r.choice(hubs), 'B', r.randrange(20, 160), r.choice((0, 1, 1, 3, 3, 5)),
                              Q(r.randrange(4, 14), 2), budget,
                              user_budget_ru_mU=budget / 2 if capped else None, user_cap_ru=Q(3) if capped else None))
        edges = tuple(PowerEdge(f'line{i}', a, b, 400, Q(961, 1000), 'A', Q(1, 100))
                      for i, (a, b) in enumerate(zip(hubs, hubs[1:])))
        return supplies, tuple(loads), edges

    def test_game_shaped_grid_matches_oracle_with_real_remote_flow(self):
        supplies, loads, edges = self.scenario()
        started = time.perf_counter()
        fast = dispatch(1000, 5, supplies, loads, edges)
        fast_seconds = time.perf_counter() - started
        slow = dispatch_reference(1000, 5, supplies, loads, edges)
        self.assertEqual(outcome(fast), outcome(slow))
        self.assertGreater(sum(r.received_mU for r in fast.receipts if r.edges), 500)  # the grid really carries power
        self.assertLess(fast.iterations, slow.iterations)
        self.assertLess(fast_seconds, 5)

    def test_order_independence_and_dispatch_routes_to_grid(self):
        supplies, loads, edges = self.scenario()
        self.assertEqual(dispatch(1, 0, supplies, loads, edges), dispatch_grid(1, 0, supplies, loads, edges))
        flipped = dispatch(1, 0, tuple(reversed(supplies)), tuple(reversed(loads)), tuple(reversed(edges)))
        self.assertEqual(outcome(flipped), outcome(dispatch(1, 0, supplies, loads, edges)))

    def test_invalid_inputs_raise_like_oracle(self):
        edge = (PowerEdge('e', 'a', 'b', 1, Q(1), 'A'),)
        for supplies, loads in (((Supply('s', 'a', 'A', 1, Q(1)),) * 2, ()),
                                ((), (Load('l', 'a', 'B', -1, 0, Q(1), Q(1)),))):
            with self.assertRaises(ValueError):
                dispatch(1, 0, supplies, loads, edge)
            with self.assertRaises(ValueError):
                dispatch_reference(1, 0, supplies, loads, edge)


if __name__ == '__main__':
    unittest.main()
