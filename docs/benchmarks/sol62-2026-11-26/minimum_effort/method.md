# Minimum Effort: standalone C++20 submission

Start: 2026-11-25T16:06:24Z. End: 2026-11-25T16:17:08Z.

Statement and public samples used: only the supplied `input/README.md`. The other tests here are locally constructed. No official test set, performance coefficient, or score was observed.

## Shared reductions

Append times can be compacted to `T+j`. This preserves rank (newer submissions lose score ties), and it weakly decreases every charged time distance: historical/new distances decrease, appended/appended distances shrink, and historical distances remain unchanged.

After fixing relative order, each cost exponent is `score(higher) - score(lower) + known_time_distance`. An adjacent pair must have score difference at least one when the higher row is newer, otherwise a tie suffices. All constraints are difference lower bounds between scores, plus box constraints. Historical score endpoints are constants.

## Fixed shapes

Fill the unspecified final slots with the sorted historical rows. A Fenwick tree on active final slots gives the target-ranked row before each append; each owner's best is just its least active final slot. The exact score optimizer starts with the componentwise least feasible scores. It propagates score bounds, separates uncoupled components, and treats single-coordinate objectives directly by convex slope search.

In a coupled component, optimize simultaneous moves of size `+h` or `-h` on a set of variables by a min-cut. For one score-difference term with current exponent `e`, after removing the common positive factor `2^h-1`, the signed change across the boundary is either `+2^e` or `-2^(e-h)`. These two cut cases are represented by a nonnegative arc of capacity `2^e-2^(e-h)` and unary arcs. A bound with slack less than `h` is enforced by a dominating arc. The algorithm line-searches each improving direction, and halves `h` until unit moves are locally optimal.

For exactness, compare an arbitrary feasible score vector to the current vector. Decompose their integer difference into unit increases of positive-coordinate level sets and unit decreases of negative-coordinate level sets. For each objective term the changes to its score difference all have the same sign, so convexity makes the combined cost change at least the sum of the individual unit moves' changes evaluated at the current vector. Any constituent unit move obeys each difference constraint because a decrease of that constraint's slack is also present in the full feasible difference. Thus if no subset unit move improves, no feasible vector improves.

Capacities, flow, and objectives are compared exactly using runs of set binary bits, including carries and borrows. This avoids allocating memory proportional to the numerical magnitude of the exponents. The modulo is applied only after optimizing.

## General group

Branch over the next currently failing owner and its new row's relative slot. Historically cold spans can be coalesced: retain any historical row which can become one of the relevant target rows, and the historical best of every owner who could fail. New rows inside a cold span retain their relative order and their score interval with respect to the two retained boundaries. Their precise position among omitted historical rows cannot change endpoint identity or threshold crossings. If a last append can finish, its score is not in any charge; it exists exactly when the target row can be overtaken without displacing the worst-ranked currently tight user. That requirement is another difference bound, so the final score itself is not enumerated.

Prune by available append count, including currently tight owners unavoidably displaced by the smallest target of a failing owner, and compare optimistic costs to the incumbent as exact integers. Solve each surviving terminal shape with the same minimizer used for fixed shapes.

## Limitations

The general search is exponential in the worst case. There is no CPU cutoff for `H<=2`. For larger horizons the code stops the search near the applicable CPU budget and outputs its best feasible incumbent if one was found. Such a result is not guaranteed optimal; if the cutoff occurs without any incumbent, the numeric fallback is 0 and is not a certificate. The fixed solver has no cutoff and optimizes exactly upon completion, but no worst-case claim is made for the official time limits. See `test-record.txt` for observed results and the scripts for reproducibility.
