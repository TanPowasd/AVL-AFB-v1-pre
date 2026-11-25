"""Generate solvable general cases with H=10 (including the historical batches)."""

import argparse
import random
from pathlib import Path


def generate(seed, index):
    rng = random.Random(seed + 7919 * index)
    actors = 3 + index % 8
    players = actors + 5
    history_size = 104 + actors
    latest = 10**9 - 10

    targets = [history_size] * 5
    family = index // 8 % 4
    if family == 0:
        targets.extend(rng.randint(26, 35) for _ in range(actors))
    elif family == 1:
        targets.extend(12 + 8 * j + rng.randrange(5) for j in range(actors))
    elif family == 2:
        targets.extend(28 + rng.choice([0, 0, 0, 3, 4, 8]) for _ in range(actors))
    else:
        targets.extend([31] * actors)

    if index % 3 == 0:
        scores = sorted(rng.sample(range(700_000_000, 950_000_000), history_size), reverse=True)
    else:
        scores = [99_999 - (j // (1 + index % 4)) * (1 + index % 7) for j in range(history_size)]
    span = 8_000 if index % 4 < 2 else 900_000_000
    times = rng.sample(range(latest - span, latest), history_size)
    times[0] = latest
    history = [
        (1 + j % 5 if j < 104 else 6 + j - 104, score, times[j])
        for j, score in enumerate(scores)
    ]
    rng.shuffle(history)
    lines = [f"{players} {history_size} 0", " ".join(map(str, targets))]
    lines.extend(f"{player} {score} {time}" for player, score, time in history)
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=19374)
    parser.add_argument("--count", type=int, default=96)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    for index in range(args.count):
        (args.output / f"s{index:03}.in").write_text(
            generate(args.seed, index), encoding="ascii", newline="\n"
        )


if __name__ == "__main__":
    main()
