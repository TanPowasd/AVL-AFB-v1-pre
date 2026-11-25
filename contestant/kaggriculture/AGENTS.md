# Contestant Workflow

When asked to participate in this contest, generate the submission autonomously.
Start with `README.md` and `AI_README.md`, then read the four required files
listed in `AI_README.md` (`handbook/player-guide.md`, `handbook/game-facts.md`,
`agents/template/main.py` / `agents/starter.py`, and `engine/rules.py`).
Do not stop at a plan, a strategy explanation, or the starter template.

- Develop a modular multi-file Python package (root `main.py` + optional helper
  `.py` modules and read-only data files such as `.json`/lookup tables) under
  `agents/submissions/`, then package it with `scripts/make_submission.py`:
  - Unified multi-problem workspace (official evaluation reads top-level
    `submissions/`): `../submissions/kaggriculture.zip`
  - Standalone single-problem workspace: `agents/submissions/my_agent.zip`
    (unless the user specifies another filename).
- Implement `act(observation)` or `Agent.act(observation)` in root `main.py`.
  Full Python 3.10+ standard library, intra-package module imports, and
  read-only access to files inside your package directory are permitted; runtime
  isolation enforces no network, no out-of-package filesystem access, 1 GiB
  memory cap, 60s load limit, 1s/step + 60s overage pool, and <= 30 errors per
  game.
- Test against the supplied public baselines (`scripts/spar.py`,
  `scripts/run_match.py`, and `scripts/record_replay.py` — all accept `.zip`, a
  directory with `main.py`, or a single `.py` file), inspect errors and
  timeouts, and improve the strategy within the available time. Report actual
  results honestly.
- Read only public contest materials and your own work. Do not inspect other
  submissions, historical results, replays, or frozen solutions outside this
  workspace.
- Your results, traces, replays, and logs must stay in this workspace's
  `results/`, `replays/`, and `logs/`. Do not inspect external directories,
  other chats, shared dashboards, or other contestants' output.
- Do not change `config/workspace.json`, start a sharing server, or use external
  tools to bypass file and opponent restrictions.
- Use only `passive`, `random`, `greedy`, and `champion` as supplied opponents.
  Your own mirror match is also allowed. `--unrestricted` is not available in a
  contestant workspace.
- Keep the rules, judge, baselines, champion, scripts, and contest configuration
  unchanged.
- Finish with the packaged `.zip` submission path and a short account of
  verification. If file writes are unavailable, return the complete code.

This folder contains public contest materials and starts without other
contestants' submissions or test results. It does not configure an
operating-system sandbox or change the coding AI's local permissions. Each
contestant needs a separate copy.

For repository maintenance requests, follow the user's requested scope; the
contestant workflow does not require creating a new submission.
