"""Generate legal fixed-shape cases from official templates or from fresh schedules."""

import argparse
from itertools import groupby
from pathlib import Path
import random
import zipfile

MAX_VALUE = 10**9


def read_template(data, witnesses, number):
    raw = iter(map(int, data.read(f"{number}.in").split()))
    players, history_size, kind = next(raw), next(raw), next(raw)
    if kind != 1:
        raise ValueError(f"Template {number} is not fixed-shape")
    targets = [next(raw) for _ in range(players)]
    history = [(next(raw), next(raw), next(raw)) for _ in range(history_size)]
    moves = [(next(raw), next(raw)) for _ in range(next(raw))]
    scores = list(map(int, witnesses.read(f"{number}.scores").split()))
    if len(scores) != len(moves):
        raise ValueError(f"Template {number} has inconsistent witness length")
    return targets, history, moves, scores


def random_score_map(history, witness, rng, variant):
    if variant in (0, 3):
        return None
    original = sorted({score for _, score, _ in history}.union(witness))
    if variant == 1:
        changed = sorted(rng.sample(range(1, MAX_VALUE + 1), len(original)))
    else:
        value = rng.randint(1, MAX_VALUE - 16 * len(original))
        changed = []
        for _ in original:
            changed.append(value)
            value += rng.randint(1, 16)
    return dict(zip(original, changed))


def from_template(template, seed, index):
    targets, history, moves, witness = template
    rng = random.Random(seed + 7919 * index)
    variant = index // 10 % 4
    horizon = rng.randint(max(1, len(moves)), 10_000)
    latest = MAX_VALUE - horizon
    span = (20_000, 900_000_000, 2_000_000, 300_000_000)[variant]
    score_map = random_score_map(history, witness, rng, variant)

    players = len(targets)
    labels = list(range(1, players + 1))
    rng.shuffle(labels)
    new_targets = [0] * players
    for old, target in enumerate(targets):
        new_targets[labels[old] - 1] = target

    ordered = sorted(history, key=lambda row: (row[1], row[2]))
    anchor = rng.choice(ordered)[1]
    new_history = []
    for old_score, rows in groupby(ordered, key=lambda row: row[1]):
        block = list(rows)
        dates = sorted(rng.sample(range(latest - max(span, len(block)), latest), len(block)))
        if old_score == anchor:
            dates[-1] = latest
        score = old_score if score_map is None else score_map[old_score]
        new_history.extend((labels[person - 1], score, date) for (person, _, _), date in zip(block, dates))

    rng.shuffle(new_history)
    lines = [f"{players} {len(history)} 1", " ".join(map(str, new_targets))]
    lines.extend(f"{person} {score} {date}" for person, score, date in new_history)
    lines.append(str(len(moves)))
    lines.extend(f"{labels[person - 1]} {rank}" for person, rank in moves)
    return "\n".join(lines) + "\n"


def construct(seed, index):
    rng = random.Random(seed + 7919 * index)
    sizes = [(3, 24), (3, 24), (40, 150), (200, 1_200)]
    actor_count = rng.randint(*sizes[index % len(sizes)])
    guard_count = (actor_count + 11) // 12 + rng.randint(1, 8)
    players = actor_count + guard_count
    actors = list(range(1, players + 1))
    rng.shuffle(actors)
    guards = actors[actor_count:]
    actors = actors[:actor_count]

    schedule = []
    for player in actors:
        repeat = 1 if index % 4 == 0 else rng.randint(1, 4)
        schedule.extend([player] * repeat)
    rng.shuffle(schedule)
    last = {player: step for step, player in enumerate(schedule)}
    latest = MAX_VALUE - rng.randint(len(schedule), 10_000)
    upper = [(player, rng.randint(600_000_000, 950_000_000))
             for player in guards for _ in range(rng.randint(16, 36))]
    lower = [(player, rng.randint(1, 200_000_000))
             for player in actors for _ in range(rng.randint(1, 8))]
    history_size = len(upper) + len(lower)
    span = 100_000 if index % 2 == 0 else 900_000_000
    times = rng.sample(range(latest - span, latest), history_size)
    times[0] = latest
    history = [(player, score, date) for (player, score), date in zip(upper + lower, times)]
    rng.shuffle(history)
    appended = []
    for step, player in enumerate(schedule):
        score_range = (960_000_000, MAX_VALUE) if last[player] == step else (400_000_000, 500_000_000)
        appended.append((player, rng.randint(*score_range), latest + step + 1))

    order = sorted(history + appended, key=lambda row: (-row[1], row[2]))
    positions = {date: rank for rank, (_, _, date) in enumerate(order, 1) if date > latest}
    targets = [history_size] * players
    for player, step in last.items():
        final_rank = positions[latest + step + 1]
        # Before finishing, every record of this player is below all upper history.
        slack_rank = rng.randint(final_rank, min(len(upper), 2 * actor_count))
        targets[player - 1] = final_rank if rng.randrange(3) == 0 else slack_rank

    lines = [f"{players} {history_size} 1", " ".join(map(str, targets))]
    lines.extend(f"{player} {score} {date}" for player, score, date in history)
    lines.append(str(len(appended)))
    lines.extend(f"{player} {positions[date]}" for player, _, date in appended)
    return "\n".join(lines) + "\n"


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--data", type=Path, default=here.parent / "实时榜单_最终测试数据.zip")
    parser.add_argument("--witnesses", type=Path, default=here / "fixed_template_witnesses.zip")
    parser.add_argument("--seed", type=int, default=571943)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--mode", choices=("templates", "construct"), default="templates")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode == "templates":
        with zipfile.ZipFile(args.data) as data, zipfile.ZipFile(args.witnesses) as witnesses:
            templates = [read_template(data, witnesses, number) for number in range(27, 37)]
    for index in range(args.count):
        if args.mode == "templates":
            case = from_template(templates[index % 10], args.seed, index)
        else:
            case = construct(args.seed, index)
        (args.output / f"f{index:03}.in").write_text(
            case, encoding="ascii", newline="\n"
        )


if __name__ == "__main__":
    main()
