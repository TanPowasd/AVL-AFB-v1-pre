# Contestant Workflow (rift_era / 裂陆纪元)

When asked to participate in this contest, develop, test, and package your submission autonomously.
Start with `AI_README.md`, then read `GUIDE.md`, `COMMANDS.md`, `handbook/`, and key `rules/` chapters (`06_agent_interface.md`, `07_scoring_and_resources.md`).
Do not stop at a plan, a strategy explanation, or the unmodified template.

- Build a complete Python 3.11+ standard-library-only strategy directory (e.g. `agents/my_bot/` containing `main.py`, `bot.py`, and an unmodified copy of `sdk.py` from the workspace root).
- Package your submission into a zip archive with `main.py` at the archive root:
  - Unified suite submission path: `python3 scripts/make_submission.py agents/my_bot --out ../submissions/rift_era.zip`
  - Local standalone submission path: `python3 scripts/make_submission.py agents/my_bot --out agents/submissions/my_bot.zip`
- Keep all runtime constraints strictly satisfied: Python standard library only, no network, 1 GiB RAM, 64 MiB writable temp dir, <= 256 orders per window, 0.5 s base time per window (450 s `time_pool`, 30 s single-window hard cap, 3600 s match total), 15 s init handshake. Never let an uncaught exception crash `main.py`.
- Test iteratively using the local tools:
  - Quick smoke / practice score: `python3 scripts/spar.py agents/my_bot --opponent advanced --ticks 400`
  - 8-seat match & practice scoring: `python3 scripts/local_match.py score --bot agents/my_bot --opponent advanced --seat 0 --seed 7 --ticks 400 --legacy-map`
  - Visual replay debugging: `python3 scripts/record_replay.py --a agents/my_bot --b advanced --seed 7 --ticks 200 --legacy-map` and inspect the output JSON in `viz/player.html`.
- Use the supplied public opponents (`none`, `simple`, `template`, `medium`, `advanced`) as a starting point, **and build your own diverse opponent bots (different playstyles / historical snapshots) for 8-seat local self-testing** — the kit does not include 7 distinct strong opponents, so relying only on repeated `advanced` copies is insufficient to test multi-nation interaction and avoid overfitting. Pass your custom opponent directories or `.zip` packages to `scripts/local_match.py match --seats ...` and `scripts/local_match.py score --opponent ...`.
- Keep your outputs inside this workspace's `results/`, `replays/`, `logs/`, `agents/submissions/`, and the unified submission path `../submissions/rift_era.zip`.
- Do not modify `sdk.py`, `runtime/`, `rules/`, `agents/medium/`, `agents/advanced/`, `config/workspace.json`, or `manifest.json`.
- Finish by verifying `faults == 0`, reporting exact local test scores across multiple seeds/seats, and giving the final submission `.zip` path. If file writes are unavailable, return complete `main.py` and `bot.py` code blocks.
