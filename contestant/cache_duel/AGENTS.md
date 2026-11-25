# Contestant Workflow (Cache Duel)

When asked to participate in this contest, generate the submission autonomously.
Start with `AI_README.md`, then read the four required files listed there.
Do not stop at a plan, a strategy explanation, or the starter template.

- Write a complete single-file Python 3.10+ strategy to `../submissions/cache_duel.py`
  (the unified final submission path). During local iteration you may also keep a working
  copy at `agents/submissions/my_agent.py`, and copy the verified best version to
  `../submissions/cache_duel.py` when finished.
- Implement both `attacker` and `defender` roles in the same file, communicating via
  line-delimited UTF-8 JSON over `stdin` / `stdout` (always `flush=True`) using only
  the Python standard library, with no network or external file access.
- Stay well inside the timing limits (`1.0s` per step, `5.0s` shared time pool per game,
  `20.0s` total thinking budget per game across up to `5000` steps) and never emit illegal
  keys, out-of-cache evictions, or non-integer (`bool`/`float`) values.
- Test against the supplied public baselines (`fifo`, `lru`, `marking`, `rand`) via
  `scripts/spar.py`, `scripts/run_match.py`, and `scripts/record_replay.py` (+ `viz/player.html`),
  inspect misses/scores/faults, and iterate within available time. Report actual results honestly.
- Read only public contest materials and your own work. Do not inspect other submissions,
  historical results, replays, or external directories outside this workspace (except your own
  `../submissions/cache_duel.py`).
- Keep your results, replays, and logs inside this workspace's `results/`, `replays/`, and `logs/`.
- Do not modify `config/workspace.json`, start a sharing server (`--serve`), use `--unrestricted`,
  or alter `engine/engine.py`, `engine/policy.py`, `config/format.json`, or supplied baselines.
- Finish with the submission path and a concise verification summary. If file writes are
  unavailable, return the complete code in a single Python code block.
