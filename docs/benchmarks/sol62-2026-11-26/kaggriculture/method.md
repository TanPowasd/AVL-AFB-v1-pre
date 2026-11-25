Started 2026-11-25T16:06:39Z; completed 2026-11-25T16:18:09Z (execution environment UTC).

# Kaggriculture strategy

Open with geese for manure revenue and a mixed crop cohort for earlier cash and the first melon payout. Expand land and staffing as funds permit. Forecast market inventory from unlocked shop demand, expected future unlocks, public rival crops and our own production. Score crops by marginal cash per occupied day, and compare animals against remaining revenue, purchase and feed costs. A finite-horizon dynamic program schedules feed and care by production date, starvation risk, care bonus and input opportunity cost.

Daily routes allocate weighted jobs with travel, pickups and return times included. The strategy protects inputs from own sales, prioritizes time-sensitive crops and market orders, and routes final-day harvests to the shed manually.

## Local evidence

40 replays finished 719 steps each. The candidate's actions met the fixture's checks for JSON serialization, correct hands counts and no more than ten orders. Max single timed act call on either side: 0.142795 s.

| Fixture | Runs | Cash results |
|---|---:|---|
| Synthetic passive opponent | 12 | Candidate cash 68,267–176,731 |
| Candidate mirror | 6 | Near-symmetric cash |
| Earlier own variant, both seats | 16 | 10 higher cash, 6 lower cash, mean candidate lead 480 |
| Forced all-PET_CAFE path, passive | 3 | Candidate cash 128,372 |
| Forced all-PIZZA_SHOP path, passive | 3 | Candidate cash 186,298–200,336 |

Raw results in tests/. Reproduce from this directory:

    PYTHONDONTWRITEBYTECODE=1 python local_test.py pass 101 102 103 104 105 106 107 108 109 110 111 112
    PYTHONDONTWRITEBYTECODE=1 python local_test.py mirror 301 302 303 304 305 306
    PYTHONDONTWRITEBYTECODE=1 python local_test.py v6 201 202 203 204 205 206 207 208
    PYTHONDONTWRITEBYTECODE=1 python local_test.py v6 201 202 203 204 205 206 207 208 --swap
    PYTHONDONTWRITEBYTECODE=1 python local_test.py pass 1001 1002 1003 --shops=PET_CAFE
    PYTHONDONTWRITEBYTECODE=1 python local_test.py pass 1001 1002 1003 --shops=PIZZA_SHOP
    python -m zipfile -t kaggriculture.zip

The partial harness approximates documented behavior and imports the candidate's price function. It does not establish official rule or RNG equivalence. Stress cases force specific town compositions. Logs count infeasible subactions, which the documented rules distinguish from structurally invalid actions. The tested opponents are synthetic or written during this same run. No official rules engine, score, reference opponents or token measurement was used. Forecasts and routes are heuristic, not optimal.

Deliverable: kaggriculture.zip (root main.py only). Source: solution/main.py. The v6_copy.py file is retained solely to reproduce the local comparison and not packaged.
