# Winged Ghost Rabbit: verified six-round policy

- Start (UTC): 2026-11-25T16:06:32Z
- End (UTC): 2026-11-25T16:10:36Z
- Submission: `solution.cpp` (standalone C++20 interactive program).

## Strategy

The six vertices with Eisenstein norm 1 form a hexagon. Each has two neighbors on the hexagon and exactly one outward neighbor, twice its coordinate, which is an inert prime. Close the outward neighbor before the rabbit can use it. With at most two surviving exits, putting the sonar at one of them distinguishes distance 0 from distance 2 and gives exact location.

At rate 4/3 the first three rounds can spend 1, 1, then 2 traps. Round 1 closes (2, 0). Round 2 closes the known unit vertex's prime exit. If the rabbit retreats to the initial vertex, round 3 closes its two remaining exits and wins. If it advances around the ring, round 3 closes its prime exit and the next unit vertex, forcing a retreat. Round 4 traps the initial vertex and confines the rabbit to the remaining two vertices. Round 5 makes no placement so the rabbit is forced back while credit accumulates. Round 6 traps its last exit. Both directions around the ring are included in the policy. The strategy does not need to handle a jump or a bounce because neither is reachable.

## Local verification

The clean-room `test_protocol.py` replays the actual compiled program from scratch on every distinct legal rabbit history. It computes graph distances by breadth-first search, checks arithmetic progressions directly by endpoint sums and endpoint distance before bounce, applies the exact bucket, and requires a clean exit from the program after capture. The run found four complete histories: two captured on round 3 and two on round 6. See `test-results.txt` for the compile/test commands, observed traces, and artifact hashes.

`ring_game.py` separately builds the finite reachability game for strategies that never give the rabbit a prime exit. Only the six unit vertices and their six outward prime neighbors matter: removing attempted traps elsewhere saves credit and does not change moves or make legal placements illegal. A bounced attempt at the known location can similarly be omitted. Every remaining action and every possible rabbit move is enumerated, with all successor states required to win. At exact 4/3 the start first enters the winning set in layer 6. An abstract credit model for every q < 4/3 tracks a bound in thirds and whether that bound is strict. It allows all actual legal purchases, potentially more; even this more permissive game has a losing start. That is a lower bound only for the class of strategies that disallow all prime moves, not for the full problem.

## Limits and score interpretation

No official interactor, hidden opponents, external references, or public baselines were used. There is no official score or official verdict to report. The rule formulas at the submitted rate give raw score 80 and normalized score 200/7 (about 28.57) **conditional on capture**; they are not judge measurements. The local exhaustive conclusion covers the supplied rules, and does not establish global rate optimality for strategies that deliberately permit prime moves.
